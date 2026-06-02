from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .data_sources.code_utils import normalize_asset_code
from .models import (
    CalibrationResidual,
    EnhancedHoldingItem,
    EnhancedHoldingVersion,
    FundAssetAllocation,
    HoldingVersion,
)


ENHANCED_METHOD = "core_plus_reports"
ENHANCED_SELECTION_WINDOW = 20
ENHANCED_MIN_ERRORS = 5
ENHANCED_MIN_IMPROVEMENT = 0.0003


@dataclass
class NormalizedReportHolding:
    asset_code: str
    asset_name: str
    asset_type: str
    weight: float
    report_date: date
    report_type: str


def _pick(row: dict[str, object], *names: str) -> object | None:
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name in row:
            return row[name]
        value = lowered.get(name.lower())
        if value is not None:
            return value
    return None


def _to_decimal(value: object) -> float | None:
    if value in {None, ""}:
        return None
    text = str(value).replace("%", "").strip()
    try:
        raw = float(text)
    except ValueError:
        return None
    return raw / 100.0


def _parse_report_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"(\d{4})年\s*([1-4])季度", text)
    if match:
        year = int(match.group(1))
        quarter = int(match.group(2))
        return {1: date(year, 3, 31), 2: date(year, 6, 30), 3: date(year, 9, 30), 4: date(year, 12, 31)}[quarter]
    for sep in ("-", "/", "."):
        try:
            parts = [int(p) for p in text.replace("年", sep).replace("月", sep).replace("日", "").split(sep) if p]
            if len(parts) >= 3:
                return date(parts[0], parts[1], parts[2])
        except Exception:
            pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _report_type(report_date: date, latest_report_date: date) -> tuple[str, float, bool]:
    if report_date == latest_report_date:
        return "latest_quarter_top10", 1.0, False
    if report_date.month == 6 and report_date >= latest_report_date - timedelta(days=370):
        return "semi_annual_full", 0.7, False
    if report_date.month == 12 and report_date >= latest_report_date - timedelta(days=550):
        return "annual_full", 0.5, False
    return "older_report", 0.2, True


def _normalize_report_rows(raw_rows: list[dict[str, object]], latest_report_date: date) -> list[NormalizedReportHolding]:
    result: list[NormalizedReportHolding] = []
    fallback_date = latest_report_date
    for raw in raw_rows or []:
        report_date = _parse_report_date(
            _pick(raw, "report_date", "报告日期", "季度", "公告日期", "持仓日期")
        ) or fallback_date
        code = normalize_asset_code(str(_pick(raw, "asset_code", "股票代码", "代码", "证券代码") or "").strip())
        if not code:
            continue
        weight = _to_decimal(_pick(raw, "weight_pct", "占净值比例", "持仓占比", "比例", "市值占净值比"))
        if weight is None or weight <= 0:
            continue
        report_type, _, _ = _report_type(report_date, latest_report_date)
        result.append(
            NormalizedReportHolding(
                asset_code=code,
                asset_name=str(_pick(raw, "asset_name", "股票名称", "名称", "证券名称") or code).strip(),
                asset_type=str(_pick(raw, "asset_type") or "stock"),
                weight=weight,
                report_date=report_date,
                report_type=report_type,
            )
        )
    return result


def _select_stock_weight(session: Session, holding_version: HoldingVersion) -> float | None:
    allocation = session.scalar(
        select(FundAssetAllocation)
        .where(
            FundAssetAllocation.fund_code == holding_version.fund_code,
            FundAssetAllocation.report_date <= holding_version.report_date,
            FundAssetAllocation.is_active.is_(True),
        )
        .order_by(FundAssetAllocation.report_date.desc(), FundAssetAllocation.created_at.desc())
    )
    return None if allocation is None else allocation.stock_weight


def get_active_enhanced_holding_version(session: Session, fund_code: str) -> EnhancedHoldingVersion | None:
    return session.scalar(
        select(EnhancedHoldingVersion)
        .where(
            EnhancedHoldingVersion.fund_code == fund_code,
            EnhancedHoldingVersion.is_active.is_(True),
        )
        .order_by(EnhancedHoldingVersion.build_date.desc(), EnhancedHoldingVersion.created_at.desc())
    )


def build_enhanced_holding_version(
    session: Session,
    fund_code: str,
    data_source,
    years: list[int] | None = None,
    build_date: date | None = None,
) -> EnhancedHoldingVersion | None:
    """用最新前十大 + 旧完整报告构造增强持仓池。"""
    build_date = build_date or date.today()
    base = session.scalar(
        select(HoldingVersion)
        .where(HoldingVersion.fund_code == fund_code, HoldingVersion.is_active.is_(True))
        .order_by(HoldingVersion.report_date.desc(), HoldingVersion.created_at.desc())
    )
    if base is None or not base.items:
        return None
    years = years or [build_date.year, build_date.year - 1]
    if hasattr(data_source, "fetch_fund_holdings_all_reports"):
        raw_rows = data_source.fetch_fund_holdings_all_reports(fund_code, years)
    else:
        raw_rows = []
        for year in years:
            raw_rows.extend(data_source.fetch_fund_holdings(fund_code, year=year))

    normalized = _normalize_report_rows(raw_rows, base.report_date)
    latest_by_code: dict[str, NormalizedReportHolding] = {}
    for row in sorted(normalized, key=lambda item: item.report_date, reverse=True):
        latest_by_code.setdefault(row.asset_code, row)

    stock_weight = _select_stock_weight(session, base)
    target_weight = stock_weight if stock_weight and stock_weight > 0 else max(base.total_weight, sum(i.weight for i in base.items))

    core_codes = {item.asset_code for item in base.items}
    version_rows: list[dict[str, object]] = []
    for item in base.items:
        version_rows.append({
            "asset_code": item.asset_code,
            "asset_name": item.asset_name,
            "asset_type": item.asset_type,
            "weight": item.weight,
            "original_weight": item.weight,
            "source_report_type": "latest_quarter_top10",
            "source_report_date": base.report_date,
            "confidence_weight": 1.0,
            "is_core": True,
            "is_extended": False,
            "is_stale": False,
        })

    extended_rows: list[dict[str, object]] = []
    for code, row in latest_by_code.items():
        if code in core_codes:
            continue
        source_type, confidence, is_stale = _report_type(row.report_date, base.report_date)
        if is_stale:
            continue
        extended_rows.append({
            "asset_code": row.asset_code,
            "asset_name": row.asset_name,
            "asset_type": row.asset_type,
            "weight": row.weight * confidence,
            "original_weight": row.weight,
            "source_report_type": source_type,
            "source_report_date": row.report_date,
            "confidence_weight": confidence,
            "is_core": False,
            "is_extended": True,
            "is_stale": is_stale,
        })

    core_total = sum(float(row["weight"]) for row in version_rows)
    extended_total = sum(float(row["weight"]) for row in extended_rows)
    if target_weight > core_total and core_total + extended_total > target_weight and extended_total > 0:
        scale = (target_weight - core_total) / extended_total
        for row in extended_rows:
            row["weight"] = float(row["weight"]) * scale
    elif target_weight <= core_total:
        extended_rows = []

    version_rows.extend(sorted(extended_rows, key=lambda row: float(row["weight"]), reverse=True))
    total_weight = sum(float(row["weight"]) for row in version_rows)
    source_counts: dict[str, int] = {}
    for row in version_rows:
        source_counts[str(row["source_report_type"])] = source_counts.get(str(row["source_report_type"]), 0) + 1

    existing = session.scalar(
        select(EnhancedHoldingVersion).where(
            EnhancedHoldingVersion.fund_code == fund_code,
            EnhancedHoldingVersion.base_holding_version_id == base.id,
            EnhancedHoldingVersion.method == ENHANCED_METHOD,
        )
    )
    if existing is None:
        existing = EnhancedHoldingVersion(
            fund_code=fund_code,
            base_holding_version_id=base.id,
            build_date=build_date,
            method=ENHANCED_METHOD,
            total_weight=total_weight,
            stock_weight=stock_weight,
            source_summary=json.dumps(source_counts, ensure_ascii=False),
            is_active=True,
        )
        session.add(existing)
        session.flush()
    else:
        existing.build_date = build_date
        existing.total_weight = total_weight
        existing.stock_weight = stock_weight
        existing.source_summary = json.dumps(source_counts, ensure_ascii=False)
        existing.is_active = True
        existing.items.clear()
        session.flush()

    session.query(EnhancedHoldingVersion).where(
        EnhancedHoldingVersion.fund_code == fund_code,
        EnhancedHoldingVersion.id != existing.id,
    ).update({"is_active": False}, synchronize_session=False)

    for row in version_rows:
        existing.items.append(
            EnhancedHoldingItem(
                asset_code=str(row["asset_code"]),
                asset_name=str(row["asset_name"]),
                asset_type=str(row["asset_type"]),
                weight=float(row["weight"]),
                original_weight=float(row["original_weight"]),
                source_report_type=str(row["source_report_type"]),
                source_report_date=row["source_report_date"],
                confidence_weight=float(row["confidence_weight"]),
                is_core=bool(row["is_core"]),
                is_extended=bool(row["is_extended"]),
                is_stale=bool(row["is_stale"]),
            )
        )
    session.flush()
    return existing


def compute_enhanced_estimate(
    enhanced_version: EnhancedHoldingVersion | None,
    quotes: dict[str, float],
) -> tuple[float | None, float, float]:
    if enhanced_version is None:
        return None, 0.0, 0.0
    estimate = 0.0
    covered = 0.0
    for item in enhanced_version.items:
        ret = quotes.get(item.asset_code)
        if ret is None:
            continue
        covered += item.weight
        estimate += item.weight * ret
    missing = max(enhanced_version.total_weight - covered, 0.0)
    return (None if covered <= 0 else estimate), covered, missing


def _mae(values: list[float]) -> float | None:
    return None if not values else sum(values) / len(values)


def enhanced_validation_stats(
    rows: list[CalibrationResidual],
    window: int = ENHANCED_SELECTION_WINDOW,
) -> dict[str, object]:
    recent = rows[-window:]
    top10_errors: list[float] = []
    enhanced_errors: list[float] = []
    for row in recent:
        top10_estimate = row.coverage_adjusted_estimate if row.coverage_adjusted_estimate is not None else row.base_estimate
        if top10_estimate is not None:
            top10_errors.append(abs(row.actual_return - top10_estimate))
        enhanced_estimate = row.enhanced_holdings_estimate
        if enhanced_estimate is not None:
            enhanced_errors.append(abs(row.actual_return - enhanced_estimate))
    top10_mae = _mae(top10_errors)
    enhanced_mae = _mae(enhanced_errors)
    enabled = (
        len(enhanced_errors) >= ENHANCED_MIN_ERRORS
        and top10_mae is not None
        and enhanced_mae is not None
        and enhanced_mae + ENHANCED_MIN_IMPROVEMENT < top10_mae
    )
    return {
        "top10_mae": top10_mae,
        "enhanced_mae": enhanced_mae,
        "sample_count": len(enhanced_errors),
        "enabled": enabled,
    }


def should_use_enhanced_holdings(session: Session, fund_code: str, holding_version_id: int) -> dict[str, object]:
    rows = session.scalars(
        select(CalibrationResidual)
        .where(
            CalibrationResidual.fund_code == fund_code,
            CalibrationResidual.holding_version_id == holding_version_id,
            CalibrationResidual.is_used_for_update.is_(True),
        )
        .order_by(CalibrationResidual.trade_date.asc())
    ).all()
    return enhanced_validation_stats(list(rows))
