from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from .base import DataSourceError, FundNavRecord, FundProfile, LiveStockQuoteRecord, StockQuoteRecord
from .code_utils import normalize_asset_code


class EfinanceDataSource:
    def __init__(self, raw_dir: str | Path | None = None) -> None:
        self.raw_dir = Path(raw_dir) if raw_dir else Path(__file__).resolve().parents[2] / "data" / "raw" / "efinance"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.last_warnings: list[str] = []
        try:
            import efinance as ef
        except ImportError as exc:
            raise DataSourceError("efinance is not installed. Run pip install efinance first.") from exc
        self.ef = ef

    # ── Fund Profile ──────────────────────────────────────────────────────

    def fetch_fund_profile(self, fund_code: str) -> FundProfile:
        try:
            info = self.ef.fund.get_base_info(fund_code)
        except Exception as exc:
            raise DataSourceError(f"efinance fetch_fund_profile failed for {fund_code}: {exc}") from exc

        if info is None or (hasattr(info, "empty") and info.empty):
            raise DataSourceError(f"efinance fetch_fund_profile returned empty for {fund_code}")

        fund_name = str(info.get("基金简称", fund_code))
        fund_type_raw = str(info.get("简介", "equity"))
        fund_type = "equity"
        for keyword, mapped in [("混合", "混合型"), ("股票", "股票型"), ("债券", "债券型"), ("货币", "货币型"),
                                ("指数", "指数型"), ("QDII", "QDII"), ("ETF", "ETF"), ("联接", "ETF联接")]:
            if keyword in fund_type_raw:
                fund_type = mapped
                break

        nav_raw = info.get("最新净值")
        latest_unit_nav = float(nav_raw) if nav_raw is not None and pd.notna(nav_raw) else None
        nav_date_raw = info.get("净值更新日期")
        latest_nav_date = None
        if nav_date_raw is not None and pd.notna(nav_date_raw):
            try:
                latest_nav_date = pd.Timestamp(str(nav_date_raw)).date()
            except Exception:
                pass

        return FundProfile(
            fund_code=fund_code,
            fund_name=fund_name,
            fund_type=fund_type,
            market="CN",
            latest_unit_nav=latest_unit_nav,
            latest_nav_date=latest_nav_date,
            accumulated_nav=None,
            source="efinance",
        )

    # ── Fund NAVs ─────────────────────────────────────────────────────────

    def fetch_fund_navs(
        self,
        fund_code: str,
        start_date: date,
        end_date: date,
    ) -> list[FundNavRecord]:
        try:
            df = self.ef.fund.get_quote_history(fund_code)
        except Exception as exc:
            raise DataSourceError(f"efinance fetch_fund_navs failed for {fund_code}: {exc}") from exc

        if df is None or df.empty:
            return []

        records: list[FundNavRecord] = []
        for _, row in df.iterrows():
            try:
                trade_date_val = pd.Timestamp(row["日期"]).date()
            except Exception:
                continue
            if trade_date_val < start_date or trade_date_val > end_date:
                continue
            try:
                records.append(FundNavRecord(
                    trade_date=trade_date_val,
                    fund_code=fund_code,
                    unit_nav=float(row["单位净值"]),
                    accumulated_nav=float(row.get("累计净值", 0)) if pd.notna(row.get("累计净值")) else None,
                    source="efinance",
                ))
            except (ValueError, KeyError):
                continue
        return records

    # ── Stock Daily Quotes ────────────────────────────────────────────────

    def fetch_stock_daily_quotes(
        self,
        asset_codes: list[str],
        start_date: date,
        end_date: date,
        sleep_seconds: float = 0.0,
    ) -> list[StockQuoteRecord]:
        from .code_utils import to_plain_symbol

        records: list[StockQuoteRecord] = []
        for code in asset_codes:
            plain = to_plain_symbol(code)
            try:
                df = self.ef.stock.get_quote_history(plain)
            except Exception:
                self.last_warnings.append(f"efinance stock quote failed for {code}")
                continue
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                try:
                    trade_date_val = pd.Timestamp(row["日期"]).date()
                except Exception:
                    continue
                if trade_date_val < start_date or trade_date_val > end_date:
                    continue
                try:
                    pct_col = "涨跌幅" if "涨跌幅" in df.columns else None
                    if pct_col is None:
                        break
                    records.append(StockQuoteRecord(
                        trade_date=trade_date_val,
                        asset_code=normalize_asset_code(code),
                        asset_name=str(row.get("股票名称", code)),
                        return_pct=float(row[pct_col]),
                        source="efinance",
                    ))
                except (ValueError, KeyError):
                    continue
        return records

    # ── Stock Live Quotes ─────────────────────────────────────────────────

    def fetch_stock_live_quotes(
        self,
        asset_codes: list[str],
        sleep_seconds: float = 0.0,
        timeout_seconds: float = 8.0,
    ) -> list[LiveStockQuoteRecord]:
        from .code_utils import to_plain_symbol

        records: list[LiveStockQuoteRecord] = []
        quote_time = datetime.now()
        today = quote_time.date()

        for code in asset_codes:
            plain = to_plain_symbol(code)
            try:
                df = self.ef.stock.get_latest_quote(plain)
            except Exception:
                self.last_warnings.append(f"efinance live quote failed for {code}")
                continue
            if df is None or df.empty:
                continue
            row = df.iloc[0]
            try:
                pct = float(row["涨跌幅"])
            except (ValueError, KeyError):
                continue
            records.append(LiveStockQuoteRecord(
                trade_date=today,
                quote_time=quote_time,
                asset_code=normalize_asset_code(code),
                asset_name=str(row.get("名称", code)),
                return_pct=pct,
                source="efinance",
            ))
        return records

    # ── Fund Holdings ─────────────────────────────────────────────────────

    def fetch_fund_holdings(
        self,
        fund_code: str,
        year: int | None = None,
    ) -> list[dict[str, object]]:
        try:
            df = self.ef.fund.get_invest_position(fund_code)
        except Exception as exc:
            raise DataSourceError(f"efinance fetch_fund_holdings failed for {fund_code}: {exc}") from exc

        if df is None or df.empty:
            return []

        rows: list[dict[str, object]] = []
        for _, row in df.iterrows():
            pub_date = row.get("公开日期")
            report_date_str = str(pub_date)[:10] if pub_date is not None and pd.notna(pub_date) else None
            rows.append({
                "fund_code": fund_code,
                "report_date": report_date_str,
                "source": "efinance:public_holdings",
                "asset_code": normalize_asset_code(str(row.get("股票代码", ""))),
                "asset_name": str(row.get("股票简称", row.get("股票代码", ""))),
                "asset_type": "stock",
                "weight_pct": float(row["持仓占比"]),
            })
        return rows

    # ── Fund Asset Allocation ─────────────────────────────────────────────

    def fetch_fund_asset_allocation(
        self,
        fund_code: str,
        report_date: date | None = None,
    ) -> list[dict[str, object]]:
        try:
            df = self.ef.fund.get_types_percentage(fund_code)
        except Exception as exc:
            raise DataSourceError(f"efinance fetch_fund_asset_allocation failed for {fund_code}: {exc}") from exc

        if df is None or df.empty:
            return []

        row = df.iloc[0]
        stock = float(row.get("股票比重", 0) or 0)
        bond = float(row.get("债券比重", 0) or 0)
        cash = float(row.get("现金比重", 0) or 0)
        other = float(row.get("其他比重", 0) or 0)

        return [{
            "fund_code": fund_code,
            "report_date": (report_date or date.today() - timedelta(days=60)).isoformat(),
            "source": "efinance:asset_allocation",
            "stock_weight_pct": stock,
            "bond_weight_pct": bond,
            "cash_weight_pct": cash,
            "other_weight_pct": other,
        }]
