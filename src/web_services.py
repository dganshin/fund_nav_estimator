from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .import_data import (
    import_asset_allocations_from_rows,
    import_funds_from_rows,
    import_holdings_from_rows,
    import_industry_allocations_from_rows,
)
from .models import Fund, FundAssetAllocation, FundIndustryAllocation, HoldingVersion, UserFundPosition


def list_fund_options(session: Session) -> list[tuple[str, str]]:
    funds = session.scalars(select(Fund).order_by(Fund.fund_code.asc())).all()
    return [(fund.fund_code, fund.fund_name) for fund in funds]


def load_fund_rows(session: Session) -> list[dict[str, object]]:
    funds = session.scalars(select(Fund).order_by(Fund.fund_code.asc())).all()
    return [
        {
            "fund_code": fund.fund_code,
            "fund_name": fund.fund_name,
            "fund_type": fund.fund_type,
            "market": fund.market,
            "is_active": fund.is_active,
        }
        for fund in funds
    ]


def load_holding_rows(session: Session, fund_code: str | None = None) -> list[dict[str, object]]:
    stmt = select(HoldingVersion).where(HoldingVersion.is_active.is_(True)).order_by(
        HoldingVersion.fund_code.asc(),
        HoldingVersion.report_date.desc(),
    )
    if fund_code:
        stmt = stmt.where(HoldingVersion.fund_code == fund_code)

    rows: list[dict[str, object]] = []
    for version in session.scalars(stmt).all():
        for item in version.items:
            rows.append(
                {
                    "fund_code": version.fund_code,
                    "report_date": version.report_date.isoformat(),
                    "source": version.source,
                    "asset_code": item.asset_code,
                    "asset_name": item.asset_name,
                    "asset_type": item.asset_type,
                    "weight_pct": round(item.weight * 100, 4),
                }
            )
    return rows


def load_asset_allocation_rows(session: Session, fund_code: str | None = None) -> list[dict[str, object]]:
    stmt = select(FundAssetAllocation).where(FundAssetAllocation.is_active.is_(True)).order_by(
        FundAssetAllocation.fund_code.asc(),
        FundAssetAllocation.report_date.desc(),
    )
    if fund_code:
        stmt = stmt.where(FundAssetAllocation.fund_code == fund_code)

    return [
        {
            "fund_code": row.fund_code,
            "report_date": row.report_date.isoformat(),
            "source": row.source,
            "stock_weight_pct": round(row.stock_weight * 100, 4),
            "bond_weight_pct": round(row.bond_weight * 100, 4),
            "cash_weight_pct": round(row.cash_weight * 100, 4),
            "other_weight_pct": round(row.other_weight * 100, 4),
        }
        for row in session.scalars(stmt).all()
    ]


def load_industry_allocation_rows(session: Session, fund_code: str | None = None) -> list[dict[str, object]]:
    stmt = select(FundIndustryAllocation).where(FundIndustryAllocation.is_active.is_(True)).order_by(
        FundIndustryAllocation.fund_code.asc(),
        FundIndustryAllocation.report_date.desc(),
        FundIndustryAllocation.weight.desc(),
    )
    if fund_code:
        stmt = stmt.where(FundIndustryAllocation.fund_code == fund_code)

    return [
        {
            "fund_code": row.fund_code,
            "report_date": row.report_date.isoformat(),
            "source": row.source,
            "industry_name": row.industry_name,
            "industry_code": row.industry_code or "",
            "weight_pct": round(row.weight * 100, 4),
        }
        for row in session.scalars(stmt).all()
    ]


def load_user_position_rows(session: Session) -> list[dict[str, object]]:
    rows = session.scalars(select(UserFundPosition).order_by(UserFundPosition.fund_code.asc())).all()
    return [
        {
            "fund_code": row.fund_code,
            "holding_amount": row.holding_amount,
            "holding_share": row.holding_share,
            "cost_nav": row.cost_nav,
            "platform": row.platform or "",
            "is_active": row.is_active,
        }
        for row in rows
    ]


def save_fund_rows(session: Session, rows: list[dict[str, object]]) -> int:
    return import_funds_from_rows(session, rows)


def save_holding_rows(session: Session, rows: list[dict[str, object]]) -> int:
    return import_holdings_from_rows(session, rows)


def save_asset_allocation_rows(session: Session, rows: list[dict[str, object]]) -> int:
    return import_asset_allocations_from_rows(session, rows)


def save_industry_allocation_rows(session: Session, rows: list[dict[str, object]]) -> int:
    return import_industry_allocations_from_rows(session, rows)


def save_user_position_rows(session: Session, rows: list[dict[str, object]]) -> int:
    count = 0
    for row in rows:
        fund_code = str(row.get("fund_code") or "").strip()
        if not fund_code:
            continue
        position = session.scalar(select(UserFundPosition).where(UserFundPosition.fund_code == fund_code))
        if position is None:
            position = UserFundPosition(fund_code=fund_code)
            session.add(position)
        position.holding_amount = None if row.get("holding_amount") in {"", None} else float(row["holding_amount"])
        position.holding_share = None if row.get("holding_share") in {"", None} else float(row["holding_share"])
        position.cost_nav = None if row.get("cost_nav") in {"", None} else float(row["cost_nav"])
        position.platform = None if row.get("platform") in {"", None} else str(row["platform"]).strip()
        position.is_active = bool(row.get("is_active", True))
        count += 1
    session.commit()
    return count


def default_date_range() -> tuple[date, date]:
    today = date.today()
    return today.replace(day=1), today
