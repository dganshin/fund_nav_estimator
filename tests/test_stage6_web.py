from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backfill import fetch_and_store_fund_navs, fetch_and_store_stock_quotes
from src.db import get_session_factory
from src.estimator import (
    build_calibration_history,
    build_effective_weight_versions,
    build_estimate_history,
    build_reconcile_history,
    compute_live_fund_estimates,
    get_online_calibration_state,
    refresh_online_calibration_states,
)
from src.import_data import (
    import_asset_allocations_from_rows,
    import_funds_from_rows,
    import_holdings_from_rows,
    import_industry_allocations_from_rows,
)
from src.init_db import init_db
from src.web_app import load_live_estimate_results
from src.web.actions import run_selection_action
from src.web.queries import (
    get_fund_sidebar_context,
    load_estimate_comparison_rows,
    load_fund_detail_holdings,
    load_fund_overview_rows,
)
from src.web_services import (
    load_asset_allocation_rows,
    load_fund_rows,
    load_holding_rows,
    load_industry_allocation_rows,
    load_user_position_rows,
    save_user_position_rows,
)
from src.data_sources.akshare_source import AKShareDataSource
from tests.test_stage4 import make_mock_source, seed_fund_holdings_and_allocations


class EmptyLiveDataSource:
    def __init__(self) -> None:
        self.last_warnings = ["Warning: no live quotes fetched."]

    def fetch_stock_live_quotes(self, asset_codes, sleep_seconds=0.0, timeout_seconds=8.0):
        return []


def create_session_factory(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)
    return get_session_factory(db_url)


def test_web_row_import_keeps_fund_code_and_bool(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        count = import_funds_from_rows(
            session,
            [
                {
                    "fund_code": "002207",
                    "fund_name": "前海开源金银珠宝混合C",
                    "fund_type": "equity_theme",
                    "market": "A股",
                    "is_active": True,
                }
            ],
        )
        rows = load_fund_rows(session)

    assert count == 1
    assert rows[0]["fund_code"] == "002207"
    assert rows[0]["is_active"] is True


def test_web_row_import_round_trips_active_holdings(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        import_funds_from_rows(
            session,
            [
                {
                    "fund_code": "002207",
                    "fund_name": "前海开源金银珠宝混合C",
                    "fund_type": "equity_theme",
                    "market": "A股",
                    "is_active": True,
                }
            ],
        )
        count = import_holdings_from_rows(
            session,
            [
                {
                    "fund_code": "002207",
                    "report_date": "2026-03-31",
                    "source": "web_manual",
                    "asset_code": "600988.SH",
                    "asset_name": "赤峰黄金",
                    "asset_type": "stock",
                    "weight_pct": 9.87,
                },
                {
                    "fund_code": "002207",
                    "report_date": "2026-03-31",
                    "source": "web_manual",
                    "asset_code": "000975.SZ",
                    "asset_name": "山金国际",
                    "asset_type": "stock",
                    "weight_pct": 8.09,
                },
            ],
        )
        rows = load_holding_rows(session, "002207")

    assert count == 1
    assert [row["asset_code"] for row in rows] == ["600988.SH", "000975.SZ"]
    assert rows[0]["weight_pct"] == 9.87


def test_web_row_import_round_trips_allocations(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        import_funds_from_rows(
            session,
            [
                {
                    "fund_code": "002207",
                    "fund_name": "前海开源金银珠宝混合C",
                    "fund_type": "equity_theme",
                    "market": "A股",
                    "is_active": True,
                }
            ],
        )
        asset_count = import_asset_allocations_from_rows(
            session,
            [
                {
                    "fund_code": "002207",
                    "report_date": "2026-03-31",
                    "source": "web_manual",
                    "stock_weight_pct": 90.8,
                    "bond_weight_pct": 0,
                    "cash_weight_pct": 0,
                    "other_weight_pct": 0,
                }
            ],
        )
        industry_count = import_industry_allocations_from_rows(
            session,
            [
                {
                    "fund_code": "002207",
                    "report_date": "2026-03-31",
                    "source": "web_manual",
                    "industry_name": "采矿业",
                    "industry_code": "B",
                    "weight_pct": 78.16,
                },
                {
                    "fund_code": "002207",
                    "report_date": "2026-03-31",
                    "source": "web_manual",
                    "industry_name": "制造业",
                    "industry_code": "C",
                    "weight_pct": 12.64,
                },
            ],
        )
        asset_rows = load_asset_allocation_rows(session, "002207")
        industry_rows = load_industry_allocation_rows(session, "002207")

    assert asset_count == 1
    assert industry_count == 2
    assert asset_rows[0]["stock_weight_pct"] == 90.8
    assert [row["industry_code"] for row in industry_rows] == ["B", "C"]


def test_web_queries_return_sidebar_defaults_for_002207(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        context = get_fund_sidebar_context(session)

    assert context["selected_fund_code"] == "002207"


def test_web_actions_and_queries_build_comparison_rows(tmp_path):
    session_factory = create_session_factory(tmp_path)
    data_source = make_mock_source()
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        fetch_and_store_stock_quotes(
            session,
            data_source,
            date.fromisoformat("2026-05-20"),
            date.fromisoformat("2026-05-22"),
            ["600988.SH", "000975.SZ"],
        )
        fetch_and_store_fund_navs(
            session,
            data_source,
            "002207",
            date.fromisoformat("2026-05-20"),
            date.fromisoformat("2026-05-22"),
        )
        build_estimate_history(
            session,
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-22"),
            fund_code="002207",
        )
        build_reconcile_history(
            session,
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-22"),
            fund_code="002207",
        )
        build_calibration_history(
            session,
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-22"),
            window=2,
            base="coverage_adjusted",
            fund_code="002207",
            min_samples=1,
        )
        action_report = run_selection_action(
            session,
            fund_code="002207",
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-22"),
            selection_window=2,
            min_samples=1,
            min_improvement_bps=5,
            selection_policy="coverage_first",
        )
        rows = load_estimate_comparison_rows(
            session,
            fund_code="002207",
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-22"),
            window=2,
            selection_policy="coverage_first",
        )

    assert action_report.payload["selection_count"] > 0
    assert rows
    assert "best_method" in rows[0]
    assert "raw_error" in rows[0]


def test_web_overview_rows_support_multi_fund_sorting(tmp_path):
    session_factory = create_session_factory(tmp_path)
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
        rows = load_fund_overview_rows(
            session,
            selection_window=20,
            selection_policy="coverage_first",
            sort_by="fund_name",
            descending=False,
        )

    assert len(rows) >= 2
    assert {row["fund_code"] for row in rows} >= {"000001", "002207"}
    assert "best_estimate" in rows[0]


def test_web_detail_holdings_include_quote_and_contribution(tmp_path):
    session_factory = create_session_factory(tmp_path)
    data_source = make_mock_source()
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        fetch_and_store_stock_quotes(
            session,
            data_source,
            date.fromisoformat("2026-05-20"),
            date.fromisoformat("2026-05-20"),
            ["600988.SH", "000975.SZ"],
        )
        build_estimate_history(
            session,
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-20"),
            fund_code="002207",
        )
        detail = load_fund_detail_holdings(
            session,
            fund_code="002207",
            trade_date=date.fromisoformat("2026-05-20"),
        )

    assert detail["trade_date"] == date.fromisoformat("2026-05-20")
    assert detail["rows"]
    assert "contribution_pct" in detail["rows"][0]


def test_compute_live_fund_estimates_uses_live_quotes(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        results = compute_live_fund_estimates(
            session=session,
            live_quotes={
                "600988.SH": {"return_pct": -0.03},
                "000975.SZ": {"return_pct": 0.01},
            },
            trade_date=date.fromisoformat("2026-05-22"),
            fund_code="002207",
            selection_window=20,
            min_samples=10,
            min_improvement_bps=5,
            selection_policy="coverage_first",
        )

    assert len(results) == 1
    assert results[0].fund_code == "002207"
    assert results[0].holdings
    assert results[0].holdings[0].published_weight_pct > 0
    assert results[0].holdings[0].effective_weight_pct > results[0].holdings[0].published_weight_pct
    assert results[0].effective_method == "修正权重"


def test_live_estimate_results_fallback_to_latest_daily_quote(tmp_path):
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

    results, warnings, used_fallback = load_live_estimate_results(
        session_factory=session_factory,
        data_source=EmptyLiveDataSource(),
        selection_policy="coverage_first",
        window=20,
        min_samples=5,
        sleep_seconds=0.0,
        fund_code="002207",
    )

    assert used_fallback is True
    assert results
    assert results[0].quote_time is not None
    assert any("fallback" in warning for warning in warnings)


def test_effective_weight_versions_scale_to_stock_weight(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        results = build_effective_weight_versions(
            session=session,
            trade_date=date.fromisoformat("2026-05-22"),
            fund_code="002207",
        )

    assert len(results) == 1
    assert round(results[0].covered_weight, 8) == 0.35
    assert round(results[0].stock_weight or 0.0, 8) == 0.9
    assert round(results[0].total_effective_weight, 8) == 0.9


def test_live_effective_weight_estimate_matches_scaled_contribution(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        results = compute_live_fund_estimates(
            session=session,
            live_quotes={
                "600988.SH": {"return_pct": 0.03},
                "000975.SZ": {"return_pct": 0.01},
            },
            trade_date=date.fromisoformat("2026-05-22"),
            fund_code="002207",
            selection_window=20,
            min_samples=10,
            min_improvement_bps=5,
            selection_policy="coverage_first",
        )

    assert len(results) == 1
    result = results[0]
    expected_scale = 0.9 / 0.35
    expected_effective = (0.2 * expected_scale * 0.03) + (0.15 * expected_scale * 0.01)
    assert result.effective_weight_estimate is not None
    assert round(result.effective_weight_estimate, 8) == round(expected_effective, 8)


def test_tencent_live_quote_parser_builds_today_records():
    source = AKShareDataSource.__new__(AKShareDataSource)
    body = (
        'v_sh600988="1~赤峰黄金~600988~37.15~36.03~35.26~~~~~~~'
        '~~~~~~~~~~~~~~~~~~20260526113502~1.12~3.11~";\n'
        'v_sz000975="51~山金国际~000975~25.03~24.34~24.07~~~~~~~'
        '~~~~~~~~~~~~~~~~~~20260526113545~0.69~2.83~";'
    )
    records = source._parse_tencent_live_quote_response(body)

    assert len(records) == 2
    assert records[0].asset_code == "600988.SH"
    assert records[0].trade_date.isoformat() == "2026-05-26"
    assert round(records[0].return_pct, 6) == round((37.15 / 36.03) - 1.0, 6)


def test_user_position_today_profit_equals_amount_times_estimate(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        save_user_position_rows(
            session,
            [
                {
                    "fund_code": "002207",
                    "holding_amount": 3000.0,
                    "holding_share": None,
                    "cost_nav": None,
                    "platform": "支付宝",
                    "is_active": True,
                }
            ],
        )
        results = compute_live_fund_estimates(
            session=session,
            live_quotes={
                "600988.SH": {"return_pct": 0.03},
                "000975.SZ": {"return_pct": 0.01},
            },
            trade_date=date.fromisoformat("2026-05-22"),
            fund_code="002207",
        )

    result = results[0]
    assert result.holding_amount == 3000.0
    assert result.estimated_today_profit is not None
    assert abs(result.estimated_today_profit - (3000.0 * result.current_estimate)) < 0.001


def test_online_calibration_state_initializes_from_stock_weight(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        state = get_online_calibration_state(session, "002207", date.fromisoformat("2026-05-22"))
        assert state is not None
        assert round(state.base_scale_factor, 8) == round(0.9 / 0.35, 8)
        assert round(state.current_scale_factor, 8) == round(0.9 / 0.35, 8)


def test_online_calibration_state_updates_and_clips_scale(tmp_path):
    session_factory = create_session_factory(tmp_path)
    data_source = make_mock_source()
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        fetch_and_store_stock_quotes(
            session,
            data_source,
            date.fromisoformat("2026-05-20"),
            date.fromisoformat("2026-05-22"),
            ["600988.SH", "000975.SZ"],
        )
        fetch_and_store_fund_navs(
            session,
            data_source,
            "002207",
            date.fromisoformat("2026-05-20"),
            date.fromisoformat("2026-05-22"),
        )
        build_estimate_history(
            session,
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-22"),
            fund_code="002207",
        )
        build_reconcile_history(
            session,
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-22"),
            fund_code="002207",
        )
        states = refresh_online_calibration_states(session, fund_code="002207")

    assert states
    state = states[0]
    assert state.current_scale_factor >= state.min_scale_factor
    assert state.current_scale_factor <= state.max_scale_factor
    assert state.sample_count >= 1


def test_new_holding_version_resets_online_calibration_state(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        old_state = get_online_calibration_state(session, "002207", date.fromisoformat("2026-05-22"))
        assert old_state is not None
        old_holding_version_id = old_state.holding_version_id
        import_holdings_from_rows(
            session,
            [
                {
                    "fund_code": "002207",
                    "report_date": "2026-04-30",
                    "source": "new_report",
                    "asset_code": "600988.SH",
                    "asset_name": "赤峰黄金",
                    "asset_type": "stock",
                    "weight_pct": 18.0,
                },
                {
                    "fund_code": "002207",
                    "report_date": "2026-04-30",
                    "source": "new_report",
                    "asset_code": "000975.SZ",
                    "asset_name": "银泰黄金",
                    "asset_type": "stock",
                    "weight_pct": 12.0,
                },
            ],
        )
        new_state = get_online_calibration_state(session, "002207", date.fromisoformat("2026-05-22"))
        assert new_state is not None
        assert old_holding_version_id != new_state.holding_version_id
        assert new_state.sample_count == 0


def test_detail_contribution_sum_matches_current_estimate(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        results = compute_live_fund_estimates(
            session=session,
            live_quotes={
                "600988.SH": {"return_pct": 0.03},
                "000975.SZ": {"return_pct": 0.01},
            },
            trade_date=date.fromisoformat("2026-05-22"),
            fund_code="002207",
        )

    result = results[0]
    contribution_sum = sum(item.contribution_pct or 0.0 for item in result.holdings) / 100.0
    assert abs(contribution_sum - result.current_estimate) < 0.00001
