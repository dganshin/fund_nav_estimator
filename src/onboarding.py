from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .backfill import fetch_and_store_fund_navs, fetch_and_store_stock_quotes
from .calibration import run_online_calibration
from .estimator import build_effective_weight_version
from .import_data import import_asset_allocations_from_rows, import_funds_from_rows, import_holdings_from_rows
from .models import Fund, FundNav, HoldingVersion, UserFundPosition
from .web_services import save_user_position_rows, save_watchlist_rows

logger = logging.getLogger(__name__)

DEFAULT_PLATFORM = "支付宝/蚂蚁财富"


def _to_pct(value: object) -> float | None:
    if value in {None, ""}:
        return None
    try:
        raw = float(str(value).replace("%", "").strip())
    except ValueError:
        return None
    return raw if abs(raw) > 1 else raw * 100


def _pick(row: dict[str, object], *names: str) -> object | None:
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name in row:
            return row[name]
        val = lowered.get(name.lower())
        if val is not None:
            return val
    return None


def _parse_report_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
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


def _normalize_holding_rows(fund_code: str, raw_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    fallback_date = date.today().replace(month=((date.today().month - 1) // 3) * 3 + 1, day=1)
    for raw in raw_rows:
        report_date = _parse_report_date(
            _pick(raw, "report_date", "报告日期", "季度", "公告日期", "持仓日期")
        ) or fallback_date
        asset_code = str(_pick(raw, "asset_code", "股票代码", "代码", "证券代码") or "").strip()
        asset_name = str(_pick(raw, "asset_name", "股票名称", "名称", "证券名称") or asset_code).strip()
        weight_pct = _to_pct(_pick(raw, "weight_pct", "占净值比例", "持仓占比", "比例", "市值占净值比"))
        if not asset_code or weight_pct is None:
            continue
        rows.append({
            "fund_code": fund_code,
            "report_date": report_date.isoformat(),
            "source": str(_pick(raw, "source") or "akshare:public_holdings"),
            "asset_code": asset_code,
            "asset_name": asset_name,
            "asset_type": str(_pick(raw, "asset_type") or "stock"),
            "weight_pct": weight_pct,
        })
    return rows


def _normalize_asset_allocation_rows(
    fund_code: str,
    raw_rows: list[dict[str, object]],
    report_date: date,
) -> list[dict[str, object]]:
    if not raw_rows:
        return []
    first = raw_rows[0]
    stock = _to_pct(_pick(first, "stock_weight_pct", "股票占净比", "股票仓位", "股票投资占比", "股票"))
    bond = _to_pct(_pick(first, "bond_weight_pct", "债券占净比", "债券"))
    cash = _to_pct(_pick(first, "cash_weight_pct", "现金占净比", "现金"))
    other = _to_pct(_pick(first, "other_weight_pct", "其他"))
    if stock is None:
        # AKShare 若只返回行业配置, 无法得出股票仓位, 留给 effective_weight 退化处理。
        return []
    return [{
        "fund_code": fund_code,
        "report_date": report_date.isoformat(),
        "source": str(_pick(first, "source") or "akshare:asset_allocation"),
        "stock_weight_pct": stock,
        "bond_weight_pct": bond or 0,
        "cash_weight_pct": cash or 0,
        "other_weight_pct": other or max(0.0, 100.0 - stock - (bond or 0) - (cash or 0)),
    }]


def _latest_nav(session: Session, fund_code: str) -> FundNav | None:
    return session.scalar(
        select(FundNav)
        .where(FundNav.fund_code == fund_code)
        .order_by(FundNav.trade_date.desc())
    )


def ensure_fund_full_onboarded(
    session: Session,
    fund_code: str,
    data_source,
    holding_amount: Optional[float] = None,
    add_watchlist: bool = True,
) -> dict[str, object]:
    """基金代码一键收录: 基础信息, 公开持仓, 资产配置, 回填, 修正权重, 在线校准。"""
    fund_code = str(fund_code).strip()
    today = date.today()
    status = "ready"
    warnings: list[str] = []

    profile = data_source.fetch_fund_profile(fund_code)
    import_funds_from_rows(session, [{
        "fund_code": fund_code,
        "fund_name": profile.fund_name or fund_code,
        "fund_type": profile.fund_type or "equity",
        "market": profile.market or "CN",
        "is_active": True,
    }])

    holdings_rows: list[dict[str, object]] = []
    try:
        if hasattr(data_source, "fetch_fund_public_holdings"):
            raw_holdings = data_source.fetch_fund_public_holdings(fund_code)
        else:
            raw_holdings = data_source.fetch_fund_holdings(fund_code, year=today.year)
        if not isinstance(raw_holdings, list):
            raw_holdings = []
        holdings_rows = _normalize_holding_rows(fund_code, raw_holdings or [])
        if holdings_rows:
            import_holdings_from_rows(session, holdings_rows)
        else:
            status = "missing_holdings"
            warnings.append("缺持仓, 需手动补充")
    except Exception as exc:
        status = "missing_holdings"
        warnings.append(f"公开持仓拉取失败: {exc}")

    active_holding = session.scalar(
        select(HoldingVersion)
        .where(HoldingVersion.fund_code == fund_code, HoldingVersion.is_active.is_(True))
        .order_by(HoldingVersion.report_date.desc())
    )
    report_date = active_holding.report_date if active_holding else today - timedelta(days=60)

    try:
        raw_alloc = data_source.fetch_fund_asset_allocation(fund_code, report_date=report_date)
        if not isinstance(raw_alloc, list):
            raw_alloc = []
        alloc_rows = _normalize_asset_allocation_rows(fund_code, raw_alloc or [], report_date)
        if alloc_rows:
            import_asset_allocations_from_rows(session, alloc_rows)
        else:
            warnings.append("缺股票仓位, 未做覆盖率放大")
    except Exception as exc:
        warnings.append(f"资产配置拉取失败: {exc}")

    start_date = max(report_date, today - timedelta(days=60))
    try:
        fetch_and_store_fund_navs(session, data_source, fund_code, start_date, today)
    except Exception as exc:
        warnings.append(f"基金净值回填失败: {exc}")

    asset_codes = [item.asset_code for item in active_holding.items] if active_holding else []
    if asset_codes:
        try:
            fetch_and_store_stock_quotes(session, data_source, start_date, today, asset_codes)
        except Exception as exc:
            warnings.append(f"股票日K回填失败: {exc}")
        try:
            build_effective_weight_version(session, fund_code, today)
            session.commit()
        except Exception as exc:
            warnings.append(f"修正权重生成失败: {exc}")
        try:
            run_online_calibration(session, fund_code)
        except Exception as exc:
            warnings.append(f"在线校准失败: {exc}")

    if add_watchlist:
        save_watchlist_rows(session, [{"fund_code": fund_code, "is_active": True}])

    if holding_amount is not None:
        latest_nav = _latest_nav(session, fund_code)
        nav = latest_nav.unit_nav if latest_nav is not None else profile.latest_unit_nav
        save_user_position_rows(session, [{
            "fund_code": fund_code,
            "holding_amount": holding_amount,
            "holding_share": (holding_amount / nav) if nav and nav > 0 else None,
            "platform": DEFAULT_PLATFORM,
            "is_active": True,
        }])

    fund = session.get(Fund, fund_code)
    if status == "ready" and warnings and any("失败" in w for w in warnings):
        status = "onboarding"
    return {
        "fund_code": fund_code,
        "fund_name": fund.fund_name if fund else fund_code,
        "latest_unit_nav": profile.latest_unit_nav,
        "latest_nav_date": profile.latest_nav_date.isoformat() if profile.latest_nav_date else None,
        "status": status,
        "warnings": warnings,
    }
