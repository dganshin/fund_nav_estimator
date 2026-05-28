from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, PrimaryKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Fund(Base):
    __tablename__ = "funds"

    fund_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    fund_name: Mapped[str] = mapped_column(String(128), nullable=False)
    fund_type: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    holding_versions: Mapped[list["HoldingVersion"]] = relationship(back_populates="fund")
    estimates: Mapped[list["FundEstimate"]] = relationship(back_populates="fund")
    actual_returns: Mapped[list["ActualReturn"]] = relationship(back_populates="fund")
    navs: Mapped[list["FundNav"]] = relationship(back_populates="fund")
    asset_allocations: Mapped[list["FundAssetAllocation"]] = relationship(back_populates="fund")
    industry_allocations: Mapped[list["FundIndustryAllocation"]] = relationship(back_populates="fund")
    effective_weight_versions: Mapped[list["EffectiveWeightVersion"]] = relationship(back_populates="fund")
    user_positions: Mapped[list["UserFundPosition"]] = relationship(back_populates="fund")
    user_watchlist_entries: Mapped[list["UserWatchlistFund"]] = relationship(back_populates="fund")
    online_calibration_states: Mapped[list["OnlineCalibrationState"]] = relationship(back_populates="fund")
    calibrated_estimates: Mapped[list["CalibratedEstimate"]] = relationship(back_populates="fund")
    selected_estimates: Mapped[list["SelectedEstimate"]] = relationship(back_populates="fund")
    calibration_residuals: Mapped[list["CalibrationResidual"]] = relationship(back_populates="fund")


class FundAlias(Base):
    __tablename__ = "fund_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alias_name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    normalized_alias: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class HoldingVersion(Base):
    __tablename__ = "holding_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False, index=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    total_weight: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("fund_code", "report_date", "source", name="uq_holding_version"),
    )

    fund: Mapped["Fund"] = relationship(back_populates="holding_versions")
    items: Mapped[list["HoldingItem"]] = relationship(
        back_populates="holding_version",
        cascade="all, delete-orphan",
    )
    estimates: Mapped[list["FundEstimate"]] = relationship(back_populates="holding_version")


class HoldingItem(Base):
    __tablename__ = "holding_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    holding_version_id: Mapped[int] = mapped_column(ForeignKey("holding_versions.id"), nullable=False, index=True)
    asset_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    asset_name: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("holding_version_id", "asset_code", name="uq_holding_item"),
    )

    holding_version: Mapped["HoldingVersion"] = relationship(back_populates="items")


class DailyQuote(Base):
    __tablename__ = "daily_quotes"

    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    asset_code: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_name: Mapped[str] = mapped_column(String(128), nullable=False)
    return_pct: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("trade_date", "asset_code", name="pk_daily_quotes"),
    )


class FundEstimate(Base):
    __tablename__ = "fund_estimates"

    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False)
    holding_version_id: Mapped[int] = mapped_column(ForeignKey("holding_versions.id"), nullable=False)
    raw_estimate: Mapped[float] = mapped_column(Float, nullable=False)
    covered_weight: Mapped[float] = mapped_column(Float, nullable=False)
    missing_weight: Mapped[float] = mapped_column(Float, nullable=False)
    missing_assets_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("trade_date", "fund_code", name="pk_fund_estimates"),
    )

    fund: Mapped["Fund"] = relationship(back_populates="estimates")
    holding_version: Mapped["HoldingVersion"] = relationship(back_populates="estimates")


class ActualReturn(Base):
    __tablename__ = "actual_returns"

    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False)
    actual_return: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("trade_date", "fund_code", name="pk_actual_returns"),
    )

    fund: Mapped["Fund"] = relationship(back_populates="actual_returns")


class FundNav(Base):
    __tablename__ = "fund_navs"

    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False)
    unit_nav: Mapped[float] = mapped_column(Float, nullable=False)
    accumulated_nav: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("trade_date", "fund_code", name="pk_fund_navs"),
    )

    fund: Mapped["Fund"] = relationship(back_populates="navs")


class FundAssetAllocation(Base):
    __tablename__ = "fund_asset_allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False, index=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    stock_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    bond_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cash_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    other_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("fund_code", "report_date", "source", name="uq_fund_asset_allocation"),
    )

    fund: Mapped["Fund"] = relationship(back_populates="asset_allocations")


class FundIndustryAllocation(Base):
    __tablename__ = "fund_industry_allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False, index=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    industry_name: Mapped[str] = mapped_column(String(128), nullable=False)
    industry_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint(
            "fund_code",
            "report_date",
            "source",
            "industry_name",
            "industry_code",
            name="uq_fund_industry_allocation",
        ),
    )

    fund: Mapped["Fund"] = relationship(back_populates="industry_allocations")


class EffectiveWeightVersion(Base):
    __tablename__ = "effective_weight_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False, index=True)
    holding_version_id: Mapped[int] = mapped_column(ForeignKey("holding_versions.id"), nullable=False, index=True)
    asset_allocation_id: Mapped[int | None] = mapped_column(ForeignKey("fund_asset_allocations.id"), nullable=True, index=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    covered_weight: Mapped[float] = mapped_column(Float, nullable=False)
    stock_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    scale_factor: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    total_effective_weight: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("fund_code", "holding_version_id", "source", name="uq_effective_weight_version"),
    )

    fund: Mapped["Fund"] = relationship(back_populates="effective_weight_versions")
    items: Mapped[list["EffectiveWeightItem"]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
    )


class EffectiveWeightItem(Base):
    __tablename__ = "effective_weight_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    effective_weight_version_id: Mapped[int] = mapped_column(ForeignKey("effective_weight_versions.id"), nullable=False, index=True)
    asset_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    asset_name: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    published_weight: Mapped[float] = mapped_column(Float, nullable=False)
    effective_weight: Mapped[float] = mapped_column(Float, nullable=False)
    adjustment_factor: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    contribution_explain: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("effective_weight_version_id", "asset_code", name="uq_effective_weight_item"),
    )

    version: Mapped["EffectiveWeightVersion"] = relationship(back_populates="items")


class UserFundPosition(Base):
    __tablename__ = "user_fund_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False, index=True)
    holding_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    holding_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_nav: Mapped[float | None] = mapped_column(Float, nullable=True)
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("fund_code", name="uq_user_fund_position"),
    )

    fund: Mapped["Fund"] = relationship(back_populates="user_positions")


class UserWatchlistFund(Base):
    __tablename__ = "user_watchlist_funds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("fund_code", name="uq_user_watchlist_fund"),
    )

    fund: Mapped["Fund"] = relationship(back_populates="user_watchlist_entries")


class OnlineCalibrationState(Base):
    __tablename__ = "online_calibration_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False, index=True)
    holding_version_id: Mapped[int] = mapped_column(ForeignKey("holding_versions.id"), nullable=False, index=True)
    base_scale_factor: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    current_scale_factor: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    min_scale_factor: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)
    max_scale_factor: Mapped[float] = mapped_column(Float, nullable=False, default=1.2)
    ewma_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    recent_mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    beta_known: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    beta_unknown: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    alpha: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    selected_model: Mapped[str] = mapped_column(String(32), nullable=False, default="coverage_adjusted")
    model_weight_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_update_trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    confidence_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    warning_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("fund_code", "holding_version_id", name="uq_online_calibration_state"),
    )

    fund: Mapped["Fund"] = relationship(back_populates="online_calibration_states")


class CalibrationResidual(Base):
    """逐日因果校准残差记录。每天只使用 T-1 及以前的 scale 计算 effective_estimate。"""
    __tablename__ = "calibration_residuals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False, index=True)
    holding_version_id: Mapped[int] = mapped_column(ForeignKey("holding_versions.id"), nullable=False, index=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    actual_return: Mapped[float] = mapped_column(Float, nullable=False)
    known_estimate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unknown_estimate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    base_estimate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    coverage_adjusted_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    single_scale_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    two_factor_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_estimate: Mapped[float] = mapped_column(Float, nullable=False)
    calibrated_estimate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    effective_estimate: Mapped[float] = mapped_column(Float, nullable=False)
    residual: Mapped[float] = mapped_column(Float, nullable=False)
    abs_residual: Mapped[float] = mapped_column(Float, nullable=False)
    scale_used_before_update: Mapped[float] = mapped_column(Float, nullable=False)
    beta_known: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    beta_unknown: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    alpha: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    params_fitted_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False, default="two_factor")
    calibration_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="online_scale")
    is_out_of_sample: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    observed_scale: Mapped[float | None] = mapped_column(Float, nullable=True)
    scale_after_update: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_used_for_update: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    skip_reason: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("fund_code", "holding_version_id", "trade_date", name="uq_calibration_residual"),
    )

    fund: Mapped["Fund"] = relationship(back_populates="calibration_residuals")


class EstimateError(Base):
    __tablename__ = "estimate_errors"

    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False)
    raw_estimate: Mapped[float] = mapped_column(Float, nullable=False)
    actual_return: Mapped[float] = mapped_column(Float, nullable=False)
    error: Mapped[float] = mapped_column(Float, nullable=False)
    abs_error: Mapped[float] = mapped_column(Float, nullable=False)
    direction_hit: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("trade_date", "fund_code", name="pk_estimate_errors"),
    )


class CalibratedEstimate(Base):
    __tablename__ = "calibrated_estimates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False, index=True)
    holding_version_id: Mapped[int] = mapped_column(ForeignKey("holding_versions.id"), nullable=False)
    base_estimate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_estimate: Mapped[float] = mapped_column(Float, nullable=False)
    coverage_adjusted_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibrated_estimate: Mapped[float] = mapped_column(Float, nullable=False)
    alpha: Mapped[float] = mapped_column(Float, nullable=False)
    beta: Mapped[float] = mapped_column(Float, nullable=False)
    window: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    train_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    train_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    mean_abs_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    direction_hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimate_actual_corr: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_status: Mapped[str] = mapped_column(String(64), nullable=False)
    warning_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "trade_date",
            "fund_code",
            "holding_version_id",
            "base_estimate_type",
            "window",
            name="uq_calibrated_estimate",
        ),
    )

    fund: Mapped["Fund"] = relationship(back_populates="calibrated_estimates")


class SelectedEstimate(Base):
    __tablename__ = "selected_estimates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False, index=True)
    holding_version_id: Mapped[int] = mapped_column(ForeignKey("holding_versions.id"), nullable=False)
    raw_estimate: Mapped[float] = mapped_column(Float, nullable=False)
    coverage_adjusted_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibrated_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_estimate: Mapped[float] = mapped_column(Float, nullable=False)
    best_method: Mapped[str] = mapped_column(String(32), nullable=False)
    selection_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="default")
    selection_window: Mapped[int] = mapped_column(Integer, nullable=False)
    min_samples: Mapped[int] = mapped_column(Integer, nullable=False)
    min_improvement_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage_adjusted_mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibrated_mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_direction_hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage_direction_hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibrated_direction_hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_reason: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    best_status: Mapped[str] = mapped_column(String(64), nullable=False)
    warning_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "trade_date",
            "fund_code",
            "holding_version_id",
            "selection_window",
            "selection_policy",
            name="uq_selected_estimate",
        ),
    )

    fund: Mapped["Fund"] = relationship(back_populates="selected_estimates")


class TaskRun(Base):
    __tablename__ = "task_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    progress_text: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    fund: Mapped["Fund"] = relationship()


class UserFundPositionEvent(Base):
    __tablename__ = "user_fund_position_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(ForeignKey("funds.fund_code"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    share_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    nav: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image_path: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    note: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    fund: Mapped["Fund"] = relationship()
