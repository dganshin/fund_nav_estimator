"""
因果在线校准（Causal Online Calibration）

核心原则：
- 今天 T 的盘中估值，只使用 <= T-1 的已公布真实净值数据
- 不做 batch training，不做随机 train/test split
- 不跨 holding_version 混用校准参数
- 每只基金单独校准
- 优先校准低维 scale_factor，不乱拟合每只股票权重

update 流程：
1. 找到最新已公布 actual_return 日期 D（D < today）
2. 用 D 当天股票收盘涨跌 + 当时 active holding_version + 当前 scale 计算 effective_estimate_D
3. 对比 actual_return_D，计算 observed_scale_D
4. EWMA 更新 current_scale_factor
5. 记录到 calibration_residuals
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    ActualReturn,
    CalibrationResidual,
    DailyQuote,
    FundAssetAllocation,
    Fund,
    HoldingVersion,
    OnlineCalibrationState,
    utcnow,
)

logger = logging.getLogger(__name__)

LEARNING_RATE = 0.10
MAX_ABS_RESIDUAL = 0.02   # 超过此残差不更新 scale（异常日）
MIN_RAW_ESTIMATE = 0.001  # raw_estimate 太小时不更新 scale
MIN_QUOTE_COVERAGE = 0.70
TWO_FACTOR_MIN_SAMPLES = 15
SCALE_MIN_SAMPLES = 5
RIDGE_LAMBDA = 20.0
RIDGE_DECAY = 0.90
RIDGE_WINDOW = 30


def calculate_error_band(
    session: Session,
    fund_code: str,
    holding_version_id: int | None = None,
    window: int = 20,
) -> dict[str, object]:
    """用当前 holding_version 的校准残差生成用户可读误差口径。"""
    if holding_version_id is None:
        holding_version = session.scalar(
            select(HoldingVersion)
            .where(HoldingVersion.fund_code == fund_code, HoldingVersion.is_active.is_(True))
            .order_by(HoldingVersion.report_date.desc())
        )
        holding_version_id = None if holding_version is None else holding_version.id
    if holding_version_id is None:
        return {"error_band_pct": None, "error_band_label": "不可估", "confidence_text": "不可估"}

    rows = session.scalars(
        select(CalibrationResidual)
        .where(
            CalibrationResidual.fund_code == fund_code,
            CalibrationResidual.holding_version_id == holding_version_id,
            CalibrationResidual.is_used_for_update.is_(True),
        )
        .order_by(CalibrationResidual.trade_date.desc())
        .limit(window)
    ).all()
    values = sorted([row.abs_residual for row in rows if row.abs_residual is not None])
    sample_count = len(values)
    if sample_count >= 10:
        idx = min(sample_count - 1, int((sample_count - 1) * 0.8))
        band = values[idx]
        label = f"预计误差≤±{band:.2%}"
    elif sample_count >= 5:
        band = sum(values) / sample_count
        label = f"参考误差±{band:.2%}"
    else:
        band = None
        label = "样本不足"
    return {
        "error_band_pct": None if band is None else band,
        "error_band_label": label,
        "confidence_text": label,
        "sample_count": sample_count,
    }


class CalibrationResult(NamedTuple):
    fund_code: str
    holding_version_id: int
    calibration_date: date
    scale_factor_before: float
    scale_factor_after: float
    raw_estimate: float
    effective_estimate: float
    actual_return: float
    residual: float
    observed_scale: float | None
    is_updated: bool
    skip_reason: str
    sample_count: int
    confidence_level: str


class CalibrationFeatures(NamedTuple):
    known_estimate: float
    unknown_estimate: float
    base_estimate: float
    quote_coverage: float
    covered_weight: float
    stock_weight: float


def _get_or_init_calibration_state(
    session: Session,
    fund_code: str,
    holding_version: HoldingVersion,
    base_scale: float,
) -> OnlineCalibrationState:
    """获取或初始化 online_calibration_states 记录。"""
    state = session.scalar(
        select(OnlineCalibrationState).where(
            OnlineCalibrationState.fund_code == fund_code,
            OnlineCalibrationState.holding_version_id == holding_version.id,
        )
    )
    if state is None:
        state = OnlineCalibrationState(
            fund_code=fund_code,
            holding_version_id=holding_version.id,
            base_scale_factor=base_scale,
            current_scale_factor=base_scale,
            min_scale_factor=base_scale * 0.80,
            max_scale_factor=base_scale * 1.20,
            beta_known=1.0,
            beta_unknown=1.0,
            alpha=0.0,
            sample_count=0,
        )
        session.add(state)
        session.flush()
    return state


def _compute_base_scale(session: Session, holding_version: HoldingVersion) -> float:
    """根据资产配置和持仓权重计算基准 scale_factor。"""
    covered_weight = sum(item.weight for item in holding_version.items)
    if covered_weight <= 0:
        return 1.0
    allocation = session.scalar(
        select(FundAssetAllocation)
        .where(
            FundAssetAllocation.fund_code == holding_version.fund_code,
            FundAssetAllocation.report_date <= holding_version.report_date,
        )
        .order_by(FundAssetAllocation.report_date.desc(), FundAssetAllocation.created_at.desc())
    )
    if allocation is None or allocation.stock_weight <= 0:
        return 1.0
    return allocation.stock_weight / covered_weight


def _select_asset_allocation_for_holding(
    session: Session,
    holding_version: HoldingVersion,
) -> FundAssetAllocation | None:
    return session.scalar(
        select(FundAssetAllocation)
        .where(
            FundAssetAllocation.fund_code == holding_version.fund_code,
            FundAssetAllocation.report_date <= holding_version.report_date,
            FundAssetAllocation.is_active.is_(True),
        )
        .order_by(FundAssetAllocation.report_date.desc(), FundAssetAllocation.created_at.desc())
    )


def _compute_features_from_daily_quotes(
    session: Session,
    holding_version: HoldingVersion,
    trade_date: date,
) -> CalibrationFeatures:
    """构造 known + unknown proxy 两个低维特征。"""
    items = holding_version.items
    known = 0.0
    quoted_weight = 0.0
    covered_weight = sum(item.weight for item in items)
    allocation = _select_asset_allocation_for_holding(session, holding_version)
    stock_weight = covered_weight if allocation is None else allocation.stock_weight
    for item in items:
        q = session.scalar(
            select(DailyQuote)
            .where(DailyQuote.asset_code == item.asset_code, DailyQuote.trade_date == trade_date)
        )
        if q is not None:
            quoted_weight += item.weight
            known += item.weight * q.return_pct
    known_avg = known / quoted_weight if quoted_weight > 0 else 0.0
    unknown_weight = max((stock_weight or 0.0) - covered_weight, 0.0)
    unknown = unknown_weight * known_avg
    quote_coverage = quoted_weight / covered_weight if covered_weight > 0 else 0.0
    return CalibrationFeatures(
        known_estimate=known,
        unknown_estimate=unknown,
        base_estimate=known + unknown,
        quote_coverage=quote_coverage,
        covered_weight=covered_weight,
        stock_weight=stock_weight or covered_weight,
    )


def _solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """小型高斯消元, 避免为 3 个参数强依赖 numpy。"""
    n = len(vector)
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        div = a[col][col]
        for j in range(col, n + 1):
            a[col][j] /= div
        for r in range(n):
            if r == col:
                continue
            factor = a[r][col]
            for j in range(col, n + 1):
                a[r][j] -= factor * a[col][j]
    return [a[i][n] for i in range(n)]


def _training_rows(
    session: Session,
    fund_code: str,
    holding_version_id: int,
    trade_date: date,
    window: int = RIDGE_WINDOW,
) -> list[CalibrationResidual]:
    rows = session.scalars(
        select(CalibrationResidual)
        .where(
            CalibrationResidual.fund_code == fund_code,
            CalibrationResidual.holding_version_id == holding_version_id,
            CalibrationResidual.trade_date < trade_date,
            CalibrationResidual.is_used_for_update.is_(True),
        )
        .order_by(CalibrationResidual.trade_date.desc())
        .limit(window)
    ).all()
    return list(reversed(rows))


def _fit_single_scale(rows: list[CalibrationResidual], base_scale: float) -> float:
    if not rows:
        return base_scale
    scale = base_scale
    for row in rows:
        base = row.base_estimate or row.raw_estimate
        if abs(base) < MIN_RAW_ESTIMATE:
            continue
        observed = row.actual_return / base
        observed = max(base_scale * 0.80, min(base_scale * 1.20, observed))
        scale = 0.8 * scale + 0.2 * observed
    return max(base_scale * 0.80, min(base_scale * 1.20, scale))


def _fit_two_factor(rows: list[CalibrationResidual]) -> tuple[float, float, float]:
    if len(rows) < TWO_FACTOR_MIN_SAMPLES:
        return 1.0, 1.0, 0.0
    rows = rows[-RIDGE_WINDOW:]
    xtx = [[0.0, 0.0, 0.0] for _ in range(3)]
    xty = [0.0, 0.0, 0.0]
    latest_index = len(rows) - 1
    for idx, row in enumerate(rows):
        w = RIDGE_DECAY ** (latest_index - idx)
        x = [row.known_estimate, row.unknown_estimate, 1.0]
        y = row.actual_return
        for i in range(3):
            xty[i] += w * x[i] * y
            for j in range(3):
                xtx[i][j] += w * x[i] * x[j]
    theta0 = [1.0, 1.0, 0.0]
    for i in range(3):
        xtx[i][i] += RIDGE_LAMBDA
        xty[i] += RIDGE_LAMBDA * theta0[i]
    solved = _solve_linear_system(xtx, xty)
    if solved is None:
        return 1.0, 1.0, 0.0
    beta_known = max(0.80, min(1.20, solved[0]))
    beta_unknown = max(0.50, min(1.80, solved[1]))
    alpha = max(-0.001, min(0.001, solved[2]))
    return beta_known, beta_unknown, alpha


def run_online_calibration(
    session: Session,
    fund_code: str,
    calibration_date: date | None = None,
    force: bool = False,
) -> CalibrationResult | None:
    """
    为指定基金运行一次因果在线校准。

    Parameters
    ----------
    calibration_date : 指定校准日期（必须有已公布 actual_return）；
                       None 表示使用最新已公布真实净值日。
    force : 是否强制重跑同一天（幂等）。

    Returns None if no calibration data is available.
    """
    # 1. 找 active holding_version
    holding_version = session.scalar(
        select(HoldingVersion)
        .where(HoldingVersion.fund_code == fund_code, HoldingVersion.is_active.is_(True))
        .order_by(HoldingVersion.report_date.desc())
    )
    if holding_version is None:
        logger.warning("calibration: no active holding_version for %s", fund_code)
        return None

    # 2. 确定校准日期
    if calibration_date is None:
        latest_ar = session.scalar(
            select(ActualReturn)
            .where(ActualReturn.fund_code == fund_code)
            .where(ActualReturn.trade_date >= holding_version.report_date)
            .order_by(ActualReturn.trade_date.desc())
        )
        if latest_ar is None:
            logger.warning("calibration: no actual_return for %s", fund_code)
            return None
        calibration_date = latest_ar.trade_date
    else:
        ar_check = session.scalar(
            select(ActualReturn)
            .where(ActualReturn.fund_code == fund_code, ActualReturn.trade_date == calibration_date)
        )
        if ar_check is None:
            logger.warning("calibration: no actual_return for %s on %s", fund_code, calibration_date)
            return None

    actual_return_obj = session.scalar(
        select(ActualReturn)
        .where(ActualReturn.fund_code == fund_code, ActualReturn.trade_date == calibration_date)
    )
    actual_return_val = actual_return_obj.actual_return

    # 3. 获取/初始化 calibration state
    base_scale = _compute_base_scale(session, holding_version)
    state = _get_or_init_calibration_state(session, fund_code, holding_version, base_scale)

    # 4. 幂等检查：如果已经校准过同一天且 force=False，则跳过更新但仍记录
    already_calibrated = (state.last_update_trade_date == calibration_date)

    # 5. 构造当天特征, 训练样本严格限制在 trade_date < D。
    features = _compute_features_from_daily_quotes(session, holding_version, calibration_date)
    training = _training_rows(session, fund_code, holding_version.id, calibration_date)
    sample_count = len(training)
    raw_estimate = features.base_estimate

    scale_before = state.current_scale_factor
    if sample_count >= TWO_FACTOR_MIN_SAMPLES:
        beta_known, beta_unknown, alpha = _fit_two_factor(training)
        scale_for_row = beta_known
        model_mode = "two_factor"
    elif sample_count >= SCALE_MIN_SAMPLES:
        scale_for_row = _fit_single_scale(training, 1.0)
        beta_known, beta_unknown, alpha = scale_for_row, scale_for_row, 0.0
        model_mode = "single_scale"
    else:
        beta_known, beta_unknown, alpha = 1.0, 1.0, 0.0
        scale_for_row = 1.0
        model_mode = "coverage_adjusted"

    effective_estimate = (
        beta_known * features.known_estimate
        + beta_unknown * features.unknown_estimate
        + alpha
    )

    residual = actual_return_val - effective_estimate
    abs_residual = abs(residual)

    # 6. 决定该日样本是否可用于后续日期训练。
    observed_scale: float | None = None
    skip_reason = ""
    is_updated = False
    new_scale = scale_for_row

    if already_calibrated and not force:
        skip_reason = "already_calibrated_this_date"
    elif features.quote_coverage < MIN_QUOTE_COVERAGE:
        skip_reason = f"quote_coverage_too_low({features.quote_coverage:.2%})"
    elif abs(features.base_estimate) < MIN_RAW_ESTIMATE:
        skip_reason = f"raw_estimate_too_small({features.base_estimate:.6f})"
    elif abs_residual > MAX_ABS_RESIDUAL:
        skip_reason = f"abs_residual_too_large({abs_residual:.4%})"
    else:
        observed_scale = actual_return_val / features.base_estimate
        is_updated = True

    # 7. 更新 state。参数由 T 以前样本训练而来, 只服务 T 之后盘中。
    if is_updated:
        state.current_scale_factor = new_scale
        state.beta_known = beta_known
        state.beta_unknown = beta_unknown
        state.alpha = alpha
        state.last_update_trade_date = calibration_date
        state.sample_count = (state.sample_count or 0) + 1
        prev_ewma = state.ewma_error or abs_residual
        state.ewma_error = 0.9 * prev_ewma + 0.1 * abs_residual
        state.recent_mae = abs_residual
        ewma = state.ewma_error or abs_residual
        if ewma < 0.005:
            state.confidence_level = "A"
        elif ewma < 0.010:
            state.confidence_level = "B"
        elif ewma < 0.020:
            state.confidence_level = "C"
        else:
            state.confidence_level = "D"
        state.warning_json = f'["{model_mode}"]'

    params_fitted_until = training[0].trade_date if training else None

    # 8. 写入 calibration_residuals（幂等 upsert）
    existing_residual = session.scalar(
        select(CalibrationResidual).where(
            CalibrationResidual.fund_code == fund_code,
            CalibrationResidual.holding_version_id == holding_version.id,
            CalibrationResidual.trade_date == calibration_date,
        )
    )
    if existing_residual is None:
        residual_row = CalibrationResidual(
            fund_code=fund_code,
            holding_version_id=holding_version.id,
            trade_date=calibration_date,
            actual_return=actual_return_val,
            known_estimate=features.known_estimate,
            unknown_estimate=features.unknown_estimate,
            base_estimate=features.base_estimate,
            raw_estimate=raw_estimate,
            calibrated_estimate=effective_estimate,
            effective_estimate=effective_estimate,
            residual=residual,
            abs_residual=abs_residual,
            scale_used_before_update=scale_before,
            beta_known=beta_known,
            beta_unknown=beta_unknown,
            alpha=alpha,
            sample_count=sample_count,
            params_fitted_until=params_fitted_until,
            model_version=model_mode,
            calibration_mode="online_scale",
            is_out_of_sample=True,
            observed_scale=observed_scale,
            scale_after_update=new_scale if is_updated else None,
            is_used_for_update=is_updated,
            skip_reason=skip_reason,
        )
        session.add(residual_row)
    else:
        existing_residual.actual_return = actual_return_val
        existing_residual.known_estimate = features.known_estimate
        existing_residual.unknown_estimate = features.unknown_estimate
        existing_residual.base_estimate = features.base_estimate
        existing_residual.raw_estimate = raw_estimate
        existing_residual.calibrated_estimate = effective_estimate
        existing_residual.effective_estimate = effective_estimate
        existing_residual.residual = residual
        existing_residual.abs_residual = abs_residual
        existing_residual.scale_used_before_update = scale_before
        existing_residual.beta_known = beta_known
        existing_residual.beta_unknown = beta_unknown
        existing_residual.alpha = alpha
        existing_residual.sample_count = sample_count
        existing_residual.params_fitted_until = params_fitted_until
        existing_residual.model_version = model_mode
        existing_residual.calibration_mode = "online_scale"
        existing_residual.is_out_of_sample = True
        existing_residual.observed_scale = observed_scale
        existing_residual.scale_after_update = new_scale if is_updated else None
        existing_residual.is_used_for_update = is_updated
        existing_residual.skip_reason = skip_reason

    session.commit()

    return CalibrationResult(
        fund_code=fund_code,
        holding_version_id=holding_version.id,
        calibration_date=calibration_date,
        scale_factor_before=scale_before,
        scale_factor_after=new_scale,
        raw_estimate=raw_estimate,
        effective_estimate=effective_estimate,
        actual_return=actual_return_val,
        residual=residual,
        observed_scale=observed_scale,
        is_updated=is_updated,
        skip_reason=skip_reason,
        sample_count=state.sample_count,
        confidence_level=state.confidence_level or "D",
    )


def load_calibration_residuals(
    session: Session,
    fund_code: str,
    limit: int = 90,
) -> list[dict]:
    """加载某基金从 active holding_version.report_date 起的残差记录，按日期升序。"""
    holding_version = session.scalar(
        select(HoldingVersion)
        .where(HoldingVersion.fund_code == fund_code, HoldingVersion.is_active.is_(True))
        .order_by(HoldingVersion.report_date.desc())
    )
    if holding_version is None:
        return []

    rows = session.scalars(
        select(CalibrationResidual)
        .where(
            CalibrationResidual.fund_code == fund_code,
            CalibrationResidual.holding_version_id == holding_version.id,
        )
        .order_by(CalibrationResidual.trade_date.asc())
        .limit(limit)
    ).all()

    return [
        {
            "trade_date": r.trade_date.isoformat(),
            "actual_return": f"{r.actual_return:+.4%}",
            "known_estimate": f"{r.known_estimate:+.4%}",
            "unknown_estimate": f"{r.unknown_estimate:+.4%}",
            "base_estimate": f"{r.base_estimate:+.4%}",
            "raw_estimate": f"{r.raw_estimate:+.4%}",
            "calibrated_estimate": f"{r.calibrated_estimate:+.4%}",
            "effective_estimate": f"{r.effective_estimate:+.4%}",
            "residual": f"{r.residual:+.4%}",
            "abs_residual": f"{r.abs_residual:.4%}",
            "scale_used": f"{r.scale_used_before_update:.4f}",
            "beta_known": f"{r.beta_known:.4f}",
            "beta_unknown": f"{r.beta_unknown:.4f}",
            "alpha": f"{r.alpha:+.4%}",
            "sample_count": r.sample_count,
            "params_fitted_until": r.params_fitted_until.isoformat() if r.params_fitted_until else "--",
            "model_version": r.model_version,
            "is_out_of_sample": "Y" if r.is_out_of_sample else "N",
            "observed_scale": f"{r.observed_scale:.4f}" if r.observed_scale is not None else "--",
            "updated_scale": f"{r.scale_after_update:.4f}" if r.scale_after_update is not None else "--",
            "is_used": "Y" if r.is_used_for_update else "N",
            "skip_reason": r.skip_reason or "",
        }
        for r in rows
    ]


def get_calibration_stats(
    session: Session,
    fund_code: str,
) -> dict:
    """返回简要校准统计，用于详情页展示。"""
    holding_version = session.scalar(
        select(HoldingVersion)
        .where(HoldingVersion.fund_code == fund_code, HoldingVersion.is_active.is_(True))
        .order_by(HoldingVersion.report_date.desc())
    )
    if holding_version is None:
        return {}

    state = session.scalar(
        select(OnlineCalibrationState).where(
            OnlineCalibrationState.fund_code == fund_code,
            OnlineCalibrationState.holding_version_id == holding_version.id,
        )
    )

    rows = session.scalars(
        select(CalibrationResidual)
        .where(
            CalibrationResidual.fund_code == fund_code,
            CalibrationResidual.holding_version_id == holding_version.id,
        )
    ).all()

    if not rows:
        band = calculate_error_band(session, fund_code, holding_version.id)
        return {
            "sample_count": 0,
            "current_scale": f"{state.current_scale_factor:.4f}" if state else "--",
            "confidence_level": state.confidence_level if state else "D",
            "error_band_label": band["error_band_label"],
            "last_calibration_date": state.last_update_trade_date.isoformat() if state and state.last_update_trade_date else "--",
        }

    raw_maes = [abs(r.actual_return - (r.base_estimate or r.raw_estimate)) for r in rows]
    raw_mae = sum(raw_maes) / len(raw_maes)
    eff_maes = [abs(r.actual_return - (r.calibrated_estimate or r.effective_estimate)) for r in rows]
    eff_mae = sum(eff_maes) / len(eff_maes)
    latest = rows[-1]
    band = calculate_error_band(session, fund_code, holding_version.id)
    sorted_abs = sorted(raw_maes)
    idx80 = min(len(sorted_abs) - 1, int((len(sorted_abs) - 1) * 0.8))

    return {
        "sample_count": len(rows),
        "raw_mae": f"{raw_mae:.4%}",
        "effective_mae": f"{eff_mae:.4%}",
        "mean_abs_error_20": f"{sum(raw_maes[-20:]) / min(len(raw_maes), 20):.4%}",
        "p80_abs_error": f"{sorted_abs[idx80]:.4%}",
        "max_abs_error": f"{max(raw_maes):.4%}",
        "error_band_label": band["error_band_label"],
        "improvement": f"{(raw_mae - eff_mae) / raw_mae:.1%}" if raw_mae > 0 else "--",
        "latest_residual": f"{latest.residual:+.4%}",
        "current_scale": f"{state.current_scale_factor:.4f}" if state else "--",
        "confidence_level": state.confidence_level if state else "D",
        "last_calibration_date": state.last_update_trade_date.isoformat() if state and state.last_update_trade_date else "--",
    }


def ensure_fund_by_code(
    session: Session,
    fund_code: str,
    data_source=None,
) -> dict:
    """
    确保 fund_code 在数据库中存在。
    如果不存在，尝试通过 data_source.fetch_fund_profile 自动创建。
    返回 fund 字典。
    """
    from .models import Fund
    from sqlalchemy import select

    fund = session.get(Fund, fund_code)
    if fund is not None:
        return {
            "fund_code": fund.fund_code,
            "fund_name": fund.fund_name,
            "fund_type": fund.fund_type,
            "market": fund.market,
            "is_active": fund.is_active,
            "created": False,
        }

    # 自动拉取基金信息
    fund_name = fund_code
    fund_type = "equity"
    market = "CN"
    latest_unit_nav: float | None = None
    latest_nav_date = None

    if data_source is not None and hasattr(data_source, "fetch_fund_profile"):
        try:
            profile = data_source.fetch_fund_profile(fund_code)
            fund_name = profile.fund_name or fund_code
            fund_type = profile.fund_type or "equity"
            market = profile.market or "CN"
            latest_unit_nav = profile.latest_unit_nav
            latest_nav_date = profile.latest_nav_date
        except Exception as exc:
            logger.warning("ensure_fund_by_code: fetch_fund_profile failed for %s: %s", fund_code, exc)

    new_fund = Fund(
        fund_code=fund_code,
        fund_name=fund_name,
        fund_type=fund_type,
        market=market,
        is_active=True,
    )
    session.add(new_fund)
    session.commit()

    return {
        "fund_code": new_fund.fund_code,
        "fund_name": new_fund.fund_name,
        "fund_type": new_fund.fund_type,
        "market": new_fund.market,
        "is_active": new_fund.is_active,
        "created": True,
        "latest_unit_nav": latest_unit_nav,
        "latest_nav_date": latest_nav_date.isoformat() if latest_nav_date else None,
    }


def run_incremental_calibration(session: Session, fund_code: str) -> int:
    """找出尚未生成 residual 的 actual_return 日期并逐日进行 run_online_calibration。"""
    from .models import ActualReturn, CalibrationResidual, HoldingVersion

    # 先找到 active holding_version
    stmt = select(HoldingVersion).where(HoldingVersion.fund_code == fund_code, HoldingVersion.is_active.is_(True))
    holding_version = session.scalar(stmt)
    if not holding_version:
        return 0

    # 找到所有 actual_return
    stmt = select(ActualReturn.trade_date).where(ActualReturn.fund_code == fund_code).order_by(ActualReturn.trade_date.asc())
    all_dates = session.scalars(stmt).all()

    # 找到已有的 residual
    stmt = select(CalibrationResidual.trade_date).where(
        CalibrationResidual.fund_code == fund_code,
        CalibrationResidual.holding_version_id == holding_version.id
    )
    existing_dates = set(session.scalars(stmt).all())

    calibrated_count = 0
    for d in all_dates:
        if d >= holding_version.report_date and d not in existing_dates:
            run_online_calibration(session, fund_code, calibration_date=d, force=False)
            calibrated_count += 1
            session.commit()
    return calibrated_count


def force_replay_calibration(session: Session, fund_code: str) -> int:
    """清理当前 holding_version 下的全部 residual，重置状态并从头 replay 一遍。"""
    from .models import ActualReturn, CalibrationResidual, HoldingVersion, OnlineCalibrationState
    from sqlalchemy import delete

    # 找到 active holding_version
    stmt = select(HoldingVersion).where(HoldingVersion.fund_code == fund_code, HoldingVersion.is_active.is_(True))
    holding_version = session.scalar(stmt)
    if not holding_version:
        return 0

    # 1. 清理
    session.execute(delete(CalibrationResidual).where(
        CalibrationResidual.fund_code == fund_code,
        CalibrationResidual.holding_version_id == holding_version.id
    ))
    session.execute(delete(OnlineCalibrationState).where(
        OnlineCalibrationState.fund_code == fund_code,
        OnlineCalibrationState.holding_version_id == holding_version.id
    ))
    session.commit()

    # 2. 找到所有 actual_return
    stmt = select(ActualReturn.trade_date).where(ActualReturn.fund_code == fund_code).order_by(ActualReturn.trade_date.asc())
    all_dates = session.scalars(stmt).all()

    # 3. 按 trade_date 升序逐日走 walk-forward 流程
    calibrated_count = 0
    for d in all_dates:
        if d >= holding_version.report_date:
            run_online_calibration(session, fund_code, calibration_date=d, force=True)
            calibrated_count += 1
            session.commit()
    return calibrated_count
