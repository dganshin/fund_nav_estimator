from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import get_session_factory
from src.estimator import build_estimate_errors, build_fund_estimates, calculate_error_stats
from src.import_data import import_actual_returns_from_csv, import_funds_from_csv, import_holdings_from_csv, import_navs_from_csv, import_quotes_from_csv
from src.init_db import init_db
from src.models import ActualReturn, EstimateError


def write_csv(path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")


def create_session_factory(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)
    return get_session_factory(db_url)


def seed_fund_and_holdings(tmp_path, session):
    funds_csv = tmp_path / "funds.csv"
    holdings_csv = tmp_path / "holdings.csv"
    write_csv(
        funds_csv,
        """
        fund_code,fund_name,fund_type,market,is_active
        000001,测试基金,equity,CN,true
        """,
    )
    write_csv(
        holdings_csv,
        """
        fund_code,report_date,source,asset_code,asset_name,asset_type,weight_pct
        000001,2026-05-01,test,000001.SZ,平安银行,stock,10
        000001,2026-05-01,test,600519.SH,贵州茅台,stock,20
        """,
    )
    import_funds_from_csv(session, funds_csv)
    import_holdings_from_csv(session, holdings_csv)


def test_import_actual_return_pct_converts_to_decimal(tmp_path):
    session_factory = create_session_factory(tmp_path)
    actuals_csv = tmp_path / "actuals.csv"

    with session_factory() as session:
        seed_fund_and_holdings(tmp_path, session)
        write_csv(
            actuals_csv,
            """
            trade_date,fund_code,actual_return_pct,source
            2026-05-21,000001,0.62,manual
            """,
        )
        report = import_actual_returns_from_csv(session, actuals_csv)
        actual = session.get(
            ActualReturn,
            {"trade_date": date.fromisoformat("2026-05-21"), "fund_code": "000001"},
        )

    assert report.imported_count == 1
    assert actual is not None
    assert actual.actual_return == 0.0062


def test_import_navs_generates_actual_return(tmp_path):
    session_factory = create_session_factory(tmp_path)
    navs_csv = tmp_path / "navs.csv"

    with session_factory() as session:
        seed_fund_and_holdings(tmp_path, session)
        write_csv(
            navs_csv,
            """
            trade_date,fund_code,unit_nav,accumulated_nav,source
            2026-05-20,000001,1.2300,1.5300,manual
            2026-05-21,000001,1.2376,1.5376,manual
            """,
        )
        report = import_navs_from_csv(session, navs_csv)
        actual = session.get(
            ActualReturn,
            {"trade_date": date.fromisoformat("2026-05-21"), "fund_code": "000001"},
        )

    assert report.imported_count == 2
    assert report.generated_actual_returns == 1
    assert actual is not None
    assert round(actual.actual_return, 8) == round(1.2376 / 1.23 - 1, 8)


def test_reconcile_computes_error_abs_error_and_direction_hit(tmp_path):
    session_factory = create_session_factory(tmp_path)
    quotes_csv = tmp_path / "quotes.csv"
    actuals_csv = tmp_path / "actuals.csv"

    with session_factory() as session:
        seed_fund_and_holdings(tmp_path, session)
        write_csv(
            quotes_csv,
            """
            trade_date,asset_code,asset_name,return_pct,source
            2026-05-21,000001.SZ,平安银行,2.0,test
            2026-05-21,600519.SH,贵州茅台,1.0,test
            """,
        )
        write_csv(
            actuals_csv,
            """
            trade_date,fund_code,actual_return_pct,source
            2026-05-21,000001,0.50,manual
            """,
        )
        import_quotes_from_csv(session, quotes_csv)
        import_actual_returns_from_csv(session, actuals_csv)
        build_fund_estimates(session, date.fromisoformat("2026-05-21"))
        report = build_estimate_errors(session, date.fromisoformat("2026-05-21"))
        error = session.get(
            EstimateError,
            {"trade_date": date.fromisoformat("2026-05-21"), "fund_code": "000001"},
        )

    assert len(report.results) == 1
    assert error is not None
    assert round(error.error, 8) == 0.001
    assert round(error.abs_error, 8) == 0.001
    assert error.direction_hit is True


def test_reconcile_is_idempotent(tmp_path):
    session_factory = create_session_factory(tmp_path)
    quotes_csv = tmp_path / "quotes.csv"
    actuals_csv = tmp_path / "actuals.csv"

    with session_factory() as session:
        seed_fund_and_holdings(tmp_path, session)
        write_csv(
            quotes_csv,
            """
            trade_date,asset_code,asset_name,return_pct,source
            2026-05-21,000001.SZ,平安银行,2.0,test
            2026-05-21,600519.SH,贵州茅台,1.0,test
            """,
        )
        write_csv(
            actuals_csv,
            """
            trade_date,fund_code,actual_return_pct,source
            2026-05-21,000001,0.50,manual
            """,
        )
        import_quotes_from_csv(session, quotes_csv)
        import_actual_returns_from_csv(session, actuals_csv)
        build_fund_estimates(session, date.fromisoformat("2026-05-21"))
        build_estimate_errors(session, date.fromisoformat("2026-05-21"))
        build_estimate_errors(session, date.fromisoformat("2026-05-21"))
        error_count = session.scalar(select(func.count()).select_from(EstimateError))

    assert error_count == 1


def test_stats_computes_mean_abs_error_and_direction_hit_rate(tmp_path):
    session_factory = create_session_factory(tmp_path)
    quotes_csv = tmp_path / "quotes.csv"
    actuals_csv = tmp_path / "actuals.csv"

    with session_factory() as session:
        seed_fund_and_holdings(tmp_path, session)
        write_csv(
            quotes_csv,
            """
            trade_date,asset_code,asset_name,return_pct,source
            2026-05-20,000001.SZ,平安银行,1.0,test
            2026-05-20,600519.SH,贵州茅台,1.0,test
            2026-05-21,000001.SZ,平安银行,-1.0,test
            2026-05-21,600519.SH,贵州茅台,-1.0,test
            """,
        )
        write_csv(
            actuals_csv,
            """
            trade_date,fund_code,actual_return_pct,source
            2026-05-20,000001,0.40,manual
            2026-05-21,000001,-0.20,manual
            """,
        )
        import_quotes_from_csv(session, quotes_csv)
        import_actual_returns_from_csv(session, actuals_csv)
        build_fund_estimates(session, date.fromisoformat("2026-05-20"))
        build_fund_estimates(session, date.fromisoformat("2026-05-21"))
        build_estimate_errors(session, date.fromisoformat("2026-05-20"))
        build_estimate_errors(session, date.fromisoformat("2026-05-21"))
        stats_results = calculate_error_stats(session, fund_code="000001")

    assert len(stats_results) == 1
    result = stats_results[0]
    assert round(result.mean_abs_error, 8) == 0.001
    assert result.direction_hit_rate == 1.0


def test_reconcile_skips_missing_actual_return_without_failing(tmp_path):
    session_factory = create_session_factory(tmp_path)
    quotes_csv = tmp_path / "quotes.csv"

    with session_factory() as session:
        seed_fund_and_holdings(tmp_path, session)
        write_csv(
            quotes_csv,
            """
            trade_date,asset_code,asset_name,return_pct,source
            2026-05-21,000001.SZ,平安银行,2.0,test
            2026-05-21,600519.SH,贵州茅台,1.0,test
            """,
        )
        import_quotes_from_csv(session, quotes_csv)
        build_fund_estimates(session, date.fromisoformat("2026-05-21"))
        report = build_estimate_errors(session, date.fromisoformat("2026-05-21"))

    assert len(report.results) == 0
    assert len(report.warnings) == 1
