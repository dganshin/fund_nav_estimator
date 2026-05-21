from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pandas as pd

from .base import DataSourceError, FundNavRecord, StockQuoteRecord
from .code_utils import normalize_asset_code, to_plain_symbol


class AKShareDataSource:
    def __init__(self, raw_dir: str | Path | None = None) -> None:
        self.raw_dir = Path(raw_dir) if raw_dir else Path(__file__).resolve().parents[2] / "data" / "raw" / "akshare"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataSourceError("akshare is not installed. Run pip install -r requirements.txt first.") from exc
        self.ak = ak

    def fetch_fund_navs(
        self,
        fund_code: str,
        start_date: date,
        end_date: date,
    ) -> list[FundNavRecord]:
        try:
            unit_df = self._call_fund_open_info(
                fund_code=fund_code,
                indicator="单位净值走势",
            )
            accumulated_df = self._call_fund_open_info(
                fund_code=fund_code,
                indicator="累计净值走势",
            )
        except Exception as exc:
            raise DataSourceError(f"AKShare fetch fund navs failed for {fund_code}: {exc}") from exc

        if unit_df is None or unit_df.empty:
            return []

        self._cache_dataframe(unit_df, f"fund_nav_unit_{fund_code}_{start_date}_{end_date}.csv")
        if accumulated_df is not None and not accumulated_df.empty:
            self._cache_dataframe(accumulated_df, f"fund_nav_acc_{fund_code}_{start_date}_{end_date}.csv")

        expected_unit_columns = {"净值日期", "单位净值"}
        if not expected_unit_columns.issubset(set(unit_df.columns)):
            raise DataSourceError(
                f"AKShare fund nav columns changed for {fund_code}. Actual columns: {list(unit_df.columns)}"
            )

        unit_df = unit_df.copy()
        unit_df["净值日期"] = pd.to_datetime(unit_df["净值日期"]).dt.date
        unit_df = unit_df[
            (unit_df["净值日期"] >= start_date) & (unit_df["净值日期"] <= end_date)
        ]

        accumulated_map: dict[date, float] = {}
        if accumulated_df is not None and not accumulated_df.empty:
            expected_acc_columns = {"净值日期", "累计净值"}
            if not expected_acc_columns.issubset(set(accumulated_df.columns)):
                raise DataSourceError(
                    f"AKShare accumulated nav columns changed for {fund_code}. Actual columns: {list(accumulated_df.columns)}"
                )
            accumulated_df = accumulated_df.copy()
            accumulated_df["净值日期"] = pd.to_datetime(accumulated_df["净值日期"]).dt.date
            accumulated_map = {
                row["净值日期"]: float(row["累计净值"])
                for _, row in accumulated_df.iterrows()
                if pd.notna(row["累计净值"])
            }

        records: list[FundNavRecord] = []
        for _, row in unit_df.iterrows():
            if pd.isna(row["单位净值"]):
                continue
            trade_date = row["净值日期"]
            records.append(
                FundNavRecord(
                    trade_date=trade_date,
                    fund_code=fund_code,
                    unit_nav=float(row["单位净值"]),
                    accumulated_nav=accumulated_map.get(trade_date),
                    source="akshare",
                )
            )
        return sorted(records, key=lambda item: item.trade_date)

    def fetch_stock_daily_quotes(
        self,
        asset_codes: list[str],
        start_date: date,
        end_date: date,
        sleep_seconds: float = 0.0,
    ) -> list[StockQuoteRecord]:
        records: list[StockQuoteRecord] = []
        for index, asset_code in enumerate(asset_codes):
            plain_symbol = to_plain_symbol(asset_code)
            try:
                quote_df = self.ak.stock_zh_a_hist(
                    symbol=plain_symbol,
                    period="daily",
                    start_date=start_date.strftime("%Y%m%d"),
                    end_date=end_date.strftime("%Y%m%d"),
                    adjust="",
                )
            except Exception as exc:
                raise DataSourceError(f"AKShare fetch stock quotes failed for {asset_code}: {exc}") from exc

            if quote_df is None or quote_df.empty:
                if sleep_seconds > 0 and index < len(asset_codes) - 1:
                    time.sleep(sleep_seconds)
                continue

            self._cache_dataframe(
                quote_df,
                f"stock_quotes_{normalize_asset_code(asset_code)}_{start_date}_{end_date}.csv",
            )

            expected_columns = {"日期", "涨跌幅"}
            if not expected_columns.issubset(set(quote_df.columns)):
                raise DataSourceError(
                    f"AKShare stock quote columns changed for {asset_code}. Actual columns: {list(quote_df.columns)}"
                )

            quote_df = quote_df.copy()
            quote_df["日期"] = pd.to_datetime(quote_df["日期"]).dt.date
            name = self._extract_asset_name(quote_df, asset_code)
            normalized_code = normalize_asset_code(asset_code)
            for _, row in quote_df.iterrows():
                if pd.isna(row["涨跌幅"]):
                    continue
                records.append(
                    StockQuoteRecord(
                        trade_date=row["日期"],
                        asset_code=normalized_code,
                        asset_name=name,
                        return_pct=float(row["涨跌幅"]) / 100.0,
                        source="akshare",
                    )
                )

            if sleep_seconds > 0 and index < len(asset_codes) - 1:
                time.sleep(sleep_seconds)

        records.sort(key=lambda item: (item.trade_date, item.asset_code))
        return records

    def fetch_fund_holdings(
        self,
        fund_code: str,
        year: int | None = None,
    ) -> list[dict[str, object]]:
        if year is None:
            raise DataSourceError("fetch_fund_holdings requires a year.")
        try:
            df = self.ak.fund_portfolio_hold_em(symbol=fund_code, date=str(year))
        except Exception as exc:
            raise DataSourceError(f"AKShare fetch fund holdings failed for {fund_code}: {exc}") from exc
        if df is None or df.empty:
            return []
        self._cache_dataframe(df, f"fund_holdings_{fund_code}_{year}.csv")
        return df.to_dict(orient="records")

    def fetch_fund_asset_allocation(
        self,
        fund_code: str,
        report_date: date | None = None,
    ) -> list[dict[str, object]]:
        year = report_date.year if report_date else date.today().year
        try:
            df = self.ak.fund_portfolio_industry_allocation_em(symbol=fund_code, date=str(year))
        except Exception as exc:
            raise DataSourceError(f"AKShare fetch fund asset allocation failed for {fund_code}: {exc}") from exc
        if df is None or df.empty:
            return []
        self._cache_dataframe(df, f"fund_industry_alloc_{fund_code}_{year}.csv")
        return df.to_dict(orient="records")

    def _cache_dataframe(self, dataframe: pd.DataFrame, filename: str) -> None:
        dataframe.to_csv(self.raw_dir / filename, index=False, encoding="utf-8-sig")

    def _extract_asset_name(self, dataframe: pd.DataFrame, asset_code: str) -> str:
        for candidate in ("股票名称", "名称"):
            if candidate in dataframe.columns:
                first_value = dataframe[candidate].dropna()
                if not first_value.empty:
                    return str(first_value.iloc[0])
        return normalize_asset_code(asset_code)

    def _call_fund_open_info(self, fund_code: str, indicator: str) -> pd.DataFrame:
        try:
            return self.ak.fund_open_fund_info_em(
                fund=fund_code,
                indicator=indicator,
            )
        except TypeError:
            return self.ak.fund_open_fund_info_em(
                symbol=fund_code,
                indicator=indicator,
            )
