from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .backfill import fetch_and_store_fund_navs, fetch_and_store_stock_quotes
from .calibration import run_online_calibration
from .estimator import build_effective_weight_version
from .import_data import import_asset_allocations_from_rows, import_funds_from_rows, import_holdings_from_rows
from .models import ActualReturn, CalibrationResidual, Fund, FundNav, HoldingVersion, OnlineCalibrationState
from .data_sources.code_utils import normalize_asset_code
from .web_services import save_user_position_rows, save_watchlist_rows

logger = logging.getLogger(__name__)

DEFAULT_PLATFORM = "支付宝/蚂蚁财富"


def _to_pct(value: object) -> float | None:
    if value in {None, ""}:
        return None
    text = str(value).replace("%", "").strip()
    try:
        raw = float(text)
    except ValueError:
        return None
    return raw


def _pick(row: dict[str, object], *names: str) -> object | None:
    lowered = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name in row:
            return row[name]
        val = lowered.get(name.lower())
        if val is not None:
            return val
    return None


def _rows_to_map(rows) -> dict[str, str]:
    result: dict[str, str] = {}
    if rows is None:
        return result
    records = rows.to_dict(orient="records") if hasattr(rows, "to_dict") else rows
    for row in records or []:
        item = str(row.get("item") or row.get("项目") or "").strip()
        value = str(row.get("value") or row.get("值") or "").strip()
        if item:
            result[item] = value
    return result


def _fetch_xq_basic_info(data_source, fund_code: str) -> dict[str, str]:
    ak = getattr(data_source, "ak", None)
    if ak is None or not hasattr(ak, "fund_individual_basic_info_xq"):
        return {}
    try:
        return _rows_to_map(ak.fund_individual_basic_info_xq(symbol=fund_code))
    except Exception:
        return {}


def _is_etf_feeder(fund_name: str, fund_type: str, basic_info: dict[str, str]) -> bool:
    text = " ".join([fund_name, fund_type, basic_info.get("基金全称", ""), basic_info.get("投资策略", "")])
    return "ETF" in text.upper() and "联接" in text


def _parse_target_weight_pct(basic_info: dict[str, str]) -> float:
    benchmark = basic_info.get("业绩比较基准", "")
    match = re.search(r"[×xX*]\s*(\d+(?:\.\d+)?)%", benchmark)
    if match:
        return float(match.group(1))
    strategy = basic_info.get("投资策略", "")
    match = re.search(r"不低于.*?(\d+(?:\.\d+)?)%", strategy)
    if match:
        return float(match.group(1))
    return 95.0


def _find_target_etf(data_source, fund_name: str, basic_info: dict[str, str]) -> dict[str, object] | None:
    ak = getattr(data_source, "ak", None)
    if ak is None:
        return None
    full_name = fund_name if "ETF" in fund_name.upper() else (basic_info.get("基金全称") or fund_name)
    manager = (basic_info.get("基金公司") or "").replace("基金管理有限公司", "").replace("基金", "")
    if not manager:
        for prefix in ("华夏", "华安", "博时", "易方达", "国泰", "工银", "前海开源", "广发", "天弘", "南方", "嘉实"):
            if fund_name.startswith(prefix):
                manager = prefix
                break
    theme_candidates: list[str] = []
    for pattern in (
        r"中证(.+?)(?:ETF|交易型开放式|指数证券投资基金)",
        r"(.+?)ETF(?:发起式)?联接",
        r"(.+?)ETF联接",
    ):
        match = re.search(pattern, full_name)
        if match:
            theme_candidates.append(match.group(1))
    if not theme_candidates:
        return None
    try:
        df = ak.fund_etf_spot_em()
    except Exception:
        try:
            df = ak.fund_etf_category_sina(symbol="ETF基金")
        except Exception:
            return None
    if df is None or df.empty:
        return None
    name_col = "名称" if "名称" in df.columns else "基金名称"
    code_col = "代码" if "代码" in df.columns else "基金代码"
    theme_keywords: list[str] = []
    for theme in theme_candidates:
        cleaned = theme
        for noise in ("主题", "指数", "行业", "材料"):
            cleaned = cleaned.replace(noise, "")
        theme_keywords.extend([theme, cleaned])
        if len(cleaned) >= 4:
            theme_keywords.append(cleaned[-4:])
        if len(cleaned) >= 3:
            theme_keywords.append(cleaned[-3:])
    if any("有色金属" in theme for theme in theme_candidates):
        theme_keywords.append("有色金属")
        theme_keywords.append("工业有色")
    if any("黄金" in theme for theme in theme_candidates):
        theme_keywords.append("黄金ETF")
        theme_keywords.append("黄金")
    theme_keywords = [kw for kw in dict.fromkeys(theme_keywords) if kw]
    candidates = []
    for _, row in df.iterrows():
        name = str(row.get(name_col, "") or "")
        code = str(row.get(code_col, "") or "").replace("sz", "").replace("sh", "").strip()
        if "联接" in name or "ETF" not in name.upper():
            continue
        theme_hit = any(keyword and keyword in name for keyword in theme_keywords)
        manager_hit = bool(manager and manager in name)
        if theme_hit:
            exact_penalty = 0
            if any("黄金" in theme for theme in theme_candidates):
                # 黄金ETF联接应优先锚定实物黄金ETF, 避免误选黄金股ETF。
                exact_penalty = 0 if "黄金ETF" in name and "黄金股" not in name else 1
            candidates.append((exact_penalty, 0 if manager_hit else 1, -sum(1 for kw in theme_keywords if kw in name), code, name))
    if not candidates:
        return None
    *_, code, name = sorted(candidates)[0]
    return {
        "asset_code": normalize_asset_code(code),
        "asset_name": name,
        "weight_pct": _parse_target_weight_pct(basic_info),
    }


def _parse_report_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if "季度" in text:
        import re

        match = re.search(r"(\d{4})年\s*([1-4])季度", text)
        if match:
            year = int(match.group(1))
            quarter = int(match.group(2))
            return {
                1: date(year, 3, 31),
                2: date(year, 6, 30),
                3: date(year, 9, 30),
                4: date(year, 12, 31),
            }[quarter]
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
    fallback_date = date.today() - timedelta(days=60)
    for raw in raw_rows:
        report_date = _parse_report_date(
            _pick(raw, "report_date", "报告日期", "季度", "公告日期", "持仓日期")
        ) or fallback_date
        asset_code = normalize_asset_code(str(_pick(raw, "asset_code", "股票代码", "代码", "证券代码") or "").strip())
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
    if stock is None and any("行业类别" in row or "占净值比例" in row for row in raw_rows):
        # AKShare 的行业配置可视为股票资产在各行业的净值占比之和。
        weights = [_to_pct(_pick(row, "占净值比例", "weight_pct")) for row in raw_rows]
        stock = sum(weight for weight in weights if weight is not None)
    if stock is None:
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


def _run_causal_calibration_history(
    session: Session,
    fund_code: str,
    holding_version: HoldingVersion,
    force_rebuild: bool = False,
) -> int:
    """从当前公开持仓报告日起逐日写入因果残差。"""
    if force_rebuild:
        session.execute(delete(CalibrationResidual).where(
            CalibrationResidual.fund_code == fund_code,
            CalibrationResidual.holding_version_id == holding_version.id,
        ))
        session.execute(delete(OnlineCalibrationState).where(
            OnlineCalibrationState.fund_code == fund_code,
            OnlineCalibrationState.holding_version_id == holding_version.id,
        ))
        session.commit()

    dates = session.scalars(
        select(ActualReturn.trade_date)
        .where(
            ActualReturn.fund_code == fund_code,
            ActualReturn.trade_date >= holding_version.report_date,
        )
        .order_by(ActualReturn.trade_date.asc())
    ).all()
    count = 0
    for trade_date in dates:
        result = run_online_calibration(session, fund_code, calibration_date=trade_date, force=force_rebuild)
        if result is not None:
            count += 1
    return count


def ensure_fund_full_onboarded(
    session: Session,
    fund_code: str,
    data_source,
    holding_amount: Optional[float] = None,
    add_watchlist: bool = True,
    force_rebuild: bool = False,
) -> dict[str, object]:
    """基金代码一键收录: 基础信息, 公开持仓, 资产配置, 回填, 修正权重, 在线校准。"""
    fund_code = str(fund_code).strip()
    today = date.today()
    status = "ready"
    warnings: list[str] = []
    print(f"[onboard] fund_code={fund_code} start")

    profile = data_source.fetch_fund_profile(fund_code)
    basic_info = _fetch_xq_basic_info(data_source, fund_code)
    fund_name = basic_info.get("基金名称") or profile.fund_name or fund_code
    fund_type = basic_info.get("基金类型") or profile.fund_type or "equity"
    is_etf_feeder = _is_etf_feeder(fund_name, fund_type, basic_info)
    print(f"[onboard] profile fetched: name={fund_name}, type={fund_type}")
    import_funds_from_rows(session, [{
        "fund_code": fund_code,
        "fund_name": fund_name,
        "fund_type": "etf_feeder" if is_etf_feeder else fund_type,
        "market": profile.market or "CN",
        "is_active": True,
    }])

    holdings_rows: list[dict[str, object]] = []
    try:
        target_etf = _find_target_etf(data_source, fund_name, basic_info) if is_etf_feeder else None
        if target_etf is not None:
            holdings_rows = [{
                "fund_code": fund_code,
                "report_date": date(today.year, 3, 31).isoformat(),
                "source": "akshare:target_etf",
                "asset_code": target_etf["asset_code"],
                "asset_name": target_etf["asset_name"],
                "asset_type": "etf",
                "weight_pct": target_etf["weight_pct"],
            }]
            print(
                f"[onboard] etf feeder target: code={target_etf['asset_code']}, "
                f"name={target_etf['asset_name']}, weight={target_etf['weight_pct']}"
            )
        elif is_etf_feeder:
            warnings.append("ETF联接基金未识别目标ETF, 不使用公开股票明细估值")
            holdings_rows = []
        else:
            if hasattr(data_source, "fetch_fund_public_holdings"):
                raw_holdings = data_source.fetch_fund_public_holdings(fund_code)
            else:
                raw_holdings = data_source.fetch_fund_holdings(fund_code, year=today.year)
            if not isinstance(raw_holdings, list):
                raw_holdings = []
            holdings_rows = _normalize_holding_rows(fund_code, raw_holdings or [])
        print(f"[onboard] holdings fetched: count={len(holdings_rows)}")
        if holdings_rows:
            import_holdings_from_rows(session, holdings_rows)
        else:
            status = "missing_holdings"
            warnings.append("缺公开持仓")
    except Exception as exc:
        status = "missing_holdings"
        warnings.append(f"公开持仓拉取失败: {exc}")
        print(f"[onboard] holdings fetched: count=0, error={exc}")

    active_holding = session.scalar(
        select(HoldingVersion)
        .where(HoldingVersion.fund_code == fund_code, HoldingVersion.is_active.is_(True))
        .order_by(HoldingVersion.report_date.desc())
    )
    report_date = active_holding.report_date if active_holding else today - timedelta(days=60)

    try:
        if is_etf_feeder and holdings_rows:
            target_weight = float(holdings_rows[0]["weight_pct"])
            alloc_rows = [{
                "fund_code": fund_code,
                "report_date": report_date.isoformat(),
                "source": "benchmark:target_etf",
                "stock_weight_pct": target_weight,
                "bond_weight_pct": 0,
                "cash_weight_pct": max(0.0, 100.0 - target_weight),
                "other_weight_pct": 0,
            }]
        else:
            raw_alloc = data_source.fetch_fund_asset_allocation(fund_code, report_date=report_date)
            if not isinstance(raw_alloc, list):
                raw_alloc = []
            alloc_rows = _normalize_asset_allocation_rows(fund_code, raw_alloc or [], report_date)
        stock_weight = None if not alloc_rows else alloc_rows[0]["stock_weight_pct"]
        print(f"[onboard] asset allocation fetched: stock_weight={stock_weight}")
        if alloc_rows:
            import_asset_allocations_from_rows(session, alloc_rows)
        else:
            warnings.append("缺股票仓位, 未做覆盖率放大")
    except Exception as exc:
        warnings.append(f"资产配置拉取失败: {exc}")

    start_date = max(report_date, today - timedelta(days=60))
    try:
        nav_report = fetch_and_store_fund_navs(session, data_source, fund_code, start_date, today)
        print(f"[onboard] nav rows imported: {nav_report.imported_count}")
    except Exception as exc:
        warnings.append(f"基金净值回填失败: {exc}")
        print(f"[onboard] nav rows imported: 0, error={exc}")

    asset_codes = [item.asset_code for item in active_holding.items] if active_holding else []
    if asset_codes:
        try:
            quote_report = fetch_and_store_stock_quotes(session, data_source, start_date, today, asset_codes)
            print(f"[onboard] stock quote rows imported: {quote_report.imported_count}")
        except Exception as exc:
            warnings.append(f"股票日K回填失败: {exc}")
            print(f"[onboard] stock quote rows imported: 0, error={exc}")
        try:
            build_effective_weight_version(session, fund_code, today)
            session.commit()
        except Exception as exc:
            warnings.append(f"修正权重生成失败: {exc}")
        try:
            residual_count = _run_causal_calibration_history(
                session=session,
                fund_code=fund_code,
                holding_version=active_holding,
                force_rebuild=force_rebuild,
            )
            if residual_count == 0:
                warnings.append("暂无可用残差样本")
            print(f"[onboard] residual rows built: {residual_count}")
        except Exception as exc:
            warnings.append(f"在线校准失败: {exc}")
            print(f"[onboard] residual rows built: 0, error={exc}")

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
    print(f"[onboard] status={status}")
    return {
        "fund_code": fund_code,
        "fund_name": fund.fund_name if fund else fund_name,
        "latest_unit_nav": profile.latest_unit_nav,
        "latest_nav_date": profile.latest_nav_date.isoformat() if profile.latest_nav_date else None,
        "status": status,
        "warnings": warnings,
    }


def repair_all_etf_feeders(
    session: Session,
    data_source,
    force_rebuild: bool = True,
) -> list[dict[str, object]]:
    """批量重建 ETF 联接基金, 统一切到目标 ETF 建模。"""
    funds = session.scalars(
        select(Fund)
        .where(
            (Fund.fund_type == "etf_feeder")
            | (Fund.fund_name.like("%ETF%联接%"))
            | (Fund.fund_name.like("%ETF发起式联接%"))
        )
        .order_by(Fund.fund_code.asc())
    ).all()
    results: list[dict[str, object]] = []
    for fund in funds:
        results.append(
            ensure_fund_full_onboarded(
                session=session,
                fund_code=fund.fund_code,
                data_source=data_source,
                add_watchlist=False,
                force_rebuild=force_rebuild,
            )
        )
    return results
