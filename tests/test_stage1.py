from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import get_session_factory
from src.estimator import build_fund_estimates
from src.import_data import import_funds_from_csv, import_holdings_from_csv, import_quotes_from_csv
from src.init_db import init_db
from src.models import DailyQuote, Fund, FundEstimate, HoldingItem, HoldingVersion


def write_csv(path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")


def create_session_factory(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)
    return get_session_factory(db_url)


def test_raw_estimate_formula_is_correct(tmp_path):
    session_factory = create_session_factory(tmp_path)
    funds_csv = tmp_path / "funds.csv"
    holdings_csv = tmp_path / "holdings.csv"
    quotes_csv = tmp_path / "quotes.csv"

    write_csv(
        funds_csv,
        """
        fund_code,fund_name,fund_type,market,is_active
        000001,测试基金,equity,CN,true
        """,
    )
    write_csv(
        holdings_csv,
        """
        fund_code,report_date,source,asset_code,asset_name,asset_type,weight_pct
        000001,2026-05-01,test,000001.SZ,平安银行,stock,10
        000001,2026-05-01,test,600519.SH,贵州茅台,stock,20
        """,
    )
    write_csv(
        quotes_csv,
        """
        trade_date,asset_code,asset_name,return_pct,source
        2026-05-21,000001.SZ,平安银行,2,test
        2026-05-21,600519.SH,贵州茅台,1,test
        """,
    )

    with session_factory() as session:
        import_funds_from_csv(session, funds_csv)
        import_holdings_from_csv(session, holdings_csv)
        import_quotes_from_csv(session, quotes_csv)
        results = build_fund_estimates(session, date.fromisoformat("2026-05-21"))
        estimate = session.get(
            FundEstimate,
            {"trade_date": date.fromisoformat("2026-05-21"), "fund_code": "000001"},
        )

    assert len(results) == 1
    assert estimate is not None
    assert estimate.raw_estimate == 0.004
    assert estimate.covered_weight == 0.3
    assert estimate.missing_weight == 0.0
    assert json.loads(estimate.missing_assets_json) == []


def test_missing_quote_does_not_interrupt_estimate(tmp_path):
    session_factory = create_session_factory(tmp_path)
    funds_csv = tmp_path / "funds.csv"
    holdings_csv = tmp_path / "holdings.csv"
    quotes_csv = tmp_path / "quotes.csv"

    write_csv(
        funds_csv,
        """
        fund_code,fund_name,fund_type,market,is_active
        000001,测试基金,equity,CN,true
        """,
    )
    write_csv(
        holdings_csv,
        """
        fund_code,report_date,source,asset_code,asset_name,asset_type,weight_pct
        000001,2026-05-01,test,000001.SZ,平安银行,stock,10
        000001,2026-05-01,test,600519.SH,贵州茅台,stock,20
        """,
    )
    write_csv(
        quotes_csv,
        """
        trade_date,asset_code,asset_name,return_pct,source
        2026-05-21,000001.SZ,平安银行,2,test
        """,
    )

    with session_factory() as session:
        import_funds_from_csv(session, funds_csv)
        import_holdings_from_csv(session, holdings_csv)
        import_quotes_from_csv(session, quotes_csv)
        results = build_fund_estimates(session, date.fromisoformat("2026-05-21"))
        estimate = session.get(
            FundEstimate,
            {"trade_date": date.fromisoformat("2026-05-21"), "fund_code": "000001"},
        )

    assert len(results) == 1
    assert results[0].warning == "缺少1个资产行情"
    assert estimate is not None
    assert estimate.raw_estimate == 0.002
    assert estimate.covered_weight == 0.1
    assert estimate.missing_weight == 0.2
    missing_assets = json.loads(estimate.missing_assets_json)
    assert missing_assets[0]["asset_code"] == "600519.SH"


def test_repeated_imports_do_not_create_duplicate_rows(tmp_path):
    session_factory = create_session_factory(tmp_path)
    funds_csv = tmp_path / "funds.csv"
    holdings_csv = tmp_path / "holdings.csv"
    quotes_csv = tmp_path / "quotes.csv"

    write_csv(
        funds_csv,
        """
        fund_code,fund_name,fund_type,market,is_active
        000001,测试基金,equity,CN,true
        """,
    )
    write_csv(
        holdings_csv,
        """
        fund_code,report_date,source,asset_code,asset_name,asset_type,weight_pct
        000001,2026-05-01,test,000001.SZ,平安银行,stock,10
        000001,2026-05-01,test,600519.SH,贵州茅台,stock,20
        """,
    )
    write_csv(
        quotes_csv,
        """
        trade_date,asset_code,asset_name,return_pct,source
        2026-05-21,000001.SZ,平安银行,2,test
        """,
    )

    with session_factory() as session:
        import_funds_from_csv(session, funds_csv)
        import_funds_from_csv(session, funds_csv)
        import_holdings_from_csv(session, holdings_csv)
        import_holdings_from_csv(session, holdings_csv)
        import_quotes_from_csv(session, quotes_csv)
        import_quotes_from_csv(session, quotes_csv)

        fund_count = session.scalar(select(func.count()).select_from(Fund))
        version_count = session.scalar(select(func.count()).select_from(HoldingVersion))
        item_count = session.scalar(select(func.count()).select_from(HoldingItem))
        quote_count = session.scalar(select(func.count()).select_from(DailyQuote))

    assert fund_count == 1
    assert version_count == 1
    assert item_count == 2
    assert quote_count == 1
