from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backfill import backfill_history
from src.db import get_session_factory
from src.estimator import (
    build_selected_estimates,
    build_selection_history,
    calculate_selected_stats,
)
from src.init_db import init_db
from src.models import ActualReturn, CalibratedEstimate, Fund, FundAssetAllocation, FundEstimate, HoldingVersion, SelectedEstimate


def create_session_factory(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)
    return get_session_factory(db_url)


def seed_selection_case(
    session,
    fund_code: str,
    history_actuals: list[float],
    raw_error: float,
    coverage_error: float | None,
    calibrated_error: float | None,
    current_trade_date: date,
    add_allocation: bool = True,
) -> None:
    fund = Fund(
        fund_code=fund_code,
        fund_name=f"基金{fund_code}",
        fund_type="hybrid",
        market="CN",
        is_active=True,
    )
    session.add(fund)
    session.flush()

    version = HoldingVersion(
        fund_code=fund_code,
        report_date=current_trade_date - timedelta(days=30),
        source="test",
        total_weight=0.70,
        is_active=True,
    )
    session.add(version)
    session.flush()

    if add_allocation:
        session.add(
            FundAssetAllocation(
                fund_code=fund_code,
                report_date=current_trade_date - timedelta(days=30),
                source="test",
                stock_weight=0.90,
                bond_weight=0.0,
                cash_weight=0.0,
                other_weight=0.0,
                is_active=True,
            )
        )

    start_date = current_trade_date - timedelta(days=len(history_actuals))
    for index, actual in enumerate(history_actuals):
        trade_date = start_date + timedelta(days=index)
        raw_estimate = actual + raw_error
        coverage_estimate = None if coverage_error is None else actual + coverage_error
        calibrated_estimate = None if calibrated_error is None else actual + calibrated_error

        session.add(
            FundEstimate(
                trade_date=trade_date,
                fund_code=fund_code,
                holding_version_id=version.id,
                raw_estimate=raw_estimate,
                covered_weight=0.70,
                missing_weight=0.0,
                missing_assets_json="[]",
            )
        )
        session.add(
            ActualReturn(
                trade_date=trade_date,
                fund_code=fund_code,
                actual_return=actual,
                source="test",
            )
        )
        session.add(
            CalibratedEstimate(
                trade_date=trade_date,
                fund_code=fund_code,
                holding_version_id=version.id,
                base_estimate_type="coverage_adjusted",
                raw_estimate=raw_estimate,
                coverage_adjusted_estimate=coverage_estimate,
                calibrated_estimate=calibrated_estimate if calibrated_estimate is not None else raw_estimate,
                alpha=0.0,
                beta=1.0,
                window=20,
                sample_count=index,
                train_start_date=None,
                train_end_date=None,
                mean_abs_error=None,
                direction_hit_rate=None,
                estimate_actual_corr=None,
                model_status="ok",
                warning_json="[]",
                confidence_score=None,
                confidence_level=None,
            )
        )

    current_actual_anchor = history_actuals[-1] if history_actuals else 0.01
    current_raw = current_actual_anchor + raw_error
    current_coverage = None if coverage_error is None else current_actual_anchor + coverage_error
    current_calibrated = None if calibrated_error is None else current_actual_anchor + calibrated_error
    session.add(
        FundEstimate(
            trade_date=current_trade_date,
            fund_code=fund_code,
            holding_version_id=version.id,
            raw_estimate=current_raw,
            covered_weight=0.70,
            missing_weight=0.0,
            missing_assets_json="[]",
        )
    )
    session.add(
        CalibratedEstimate(
            trade_date=current_trade_date,
            fund_code=fund_code,
            holding_version_id=version.id,
            base_estimate_type="coverage_adjusted",
            raw_estimate=current_raw,
            coverage_adjusted_estimate=current_coverage,
            calibrated_estimate=current_calibrated if current_calibrated is not None else current_raw,
            alpha=0.0,
            beta=1.0,
            window=20,
            sample_count=len(history_actuals),
            train_start_date=None,
            train_end_date=None,
            mean_abs_error=None,
            direction_hit_rate=None,
            estimate_actual_corr=None,
            model_status="ok",
            warning_json="[]",
            confidence_score=None,
            confidence_level=None,
        )
    )
    session.commit()


def test_select_estimate_fallback_prefers_coverage_when_samples_insufficient(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="100001",
            history_actuals=[0.01, 0.012, 0.011, 0.013, 0.012],
            raw_error=0.005,
            coverage_error=0.001,
            calibrated_error=0.0008,
            current_trade_date=date.fromisoformat("2026-05-21"),
        )
        result = build_selected_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            fund_code="100001",
            selection_window=20,
            min_samples=10,
            min_improvement_bps=3,
        )[0]

    assert result.best_method == "coverage_adjusted"
    assert result.best_status == "insufficient_samples_fallback"
    assert result.confidence_level in {"C", "D"}


def test_select_estimate_fallback_prefers_raw_when_coverage_unavailable(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="100002",
            history_actuals=[0.01, 0.012, 0.011, 0.013, 0.012],
            raw_error=0.005,
            coverage_error=None,
            calibrated_error=None,
            current_trade_date=date.fromisoformat("2026-05-21"),
            add_allocation=False,
        )
        result = build_selected_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            fund_code="100002",
            selection_window=20,
            min_samples=10,
            min_improvement_bps=3,
        )[0]

    assert result.best_method == "raw"
    assert result.best_status == "insufficient_samples_fallback"


def test_select_estimate_prefers_coverage_when_clearly_better_than_raw(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="100003",
            history_actuals=[0.01 + i * 0.0001 for i in range(12)],
            raw_error=0.005,
            coverage_error=0.001,
            calibrated_error=0.0015,
            current_trade_date=date.fromisoformat("2026-05-21"),
        )
        result = build_selected_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            fund_code="100003",
            selection_window=20,
            min_samples=10,
            min_improvement_bps=3,
        )[0]

    assert result.best_method == "coverage_adjusted"


def test_select_estimate_does_not_switch_to_calibrated_when_advantage_too_small(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="100004",
            history_actuals=[0.01 + i * 0.0001 for i in range(12)],
            raw_error=0.005,
            coverage_error=0.0010,
            calibrated_error=0.0008,
            current_trade_date=date.fromisoformat("2026-05-21"),
        )
        result = build_selected_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            fund_code="100004",
            selection_window=20,
            min_samples=10,
            min_improvement_bps=3,
        )[0]

    assert result.best_method == "coverage_adjusted"


def test_select_estimate_switches_to_calibrated_when_advantage_is_clear(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="100005",
            history_actuals=[0.01 + i * 0.0001 for i in range(12)],
            raw_error=0.005,
            coverage_error=0.0010,
            calibrated_error=0.0004,
            current_trade_date=date.fromisoformat("2026-05-21"),
        )
        result = build_selected_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            fund_code="100005",
            selection_window=20,
            min_samples=10,
            min_improvement_bps=3,
        )[0]

    assert result.best_method == "calibrated"


def test_select_estimate_excludes_trade_date_and_future_from_history(tmp_path):
    session_factory = create_session_factory(tmp_path)
    target_date = date.fromisoformat("2026-05-21")
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="100006",
            history_actuals=[0.01 + i * 0.0001 for i in range(12)],
            raw_error=0.005,
            coverage_error=0.0010,
            calibrated_error=0.0015,
            current_trade_date=target_date,
        )
        result_before = build_selected_estimates(
            session,
            trade_date=target_date,
            fund_code="100006",
            selection_window=20,
            min_samples=10,
            min_improvement_bps=3,
        )[0]
        future_row = session.scalar(
            select(FundEstimate).where(
                FundEstimate.fund_code == "100006",
                FundEstimate.trade_date == target_date,
            )
        )
        assert future_row is not None
        session.add(
            FundEstimate(
                trade_date=date.fromisoformat("2026-05-22"),
                fund_code="100006",
                holding_version_id=future_row.holding_version_id,
                raw_estimate=0.5,
                covered_weight=0.70,
                missing_weight=0.0,
                missing_assets_json="[]",
            )
        )
        session.add(
            ActualReturn(
                trade_date=date.fromisoformat("2026-05-22"),
                fund_code="100006",
                actual_return=-0.5,
                source="test",
            )
        )
        session.add(
            CalibratedEstimate(
                trade_date=date.fromisoformat("2026-05-22"),
                fund_code="100006",
                holding_version_id=future_row.holding_version_id,
                base_estimate_type="coverage_adjusted",
                raw_estimate=0.5,
                coverage_adjusted_estimate=0.49,
                calibrated_estimate=-0.5,
                alpha=0.0,
                beta=1.0,
                window=20,
                sample_count=12,
                train_start_date=None,
                train_end_date=None,
                mean_abs_error=None,
                direction_hit_rate=None,
                estimate_actual_corr=None,
                model_status="ok",
                warning_json="[]",
                confidence_score=None,
                confidence_level=None,
            )
        )
        session.commit()
        result_after = build_selected_estimates(
            session,
            trade_date=target_date,
            fund_code="100006",
            selection_window=20,
            min_samples=10,
            min_improvement_bps=3,
        )[0]

    assert result_before.best_method == result_after.best_method


def test_select_history_uses_only_past_data_each_day(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="100007",
            history_actuals=[0.01, 0.011, 0.012],
            raw_error=0.005,
            coverage_error=0.001,
            calibrated_error=0.0004,
            current_trade_date=date.fromisoformat("2026-05-04"),
        )
        count = build_selection_history(
            session,
            fund_code="100007",
            start_date=date.fromisoformat("2026-05-02"),
            end_date=date.fromisoformat("2026-05-04"),
            selection_window=20,
            min_samples=1,
            min_improvement_bps=3,
        )
        rows = session.scalars(
            select(SelectedEstimate)
            .where(SelectedEstimate.fund_code == "100007")
            .order_by(SelectedEstimate.trade_date.asc())
        ).all()

    assert count == 3
    assert [row.sample_count for row in rows] == [1, 2, 3]


def test_selected_estimates_are_idempotent(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="100008",
            history_actuals=[0.01 + i * 0.0001 for i in range(12)],
            raw_error=0.005,
            coverage_error=0.001,
            calibrated_error=0.0004,
            current_trade_date=date.fromisoformat("2026-05-21"),
        )
        build_selected_estimates(session, date.fromisoformat("2026-05-21"), fund_code="100008")
        build_selected_estimates(session, date.fromisoformat("2026-05-21"), fund_code="100008")
        count = session.scalar(select(func.count()).select_from(SelectedEstimate))

    assert count == 1


def test_selected_stats_compute_best_mae_correctly(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="100009",
            history_actuals=[0.01 + i * 0.0001 for i in range(12)],
            raw_error=0.005,
            coverage_error=0.001,
            calibrated_error=0.0015,
            current_trade_date=date.fromisoformat("2026-05-21"),
        )
        build_selection_history(
            session,
            fund_code="100009",
            start_date=date.fromisoformat("2026-05-10"),
            end_date=date.fromisoformat("2026-05-21"),
            selection_window=20,
            min_samples=10,
            min_improvement_bps=3,
        )
        stats = calculate_selected_stats(
            session,
            fund_code="100009",
            start_date=date.fromisoformat("2026-05-10"),
            end_date=date.fromisoformat("2026-05-21"),
            selection_window=20,
        )[0]
        rows = session.execute(
            select(SelectedEstimate, ActualReturn)
            .join(
                ActualReturn,
                (ActualReturn.trade_date == SelectedEstimate.trade_date)
                & (ActualReturn.fund_code == SelectedEstimate.fund_code),
            )
            .where(SelectedEstimate.fund_code == "100009")
        ).all()
        expected = sum(abs(actual.actual_return - selected.best_estimate) for selected, actual in rows) / len(rows)

    assert round(stats.best_mean_abs_error, 10) == round(expected, 10)


def test_backfill_history_calls_select_history(tmp_path):
    from tests.test_stage4 import MockDataSource, seed_fund_holdings_and_allocations, make_mock_source

    session_factory = create_session_factory(tmp_path)
    data_source = make_mock_source()
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        result = backfill_history(
            session=session,
            data_source=data_source,
            fund_code="002207",
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-22"),
            window=2,
            base="coverage_adjusted",
            min_samples=1,
        )
        count = session.scalar(select(func.count()).select_from(SelectedEstimate))

    assert result[5] > 0
    assert count > 0
