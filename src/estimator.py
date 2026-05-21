from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from .models import ActualReturn, CalibratedEstimate, DailyQuote, EstimateError, Fund, FundAssetAllocation, FundEstimate, HoldingVersion


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


@dataclass
class CalibrationTrainingSample:
    trade_date: date
    base_estimate: float
    raw_estimate: float
    actual_return: float


@dataclass
class CalibrationResult:
    fund_code: str
    fund_name: str
    raw_estimate: float
    coverage_adjusted_estimate: float | None
    calibrated_estimate: float
    alpha: float
    beta: float
    sample_count: int
    window: int
    train_start_date: date | None
    train_end_date: date | None
    mean_abs_error: float | None
    direction_hit_rate: float | None
    estimate_actual_corr: float | None
    model_status: str
    warnings: list[str]
    holding_version_id: int
    confidence_score: float | None
    confidence_level: str | None


@dataclass
class CalibrationStatsResult:
    fund_code: str
    fund_name: str
    sample_count: int
    raw_mean_abs_error: float
    calibrated_mean_abs_error: float
    improvement_pct: float | None
    raw_direction_hit_rate: float
    calibrated_direction_hit_rate: float
    raw_corr: float | None
    calibrated_corr: float | None


def format_percent(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "N/A"
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


def format_hit_rate(value: float | None) -> str:
    if value is None:
        return "N/A"
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


def calculate_linear_regression(x_values: list[float], y_values: list[float]) -> tuple[float, float] | None:
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(y_values) / len(y_values)
    variance_x = sum((x - x_mean) ** 2 for x in x_values)
    if variance_x == 0:
        return None
    covariance = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values))
    beta = covariance / variance_x
    alpha = y_mean - beta * x_mean
    return alpha, beta


def determine_confidence(
    sample_count: int,
    mean_abs_error: float | None,
    direction_hit_rate: float | None,
    estimate_actual_corr: float | None,
    model_status: str,
) -> tuple[float | None, str | None]:
    if model_status != "ok" or sample_count < 5:
        return 0.25, "D"
    if (
        sample_count >= 20
        and mean_abs_error is not None
        and mean_abs_error <= 0.003
        and direction_hit_rate is not None
        and direction_hit_rate >= 0.75
        and estimate_actual_corr is not None
        and estimate_actual_corr >= 0.70
    ):
        return 0.9, "A"
    if (
        sample_count >= 10
        and mean_abs_error is not None
        and mean_abs_error <= 0.006
        and direction_hit_rate is not None
        and direction_hit_rate >= 0.65
    ):
        return 0.75, "B"
    if sample_count >= 5:
        return 0.6, "C"
    return 0.25, "D"


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


def select_asset_allocation(session: Session, fund_code: str, trade_date: date) -> FundAssetAllocation | None:
    stmt = (
        select(FundAssetAllocation)
        .where(
            FundAssetAllocation.fund_code == fund_code,
            FundAssetAllocation.report_date <= trade_date,
        )
        .order_by(FundAssetAllocation.report_date.desc(), FundAssetAllocation.created_at.desc())
    )
    return session.scalars(stmt).first()


def calculate_coverage_adjusted_estimate(
    raw_estimate: float,
    covered_weight: float,
    target_equity_weight: float | None,
) -> float | None:
    if covered_weight <= 0:
        return None
    target_weight = covered_weight if target_equity_weight is None else target_equity_weight
    return raw_estimate / covered_weight * target_weight


def compute_coverage_adjusted_estimate(
    session: Session,
    fund_code: str,
    trade_date: date,
    raw_estimate: float,
    covered_weight: float,
) -> tuple[float | None, list[str]]:
    warnings: list[str] = []
    if covered_weight <= 0:
        warnings.append(
            f"Warning: fund {fund_code} on {trade_date} has non-positive covered_weight, "
            "coverage_adjusted_estimate falls back to raw_estimate."
        )
        return None, warnings

    allocation = select_asset_allocation(session, fund_code, trade_date)
    if allocation is None:
        warnings.append(
            f"Warning: fund {fund_code} on {trade_date} is missing asset allocation, "
            "coverage_adjusted_estimate falls back to raw_estimate."
        )
        return None, warnings

    return calculate_coverage_adjusted_estimate(
        raw_estimate,
        covered_weight,
        allocation.stock_weight,
    ), warnings


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


def collect_training_samples(
    session: Session,
    fund_code: str,
    trade_date: date,
    window: int,
    requested_base: str,
) -> list[CalibrationTrainingSample]:
    stmt = (
        select(FundEstimate, ActualReturn)
        .join(
            ActualReturn,
            and_(
                ActualReturn.trade_date == FundEstimate.trade_date,
                ActualReturn.fund_code == FundEstimate.fund_code,
            ),
        )
        .where(
            FundEstimate.fund_code == fund_code,
            FundEstimate.trade_date < trade_date,
        )
        .order_by(FundEstimate.trade_date.desc())
    )

    samples: list[CalibrationTrainingSample] = []
    for estimate, actual in session.execute(stmt).all():
        coverage_adjusted_estimate, _ = compute_coverage_adjusted_estimate(
            session,
            fund_code,
            estimate.trade_date,
            estimate.raw_estimate,
            estimate.covered_weight,
        )
        if requested_base == "coverage_adjusted" and coverage_adjusted_estimate is not None:
            base_estimate = coverage_adjusted_estimate
        else:
            base_estimate = estimate.raw_estimate

        samples.append(
            CalibrationTrainingSample(
                trade_date=estimate.trade_date,
                base_estimate=base_estimate,
                raw_estimate=estimate.raw_estimate,
                actual_return=actual.actual_return,
            )
        )
        if len(samples) >= window:
            break

    return list(reversed(samples))


def upsert_calibrated_estimate(
    session: Session,
    trade_date: date,
    fund_code: str,
    holding_version_id: int,
    requested_base: str,
    window: int,
) -> CalibratedEstimate:
    calibrated = session.scalar(
        select(CalibratedEstimate).where(
            CalibratedEstimate.trade_date == trade_date,
            CalibratedEstimate.fund_code == fund_code,
            CalibratedEstimate.holding_version_id == holding_version_id,
            CalibratedEstimate.base_estimate_type == requested_base,
            CalibratedEstimate.window == window,
        )
    )
    if calibrated is None:
        calibrated = CalibratedEstimate(
            trade_date=trade_date,
            fund_code=fund_code,
            holding_version_id=holding_version_id,
            base_estimate_type=requested_base,
            window=window,
        )
        session.add(calibrated)
    return calibrated


def build_calibrated_estimates(
    session: Session,
    trade_date: date,
    window: int = 20,
    base: str = "raw",
    fund_code: str | None = None,
    min_samples: int = 5,
) -> list[CalibrationResult]:
    stmt = select(FundEstimate, Fund).join(Fund, Fund.fund_code == FundEstimate.fund_code).where(
        FundEstimate.trade_date == trade_date,
    )
    if fund_code:
        stmt = stmt.where(FundEstimate.fund_code == fund_code)
    stmt = stmt.order_by(FundEstimate.fund_code.asc())

    results: list[CalibrationResult] = []

    for estimate, fund in session.execute(stmt).all():
        warnings: list[str] = []
        coverage_adjusted_estimate, coverage_warnings = compute_coverage_adjusted_estimate(
            session,
            estimate.fund_code,
            trade_date,
            estimate.raw_estimate,
            estimate.covered_weight,
        )
        warnings.extend(coverage_warnings)

        current_base_estimate = estimate.raw_estimate
        if base == "coverage_adjusted":
            if coverage_adjusted_estimate is not None:
                current_base_estimate = coverage_adjusted_estimate
            else:
                warnings.append(
                    f"Warning: fund {estimate.fund_code} on {trade_date} falls back to raw_estimate "
                    "for calibration because coverage_adjusted_estimate is unavailable."
                )

        training_samples = collect_training_samples(
            session=session,
            fund_code=estimate.fund_code,
            trade_date=trade_date,
            window=window,
            requested_base=base,
        )
        sample_count = len(training_samples)
        alpha = 0.0
        beta = 1.0
        train_start_date: date | None = None
        train_end_date: date | None = None
        mean_abs_error: float | None = None
        direction_hit_rate: float | None = None
        estimate_actual_corr: float | None = None
        model_status = "insufficient_samples"
        calibrated_estimate = estimate.raw_estimate

        if sample_count >= min_samples:
            x_values = [sample.base_estimate for sample in training_samples]
            y_values = [sample.actual_return for sample in training_samples]
            regression = calculate_linear_regression(x_values, y_values)
            if regression is None:
                model_status = "insufficient_variance"
                warnings.append(
                    f"Warning: fund {estimate.fund_code} before {trade_date} has zero variance in "
                    "training estimates, calibration falls back to raw_estimate."
                )
            else:
                alpha, beta = regression
                train_start_date = training_samples[0].trade_date
                train_end_date = training_samples[-1].trade_date
                train_predictions = [alpha + beta * value for value in x_values]
                train_errors = [actual - predicted for actual, predicted in zip(y_values, train_predictions)]
                mean_abs_error = sum(abs(error) for error in train_errors) / sample_count
                direction_hit_rate = sum(
                    1 for predicted, actual in zip(train_predictions, y_values)
                    if is_direction_hit(predicted, actual)
                ) / sample_count
                estimate_actual_corr = calculate_correlation(x_values, y_values)
                calibrated_estimate = alpha + beta * current_base_estimate
                model_status = "ok"

        confidence_score, confidence_level = determine_confidence(
            sample_count=sample_count,
            mean_abs_error=mean_abs_error,
            direction_hit_rate=direction_hit_rate,
            estimate_actual_corr=estimate_actual_corr,
            model_status=model_status,
        )

        calibrated_row = upsert_calibrated_estimate(
            session=session,
            trade_date=trade_date,
            fund_code=estimate.fund_code,
            holding_version_id=estimate.holding_version_id,
            requested_base=base,
            window=window,
        )
        calibrated_row.raw_estimate = estimate.raw_estimate
        calibrated_row.coverage_adjusted_estimate = coverage_adjusted_estimate
        calibrated_row.calibrated_estimate = round(calibrated_estimate, 8)
        calibrated_row.alpha = round(alpha, 8)
        calibrated_row.beta = round(beta, 8)
        calibrated_row.sample_count = sample_count
        calibrated_row.train_start_date = train_start_date
        calibrated_row.train_end_date = train_end_date
        calibrated_row.mean_abs_error = None if mean_abs_error is None else round(mean_abs_error, 8)
        calibrated_row.direction_hit_rate = direction_hit_rate
        calibrated_row.estimate_actual_corr = estimate_actual_corr
        calibrated_row.model_status = model_status
        calibrated_row.warning_json = json.dumps(warnings, ensure_ascii=False)
        calibrated_row.confidence_score = confidence_score
        calibrated_row.confidence_level = confidence_level
        calibrated_row.created_at = datetime.now(UTC).replace(tzinfo=None)

        results.append(
            CalibrationResult(
                fund_code=estimate.fund_code,
                fund_name=fund.fund_name,
                raw_estimate=estimate.raw_estimate,
                coverage_adjusted_estimate=coverage_adjusted_estimate,
                calibrated_estimate=calibrated_row.calibrated_estimate,
                alpha=calibrated_row.alpha,
                beta=calibrated_row.beta,
                sample_count=sample_count,
                window=window,
                train_start_date=train_start_date,
                train_end_date=train_end_date,
                mean_abs_error=calibrated_row.mean_abs_error,
                direction_hit_rate=direction_hit_rate,
                estimate_actual_corr=estimate_actual_corr,
                model_status=model_status,
                warnings=warnings,
                holding_version_id=estimate.holding_version_id,
                confidence_score=confidence_score,
                confidence_level=confidence_level,
            )
        )

    session.commit()
    return results


def build_calibration_history(
    session: Session,
    start_date: date,
    end_date: date,
    window: int = 20,
    base: str = "raw",
    fund_code: str | None = None,
    min_samples: int = 5,
) -> int:
    stmt = select(FundEstimate.trade_date).where(
        FundEstimate.trade_date >= start_date,
        FundEstimate.trade_date <= end_date,
    )
    if fund_code:
        stmt = stmt.where(FundEstimate.fund_code == fund_code)
    trade_dates = sorted({item[0] for item in session.execute(stmt).all()})

    total_count = 0
    for current_trade_date in trade_dates:
        results = build_calibrated_estimates(
            session=session,
            trade_date=current_trade_date,
            window=window,
            base=base,
            fund_code=fund_code,
            min_samples=min_samples,
        )
        total_count += len(results)
    return total_count


def calculate_calibration_stats(
    session: Session,
    fund_code: str | None = None,
    window: int | None = None,
    base: str = "raw",
) -> list[CalibrationStatsResult]:
    stmt = (
        select(CalibratedEstimate, ActualReturn, Fund)
        .join(
            ActualReturn,
            and_(
                ActualReturn.trade_date == CalibratedEstimate.trade_date,
                ActualReturn.fund_code == CalibratedEstimate.fund_code,
            ),
        )
        .join(Fund, Fund.fund_code == CalibratedEstimate.fund_code)
        .where(CalibratedEstimate.base_estimate_type == base)
        .order_by(CalibratedEstimate.fund_code.asc(), CalibratedEstimate.trade_date.asc())
    )
    if fund_code:
        stmt = stmt.where(CalibratedEstimate.fund_code == fund_code)
    if window is not None:
        stmt = stmt.where(CalibratedEstimate.window == window)

    grouped_rows: dict[str, tuple[str, list[tuple[CalibratedEstimate, ActualReturn]]]] = {}
    for calibrated, actual, fund in session.execute(stmt).all():
        grouped_rows.setdefault(calibrated.fund_code, (fund.fund_name, []))[1].append((calibrated, actual))

    results: list[CalibrationStatsResult] = []
    for current_fund_code, (fund_name, rows) in grouped_rows.items():
        if not rows:
            continue
        sample_count = len(rows)
        raw_errors = [abs(actual.actual_return - calibrated.raw_estimate) for calibrated, actual in rows]
        calibrated_errors = [abs(actual.actual_return - calibrated.calibrated_estimate) for calibrated, actual in rows]
        raw_mae = sum(raw_errors) / sample_count
        calibrated_mae = sum(calibrated_errors) / sample_count
        improvement_pct = None
        if raw_mae > 0:
            improvement_pct = (raw_mae - calibrated_mae) / raw_mae

        raw_direction_hit_rate = sum(
            1 for calibrated, actual in rows
            if is_direction_hit(calibrated.raw_estimate, actual.actual_return)
        ) / sample_count
        calibrated_direction_hit_rate = sum(
            1 for calibrated, actual in rows
            if is_direction_hit(calibrated.calibrated_estimate, actual.actual_return)
        ) / sample_count
        raw_corr = calculate_correlation(
            [calibrated.raw_estimate for calibrated, _ in rows],
            [actual.actual_return for _, actual in rows],
        )
        calibrated_corr = calculate_correlation(
            [calibrated.calibrated_estimate for calibrated, _ in rows],
            [actual.actual_return for _, actual in rows],
        )

        results.append(
            CalibrationStatsResult(
                fund_code=current_fund_code,
                fund_name=fund_name,
                sample_count=sample_count,
                raw_mean_abs_error=raw_mae,
                calibrated_mean_abs_error=calibrated_mae,
                improvement_pct=improvement_pct,
                raw_direction_hit_rate=raw_direction_hit_rate,
                calibrated_direction_hit_rate=calibrated_direction_hit_rate,
                raw_corr=raw_corr,
                calibrated_corr=calibrated_corr,
            )
        )

    return results
