from __future__ import annotations

import json
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..estimator import compute_coverage_adjusted_estimate
from ..models import (
    ActualReturn,
    CalibratedEstimate,
    Fund,
    FundAssetAllocation,
    FundEstimate,
    FundNav,
    HoldingVersion,
    SelectedEstimate,
)


CONFIDENCE_SORT_ORDER = {
    "A": 4,
    "B": 3,
    "C": 2,
    "D": 1,
    None: 0,
}


def get_fund_date_range(session: Session, fund_code: str) -> tuple[date | None, date | None]:
    range_row = session.execute(
        select(
            func.min(FundEstimate.trade_date),
            func.max(FundEstimate.trade_date),
        ).where(FundEstimate.fund_code == fund_code)
    ).one()
    start_date, end_date = range_row
    if start_date is not None and end_date is not None:
        return start_date, end_date

    nav_range = session.execute(
        select(
            func.min(FundNav.trade_date),
            func.max(FundNav.trade_date),
        ).where(FundNav.fund_code == fund_code)
    ).one()
    return nav_range


def get_fund_sidebar_context(session: Session, preferred_fund_code: str = "002207") -> dict[str, object]:
    funds = session.scalars(
        select(Fund).where(Fund.is_active.is_(True)).order_by(Fund.fund_code.asc())
    ).all()
    options = [(fund.fund_code, fund.fund_name) for fund in funds]
    selected_code = ""
    if any(code == preferred_fund_code for code, _ in options):
        selected_code = preferred_fund_code
    elif options:
        selected_code = options[0][0]

    start_date = None
    end_date = None
    if selected_code:
        start_date, end_date = get_fund_date_range(session, selected_code)

    return {
        "fund_options": options,
        "selected_fund_code": selected_code,
        "start_date": start_date,
        "end_date": end_date,
    }


def get_latest_dashboard_snapshot(
    session: Session,
    fund_code: str,
    selection_window: int = 20,
    selection_policy: str = "coverage_first",
) -> dict[str, object]:
    fund = session.get(Fund, fund_code)
    latest_selected = session.scalars(
        select(SelectedEstimate)
        .where(
            SelectedEstimate.fund_code == fund_code,
            SelectedEstimate.selection_window == selection_window,
            SelectedEstimate.selection_policy == selection_policy,
        )
        .order_by(SelectedEstimate.trade_date.desc())
    ).first()
    latest_nav_date = session.scalar(
        select(func.max(FundNav.trade_date)).where(FundNav.fund_code == fund_code)
    )
    latest_actual_date = session.scalar(
        select(func.max(ActualReturn.trade_date)).where(ActualReturn.fund_code == fund_code)
    )
    latest_hit_rate = None
    latest_mae = None
    if latest_selected is not None:
        latest_mae = {
            "raw": latest_selected.raw_mae,
            "coverage_adjusted": latest_selected.coverage_adjusted_mae,
            "calibrated": latest_selected.calibrated_mae,
        }.get(latest_selected.best_method)
        latest_hit_rate = {
            "raw": latest_selected.raw_direction_hit_rate,
            "coverage_adjusted": latest_selected.coverage_direction_hit_rate,
            "calibrated": latest_selected.calibrated_direction_hit_rate,
        }.get(latest_selected.best_method)

    return {
        "fund_name": None if fund is None else fund.fund_name,
        "fund_code": fund_code,
        "latest_estimate_date": None if latest_selected is None else latest_selected.trade_date,
        "latest_nav_date": latest_nav_date,
        "latest_actual_date": latest_actual_date,
        "raw_estimate": None if latest_selected is None else latest_selected.raw_estimate,
        "coverage_adjusted_estimate": None if latest_selected is None else latest_selected.coverage_adjusted_estimate,
        "calibrated_estimate": None if latest_selected is None else latest_selected.calibrated_estimate,
        "best_estimate": None if latest_selected is None else latest_selected.best_estimate,
        "best_method": None if latest_selected is None else latest_selected.best_method,
        "confidence_level": None if latest_selected is None else latest_selected.confidence_level,
        "latest_mae": latest_mae,
        "direction_hit_rate": latest_hit_rate,
    }


def load_fund_overview_rows(
    session: Session,
    selection_window: int = 20,
    selection_policy: str = "coverage_first",
    sort_by: str = "best_estimate",
    descending: bool = True,
) -> list[dict[str, object]]:
    funds = session.scalars(
        select(Fund).where(Fund.is_active.is_(True)).order_by(Fund.fund_code.asc())
    ).all()

    rows: list[dict[str, object]] = []
    for fund in funds:
        snapshot = get_latest_dashboard_snapshot(
            session=session,
            fund_code=fund.fund_code,
            selection_window=selection_window,
            selection_policy=selection_policy,
        )
        latest_estimate_date = snapshot["latest_estimate_date"]
        latest_actual_date = snapshot["latest_actual_date"]
        latest_nav_date = snapshot["latest_nav_date"]
        rows.append(
            {
                "fund_code": fund.fund_code,
                "fund_name": fund.fund_name,
                "best_estimate": snapshot["best_estimate"],
                "raw_estimate": snapshot["raw_estimate"],
                "coverage_adjusted_estimate": snapshot["coverage_adjusted_estimate"],
                "calibrated_estimate": snapshot["calibrated_estimate"],
                "best_method": snapshot["best_method"],
                "confidence_level": snapshot["confidence_level"],
                "latest_mae": snapshot["latest_mae"],
                "direction_hit_rate": snapshot["direction_hit_rate"],
                "latest_estimate_date": latest_estimate_date,
                "latest_actual_date": latest_actual_date,
                "latest_nav_date": latest_nav_date,
                "sort_confidence": CONFIDENCE_SORT_ORDER.get(snapshot["confidence_level"], 0),
            }
        )

    def sort_value(row: dict[str, object]):
        if sort_by == "fund_name":
            return (row["fund_name"] or "", row["fund_code"])
        if sort_by == "confidence":
            return row["sort_confidence"]
        if sort_by == "latest_estimate_date":
            return row["latest_estimate_date"] or date.min
        value = row.get(sort_by)
        return -999999 if value is None else value

    rows.sort(key=sort_value, reverse=descending)
    return rows


def load_estimate_comparison_rows(
    session: Session,
    fund_code: str,
    start_date: date,
    end_date: date,
    window: int = 20,
    selection_policy: str = "coverage_first",
) -> list[dict[str, object]]:
    stmt = (
        select(FundEstimate, ActualReturn, CalibratedEstimate, SelectedEstimate)
        .join(
            ActualReturn,
            (ActualReturn.trade_date == FundEstimate.trade_date)
            & (ActualReturn.fund_code == FundEstimate.fund_code),
            isouter=True,
        )
        .join(
            CalibratedEstimate,
            (CalibratedEstimate.trade_date == FundEstimate.trade_date)
            & (CalibratedEstimate.fund_code == FundEstimate.fund_code)
            & (CalibratedEstimate.holding_version_id == FundEstimate.holding_version_id)
            & (CalibratedEstimate.window == window)
            & (CalibratedEstimate.base_estimate_type == "coverage_adjusted"),
            isouter=True,
        )
        .join(
            SelectedEstimate,
            (SelectedEstimate.trade_date == FundEstimate.trade_date)
            & (SelectedEstimate.fund_code == FundEstimate.fund_code)
            & (SelectedEstimate.holding_version_id == FundEstimate.holding_version_id)
            & (SelectedEstimate.selection_window == window)
            & (SelectedEstimate.selection_policy == selection_policy),
            isouter=True,
        )
        .where(
            FundEstimate.fund_code == fund_code,
            FundEstimate.trade_date >= start_date,
            FundEstimate.trade_date <= end_date,
        )
        .order_by(FundEstimate.trade_date.desc())
    )

    rows: list[dict[str, object]] = []
    for estimate, actual, calibrated, selected_row in session.execute(stmt).all():
        coverage_adjusted_estimate = None
        if calibrated is not None and calibrated.coverage_adjusted_estimate is not None:
            coverage_adjusted_estimate = calibrated.coverage_adjusted_estimate
        else:
            coverage_adjusted_estimate, _ = compute_coverage_adjusted_estimate(
                session=session,
                fund_code=fund_code,
                trade_date=estimate.trade_date,
                raw_estimate=estimate.raw_estimate,
                covered_weight=estimate.covered_weight,
            )

        actual_return = None if actual is None else actual.actual_return
        calibrated_estimate = None if calibrated is None else calibrated.calibrated_estimate
        best_estimate = None if selected_row is None else selected_row.best_estimate
        best_method = None if selected_row is None else selected_row.best_method
        confidence_level = None if selected_row is None else selected_row.confidence_level

        rows.append(
            {
                "trade_date": estimate.trade_date.isoformat(),
                "actual_return": _format_optional_percent(actual_return),
                "actual_return_value": actual_return,
                "raw_estimate": _format_optional_percent(estimate.raw_estimate, signed=True),
                "raw_estimate_value": estimate.raw_estimate,
                "coverage_adjusted_estimate": _format_optional_percent(coverage_adjusted_estimate, signed=True),
                "coverage_adjusted_estimate_value": coverage_adjusted_estimate,
                "calibrated_estimate": _format_optional_percent(calibrated_estimate, signed=True),
                "calibrated_estimate_value": calibrated_estimate,
                "best_estimate": _format_optional_percent(best_estimate, signed=True),
                "best_estimate_value": best_estimate,
                "best_method": best_method or "N/A",
                "raw_error": _format_optional_percent(_error(actual_return, estimate.raw_estimate), signed=True),
                "raw_error_value": _error(actual_return, estimate.raw_estimate),
                "coverage_error": _format_optional_percent(_error(actual_return, coverage_adjusted_estimate), signed=True),
                "coverage_error_value": _error(actual_return, coverage_adjusted_estimate),
                "calibrated_error": _format_optional_percent(_error(actual_return, calibrated_estimate), signed=True),
                "calibrated_error_value": _error(actual_return, calibrated_estimate),
                "best_error": _format_optional_percent(_error(actual_return, best_estimate), signed=True),
                "best_error_value": _error(actual_return, best_estimate),
                "confidence_level": confidence_level or "N/A",
                "decision_reason": None if selected_row is None else selected_row.decision_reason,
                "warning_json": [] if selected_row is None else json.loads(selected_row.warning_json or "[]"),
            }
        )
    return rows


def get_active_holding_summary(session: Session, fund_code: str) -> dict[str, object]:
    version = session.scalars(
        select(HoldingVersion)
        .where(HoldingVersion.fund_code == fund_code, HoldingVersion.is_active.is_(True))
        .order_by(HoldingVersion.report_date.desc(), HoldingVersion.created_at.desc())
    ).first()
    if version is None:
        return {"report_date": None, "source": None, "total_weight_pct": None, "rows": []}
    return {
        "report_date": version.report_date,
        "source": version.source,
        "total_weight_pct": round(version.total_weight * 100, 4),
        "rows": [
            {
                "asset_code": item.asset_code,
                "asset_name": item.asset_name,
                "asset_type": item.asset_type,
                "weight_pct": round(item.weight * 100, 4),
            }
            for item in version.items
        ],
    }


def get_active_asset_allocation_summary(session: Session, fund_code: str) -> dict[str, object]:
    row = session.scalars(
        select(FundAssetAllocation)
        .where(FundAssetAllocation.fund_code == fund_code, FundAssetAllocation.is_active.is_(True))
        .order_by(FundAssetAllocation.report_date.desc(), FundAssetAllocation.created_at.desc())
    ).first()
    if row is None:
        return {"stock_weight_pct": None, "report_date": None, "source": None}
    return {
        "stock_weight_pct": round(row.stock_weight * 100, 4),
        "bond_weight_pct": round(row.bond_weight * 100, 4),
        "cash_weight_pct": round(row.cash_weight * 100, 4),
        "other_weight_pct": round(row.other_weight * 100, 4),
        "report_date": row.report_date,
        "source": row.source,
    }


def _error(actual_return: float | None, estimate: float | None) -> float | None:
    if actual_return is None or estimate is None:
        return None
    return actual_return - estimate


def _format_optional_percent(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "N/A"
    sign = "+" if signed else ""
    return f"{value * 100:{sign}.2f}%"
