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
