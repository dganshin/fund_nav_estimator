from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.frontend_app import app, build_home_rows, load_live_estimate_bundle
from src.init_db import init_db
from src.db import get_session_factory
from src.import_data import import_funds_from_rows
from src.backfill import fetch_and_store_stock_quotes
from tests.test_stage4 import make_mock_source, seed_fund_holdings_and_allocations


class EmptyLiveDataSource:
    def __init__(self) -> None:
        self.last_warnings = ["Warning: no live quotes fetched."]

    def fetch_stock_live_quotes(self, asset_codes, sleep_seconds=0.0, timeout_seconds=8.0):
        return []


def create_session_factory(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'frontend.db'}"
    init_db(db_url)
    return get_session_factory(db_url)


def test_frontend_live_bundle_falls_back_to_daily_quotes(tmp_path, monkeypatch):
    session_factory = create_session_factory(tmp_path)
    data_source = make_mock_source()
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        fetch_and_store_stock_quotes(
            session,
            data_source,
            date.fromisoformat("2026-05-22"),
            date.fromisoformat("2026-05-22"),
            ["600988.SH", "000975.SZ"],
        )

    monkeypatch.setattr("src.frontend_app.get_cached_session_factory", lambda: session_factory)
    monkeypatch.setattr("src.frontend_app.get_cached_data_source", lambda: EmptyLiveDataSource())
    results, status_message, used_fallback = load_live_estimate_bundle(fund_code="002207")

    assert results
    assert used_fallback is True
    assert "最近收盘缓存" in status_message


def test_frontend_homepage_lists_funds(tmp_path, monkeypatch):
    session_factory = create_session_factory(tmp_path)
    data_source = make_mock_source()
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        import_funds_from_rows(
            session,
            [
                {
                    "fund_code": "000001",
                    "fund_name": "示例成长混合",
                    "fund_type": "equity",
                    "market": "A股",
                    "is_active": True,
                }
            ],
        )
        fetch_and_store_stock_quotes(
            session,
            data_source,
            date.fromisoformat("2026-05-22"),
            date.fromisoformat("2026-05-22"),
            ["600988.SH", "000975.SZ"],
        )

    monkeypatch.setattr("src.frontend_app.get_cached_session_factory", lambda: session_factory)
    monkeypatch.setattr("src.frontend_app.get_cached_data_source", lambda: EmptyLiveDataSource())

    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "基金实时估值" in response.text
    assert "测试真实基金" in response.text


def test_build_home_rows_formats_profit_and_estimate(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        results = []
        from src.estimator import compute_live_fund_estimates

        results = compute_live_fund_estimates(
            session=session,
            live_quotes={
                "600988.SH": {"return_pct": 0.03},
                "000975.SZ": {"return_pct": 0.01},
            },
            trade_date=date.fromisoformat("2026-05-22"),
            fund_code="002207",
        )

    rows = build_home_rows(results)
    assert rows
    assert rows[0]["current_estimate_text"].startswith("+")
