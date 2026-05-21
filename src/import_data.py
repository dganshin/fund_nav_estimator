from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ActualReturn, DailyQuote, Fund, HoldingItem, HoldingVersion


def parse_date(value: str) -> date:
    return date.fromisoformat(value.strip())


def parse_float(value: str) -> float:
    return float(value.strip())


def import_funds_from_yaml(session: Session, yaml_path: str | Path) -> int:
    yaml_file = Path(yaml_path)
    payload = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
    funds = payload.get("funds", [])
    count = 0

    for item in funds:
        fund_code = str(item["fund_code"]).strip()
        fund = session.get(Fund, fund_code)
        if fund is None:
            fund = Fund(fund_code=fund_code)
            session.add(fund)
        fund.fund_name = item["fund_name"].strip()
        fund.fund_type = item["fund_type"].strip()
        fund.market = item["market"].strip()
        fund.is_active = bool(item.get("is_active", True))
        count += 1

    session.commit()
    return count


def import_holdings_from_csv(session: Session, csv_path: str | Path) -> int:
    csv_file = Path(csv_path)
    grouped_rows: dict[tuple[str, date, str], list[dict[str, str]]] = defaultdict(list)

    with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            key = (
                row["fund_code"].strip(),
                parse_date(row["report_date"]),
                row["source"].strip(),
            )
            grouped_rows[key].append(row)

    created_or_updated = 0
    fund_latest_key: dict[str, tuple[str, date, str]] = {}
    for key in grouped_rows:
        fund_code, report_date, source = key
        latest_key = fund_latest_key.get(fund_code)
        if latest_key is None or (report_date, source) > (latest_key[1], latest_key[2]):
            fund_latest_key[fund_code] = key

    for key, rows in grouped_rows.items():
        fund_code, report_date, source = key
        if session.get(Fund, fund_code) is None:
            raise ValueError(f"Fund {fund_code} not found. Import funds first.")

        version = session.scalar(
            select(HoldingVersion).where(
                HoldingVersion.fund_code == fund_code,
                HoldingVersion.report_date == report_date,
                HoldingVersion.source == source,
            )
        )
        total_weight = sum(parse_float(row["weight"]) for row in rows)
        is_active = fund_latest_key[fund_code] == key

        if version is None:
            version = HoldingVersion(
                fund_code=fund_code,
                report_date=report_date,
                source=source,
                total_weight=total_weight,
                is_active=is_active,
            )
            session.add(version)
            session.flush()
        else:
            version.total_weight = total_weight
            version.is_active = is_active
            version.items.clear()

        for row in rows:
            version.items.append(
                HoldingItem(
                    asset_code=row["asset_code"].strip(),
                    asset_name=row["asset_name"].strip(),
                    asset_type=row["asset_type"].strip(),
                    weight=parse_float(row["weight"]),
                )
            )

        created_or_updated += 1

    for fund_code, latest_key in fund_latest_key.items():
        session.query(HoldingVersion).filter(
            HoldingVersion.fund_code == fund_code,
            HoldingVersion.id.is_not(None),
        ).update({"is_active": False}, synchronize_session=False)

        session.query(HoldingVersion).filter(
            HoldingVersion.fund_code == fund_code,
            HoldingVersion.report_date == latest_key[1],
            HoldingVersion.source == latest_key[2],
        ).update({"is_active": True}, synchronize_session=False)

    session.commit()
    return created_or_updated


def import_quotes_from_csv(session: Session, csv_path: str | Path) -> int:
    csv_file = Path(csv_path)
    count = 0

    with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            trade_date = parse_date(row["trade_date"])
            asset_code = row["asset_code"].strip()
            quote = session.get(DailyQuote, {"trade_date": trade_date, "asset_code": asset_code})
            if quote is None:
                quote = DailyQuote(trade_date=trade_date, asset_code=asset_code)
                session.add(quote)
            quote.asset_name = row["asset_name"].strip()
            quote.return_pct = parse_float(row["return_pct"])
            quote.source = row["source"].strip()
            count += 1

    session.commit()
    return count


def import_actual_returns_from_csv(session: Session, csv_path: str | Path) -> int:
    csv_file = Path(csv_path)
    count = 0

    with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            trade_date = parse_date(row["trade_date"])
            fund_code = row["fund_code"].strip()
            if session.get(Fund, fund_code) is None:
                raise ValueError(f"Fund {fund_code} not found. Import funds first.")

            actual_return = session.get(
                ActualReturn,
                {"trade_date": trade_date, "fund_code": fund_code},
            )
            if actual_return is None:
                actual_return = ActualReturn(trade_date=trade_date, fund_code=fund_code)
                session.add(actual_return)
            actual_return.actual_return = parse_float(row["actual_return"])
            actual_return.source = row["source"].strip()
            count += 1

    session.commit()
    return count

