from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from .models import (
    ActualReturn,
    CalibratedEstimate,
    DailyQuote,
    EffectiveWeightItem,
    EffectiveWeightVersion,
    EstimateError,
    Fund,
    FundAssetAllocation,
    FundEstimate,
    HoldingVersion,
    OnlineCalibrationState,
    SelectedEstimate,
    UserFundPosition,
)


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
class LiveHoldingContribution:
    asset_code: str
    asset_name: str
    asset_type: str
    published_weight_pct: float
    effective_weight_pct: float
    adjustment_factor: float
    return_pct: float | None
    contribution_pct: float | None
    contribution_explain: str


@dataclass
class LiveFundEstimateResult:
    fund_code: str
    fund_name: str
    trade_date: date
    quote_time: datetime | None
    current_estimate: float
    effective_method: str
    confidence_level: str | None
    holding_amount: float | None
    estimated_today_profit: float | None
    latest_real_nav_date: date | None
    current_scale_factor: float
    data_warning: str | None
    raw_estimate: float
    effective_weight_estimate: float | None
    coverage_adjusted_estimate: float | None
    calibrated_estimate: float | None
    single_scale_estimate: float | None
    two_factor_estimate: float | None
    final_estimate: float
    final_method: str
    covered_weight: float
    missing_weight: float
    latest_mae: float | None
    direction_hit_rate: float | None
    holding_version_id: int | None
    best_status: str
    decision_reason: str
    warnings: list[str]
    holdings: list[LiveHoldingContribution]
    error_band_pct: float | None = None
    error_band_label: str = "样本不足"
    confidence_text: str = "样本不足"


@dataclass
class EffectiveWeightResult:
    fund_code: str
    fund_name: str
    holding_version_id: int
    report_date: date
    covered_weight: float
    stock_weight: float | None
    scale_factor: float
    total_effective_weight: float
    warnings: list[str]


@dataclass
class OnlineCalibrationStateResult:
    fund_code: str
    holding_version_id: int
    base_scale_factor: float
    current_scale_factor: float
    min_scale_factor: float
    max_scale_factor: float
    ewma_error: float | None
    recent_mae: float | None
    sample_count: int
    last_update_trade_date: date | None
    confidence_level: str | None
    warning_json: list[str]


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
    selection_policy: str
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
    selection_policy: str
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


@dataclass
class SelectionInspectResult:
    trade_date: date
    fund_code: str
    raw_estimate: float
    coverage_adjusted_estimate: float | None
    calibrated_estimate: float | None
    best_method: str
    decision_reason: str
    sample_count: int
    raw_mae: float | None
    coverage_adjusted_mae: float | None
    calibrated_mae: float | None
    best_status: str
    warning_json: list[str]


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


def calculate_weight_scale_factor(
    covered_weight: float,
    stock_weight: float | None,
) -> float:
    if stock_weight is None or covered_weight <= 0:
        return 1.0
    return stock_weight / covered_weight


def build_effective_weight_version(
    session: Session,
    fund_code: str,
    trade_date: date,
    source: str = "覆盖率缩放",
) -> EffectiveWeightVersion | None:
    fund = session.get(Fund, fund_code)
    holding_version = select_holding_version(session, fund_code, trade_date)
    if fund is None or holding_version is None:
        return None

    allocation = select_asset_allocation(session, fund_code, trade_date)
    covered_weight = sum(item.weight for item in holding_version.items)
    stock_weight = None if allocation is None else allocation.stock_weight
    scale_factor = calculate_weight_scale_factor(covered_weight, stock_weight)
    report_date = holding_version.report_date
    if allocation is not None and allocation.report_date > report_date:
        report_date = allocation.report_date

    version = session.scalar(
        select(EffectiveWeightVersion).where(
            EffectiveWeightVersion.fund_code == fund_code,
            EffectiveWeightVersion.holding_version_id == holding_version.id,
            EffectiveWeightVersion.source == source,
        )
    )
    if version is None:
        version = EffectiveWeightVersion(
            fund_code=fund_code,
            holding_version_id=holding_version.id,
            asset_allocation_id=None if allocation is None else allocation.id,
            report_date=report_date,
            source=source,
            covered_weight=covered_weight,
            stock_weight=stock_weight,
            scale_factor=scale_factor,
            total_effective_weight=0.0,
            is_active=True,
        )
        session.add(version)
        session.flush()

    session.query(EffectiveWeightVersion).where(
        EffectiveWeightVersion.fund_code == fund_code,
        EffectiveWeightVersion.id != version.id,
    ).update({"is_active": False}, synchronize_session=False)

    version.asset_allocation_id = None if allocation is None else allocation.id
    version.report_date = report_date
    version.covered_weight = covered_weight
    version.stock_weight = stock_weight
    version.scale_factor = scale_factor
    version.is_active = True
    session.query(EffectiveWeightItem).where(
        EffectiveWeightItem.effective_weight_version_id == version.id,
    ).delete(synchronize_session=False)
    session.flush()

    total_effective_weight = 0.0
    contribution_explain = "按股票仓位和前十覆盖率统一缩放"
    if allocation is None or covered_weight <= 0:
        contribution_explain = "缺少股票仓位或覆盖权重, 使用公开权重"
    for item in sorted(holding_version.items, key=lambda row: row.weight, reverse=True):
        effective_weight = item.weight * scale_factor
        total_effective_weight += effective_weight
        version.items.append(
            EffectiveWeightItem(
                asset_code=item.asset_code,
                asset_name=item.asset_name,
                asset_type=item.asset_type,
                published_weight=item.weight,
                effective_weight=effective_weight,
                adjustment_factor=scale_factor,
                contribution_explain=contribution_explain,
            )
        )

    version.total_effective_weight = total_effective_weight
    session.flush()
    return version


def build_effective_weight_versions(
    session: Session,
    trade_date: date,
    fund_code: str | None = None,
) -> list[EffectiveWeightResult]:
    stmt = select(Fund).where(Fund.is_active.is_(True))
    if fund_code is not None:
        stmt = stmt.where(Fund.fund_code == fund_code)
    funds = session.scalars(stmt.order_by(Fund.fund_code.asc())).all()

    results: list[EffectiveWeightResult] = []
    for fund in funds:
        version = build_effective_weight_version(session, fund.fund_code, trade_date)
        if version is None:
            continue
        warnings: list[str] = []
        if version.stock_weight is None:
            warnings.append("缺少股票仓位, 修正权重回退为公开权重")
        elif version.covered_weight <= 0:
            warnings.append("覆盖权重小于等于0, 修正权重回退为公开权重")
        results.append(
            EffectiveWeightResult(
                fund_code=fund.fund_code,
                fund_name=fund.fund_name,
                holding_version_id=version.holding_version_id,
                report_date=version.report_date,
                covered_weight=version.covered_weight,
                stock_weight=version.stock_weight,
                scale_factor=version.scale_factor,
                total_effective_weight=version.total_effective_weight,
                warnings=warnings,
            )
        )
    session.commit()
    return results


def determine_realtime_confidence(
    sample_count: int,
    recent_mae: float | None,
    warning_count: int = 0,
) -> str:
    if sample_count >= 20 and recent_mae is not None and recent_mae <= 0.003 and warning_count == 0:
        return "A"
    if sample_count >= 10 and recent_mae is not None and recent_mae <= 0.006:
        return "B"
    if sample_count >= 5:
        return "C"
    return "D"


def upsert_online_calibration_state(
    session: Session,
    fund_code: str,
    holding_version_id: int,
    trade_date: date,
) -> OnlineCalibrationState:
    state = session.scalar(
        select(OnlineCalibrationState).where(
            OnlineCalibrationState.fund_code == fund_code,
            OnlineCalibrationState.holding_version_id == holding_version_id,
        )
    )
    holding_version = session.get(HoldingVersion, holding_version_id)
    if holding_version is None:
        raise ValueError(f"Missing holding version {holding_version_id} for {fund_code}.")
    allocation = select_asset_allocation(session, fund_code, trade_date)
    covered_weight = sum(item.weight for item in holding_version.items)
    stock_weight = None if allocation is None else allocation.stock_weight
    base_scale_factor = calculate_weight_scale_factor(covered_weight, stock_weight)
    min_scale_factor = base_scale_factor * 0.80
    max_scale_factor = base_scale_factor * 1.20
    if state is None:
        state = OnlineCalibrationState(
            fund_code=fund_code,
            holding_version_id=holding_version_id,
        )
        session.add(state)
        try:
            session.flush()
        except Exception:
            session.rollback()
            state = session.scalar(
                select(OnlineCalibrationState).where(
                    OnlineCalibrationState.fund_code == fund_code,
                    OnlineCalibrationState.holding_version_id == holding_version_id,
                )
            )
            if state is None:
                raise
    state.base_scale_factor = base_scale_factor
    state.current_scale_factor = base_scale_factor
    state.min_scale_factor = min_scale_factor
    state.max_scale_factor = max_scale_factor
    state.ewma_error = None
    state.recent_mae = None
    state.sample_count = 0
    state.last_update_trade_date = None
    state.confidence_level = "D"
    state.warning_json = "[]"
    session.flush()
    return state


def refresh_online_calibration_states(
    session: Session,
    fund_code: str | None = None,
    learning_rate: float = 0.10,
    min_abs_raw_estimate: float = 0.001,
    max_single_day_error: float = 0.02,
) -> list[OnlineCalibrationStateResult]:
    fund_stmt = select(Fund).where(Fund.is_active.is_(True))
    if fund_code is not None:
        fund_stmt = fund_stmt.where(Fund.fund_code == fund_code)
    funds = session.scalars(fund_stmt.order_by(Fund.fund_code.asc())).all()

    state_map: dict[tuple[str, int], OnlineCalibrationState] = {}
    for fund in funds:
        versions = session.scalars(
            select(HoldingVersion)
            .where(HoldingVersion.fund_code == fund.fund_code)
            .order_by(HoldingVersion.report_date.asc(), HoldingVersion.created_at.asc())
        ).all()
        for version in versions:
            state = upsert_online_calibration_state(
                session=session,
                fund_code=fund.fund_code,
                holding_version_id=version.id,
                trade_date=max(date.today(), version.report_date),
            )
            state_map[(fund.fund_code, version.id)] = state

    stmt = (
        select(FundEstimate, ActualReturn)
        .join(
            ActualReturn,
            and_(
                ActualReturn.trade_date == FundEstimate.trade_date,
                ActualReturn.fund_code == FundEstimate.fund_code,
            ),
        )
        .order_by(FundEstimate.fund_code.asc(), FundEstimate.trade_date.asc())
    )
    if fund_code is not None:
        stmt = stmt.where(FundEstimate.fund_code == fund_code)

    for estimate, actual in session.execute(stmt).all():
        state = state_map.get((estimate.fund_code, estimate.holding_version_id))
        if state is None:
            state = upsert_online_calibration_state(
                session=session,
                fund_code=estimate.fund_code,
                holding_version_id=estimate.holding_version_id,
                trade_date=estimate.trade_date,
            )
            state_map[(estimate.fund_code, estimate.holding_version_id)] = state

        current_estimated = estimate.raw_estimate * state.current_scale_factor
        error_value = abs(actual.actual_return - current_estimated)
        if state.ewma_error is None:
            state.ewma_error = error_value
        else:
            state.ewma_error = (1.0 - learning_rate) * state.ewma_error + learning_rate * error_value
        state.recent_mae = state.ewma_error

        warnings: list[str] = []
        should_update = True
        if abs(estimate.raw_estimate) < min_abs_raw_estimate:
            should_update = False
            warnings.append("原始估值过小, 跳过缩放更新")
        if error_value > max_single_day_error:
            should_update = False
            warnings.append("单日误差过大, 跳过缩放更新")

        if should_update:
            observed_scale = actual.actual_return / estimate.raw_estimate
            observed_scale = min(max(observed_scale, state.min_scale_factor), state.max_scale_factor)
            state.current_scale_factor = (
                (1.0 - learning_rate) * state.current_scale_factor
                + learning_rate * observed_scale
            )
            state.sample_count += 1
        state.last_update_trade_date = estimate.trade_date
        state.warning_json = json.dumps(warnings, ensure_ascii=False)
        state.confidence_level = determine_realtime_confidence(
            sample_count=state.sample_count,
            recent_mae=state.recent_mae,
            warning_count=len(warnings),
        )

    session.commit()
    return [
        OnlineCalibrationStateResult(
            fund_code=state.fund_code,
            holding_version_id=state.holding_version_id,
            base_scale_factor=state.base_scale_factor,
            current_scale_factor=state.current_scale_factor,
            min_scale_factor=state.min_scale_factor,
            max_scale_factor=state.max_scale_factor,
            ewma_error=state.ewma_error,
            recent_mae=state.recent_mae,
            sample_count=state.sample_count,
            last_update_trade_date=state.last_update_trade_date,
            confidence_level=state.confidence_level,
            warning_json=json.loads(state.warning_json or "[]"),
        )
        for state in sorted(state_map.values(), key=lambda item: (item.fund_code, item.holding_version_id))
    ]


def get_online_calibration_state(
    session: Session,
    fund_code: str,
    trade_date: date,
) -> OnlineCalibrationState | None:
    holding_version = select_holding_version(session, fund_code, trade_date)
    if holding_version is None:
        return None
    state = session.scalar(
        select(OnlineCalibrationState).where(
            OnlineCalibrationState.fund_code == fund_code,
            OnlineCalibrationState.holding_version_id == holding_version.id,
        )
    )
    if state is None:
        state = upsert_online_calibration_state(
            session=session,
            fund_code=fund_code,
            holding_version_id=holding_version.id,
            trade_date=trade_date,
        )
        session.flush()
    return state


def get_effective_weight_version(
    session: Session,
    fund_code: str,
    trade_date: date,
) -> EffectiveWeightVersion | None:
    holding_version = select_holding_version(session, fund_code, trade_date)
    if holding_version is None:
        return None

    allocation = select_asset_allocation(session, fund_code, trade_date)
    version = session.scalar(
        select(EffectiveWeightVersion).where(
            EffectiveWeightVersion.fund_code == fund_code,
            EffectiveWeightVersion.holding_version_id == holding_version.id,
            EffectiveWeightVersion.source == "覆盖率缩放",
        )
    )
    if version is None:
        return build_effective_weight_version(session, fund_code, trade_date)

    target_stock_weight = None if allocation is None else allocation.stock_weight
    target_scale = calculate_weight_scale_factor(
        covered_weight=sum(item.weight for item in holding_version.items),
        stock_weight=target_stock_weight,
    )
    if (
        version.asset_allocation_id != (None if allocation is None else allocation.id)
        or abs(version.scale_factor - target_scale) > 1e-9
        or len(version.items) != len(holding_version.items)
    ):
        return build_effective_weight_version(session, fund_code, trade_date)
    return version


def compute_live_fund_estimate(
    session: Session,
    fund_code: str,
    live_quotes: dict[str, dict[str, object]],
    trade_date: date,
    quote_time: datetime | None = None,
    fund_name: str | None = None,
    selection_window: int = 20,
    min_samples: int = 10,
    min_improvement_bps: int = 5,
    selection_policy: str = "coverage_first",
    calibration_window: int = 20,
    calibration_base: str = "coverage_adjusted",
    calibration_min_samples: int = 5,
) -> LiveFundEstimateResult | None:
    fund = session.get(Fund, fund_code)
    version = select_holding_version(session, fund_code, trade_date)
    position = session.scalar(
        select(UserFundPosition).where(
            UserFundPosition.fund_code == fund_code,
            UserFundPosition.is_active.is_(True),
        )
    )
    if version is None:
        return LiveFundEstimateResult(
            fund_code=fund_code,
            fund_name=fund_name or (fund.fund_name if fund else fund_code),
            trade_date=trade_date,
            quote_time=quote_time,
            current_estimate=None,
            effective_method="未拉取",
            confidence_level=None,
            holding_amount=position.holding_amount if position else None,
            estimated_today_profit=None,
            latest_real_nav_date=None,
            current_scale_factor=1.0,
            data_warning="无持仓数据",
            raw_estimate=0.0,
            effective_weight_estimate=None,
            coverage_adjusted_estimate=None,
            calibrated_estimate=None,
            single_scale_estimate=None,
            two_factor_estimate=None,
            final_estimate=None,
            final_method="无数据",
            covered_weight=0.0,
            missing_weight=0.0,
            latest_mae=None,
            direction_hit_rate=None,
            holding_version_id=None,
            best_status="no_data",
            decision_reason="缺少持仓配置",
            warnings=["当前基金尚未拉取或配置任何持仓数据"],
            holdings=[],
            error_band_pct=None,
            error_band_label="缺持仓",
            confidence_text="缺持仓",
        )
    state = get_online_calibration_state(session, fund_code, trade_date)
    position = session.scalar(
        select(UserFundPosition).where(
            UserFundPosition.fund_code == fund_code,
            UserFundPosition.is_active.is_(True),
        )
    )
    latest_real_nav_date = session.scalar(
        select(ActualReturn.trade_date)
        .where(ActualReturn.fund_code == fund_code)
        .order_by(ActualReturn.trade_date.desc())
        .limit(1)
    )

    warnings: list[str] = []
    raw_estimate = 0.0
    effective_weight_estimate = 0.0
    covered_weight = 0.0
    holdings: list[LiveHoldingContribution] = []
    effective_version = get_effective_weight_version(session, fund_code, trade_date)
    effective_weight_map = {}
    if effective_version is not None:
        effective_weight_map = {
            item.asset_code: item
            for item in effective_version.items
        }
    current_scale_factor = 1.0 if state is None else state.current_scale_factor
    sample_count_for_model = 0 if state is None else state.sample_count
    beta_known = 1.0 if state is None else (state.beta_known or 1.0)
    beta_unknown = 1.0 if state is None else (state.beta_unknown or 1.0)
    alpha = 0.0 if state is None else (state.alpha or 0.0)
    model_weights: dict[str, float] = {}
    if state is not None and getattr(state, "model_weight_json", ""):
        try:
            model_weights = {
                str(k): float(v)
                for k, v in json.loads(state.model_weight_json or "{}").items()
            }
        except Exception:
            model_weights = {}
    selected_model = "coverage_adjusted" if state is None else getattr(state, "selected_model", "") or ""
    if not selected_model:
        if sample_count_for_model >= 15:
            selected_model = "two_factor"
        elif sample_count_for_model >= 5:
            selected_model = "single_scale"
        else:
            selected_model = "coverage_adjusted"

    if selected_model == "two_factor":
        display_known_factor = beta_known
        model_mode = "two_factor"
    elif selected_model == "single_scale":
        display_known_factor = current_scale_factor
        beta_known = current_scale_factor
        beta_unknown = current_scale_factor
        alpha = 0.0
        model_mode = "single_scale"
    else:
        display_known_factor = current_scale_factor
        beta_known = 1.0
        beta_unknown = 1.0
        alpha = 0.0
        model_mode = "coverage_adjusted"
    allocation = select_asset_allocation(session, fund_code, trade_date)
    published_total_weight = sum(item.weight for item in version.items)
    stock_weight = published_total_weight if allocation is None else allocation.stock_weight

    for item in sorted(version.items, key=lambda row: row.weight, reverse=True):
        quote = live_quotes.get(item.asset_code)
        effective_item = effective_weight_map.get(item.asset_code)
        published_weight = item.weight if effective_item is None else effective_item.published_weight
        effective_weight = published_weight * display_known_factor
        adjustment_factor = display_known_factor
        contribution_explain = "按因果在线参数修正已知持仓"
        return_pct = None if quote is None else float(quote["return_pct"])
        contribution_pct = None
        if return_pct is not None:
            covered_weight += item.weight
            raw_estimate += item.weight * return_pct
            effective_weight_estimate += effective_weight * return_pct
            contribution_pct = effective_weight * return_pct * 100.0
            if quote_time is None:
                current_quote_time = quote.get("quote_time")
                if isinstance(current_quote_time, datetime):
                    quote_time = current_quote_time
        else:
            warnings.append(f"Warning: live quote missing for {item.asset_code}.")
        holdings.append(
            LiveHoldingContribution(
                asset_code=item.asset_code,
                asset_name=item.asset_name,
                asset_type=item.asset_type,
                published_weight_pct=round(published_weight * 100, 4),
                effective_weight_pct=round(effective_weight * 100, 4),
                adjustment_factor=round(adjustment_factor, 6),
                return_pct=None if return_pct is None else round(return_pct * 100, 4),
                contribution_pct=None if contribution_pct is None else round(contribution_pct, 4),
                contribution_explain=contribution_explain,
            )
        )

    missing_weight = max(version.total_weight - covered_weight, 0.0)
    known_avg = raw_estimate / covered_weight if covered_weight > 0 else 0.0
    unknown_weight = max((stock_weight or 0.0) - published_total_weight, 0.0)
    unknown_estimate = unknown_weight * known_avg
    base_estimate = raw_estimate + unknown_estimate
    causal_calibrated_estimate = beta_known * raw_estimate + beta_unknown * unknown_estimate + alpha
    single_scale_estimate = current_scale_factor * base_estimate
    _two_factor_estimate = causal_calibrated_estimate  # 保持纯 two_factor，在 ensemble 覆盖之前
    if model_weights:
        candidate_estimates = {
            "coverage_adjusted": base_estimate,
            "single_scale": single_scale_estimate,
            "two_factor": causal_calibrated_estimate,
        }
        usable_weights = {
            model: weight
            for model, weight in model_weights.items()
            if model in candidate_estimates
        }
        total_weight = sum(usable_weights.values())
        if total_weight > 0:
            causal_calibrated_estimate = sum(
                candidate_estimates[model] * weight / total_weight
                for model, weight in usable_weights.items()
            )
            model_mode = "ensemble"
    coverage_adjusted_estimate: float | None = None
    coverage_warnings: list[str] = []
    if effective_version is not None:
        coverage_adjusted_estimate = base_estimate
    else:
        coverage_adjusted_estimate, coverage_warnings = compute_coverage_adjusted_estimate(
            session=session,
            fund_code=fund_code,
            trade_date=trade_date,
            raw_estimate=raw_estimate,
            covered_weight=covered_weight,
        )
        warnings.extend(coverage_warnings)

    sample_count = sample_count_for_model
    calibrated_estimate = causal_calibrated_estimate
    calibrated_mae: float | None = None
    calibrated_hit_rate: float | None = None
    calibrated_corr: float | None = None

    history_rows = _collect_selection_history(
        session=session,
        fund_code=fund_code,
        trade_date=trade_date,
        selection_window=selection_window,
    )
    method_errors = _build_method_error_history(history_rows)
    raw_mae = _calculate_mae_from_errors(method_errors["raw"])
    coverage_mae = _calculate_mae_from_errors(method_errors["coverage_adjusted"])
    raw_hit_rate = _calculate_hit_rate_from_errors(method_errors["raw"])
    coverage_hit_rate = _calculate_hit_rate_from_errors(method_errors["coverage_adjusted"])
    raw_corr = _calculate_corr_from_error_history(method_errors["raw"])
    coverage_corr = _calculate_corr_from_error_history(method_errors["coverage_adjusted"])

    selected_sample_count = sample_count_for_model
    best_method = model_mode
    best_estimate = causal_calibrated_estimate
    best_status = "ok"
    decision_reason = "首页使用已知持仓贡献 + 未知仓位代理贡献的因果在线校准"
    best_mae = None if state is None else state.recent_mae
    best_hit_rate = None
    if state is not None and state.warning_json:
        warnings.extend(json.loads(state.warning_json))
    confidence_level = "D" if state is None else state.confidence_level
    from .calibration import calculate_error_band
    error_band = calculate_error_band(session, fund_code, version.id)

    estimated_today_profit = None
    holding_amount = None if position is None else position.holding_amount
    if holding_amount is not None:
        estimated_today_profit = holding_amount * best_estimate
    data_warning = None
    if covered_weight <= 0:
        data_warning = "行情缺失"
        best_status = "missing_quotes"
        best_estimate = None
        estimated_today_profit = None
        error_band = {"error_band_pct": None, "error_band_label": "不可估", "confidence_text": "不可估"}
    elif quote_time is None:
        data_warning = "缺少实时行情"

    return LiveFundEstimateResult(
        fund_code=fund_code,
        fund_name=fund_name or (fund.fund_name if fund is not None else fund_code),
        trade_date=trade_date,
        quote_time=quote_time,
        current_estimate=None if best_estimate is None else round(best_estimate, 8),
        effective_method="修正权重",
        confidence_level=confidence_level,
        holding_amount=holding_amount,
        estimated_today_profit=None if estimated_today_profit is None else round(estimated_today_profit, 8),
        latest_real_nav_date=latest_real_nav_date,
        current_scale_factor=round(current_scale_factor, 8),
        data_warning=data_warning,
        raw_estimate=round(raw_estimate, 8),
        effective_weight_estimate=None if coverage_adjusted_estimate is None else round(effective_weight_estimate, 8),
        coverage_adjusted_estimate=None if coverage_adjusted_estimate is None else round(coverage_adjusted_estimate, 8),
        calibrated_estimate=None if calibrated_estimate is None else round(calibrated_estimate, 8),
        single_scale_estimate=round(single_scale_estimate, 8),
        two_factor_estimate=round(_two_factor_estimate, 8),
        final_estimate=None if best_estimate is None else round(best_estimate, 8),
        final_method=best_method,
        covered_weight=round(covered_weight, 8),
        missing_weight=round(missing_weight, 8),
        latest_mae=best_mae,
        direction_hit_rate=best_hit_rate,
        holding_version_id=version.id,
        best_status=best_status,
        decision_reason=decision_reason,
        warnings=warnings,
        holdings=holdings,
        error_band_pct=error_band["error_band_pct"],
        error_band_label=str(error_band["error_band_label"]),
        confidence_text=str(error_band["confidence_text"]),
    )


def compute_live_fund_estimates(
    session: Session,
    live_quotes: dict[str, dict[str, object]],
    trade_date: date,
    quote_time: datetime | None = None,
    fund_code: str | None = None,
    selection_window: int = 20,
    min_samples: int = 10,
    min_improvement_bps: int = 5,
    selection_policy: str = "coverage_first",
    calibration_window: int = 20,
    calibration_base: str = "coverage_adjusted",
    calibration_min_samples: int = 5,
) -> list[LiveFundEstimateResult]:
    stmt = select(Fund).where(Fund.is_active.is_(True)).order_by(Fund.fund_code.asc())
    if fund_code:
        stmt = stmt.where(Fund.fund_code == fund_code)
    results: list[LiveFundEstimateResult] = []
    for fund in session.scalars(stmt).all():
        result = compute_live_fund_estimate(
            session=session,
            fund_code=fund.fund_code,
            live_quotes=live_quotes,
            trade_date=trade_date,
            quote_time=quote_time,
            fund_name=fund.fund_name,
            selection_window=selection_window,
            min_samples=min_samples,
            min_improvement_bps=min_improvement_bps,
            selection_policy=selection_policy,
            calibration_window=calibration_window,
            calibration_base=calibration_base,
            calibration_min_samples=calibration_min_samples,
        )
        if result is not None:
            results.append(result)
            # 保存实时估值快照（含所有模型值 + ensemble），校准直接使用，不再重算
            _upsert_live_fund_estimate(session, result)
    return results


def _upsert_live_fund_estimate(session: Session, result: LiveFundEstimateResult) -> None:
    from .models import FundEstimate
    import json
    existing = session.get(FundEstimate, {"trade_date": result.trade_date, "fund_code": result.fund_code})
    snapshot = json.dumps({
        "current_estimate": result.current_estimate,
        "coverage_adjusted": result.coverage_adjusted_estimate,
        "single_scale": result.single_scale_estimate,
        "two_factor": result.two_factor_estimate,
        "calibrated": result.calibrated_estimate,
    })
    if existing is not None:
        existing.raw_estimate = result.raw_estimate
        existing.covered_weight = result.covered_weight
        existing.missing_weight = result.missing_weight
        existing.missing_assets_json = snapshot
    else:
        session.add(FundEstimate(
            trade_date=result.trade_date,
            fund_code=result.fund_code,
            holding_version_id=result.holding_version_id or 0,
            raw_estimate=result.raw_estimate,
            covered_weight=result.covered_weight,
            missing_weight=result.missing_weight,
            missing_assets_json=snapshot,
        ))
    session.commit()


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
    min_improvement_bps: int = 5,
    selection_policy: str = "coverage_first",
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

        raw_corr = _calculate_corr_from_error_history(method_errors["raw"])
        coverage_corr = _calculate_corr_from_error_history(method_errors["coverage_adjusted"])
        calibrated_corr = _calculate_corr_from_error_history(method_errors["calibrated"])

        (
            best_method,
            best_estimate,
            best_status,
            decision_reason,
        ) = _select_best_method_for_policy(
            selection_policy=selection_policy,
            raw_estimate=estimate.raw_estimate,
            coverage_adjusted_estimate=coverage_adjusted_estimate,
            calibrated_estimate=calibrated_estimate,
            sample_count=sample_count,
            min_samples=min_samples,
            improvement_threshold=improvement_threshold,
            min_improvement_bps=min_improvement_bps,
            raw_mae=raw_mae,
            coverage_mae=coverage_mae,
            calibrated_mae=calibrated_mae,
            raw_hit_rate=raw_hit_rate,
            coverage_hit_rate=coverage_hit_rate,
            calibrated_hit_rate=calibrated_hit_rate,
            raw_corr=raw_corr,
            coverage_corr=coverage_corr,
            calibrated_corr=calibrated_corr,
            method_errors=method_errors,
            warnings=warnings,
        )

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
            selection_policy=selection_policy,
        )
        selected_row.raw_estimate = estimate.raw_estimate
        selected_row.coverage_adjusted_estimate = coverage_adjusted_estimate
        selected_row.calibrated_estimate = calibrated_estimate
        selected_row.best_estimate = round(best_estimate, 8)
        selected_row.best_method = best_method
        selected_row.selection_policy = selection_policy
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
                selection_policy=selection_policy,
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
    min_improvement_bps: int = 5,
    selection_policy: str = "coverage_first",
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
            selection_policy=selection_policy,
        )
        total_count += len(results)
    return total_count


def calculate_selected_stats(
    session: Session,
    fund_code: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    selection_window: int = 20,
    selection_policy: str = "coverage_first",
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
        .where(
            SelectedEstimate.selection_window == selection_window,
            SelectedEstimate.selection_policy == selection_policy,
        )
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
                selection_policy=selection_policy,
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


def inspect_selected_estimates(
    session: Session,
    fund_code: str,
    method: str,
    start_date: date | None = None,
    end_date: date | None = None,
    selection_window: int = 20,
    selection_policy: str = "coverage_first",
) -> list[SelectionInspectResult]:
    stmt = (
        select(SelectedEstimate)
        .where(
            SelectedEstimate.fund_code == fund_code,
            SelectedEstimate.best_method == method,
            SelectedEstimate.selection_window == selection_window,
            SelectedEstimate.selection_policy == selection_policy,
        )
        .order_by(SelectedEstimate.trade_date.asc())
    )
    if start_date is not None:
        stmt = stmt.where(SelectedEstimate.trade_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(SelectedEstimate.trade_date <= end_date)

    return [
        SelectionInspectResult(
            trade_date=row.trade_date,
            fund_code=row.fund_code,
            raw_estimate=row.raw_estimate,
            coverage_adjusted_estimate=row.coverage_adjusted_estimate,
            calibrated_estimate=row.calibrated_estimate,
            best_method=row.best_method,
            decision_reason=row.decision_reason,
            sample_count=row.sample_count,
            raw_mae=row.raw_mae,
            coverage_adjusted_mae=row.coverage_adjusted_mae,
            calibrated_mae=row.calibrated_mae,
            best_status=row.best_status,
            warning_json=json.loads(row.warning_json or "[]"),
        )
        for row in session.scalars(stmt).all()
    ]


def _select_best_method_for_policy(
    selection_policy: str,
    raw_estimate: float,
    coverage_adjusted_estimate: float | None,
    calibrated_estimate: float | None,
    sample_count: int,
    min_samples: int,
    improvement_threshold: float,
    min_improvement_bps: int,
    raw_mae: float | None,
    coverage_mae: float | None,
    calibrated_mae: float | None,
    raw_hit_rate: float | None,
    coverage_hit_rate: float | None,
    calibrated_hit_rate: float | None,
    raw_corr: float | None,
    coverage_corr: float | None,
    calibrated_corr: float | None,
    method_errors: dict[str, list[dict[str, float | date]]],
    warnings: list[str],
) -> tuple[str, float, str, str]:
    if selection_policy == "default":
        return _select_best_method_default(
            raw_estimate=raw_estimate,
            coverage_adjusted_estimate=coverage_adjusted_estimate,
            calibrated_estimate=calibrated_estimate,
            sample_count=sample_count,
            min_samples=min_samples,
            improvement_threshold=improvement_threshold,
            min_improvement_bps=min_improvement_bps,
            raw_mae=raw_mae,
            coverage_mae=coverage_mae,
            calibrated_mae=calibrated_mae,
            method_errors=method_errors,
            warnings=warnings,
        )

    return _select_best_method_coverage_first(
        policy_name=selection_policy,
        raw_estimate=raw_estimate,
        coverage_adjusted_estimate=coverage_adjusted_estimate,
        calibrated_estimate=calibrated_estimate,
        sample_count=sample_count,
        min_samples=min_samples,
        improvement_threshold=improvement_threshold,
        min_improvement_bps=min_improvement_bps,
        raw_mae=raw_mae,
        coverage_mae=coverage_mae,
        calibrated_mae=calibrated_mae,
        raw_hit_rate=raw_hit_rate,
        coverage_hit_rate=coverage_hit_rate,
        calibrated_hit_rate=calibrated_hit_rate,
        raw_corr=raw_corr,
        coverage_corr=coverage_corr,
        calibrated_corr=calibrated_corr,
        method_errors=method_errors,
        warnings=warnings,
    )


def _select_best_method_default(
    raw_estimate: float,
    coverage_adjusted_estimate: float | None,
    calibrated_estimate: float | None,
    sample_count: int,
    min_samples: int,
    improvement_threshold: float,
    min_improvement_bps: int,
    raw_mae: float | None,
    coverage_mae: float | None,
    calibrated_mae: float | None,
    method_errors: dict[str, list[dict[str, float | date]]],
    warnings: list[str],
) -> tuple[str, float, str, str]:
    best_method = "raw"
    best_estimate = raw_estimate
    best_status = "ok"
    decision_reason = "raw 为默认基线方法。"

    if sample_count < min_samples:
        if coverage_adjusted_estimate is not None:
            return (
                "coverage_adjusted",
                coverage_adjusted_estimate,
                "insufficient_samples_fallback",
                "历史样本不足, coverage_adjusted 可用, 使用 coverage_adjusted fallback。",
            )
        return ("raw", raw_estimate, "insufficient_samples_fallback", "历史样本不足, coverage_adjusted 不可用, 使用 raw fallback。")

    current_best_method = "raw"
    current_best_estimate = raw_estimate
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
                warnings.append("Warning: coverage_adjusted recent underperform protection triggered, keep raw.")
            else:
                current_best_method = "coverage_adjusted"
                current_best_estimate = coverage_adjusted_estimate
                current_best_mae = coverage_mae
                current_reason = (
                    f"coverage_adjusted 历史 MAE 比 raw 低 {format_percent(raw_mae - coverage_mae)},"
                    f" 超过切换阈值 {min_improvement_bps} bps。"
                )
        else:
            current_reason = f"coverage_adjusted 相比 raw 的改进未超过切换阈值 {min_improvement_bps} bps, 保持 raw。"

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
                warnings.append("Warning: calibrated recent underperform protection triggered, keep base method.")
                best_status = "protected_switch_blocked"
            else:
                current_best_method = "calibrated"
                current_best_estimate = calibrated_estimate
                current_reason = (
                    f"calibrated 历史 MAE 比 {baseline_method} 低 "
                    f"{format_percent((method_errors_mae(method_errors[baseline_method]) or 0) - calibrated_mae)},"
                    f" 超过切换阈值 {min_improvement_bps} bps。"
                )
        else:
            current_reason = (
                f"calibrated 仅小幅领先 {current_best_method}, 未超过切换阈值 {min_improvement_bps} bps,"
                f" 选择更稳的 {current_best_method}。"
            )

    return current_best_method, current_best_estimate, best_status, current_reason


def _select_best_method_coverage_first(
    policy_name: str,
    raw_estimate: float,
    coverage_adjusted_estimate: float | None,
    calibrated_estimate: float | None,
    sample_count: int,
    min_samples: int,
    improvement_threshold: float,
    min_improvement_bps: int,
    raw_mae: float | None,
    coverage_mae: float | None,
    calibrated_mae: float | None,
    raw_hit_rate: float | None,
    coverage_hit_rate: float | None,
    calibrated_hit_rate: float | None,
    raw_corr: float | None,
    coverage_corr: float | None,
    calibrated_corr: float | None,
    method_errors: dict[str, list[dict[str, float | date]]],
    warnings: list[str],
) -> tuple[str, float, str, str]:
    if sample_count < min_samples:
        if coverage_adjusted_estimate is not None:
            return (
                "coverage_adjusted",
                coverage_adjusted_estimate,
                "insufficient_samples_fallback",
                "历史样本不足, coverage_adjusted 可用, 使用 coverage_adjusted fallback。",
            )
        return ("raw", raw_estimate, "insufficient_samples_fallback", "历史样本不足且 coverage_adjusted 不可用, 使用 raw fallback。")

    if coverage_adjusted_estimate is None:
        if calibrated_estimate is not None and calibrated_mae is not None and raw_mae is not None and calibrated_mae <= raw_mae - improvement_threshold:
            if _recent_underperform_count(method_errors["calibrated"], method_errors["raw"]) >= 2:
                warnings.append("Warning: calibrated recent underperform protection triggered, keep raw.")
                return ("raw", raw_estimate, "coverage_unavailable_fallback", "coverage_adjusted 不可用, calibrated 最近表现不稳, 保持 raw。")
            return ("calibrated", calibrated_estimate, "ok", "coverage_adjusted 不可用, calibrated 明显优于 raw, 选择 calibrated。")
        return ("raw", raw_estimate, "coverage_unavailable_fallback", "coverage_adjusted 不可用, 使用 raw。")

    best_method = "coverage_adjusted"
    best_estimate = coverage_adjusted_estimate
    best_status = "ok"
    decision_reason = "coverage_adjusted 为默认 baseline。"

    if (
        raw_mae is not None
        and coverage_mae is not None
        and len(method_errors["raw"]) >= min_samples
        and raw_mae <= coverage_mae - improvement_threshold
    ):
        if _recent_underperform_count(method_errors["raw"], method_errors["coverage_adjusted"]) >= 2:
            warnings.append("Warning: raw recent underperform protection triggered, keep coverage_adjusted.")
        else:
            best_method = "raw"
            best_estimate = raw_estimate
            decision_reason = (
                f"raw 历史 MAE 比 coverage_adjusted 低 {format_percent(coverage_mae - raw_mae)},"
                f" 超过切换阈值 {min_improvement_bps} bps。"
            )

    baseline_method = best_method
    baseline_estimate = best_estimate
    baseline_mae = raw_mae if best_method == "raw" else coverage_mae
    baseline_hit_rate = raw_hit_rate if best_method == "raw" else coverage_hit_rate
    baseline_corr = raw_corr if best_method == "raw" else coverage_corr

    if calibrated_estimate is None or calibrated_mae is None or len(method_errors["calibrated"]) < min_samples:
        return best_method, best_estimate, best_status, decision_reason

    if baseline_mae is None or calibrated_mae > baseline_mae - improvement_threshold:
        return (
            best_method,
            best_estimate,
            best_status,
            f"calibrated 对 {baseline_method} 的改进未超过切换阈值 {min_improvement_bps} bps, 保持 {baseline_method}。",
        )

    if baseline_hit_rate is not None and calibrated_hit_rate is not None and calibrated_hit_rate < baseline_hit_rate:
        return (
            best_method,
            best_estimate,
            "protected_switch_blocked",
            f"calibrated 的方向命中率低于 {baseline_method}, 保持 {baseline_method}。",
        )

    corr_floor = None if baseline_corr is None else baseline_corr - 0.05
    if corr_floor is not None and calibrated_corr is not None and calibrated_corr < corr_floor:
        return (
            best_method,
            best_estimate,
            "protected_switch_blocked",
            f"calibrated 的相关系数明显低于 {baseline_method}, 保持 {baseline_method}。",
        )

    if _recent_underperform_count(method_errors["calibrated"], method_errors[baseline_method]) >= 2:
        warnings.append("Warning: calibrated recent underperform protection triggered, keep base method.")
        return (
            best_method,
            best_estimate,
            "protected_switch_blocked",
            f"calibrated 最近 3 个样本中至少 2 次明显更差, 保持 {baseline_method}。",
        )

    if policy_name == "calibrated_if_clear":
        reason_prefix = "calibrated 明显优于 coverage_adjusted, 切换为 calibrated。"
    else:
        reason_prefix = f"calibrated 明显优于 {baseline_method}, 切换为 calibrated。"
    return ("calibrated", calibrated_estimate, "ok", reason_prefix)


def upsert_selected_estimate(
    session: Session,
    trade_date: date,
    fund_code: str,
    holding_version_id: int,
    selection_window: int,
    selection_policy: str,
) -> SelectedEstimate:
    selected_row = session.scalar(
        select(SelectedEstimate).where(
            SelectedEstimate.trade_date == trade_date,
            SelectedEstimate.fund_code == fund_code,
            SelectedEstimate.holding_version_id == holding_version_id,
            SelectedEstimate.selection_window == selection_window,
            SelectedEstimate.selection_policy == selection_policy,
        )
    )
    if selected_row is None:
        selected_row = SelectedEstimate(
            trade_date=trade_date,
            fund_code=fund_code,
            holding_version_id=holding_version_id,
            selection_policy=selection_policy,
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
