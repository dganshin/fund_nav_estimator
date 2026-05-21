from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from sqlalchemy import and_, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import get_session_factory
from src.estimator import (
    build_calibration_history,
    build_estimate_errors,
    build_fund_estimates,
    calculate_calibration_stats,
    calculate_compare_estimates,
    calculate_error_stats,
)
from src.import_data import (
    import_asset_allocations_from_csv,
    import_funds_from_csv,
    import_holdings_from_csv,
    import_industry_allocations_from_csv,
    import_navs_from_csv,
    import_quotes_from_csv,
)
from src.init_db import init_db
from src.models import ActualReturn, CalibratedEstimate, FundEstimate


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


def test_calibration_stats_base_raw_uses_raw_mae(tmp_path):
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
        stats = calculate_calibration_stats(
            session,
            fund_code="000001",
            window=5,
            base="raw",
        )
        rows = session.execute(
            select(CalibratedEstimate, ActualReturn)
            .join(
                ActualReturn,
                and_(
                    ActualReturn.trade_date == CalibratedEstimate.trade_date,
                    ActualReturn.fund_code == CalibratedEstimate.fund_code,
                ),
            )
            .where(
                CalibratedEstimate.fund_code == "000001",
                CalibratedEstimate.window == 5,
                CalibratedEstimate.base_estimate_type == "raw",
            )
        ).all()

    expected = sum(abs(actual.actual_return - calibrated.raw_estimate) for calibrated, actual in rows) / len(rows)
    assert len(stats) == 1
    assert round(stats[0].base_mean_abs_error, 10) == round(expected, 10)
    assert stats[0].base_estimate_type == "raw"


def test_calibration_stats_base_coverage_uses_coverage_mae(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        prepare_demo_dataset(session)
        build_calibration_history(
            session,
            start_date=date.fromisoformat("2026-05-18"),
            end_date=date.fromisoformat("2026-05-21"),
            window=5,
            base="coverage_adjusted",
            fund_code="000002",
            min_samples=2,
        )
        stats = calculate_calibration_stats(
            session,
            fund_code="000002",
            window=5,
            base="coverage_adjusted",
        )
        rows = session.execute(
            select(CalibratedEstimate, ActualReturn)
            .join(
                ActualReturn,
                and_(
                    ActualReturn.trade_date == CalibratedEstimate.trade_date,
                    ActualReturn.fund_code == CalibratedEstimate.fund_code,
                ),
            )
            .where(
                CalibratedEstimate.fund_code == "000002",
                CalibratedEstimate.window == 5,
                CalibratedEstimate.base_estimate_type == "coverage_adjusted",
            )
        ).all()

    expected_pairs = [
        (calibrated.coverage_adjusted_estimate, actual.actual_return)
        for calibrated, actual in rows
        if calibrated.coverage_adjusted_estimate is not None
    ]
    expected = sum(abs(actual_return - estimate) for estimate, actual_return in expected_pairs) / len(expected_pairs)
    assert len(stats) == 1
    assert round(stats[0].base_mean_abs_error, 10) == round(expected, 10)
    assert stats[0].base_estimate_type == "coverage_adjusted"


def test_calibration_stats_negative_improvement_is_supported(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        prepare_demo_dataset(session)
        build_calibration_history(
            session,
            start_date=date.fromisoformat("2026-05-18"),
            end_date=date.fromisoformat("2026-05-21"),
            window=5,
            base="coverage_adjusted",
            fund_code="000002",
            min_samples=2,
        )
        rows = session.scalars(
            select(CalibratedEstimate).where(
                CalibratedEstimate.fund_code == "000002",
                CalibratedEstimate.window == 5,
                CalibratedEstimate.base_estimate_type == "coverage_adjusted",
            )
        ).all()
        for row in rows:
            row.calibrated_estimate = row.raw_estimate + 0.05
        session.commit()
        stats = calculate_calibration_stats(
            session,
            fund_code="000002",
            window=5,
            base="coverage_adjusted",
        )

    assert len(stats) == 1
    assert stats[0].improvement_pct is not None
    assert stats[0].improvement_pct < 0


def test_compare_estimates_picks_lowest_mae_method(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        prepare_demo_dataset(session)
        build_calibration_history(
            session,
            start_date=date.fromisoformat("2026-05-18"),
            end_date=date.fromisoformat("2026-05-21"),
            window=5,
            base="coverage_adjusted",
            fund_code="000002",
            min_samples=2,
        )
        rows = session.scalars(
            select(CalibratedEstimate).where(
                CalibratedEstimate.fund_code == "000002",
                CalibratedEstimate.window == 5,
                CalibratedEstimate.base_estimate_type == "coverage_adjusted",
            )
        ).all()
        for row in rows:
            row.calibrated_estimate = row.raw_estimate + 0.05
        session.commit()
        results = calculate_compare_estimates(
            session,
            fund_code="000002",
            window=5,
            base="coverage_adjusted",
            start_date=date.fromisoformat("2026-05-18"),
            end_date=date.fromisoformat("2026-05-21"),
        )

    assert len(results) == 1
    assert results[0].best_method == "coverage_adjusted"


def test_stats_support_start_end_date_filter(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        prepare_demo_dataset(session)
        all_results = calculate_error_stats(session, fund_code="000001")
        filtered_results = calculate_error_stats(
            session,
            fund_code="000001",
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-21"),
        )

    assert all_results[0].sample_count == 6
    assert filtered_results[0].sample_count == 2


def test_calibration_stats_support_start_end_date_filter(tmp_path):
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
        filtered_results = calculate_calibration_stats(
            session,
            fund_code="000001",
            window=5,
            base="raw",
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-21"),
        )

    assert len(filtered_results) == 1
    assert filtered_results[0].sample_count == 2


def test_compare_estimates_support_start_end_date_filter(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        prepare_demo_dataset(session)
        build_calibration_history(
            session,
            start_date=date.fromisoformat("2026-05-18"),
            end_date=date.fromisoformat("2026-05-21"),
            window=5,
            base="coverage_adjusted",
            fund_code="000002",
            min_samples=2,
        )
        filtered_results = calculate_compare_estimates(
            session,
            fund_code="000002",
            window=5,
            base="coverage_adjusted",
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-21"),
        )

    assert len(filtered_results) == 1
    assert filtered_results[0].sample_count == 2
