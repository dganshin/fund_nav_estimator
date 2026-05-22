from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backfill import fetch_and_store_fund_navs, fetch_and_store_stock_quotes
from src.db import get_session_factory
from src.estimator import build_calibration_history, build_estimate_history, build_reconcile_history, compute_live_fund_estimates
from src.import_data import (
    import_asset_allocations_from_rows,
    import_funds_from_rows,
    import_holdings_from_rows,
    import_industry_allocations_from_rows,
)
from src.init_db import init_db
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
)
from tests.test_stage4 import make_mock_source, seed_fund_holdings_and_allocations


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
    assert results[0].final_method in {"raw", "coverage_adjusted", "calibrated"}
