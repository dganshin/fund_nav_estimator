from __future__ import annotations

from datetime import date, datetime, time, timedelta


QDII_MIN_QUOTE_COVERAGE = 0.70
CN_MARKETS = {"SH", "SZ", "BJ"}
FOREIGN_MARKETS = {"US", "HK", "JP", "OTHER"}


def is_qdii_text(*values: object) -> bool:
    text = " ".join(str(v or "") for v in values).upper()
    return any(key in text for key in ("QDII", "海外", "全球", "美股", "US", "GLOBAL"))


def is_qdii_fund(fund) -> bool:
    if fund is None:
        return False
    return is_qdii_text(
        getattr(fund, "fund_type", ""),
        getattr(fund, "market", ""),
        getattr(fund, "fund_name", ""),
    )


def classify_asset_market(asset_code: str) -> tuple[str, str]:
    code = str(asset_code or "").strip().upper()
    if "." in code:
        _, market = code.rsplit(".", 1)
        market = market.upper()
        if market in CN_MARKETS:
            return "CN", "CNY"
        if market == "HK":
            return "HK", "HKD"
        if market in {"JP", "T"}:
            return "JP", "JPY"
        if market in {"US", "NASDAQ", "NYSE"}:
            return "US", "USD"
    if code.startswith("JP") and len(code) >= 10:
        return "JP", "JPY"
    if code.isalpha() and 1 <= len(code) <= 5:
        return "US", "USD"
    return "OTHER", "USD"


def is_cn_market(asset_code: str) -> bool:
    return classify_asset_market(asset_code)[0] == "CN"


def _us_open_time(now: datetime) -> time:
    # 简化处理美国夏令时: 3-10 月按北京时间 21:30 开盘, 其他月份 22:30。
    return time(21, 30) if 3 <= now.month <= 10 else time(22, 30)


def is_us_trading_time(now: datetime) -> bool:
    current = now.time()
    return current >= _us_open_time(now) or current < time(4, 0)


def is_cn_day_session(now: datetime) -> bool:
    current = now.time()
    return time(9, 30) <= current <= time(15, 0)


def qdii_status(
    now: datetime,
    quote_coverage: float,
    foreign_total_weight: float,
    foreign_covered_weight: float,
) -> tuple[str, str, str]:
    if quote_coverage < QDII_MIN_QUOTE_COVERAGE:
        if is_cn_day_session(now) and foreign_covered_weight <= 0 < foreign_total_weight:
            return (
                "海外未开盘",
                "海外市场未完整交易，QDII 完整估值不可用，仅显示已覆盖部分。",
                "cn_close",
            )
        return (
            "海外行情覆盖不足",
            "海外行情覆盖不足，QDII 完整估值不可用，仅显示已覆盖部分。",
            "foreign_live" if is_us_trading_time(now) else "cn_close",
        )
    if is_us_trading_time(now):
        return "海外交易中", "美股交易中，当前为夜盘估值。", "foreign_live"
    if now.time() < time(15, 0):
        return "海外已收盘", "美股已收盘，当前为昨夜收盘估值，等待官方净值确认。", "foreign_close"
    return "等待官方净值", "海外市场已收盘，等待官方净值确认。", "foreign_close"


def qdii_snapshot_valuation_date(now: datetime, snapshot_type: str | None) -> date:
    # 次日早上的 foreign_close 属于前一估值日。
    if snapshot_type == "foreign_close" and now.time() < time(15, 0):
        return now.date() - timedelta(days=1)
    return now.date()
