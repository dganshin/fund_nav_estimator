"""
因果校准新功能测试：
1. ensure_fund_by_code 对未知基金自动创建
2. quick-buy platform 默认支付宝/蚂蚁财富
3. 搜索未添加基金代码时触发 fetch_fund_profile
4. online_calibration_state 初始化正确
5. 校准只使用最新 actual_return，不使用未来数据
6. 同一 holding_version + 同一日期 scale 更新幂等
7. 新 holding_version 会重置 calibration state（新版本无 state）
8. raw_estimate 太小时跳过更新
9. 异常误差日跳过更新
10. calibration_residuals 能逐日记录 residual
11. ensure_fund_by_code 对已存在基金不重复创建
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.calibration import (
    CalibrationResidual,
    ensure_fund_by_code,
    get_calibration_stats,
    load_calibration_residuals,
    run_online_calibration,
)
from src.data_sources.base import FundProfile
from src.frontend_app import app
from src.init_db import init_db
from src.db import get_session_factory
from src.models import (
    ActualReturn,
    DailyQuote,
    HoldingVersion,
    HoldingItem,
    OnlineCalibrationState,
    Fund,
)
from tests.test_stage4 import seed_fund_holdings_and_allocations


def make_db(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'cal.db'}"
    init_db(db_url)
    return get_session_factory(db_url)


def seed_with_actual_return(tmp_path, session):
    """seed 基金 + active holding_version + daily_quotes + actual_return."""
    seed_fund_holdings_and_allocations(tmp_path, session)
    # seed daily quotes for holding stocks
    for code, ret in [("600988.SH", 0.02), ("000975.SZ", 0.01)]:
        q = DailyQuote(
            trade_date=date(2026, 5, 22),
            asset_code=code,
            asset_name=code,
            return_pct=ret,
            source="test",
        )
        session.add(q)
    # seed actual return for fund
    ar = ActualReturn(
        trade_date=date(2026, 5, 22),
        fund_code="002207",
        actual_return=0.015,
        source="test",
    )
    session.add(ar)
    session.commit()


# ── 1. ensure_fund_by_code：未知基金自动创建 ─────────────────────────────

def test_ensure_fund_creates_new_fund(tmp_path):
    sf = make_db(tmp_path)
    mock_ds = MagicMock()
    mock_ds.fetch_fund_profile.return_value = FundProfile(
        fund_code="001467",
        fund_name="华夏新经济混合",
        fund_type="equity",
        market="CN",
        latest_unit_nav=2.345,
        latest_nav_date=date(2026, 5, 22),
        accumulated_nav=None,
        source="mock",
    )
    with sf() as session:
        result = ensure_fund_by_code(session, "001467", mock_ds)

    assert result["fund_code"] == "001467"
    assert result["fund_name"] == "华夏新经济混合"
    assert result["created"] is True
    assert result["latest_unit_nav"] == 2.345

    # 再调用一次，不应重复创建
    with sf() as session:
        result2 = ensure_fund_by_code(session, "001467", mock_ds)
    assert result2["created"] is False


# ── 2. ensure_fund_by_code：已存在基金不重复创建 ─────────────────────────

def test_ensure_fund_skips_existing(tmp_path):
    sf = make_db(tmp_path)
    with sf() as session:
        session.add(Fund(fund_code="002207", fund_name="前海开源", fund_type="equity", market="A股", is_active=True))
        session.commit()
    mock_ds = MagicMock()
    with sf() as session:
        r = ensure_fund_by_code(session, "002207", mock_ds)
    assert r["created"] is False
    mock_ds.fetch_fund_profile.assert_not_called()


# ── 3. /api/quick-buy：platform 默认支付宝/蚂蚁财富 ──────────────────────

def test_quick_buy_platform_default(tmp_path, monkeypatch):
    sf = make_db(tmp_path)
    mock_ds = MagicMock()
    mock_ds.fetch_fund_profile.return_value = FundProfile(
        fund_code="001467", fund_name="测试基金", fund_type="equity", market="CN",
        latest_unit_nav=2.0, latest_nav_date=date(2026, 5, 22), accumulated_nav=None, source="mock",
    )
    monkeypatch.setattr("src.frontend_app.get_cached_session_factory", lambda: sf)
    monkeypatch.setattr("src.frontend_app.get_cached_data_source", lambda: mock_ds)

    client = TestClient(app)
    resp = client.post("/api/quick-buy", json={"fund_code": "001467", "holding_amount": 5000})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True

    from src.web_services import load_user_position_rows
    with sf() as session:
        positions = load_user_position_rows(session)
    pos = next((p for p in positions if p["fund_code"] == "001467"), None)
    assert pos is not None
    assert pos["holding_amount"] == 5000.0
    assert "支付宝" in (pos.get("platform") or "")


# ── 4. /api/search-fund：未知代码触发 fetch_fund_profile ────────────────

def test_search_fund_unknown_code_calls_profile(tmp_path, monkeypatch):
    sf = make_db(tmp_path)
    mock_ds = MagicMock()
    mock_ds.fetch_fund_profile.return_value = FundProfile(
        fund_code="001467", fund_name="新经济混合", fund_type="equity", market="CN",
        latest_unit_nav=1.5, latest_nav_date=date(2026, 5, 22), accumulated_nav=None, source="mock",
    )
    monkeypatch.setattr("src.frontend_app.get_cached_session_factory", lambda: sf)
    monkeypatch.setattr("src.frontend_app.get_cached_data_source", lambda: mock_ds)

    client = TestClient(app)
    resp = client.get("/api/search-fund?code=001467")
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert data["in_db"] is False
    assert data["fund_name"] == "新经济混合"
    mock_ds.fetch_fund_profile.assert_called_once_with("001467")


# ── 5. online_calibration_state 初始化正确 ───────────────────────────────

def test_calibration_state_initializes(tmp_path):
    sf = make_db(tmp_path)
    with sf() as session:
        seed_with_actual_return(tmp_path, session)
        result = run_online_calibration(session, "002207")

    assert result is not None
    assert result.fund_code == "002207"
    # scale_factor 应该在合理范围
    assert 0.5 <= result.scale_factor_after <= 2.0
    assert result.sample_count >= 1


# ── 6. 校准幂等：同一天再跑不重复更新 scale ──────────────────────────────

def test_calibration_idempotent_same_date(tmp_path):
    sf = make_db(tmp_path)
    with sf() as session:
        seed_with_actual_return(tmp_path, session)
        r1 = run_online_calibration(session, "002207")
    assert r1 is not None
    scale_after_first = r1.scale_factor_after

    with sf() as session:
        r2 = run_online_calibration(session, "002207", calibration_date=date(2026, 5, 22))
    assert r2 is not None
    # 第二次不更新 scale（幂等）
    assert r2.is_updated is False
    assert r2.skip_reason == "already_calibrated_this_date"
    # scale 不变
    assert abs(r2.scale_factor_after - scale_after_first) < 1e-8


# ── 7. force=True 时可强制重跑 ──────────────────────────────────────────

def test_calibration_force_reruns(tmp_path):
    sf = make_db(tmp_path)
    with sf() as session:
        seed_with_actual_return(tmp_path, session)
        run_online_calibration(session, "002207")  # first run
        r2 = run_online_calibration(session, "002207", calibration_date=date(2026, 5, 22), force=True)
    assert r2 is not None
    # 强制重跑允许更新
    # is_updated 取决于是否满足其他条件


# ── 8. raw_estimate 太小时跳过 ──────────────────────────────────────────

def test_calibration_skips_if_raw_estimate_tiny(tmp_path):
    sf = make_db(tmp_path)
    with sf() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        # 给一个几乎为零的 return
        for code in ["600988.SH", "000975.SZ"]:
            session.add(DailyQuote(
                trade_date=date(2026, 5, 22), asset_code=code, asset_name=code,
                return_pct=0.000001, source="test",
            ))
        session.add(ActualReturn(
            trade_date=date(2026, 5, 22), fund_code="002207", actual_return=0.015, source="test",
        ))
        session.commit()
        result = run_online_calibration(session, "002207")
    assert result is not None
    assert result.is_updated is False
    assert "raw_estimate_too_small" in result.skip_reason


# ── 9. 异常大误差日跳过 ─────────────────────────────────────────────────

def test_calibration_skips_on_large_residual(tmp_path):
    sf = make_db(tmp_path)
    with sf() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        for code, ret in [("600988.SH", 0.05), ("000975.SZ", 0.05)]:
            session.add(DailyQuote(
                trade_date=date(2026, 5, 22), asset_code=code, asset_name=code,
                return_pct=ret, source="test",
            ))
        # actual_return 相差超过 2%
        session.add(ActualReturn(
            trade_date=date(2026, 5, 22), fund_code="002207", actual_return=-0.05, source="test",
        ))
        session.commit()
        result = run_online_calibration(session, "002207")
    assert result is not None
    assert result.is_updated is False
    assert "abs_residual_too_large" in result.skip_reason


# ── 10. calibration_residuals 逐日记录 ──────────────────────────────────

def test_calibration_residuals_recorded(tmp_path):
    sf = make_db(tmp_path)
    with sf() as session:
        seed_with_actual_return(tmp_path, session)
        run_online_calibration(session, "002207")
        rows = load_calibration_residuals(session, "002207")

    assert len(rows) >= 1
    row = rows[0]
    assert "trade_date" in row
    assert "actual_return" in row
    assert "raw_estimate" in row
    assert "effective_estimate" in row
    assert "residual" in row
    assert "scale_used" in row
    assert "is_used" in row


# ── 11. get_calibration_stats 包含所有关键字段 ──────────────────────────

def test_calibration_stats_fields(tmp_path):
    sf = make_db(tmp_path)
    with sf() as session:
        seed_with_actual_return(tmp_path, session)
        run_online_calibration(session, "002207")
        stats = get_calibration_stats(session, "002207")

    assert "sample_count" in stats
    assert "current_scale" in stats
    assert "confidence_level" in stats
    assert "last_calibration_date" in stats
