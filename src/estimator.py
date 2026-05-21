from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from .models import ActualReturn, CalibratedEstimate, DailyQuote, EstimateError, Fund, FundAssetAllocation, FundEstimate, HoldingVersion, SelectedEstimate


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
class HistoryBuildReport:
    total_count: int
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
    base_estimate_type: str
    base_mean_abs_error: float
    calibrated_mean_abs_error: float
    improvement_pct: float | None
    base_direction_hit_rate: float
    calibrated_direction_hit_rate: float
    base_corr: float | None
    calibrated_corr: float | None

    @property
    def raw_mean_abs_error(self) -> float:
        return self.base_mean_abs_error

    @property
    def raw_direction_hit_rate(self) -> float:
        return self.base_direction_hit_rate

    @property
    def raw_corr(self) -> float | None:
        return self.base_corr


@dataclass
class CompareEstimatesResult:
    fund_code: str
    fund_name: str
    start_date: date | None
    end_date: date | None
    sample_count: int
    raw_mean_abs_error: float | None
    coverage_adjusted_mean_abs_error: float | None
    calibrated_mean_abs_error: float | None
    best_method: str | None
    raw_direction_hit_rate: float | None
    coverage_direction_hit_rate: float | None
    calibrated_direction_hit_rate: float | None
    raw_corr: float | None
    coverage_corr: float | None
    calibrated_corr: float | None


@dataclass
class SelectedEstimateResult:
    fund_code: str
    fund_name: str
    raw_estimate: float
    coverage_adjusted_estimate: float | None
    calibrated_estimate: float | None
    best_estimate: float
    best_method: str
    sample_count: int
    raw_mae: float | None
    coverage_adjusted_mae: float | None
    calibrated_mae: float | None
    confidence_score: float | None
    confidence_level: str | None
    best_status: str
    decision_reason: str
    warning_json: list[str]


@dataclass
class SelectedStatsResult:
    fund_code: str
    fund_name: str
    start_date: date | None
    end_date: date | None
    sample_count: int
    raw_mean_abs_error: float | None
    coverage_adjusted_mean_abs_error: float | None
    calibrated_mean_abs_error: float | None
    best_mean_abs_error: float | None
    best_single_method: str | None
    best_method_distribution: str
    best_direction_hit_rate: float | None
    best_corr: float | None


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


def build_fund_estimates(
    session: Session,
    trade_date: date,
    fund_code: str | None = None,
) -> list[EstimateResult]:
    stmt = select(Fund).where(Fund.is_active.is_(True))
    if fund_code:
        stmt = stmt.where(Fund.fund_code == fund_code)
    funds = session.scalars(stmt.order_by(Fund.fund_code.asc())).all()
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


def build_estimate_history(
    session: Session,
    start_date: date,
    end_date: date,
    fund_code: str | None = None,
) -> HistoryBuildReport:
    stmt = select(DailyQuote.trade_date).where(
        DailyQuote.trade_date >= start_date,
        DailyQuote.trade_date <= end_date,
    )
    trade_dates = sorted({item[0] for item in session.execute(stmt).all()})
    total_count = 0
    warnings: list[str] = []
    if not trade_dates:
        warnings.append(
            f"Warning: no daily quotes found between {start_date} and {end_date}."
        )
        return HistoryBuildReport(total_count=0, warnings=warnings)

    for current_trade_date in trade_dates:
        results = build_fund_estimates(
            session=session,
            trade_date=current_trade_date,
            fund_code=fund_code,
        )
        if not results:
            warnings.append(
                f"Warning: no estimates built for {current_trade_date}. Missing active holdings or quotes."
            )
            continue
        total_count += len(results)

    return HistoryBuildReport(total_count=total_count, warnings=warnings)


def build_estimate_errors(
    session: Session,
    trade_date: date,
    fund_code: str | None = None,
) -> ReconcileReport:
    stmt = select(FundEstimate).where(FundEstimate.trade_date == trade_date)
    if fund_code:
        stmt = stmt.where(FundEstimate.fund_code == fund_code)
    estimates = session.scalars(stmt.order_by(FundEstimate.fund_code.asc())).all()
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


def build_reconcile_history(
    session: Session,
    start_date: date,
    end_date: date,
    fund_code: str | None = None,
) -> HistoryBuildReport:
    stmt = select(FundEstimate.trade_date).where(
        FundEstimate.trade_date >= start_date,
        FundEstimate.trade_date <= end_date,
    )
    if fund_code:
        stmt = stmt.where(FundEstimate.fund_code == fund_code)
    trade_dates = sorted({item[0] for item in session.execute(stmt).all()})

    total_count = 0
    warnings: list[str] = []
    if not trade_dates:
        warnings.append(
            f"Warning: no fund estimates found between {start_date} and {end_date}."
        )
        return HistoryBuildReport(total_count=0, warnings=warnings)
    for current_trade_date in trade_dates:
        report = build_estimate_errors(
            session=session,
            trade_date=current_trade_date,
            fund_code=fund_code,
        )
        total_count += len(report.results)
        warnings.extend(report.warnings)

    return HistoryBuildReport(total_count=total_count, warnings=warnings)


def calculate_error_stats(
    session: Session,
    fund_code: str | None = None,
    window: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[StatsResult]:
    stmt = select(EstimateError, Fund).join(Fund, Fund.fund_code == EstimateError.fund_code)
    if fund_code:
        stmt = stmt.where(EstimateError.fund_code == fund_code)
    if start_date is not None:
        stmt = stmt.where(EstimateError.trade_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(EstimateError.trade_date <= end_date)
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
    start_date: date | None = None,
    end_date: date | None = None,
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
    if start_date is not None:
        stmt = stmt.where(CalibratedEstimate.trade_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(CalibratedEstimate.trade_date <= end_date)

    grouped_rows: dict[str, tuple[str, list[tuple[CalibratedEstimate, ActualReturn]]]] = {}
    for calibrated, actual, fund in session.execute(stmt).all():
        grouped_rows.setdefault(calibrated.fund_code, (fund.fund_name, []))[1].append((calibrated, actual))

    results: list[CalibrationStatsResult] = []
    for current_fund_code, (fund_name, rows) in grouped_rows.items():
        if not rows:
            continue
        base_pairs: list[tuple[float, float]] = []
        calibrated_pairs: list[tuple[float, float]] = []
        for calibrated, actual in rows:
            base_estimate = calibrated.raw_estimate
            if base == "coverage_adjusted":
                base_estimate = calibrated.coverage_adjusted_estimate
            if base_estimate is None:
                continue
            base_pairs.append((base_estimate, actual.actual_return))
            calibrated_pairs.append((calibrated.calibrated_estimate, actual.actual_return))

        sample_count = len(base_pairs)
        if sample_count == 0:
            continue
        base_errors = [abs(actual_return - estimate) for estimate, actual_return in base_pairs]
        calibrated_errors = [abs(actual_return - estimate) for estimate, actual_return in calibrated_pairs]
        base_mae = sum(base_errors) / sample_count
        calibrated_mae = sum(calibrated_errors) / sample_count
        improvement_pct = None
        if base_mae > 0:
            improvement_pct = (base_mae - calibrated_mae) / base_mae

        base_direction_hit_rate = sum(
            1 for estimate, actual_return in base_pairs
            if is_direction_hit(estimate, actual_return)
        ) / sample_count
        calibrated_direction_hit_rate = sum(
            1 for estimate, actual_return in calibrated_pairs
            if is_direction_hit(estimate, actual_return)
        ) / sample_count
        base_corr = calculate_correlation(
            [estimate for estimate, _ in base_pairs],
            [actual_return for _, actual_return in base_pairs],
        )
        calibrated_corr = calculate_correlation(
            [estimate for estimate, _ in calibrated_pairs],
            [actual_return for _, actual_return in calibrated_pairs],
        )

        results.append(
            CalibrationStatsResult(
                fund_code=current_fund_code,
                fund_name=fund_name,
                sample_count=sample_count,
                base_estimate_type=base,
                base_mean_abs_error=base_mae,
                calibrated_mean_abs_error=calibrated_mae,
                improvement_pct=improvement_pct,
                base_direction_hit_rate=base_direction_hit_rate,
                calibrated_direction_hit_rate=calibrated_direction_hit_rate,
                base_corr=base_corr,
                calibrated_corr=calibrated_corr,
            )
        )

    return results


def calculate_compare_estimates(
    session: Session,
    fund_code: str | None = None,
    window: int = 20,
    base: str = "coverage_adjusted",
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[CompareEstimatesResult]:
    stmt = (
        select(FundEstimate, ActualReturn, Fund, CalibratedEstimate)
        .join(
            ActualReturn,
            and_(
                ActualReturn.trade_date == FundEstimate.trade_date,
                ActualReturn.fund_code == FundEstimate.fund_code,
            ),
        )
        .join(Fund, Fund.fund_code == FundEstimate.fund_code)
        .join(
            CalibratedEstimate,
            and_(
                CalibratedEstimate.trade_date == FundEstimate.trade_date,
                CalibratedEstimate.fund_code == FundEstimate.fund_code,
                CalibratedEstimate.holding_version_id == FundEstimate.holding_version_id,
                CalibratedEstimate.window == window,
                CalibratedEstimate.base_estimate_type == base,
            ),
            isouter=True,
        )
        .order_by(FundEstimate.fund_code.asc(), FundEstimate.trade_date.asc())
    )
    if fund_code:
        stmt = stmt.where(FundEstimate.fund_code == fund_code)
    if start_date is not None:
        stmt = stmt.where(FundEstimate.trade_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(FundEstimate.trade_date <= end_date)

    grouped_rows: dict[str, tuple[str, list[tuple[FundEstimate, ActualReturn, CalibratedEstimate | None]]]] = {}
    for estimate, actual, fund, calibrated in session.execute(stmt).all():
        grouped_rows.setdefault(estimate.fund_code, (fund.fund_name, []))[1].append((estimate, actual, calibrated))

    results: list[CompareEstimatesResult] = []
    for current_fund_code, (fund_name, rows) in grouped_rows.items():
        if not rows:
            continue

        raw_pairs: list[tuple[float, float]] = []
        coverage_pairs: list[tuple[float, float]] = []
        calibrated_pairs: list[tuple[float, float]] = []
        actual_start_date = rows[0][0].trade_date
        actual_end_date = rows[-1][0].trade_date

        for estimate, actual, calibrated in rows:
            raw_pairs.append((estimate.raw_estimate, actual.actual_return))

            coverage_estimate: float | None = None
            if calibrated is not None and calibrated.coverage_adjusted_estimate is not None:
                coverage_estimate = calibrated.coverage_adjusted_estimate
            else:
                coverage_estimate, _ = compute_coverage_adjusted_estimate(
                    session=session,
                    fund_code=estimate.fund_code,
                    trade_date=estimate.trade_date,
                    raw_estimate=estimate.raw_estimate,
                    covered_weight=estimate.covered_weight,
                )
            if coverage_estimate is not None:
                coverage_pairs.append((coverage_estimate, actual.actual_return))

            if calibrated is not None:
                calibrated_pairs.append((calibrated.calibrated_estimate, actual.actual_return))

        raw_mae = _calculate_mae(raw_pairs)
        coverage_mae = _calculate_mae(coverage_pairs)
        calibrated_mae = _calculate_mae(calibrated_pairs)
        best_method = _pick_best_method(
            {
                "raw": raw_mae,
                "coverage_adjusted": coverage_mae,
                "calibrated": calibrated_mae,
            }
        )

        results.append(
            CompareEstimatesResult(
                fund_code=current_fund_code,
                fund_name=fund_name,
                start_date=start_date or actual_start_date,
                end_date=end_date or actual_end_date,
                sample_count=len(raw_pairs),
                raw_mean_abs_error=raw_mae,
                coverage_adjusted_mean_abs_error=coverage_mae,
                calibrated_mean_abs_error=calibrated_mae,
                best_method=best_method,
                raw_direction_hit_rate=_calculate_hit_rate(raw_pairs),
                coverage_direction_hit_rate=_calculate_hit_rate(coverage_pairs),
                calibrated_direction_hit_rate=_calculate_hit_rate(calibrated_pairs),
                raw_corr=_calculate_corr_from_pairs(raw_pairs),
                coverage_corr=_calculate_corr_from_pairs(coverage_pairs),
                calibrated_corr=_calculate_corr_from_pairs(calibrated_pairs),
            )
        )

    return results


def _calculate_mae(pairs: list[tuple[float, float]]) -> float | None:
    if not pairs:
        return None
    return sum(abs(actual_return - estimate) for estimate, actual_return in pairs) / len(pairs)


def _calculate_hit_rate(pairs: list[tuple[float, float]]) -> float | None:
    if not pairs:
        return None
    return sum(1 for estimate, actual_return in pairs if is_direction_hit(estimate, actual_return)) / len(pairs)


def _calculate_corr_from_pairs(pairs: list[tuple[float, float]]) -> float | None:
    if not pairs:
        return None
    return calculate_correlation(
        [estimate for estimate, _ in pairs],
        [actual_return for _, actual_return in pairs],
    )


def _pick_best_method(mae_by_method: dict[str, float | None]) -> str | None:
    available = [(method, mae) for method, mae in mae_by_method.items() if mae is not None]
    if not available:
        return None
    return min(available, key=lambda item: item[1])[0]


def build_selected_estimates(
    session: Session,
    trade_date: date,
    fund_code: str | None = None,
    selection_window: int = 20,
    min_samples: int = 10,
    min_improvement_bps: int = 3,
) -> list[SelectedEstimateResult]:
    stmt = (
        select(FundEstimate, Fund)
        .join(Fund, Fund.fund_code == FundEstimate.fund_code)
        .where(FundEstimate.trade_date == trade_date)
        .order_by(FundEstimate.fund_code.asc())
    )
    if fund_code:
        stmt = stmt.where(FundEstimate.fund_code == fund_code)

    results: list[SelectedEstimateResult] = []
    improvement_threshold = min_improvement_bps / 10000.0

    for estimate, fund in session.execute(stmt).all():
        warnings: list[str] = []
        coverage_adjusted_estimate, coverage_warnings = compute_coverage_adjusted_estimate(
            session=session,
            fund_code=estimate.fund_code,
            trade_date=trade_date,
            raw_estimate=estimate.raw_estimate,
            covered_weight=estimate.covered_weight,
        )
        warnings.extend(coverage_warnings)

        calibrated_row = _select_calibrated_row(
            session=session,
            fund_code=estimate.fund_code,
            trade_date=trade_date,
            holding_version_id=estimate.holding_version_id,
            window=selection_window,
        )
        calibrated_estimate = None if calibrated_row is None else calibrated_row.calibrated_estimate
        if calibrated_row is None:
            warnings.append(
                f"Warning: fund {estimate.fund_code} on {trade_date} is missing calibrated_estimate for window {selection_window}."
            )

        history_rows = _collect_selection_history(
            session=session,
            fund_code=estimate.fund_code,
            trade_date=trade_date,
            selection_window=selection_window,
        )
        sample_count = len(history_rows)
        method_errors = _build_method_error_history(history_rows)
        raw_mae = _calculate_mae_from_errors(method_errors["raw"])
        coverage_mae = _calculate_mae_from_errors(method_errors["coverage_adjusted"])
        calibrated_mae = _calculate_mae_from_errors(method_errors["calibrated"])
        raw_hit_rate = _calculate_hit_rate_from_errors(method_errors["raw"])
        coverage_hit_rate = _calculate_hit_rate_from_errors(method_errors["coverage_adjusted"])
        calibrated_hit_rate = _calculate_hit_rate_from_errors(method_errors["calibrated"])

        best_method = "raw"
        best_estimate = estimate.raw_estimate
        best_status = "ok"
        decision_reason = "raw 为默认基线方法。"

        if sample_count < min_samples:
            if coverage_adjusted_estimate is not None:
                best_method = "coverage_adjusted"
                best_estimate = coverage_adjusted_estimate
                decision_reason = "历史样本不足, coverage_adjusted 可用, 使用 coverage_adjusted fallback。"
            else:
                decision_reason = "历史样本不足, coverage_adjusted 不可用, 使用 raw fallback。"
            best_status = "insufficient_samples_fallback"
        else:
            current_best_method = "raw"
            current_best_estimate = estimate.raw_estimate
            current_best_mae = raw_mae
            current_reason = "raw 为默认基线方法。"

            if (
                coverage_adjusted_estimate is not None
                and coverage_mae is not None
                and raw_mae is not None
                and len(method_errors["coverage_adjusted"]) >= min_samples
            ):
                if coverage_mae <= raw_mae - improvement_threshold:
                    if _recent_underperform_count(
                        candidate_errors=method_errors["coverage_adjusted"],
                        baseline_errors=method_errors["raw"],
                    ) >= 2:
                        warnings.append(
                            "Warning: coverage_adjusted recent underperform protection triggered, keep raw."
                        )
                    else:
                        current_best_method = "coverage_adjusted"
                        current_best_estimate = coverage_adjusted_estimate
                        current_best_mae = coverage_mae
                        current_reason = (
                            f"coverage_adjusted 历史 MAE 比 raw 低 {format_percent(raw_mae - coverage_mae)},"
                            f" 超过切换阈值 {min_improvement_bps} bps。"
                        )
                else:
                    current_reason = (
                        f"coverage_adjusted 相比 raw 的改进未超过切换阈值 {min_improvement_bps} bps, 保持 raw。"
                    )

            if (
                calibrated_estimate is not None
                and calibrated_mae is not None
                and current_best_mae is not None
                and len(method_errors["calibrated"]) >= min_samples
            ):
                if calibrated_mae <= current_best_mae - improvement_threshold:
                    baseline_method = current_best_method
                    if _recent_underperform_count(
                        candidate_errors=method_errors["calibrated"],
                        baseline_errors=method_errors[baseline_method],
                    ) >= 2:
                        warnings.append(
                            "Warning: calibrated recent underperform protection triggered, keep base method."
                        )
                        best_status = "protected_switch_blocked"
                    else:
                        current_best_method = "calibrated"
                        current_best_estimate = calibrated_estimate
                        current_best_mae = calibrated_mae
                        current_reason = (
                            f"calibrated 历史 MAE 比 {baseline_method} 低 "
                            f"{format_percent((method_errors_mae(method_errors[baseline_method]) or 0) - calibrated_mae)},"
                            f" 超过切换阈值 {min_improvement_bps} bps。"
                        )
                else:
                    baseline_label = current_best_method
                    current_reason = (
                        f"calibrated 仅小幅领先 {baseline_label}, 未超过切换阈值 {min_improvement_bps} bps,"
                        f" 选择更稳的 {baseline_label}。"
                    )

            best_method = current_best_method
            best_estimate = current_best_estimate
            decision_reason = current_reason

        best_mae = {
            "raw": raw_mae,
            "coverage_adjusted": coverage_mae,
            "calibrated": calibrated_mae,
        }.get(best_method)
        best_hit_rate = {
            "raw": raw_hit_rate,
            "coverage_adjusted": coverage_hit_rate,
            "calibrated": calibrated_hit_rate,
        }.get(best_method)
        best_corr = _calculate_corr_from_error_history(method_errors[best_method])
        confidence_score, confidence_level = determine_selected_confidence(
            sample_count=sample_count,
            min_samples=min_samples,
            mean_abs_error=best_mae,
            direction_hit_rate=best_hit_rate,
            corr=best_corr,
            best_status=best_status,
        )

        selected_row = upsert_selected_estimate(
            session=session,
            trade_date=trade_date,
            fund_code=estimate.fund_code,
            holding_version_id=estimate.holding_version_id,
            selection_window=selection_window,
        )
        selected_row.raw_estimate = estimate.raw_estimate
        selected_row.coverage_adjusted_estimate = coverage_adjusted_estimate
        selected_row.calibrated_estimate = calibrated_estimate
        selected_row.best_estimate = round(best_estimate, 8)
        selected_row.best_method = best_method
        selected_row.selection_window = selection_window
        selected_row.min_samples = min_samples
        selected_row.min_improvement_bps = min_improvement_bps
        selected_row.sample_count = sample_count
        selected_row.raw_mae = None if raw_mae is None else round(raw_mae, 8)
        selected_row.coverage_adjusted_mae = None if coverage_mae is None else round(coverage_mae, 8)
        selected_row.calibrated_mae = None if calibrated_mae is None else round(calibrated_mae, 8)
        selected_row.raw_direction_hit_rate = raw_hit_rate
        selected_row.coverage_direction_hit_rate = coverage_hit_rate
        selected_row.calibrated_direction_hit_rate = calibrated_hit_rate
        selected_row.decision_reason = decision_reason
        selected_row.confidence_score = confidence_score
        selected_row.confidence_level = confidence_level
        selected_row.best_status = best_status
        selected_row.warning_json = json.dumps(warnings, ensure_ascii=False)
        selected_row.created_at = datetime.now(UTC).replace(tzinfo=None)

        results.append(
            SelectedEstimateResult(
                fund_code=estimate.fund_code,
                fund_name=fund.fund_name,
                raw_estimate=estimate.raw_estimate,
                coverage_adjusted_estimate=coverage_adjusted_estimate,
                calibrated_estimate=calibrated_estimate,
                best_estimate=selected_row.best_estimate,
                best_method=best_method,
                sample_count=sample_count,
                raw_mae=raw_mae,
                coverage_adjusted_mae=coverage_mae,
                calibrated_mae=calibrated_mae,
                confidence_score=confidence_score,
                confidence_level=confidence_level,
                best_status=best_status,
                decision_reason=decision_reason,
                warning_json=warnings,
            )
        )

    session.commit()
    return results


def build_selection_history(
    session: Session,
    start_date: date,
    end_date: date,
    fund_code: str | None = None,
    selection_window: int = 20,
    min_samples: int = 10,
    min_improvement_bps: int = 3,
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
        results = build_selected_estimates(
            session=session,
            trade_date=current_trade_date,
            fund_code=fund_code,
            selection_window=selection_window,
            min_samples=min_samples,
            min_improvement_bps=min_improvement_bps,
        )
        total_count += len(results)
    return total_count


def calculate_selected_stats(
    session: Session,
    fund_code: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    selection_window: int = 20,
) -> list[SelectedStatsResult]:
    stmt = (
        select(SelectedEstimate, ActualReturn, Fund)
        .join(
            ActualReturn,
            and_(
                ActualReturn.trade_date == SelectedEstimate.trade_date,
                ActualReturn.fund_code == SelectedEstimate.fund_code,
            ),
        )
        .join(Fund, Fund.fund_code == SelectedEstimate.fund_code)
        .where(SelectedEstimate.selection_window == selection_window)
        .order_by(SelectedEstimate.fund_code.asc(), SelectedEstimate.trade_date.asc())
    )
    if fund_code:
        stmt = stmt.where(SelectedEstimate.fund_code == fund_code)
    if start_date is not None:
        stmt = stmt.where(SelectedEstimate.trade_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(SelectedEstimate.trade_date <= end_date)

    grouped_rows: dict[str, tuple[str, list[tuple[SelectedEstimate, ActualReturn]]]] = {}
    for selected_row, actual, fund in session.execute(stmt).all():
        grouped_rows.setdefault(selected_row.fund_code, (fund.fund_name, []))[1].append((selected_row, actual))

    results: list[SelectedStatsResult] = []
    for current_fund_code, (fund_name, rows) in grouped_rows.items():
        raw_pairs = [(row.raw_estimate, actual.actual_return) for row, actual in rows]
        coverage_pairs = [
            (row.coverage_adjusted_estimate, actual.actual_return)
            for row, actual in rows
            if row.coverage_adjusted_estimate is not None
        ]
        calibrated_pairs = [
            (row.calibrated_estimate, actual.actual_return)
            for row, actual in rows
            if row.calibrated_estimate is not None
        ]
        best_pairs = [(row.best_estimate, actual.actual_return) for row, actual in rows]

        raw_mae = _calculate_mae(raw_pairs)
        coverage_mae = _calculate_mae(coverage_pairs)
        calibrated_mae = _calculate_mae(calibrated_pairs)
        best_mae = _calculate_mae(best_pairs)
        best_single_method = _pick_best_method(
            {
                "raw": raw_mae,
                "coverage_adjusted": coverage_mae,
                "calibrated": calibrated_mae,
            }
        )
        distribution = _format_best_method_distribution([row.best_method for row, _ in rows])

        results.append(
            SelectedStatsResult(
                fund_code=current_fund_code,
                fund_name=fund_name,
                start_date=rows[0][0].trade_date,
                end_date=rows[-1][0].trade_date,
                sample_count=len(rows),
                raw_mean_abs_error=raw_mae,
                coverage_adjusted_mean_abs_error=coverage_mae,
                calibrated_mean_abs_error=calibrated_mae,
                best_mean_abs_error=best_mae,
                best_single_method=best_single_method,
                best_method_distribution=distribution,
                best_direction_hit_rate=_calculate_hit_rate(best_pairs),
                best_corr=_calculate_corr_from_pairs(best_pairs),
            )
        )

    return results


def upsert_selected_estimate(
    session: Session,
    trade_date: date,
    fund_code: str,
    holding_version_id: int,
    selection_window: int,
) -> SelectedEstimate:
    selected_row = session.scalar(
        select(SelectedEstimate).where(
            SelectedEstimate.trade_date == trade_date,
            SelectedEstimate.fund_code == fund_code,
            SelectedEstimate.holding_version_id == holding_version_id,
            SelectedEstimate.selection_window == selection_window,
        )
    )
    if selected_row is None:
        selected_row = SelectedEstimate(
            trade_date=trade_date,
            fund_code=fund_code,
            holding_version_id=holding_version_id,
            selection_window=selection_window,
        )
        session.add(selected_row)
    return selected_row


def determine_selected_confidence(
    sample_count: int,
    min_samples: int,
    mean_abs_error: float | None,
    direction_hit_rate: float | None,
    corr: float | None,
    best_status: str,
) -> tuple[float | None, str | None]:
    if best_status != "ok":
        if sample_count >= 5:
            return 0.55, "C"
        return 0.25, "D"
    if (
        sample_count >= 20
        and mean_abs_error is not None
        and mean_abs_error <= 0.003
        and direction_hit_rate is not None
        and direction_hit_rate >= 0.75
        and corr is not None
        and corr >= 0.70
    ):
        return 0.9, "A"
    if (
        sample_count >= max(10, min_samples)
        and mean_abs_error is not None
        and mean_abs_error <= 0.006
        and direction_hit_rate is not None
        and direction_hit_rate >= 0.65
    ):
        return 0.75, "B"
    if sample_count >= 5:
        return 0.6, "C"
    return 0.25, "D"


def _select_calibrated_row(
    session: Session,
    fund_code: str,
    trade_date: date,
    holding_version_id: int,
    window: int,
    base_type: str = "coverage_adjusted",
) -> CalibratedEstimate | None:
    return session.scalar(
        select(CalibratedEstimate).where(
            CalibratedEstimate.trade_date == trade_date,
            CalibratedEstimate.fund_code == fund_code,
            CalibratedEstimate.holding_version_id == holding_version_id,
            CalibratedEstimate.window == window,
            CalibratedEstimate.base_estimate_type == base_type,
        )
    )


def _collect_selection_history(
    session: Session,
    fund_code: str,
    trade_date: date,
    selection_window: int,
) -> list[dict[str, float | date | None]]:
    stmt = (
        select(FundEstimate, ActualReturn, CalibratedEstimate)
        .join(
            ActualReturn,
            and_(
                ActualReturn.trade_date == FundEstimate.trade_date,
                ActualReturn.fund_code == FundEstimate.fund_code,
            ),
        )
        .join(
            CalibratedEstimate,
            and_(
                CalibratedEstimate.trade_date == FundEstimate.trade_date,
                CalibratedEstimate.fund_code == FundEstimate.fund_code,
                CalibratedEstimate.holding_version_id == FundEstimate.holding_version_id,
                CalibratedEstimate.window == selection_window,
                CalibratedEstimate.base_estimate_type == "coverage_adjusted",
            ),
            isouter=True,
        )
        .where(
            FundEstimate.fund_code == fund_code,
            FundEstimate.trade_date < trade_date,
        )
        .order_by(FundEstimate.trade_date.desc())
    )
    rows = session.execute(stmt).all()[:selection_window]
    history: list[dict[str, float | date | None]] = []
    for estimate, actual, calibrated in rows:
        coverage_estimate = None
        if calibrated is not None and calibrated.coverage_adjusted_estimate is not None:
            coverage_estimate = calibrated.coverage_adjusted_estimate
        else:
            coverage_estimate, _ = compute_coverage_adjusted_estimate(
                session=session,
                fund_code=estimate.fund_code,
                trade_date=estimate.trade_date,
                raw_estimate=estimate.raw_estimate,
                covered_weight=estimate.covered_weight,
            )
        history.append(
            {
                "trade_date": estimate.trade_date,
                "actual_return": actual.actual_return,
                "raw_estimate": estimate.raw_estimate,
                "coverage_adjusted_estimate": coverage_estimate,
                "calibrated_estimate": None if calibrated is None else calibrated.calibrated_estimate,
            }
        )
    history.reverse()
    return history


def _build_method_error_history(
    history_rows: list[dict[str, float | date | None]],
) -> dict[str, list[dict[str, float | date]]]:
    history: dict[str, list[dict[str, float | date]]] = {
        "raw": [],
        "coverage_adjusted": [],
        "calibrated": [],
    }
    for row in history_rows:
        actual_return = row["actual_return"]
        for method_name, field_name in (
            ("raw", "raw_estimate"),
            ("coverage_adjusted", "coverage_adjusted_estimate"),
            ("calibrated", "calibrated_estimate"),
        ):
            estimate = row[field_name]
            if estimate is None:
                continue
            history[method_name].append(
                {
                    "trade_date": row["trade_date"],
                    "estimate": float(estimate),
                    "actual_return": float(actual_return),
                    "abs_error": abs(float(actual_return) - float(estimate)),
                }
            )
    return history


def _calculate_mae_from_errors(error_history: list[dict[str, float | date]]) -> float | None:
    if not error_history:
        return None
    return sum(float(item["abs_error"]) for item in error_history) / len(error_history)


def _calculate_hit_rate_from_errors(error_history: list[dict[str, float | date]]) -> float | None:
    if not error_history:
        return None
    return sum(
        1
        for item in error_history
        if is_direction_hit(float(item["estimate"]), float(item["actual_return"]))
    ) / len(error_history)


def _calculate_corr_from_error_history(error_history: list[dict[str, float | date]]) -> float | None:
    if not error_history:
        return None
    return calculate_correlation(
        [float(item["estimate"]) for item in error_history],
        [float(item["actual_return"]) for item in error_history],
    )


def _recent_underperform_count(
    candidate_errors: list[dict[str, float | date]],
    baseline_errors: list[dict[str, float | date]],
    threshold: float = 0.001,
) -> int:
    baseline_by_date = {item["trade_date"]: float(item["abs_error"]) for item in baseline_errors}
    shared = [
        item for item in candidate_errors
        if item["trade_date"] in baseline_by_date
    ]
    shared = shared[-3:]
    return sum(
        1
        for item in shared
        if float(item["abs_error"]) > baseline_by_date[item["trade_date"]] + threshold
    )


def method_errors_mae(error_history: list[dict[str, float | date]]) -> float | None:
    return _calculate_mae_from_errors(error_history)


def _format_best_method_distribution(methods: list[str]) -> str:
    if not methods:
        return "N/A"
    total = len(methods)
    parts: list[str] = []
    for method in ("coverage_adjusted", "calibrated", "raw"):
        count = sum(1 for item in methods if item == method)
        if count == 0:
            continue
        parts.append(f"{method}: {count / total * 100:.0f}%")
    return ", ".join(parts) if parts else "N/A"
