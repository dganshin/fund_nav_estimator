from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.estimator import (
    build_selected_estimates,
    build_selection_history,
    calculate_selected_stats,
    inspect_selected_estimates,
)
from src.models import SelectedEstimate
from tests.test_stage5 import create_session_factory, seed_selection_case


def test_coverage_first_prefers_coverage_when_samples_insufficient(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="200001",
            history_actuals=[0.01, 0.012, 0.011, 0.013, 0.012],
            raw_error=0.005,
            coverage_error=0.001,
            calibrated_error=0.0008,
            current_trade_date=date.fromisoformat("2026-05-21"),
        )
        result = build_selected_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            fund_code="200001",
            selection_window=20,
            min_samples=10,
            min_improvement_bps=5,
            selection_policy="coverage_first",
        )[0]

    assert result.best_method == "coverage_adjusted"
    assert result.best_status == "insufficient_samples_fallback"


def test_coverage_first_does_not_pick_raw_without_clear_advantage(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="200002",
            history_actuals=[0.01 + i * 0.0001 for i in range(12)],
            raw_error=0.0009,
            coverage_error=0.0011,
            calibrated_error=0.0013,
            current_trade_date=date.fromisoformat("2026-05-21"),
        )
        result = build_selected_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            fund_code="200002",
            selection_window=20,
            min_samples=10,
            min_improvement_bps=5,
            selection_policy="coverage_first",
        )[0]

    assert result.best_method == "coverage_adjusted"


def test_coverage_first_does_not_switch_to_calibrated_for_small_advantage(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="200003",
            history_actuals=[0.01 + i * 0.0001 for i in range(12)],
            raw_error=0.005,
            coverage_error=0.0010,
            calibrated_error=0.0008,
            current_trade_date=date.fromisoformat("2026-05-21"),
        )
        result = build_selected_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            fund_code="200003",
            selection_window=20,
            min_samples=10,
            min_improvement_bps=5,
            selection_policy="coverage_first",
        )[0]

    assert result.best_method == "coverage_adjusted"


def test_coverage_first_switches_to_calibrated_when_advantage_is_clear(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="200004",
            history_actuals=[0.01 + i * 0.0001 for i in range(12)],
            raw_error=0.005,
            coverage_error=0.0012,
            calibrated_error=0.0004,
            current_trade_date=date.fromisoformat("2026-05-21"),
        )
        result = build_selected_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            fund_code="200004",
            selection_window=20,
            min_samples=10,
            min_improvement_bps=5,
            selection_policy="coverage_first",
        )[0]

    assert result.best_method == "calibrated"


def test_selection_policy_is_written_and_isolated(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="200005",
            history_actuals=[0.01 + i * 0.0001 for i in range(12)],
            raw_error=0.005,
            coverage_error=0.0010,
            calibrated_error=0.0004,
            current_trade_date=date.fromisoformat("2026-05-21"),
        )
        build_selected_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            fund_code="200005",
            selection_policy="coverage_first",
        )
        build_selected_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            fund_code="200005",
            selection_policy="default",
        )
        rows = session.scalars(
            select(SelectedEstimate)
            .where(SelectedEstimate.fund_code == "200005")
            .order_by(SelectedEstimate.selection_policy.asc())
        ).all()

    assert len(rows) == 2
    assert {row.selection_policy for row in rows} == {"coverage_first", "default"}


def test_selected_stats_filter_by_selection_policy(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="200006",
            history_actuals=[0.01 + i * 0.0001 for i in range(12)],
            raw_error=0.005,
            coverage_error=0.0010,
            calibrated_error=0.0004,
            current_trade_date=date.fromisoformat("2026-05-21"),
        )
        build_selection_history(
            session,
            fund_code="200006",
            start_date=date.fromisoformat("2026-05-10"),
            end_date=date.fromisoformat("2026-05-21"),
            selection_window=20,
            min_samples=10,
            min_improvement_bps=5,
            selection_policy="coverage_first",
        )
        build_selection_history(
            session,
            fund_code="200006",
            start_date=date.fromisoformat("2026-05-10"),
            end_date=date.fromisoformat("2026-05-21"),
            selection_window=20,
            min_samples=10,
            min_improvement_bps=5,
            selection_policy="default",
        )
        coverage_stats = calculate_selected_stats(
            session,
            fund_code="200006",
            start_date=date.fromisoformat("2026-05-10"),
            end_date=date.fromisoformat("2026-05-21"),
            selection_window=20,
            selection_policy="coverage_first",
        )[0]
        default_stats = calculate_selected_stats(
            session,
            fund_code="200006",
            start_date=date.fromisoformat("2026-05-10"),
            end_date=date.fromisoformat("2026-05-21"),
            selection_window=20,
            selection_policy="default",
        )[0]

    assert coverage_stats.selection_policy == "coverage_first"
    assert default_stats.selection_policy == "default"
    assert coverage_stats.sample_count == default_stats.sample_count


def test_inspect_selections_can_show_why_raw_was_chosen(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="200007",
            history_actuals=[0.01 + i * 0.0001 for i in range(12)],
            raw_error=0.0002,
            coverage_error=0.0015,
            calibrated_error=0.0017,
            current_trade_date=date.fromisoformat("2026-05-21"),
        )
        build_selection_history(
            session,
            fund_code="200007",
            start_date=date.fromisoformat("2026-05-10"),
            end_date=date.fromisoformat("2026-05-21"),
            selection_window=20,
            min_samples=10,
            min_improvement_bps=5,
            selection_policy="coverage_first",
        )
        rows = inspect_selected_estimates(
            session,
            fund_code="200007",
            method="raw",
            start_date=date.fromisoformat("2026-05-10"),
            end_date=date.fromisoformat("2026-05-21"),
            selection_window=20,
            selection_policy="coverage_first",
        )

    assert rows
    assert any("raw" in row.decision_reason for row in rows)


def test_selected_estimates_same_policy_are_idempotent(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_selection_case(
            session,
            fund_code="200008",
            history_actuals=[0.01 + i * 0.0001 for i in range(12)],
            raw_error=0.005,
            coverage_error=0.0010,
            calibrated_error=0.0004,
            current_trade_date=date.fromisoformat("2026-05-21"),
        )
        build_selected_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            fund_code="200008",
            selection_policy="coverage_first",
        )
        build_selected_estimates(
            session,
            trade_date=date.fromisoformat("2026-05-21"),
            fund_code="200008",
            selection_policy="coverage_first",
        )
        count = session.scalar(select(func.count()).select_from(SelectedEstimate))

    assert count == 1
