from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import get_session_factory
from src.import_data import (
    import_asset_allocations_from_rows,
    import_funds_from_rows,
    import_holdings_from_rows,
    import_industry_allocations_from_rows,
)
from src.init_db import init_db
from src.web_services import (
    load_asset_allocation_rows,
    load_fund_rows,
    load_holding_rows,
    load_industry_allocation_rows,
)


def create_session_factory(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    init_db(db_url)
    return get_session_factory(db_url)


def test_web_row_import_keeps_fund_code_and_bool(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        count = import_funds_from_rows(
            session,
            [
                {
                    "fund_code": "002207",
                    "fund_name": "前海开源金银珠宝混合C",
                    "fund_type": "equity_theme",
                    "market": "A股",
                    "is_active": True,
                }
            ],
        )
        rows = load_fund_rows(session)

    assert count == 1
    assert rows[0]["fund_code"] == "002207"
    assert rows[0]["is_active"] is True


def test_web_row_import_round_trips_active_holdings(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        import_funds_from_rows(
            session,
            [
                {
                    "fund_code": "002207",
                    "fund_name": "前海开源金银珠宝混合C",
                    "fund_type": "equity_theme",
                    "market": "A股",
                    "is_active": True,
                }
            ],
        )
        count = import_holdings_from_rows(
            session,
            [
                {
                    "fund_code": "002207",
                    "report_date": "2026-03-31",
                    "source": "web_manual",
                    "asset_code": "600988.SH",
                    "asset_name": "赤峰黄金",
                    "asset_type": "stock",
                    "weight_pct": 9.87,
                },
                {
                    "fund_code": "002207",
                    "report_date": "2026-03-31",
                    "source": "web_manual",
                    "asset_code": "000975.SZ",
                    "asset_name": "山金国际",
                    "asset_type": "stock",
                    "weight_pct": 8.09,
                },
            ],
        )
        rows = load_holding_rows(session, "002207")

    assert count == 1
    assert [row["asset_code"] for row in rows] == ["600988.SH", "000975.SZ"]
    assert rows[0]["weight_pct"] == 9.87


def test_web_row_import_round_trips_allocations(tmp_path):
    session_factory = create_session_factory(tmp_path)
    with session_factory() as session:
        import_funds_from_rows(
            session,
            [
                {
                    "fund_code": "002207",
                    "fund_name": "前海开源金银珠宝混合C",
                    "fund_type": "equity_theme",
                    "market": "A股",
                    "is_active": True,
                }
            ],
        )
        asset_count = import_asset_allocations_from_rows(
            session,
            [
                {
                    "fund_code": "002207",
                    "report_date": "2026-03-31",
                    "source": "web_manual",
                    "stock_weight_pct": 90.8,
                    "bond_weight_pct": 0,
                    "cash_weight_pct": 0,
                    "other_weight_pct": 0,
                }
            ],
        )
        industry_count = import_industry_allocations_from_rows(
            session,
            [
                {
                    "fund_code": "002207",
                    "report_date": "2026-03-31",
                    "source": "web_manual",
                    "industry_name": "采矿业",
                    "industry_code": "B",
                    "weight_pct": 78.16,
                },
                {
                    "fund_code": "002207",
                    "report_date": "2026-03-31",
                    "source": "web_manual",
                    "industry_name": "制造业",
                    "industry_code": "C",
                    "weight_pct": 12.64,
                },
            ],
        )
        asset_rows = load_asset_allocation_rows(session, "002207")
        industry_rows = load_industry_allocation_rows(session, "002207")

    assert asset_count == 1
    assert industry_count == 2
    assert asset_rows[0]["stock_weight_pct"] == 90.8
    assert [row["industry_code"] for row in industry_rows] == ["B", "C"]
