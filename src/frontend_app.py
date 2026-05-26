from __future__ import annotations

import csv
import io
import logging
import sys
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
from time import monotonic

from fastapi import BackgroundTasks, FastAPI, Form, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.calibration import (
        ensure_fund_by_code,
        get_calibration_stats,
        load_calibration_residuals,
        run_online_calibration,
    )
    from src.data_sources import AKShareDataSource
    from src.db import get_session_factory
    from src.estimator import compute_live_fund_estimates
    from src.init_db import init_db
    from src.models import DailyQuote, EffectiveWeightVersion, Fund, HoldingVersion, UserFundPosition, UserWatchlistFund, TaskRun, CalibrationResidual
    from src.onboarding import ensure_fund_full_onboarded
    from src.tasks import async_onboard_new_fund, sync_daily_all_funds
    from src.web.actions import run_effective_weight_action
    from src.web_services import (
        deactivate_fund,
        load_asset_allocation_rows,
        load_fund_rows,
        load_holding_rows,
        load_user_position_rows,
        load_watchlist_rows,
        save_asset_allocation_rows,
        save_fund_rows,
        save_holding_rows,
        save_user_position_rows,
        save_watchlist_rows,
        toggle_watchlist_fund,
    )
else:
    from .calibration import (
        ensure_fund_by_code,
        get_calibration_stats,
        load_calibration_residuals,
        run_online_calibration,
    )
    from .data_sources import AKShareDataSource
    from .db import get_session_factory
    from .estimator import compute_live_fund_estimates
    from .init_db import init_db
    from .models import DailyQuote, EffectiveWeightVersion, Fund, HoldingVersion, UserFundPosition, UserWatchlistFund, TaskRun, CalibrationResidual
    from .onboarding import ensure_fund_full_onboarded
    from .tasks import async_onboard_new_fund, sync_daily_all_funds
    from .web.actions import run_effective_weight_action
    from .web_services import (
        deactivate_fund,
        load_asset_allocation_rows,
        load_fund_rows,
        load_holding_rows,
        load_user_position_rows,
        load_watchlist_rows,
        save_asset_allocation_rows,
        save_fund_rows,
        save_holding_rows,
        save_user_position_rows,
        save_watchlist_rows,
        toggle_watchlist_fund,
    )

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_CACHE_DIR = PROJECT_ROOT / "data" / "raw" / "akshare"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"

app = FastAPI(title="基金实时估值")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

SORT_OPTIONS = {
    "estimate_desc": "估值排序",
    "profit_desc": "盈亏排序",
    "error_asc": "误差排序",
    "name_asc": "名称排序",
}
CONFIDENCE_RANK = {"A": 4, "B": 3, "C": 2, "D": 1, None: 0}
LIVE_BUNDLE_CACHE: dict[str, tuple[float, tuple]] = {}
LIVE_BUNDLE_TTL = 12.0


@lru_cache(maxsize=1)
def get_cached_session_factory():
    init_db()
    return get_session_factory()


@lru_cache(maxsize=1)
def get_cached_data_source():
    return AKShareDataSource(raw_dir=RAW_CACHE_DIR)


def format_percent(v: float | None) -> str:
    return "--" if v is None else f"{v:+.2%}"


def format_amount(v: float | None) -> str:
    return "--" if v is None else f"{v:+.2f}"


def get_tone(v: float | None) -> str:
    if v is None:
        return "muted"
    return "up" if v > 0 else ("down" if v < 0 else "muted")


def summarize_status(warnings: list[str], used_fallback: bool) -> str:
    if used_fallback:
        return "部分行情延迟，已使用最近收盘缓存"
    if any("timed out" in w or "failed" in w for w in warnings):
        return "部分行情延迟，已使用可用行情"
    return "实时行情正常"


def load_latest_daily_quote_map(session, asset_codes: list[str]):
    if not asset_codes:
        return {}, False
    rows = session.scalars(
        select(DailyQuote)
        .where(DailyQuote.asset_code.in_(asset_codes))
        .order_by(DailyQuote.trade_date.desc())
    ).all()
    quote_map: dict = {}
    for q in rows:
        if q.asset_code in quote_map:
            continue
        quote_map[q.asset_code] = {
            "asset_name": q.asset_name,
            "return_pct": q.return_pct,
            "quote_time": datetime.combine(q.trade_date, time(15, 0)),
            "source": f"{q.source}:fallback_daily",
        }
    return quote_map, bool(quote_map)


def load_live_estimate_bundle(fund_code: str | None = None, force_refresh: bool = False):
    cache_key = fund_code or "__all__"
    cached = LIVE_BUNDLE_CACHE.get(cache_key)
    if not force_refresh and cached and monotonic() - cached[0] <= LIVE_BUNDLE_TTL:
        return cached[1]

    session_factory = get_cached_session_factory()
    data_source = get_cached_data_source()

    with session_factory() as session:
        if fund_code is None:
            pos_rows = [r for r in load_user_position_rows(session) if r.get("is_active")]
            wl_rows = [r for r in load_watchlist_rows(session) if r.get("is_active")]
            active_codes = {str(r["fund_code"]) for r in pos_rows} | {str(r["fund_code"]) for r in wl_rows}
            holding_rows = load_holding_rows(session) if not active_codes else [
                r for r in load_holding_rows(session) if r["fund_code"] in active_codes
            ]
        else:
            holding_rows = load_holding_rows(session, fund_code)

    asset_codes = list(dict.fromkeys(str(r["asset_code"]) for r in holding_rows))
    if not asset_codes:
        with session_factory() as session:
            results = compute_live_fund_estimates(
                session=session,
                live_quotes={},
                trade_date=date.today(),
                quote_time=None,
                fund_code=fund_code,
            )
        payload = (results, "当前没有可用持仓", False)
        LIVE_BUNDLE_CACHE[cache_key] = (monotonic(), payload)
        return payload

    try:
        if hasattr(data_source, "last_warnings"):
            data_source.last_warnings = []
        live_records = data_source.fetch_stock_live_quotes(
            asset_codes=asset_codes, sleep_seconds=0.0, timeout_seconds=8.0
        )
        warnings = list(getattr(data_source, "last_warnings", []))
    except Exception as exc:
        logger.warning("fetch_stock_live_quotes failed: %s", exc)
        live_records = []
        warnings = [str(exc)]

    live_quote_map = {
        r.asset_code: {
            "asset_name": r.asset_name,
            "return_pct": r.return_pct,
            "quote_time": r.quote_time,
            "source": r.source,
        }
        for r in live_records
    }

    used_fallback = False
    with session_factory() as session:
        fallback_map, has_fallback = load_latest_daily_quote_map(session, asset_codes)

    if not live_records and has_fallback:
        live_quote_map = fallback_map
        used_fallback = True
        warnings.append("fallback_to_daily_quotes")
    elif live_records and len(live_quote_map) < len(asset_codes) and has_fallback:
        for code, payload in fallback_map.items():
            live_quote_map.setdefault(code, payload)
        used_fallback = True
        warnings.append("partial_fallback")

    if not live_quote_map:
        return [], "当前抓不到实时行情，也没有可用缓存", False

    quote_times = [p["quote_time"] for p in live_quote_map.values() if isinstance(p.get("quote_time"), datetime)]
    quote_time = max(quote_times) if quote_times else datetime.now()

    with session_factory() as session:
        results = compute_live_fund_estimates(
            session=session,
            live_quotes=live_quote_map,
            trade_date=quote_time.date(),
            quote_time=quote_time,
            fund_code=fund_code,
            selection_window=20,
            min_samples=10,
            min_improvement_bps=5,
            selection_policy="coverage_first",
            calibration_window=20,
            calibration_base="coverage_adjusted",
            calibration_min_samples=5,
        )

    payload = (results, summarize_status(warnings, used_fallback), used_fallback)
    LIVE_BUNDLE_CACHE[cache_key] = (monotonic(), payload)
    return payload


def get_compare_residuals(session, fund_codes: list[str]) -> dict:
    if not fund_codes:
        return {}
    from sqlalchemy import func
    
    subq = (
        session.query(
            CalibrationResidual.fund_code,
            func.max(CalibrationResidual.trade_date).label("max_date")
        )
        .filter(CalibrationResidual.fund_code.in_(fund_codes))
        .group_by(CalibrationResidual.fund_code)
        .subquery()
    )
    residuals = (
        session.query(CalibrationResidual)
        .join(subq, (CalibrationResidual.fund_code == subq.c.fund_code) & (CalibrationResidual.trade_date == subq.c.max_date))
        .all()
    )
    return {r.fund_code: r for r in residuals}

def build_home_rows(results: list, residuals_map: dict | None = None) -> list[dict]:
    residuals_map = residuals_map or {}
    now = datetime.now()
    is_market_open = now.time() >= time(9, 30)
    today_date = now.date()
    rows = []
    for item in results:
        if item.fund_code in ("000001", "000002") or "示例" in (item.fund_name or ""):
            continue
        status_text = item.error_band_label or "样本不足"
        current_estimate_text = format_percent(item.current_estimate)
        if item.best_status == "no_data":
            current_estimate_text = "缺持仓"
            status_text = "缺持仓"
        elif item.best_status == "missing_quotes":
            current_estimate_text = "行情缺失"
            status_text = "不可估"
        res = residuals_map.get(item.fund_code)
        compare_actual_text = None
        compare_predicted_text = None
        compare_actual_tone = "muted"
        compare_predicted_tone = "muted"
        show_compare = False
        
        if res:
            if is_market_open:
                if res.trade_date == today_date:
                    show_compare = True
            else:
                show_compare = True
                
        if show_compare:
            compare_actual_text = format_percent(res.actual_return)
            compare_predicted_text = format_percent(res.calibrated_estimate)
            compare_actual_tone = get_tone(res.actual_return)
            compare_predicted_tone = get_tone(res.calibrated_estimate)

        rows.append({
            "fund_code": item.fund_code,
            "fund_name": item.fund_name,
            "current_estimate": item.current_estimate,
            "current_estimate_text": current_estimate_text,
            "estimate_tone": get_tone(item.current_estimate),
            "holding_amount": item.holding_amount,
            "estimated_today_profit": item.estimated_today_profit,
            "estimated_today_profit_text": format_amount(item.estimated_today_profit),
            "profit_tone": get_tone(item.estimated_today_profit),
            "confidence_level": item.confidence_level or "D",
            "error_band_pct": item.error_band_pct,
            "error_band_label": status_text,
            "confidence_text": item.confidence_text,
            "best_status": item.best_status,
            "quote_time": item.quote_time.strftime("%H:%M:%S") if (item.quote_time and item.quote_time.date() == date.today()) else (item.quote_time.strftime("%m-%d %H:%M") if item.quote_time else "--"),
            "raw_quote_time": item.quote_time,
            "is_holding": item.holding_amount is not None,
            "is_watchlist": False,
            "show_compare": show_compare,
            "compare_actual_text": compare_actual_text,
            "compare_predicted_text": compare_predicted_text,
            "compare_actual_tone": compare_actual_tone,
            "compare_predicted_tone": compare_predicted_tone,
        })
    return rows


def sort_home_rows(rows: list[dict], sort_key: str) -> list[dict]:
    if sort_key == "profit_desc":
        rows.sort(key=lambda r: r["estimated_today_profit"] if r["estimated_today_profit"] is not None else -999999, reverse=True)
    elif sort_key == "error_asc":
        rows.sort(key=lambda r: r["error_band_pct"] if r["error_band_pct"] is not None else 999999)
    elif sort_key == "name_asc":
        rows.sort(key=lambda r: (r["fund_name"], r["fund_code"]))
    else:
        rows.sort(key=lambda r: r["current_estimate"] if r["current_estimate"] is not None else -999999, reverse=True)
    return rows


def build_detail_context(result, is_watchlist: bool = False, holding_report_date: date | None = None) -> dict:
    holdings = sorted(result.holdings, key=lambda h: abs(h.contribution_pct or 0.0), reverse=True)
    return {
        "fund_code": result.fund_code,
        "fund_name": result.fund_name,
        "current_estimate_text": format_percent(result.current_estimate),
        "current_estimate_tone": get_tone(result.current_estimate),
        "estimated_today_profit_text": format_amount(result.estimated_today_profit),
        "estimated_today_profit_tone": get_tone(result.estimated_today_profit),
        "confidence_level": result.confidence_level or "D",
        "error_band_label": result.error_band_label or "样本不足",
        "confidence_text": result.confidence_text,
        "quote_time": result.quote_time.strftime("%H:%M:%S") if (result.quote_time and result.quote_time.date() == date.today()) else (result.quote_time.strftime("%m-%d %H:%M") if result.quote_time else "--"),
        "trade_date": result.trade_date.isoformat(),
        "holding_report_date": holding_report_date.isoformat() if holding_report_date else "--",
        "latest_real_nav_date": result.latest_real_nav_date.isoformat() if result.latest_real_nav_date else "--",
        "is_realtime": result.quote_time.date() == date.today() if result.quote_time else False,
        "is_watchlist": is_watchlist,
        "holdings": [
            {
                "asset_name": h.asset_name,
                "asset_code": h.asset_code,
                "published_weight": f"{h.published_weight_pct:.2f}%",
                "effective_weight": f"{h.effective_weight_pct:.2f}%",
                "live_return": "--" if h.return_pct is None else f"{h.return_pct:+.2f}%",
                "return_tone": get_tone(h.return_pct),
                "contribution": "--" if h.contribution_pct is None else f"{h.contribution_pct:+.2f}%",
                "contribution_tone": get_tone(h.contribution_pct),
            }
            for h in holdings
        ],
        "advanced_rows": [
            ("公开权重合计", f"{result.covered_weight * 100:.2f}%"),
            ("修正权重合计", f"{result.current_scale_factor * result.covered_weight * 100:.2f}%"),
            ("当前缩放系数", f"{result.current_scale_factor:.4f}"),
            ("最近真实净值日", result.latest_real_nav_date.isoformat() if result.latest_real_nav_date else "--"),
            ("最近误差", "--" if result.latest_mae is None else f"{result.latest_mae:.2%}"),
        ],
    }


# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/")
def index(
    request: Request,
    search: str = Query(""),
    sort: str = Query("estimate_desc"),
    refresh: str = Query("off"),
    force: int = Query(0),
):
    results, status_message, used_fallback = load_live_estimate_bundle(force_refresh=bool(force))

    session_factory = get_cached_session_factory()
    with session_factory() as session:
        fund_codes = [r.fund_code for r in results]
        residuals_map = get_compare_residuals(session, fund_codes)
    
    rows = build_home_rows(results, residuals_map)

    session_factory = get_cached_session_factory()
    with session_factory() as session:
        watchlist_codes = {str(r["fund_code"]) for r in load_watchlist_rows(session) if r.get("is_active")}
        
        # 获取所有运行中或等待中的任务
        tasks_query = session.execute(
            select(TaskRun).where(TaskRun.status.in_(["pending", "running"]))
        ).scalars().all()
        active_tasks = [
            {
                "task_type": t.task_type,
                "fund_code": t.fund_code,
                "status": t.status,
                "progress_text": t.progress_text,
            }
            for t in tasks_query
        ]

    for row in rows:
        row["is_watchlist"] = row["fund_code"] in watchlist_codes

    if search.strip():
        kw = search.strip().lower()
        rows = [r for r in rows if kw in r["fund_name"].lower() or kw in r["fund_code"].lower()]

    rows = sort_home_rows(rows, sort)
    raw_times = [r.pop("raw_quote_time") for r in rows if "raw_quote_time" in r]
    if raw_times:
        latest_dt = max(raw_times)
        latest_time = latest_dt.strftime("%H:%M:%S") if latest_dt.date() == date.today() else latest_dt.strftime("%m-%d %H:%M")
    else:
        latest_time = "--"
    data_mode = "最近收盘缓存" if used_fallback else "实时行情"
    quote_dates = [res.quote_time.date().isoformat() for res in results if res.quote_time]
    estimate_date = max(quote_dates) if quote_dates else "--"

    return templates.TemplateResponse(request, "index.html", {
        "rows": rows,
        "search": search,
        "sort": sort,
        "sort_options": SORT_OPTIONS,
        "refresh": refresh,
        "status_message": status_message,
        "latest_time": latest_time,
        "data_mode": data_mode,
        "estimate_date": estimate_date,
        "active_tasks": active_tasks,
        "today_label": date.today().isoformat(),
    })


@app.get("/api/live-estimates")
def api_live_estimates(
    search: str = Query(""),
    sort: str = Query("estimate_desc"),
):
    try:
        results, status_message, used_fallback = load_live_estimate_bundle()
    except Exception as exc:
        logger.error("api_live_estimates error: %s", exc)
        return JSONResponse({"rows": [], "status_message": "行情获取失败", "latest_time": "--"})

    session_factory = get_cached_session_factory()
    with session_factory() as session:
        fund_codes = [r.fund_code for r in results]
        residuals_map = get_compare_residuals(session, fund_codes)

    rows = build_home_rows(results, residuals_map)
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        watchlist_codes = {str(r["fund_code"]) for r in load_watchlist_rows(session) if r.get("is_active")}
    for row in rows:
        row["is_watchlist"] = row["fund_code"] in watchlist_codes

    if search.strip():
        kw = search.strip().lower()
        rows = [r for r in rows if kw in r["fund_name"].lower() or kw in r["fund_code"].lower()]

    rows = sort_home_rows(rows, sort)
    raw_times = [r.pop("raw_quote_time") for r in rows if "raw_quote_time" in r]
    if raw_times:
        latest_dt = max(raw_times)
        latest_time = latest_dt.strftime("%H:%M:%S") if latest_dt.date() == date.today() else latest_dt.strftime("%m-%d %H:%M")
    else:
        latest_time = "--"
    data_mode = "最近收盘缓存" if used_fallback else "实时行情"

    return JSONResponse({
        "rows": rows,
        "status_message": status_message,
        "latest_time": latest_time,
        "data_mode": data_mode,
    })


@app.get("/fund/{fund_code}")
def fund_detail(request: Request, fund_code: str, debug: int = 0, msg: str = ""):
    results, status_message, used_fallback = load_live_estimate_bundle(fund_code=fund_code)
    result = results[0] if results else None
    session_factory = get_cached_session_factory()
    is_watchlist = False
    cal_stats: dict = {}
    holding_report_date = None
    with session_factory() as session:
        wl_rows = load_watchlist_rows(session)
        is_watchlist = any(str(r["fund_code"]) == fund_code and r.get("is_active") for r in wl_rows)
        active_holding = session.scalar(
            select(HoldingVersion)
            .where(HoldingVersion.fund_code == fund_code, HoldingVersion.is_active.is_(True))
            .order_by(HoldingVersion.report_date.desc(), HoldingVersion.created_at.desc())
        )
        holding_report_date = None if active_holding is None else active_holding.report_date
        try:
            cal_stats = get_calibration_stats(session, fund_code)
        except Exception:
            pass

    if result is None:
        return templates.TemplateResponse(
            request, "fund_detail.html",
            {"detail": None, "status_message": status_message, "debug": debug, "msg": msg, "cal_stats": cal_stats},
            status_code=404,
        )
    return templates.TemplateResponse(request, "fund_detail.html", {
        "detail": build_detail_context(result, is_watchlist, holding_report_date),
        "status_message": status_message,
        "debug": debug,
        "msg": msg,
        "cal_stats": cal_stats,
    })


@app.post("/fund/{fund_code}/watch")
def toggle_watch(fund_code: str):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        toggle_watchlist_fund(session, fund_code)
    return RedirectResponse(url=f"/fund/{fund_code}", status_code=303)


# ── Portfolio ──────────────────────────────────────────────────────────────

@app.get("/portfolio")
def portfolio(request: Request, saved: int = 0):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        positions = load_user_position_rows(session)
        funds = load_fund_rows(session)
        wl_rows = load_watchlist_rows(session)
    fund_name_map = {f["fund_code"]: f["fund_name"] for f in funds}
    for p in positions:
        p["fund_name"] = fund_name_map.get(p["fund_code"], "")
    for w in wl_rows:
        w["fund_name"] = fund_name_map.get(w["fund_code"], "")
    return templates.TemplateResponse(request, "portfolio.html", {
        "positions": positions,
        "funds": [f for f in funds if f["is_active"]],
        "watchlist_rows": wl_rows,
        "saved": saved,
    })


@app.post("/portfolio")
def save_portfolio(
    fund_code: str = Form(...),
    holding_amount: str = Form(""),
    holding_share: str = Form(""),
    cost_nav: str = Form(""),
    platform: str = Form("支付宝/蚂蚁财富"),
    is_active: str = Form("1"),
):
    fund_code = fund_code.strip()
    session_factory = get_cached_session_factory()
    data_source = get_cached_data_source()
    with session_factory() as session:
        amt = None if not holding_amount.strip() else float(holding_amount)
        share = None if not holding_share.strip() else float(holding_share)
        fund_info = ensure_fund_full_onboarded(
            session,
            fund_code,
            data_source,
            holding_amount=amt,
            add_watchlist=True,
        )
        if amt is not None and share is None and fund_info.get("latest_unit_nav"):
            share = amt / float(fund_info["latest_unit_nav"])

        save_user_position_rows(session, [{
            "fund_code": fund_code,
            "holding_amount": amt,
            "holding_share": share,
            "cost_nav": None if not cost_nav.strip() else float(cost_nav),
            "platform": platform.strip() or "支付宝/蚂蚁财富",
            "is_active": is_active == "1",
        }])
    return RedirectResponse(url="/portfolio?saved=1", status_code=303)


@app.post("/portfolio/watchlist")
def save_watchlist(fund_code: str = Form(...), is_active: str = Form("1")):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        save_watchlist_rows(session, [{"fund_code": fund_code, "is_active": is_active == "1"}])
    return RedirectResponse(url="/portfolio?saved=1", status_code=303)


# ── Manage ──────────────────────────────────────────────────────────────────

def _load_eff_weights(session) -> list[dict]:
    rows = session.scalars(
        select(EffectiveWeightVersion)
        .where(EffectiveWeightVersion.is_active.is_(True))
        .order_by(EffectiveWeightVersion.fund_code, EffectiveWeightVersion.report_date.desc())
    ).all()
    return [
        {
            "fund_code": r.fund_code,
            "report_date": r.report_date.isoformat(),
            "stock_weight": r.stock_weight,
            "scale_factor": r.scale_factor,
            "covered_weight": r.covered_weight,
            "total_effective_weight": r.total_effective_weight,
        }
        for r in rows[:30]
    ]


@app.get("/manage")
def manage(request: Request, message: str = ""):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        funds = load_fund_rows(session)
        holdings = load_holding_rows(session)[:30]
        assets = load_asset_allocation_rows(session)
        eff_weights = _load_eff_weights(session)
    return templates.TemplateResponse(request, "manage.html", {
        "message": message,
        "funds": funds,
        "holdings": holdings,
        "assets": assets,
        "eff_weights": eff_weights,
    })


@app.post("/manage/fund/save")
def manage_fund_save(
    fund_code: str = Form(...),
    fund_name: str = Form(...),
    fund_type: str = Form("equity"),
    market: str = Form("A股"),
    is_active: str = Form("1"),
):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        save_fund_rows(session, [{
            "fund_code": fund_code.strip(),
            "fund_name": fund_name.strip(),
            "fund_type": fund_type,
            "market": market,
            "is_active": is_active == "1",
        }])
    return RedirectResponse(url=f"/manage?message=已保存基金 {fund_code}", status_code=303)


@app.post("/manage/fund/disable")
def manage_fund_disable(fund_code: str = Form(...)):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        ok = deactivate_fund(session, fund_code)
    msg = f"已停用基金 {fund_code}" if ok else "未找到该基金"
    return RedirectResponse(url=f"/manage?message={msg}", status_code=303)


@app.post("/manage/holding/save")
async def manage_holding_save(request: Request):
    form = await request.form()
    fund_code = str(form.get("fund_code", "")).strip()
    report_date = str(form.get("report_date", "")).strip()
    source = str(form.get("source", "官网")).strip() or "官网"

    if not fund_code or not report_date:
        return RedirectResponse(url="/manage?message=请填写基金代码和报告日", status_code=303)

    rows = []
    i = 0
    while True:
        code = str(form.get(f"asset_code_{i}", "")).strip()
        name = str(form.get(f"asset_name_{i}", "")).strip()
        atype = str(form.get(f"asset_type_{i}", "stock")).strip() or "stock"
        weight_str = str(form.get(f"weight_pct_{i}", "")).strip()
        if not code and not name and i >= 10:
            break
        if code and name and weight_str:
            try:
                rows.append({
                    "fund_code": fund_code,
                    "report_date": report_date,
                    "source": source,
                    "asset_code": code,
                    "asset_name": name,
                    "asset_type": atype,
                    "weight_pct": float(weight_str),
                })
            except ValueError:
                pass
        i += 1

    if not rows:
        return RedirectResponse(url="/manage?message=未填写有效持仓行", status_code=303)

    session_factory = get_cached_session_factory()
    with session_factory() as session:
        count = save_holding_rows(session, rows)
    return RedirectResponse(url=f"/manage?message=已保存持仓 {count} 条", status_code=303)


@app.post("/manage/asset-allocation/save")
def manage_asset_save(
    fund_code: str = Form(...),
    report_date: str = Form(...),
    source: str = Form("官网"),
    stock_weight_pct: str = Form("0"),
    bond_weight_pct: str = Form("0"),
    cash_weight_pct: str = Form("0"),
    other_weight_pct: str = Form("0"),
):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        save_asset_allocation_rows(session, [{
            "fund_code": fund_code.strip(),
            "report_date": report_date,
            "source": source.strip() or "官网",
            "stock_weight_pct": float(stock_weight_pct or 0),
            "bond_weight_pct": float(bond_weight_pct or 0),
            "cash_weight_pct": float(cash_weight_pct or 0),
            "other_weight_pct": float(other_weight_pct or 0),
        }])
    return RedirectResponse(url=f"/manage?message=已保存资产配置", status_code=303)


@app.post("/manage/effective-weight/generate")
def manage_eff_weight(fund_code: str = Form(...), trade_date: str = Form(...)):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        run_effective_weight_action(
            session=session,
            fund_code=fund_code,
            trade_date=date.fromisoformat(trade_date),
        )
    return RedirectResponse(url="/manage?message=已生成或更新修正权重", status_code=303)


# ── Legacy routes (keep old URLs working) ──────────────────────────────────

@app.post("/manage/funds")
def manage_funds_csv(csv_text: str = Form(...)):
    rows = _parse_csv(csv_text)
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        count = save_fund_rows(session, rows)
    return RedirectResponse(url=f"/manage?message=已保存基金 {count} 条", status_code=303)


@app.post("/manage/funds/deactivate")
def manage_fund_deactivate(fund_code: str = Form(...)):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        ok = deactivate_fund(session, fund_code)
    msg = f"已停用基金 {fund_code}" if ok else "未找到该基金"
    return RedirectResponse(url=f"/manage?message={msg}", status_code=303)


@app.post("/manage/holdings")
def manage_holdings_csv(csv_text: str = Form(...)):
    rows = _parse_csv(csv_text)
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        count = save_holding_rows(session, rows)
    return RedirectResponse(url=f"/manage?message=已保存持仓 {count} 条", status_code=303)


@app.post("/manage/assets")
def manage_assets_csv(csv_text: str = Form(...)):
    rows = _parse_csv(csv_text)
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        count = save_asset_allocation_rows(session, rows)
    return RedirectResponse(url=f"/manage?message=已保存资产配置 {count} 条", status_code=303)


@app.post("/manage/effective-weights")
def manage_effective_weights(fund_code: str = Form(...), trade_date: str = Form(...)):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        run_effective_weight_action(
            session=session,
            fund_code=fund_code,
            trade_date=date.fromisoformat(trade_date),
        )
    return RedirectResponse(url="/manage?message=已生成或更新修正权重", status_code=303)



# ── Fund Search API ────────────────────────────────────────────────────────

@app.get("/api/search-fund")
def api_search_fund(code: str = Query(""), fund_code: str = Query("")):
    """搜索基金：先查本地库，如果没有则尝试拉取基础信息（不创建记录）。"""
    code = (fund_code or code).strip()
    if not code:
        return JSONResponse({"found": False, "in_db": False})

    session_factory = get_cached_session_factory()
    data_source = get_cached_data_source()

    with session_factory() as session:
        fund = session.get(Fund, code)
        if fund is not None:
            # 查持仓状态
            pos = session.scalar(
                select(UserFundPosition).where(UserFundPosition.fund_code == code)
            )
            wl = session.scalar(
                select(UserWatchlistFund).where(UserWatchlistFund.fund_code == code)
            )
            has_holdings = bool(load_holding_rows(session, code))
            return JSONResponse({
                "found": True,
                "in_db": True,
                "fund_code": fund.fund_code,
                "fund_name": fund.fund_name,
                "is_active": fund.is_active,
                "has_position": pos is not None,
                "holding_amount": pos.holding_amount if pos else None,
                "in_watchlist": bool(wl and wl.is_active),
                "holdings_status": "已拉取" if has_holdings else "待拉取",
            })

    # 不在库里，尝试拉取基础信息
    try:
        profile = data_source.fetch_fund_profile(code)
        return JSONResponse({
            "found": True,
            "in_db": False,
            "fund_code": profile.fund_code,
            "fund_name": profile.fund_name,
            "latest_unit_nav": profile.latest_unit_nav,
            "latest_nav_date": profile.latest_nav_date.isoformat() if profile.latest_nav_date else None,
            "holdings_status": "待拉取",
        })
    except Exception as exc:
        logger.warning("search_fund fetch_fund_profile failed for %s: %s", code, exc)
        return JSONResponse({"found": False, "in_db": False, "fund_code": code})


@app.post("/api/quick-add")
async def api_quick_add(request: Request, background_tasks: BackgroundTasks):
    """快速加入自选：只需 fund_code，后台进行完整建档。"""
    body = await request.json()
    fund_code = str(body.get("fund_code", "")).strip()
    if not fund_code:
        return JSONResponse({"ok": False, "error": "fund_code 不能为空"})

    session_factory = get_cached_session_factory()
    data_source = get_cached_data_source()
    with session_factory() as session:
        # 先保存自选，让前端立刻可见
        save_watchlist_rows(session, [{"fund_code": fund_code, "is_active": True}])
        
        # 获取基础信息
        profile = ensure_fund_by_code(session, fund_code, data_source)
        
        # 创建 TaskRun 记录
        task = TaskRun(
            task_type="onboard_fund",
            fund_code=fund_code,
            status="pending",
            progress_text="等待建档任务启动...",
        )
        session.add(task)
        session.commit()
        task_id = task.id

    # 启动后台任务
    background_tasks.add_task(async_onboard_new_fund, fund_code, task_id)

    return JSONResponse({
        "ok": True,
        "fund_code": profile["fund_code"],
        "fund_name": profile["fund_name"],
        "created": profile.get("created", False),
        "status": "onboarding",
    })


@app.post("/api/quick-buy")
async def api_quick_buy(request: Request, background_tasks: BackgroundTasks):
    """快速买入：fund_code + holding_amount，后台进行建档。"""
    body = await request.json()
    fund_code = str(body.get("fund_code", "")).strip()
    holding_amount_raw = body.get("holding_amount")
    if not fund_code:
        return JSONResponse({"ok": False, "error": "fund_code 不能为空"})
    try:
        holding_amount = float(holding_amount_raw)
        assert holding_amount > 0
    except Exception:
        return JSONResponse({"ok": False, "error": "持有金额必须是正数"})

    session_factory = get_cached_session_factory()
    data_source = get_cached_data_source()
    with session_factory() as session:
        # 先保存持仓
        profile = ensure_fund_by_code(session, fund_code, data_source)
        save_user_position_rows(session, [{
            "fund_code": fund_code,
            "holding_amount": holding_amount,
            "holding_share": (holding_amount / profile.get("latest_unit_nav")) if profile.get("latest_unit_nav") else None,
            "platform": "支付宝/蚂蚁财富",
            "is_active": True,
        }])
        save_watchlist_rows(session, [{"fund_code": fund_code, "is_active": True}])
        
        pos = session.scalar(select(UserFundPosition).where(UserFundPosition.fund_code == fund_code))

        # 创建 TaskRun 记录
        task = TaskRun(
            task_type="onboard_fund",
            fund_code=fund_code,
            status="pending",
            progress_text="等待建档任务启动...",
        )
        session.add(task)
        session.commit()
        task_id = task.id
        
        estimated_share = pos.holding_share if pos else None

    background_tasks.add_task(async_onboard_new_fund, fund_code, task_id)

    return JSONResponse({
        "ok": True,
        "fund_code": profile["fund_code"],
        "fund_name": profile["fund_name"],
        "holding_amount": holding_amount,
        "estimated_share": estimated_share,
        "status": "onboarding",
    })


@app.post("/api/fund/{fund_code}/calibrate")
async def api_fund_calibrate(fund_code: str, request: Request):
    """
    增量校准或强制重跑全部校准。
    body: {"force": true/false}
    """
    body = await request.json() if request.method == "POST" else {}
    force = body.get("force", False)
    
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        if force:
            from .calibration import force_replay_calibration
            count = force_replay_calibration(session, fund_code)
        else:
            from .calibration import run_incremental_calibration
            count = run_incremental_calibration(session, fund_code)
            
    return JSONResponse({
        "ok": True,
        "fund_code": fund_code,
        "calibrated_count": count,
    })

@app.post("/api/position/set-amount")
async def api_position_set_amount(request: Request):
    body = await request.json()
    fund_code = body.get("fund_code", "").strip()
    amount = float(body.get("amount", 0))
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        save_user_position_rows(session, [{"fund_code": fund_code, "holding_amount": amount, "is_active": True}])
    return JSONResponse({"ok": True})

@app.post("/api/position/buy")
async def api_position_buy(request: Request):
    body = await request.json()
    fund_code = body.get("fund_code", "").strip()
    amount = float(body.get("amount", 0))
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        pos = session.scalar(select(UserFundPosition).where(UserFundPosition.fund_code == fund_code))
        current = pos.holding_amount if pos else 0.0
        save_user_position_rows(session, [{"fund_code": fund_code, "holding_amount": current + amount, "is_active": True}])
    return JSONResponse({"ok": True})

@app.post("/api/position/sell")
async def api_position_sell(request: Request):
    body = await request.json()
    fund_code = body.get("fund_code", "").strip()
    amount = float(body.get("amount", 0))
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        pos = session.scalar(select(UserFundPosition).where(UserFundPosition.fund_code == fund_code))
        current = pos.holding_amount if pos else 0.0
        new_amount = max(0.0, current - amount)
        save_user_position_rows(session, [{"fund_code": fund_code, "holding_amount": new_amount, "is_active": True}])
    return JSONResponse({"ok": True})

@app.post("/api/position/clear")
async def api_position_clear(request: Request):
    body = await request.json()
    fund_code = body.get("fund_code", "").strip()
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        save_user_position_rows(session, [{"fund_code": fund_code, "holding_amount": 0.0, "is_active": False}])
    return JSONResponse({"ok": True})

@app.post("/api/position-events/import-text")
async def api_position_import_text(request: Request):
    return JSONResponse({"ok": False, "error": "待解析"})

@app.post("/api/position-events/import-image")
async def api_position_import_image(request: Request):
    return JSONResponse({"ok": False, "error": "not_implemented"})

@app.post("/api/sync-daily")
async def api_sync_daily(background_tasks: BackgroundTasks):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        task = TaskRun(
            task_type="sync_daily",
            fund_code="ALL",
            status="pending",
            progress_text="等待同步任务启动...",
        )
        session.add(task)
        session.commit()
        task_id = task.id
    
    background_tasks.add_task(sync_daily_all_funds, task_id)
    return JSONResponse({"ok": True, "task_id": task_id})


# ── Calibration Routes ─────────────────────────────────────────────────────

@app.post("/manage/calibration/run")
def manage_calibration_run(
    fund_code: str = Form(...),
    calibration_date: str = Form(""),
    force: str = Form("0"),
):
    """手动触发单基金因果校准。"""
    session_factory = get_cached_session_factory()
    cal_date = date.fromisoformat(calibration_date) if calibration_date.strip() else None
    with session_factory() as session:
        result = run_online_calibration(
            session=session,
            fund_code=fund_code,
            calibration_date=cal_date,
            force=force == "1",
        )
    if result is None:
        msg = f"校准失败：{fund_code} 暂无可用数据（需要 active 持仓 + 已公布真实净值）"
    elif result.is_updated:
        msg = (f"已校准 {fund_code}：scale {result.scale_factor_before:.4f} → "
               f"{result.scale_factor_after:.4f}，残差 {result.residual:+.4%}，"
               f"置信度 {result.confidence_level}")
    else:
        msg = (f"校准记录已写入（跳过更新）：{fund_code}，"
               f"原因: {result.skip_reason}")
    return RedirectResponse(url=f"/fund/{fund_code}?msg={msg}", status_code=303)


@app.get("/api/fund/{fund_code}/calibration-residuals")
def api_calibration_residuals(fund_code: str, limit: int = Query(90)):
    """返回某基金的逐日校准残差，用于详情页折叠展示。"""
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        rows = load_calibration_residuals(session, fund_code, limit=limit)
        stats = get_calibration_stats(session, fund_code)
    return JSONResponse({"residuals": rows, "stats": stats})


# ── Health ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True}



def _parse_csv(content: str) -> list[dict]:
    if not content.strip():
        return []
    reader = csv.DictReader(io.StringIO(content.strip()))
    return [dict(row) for row in reader]
