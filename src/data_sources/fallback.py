from __future__ import annotations

from datetime import date

from .base import DataSourceError, FundNavRecord, FundProfile, LiveStockQuoteRecord, StockQuoteRecord


class FallbackDataSource:
    """按优先级依次尝试多个数据源，返回第一个成功结果。"""

    def __init__(self, sources: list) -> None:
        if not sources:
            raise DataSourceError("FallbackDataSource requires at least one source")
        self.sources = sources

    @property
    def last_warnings(self) -> list[str]:
        warnings: list[str] = []
        for source in self.sources:
            warnings.extend(getattr(source, "last_warnings", []))
        return warnings

    @last_warnings.setter
    def last_warnings(self, value: list[str]) -> None:
        pass

    @property
    def ak(self):
        # 暴露 ak 对象给 _fetch_xq_basic_info 等直接调用方
        for source in self.sources:
            ak = getattr(source, "ak", None)
            if ak is not None:
                return ak
        return None

    @property
    def raw_dir(self):
        for source in self.sources:
            if hasattr(source, "raw_dir"):
                return source.raw_dir
        return None

    def _try(self, method: str, *args, **kwargs):
        errors: list[str] = []
        for source in self.sources:
            try:
                fn = getattr(source, method)
                return fn(*args, **kwargs)
            except Exception as exc:
                errors.append(f"[{type(source).__name__}] {exc}")
        raise DataSourceError(f"All sources failed for {method}: " + "; ".join(errors))

    # ── Delegated methods ─────────────────────────────────────────────────

    def fetch_fund_profile(self, fund_code: str) -> FundProfile:
        return self._try("fetch_fund_profile", fund_code)

    def fetch_fund_navs(
        self,
        fund_code: str,
        start_date: date,
        end_date: date,
    ) -> list[FundNavRecord]:
        return self._try("fetch_fund_navs", fund_code, start_date, end_date)

    def fetch_stock_daily_quotes(
        self,
        asset_codes: list[str],
        start_date: date,
        end_date: date,
        sleep_seconds: float = 0.0,
    ) -> list[StockQuoteRecord]:
        return self._try("fetch_stock_daily_quotes", asset_codes, start_date, end_date, sleep_seconds)

    def fetch_stock_live_quotes(
        self,
        asset_codes: list[str],
        sleep_seconds: float = 0.0,
        timeout_seconds: float = 8.0,
    ) -> list[LiveStockQuoteRecord]:
        return self._try("fetch_stock_live_quotes", asset_codes, sleep_seconds, timeout_seconds)

    def fetch_fund_holdings(
        self,
        fund_code: str,
        year: int | None = None,
    ) -> list[dict[str, object]]:
        return self._try("fetch_fund_holdings", fund_code, year)

    def fetch_fund_asset_allocation(
        self,
        fund_code: str,
        report_date: date | None = None,
    ) -> list[dict[str, object]]:
        return self._try("fetch_fund_asset_allocation", fund_code, report_date)

    def fetch_fund_public_holdings(self, fund_code: str) -> list[dict[str, object]]:
        return self._try("fetch_fund_public_holdings", fund_code)
