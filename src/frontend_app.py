from __future__ import annotations

import csv
import io
import sys
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
from time import monotonic

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.data_sources import AKShareDataSource
    from src.db import get_session_factory
    from src.estimator import compute_live_fund_estimates
    from src.init_db import init_db
    from src.models import DailyQuote, Fund, UserFundPosition
    from src.web.actions import run_effective_weight_action
    from src.web_services import (
        load_asset_allocation_rows,
        load_fund_rows,
        load_holding_rows,
        load_industry_allocation_rows,
        load_user_position_rows,
        load_watchlist_rows,
        deactivate_fund,
        save_asset_allocation_rows,
        save_fund_rows,
        save_holding_rows,
        save_industry_allocation_rows,
        save_user_position_rows,
        save_watchlist_rows,
        toggle_watchlist_fund,
    )
else:
    from .data_sources import AKShareDataSource
    from .db import get_session_factory
    from .estimator import compute_live_fund_estimates
    from .init_db import init_db
    from .models import DailyQuote, Fund, UserFundPosition
    from .web.actions import run_effective_weight_action
    from .web_services import (
        deactivate_fund,
        load_asset_allocation_rows,
        load_fund_rows,
        load_holding_rows,
        load_industry_allocation_rows,
        load_user_position_rows,
        load_watchlist_rows,
        save_asset_allocation_rows,
        save_fund_rows,
        save_holding_rows,
        save_industry_allocation_rows,
        save_user_position_rows,
        save_watchlist_rows,
        toggle_watchlist_fund,
    )


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_CACHE_DIR = PROJECT_ROOT / "data" / "raw" / "akshare"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"

app = FastAPI(title="Fund NAV Estimator Frontend")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

SORT_OPTIONS = {
    "estimate_desc": "按估值排序",
    "profit_desc": "按盈亏排序",
    "confidence_desc": "按置信度排序",
    "name_asc": "按基金名称排序",
}
VIEW_OPTIONS = {
    "watchlist": "自选",
    "holding": "持有",
    "all": "全部",
}

CONFIDENCE_RANK = {"A": 4, "B": 3, "C": 2, "D": 1, None: 0}
LIVE_BUNDLE_CACHE: dict[str, tuple[float, tuple[list, str, bool]]] = {}
LIVE_BUNDLE_TTL_SECONDS = 12.0


@lru_cache(maxsize=1)
def get_cached_session_factory():
    init_db()
    return get_session_factory()


@lru_cache(maxsize=1)
def get_cached_data_source():
    return AKShareDataSource(raw_dir=RAW_CACHE_DIR)


def get_data_status(quote_time: datetime | None) -> tuple[str, str]:
    if quote_time is None:
        return "无数据", "muted"
    age_seconds = max(int((datetime.now() - quote_time).total_seconds()), 0)
    if age_seconds > 300:
        return "数据过期", "stale"
    if age_seconds > 60:
        return "可能延迟", "delay"
    return "实时", "live"


def summarize_runtime_status(warnings: list[str], used_fallback: bool) -> str:
    if used_fallback:
        return "部分行情延迟, 已使用最近收盘缓存继续估值。"
    if any("timed out" in item or "failed" in item for item in warnings):
        return "部分行情延迟, 已使用可用行情继续估值。"
    return "实时行情正常。"


def format_percent(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{value:+.2%}"


def format_amount(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{value:+.2f}"


def get_tone(value: float | None) -> str:
    if value is None:
        return "muted"
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "muted"


def parse_csv_text(content: str) -> list[dict[str, object]]:
    if not content.strip():
        return []
    reader = csv.DictReader(io.StringIO(content.strip()))
    return [dict(row) for row in reader]


def load_latest_daily_quote_map(session, asset_codes: list[str]) -> tuple[dict[str, dict[str, object]], bool]:
    if not asset_codes:
        return {}, False
    rows = session.scalars(
        select(DailyQuote)
        .where(DailyQuote.asset_code.in_(asset_codes))
        .order_by(DailyQuote.trade_date.desc())
    ).all()
    quote_map: dict[str, dict[str, object]] = {}
    for quote in rows:
        if quote.asset_code in quote_map:
            continue
        quote_map[quote.asset_code] = {
            "asset_name": quote.asset_name,
            "return_pct": quote.return_pct,
            "quote_time": datetime.combine(quote.trade_date, time(hour=15, minute=0)),
            "source": f"{quote.source}:fallback_daily",
        }
    return quote_map, bool(quote_map)


def load_live_estimate_bundle(fund_code: str | None = None, force_refresh: bool = False) -> tuple[list, str, bool]:
    cache_key = fund_code or "__all__"
    cached = LIVE_BUNDLE_CACHE.get(cache_key)
    if not force_refresh and cached and monotonic() - cached[0] <= LIVE_BUNDLE_TTL_SECONDS:
        return cached[1]
    session_factory = get_cached_session_factory()
    data_source = get_cached_data_source()

    with session_factory() as session:
        target_fund_code = fund_code
        if target_fund_code is None:
            position_rows = [row for row in load_user_position_rows(session) if row.get("is_active")]
            watchlist_rows = [row for row in load_watchlist_rows(session) if row.get("is_active")]
            active_codes = {str(row["fund_code"]) for row in position_rows}
            active_codes.update(str(row["fund_code"]) for row in watchlist_rows)
            if active_codes:
                holding_rows = [row for row in load_holding_rows(session) if row["fund_code"] in active_codes]
            else:
                holding_rows = load_holding_rows(session, None)
        else:
            holding_rows = load_holding_rows(session, target_fund_code)
        asset_codes = list(dict.fromkeys(str(row["asset_code"]) for row in holding_rows))
    if not asset_codes:
        return [], "当前没有可用持仓。", False

    if hasattr(data_source, "last_warnings"):
        data_source.last_warnings = []  # type: ignore[attr-defined]
    live_records = data_source.fetch_stock_live_quotes(
        asset_codes=asset_codes,
        sleep_seconds=0.0,
        timeout_seconds=8.0,
    )
    warnings = list(getattr(data_source, "last_warnings", []))
    live_quote_map = {
        record.asset_code: {
            "asset_name": record.asset_name,
            "return_pct": record.return_pct,
            "quote_time": record.quote_time,
            "source": record.source,
        }
        for record in live_records
    }
    used_fallback = False
    with session_factory() as session:
        fallback_map, has_fallback = load_latest_daily_quote_map(session, asset_codes)
    if not live_records and has_fallback:
        live_quote_map = fallback_map
        used_fallback = True
        warnings.append("fallback_to_daily_quotes")
    elif live_records and len(live_quote_map) < len(asset_codes) and has_fallback:
        for asset_code, payload in fallback_map.items():
            live_quote_map.setdefault(asset_code, payload)
        used_fallback = True
        warnings.append("partial_fallback_to_daily_quotes")
    if not live_quote_map:
        return [], "当前抓不到实时行情, 也没有可用缓存。", False

    quote_times = [
        payload.get("quote_time")
        for payload in live_quote_map.values()
        if isinstance(payload.get("quote_time"), datetime)
    ]
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
    payload = (results, summarize_runtime_status(warnings, used_fallback), used_fallback)
    LIVE_BUNDLE_CACHE[cache_key] = (monotonic(), payload)
    return payload


def build_home_rows(results: list) -> list[dict[str, object]]:
    rows = []
    for item in results:
        status_label, status_tone = get_data_status(item.quote_time)
        rows.append(
            {
                "fund_code": item.fund_code,
                "fund_name": item.fund_name,
                "current_estimate": item.current_estimate,
                "current_estimate_text": format_percent(item.current_estimate),
                "estimate_tone": get_tone(item.current_estimate),
                "holding_amount": item.holding_amount,
                "estimated_today_profit": item.estimated_today_profit,
                "estimated_today_profit_text": format_amount(item.estimated_today_profit),
                "profit_tone": get_tone(item.estimated_today_profit),
                "confidence_level": item.confidence_level or "D",
                "quote_time": item.quote_time.strftime("%H:%M:%S") if item.quote_time else "--",
                "status_label": status_label,
                "status_tone": status_tone,
                "latest_real_nav_date": item.latest_real_nav_date.isoformat() if item.latest_real_nav_date else "--",
                "effective_method": item.effective_method,
                "is_holding": item.holding_amount is not None,
                "is_watchlist": False,
            }
        )
    return rows


def sort_home_rows(rows: list[dict[str, object]], sort_key: str) -> list[dict[str, object]]:
    if sort_key == "profit_desc":
        rows.sort(key=lambda row: -999999 if row["estimated_today_profit"] is None else row["estimated_today_profit"], reverse=True)
    elif sort_key == "confidence_desc":
        rows.sort(key=lambda row: CONFIDENCE_RANK.get(row["confidence_level"], 0), reverse=True)
    elif sort_key == "name_asc":
        rows.sort(key=lambda row: (row["fund_name"], row["fund_code"]))
    else:
        rows.sort(key=lambda row: -999999 if row["current_estimate"] is None else row["current_estimate"], reverse=True)
    return rows


def build_detail_context(result) -> dict[str, object]:
    holdings = []
    sorted_holdings = sorted(
        result.holdings,
        key=lambda row: abs(row.contribution_pct or 0.0),
        reverse=True,
    )
    for row in sorted_holdings:
        holdings.append(
            {
                "asset_name": row.asset_name,
                "asset_code": row.asset_code,
                "published_weight": f"{row.published_weight_pct:.2f}%",
                "effective_weight": f"{row.effective_weight_pct:.2f}%",
                "live_return": "--" if row.return_pct is None else f"{row.return_pct:+.2f}%",
                "return_tone": get_tone(row.return_pct),
                "contribution": "--" if row.contribution_pct is None else f"{row.contribution_pct:+.2f}%",
                "contribution_tone": get_tone(row.contribution_pct),
            }
        )
    return {
        "fund_code": result.fund_code,
        "fund_name": result.fund_name,
        "current_estimate_text": format_percent(result.current_estimate),
        "current_estimate_tone": get_tone(result.current_estimate),
        "estimated_today_profit_text": format_amount(result.estimated_today_profit),
        "estimated_today_profit_tone": get_tone(result.estimated_today_profit),
        "confidence_level": result.confidence_level or "D",
        "quote_time": result.quote_time.strftime("%H:%M:%S") if result.quote_time else "--",
        "trade_date": result.trade_date.isoformat(),
        "latest_real_nav_date": result.latest_real_nav_date.isoformat() if result.latest_real_nav_date else "--",
        "is_realtime": result.quote_time.date() == date.today() if result.quote_time else False,
        "holdings": holdings,
        "advanced_rows": [
            ("公开权重合计", f"{result.covered_weight * 100:.2f}%"),
            ("修正权重合计", f"{result.current_scale_factor * result.covered_weight * 100:.2f}%"),
            ("当前缩放系数", f"{result.current_scale_factor:.4f}"),
            ("最近真实净值日期", result.latest_real_nav_date.isoformat() if result.latest_real_nav_date else "--"),
            ("最近误差", "--" if result.latest_mae is None else f"{result.latest_mae:.2%}"),
        ],
    }


@app.get("/")
def index(
    request: Request,
    search: str = Query("", description="搜索基金"),
    sort: str = Query("estimate_desc", description="排序"),
    refresh: str = Query("off", description="自动刷新"),
    view: str = Query("watchlist", description="首页视图"),
    force: int = Query(0, description="强制刷新"),
):
    results, status_message, used_fallback = load_live_estimate_bundle(force_refresh=bool(force))
    rows = build_home_rows(results)
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        watchlist_codes = {
            str(row["fund_code"])
            for row in load_watchlist_rows(session)
            if row.get("is_active")
        }
    for row in rows:
        row["is_watchlist"] = row["fund_code"] in watchlist_codes
    if view == "watchlist":
        watchlist_rows = [row for row in rows if row["is_watchlist"]]
        if watchlist_rows:
            rows = watchlist_rows
    elif view == "holding":
        holding_rows = [row for row in rows if row["is_holding"]]
        if holding_rows:
            rows = holding_rows
    if search.strip():
        keyword = search.strip().lower()
        rows = [
            row for row in rows
            if keyword in row["fund_name"].lower() or keyword in row["fund_code"].lower()
        ]
    rows = sort_home_rows(rows, sort)
    latest_time = max((row["quote_time"] for row in rows), default="--")
    estimate_date = "--"
    data_mode = "实时行情"
    if rows:
        quote_dates = [result.quote_time.date().isoformat() for result in results if result.quote_time is not None]
        if quote_dates:
            estimate_date = max(quote_dates)
    if used_fallback:
        data_mode = "最近收盘缓存"
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "rows": rows,
            "status_message": status_message,
            "used_fallback": used_fallback,
            "search": search,
            "sort": sort,
            "sort_options": SORT_OPTIONS,
            "refresh": refresh,
            "view": view,
            "view_options": VIEW_OPTIONS,
            "force": force,
            "latest_time": latest_time,
            "estimate_date": estimate_date,
            "data_mode": data_mode,
            "today_label": date.today().isoformat(),
        },
    )


@app.get("/fund/{fund_code}")
def fund_detail(request: Request, fund_code: str, debug: int = 0):
    results, status_message, used_fallback = load_live_estimate_bundle(fund_code=fund_code)
    result = results[0] if results else None
    if result is None:
        return templates.TemplateResponse(
            request,
            "fund_detail.html",
            {
                "detail": None,
                "status_message": status_message,
                "used_fallback": used_fallback,
                "debug": debug,
            },
            status_code=404,
        )
    return templates.TemplateResponse(
        request,
        "fund_detail.html",
        {
            "detail": build_detail_context(result),
            "status_message": status_message,
            "used_fallback": used_fallback,
            "debug": debug,
        },
    )


@app.get("/portfolio")
def portfolio(request: Request, saved: int = 0):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        positions = load_user_position_rows(session)
        funds = load_fund_rows(session)
        watchlist_rows = load_watchlist_rows(session)
    return templates.TemplateResponse(
        request,
        "portfolio.html",
        {
            "positions": positions,
            "funds": funds,
            "watchlist_rows": watchlist_rows,
            "saved": saved,
        },
    )


@app.post("/portfolio")
def save_portfolio(
    fund_code: str = Form(...),
    holding_amount: str = Form(""),
    holding_share: str = Form(""),
    cost_nav: str = Form(""),
    platform: str = Form(""),
    is_active: str = Form("1"),
):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        save_user_position_rows(
            session,
            [
                {
                    "fund_code": fund_code,
                    "holding_amount": None if not holding_amount.strip() else float(holding_amount),
                    "holding_share": None if not holding_share.strip() else float(holding_share),
                    "cost_nav": None if not cost_nav.strip() else float(cost_nav),
                    "platform": platform.strip() or None,
                    "is_active": is_active == "1",
                }
            ],
        )
    return RedirectResponse(url="/portfolio?saved=1", status_code=303)


@app.post("/portfolio/watchlist")
def save_watchlist(
    fund_code: str = Form(...),
    is_active: str = Form("1"),
):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        save_watchlist_rows(
            session,
            [
                {
                    "fund_code": fund_code,
                    "is_active": is_active == "1",
                }
            ],
        )
    return RedirectResponse(url="/portfolio?saved=1", status_code=303)


@app.post("/fund/{fund_code}/watch")
def toggle_watch(fund_code: str):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        toggle_watchlist_fund(session, fund_code)
    return RedirectResponse(url=f"/fund/{fund_code}", status_code=303)


@app.get("/manage")
def manage(request: Request, message: str = ""):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        funds = load_fund_rows(session)
        holdings = load_holding_rows(session)
        assets = load_asset_allocation_rows(session)
        industries = load_industry_allocation_rows(session)
    return templates.TemplateResponse(
        request,
        "manage.html",
        {
            "message": message,
            "funds": funds,
            "holdings": holdings[:20],
            "assets": assets[:20],
            "industries": industries[:20],
        },
    )


@app.post("/manage/funds/deactivate")
def manage_fund_deactivate(fund_code: str = Form(...)):
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        ok = deactivate_fund(session, fund_code)
    if ok:
        return RedirectResponse(url=f"/manage?message=已停用基金 {fund_code}", status_code=303)
    return RedirectResponse(url="/manage?message=未找到该基金", status_code=303)


@app.post("/manage/funds")
def manage_funds(csv_text: str = Form(...)):
    rows = parse_csv_text(csv_text)
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        count = save_fund_rows(session, rows)
    return RedirectResponse(url=f"/manage?message=已保存基金 {count} 条", status_code=303)


@app.post("/manage/holdings")
def manage_holdings(csv_text: str = Form(...)):
    rows = parse_csv_text(csv_text)
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        count = save_holding_rows(session, rows)
    return RedirectResponse(url=f"/manage?message=已保存持仓 {count} 条", status_code=303)


@app.post("/manage/assets")
def manage_assets(csv_text: str = Form(...)):
    rows = parse_csv_text(csv_text)
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        count = save_asset_allocation_rows(session, rows)
    return RedirectResponse(url=f"/manage?message=已保存资产配置 {count} 条", status_code=303)


@app.post("/manage/industries")
def manage_industries(csv_text: str = Form(...)):
    rows = parse_csv_text(csv_text)
    session_factory = get_cached_session_factory()
    with session_factory() as session:
        count = save_industry_allocation_rows(session, rows)
    return RedirectResponse(url=f"/manage?message=已保存行业配置 {count} 条", status_code=303)


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


@app.get("/health")
def health():
    return {"ok": True}
