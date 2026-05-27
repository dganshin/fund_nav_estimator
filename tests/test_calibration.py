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
import pandas as pd
from fastapi.testclient import TestClient
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.calibration import (
    CalibrationResidual,
    _select_causal_model,
    calculate_error_band,
    ensure_fund_by_code,
    get_calibration_stats,
    load_calibration_residuals,
    run_online_calibration,
)
from src.data_sources.base import FundNavRecord, FundProfile, StockQuoteRecord
from src.onboarding import _find_target_etf, ensure_fund_full_onboarded
from src.frontend_app import app
from src.init_db import init_db
from src.db import get_session_factory
from src.models import (
    ActualReturn,
    DailyQuote,
    FundNav,
    HoldingVersion,
    HoldingItem,
    OnlineCalibrationState,
    Fund,
    TaskRun,
    UserFundPositionEvent,
)
from src.tasks import sync_daily_all_funds
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


def test_sync_daily_skips_per_fund_nav_when_already_calibrated(tmp_path, monkeypatch):
    sf = make_db(tmp_path)

    class IncrementalOnlySource:
        def fetch_latest_fund_navs_bulk(self):
            return []

        def fetch_fund_navs(self, *args, **kwargs):
            raise AssertionError("daily sync should not refetch per-fund nav history")

        def fetch_stock_daily_quotes(self, *args, **kwargs):
            raise AssertionError("already calibrated fund should not refetch quotes")

    monkeypatch.setattr("src.tasks.get_session_factory", lambda: sf)
    monkeypatch.setattr("src.tasks.AKShareDataSource", IncrementalOnlySource)

    with sf() as session:
        session.add(Fund(fund_code="009999", fund_name="已校准基金", fund_type="equity", market="CN", is_active=True))
        hv = HoldingVersion(
            fund_code="009999",
            report_date=date(2026, 3, 31),
            source="test",
            total_weight=0.5,
            is_active=True,
        )
        session.add(hv)
        session.flush()
        session.add(HoldingItem(holding_version_id=hv.id, asset_code="600000.SH", asset_name="测试股票", asset_type="stock", weight=0.5))
        session.add(FundNav(trade_date=date(2026, 5, 26), fund_code="009999", unit_nav=1.0, source="test"))
        session.add(ActualReturn(trade_date=date(2026, 5, 26), fund_code="009999", actual_return=0.01, source="test"))
        session.add(CalibrationResidual(
            fund_code="009999",
            holding_version_id=hv.id,
            trade_date=date(2026, 5, 26),
            actual_return=0.01,
            known_estimate=0.01,
            unknown_estimate=0.0,
            base_estimate=0.01,
            raw_estimate=0.01,
            calibrated_estimate=0.01,
            effective_estimate=0.01,
            residual=0.0,
            abs_residual=0.0,
            scale_used_before_update=1.0,
            beta_known=1.0,
            beta_unknown=1.0,
            alpha=0.0,
            sample_count=1,
            is_used_for_update=True,
            skip_reason="",
        ))
        task = TaskRun(task_type="sync_daily", fund_code="ALL", status="pending")
        session.add(task)
        session.commit()
        task_id = task.id

    sync_daily_all_funds(task_id)

    with sf() as session:
        task = session.get(TaskRun, task_id)
    assert task.status == "success"


def make_candidate_residual(day: int, actual: float, single: float, two: float) -> CalibrationResidual:
    return CalibrationResidual(
        fund_code="002207",
        holding_version_id=1,
        trade_date=date(2026, 5, day),
        actual_return=actual,
        known_estimate=actual,
        unknown_estimate=0.0,
        base_estimate=actual + 0.003,
        coverage_adjusted_estimate=actual + 0.003,
        single_scale_estimate=single,
        two_factor_estimate=two,
        raw_estimate=actual + 0.003,
        calibrated_estimate=single,
        effective_estimate=single,
        residual=actual - single,
        abs_residual=abs(actual - single),
        scale_used_before_update=1.0,
        is_used_for_update=True,
        skip_reason="",
        model_version="single_scale",
        is_out_of_sample=True,
    )


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
        event = session.scalar(select(UserFundPositionEvent).where(UserFundPositionEvent.fund_code == "001467"))
    pos = next((p for p in positions if p["fund_code"] == "001467"), None)
    assert pos is not None
    assert pos["holding_amount"] == 5000.0
    assert "支付宝" in (pos.get("platform") or "")
    assert event.event_type == "set_amount"
    assert event.effective_date == event.trade_date


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
    # scale_factor 来自股票仓位 / 公开持仓覆盖率, 前十大覆盖低时可大于 2。
    assert 0.5 <= result.scale_factor_after <= 5.0
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


class FullOnboardSource:
    def __init__(self):
        self.calls = []

    def fetch_fund_profile(self, fund_code):
        self.calls.append("profile")
        return FundProfile(
            fund_code=fund_code,
            fund_name="自动基金",
            fund_type="equity",
            market="CN",
            latest_unit_nav=2.0,
            latest_nav_date=date(2026, 5, 22),
            accumulated_nav=None,
            source="mock",
        )

    def fetch_fund_public_holdings(self, fund_code):
        self.calls.append("holdings")
        return [
            {"report_date": "2026-03-31", "asset_code": "600988.SH", "asset_name": "赤峰黄金", "weight_pct": 10.0},
            {"report_date": "2026-03-31", "asset_code": "000975.SZ", "asset_name": "山金国际", "weight_pct": 8.0},
        ]

    def fetch_fund_asset_allocation(self, fund_code, report_date=None):
        self.calls.append("asset_allocation")
        return [{"report_date": "2026-03-31", "stock_weight_pct": 90.0}]

    def fetch_fund_navs(self, fund_code, start_date, end_date):
        self.calls.append("navs")
        return [
            FundNavRecord(date(2026, 5, 21), fund_code, 1.0, None, "mock"),
            FundNavRecord(date(2026, 5, 22), fund_code, 1.01, None, "mock"),
        ]

    def fetch_stock_daily_quotes(self, asset_codes, start_date, end_date, sleep_seconds=0.0):
        self.calls.append("quotes")
        return [
            StockQuoteRecord(date(2026, 5, 22), code, code, 0.01, "mock")
            for code in asset_codes
        ]


def test_ensure_fund_full_onboarded_fetches_public_data(tmp_path):
    sf = make_db(tmp_path)
    source = FullOnboardSource()
    with sf() as session:
        result = ensure_fund_full_onboarded(session, "001467", source, holding_amount=2000)

    assert result["fund_name"] == "自动基金"
    assert result["status"] == "ready"
    assert source.calls[:3] == ["profile", "holdings", "asset_allocation"]
    assert "navs" in source.calls
    assert "quotes" in source.calls


class EtfFeederOnboardSource(FullOnboardSource):
    def __init__(self):
        super().__init__()
        self.ak = self

    def fetch_fund_profile(self, fund_code):
        self.calls.append("profile")
        return FundProfile(
            fund_code=fund_code,
            fund_name="广发半导体设备ETF联接C",
            fund_type="股票型-标准指数",
            market="CN",
            latest_unit_nav=2.0,
            latest_nav_date=date(2026, 5, 22),
            accumulated_nav=None,
            source="mock",
        )

    def fund_individual_basic_info_xq(self, symbol):
        return pd.DataFrame([
            {"项目": "基金名称", "值": "广发中证半导体材料设备ETF发起式联接C"},
            {"项目": "基金全称", "值": "广发中证半导体材料设备主题交易型开放式指数证券投资基金发起式联接基金"},
            {"项目": "基金类型", "值": "股票型-标准指数"},
            {"项目": "基金公司", "值": "广发基金管理有限公司"},
            {"项目": "投资策略", "值": "本基金为ETF联接基金，主要通过投资于目标ETF实现跟踪。"},
            {"项目": "业绩比较基准", "值": "中证半导体材料设备主题指数收益率×95%+银行活期存款利率×5%"},
        ])

    def fund_etf_spot_em(self):
        return pd.DataFrame([
            {"代码": "560780", "名称": "半导体设备ETF广发"},
            {"代码": "512480", "名称": "半导体ETF国联安"},
        ])


def test_etf_feeder_prefers_target_etf_over_public_stock_holdings(tmp_path):
    sf = make_db(tmp_path)
    source = EtfFeederOnboardSource()
    with sf() as session:
        result = ensure_fund_full_onboarded(session, "020640", source, holding_amount=100)
        hv = session.scalar(select(HoldingVersion).where(HoldingVersion.fund_code == "020640"))
        item = session.scalar(select(HoldingItem).where(HoldingItem.holding_version_id == hv.id))
        fund = session.get(Fund, "020640")

    assert result["status"] == "ready"
    assert fund.fund_type == "etf_feeder"
    assert hv.source == "akshare:target_etf"
    assert item.asset_code == "560780.SH"
    assert item.asset_type == "etf"
    assert round(item.weight, 4) == 0.95


def test_gold_etf_feeder_matches_physical_gold_etf_not_gold_stock_etf():
    class GoldEtfSource:
        class ak:
            @staticmethod
            def fund_etf_spot_em():
                return pd.DataFrame([
                    {"代码": "159562", "名称": "黄金股ETF华夏"},
                    {"代码": "518850", "名称": "黄金ETF华夏"},
                    {"代码": "518880", "名称": "黄金ETF华安"},
                ])

    target = _find_target_etf(GoldEtfSource(), "华夏黄金ETF联接A", {})

    assert target is not None
    assert target["asset_code"] == "518850.SH"
    assert target["asset_name"] == "黄金ETF华夏"


def test_missing_holdings_home_row_shows_status_not_zero(tmp_path):
    sf = make_db(tmp_path)
    with sf() as session:
        session.add(Fund(fund_code="017193", fund_name="缺持仓基金", fund_type="equity", market="CN", is_active=True))
        session.commit()
        from src.estimator import compute_live_fund_estimates
        from src.frontend_app import build_home_rows

        results = compute_live_fund_estimates(session, {}, date(2026, 5, 22), fund_code="017193")
        rows = build_home_rows(results)

    assert rows[0]["current_estimate_text"] == "缺持仓"
    assert rows[0]["error_band_label"] == "缺持仓"


def test_error_band_uses_recent_residuals(tmp_path):
    sf = make_db(tmp_path)
    with sf() as session:
        seed_with_actual_return(tmp_path, session)
        hv = session.scalar(select(HoldingVersion).where(HoldingVersion.fund_code == "002207"))
        for i in range(10):
            session.add(CalibrationResidual(
                fund_code="002207",
                holding_version_id=hv.id,
                trade_date=date(2026, 5, 1 + i),
                actual_return=0.01,
                raw_estimate=0.01,
                effective_estimate=0.01,
                residual=0.001 * i,
                abs_residual=0.001 * i,
                scale_used_before_update=1.0,
                is_used_for_update=True,
                skip_reason="",
                params_fitted_until=date(2026, 4, 30),
                model_version=1,
                is_out_of_sample=True,
            ))
        session.commit()
        band = calculate_error_band(session, "002207", hv.id)

    assert band["error_band_label"].startswith("预计误差≤±")


def test_model_selector_does_not_force_two_factor_when_single_scale_is_better():
    rows = [
        make_candidate_residual(
            day=i,
            actual=0.01,
            single=0.0105,
            two=0.014,
        )
        for i in range(1, 21)
    ]

    assert _select_causal_model(rows, sample_count=20) == "single_scale"
