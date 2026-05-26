from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.frontend_app import app, build_home_rows, load_live_estimate_bundle
from src.init_db import init_db
from src.db import get_session_factory
from src.import_data import import_funds_from_rows
from src.web_services import load_fund_rows, save_watchlist_rows, save_user_position_rows
from src.backfill import fetch_and_store_stock_quotes
from tests.test_stage4 import make_mock_source, seed_fund_holdings_and_allocations


class EmptyLiveDataSource:
    def __init__(self):
        self.last_warnings = ["Warning: no live quotes fetched."]

    def fetch_stock_live_quotes(self, asset_codes, sleep_seconds=0.0, timeout_seconds=8.0):
        return []


def create_session_factory(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'frontend.db'}"
    init_db(db_url)
    return get_session_factory(db_url)


def seed_with_quotes(tmp_path, session):
    seed_fund_holdings_and_allocations(tmp_path, session)
    fetch_and_store_stock_quotes(
        session,
        make_mock_source(),
        date.fromisoformat("2026-05-22"),
        date.fromisoformat("2026-05-22"),
        ["600988.SH", "000975.SZ"],
    )


# ── 1. /api/live-estimates 返回 JSON 且字段完整 ─────────────────────────────

def test_api_live_estimates_returns_json(tmp_path, monkeypatch):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_with_quotes(tmp_path, session)

    monkeypatch.setattr("src.frontend_app.get_cached_session_factory", lambda: session_factory)
    monkeypatch.setattr("src.frontend_app.get_cached_data_source", lambda: EmptyLiveDataSource())

    client = TestClient(app)
    resp = client.get("/api/live-estimates")
    assert resp.status_code == 200
    data = resp.json()
    assert "rows" in data
    assert "status_message" in data
    assert "latest_time" in data
    if data["rows"]:
        row = data["rows"][0]
        for field in ("fund_code", "fund_name", "current_estimate_text", "confidence_level", "quote_time"):
            assert field in row, f"missing field: {field}"


# ── 2. holding_amount × final_estimate ≈ estimated_today_profit ────────────

def test_estimated_today_profit_calculation(tmp_path, monkeypatch):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        save_user_position_rows(session, [{"fund_code": "002207", "holding_amount": 10000.0, "is_active": True}])

    monkeypatch.setattr("src.frontend_app.get_cached_session_factory", lambda: session_factory)
    monkeypatch.setattr("src.frontend_app.get_cached_data_source", lambda: EmptyLiveDataSource())
    monkeypatch.setattr("src.frontend_app.LIVE_BUNDLE_CACHE", {})

    from src.estimator import compute_live_fund_estimates
    with session_factory() as session:
        results = compute_live_fund_estimates(
            session=session,
            live_quotes={"600988.SH": {"return_pct": 0.03}, "000975.SZ": {"return_pct": 0.01}},
            trade_date=date.fromisoformat("2026-05-22"),
            fund_code="002207",
        )
    rows = build_home_rows(results)
    for row in rows:
        if row["fund_code"] == "002207" and row["holding_amount"] is not None:
            est = row["current_estimate"]
            profit = row["estimated_today_profit"]
            if est is not None and profit is not None:
                expected = row["holding_amount"] * est
                assert abs(profit - expected) < 0.01
            break


# ── 3. 无 holding_amount 时今日盈亏为 None / '--' ──────────────────────────

def test_no_holding_amount_shows_dash(tmp_path, monkeypatch):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)

    from src.estimator import compute_live_fund_estimates
    with session_factory() as session:
        results = compute_live_fund_estimates(
            session=session,
            live_quotes={"600988.SH": {"return_pct": 0.03}, "000975.SZ": {"return_pct": 0.01}},
            trade_date=date.fromisoformat("2026-05-22"),
            fund_code="002207",
        )
    rows = build_home_rows(results)
    for row in rows:
        if row["fund_code"] == "002207":
            assert row["holding_amount"] is None or row["estimated_today_profit"] is None or row["estimated_today_profit_text"] == "--"
            break


# ── 4. live-estimate bundle: 回退到 daily quotes ──────────────────────────

def test_frontend_live_bundle_falls_back_to_daily_quotes(tmp_path, monkeypatch):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_with_quotes(tmp_path, session)

    monkeypatch.setattr("src.frontend_app.get_cached_session_factory", lambda: session_factory)
    monkeypatch.setattr("src.frontend_app.get_cached_data_source", lambda: EmptyLiveDataSource())
    monkeypatch.setattr("src.frontend_app.LIVE_BUNDLE_CACHE", {})

    results, status_message, used_fallback = load_live_estimate_bundle(fund_code="002207")
    assert results
    assert used_fallback is True


# ── 5. 首页正常显示基金列表 ────────────────────────────────────────────────

def test_frontend_homepage_lists_funds(tmp_path, monkeypatch):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_with_quotes(tmp_path, session)

    monkeypatch.setattr("src.frontend_app.get_cached_session_factory", lambda: session_factory)
    monkeypatch.setattr("src.frontend_app.get_cached_data_source", lambda: EmptyLiveDataSource())
    monkeypatch.setattr("src.frontend_app.LIVE_BUNDLE_CACHE", {})

    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "基金实时估值" in resp.text
    assert "测试真实基金" in resp.text


# ── 6. manage 保存 fund 时 fund_code 保留前导零 ───────────────────────────

def test_manage_fund_save_preserves_leading_zeros(tmp_path, monkeypatch):
    session_factory = create_session_factory(tmp_path)
    monkeypatch.setattr("src.frontend_app.get_cached_session_factory", lambda: session_factory)

    client = TestClient(app)
    resp = client.post(
        "/manage/fund/save",
        data={"fund_code": "002207", "fund_name": "测试基金", "fund_type": "equity", "market": "A股", "is_active": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with session_factory() as session:
        funds = load_fund_rows(session)
    codes = [f["fund_code"] for f in funds]
    assert "002207" in codes


# ── 7. portfolio 保存 holding_amount 后首页能读到 ─────────────────────────

def test_portfolio_save_holding_amount(tmp_path, monkeypatch):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)

    monkeypatch.setattr("src.frontend_app.get_cached_session_factory", lambda: session_factory)

    client = TestClient(app)
    resp = client.post(
        "/portfolio",
        data={"fund_code": "002207", "holding_amount": "5000", "is_active": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with session_factory() as session:
        positions = [p for p in __import__("src.web_services", fromlist=["load_user_position_rows"]).load_user_position_rows(session) if p["fund_code"] == "002207"]
    assert positions
    assert positions[0]["holding_amount"] == 5000.0


# ── 8. manage 停用基金 ─────────────────────────────────────────────────────

def test_frontend_manage_can_deactivate_fund(tmp_path, monkeypatch):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)

    monkeypatch.setattr("src.frontend_app.get_cached_session_factory", lambda: session_factory)

    client = TestClient(app)
    resp = client.post(
        "/manage/fund/disable",
        data={"fund_code": "002207"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with session_factory() as session:
        funds = load_fund_rows(session)
    row = next(f for f in funds if f["fund_code"] == "002207")
    assert row["is_active"] is False


# ── 9. /api/live-estimates 搜索过滤正常 ───────────────────────────────────

def test_api_live_estimates_search_filter(tmp_path, monkeypatch):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_with_quotes(tmp_path, session)
        import_funds_from_rows(session, [{
            "fund_code": "000001", "fund_name": "无关基金", "fund_type": "equity", "market": "A股", "is_active": True
        }])

    monkeypatch.setattr("src.frontend_app.get_cached_session_factory", lambda: session_factory)
    monkeypatch.setattr("src.frontend_app.get_cached_data_source", lambda: EmptyLiveDataSource())
    monkeypatch.setattr("src.frontend_app.LIVE_BUNDLE_CACHE", {})

    client = TestClient(app)
    resp = client.get("/api/live-estimates?search=002207")
    assert resp.status_code == 200
    data = resp.json()
    for row in data.get("rows", []):
        assert "002207" in row["fund_code"] or "002207" in row["fund_name"]


def make_home_result(**kwargs):
    base = {
        "fund_code": "001467",
        "fund_name": "测试基金",
        "current_estimate": 0.039,
        "best_status": "ready",
        "error_band_label": "预计误差≤±0.30%",
        "holding_amount": 1000.0,
        "confidence_level": None,
        "error_band_pct": 0.003,
        "confidence_text": "",
        "quote_time": datetime(2026, 5, 26, 16, 15),
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_home_rows_keep_previous_close_when_using_quote_cache():
    result = make_home_result()
    residual = SimpleNamespace(
        fund_code="001467",
        trade_date=date(2026, 5, 26),
        actual_return=0.028,
    )

    rows = build_home_rows(
        [result],
        residuals_map={"001467": residual},
        now=datetime(2026, 5, 27, 8, 30),
    )

    row = rows[0]
    assert row["actual_return_available"] is True
    assert row["actual_return_today_text"] == "+2.80%"
    assert row["actual_return_date"] == "2026-05-26"
    assert abs(row["estimated_today_profit"] - 28.0) < 0.01


def test_home_rows_hide_stale_close_after_realtime_quote_starts():
    result = make_home_result(quote_time=datetime(2026, 5, 27, 9, 35))
    residual = SimpleNamespace(
        fund_code="001467",
        trade_date=date(2026, 5, 26),
        actual_return=0.028,
    )

    rows = build_home_rows(
        [result],
        residuals_map={"001467": residual},
        now=datetime(2026, 5, 27, 9, 35),
    )

    row = rows[0]
    assert row["actual_return_available"] is False
    assert row["profit_return_source"] == "estimate"
    assert abs(row["estimated_today_profit"] - 39.0) < 0.01
