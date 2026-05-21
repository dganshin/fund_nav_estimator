from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ActualReturn, DailyQuote, EstimateError, Fund, FundEstimate, HoldingVersion


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


def build_fund_estimates(session: Session, trade_date: date) -> int:
    funds = session.scalars(select(Fund).where(Fund.is_active.is_(True))).all()
    created_or_updated = 0

    for fund in funds:
        version = select_holding_version(session, fund.fund_code, trade_date)
        if version is None:
            continue

        raw_estimate = 0.0
        covered_weight = 0.0

        for item in version.items:
            quote = session.get(DailyQuote, {"trade_date": trade_date, "asset_code": item.asset_code})
            if quote is None:
                continue

            covered_weight += item.weight
            raw_estimate += item.weight / 100.0 * quote.return_pct

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
        estimate.raw_estimate = round(raw_estimate, 6)
        estimate.covered_weight = round(covered_weight, 4)
        estimate.missing_weight = round(missing_weight, 4)
        created_or_updated += 1

    session.commit()
    return created_or_updated


def build_estimate_errors(session: Session, trade_date: date) -> int:
    estimates = session.scalars(
        select(FundEstimate).where(FundEstimate.trade_date == trade_date)
    ).all()
    created_or_updated = 0

    for estimate in estimates:
        actual = session.get(
            ActualReturn,
            {"trade_date": trade_date, "fund_code": estimate.fund_code},
        )
        if actual is None:
            continue

        error_value = round(actual.actual_return - estimate.raw_estimate, 6)
        direction_hit = (
            (estimate.raw_estimate == 0 and actual.actual_return == 0)
            or (estimate.raw_estimate > 0 and actual.actual_return > 0)
            or (estimate.raw_estimate < 0 and actual.actual_return < 0)
        )
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
        created_or_updated += 1

    session.commit()
    return created_or_updated

