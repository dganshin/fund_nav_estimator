from __future__ import annotations

import json
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

import pandas as pd
import requests

from .base import DataSourceError, FundNavRecord, FundProfile, LiveStockQuoteRecord, StockQuoteRecord
from .code_utils import normalize_asset_code, to_plain_symbol, to_prefixed_symbol


class AKShareDataSource:
    def __init__(self, raw_dir: str | Path | None = None) -> None:
        self.raw_dir = Path(raw_dir) if raw_dir else Path(__file__).resolve().parents[2] / "data" / "raw" / "akshare"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.last_warnings: list[str] = []
        try:
            import akshare as ak
        except ImportError as exc:
            raise DataSourceError("akshare is not installed. Run pip install -r requirements.txt first.") from exc
        self.ak = ak

    def _request_text(self, url: str, timeout: float = 8.0) -> str:
        session = requests.Session()
        session.trust_env = False
        response = session.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        return response.text

    def _fetch_profile_from_eastmoney_suggest(self, fund_code: str) -> tuple[str | None, str | None]:
        try:
            payload = self._request_text(
                f"https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx?m=1&key={fund_code}"
            )
            data = json.loads(payload)
            for item in data.get("Datas", []) or []:
                if str(item.get("CODE", "")).zfill(6) == fund_code:
                    return str(item.get("NAME") or "").strip() or None, None
        except Exception as exc:
            self.last_warnings.append(f"eastmoney suggest profile failed for {fund_code}: {exc}")
        return None, None

    def _fetch_profile_from_fundcode_search(self, fund_code: str) -> tuple[str | None, str | None]:
        try:
            payload = self._request_text("https://fund.eastmoney.com/js/fundcode_search.js", timeout=12.0)
            pattern = rf'\["{fund_code}",\s*"[^"]*",\s*"([^"]+)",\s*"([^"]*)"'
            match = re.search(pattern, payload)
            if match:
                return match.group(1).strip() or None, match.group(2).strip() or None
        except Exception as exc:
            self.last_warnings.append(f"eastmoney fundcode profile failed for {fund_code}: {exc}")
        return None, None

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

    def fetch_latest_fund_navs_bulk(self) -> list[FundNavRecord]:
        """批量拉取当天和昨天所有的场外、场内基金最新净值。"""
        records: list[FundNavRecord] = []
        df_list = []
        try:
            open_df = self.ak.fund_open_fund_daily_em()
            if open_df is not None and not open_df.empty:
                df_list.append(open_df)
        except Exception as exc:
            self.last_warnings.append(f"ak.fund_open_fund_daily_em error: {exc}")
            
        try:
            etf_df = self.ak.fund_etf_fund_daily_em()
            if etf_df is not None and not etf_df.empty:
                df_list.append(etf_df)
        except Exception as exc:
            self.last_warnings.append(f"ak.fund_etf_fund_daily_em error: {exc}")
            
        for df in df_list:
            date1_col, date2_col = None, None
            for col in df.columns:
                if str(col).endswith("-单位净值"):
                    if date1_col is None:
                        date1_col = col
                    elif date2_col is None:
                        date2_col = col
                        
            for d_col in filter(None, [date1_col, date2_col]):
                d_str = d_col.split("-单位净值")[0]
                try:
                    d = date.fromisoformat(d_str)
                except Exception:
                    continue
                acc_col = f"{d_str}-累计净值"
                for _, row in df.iterrows():
                    code = str(row["基金代码"]).zfill(6)
                    val = row.get(d_col)
                    if pd.notna(val) and val != "":
                        try:
                            v = float(val)
                            acc_val = row.get(acc_col) if acc_col in df.columns else None
                            acc_v = float(acc_val) if pd.notna(acc_val) and acc_val != "" else None
                            records.append(
                                FundNavRecord(
                                    trade_date=d,
                                    fund_code=code,
                                    unit_nav=v,
                                    accumulated_nav=acc_v,
                                    source="akshare:daily_em_bulk",
                                )
                            )
                        except ValueError:
                            pass
        return records

    def fetch_stock_daily_quotes(
        self,
        asset_codes: list[str],
        start_date: date,
        end_date: date,
        sleep_seconds: float = 0.0,
    ) -> list[StockQuoteRecord]:
        self.last_warnings = []
        records: list[StockQuoteRecord] = []
        for index, asset_code in enumerate(asset_codes):
            try:
                records.extend(
                    self._fetch_stock_records_with_fallback(
                        asset_code=asset_code,
                        start_date=start_date,
                        end_date=end_date,
                    )
                )
            except Exception as exc:
                self.last_warnings.append(
                    f"Warning: stock quote fetch failed for {asset_code}: {exc}"
                )

            if sleep_seconds > 0 and index < len(asset_codes) - 1:
                time.sleep(sleep_seconds)

        records.sort(key=lambda item: (item.trade_date, item.asset_code))
        return records

    def fetch_stock_live_quotes(
        self,
        asset_codes: list[str],
        sleep_seconds: float = 0.0,
        timeout_seconds: float = 8.0,
    ) -> list[LiveStockQuoteRecord]:
        self.last_warnings = []
        dedup_codes = list(dict.fromkeys(asset_codes))
        # 优先东财实时行情（跟日K同一数据源，盘中收盘涨跌幅口径一致）
        try:
            records = self._fetch_stock_live_quotes_from_eastmoney(
                asset_codes=dedup_codes,
                timeout_seconds=timeout_seconds,
            )
            if records:
                records.sort(key=lambda item: item.asset_code)
                return records
        except Exception as exc:
            self.last_warnings.append(
                f"Warning: eastmoney live quote fetch failed: {exc}"
            )
        # 回退腾讯实时行情
        try:
            records = self._fetch_stock_live_quotes_from_tencent(
                asset_codes=dedup_codes,
                timeout_seconds=timeout_seconds,
            )
            if records:
                records.sort(key=lambda item: item.asset_code)
                return records
        except Exception as exc:
            self.last_warnings.append(
                f"Warning: tencent live quote batch fetch failed: {exc}"
            )

        records: list[LiveStockQuoteRecord] = []
        max_workers = min(6, max(1, len(dedup_codes)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(self._fetch_stock_live_quote, asset_code): asset_code
                for asset_code in dedup_codes
            }
            pending = set(future_map)
            deadline = time.monotonic() + max(timeout_seconds, 1.0)
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, pending = wait(
                    pending,
                    timeout=min(remaining, 1.0),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    asset_code = future_map.get(future, "UNKNOWN")
                    try:
                        record = future.result()
                        if record is not None:
                            records.append(record)
                    except Exception as exc:
                        self.last_warnings.append(
                            f"Warning: live stock quote fetch failed for {asset_code}: {exc}"
                        )
                    if sleep_seconds > 0:
                        time.sleep(sleep_seconds)
            if pending:
                for future in pending:
                    asset_code = future_map.get(future, "UNKNOWN")
                    future.cancel()
                    self.last_warnings.append(
                        f"Warning: live stock quote fetch timed out for {asset_code}."
                    )
        records.sort(key=lambda item: item.asset_code)
        return records

    def _asset_code_to_eastmoney_secid(self, asset_code: str) -> str | None:
        """将资产代码转为东财 secid 格式（1.SH, 0.SZ, 116.HK）。"""
        normalized = normalize_asset_code(asset_code)
        if "." not in normalized:
            return None
        digits, market = normalized.split(".", 1)
        market_map = {"SH": "1", "SZ": "0", "BJ": "0", "HK": "116"}
        prefix = market_map.get(market.upper())
        if prefix is None:
            return None
        return f"{prefix}.{digits}"

    def _fetch_stock_live_quotes_from_eastmoney(
        self,
        asset_codes: list[str],
        timeout_seconds: float,
    ) -> list[LiveStockQuoteRecord]:
        """东财实时行情，跟日K同一数据源，盘中收盘涨跌幅口径一致。"""
        if not asset_codes:
            return []
        secids = []
        code_map: dict[str, str] = {}
        for code in asset_codes:
            secid = self._asset_code_to_eastmoney_secid(code)
            if secid is None:
                continue
            secids.append(secid)
            code_map[secid] = code
        if not secids:
            return []
        url = "http://push2.eastmoney.com/api/qt/stock/get"
        fields = "f43,f57,f58,f60,f170"
        records: list[LiveStockQuoteRecord] = []
        now = datetime.now()
        import requests as req
        for start in range(0, len(secids), 50):
            chunk = secids[start:start + 50]
            try:
                r = req.get(
                    url,
                    params={"secids": ",".join(chunk), "fields": fields},
                    timeout=max(timeout_seconds, 2.0),
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                r.raise_for_status()
                payload = r.json()
            except Exception:
                continue
            stocks = payload.get("data", {})
            if not stocks:
                continue
            # 单只返回 dict，批量返回 list
            if isinstance(stocks, dict):
                stocks = [stocks]
            for stock in stocks:
                if not isinstance(stock, dict):
                    continue
                secid = stock.get("f57", "")
                orig_code = code_map.get(secid)
                if orig_code is None:
                    # 反向匹配：东财可能返回带市场前缀的代码
                    plain = str(secid).replace("1.", "").replace("0.", "").replace("116.", "")
                    normalized = normalize_asset_code(plain)
                    orig_code = normalized
                latest = stock.get("f43")
                prev_close = stock.get("f60")
                change_pct = stock.get("f170")
                if latest is None or prev_close in (None, 0) or change_pct is None:
                    continue
                records.append(LiveStockQuoteRecord(
                    trade_date=now.date(),
                    quote_time=now,
                    asset_code=normalize_asset_code(orig_code),
                    asset_name=str(stock.get("f58", orig_code)),
                    return_pct=(float(change_pct) / 100.0),
                    source="eastmoney:qt_live",
                ))
        return records

    def _fetch_stock_live_quotes_from_tencent(
        self,
        asset_codes: list[str],
        timeout_seconds: float,
    ) -> list[LiveStockQuoteRecord]:
        if not asset_codes:
            return []
        symbols = []
        for asset_code in asset_codes:
            try:
                symbols.append(to_prefixed_symbol(asset_code))
            except ValueError as e:
                self.last_warnings.append(f"Warning: skip tencent fetch for unsupported code: {asset_code}")
                continue
        if not symbols:
            return []
        records: list[LiveStockQuoteRecord] = []
        for start in range(0, len(symbols), 50):
            chunk = symbols[start:start + 50]
            url = f"http://qt.gtimg.cn/q={','.join(chunk)}"
            opener = build_opener(ProxyHandler({}))
            opener.addheaders = [
                ("User-Agent", "Mozilla/5.0"),
                ("Referer", "http://gu.qq.com/"),
            ]
            request = Request(url)
            with opener.open(request, timeout=max(timeout_seconds, 1.0)) as response:
                body = response.read().decode("gbk", errors="ignore")
            records.extend(self._parse_tencent_live_quote_response(body))
        return records

    def _parse_tencent_live_quote_response(self, body: str) -> list[LiveStockQuoteRecord]:
        records: list[LiveStockQuoteRecord] = []
        for line in body.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            raw_value = line.split("=", 1)[1].strip().strip(";").strip('"')
            if not raw_value:
                continue
            parts = raw_value.split("~")
            if len(parts) < 33:
                continue
            asset_name = parts[1].strip()
            plain_code = parts[2].strip()
            latest_price = self._to_float(parts[3])
            prev_close = self._to_float(parts[4])
            quote_time_raw = parts[30].strip()
            if not plain_code or latest_price is None or prev_close in (None, 0):
                continue
            try:
                quote_time = datetime.strptime(quote_time_raw, "%Y%m%d%H%M%S")
            except ValueError:
                quote_time = datetime.now()
            normalized_code = normalize_asset_code(plain_code)
            records.append(
                LiveStockQuoteRecord(
                    trade_date=quote_time.date(),
                    quote_time=quote_time,
                    asset_code=normalized_code,
                    asset_name=asset_name or normalized_code,
                    return_pct=(latest_price / prev_close) - 1.0,
                    source="tencent:qt_live",
                )
            )
        return records

    def fetch_fund_profile(self, fund_code: str) -> FundProfile:
        """拉取基金基础信息：名称、最新净值、净值日期。失败时返回降级结果，不报错。"""
        fund_name = fund_code
        fund_type = "equity"
        latest_unit_nav: float | None = None
        latest_nav_date: date | None = None
        accumulated_nav: float | None = None
        try:
            df = self._call_fund_open_info(fund_code=fund_code, indicator="单位净值走势")
            if df is not None and not df.empty and "净值日期" in df.columns and "单位净值" in df.columns:
                df = df.copy()
                df["净值日期"] = pd.to_datetime(df["净值日期"]).dt.date
                df = df.sort_values("净值日期", ascending=False)
                row = df.iloc[0]
                latest_nav_date = row["净值日期"]
                latest_unit_nav = float(row["单位净值"]) if pd.notna(row["单位净值"]) else None
        except Exception:
            pass
        try:
            acc_df = self._call_fund_open_info(fund_code=fund_code, indicator="累计净值走势")
            if acc_df is not None and not acc_df.empty and "净值日期" in acc_df.columns and "累计净值" in acc_df.columns:
                acc_df = acc_df.copy()
                acc_df["净值日期"] = pd.to_datetime(acc_df["净值日期"]).dt.date
                acc_df = acc_df.sort_values("净值日期", ascending=False)
                acc_row = acc_df.iloc[0]
                accumulated_nav = float(acc_row["累计净值"]) if pd.notna(acc_row["累计净值"]) else None
        except Exception:
            pass
        # 尝试拉基金名称
        try:
            info_df = self.ak.fund_open_fund_info_em(symbol=fund_code, indicator="基本概况") if hasattr(self.ak, "fund_open_fund_info_em") else None
            if info_df is not None and not info_df.empty:
                for _, r in info_df.iterrows():
                    item_val = str(r.get("item", "") or "")
                    if "基金简称" in item_val or "基金名称" in item_val:
                        fund_name = str(r.get("value", fund_code) or fund_code).strip() or fund_code
                        break
        except Exception:
            pass
        if fund_name == fund_code:
            try:
                name_df = self.ak.fund_name_em() if hasattr(self.ak, "fund_name_em") else None
                if name_df is not None and not name_df.empty and {"基金代码", "基金简称"}.issubset(name_df.columns):
                    matched = name_df[name_df["基金代码"].astype(str).str.zfill(6) == fund_code]
                    if not matched.empty:
                        fund_name = str(matched.iloc[0]["基金简称"] or fund_code).strip() or fund_code
                        if "基金类型" in matched.columns:
                            fund_type = str(matched.iloc[0]["基金类型"] or fund_type).strip() or fund_type
            except Exception:
                pass
        if fund_name == fund_code:
            fallback_name, fallback_type = self._fetch_profile_from_eastmoney_suggest(fund_code)
            if fallback_name:
                fund_name = fallback_name
            if fallback_type:
                fund_type = fallback_type
        if fund_name == fund_code or fund_type == "equity":
            fallback_name, fallback_type = self._fetch_profile_from_fundcode_search(fund_code)
            if fallback_name:
                fund_name = fallback_name
            if fallback_type:
                fund_type = fallback_type
        return FundProfile(
            fund_code=fund_code,
            fund_name=fund_name,
            fund_type=fund_type,
            market="CN",
            latest_unit_nav=latest_unit_nav,
            latest_nav_date=latest_nav_date,
            accumulated_nav=accumulated_nav,
            source="akshare",
        )

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

    def fetch_fund_public_holdings(self, fund_code: str) -> list[dict[str, object]]:
        """拉最新公开持仓, 只代表季报/公开前十大, 不是实时持仓。"""
        years = [date.today().year, date.today().year - 1]
        rows: list[dict[str, object]] = []
        for year in years:
            try:
                rows.extend(self.fetch_fund_holdings(fund_code, year=year))
            except Exception:
                continue
            if rows:
                break
        return rows

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

    def _fetch_stock_records_with_fallback(
        self,
        asset_code: str,
        start_date: date,
        end_date: date,
    ) -> list[StockQuoteRecord]:
        if self._looks_like_hk_code(asset_code):
            try:
                return self._fetch_hk_stock_records(
                    asset_code=asset_code,
                    start_date=start_date,
                    end_date=end_date,
                )
            except Exception as exc:
                self.last_warnings.append(
                    f"Warning: hk stock history failed for {asset_code}: {exc}"
                )
        if self._looks_like_etf_code(asset_code):
            try:
                return self._fetch_etf_fund_records(
                    asset_code=asset_code,
                    start_date=start_date,
                    end_date=end_date,
                )
            except Exception as exc:
                self.last_warnings.append(
                    f"Warning: etf quote fetch failed for {asset_code}: {exc}"
                )
        eastmoney_error: Exception | None = None
        try:
            return self._fetch_stock_records_from_eastmoney(
                asset_code=asset_code,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as exc:
            eastmoney_error = exc
            self.last_warnings.append(
                f"Warning: eastmoney stock history failed for {asset_code}, fallback to sina daily. Error: {exc}"
            )

        try:
            return self._fetch_stock_records_from_sina(
                asset_code=asset_code,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as exc:
            raise DataSourceError(
                f"AKShare fetch stock quotes failed for {asset_code}. "
                f"eastmoney_error={eastmoney_error}; sina_error={exc}"
            ) from exc

    def _looks_like_etf_code(self, asset_code: str) -> bool:
        plain = to_plain_symbol(asset_code)
        return len(plain) == 6 and plain.startswith(("1", "5"))

    def _looks_like_hk_code(self, asset_code: str) -> bool:
        normalized = normalize_asset_code(asset_code)
        return normalized.endswith(".HK")

    def _fetch_hk_stock_records(
        self,
        asset_code: str,
        start_date: date,
        end_date: date,
    ) -> list[StockQuoteRecord]:
        plain = to_plain_symbol(asset_code).zfill(5)
        try:
            quote_df = self.ak.stock_hk_hist(
                symbol=plain,
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="",
            )
        except Exception:
            quote_df = self.ak.stock_hk_daily(symbol=plain, adjust="")
            if quote_df is not None and not quote_df.empty:
                quote_df = quote_df.copy()
                quote_df["date"] = pd.to_datetime(quote_df["date"]).dt.date
                quote_df = quote_df.sort_values("date")
                quote_df["prev_close"] = quote_df["close"].shift(1)
                quote_df["涨跌幅"] = (quote_df["close"] / quote_df["prev_close"] - 1.0) * 100
                quote_df = quote_df.rename(columns={"date": "日期"})
                quote_df = quote_df[
                    (quote_df["日期"] >= start_date)
                    & (quote_df["日期"] <= end_date)
                ]
        if quote_df is None or quote_df.empty:
            return []
        self._cache_dataframe(
            quote_df,
            f"stock_hk_quotes_{plain}_{start_date}_{end_date}.csv",
        )
        expected_columns = {"日期", "涨跌幅"}
        if not expected_columns.issubset(set(quote_df.columns)):
            raise DataSourceError(
                f"AKShare hk stock quote columns changed for {asset_code}. Actual columns: {list(quote_df.columns)}"
            )
        quote_df = quote_df.copy()
        quote_df["日期"] = pd.to_datetime(quote_df["日期"]).dt.date
        normalized_code = normalize_asset_code(plain)
        records: list[StockQuoteRecord] = []
        for _, row in quote_df.iterrows():
            if pd.isna(row["涨跌幅"]):
                continue
            records.append(
                StockQuoteRecord(
                    trade_date=row["日期"],
                    asset_code=normalized_code,
                    asset_name=normalized_code,
                    return_pct=float(row["涨跌幅"]) / 100.0,
                    source="akshare:hk_hist",
                )
            )
        return records

    def _fetch_etf_fund_records(
        self,
        asset_code: str,
        start_date: date,
        end_date: date,
    ) -> list[StockQuoteRecord]:
        plain = to_plain_symbol(asset_code)
        df = self.ak.fund_etf_fund_info_em(
            fund=plain,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return []
        self._cache_dataframe(df, f"etf_nav_{plain}_{start_date}_{end_date}.csv")
        expected = {"净值日期", "日增长率"}
        if not expected.issubset(set(df.columns)):
            raise DataSourceError(
                f"AKShare etf nav columns changed for {plain}. Actual columns: {list(df.columns)}"
            )
        df = df.copy()
        df["净值日期"] = pd.to_datetime(df["净值日期"]).dt.date
        records: list[StockQuoteRecord] = []
        for _, row in df.iterrows():
            if pd.isna(row["净值日期"]) or pd.isna(row["日增长率"]):
                continue
            records.append(
                StockQuoteRecord(
                    trade_date=row["净值日期"],
                    asset_code=normalize_asset_code(plain),
                    asset_name=normalize_asset_code(plain),
                    return_pct=float(row["日增长率"]) / 100.0,
                    source="akshare:etf_nav",
                )
            )
        return records

    def _fetch_stock_records_from_eastmoney(
        self,
        asset_code: str,
        start_date: date,
        end_date: date,
    ) -> list[StockQuoteRecord]:
        quote_df = self.ak.stock_zh_a_hist(
            symbol=to_plain_symbol(asset_code),
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="",
        )
        if quote_df is None or quote_df.empty:
            return []

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
        records: list[StockQuoteRecord] = []
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
        return records

    def _fetch_stock_records_from_sina(
        self,
        asset_code: str,
        start_date: date,
        end_date: date,
    ) -> list[StockQuoteRecord]:
        lookup_start = start_date - timedelta(days=10)
        quote_df = self.ak.stock_zh_a_daily(
            symbol=to_prefixed_symbol(asset_code),
            start_date=lookup_start.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="",
        )
        if quote_df is None or quote_df.empty:
            return []

        self._cache_dataframe(
            quote_df,
            f"stock_quotes_sina_{normalize_asset_code(asset_code)}_{start_date}_{end_date}.csv",
        )

        quote_df = quote_df.copy()
        if "date" in quote_df.columns:
            quote_df["date"] = pd.to_datetime(quote_df["date"]).dt.date
        else:
            quote_df = quote_df.reset_index()
            if "date" not in quote_df.columns:
                raise DataSourceError(
                    f"AKShare sina stock quote columns changed for {asset_code}. Actual columns: {list(quote_df.columns)}"
                )
            quote_df["date"] = pd.to_datetime(quote_df["date"]).dt.date

        expected_columns = {"date", "close"}
        if not expected_columns.issubset(set(quote_df.columns)):
            raise DataSourceError(
                f"AKShare sina stock quote columns changed for {asset_code}. Actual columns: {list(quote_df.columns)}"
            )

        quote_df = quote_df.sort_values("date").copy()
        quote_df["prev_close"] = quote_df["close"].shift(1)
        quote_df["return_pct"] = quote_df["close"] / quote_df["prev_close"] - 1
        quote_df = quote_df[
            (quote_df["date"] >= start_date)
            & (quote_df["date"] <= end_date)
        ]

        normalized_code = normalize_asset_code(asset_code)
        records: list[StockQuoteRecord] = []
        for _, row in quote_df.iterrows():
            if pd.isna(row["return_pct"]):
                continue
            records.append(
                StockQuoteRecord(
                    trade_date=row["date"],
                    asset_code=normalized_code,
                    asset_name=normalized_code,
                    return_pct=float(row["return_pct"]),
                    source="akshare:sina_daily",
                )
            )
        return records

    def _fetch_stock_live_quote(self, asset_code: str) -> LiveStockQuoteRecord | None:
        symbol = to_prefixed_symbol(asset_code)
        quote_df = self.ak.stock_individual_spot_xq(symbol=symbol)
        if quote_df is None or quote_df.empty:
            return None

        self._cache_dataframe(
            quote_df,
            f"stock_live_{normalize_asset_code(asset_code)}_{date.today().isoformat()}.csv",
        )
        value_map = {
            str(row["item"]): row["value"]
            for _, row in quote_df.iterrows()
        }
        latest_price = self._to_float(value_map.get("现价"))
        prev_close = self._to_float(value_map.get("昨收"))
        if latest_price is None or prev_close in (None, 0):
            raise DataSourceError(
                f"AKShare live quote fields missing for {asset_code}. Actual items: {list(value_map.keys())}"
            )

        change_pct = (latest_price / prev_close - 1.0) * 100.0

        quote_time_raw = value_map.get("时间")
        quote_time = datetime.now()
        if quote_time_raw is not None:
            try:
                parsed_time = pd.to_datetime(quote_time_raw).to_pydatetime()
                if parsed_time.date() == date.today():
                    quote_time = parsed_time
                else:
                    self.last_warnings.append(
                        f"Warning: live quote timestamp for {asset_code} is stale ({parsed_time}), use local fetch time instead."
                    )
            except Exception:
                quote_time = datetime.now()

        name = str(value_map.get("名称") or normalize_asset_code(asset_code))
        return LiveStockQuoteRecord(
            trade_date=quote_time.date(),
            quote_time=quote_time,
            asset_code=normalize_asset_code(asset_code),
            asset_name=name,
            return_pct=float(change_pct) / 100.0,
            source="akshare:xq_live",
        )

    def _to_float(self, value) -> float | None:
        if value is None or pd.isna(value):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
