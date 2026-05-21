from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .data_sources.base import DataSource
from .estimator import (
    HistoryBuildReport,
    build_calibration_history,
    build_estimate_history,
    build_reconcile_history,
    build_selection_history,
    calculate_selected_stats,
)
from .import_data import ImportReport, import_nav_records, import_quote_records
from .models import Fund, FundEstimate, HoldingVersion


@dataclass
class BackfillSummary:
    fund_code: str
    fund_name: str
    start_date: date
    end_date: date
    sample_count: int
    raw_mean_abs_error: float | None
    coverage_mean_abs_error: float | None
    calibrated_mean_abs_error: float | None
    best_mean_abs_error: float | None
    best_method_distribution: str
    confidence_level: str | None


def get_active_holding_asset_codes(
    session: Session,
    fund_code: str,
    trade_date: date | None = None,
) -> list[str]:
    stmt = select(HoldingVersion).where(
        HoldingVersion.fund_code == fund_code,
        HoldingVersion.is_active.is_(True),
    )
    if trade_date is not None:
        stmt = stmt.where(HoldingVersion.report_date <= trade_date)
    stmt = stmt.order_by(HoldingVersion.report_date.desc(), HoldingVersion.created_at.desc())
    version = session.scalars(stmt).first()
    if version is None:
        return []
    return [item.asset_code for item in version.items]


def fetch_and_store_fund_navs(
    session: Session,
    data_source: DataSource,
    fund_code: str,
    start_date: date,
    end_date: date,
) -> ImportReport:
    if hasattr(data_source, "last_warnings"):
        data_source.last_warnings = []  # type: ignore[attr-defined]
    records = data_source.fetch_fund_navs(
        fund_code=fund_code,
        start_date=start_date,
        end_date=end_date,
    )
    warnings = list(getattr(data_source, "last_warnings", []))
    if not records:
        warnings.append(f"Warning: no fund navs fetched for {fund_code}.")
        return ImportReport(imported_count=0, warnings=warnings)
    report = import_nav_records(session, records)
    report.warnings = warnings + report.warnings
    return report


def fetch_and_store_stock_quotes(
    session: Session,
    data_source: DataSource,
    start_date: date,
    end_date: date,
    asset_codes: list[str],
    sleep_seconds: float = 0.0,
) -> ImportReport:
    if not asset_codes:
        return ImportReport(imported_count=0, warnings=["Warning: no asset codes available for quote fetch."])
    if hasattr(data_source, "last_warnings"):
        data_source.last_warnings = []  # type: ignore[attr-defined]
    records = data_source.fetch_stock_daily_quotes(
        asset_codes=asset_codes,
        start_date=start_date,
        end_date=end_date,
        sleep_seconds=sleep_seconds,
    )
    warnings = list(getattr(data_source, "last_warnings", []))
    if not records:
        warnings.append("Warning: no stock quotes fetched.")
        return ImportReport(imported_count=0, warnings=warnings)
    count = import_quote_records(session, records)
    return ImportReport(imported_count=count, warnings=warnings)


def backfill_history(
    session: Session,
    data_source: DataSource,
    fund_code: str,
    start_date: date,
    end_date: date,
    window: int = 20,
    base: str = "coverage_adjusted",
    min_samples: int = 5,
    sleep_seconds: float = 0.0,
) -> tuple[ImportReport, ImportReport, HistoryBuildReport, HistoryBuildReport, int, int, list[BackfillSummary]]:
    nav_report = fetch_and_store_fund_navs(
        session=session,
        data_source=data_source,
        fund_code=fund_code,
        start_date=start_date,
        end_date=end_date,
    )
    asset_codes = get_active_holding_asset_codes(session, fund_code=fund_code, trade_date=end_date)
    quote_report = fetch_and_store_stock_quotes(
        session=session,
        data_source=data_source,
        start_date=start_date,
        end_date=end_date,
        asset_codes=asset_codes,
        sleep_seconds=sleep_seconds,
    )
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
        min_improvement_bps=3,
    )
    selected_stats = calculate_selected_stats(
        session=session,
        fund_code=fund_code,
        start_date=start_date,
        end_date=end_date,
        selection_window=window,
    )

    summaries: list[BackfillSummary] = []
    fund = session.get(Fund, fund_code)
    for stat in selected_stats:
        from .models import SelectedEstimate

        latest_estimate = session.scalars(
            select(SelectedEstimate)
            .where(
                SelectedEstimate.fund_code == stat.fund_code,
                SelectedEstimate.trade_date >= start_date,
                SelectedEstimate.trade_date <= end_date,
                SelectedEstimate.selection_window == window,
            )
            .order_by(SelectedEstimate.trade_date.desc())
        ).first()
        confidence_level = None
        if latest_estimate is not None:
            confidence_level = latest_estimate.confidence_level

        summaries.append(
            BackfillSummary(
                fund_code=stat.fund_code,
                fund_name=fund.fund_name if fund is not None else stat.fund_code,
                start_date=start_date,
                end_date=end_date,
                sample_count=stat.sample_count,
                raw_mean_abs_error=stat.raw_mean_abs_error,
                coverage_mean_abs_error=stat.coverage_adjusted_mean_abs_error,
                calibrated_mean_abs_error=stat.calibrated_mean_abs_error,
                best_mean_abs_error=stat.best_mean_abs_error,
                best_method_distribution=stat.best_method_distribution,
                confidence_level=confidence_level,
            )
        )

    return nav_report, quote_report, estimate_report, reconcile_report, calibration_count, selection_count, summaries
