from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from ..backfill import backfill_history
from ..estimator import (
    build_effective_weight_versions,
    build_calibration_history,
    build_estimate_history,
    build_reconcile_history,
    build_selection_history,
)


@dataclass
class ActionReport:
    logs: list[str]
    warnings: list[str]
    payload: dict[str, object]


def run_backfill_action(
    session: Session,
    data_source,
    fund_code: str,
    start_date: date,
    end_date: date,
    window: int,
    base: str,
    min_samples: int,
    selection_policy: str,
    sleep_seconds: float,
) -> ActionReport:
    nav_report, quote_report, estimate_report, reconcile_report, calibration_count, selection_count, summaries = backfill_history(
        session=session,
        data_source=data_source,
        fund_code=fund_code,
        start_date=start_date,
        end_date=end_date,
        window=window,
        base=base,
        min_samples=min_samples,
        selection_policy=selection_policy,
        sleep_seconds=sleep_seconds,
    )
    logs = [
        f"Imported fund nav rows: {nav_report.imported_count}",
        f"Imported daily quotes: {quote_report.imported_count}",
        f"Built historical estimates: {estimate_report.total_count}",
        f"Built historical estimate errors: {reconcile_report.total_count}",
        f"Built calibration history rows: {calibration_count}",
        f"Built selected estimate rows: {selection_count}",
    ]
    warnings = nav_report.warnings + quote_report.warnings + estimate_report.warnings + reconcile_report.warnings
    payload = {
        "nav_report": nav_report,
        "quote_report": quote_report,
        "estimate_report": estimate_report,
        "reconcile_report": reconcile_report,
        "calibration_count": calibration_count,
        "selection_count": selection_count,
        "summaries": summaries,
    }
    return ActionReport(logs=logs, warnings=warnings, payload=payload)


def run_recalculate_action(
    session: Session,
    fund_code: str,
    start_date: date,
    end_date: date,
    window: int,
    base: str,
    min_samples: int,
    selection_policy: str,
) -> ActionReport:
    estimate_report = build_estimate_history(
        session=session,
        start_date=start_date,
        end_date=end_date,
        fund_code=fund_code,
    )
    reconcile_report = build_reconcile_history(
        session=session,
        start_date=start_date,
        end_date=end_date,
        fund_code=fund_code,
    )
    calibration_count = build_calibration_history(
        session=session,
        start_date=start_date,
        end_date=end_date,
        window=window,
        base=base,
        fund_code=fund_code,
        min_samples=min_samples,
    )
    selection_count = build_selection_history(
        session=session,
        start_date=start_date,
        end_date=end_date,
        fund_code=fund_code,
        selection_window=window,
        min_samples=max(10, min_samples),
        min_improvement_bps=5,
        selection_policy=selection_policy,
    )
    return ActionReport(
        logs=[
            f"Built historical estimates: {estimate_report.total_count}",
            f"Built historical estimate errors: {reconcile_report.total_count}",
            f"Built calibration history rows: {calibration_count}",
            f"Built selected estimate rows: {selection_count}",
        ],
        warnings=estimate_report.warnings + reconcile_report.warnings,
        payload={
            "estimate_report": estimate_report,
            "reconcile_report": reconcile_report,
            "calibration_count": calibration_count,
            "selection_count": selection_count,
        },
    )


def run_selection_action(
    session: Session,
    fund_code: str,
    start_date: date,
    end_date: date,
    selection_window: int,
    min_samples: int,
    min_improvement_bps: int,
    selection_policy: str,
) -> ActionReport:
    selection_count = build_selection_history(
        session=session,
        start_date=start_date,
        end_date=end_date,
        fund_code=fund_code,
        selection_window=selection_window,
        min_samples=min_samples,
        min_improvement_bps=min_improvement_bps,
        selection_policy=selection_policy,
    )
    return ActionReport(
        logs=[f"Built selected estimate rows: {selection_count}"],
        warnings=[],
        payload={"selection_count": selection_count},
    )


def run_effective_weight_action(
    session: Session,
    fund_code: str,
    trade_date: date,
) -> ActionReport:
    results = build_effective_weight_versions(
        session=session,
        trade_date=trade_date,
        fund_code=fund_code,
    )
    logs = [
        (
            f"{item.fund_code} 修正权重已更新: "
            f"覆盖权重 {item.covered_weight * 100:.2f}%, "
            f"股票仓位 {0.0 if item.stock_weight is None else item.stock_weight * 100:.2f}%, "
            f"修正后合计 {item.total_effective_weight * 100:.2f}%"
        )
        for item in results
    ]
    warnings = [warning for item in results for warning in item.warnings]
    return ActionReport(
        logs=logs or ["没有可更新的修正权重"],
        warnings=warnings,
        payload={"effective_weight_count": len(results)},
    )
