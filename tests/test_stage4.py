from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backfill import backfill_history, fetch_and_store_fund_navs, fetch_and_store_stock_quotes, get_active_holding_asset_codes
from src.data_sources.base import FundNavRecord, StockQuoteRecord
from src.data_sources.code_utils import normalize_asset_code, to_plain_symbol, to_prefixed_symbol
from src.db import get_session_factory
from src.estimator import build_estimate_errors, build_estimate_history, build_fund_estimates, build_reconcile_history
from src.import_data import import_asset_allocations_from_csv, import_funds_from_csv, import_holdings_from_csv
from src.init_db import init_db
from src.models import ActualReturn, DailyQuote, EstimateError, FundEstimate, FundNav


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def create_session_factory(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)
    return get_session_factory(db_url)


def write_csv(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")


def seed_fund_holdings_and_allocations(tmp_path, session) -> None:
    funds_csv = tmp_path / "funds.csv"
    holdings_csv = tmp_path / "holdings.csv"
    asset_alloc_csv = tmp_path / "asset_alloc.csv"
    write_csv(
        funds_csv,
        """
        fund_code,fund_name,fund_type,market,is_active
        002207,测试真实基金,hybrid,CN,true
        """,
    )
    write_csv(
        holdings_csv,
        """
        fund_code,report_date,source,asset_code,asset_name,asset_type,weight_pct
        002207,2026-03-31,manual,600988.SH,赤峰黄金,stock,20
        002207,2026-03-31,manual,000975.SZ,银泰黄金,stock,15
        """,
    )
    write_csv(
        asset_alloc_csv,
        """
        fund_code,report_date,source,stock_weight_pct,bond_weight_pct,cash_weight_pct,other_weight_pct
        002207,2026-03-31,manual,90,0,10,0
        """,
    )
    import_funds_from_csv(session, funds_csv)
    import_holdings_from_csv(session, holdings_csv)
    import_asset_allocations_from_csv(session, asset_alloc_csv)


@dataclass
class MockDataSource:
    nav_records: list[FundNavRecord] = field(default_factory=list)
    quote_records: list[StockQuoteRecord] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def fetch_fund_navs(self, fund_code: str, start_date: date, end_date: date) -> list[FundNavRecord]:
        self.calls.append("fetch_fund_navs")
        return [
            record
            for record in self.nav_records
            if record.fund_code == fund_code and start_date <= record.trade_date <= end_date
        ]

    def fetch_stock_daily_quotes(
        self,
        asset_codes: list[str],
        start_date: date,
        end_date: date,
        sleep_seconds: float = 0.0,
    ) -> list[StockQuoteRecord]:
        self.calls.append("fetch_stock_daily_quotes")
        asset_set = set(asset_codes)
        return [
            record
            for record in self.quote_records
            if record.asset_code in asset_set and start_date <= record.trade_date <= end_date
        ]

    def fetch_fund_holdings(self, fund_code: str, year: int | None = None) -> list[dict[str, object]]:
        self.calls.append("fetch_fund_holdings")
        return []

    def fetch_fund_asset_allocation(self, fund_code: str, report_date: date | None = None) -> list[dict[str, object]]:
        self.calls.append("fetch_fund_asset_allocation")
        return []


def make_mock_source() -> MockDataSource:
    return MockDataSource(
        nav_records=[
            FundNavRecord(date.fromisoformat("2026-05-20"), "002207", 1.0000, 1.0000, "mock"),
            FundNavRecord(date.fromisoformat("2026-05-21"), "002207", 1.0100, 1.0100, "mock"),
            FundNavRecord(date.fromisoformat("2026-05-22"), "002207", 1.0201, 1.0201, "mock"),
        ],
        quote_records=[
            StockQuoteRecord(date.fromisoformat("2026-05-20"), "600988.SH", "赤峰黄金", 0.02, "mock"),
            StockQuoteRecord(date.fromisoformat("2026-05-20"), "000975.SZ", "银泰黄金", 0.01, "mock"),
            StockQuoteRecord(date.fromisoformat("2026-05-21"), "600988.SH", "赤峰黄金", 0.01, "mock"),
            StockQuoteRecord(date.fromisoformat("2026-05-21"), "000975.SZ", "银泰黄金", 0.00, "mock"),
            StockQuoteRecord(date.fromisoformat("2026-05-22"), "600988.SH", "赤峰黄金", 0.03, "mock"),
            StockQuoteRecord(date.fromisoformat("2026-05-22"), "000975.SZ", "银泰黄金", 0.01, "mock"),
        ],
    )


def test_code_utils_convert_symbols_correctly():
    assert normalize_asset_code("600988.SH") == "600988.SH"
    assert normalize_asset_code("000975.SZ") == "000975.SZ"
    assert normalize_asset_code("688981.SH") == "688981.SH"
    assert to_plain_symbol("600988.SH") == "600988"
    assert to_plain_symbol("000975.SZ") == "000975"
    assert to_prefixed_symbol("600988.SH") == "sh600988"
    assert to_prefixed_symbol("000975.SZ") == "sz000975"


def test_mock_nav_fetch_writes_fund_navs_and_actual_returns(tmp_path):
    session_factory = create_session_factory(tmp_path)
    data_source = make_mock_source()
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        report = fetch_and_store_fund_navs(
            session,
            data_source,
            "002207",
            date.fromisoformat("2026-05-20"),
            date.fromisoformat("2026-05-22"),
        )
        nav_count = session.scalar(select(func.count()).select_from(FundNav))
        actual_count = session.scalar(select(func.count()).select_from(ActualReturn))
        actual = session.get(ActualReturn, {"trade_date": date.fromisoformat("2026-05-21"), "fund_code": "002207"})

    assert report.imported_count == 3
    assert report.generated_actual_returns == 2
    assert nav_count == 3
    assert actual_count == 2
    assert actual is not None
    assert round(actual.actual_return, 8) == 0.01


def test_mock_quote_fetch_writes_daily_quotes(tmp_path):
    session_factory = create_session_factory(tmp_path)
    data_source = make_mock_source()
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        report = fetch_and_store_stock_quotes(
            session,
            data_source,
            date.fromisoformat("2026-05-20"),
            date.fromisoformat("2026-05-22"),
            ["600988.SH", "000975.SZ"],
        )
        quote_count = session.scalar(select(func.count()).select_from(DailyQuote))

    assert report.imported_count == 6
    assert quote_count == 6


def test_fetch_from_active_holdings_reads_active_asset_pool(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        asset_codes = get_active_holding_asset_codes(session, "002207", date.fromisoformat("2026-05-22"))

    assert asset_codes == ["600988.SH", "000975.SZ"]


def test_estimate_history_builds_fund_estimates(tmp_path):
    session_factory = create_session_factory(tmp_path)
    data_source = make_mock_source()
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        fetch_and_store_stock_quotes(
            session,
            data_source,
            date.fromisoformat("2026-05-20"),
            date.fromisoformat("2026-05-22"),
            ["600988.SH", "000975.SZ"],
        )
        report = build_estimate_history(
            session,
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-22"),
            fund_code="002207",
        )
        estimate_count = session.scalar(select(func.count()).select_from(FundEstimate))

    assert report.total_count == 3
    assert estimate_count == 3


def test_reconcile_history_builds_estimate_errors(tmp_path):
    session_factory = create_session_factory(tmp_path)
    data_source = make_mock_source()
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        fetch_and_store_stock_quotes(
            session,
            data_source,
            date.fromisoformat("2026-05-20"),
            date.fromisoformat("2026-05-22"),
            ["600988.SH", "000975.SZ"],
        )
        fetch_and_store_fund_navs(
            session,
            data_source,
            "002207",
            date.fromisoformat("2026-05-20"),
            date.fromisoformat("2026-05-22"),
        )
        build_estimate_history(
            session,
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-22"),
            fund_code="002207",
        )
        report = build_reconcile_history(
            session,
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-22"),
            fund_code="002207",
        )
        error_count = session.scalar(select(func.count()).select_from(EstimateError))

    assert report.total_count == 2
    assert error_count == 2


def test_backfill_history_flow_order_and_idempotency(tmp_path):
    session_factory = create_session_factory(tmp_path)
    data_source = make_mock_source()
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        first = backfill_history(
            session=session,
            data_source=data_source,
            fund_code="002207",
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-22"),
            window=2,
            base="coverage_adjusted",
            min_samples=1,
        )
        second = backfill_history(
            session=session,
            data_source=data_source,
            fund_code="002207",
            start_date=date.fromisoformat("2026-05-20"),
            end_date=date.fromisoformat("2026-05-22"),
            window=2,
            base="coverage_adjusted",
            min_samples=1,
        )
        nav_count = session.scalar(select(func.count()).select_from(FundNav))
        quote_count = session.scalar(select(func.count()).select_from(DailyQuote))
        estimate_count = session.scalar(select(func.count()).select_from(FundEstimate))
        error_count = session.scalar(select(func.count()).select_from(EstimateError))

    assert data_source.calls[:2] == ["fetch_fund_navs", "fetch_stock_daily_quotes"]
    assert nav_count == 3
    assert quote_count == 6
    assert estimate_count == 3
    assert error_count == 2
    assert first[6][0].fund_code == "002207"
    assert second[6][0].fund_code == "002207"


def test_empty_data_source_does_not_write_bad_rows(tmp_path):
    session_factory = create_session_factory(tmp_path)
    data_source = MockDataSource()
    with session_factory() as session:
        seed_fund_holdings_and_allocations(tmp_path, session)
        nav_report = fetch_and_store_fund_navs(
            session,
            data_source,
            "002207",
            date.fromisoformat("2026-05-20"),
            date.fromisoformat("2026-05-22"),
        )
        quote_report = fetch_and_store_stock_quotes(
            session,
            data_source,
            date.fromisoformat("2026-05-20"),
            date.fromisoformat("2026-05-22"),
            ["600988.SH"],
        )
        nav_count = session.scalar(select(func.count()).select_from(FundNav))
        quote_count = session.scalar(select(func.count()).select_from(DailyQuote))

    assert nav_report.imported_count == 0
    assert quote_report.imported_count == 0
    assert nav_count == 0
    assert quote_count == 0
