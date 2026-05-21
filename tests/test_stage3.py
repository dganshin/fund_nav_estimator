from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import get_session_factory
from src.estimator import build_calibrated_estimates, build_calibration_history, build_estimate_errors, build_fund_estimates, calculate_calibration_stats, determine_confidence
from src.import_data import import_asset_allocations_from_csv, import_funds_from_csv, import_holdings_from_csv, import_industry_allocations_from_csv, import_navs_from_csv, import_quotes_from_csv
from src.init_db import init_db
from src.models import ActualReturn, CalibratedEstimate, Fund, FundAssetAllocation, FundIndustryAllocation


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def create_session_factory(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)
    return get_session_factory(db_url)


def prepare_demo_dataset(session) -> None:
    import_funds_from_csv(session, PROJECT_ROOT / "data" / "example_funds.csv")
    import_holdings_from_csv(session, PROJECT_ROOT / "data" / "example_holdings.csv")
    import_quotes_from_csv(session, PROJECT_ROOT / "data" / "example_quotes.csv")
    import_asset_allocations_from_csv(session, PROJECT_ROOT / "data" / "example_asset_allocations.csv")
    import_industry_allocations_from_csv(session, PROJECT_ROOT / "data" / "example_industry_allocations.csv")
    import_navs_from_csv(session, PROJECT_ROOT / "data" / "example_fund_navs.csv")

    for trade_date in [
        "2026-05-16",
        "2026-05-17",
        "2026-05-18",
        "2026-05-19",
        "2026-05-20",
        "2026-05-21",
    ]:
        build_fund_estimates(session, date.fromisoformat(trade_date))
        build_estimate_errors(session, date.fromisoformat(trade_date))


def write_csv(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")


def test_rolling_calibration_computes_alpha_beta_and_estimate(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        prepare_demo_dataset(session)
        results = build_calibrated_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            window=5,
            base="raw",
            fund_code="000001",
            min_samples=5,
        )

    assert len(results) == 1
    result = results[0]
    assert result.model_status == "ok"
    assert result.sample_count == 5
    assert round(result.alpha, 8) != 0
    assert round(result.beta, 8) != 1
    assert round(result.calibrated_estimate, 8) != round(result.raw_estimate, 8)


def test_insufficient_samples_falls_back_to_raw_estimate(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        prepare_demo_dataset(session)
        results = build_calibrated_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            window=20,
            base="raw",
            fund_code="000001",
            min_samples=6,
        )

    result = results[0]
    assert result.model_status == "insufficient_samples"
    assert result.calibrated_estimate == result.raw_estimate
    assert result.alpha == 0
    assert result.beta == 1


def test_training_data_excludes_trade_date_and_future(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        prepare_demo_dataset(session)
        baseline = build_calibrated_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            window=5,
            base="raw",
            fund_code="000001",
            min_samples=5,
        )[0]
        current_actual = session.get(
            ActualReturn,
            {"trade_date": date.fromisoformat("2026-05-21"), "fund_code": "000001"},
        )
        assert current_actual is not None
        current_actual.actual_return = 0.5
        session.commit()

        after_change = build_calibrated_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            window=5,
            base="raw",
            fund_code="000001",
            min_samples=5,
        )[0]

    assert round(baseline.alpha, 8) == round(after_change.alpha, 8)
    assert round(baseline.beta, 8) == round(after_change.beta, 8)


def test_coverage_adjusted_estimate_is_calculated_correctly(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        prepare_demo_dataset(session)
        result = build_calibrated_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            window=5,
            base="coverage_adjusted",
            fund_code="000002",
            min_samples=5,
        )[0]

    assert result.coverage_adjusted_estimate is not None
    assert round(result.coverage_adjusted_estimate, 8) == round(0.00483 / 0.483 * 0.908, 8)


def test_missing_asset_allocation_falls_back_to_raw(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        import_funds_from_csv(session, PROJECT_ROOT / "data" / "example_funds.csv")
        import_holdings_from_csv(session, PROJECT_ROOT / "data" / "example_holdings.csv")
        import_quotes_from_csv(session, PROJECT_ROOT / "data" / "example_quotes.csv")
        import_navs_from_csv(session, PROJECT_ROOT / "data" / "example_fund_navs.csv")
        for trade_date in [
            "2026-05-16",
            "2026-05-17",
            "2026-05-18",
            "2026-05-19",
            "2026-05-20",
            "2026-05-21",
        ]:
            build_fund_estimates(session, date.fromisoformat(trade_date))
            build_estimate_errors(session, date.fromisoformat(trade_date))

        result = build_calibrated_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            window=5,
            base="coverage_adjusted",
            fund_code="000001",
            min_samples=5,
        )[0]

    assert result.coverage_adjusted_estimate is None
    assert any("falls back to raw_estimate" in warning for warning in result.warnings)


def test_calibrated_estimates_are_idempotent(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        prepare_demo_dataset(session)
        build_calibrated_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            window=5,
            base="raw",
            fund_code="000001",
            min_samples=5,
        )
        build_calibrated_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            window=5,
            base="raw",
            fund_code="000001",
            min_samples=5,
        )
        count = session.scalar(select(func.count()).select_from(CalibratedEstimate))

    assert count == 1


def test_calibrate_history_does_not_use_future_data(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        prepare_demo_dataset(session)
        build_calibration_history(
            session,
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-21"),
            window=5,
            base="raw",
            fund_code="000001",
            min_samples=5,
        )
        rows = session.scalars(
            select(CalibratedEstimate)
            .where(CalibratedEstimate.fund_code == "000001")
            .order_by(CalibratedEstimate.trade_date.asc())
        ).all()

    assert len(rows) == 2
    assert rows[0].trade_date == date.fromisoformat("2026-05-20")
    assert rows[0].sample_count == 4
    assert rows[0].model_status == "insufficient_samples"
    assert rows[1].trade_date == date.fromisoformat("2026-05-21")
    assert rows[1].sample_count == 5


def test_calibration_stats_compares_raw_and_calibrated_mae(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        prepare_demo_dataset(session)
        build_calibration_history(
            session,
            start_date=date.fromisoformat("2026-05-18"),
            end_date=date.fromisoformat("2026-05-21"),
            window=5,
            base="raw",
            fund_code="000001",
            min_samples=2,
        )
        stats = calculate_calibration_stats(session, fund_code="000001", window=5, base="raw")

    assert len(stats) == 1
    assert stats[0].calibrated_mean_abs_error <= stats[0].raw_mean_abs_error


def test_confidence_level_rules_work(tmp_path):
    assert determine_confidence(20, 0.002, 0.8, 0.8, "ok") == (0.9, "A")
    assert determine_confidence(12, 0.005, 0.7, 0.5, "ok") == (0.75, "B")
    assert determine_confidence(5, 0.01, 0.5, 0.2, "ok") == (0.6, "C")
    assert determine_confidence(3, None, None, None, "insufficient_samples") == (0.25, "D")


def test_import_asset_and_industry_allocations_preserve_leading_zero_and_convert_percent(tmp_path):
    session_factory = create_session_factory(tmp_path)
    funds_csv = tmp_path / "funds.csv"
    asset_csv = tmp_path / "asset_alloc.csv"
    industry_csv = tmp_path / "industry_alloc.csv"
    write_csv(
        funds_csv,
        """
        fund_code,fund_name,fund_type,market,is_active
        000123,测试资源主题,equity,CN,true
        """,
    )
    write_csv(
        asset_csv,
        """
        fund_code,report_date,source,stock_weight_pct,bond_weight_pct,cash_weight_pct,other_weight_pct
        000123,2026-03-31,manual,90.80,0,8.50,0.70
        """,
    )
    write_csv(
        industry_csv,
        """
        fund_code,report_date,source,industry_name,industry_code,weight_pct
        000123,2026-03-31,manual,采矿业,B,78.16
        """,
    )

    with session_factory() as session:
        import_funds_from_csv(session, funds_csv)
        import_asset_allocations_from_csv(session, asset_csv)
        import_industry_allocations_from_csv(session, industry_csv)
        fund = session.get(Fund, "000123")
        asset = session.scalars(select(FundAssetAllocation)).first()
        industry = session.scalars(select(FundIndustryAllocation)).first()

    assert fund is not None
    assert asset is not None
    assert industry is not None
    assert asset.fund_code == "000123"
    assert round(asset.stock_weight, 8) == 0.908
    assert industry.fund_code == "000123"
    assert round(industry.weight, 8) == 0.7816
