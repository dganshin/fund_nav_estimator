from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ActualReturn, DailyQuote, EstimateError, Fund, FundEstimate, HoldingVersion


@dataclass
class EstimateResult:
    fund_code: str
    fund_name: str
    raw_estimate: float
    covered_weight: float
    missing_weight: float
    holding_version_id: int
    missing_assets: list[dict[str, str]]
    warning: str


@dataclass
class ReconcileResult:
    fund_code: str
    fund_name: str
    raw_estimate: float
    actual_return: float
    error: float
    abs_error: float
    direction_hit: bool


@dataclass
class ReconcileReport:
    results: list[ReconcileResult]
    warnings: list[str]


@dataclass
class StatsResult:
    fund_code: str
    fund_name: str
    sample_count: int
    mean_error: float
    mean_abs_error: float
    max_abs_error: float
    direction_hit_rate: float
    estimate_actual_corr: float | None
    latest_error: float
    latest_trade_date: date


def format_percent(value: float, signed: bool = False) -> str:
    sign = "+" if signed else ""
    return f"{value * 100:{sign}.2f}%"


def format_missing_assets(missing_assets: list[dict[str, str]]) -> str:
    if not missing_assets:
        return "无"
    return ", ".join(
        f"{asset['asset_code']} {asset['asset_name']}".strip()
        for asset in missing_assets
    )


def format_ratio(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def format_hit_rate(value: float) -> str:
    return f"{value * 100:.2f}%"


def is_direction_hit(raw_estimate: float, actual_return: float) -> bool:
    return (
        (raw_estimate == 0 and actual_return == 0)
        or (raw_estimate > 0 and actual_return > 0)
        or (raw_estimate < 0 and actual_return < 0)
    )


def calculate_correlation(x_values: list[float], y_values: list[float]) -> float | None:
    if len(x_values) < 2 or len(y_values) < 2:
        return None

    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
    x_variance = sum((x - x_mean) ** 2 for x in x_values)
    y_variance = sum((y - y_mean) ** 2 for y in y_values)

    if x_variance == 0 or y_variance == 0:
        return None

    return numerator / math.sqrt(x_variance * y_variance)


def select_holding_version(session: Session, fund_code: str, trade_date: date) -> HoldingVersion | None:
    stmt = (
        select(HoldingVersion)
        .where(
            HoldingVersion.fund_code == fund_code,
            HoldingVersion.report_date <= trade_date,
        )
        .order_by(HoldingVersion.report_date.desc(), HoldingVersion.created_at.desc())
    )
    return session.scalars(stmt).first()


def build_fund_estimates(session: Session, trade_date: date) -> list[EstimateResult]:
    funds = session.scalars(select(Fund).where(Fund.is_active.is_(True))).all()
    results: list[EstimateResult] = []

    for fund in funds:
        version = select_holding_version(session, fund.fund_code, trade_date)
        if version is None:
            continue

        raw_estimate = 0.0
        covered_weight = 0.0
        missing_assets: list[dict[str, str]] = []

        for item in version.items:
            quote = session.get(
                DailyQuote,
                {"trade_date": trade_date, "asset_code": item.asset_code},
            )
            if quote is None:
                missing_assets.append(
                    {
                        "asset_code": item.asset_code,
                        "asset_name": item.asset_name,
                        "asset_type": item.asset_type,
                    }
                )
                continue

            covered_weight += item.weight
            raw_estimate += item.weight * quote.return_pct

        missing_weight = max(version.total_weight - covered_weight, 0.0)
        estimate = session.get(FundEstimate, {"trade_date": trade_date, "fund_code": fund.fund_code})
        if estimate is None:
            estimate = FundEstimate(
                trade_date=trade_date,
                fund_code=fund.fund_code,
                holding_version_id=version.id,
            )
            session.add(estimate)

        estimate.holding_version_id = version.id
        estimate.raw_estimate = round(raw_estimate, 8)
        estimate.covered_weight = round(covered_weight, 8)
        estimate.missing_weight = round(missing_weight, 8)
        estimate.missing_assets_json = json.dumps(missing_assets, ensure_ascii=False)
        estimate.created_at = datetime.now(UTC).replace(tzinfo=None)

        warning = ""
        if missing_assets:
            warning = f"缺少{len(missing_assets)}个资产行情"

        results.append(
            EstimateResult(
                fund_code=fund.fund_code,
                fund_name=fund.fund_name,
                raw_estimate=estimate.raw_estimate,
                covered_weight=estimate.covered_weight,
                missing_weight=estimate.missing_weight,
                holding_version_id=version.id,
                missing_assets=missing_assets,
                warning=warning,
            )
        )

    session.commit()
    return results


def build_estimate_errors(session: Session, trade_date: date) -> ReconcileReport:
    estimates = session.scalars(
        select(FundEstimate).where(FundEstimate.trade_date == trade_date)
    ).all()
    results: list[ReconcileResult] = []
    warnings: list[str] = []

    for estimate in estimates:
        fund = session.get(Fund, estimate.fund_code)
        actual = session.get(
            ActualReturn,
            {"trade_date": trade_date, "fund_code": estimate.fund_code},
        )
        if actual is None:
            warnings.append(
                f"Warning: fund {estimate.fund_code} on {trade_date} is missing actual_return."
            )
            continue

        error_value = round(actual.actual_return - estimate.raw_estimate, 8)
        direction_hit = is_direction_hit(estimate.raw_estimate, actual.actual_return)
        error = session.get(
            EstimateError,
            {"trade_date": trade_date, "fund_code": estimate.fund_code},
        )
        if error is None:
            error = EstimateError(trade_date=trade_date, fund_code=estimate.fund_code)
            session.add(error)

        error.raw_estimate = estimate.raw_estimate
        error.actual_return = actual.actual_return
        error.error = error_value
        error.abs_error = abs(error_value)
        error.direction_hit = direction_hit

        results.append(
            ReconcileResult(
                fund_code=estimate.fund_code,
                fund_name=fund.fund_name if fund is not None else estimate.fund_code,
                raw_estimate=estimate.raw_estimate,
                actual_return=actual.actual_return,
                error=error.error,
                abs_error=error.abs_error,
                direction_hit=direction_hit,
            )
        )

    session.commit()
    return ReconcileReport(results=results, warnings=warnings)


def calculate_error_stats(
    session: Session,
    fund_code: str | None = None,
    window: int | None = None,
) -> list[StatsResult]:
    stmt = select(EstimateError, Fund).join(Fund, Fund.fund_code == EstimateError.fund_code)
    if fund_code:
        stmt = stmt.where(EstimateError.fund_code == fund_code)
    stmt = stmt.order_by(EstimateError.fund_code.asc(), EstimateError.trade_date.asc())

    grouped_rows: dict[str, tuple[str, list[EstimateError]]] = {}
    for error, fund in session.execute(stmt).all():
        grouped_rows.setdefault(error.fund_code, (fund.fund_name, []))[1].append(error)

    results: list[StatsResult] = []
    for current_fund_code, (fund_name, errors) in grouped_rows.items():
        series = errors[-window:] if window is not None else errors
        if not series:
            continue

        sample_count = len(series)
        mean_error = sum(item.error for item in series) / sample_count
        mean_abs_error = sum(item.abs_error for item in series) / sample_count
        max_abs_error = max(item.abs_error for item in series)
        direction_hit_rate = sum(1 for item in series if item.direction_hit) / sample_count
        latest = series[-1]
        corr = calculate_correlation(
            [item.raw_estimate for item in series],
            [item.actual_return for item in series],
        )

        results.append(
            StatsResult(
                fund_code=current_fund_code,
                fund_name=fund_name,
                sample_count=sample_count,
                mean_error=mean_error,
                mean_abs_error=mean_abs_error,
                max_abs_error=max_abs_error,
                direction_hit_rate=direction_hit_rate,
                estimate_actual_corr=corr,
                latest_error=latest.error,
                latest_trade_date=latest.trade_date,
            )
        )

    return results
