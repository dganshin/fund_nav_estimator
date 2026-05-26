from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


class DataSourceError(RuntimeError):
    pass


@dataclass
class FundProfile:
    fund_code: str
    fund_name: str
    fund_type: str
    market: str
    latest_unit_nav: float | None
    latest_nav_date: date | None
    accumulated_nav: float | None
    source: str


@dataclass
class FundNavRecord:
    trade_date: date
    fund_code: str
    unit_nav: float
    accumulated_nav: float | None
    source: str


@dataclass
class StockQuoteRecord:
    trade_date: date
    asset_code: str
    asset_name: str
    return_pct: float
    source: str


@dataclass
class LiveStockQuoteRecord:
    trade_date: date
    quote_time: datetime
    asset_code: str
    asset_name: str
    return_pct: float
    source: str


class DataSource(Protocol):
    def fetch_fund_navs(
        self,
        fund_code: str,
        start_date: date,
        end_date: date,
    ) -> list[FundNavRecord]:
        ...

    def fetch_stock_daily_quotes(
        self,
        asset_codes: list[str],
        start_date: date,
        end_date: date,
        sleep_seconds: float = 0.0,
    ) -> list[StockQuoteRecord]:
        ...

    def fetch_stock_live_quotes(
        self,
        asset_codes: list[str],
        sleep_seconds: float = 0.0,
        timeout_seconds: float = 8.0,
    ) -> list[LiveStockQuoteRecord]:
        ...

    def fetch_fund_holdings(
        self,
        fund_code: str,
        year: int | None = None,
    ) -> list[dict[str, object]]:
        ...

    def fetch_fund_asset_allocation(
        self,
        fund_code: str,
        report_date: date | None = None,
    ) -> list[dict[str, object]]:
        ...
