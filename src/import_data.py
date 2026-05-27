from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .data_sources.base import FundNavRecord, StockQuoteRecord
from .models import ActualReturn, DailyQuote, Fund, FundAssetAllocation, FundIndustryAllocation, FundNav, HoldingItem, HoldingVersion


class DataImportError(ValueError):
    pass


@dataclass
class ImportReport:
    imported_count: int
    warnings: list[str]
    generated_actual_returns: int = 0


WEB_INPUT_PATH = Path("web_input")


def validate_required_columns(fieldnames: list[str] | None, required_fields: Iterable[str], csv_path: Path) -> None:
    if fieldnames is None:
        raise DataImportError(f"{csv_path} is missing header row.")

    missing_fields = [field for field in required_fields if field not in fieldnames]
    if missing_fields:
        raise DataImportError(
            f"{csv_path} is missing required columns: {', '.join(missing_fields)}"
        )


def read_required_value(row: dict[str, str], field_name: str, row_number: int, csv_path: Path) -> str:
    raw_value = row.get(field_name)
    if raw_value is None:
        raise DataImportError(f"{csv_path} row {row_number}: missing field {field_name}.")

    value = raw_value.strip()
    if value == "":
        raise DataImportError(f"{csv_path} row {row_number}: empty value for {field_name}.")
    return value


def coerce_row_value(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def normalize_rows(rows: Iterable[dict[str, object]]) -> list[dict[str, str]]:
    normalized_rows: list[dict[str, str]] = []
    for row in rows:
        normalized_row: dict[str, str] = {}
        for key, value in row.items():
            normalized_row[str(key)] = coerce_row_value(value) or ""
        normalized_rows.append(normalized_row)
    return normalized_rows


def filter_blank_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if any(value.strip() for value in row.values())]


def read_optional_value(row: dict[str, str], field_name: str) -> str | None:
    raw_value = row.get(field_name)
    if raw_value is None:
        return None

    value = raw_value.strip()
    if value == "":
        return None
    return value


def parse_date(value: str) -> date:
    return date.fromisoformat(value.strip())


def parse_csv_date(value: str, field_name: str, row_number: int, csv_path: Path) -> date:
    try:
        return parse_date(value)
    except ValueError as exc:
        raise DataImportError(
            f"{csv_path} row {row_number}: invalid date for {field_name}: {value}. "
            "Expected YYYY-MM-DD."
        ) from exc


def parse_decimal(value: str, field_name: str, row_number: int, csv_path: Path) -> float:
    try:
        return float(value.strip())
    except ValueError as exc:
        raise DataImportError(
            f"{csv_path} row {row_number}: invalid numeric value for {field_name}: {value}"
        ) from exc


def parse_percent_to_decimal(value: str, field_name: str, row_number: int, csv_path: Path) -> float:
    return parse_decimal(value, field_name, row_number, csv_path) / 100.0


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def validate_extreme_actual_return(
    warnings: list[str],
    fund_code: str,
    trade_date: date,
    actual_return: float,
) -> None:
    if abs(actual_return) > 0.2:
        warnings.append(
            f"Warning: fund {fund_code} on {trade_date} has actual_return "
            f"{actual_return * 100:+.2f}%, exceeding 20%."
        )


def upsert_actual_return(
    session: Session,
    trade_date: date,
    fund_code: str,
    actual_return_value: float,
    source: str,
) -> ActualReturn:
    actual_return = session.get(
        ActualReturn,
        {"trade_date": trade_date, "fund_code": fund_code},
    )
    if actual_return is None:
        actual_return = ActualReturn(trade_date=trade_date, fund_code=fund_code)
        session.add(actual_return)

    actual_return.actual_return = actual_return_value
    actual_return.source = source
    return actual_return


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


def import_funds_from_rows(
    session: Session,
    rows: Iterable[dict[str, object]],
    source_path: Path | None = None,
) -> int:
    csv_path = source_path or WEB_INPUT_PATH
    normalized_rows = filter_blank_rows(normalize_rows(rows))
    required_fields = ["fund_code", "fund_name", "fund_type", "market", "is_active"]
    count = 0

    for row_number, row in enumerate(normalized_rows, start=2):
        validate_required_columns(list(row.keys()), required_fields, csv_path)
        fund_code = read_required_value(row, "fund_code", row_number, csv_path)
        fund = session.get(Fund, fund_code)
        if fund is None:
            fund = Fund(fund_code=fund_code)
            session.add(fund)

        fund_name = read_required_value(row, "fund_name", row_number, csv_path)
        if not (fund_name == fund_code and fund.fund_name and fund.fund_name != fund_code):
            fund.fund_name = fund_name
        fund.fund_type = read_required_value(row, "fund_type", row_number, csv_path)
        fund.market = read_required_value(row, "market", row_number, csv_path)
        fund.is_active = parse_bool(read_required_value(row, "is_active", row_number, csv_path))
        count += 1

    session.commit()
    return count


def import_funds_from_csv(session: Session, csv_path: str | Path) -> int:
    csv_file = Path(csv_path)

    with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return import_funds_from_rows(session, list(reader), csv_file)


def get_weight_value(row: dict[str, str], row_number: int, csv_path: Path) -> tuple[str, str]:
    for field_name in ("weight_pct", "weight"):
        raw_value = row.get(field_name)
        if raw_value is not None and raw_value.strip() != "":
            return field_name, raw_value
    raise DataImportError(
        f"{csv_path} row {row_number}: missing required field weight_pct."
    )


def get_actual_return_value(row: dict[str, str], row_number: int, csv_path: Path) -> tuple[str, str]:
    for field_name in ("actual_return_pct", "actual_return"):
        raw_value = row.get(field_name)
        if raw_value is not None and raw_value.strip() != "":
            return field_name, raw_value
    raise DataImportError(
        f"{csv_path} row {row_number}: missing required field actual_return_pct."
    )


def import_holdings_from_rows(
    session: Session,
    rows: Iterable[dict[str, object]],
    source_path: Path | None = None,
) -> int:
    csv_path = source_path or WEB_INPUT_PATH
    normalized_rows = filter_blank_rows(normalize_rows(rows))
    grouped_rows: dict[tuple[str, date, str], list[dict[str, str]]] = defaultdict(list)
    required_fields = ["fund_code", "report_date", "source", "asset_code", "asset_name", "asset_type"]

    for row_number, row in enumerate(normalized_rows, start=2):
        validate_required_columns(list(row.keys()), required_fields, csv_path)
        fund_code = read_required_value(row, "fund_code", row_number, csv_path)
        report_date = parse_csv_date(read_required_value(row, "report_date", row_number, csv_path), "report_date", row_number, csv_path)
        source = read_required_value(row, "source", row_number, csv_path)
        asset_code = read_required_value(row, "asset_code", row_number, csv_path)
        asset_name = read_required_value(row, "asset_name", row_number, csv_path)
        asset_type = read_required_value(row, "asset_type", row_number, csv_path)
        weight_field_name, weight_value = get_weight_value(row, row_number, csv_path)
        weight_decimal = parse_percent_to_decimal(weight_value, weight_field_name, row_number, csv_path)

        key = (fund_code, report_date, source)
        grouped_rows[key].append(
            {
                "asset_code": asset_code,
                "asset_name": asset_name,
                "asset_type": asset_type,
                "weight_decimal": f"{weight_decimal:.12f}",
            }
        )

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
            raise DataImportError(f"Fund {fund_code} not found. Import funds first.")

        version = session.scalar(
            select(HoldingVersion).where(
                HoldingVersion.fund_code == fund_code,
                HoldingVersion.report_date == report_date,
                HoldingVersion.source == source,
            )
        )

        seen_assets: set[str] = set()
        total_weight = 0.0
        for row in rows:
            asset_code = row["asset_code"]
            if asset_code in seen_assets:
                raise DataImportError(
                    f"{csv_path} duplicate asset_code {asset_code} in fund {fund_code} "
                    f"report_date {report_date} source {source}."
                )
            seen_assets.add(asset_code)
            total_weight += float(row["weight_decimal"])

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
            session.flush()

        for row in rows:
            version.items.append(
                HoldingItem(
                    asset_code=row["asset_code"],
                    asset_name=row["asset_name"],
                    asset_type=row["asset_type"],
                    weight=float(row["weight_decimal"]),
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


def import_holdings_from_csv(session: Session, csv_path: str | Path) -> int:
    csv_file = Path(csv_path)
    with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return import_holdings_from_rows(session, list(reader), csv_file)


def import_quotes_from_csv(session: Session, csv_path: str | Path) -> int:
    csv_file = Path(csv_path)
    count = 0
    required_fields = ["trade_date", "asset_code", "asset_name", "return_pct", "source"]

    with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        validate_required_columns(reader.fieldnames, required_fields, csv_file)

        for row_number, row in enumerate(reader, start=2):
            trade_date = parse_csv_date(read_required_value(row, "trade_date", row_number, csv_file), "trade_date", row_number, csv_file)
            asset_code = read_required_value(row, "asset_code", row_number, csv_file)
            quote = session.get(DailyQuote, {"trade_date": trade_date, "asset_code": asset_code})
            if quote is None:
                quote = DailyQuote(trade_date=trade_date, asset_code=asset_code)
                session.add(quote)
            quote.asset_name = read_required_value(row, "asset_name", row_number, csv_file)
            quote.return_pct = parse_percent_to_decimal(
                read_required_value(row, "return_pct", row_number, csv_file),
                "return_pct",
                row_number,
                csv_file,
            )
            quote.source = read_required_value(row, "source", row_number, csv_file)
            count += 1

    session.commit()
    return count


def import_quote_records(session: Session, records: Iterable[StockQuoteRecord]) -> int:
    count = 0
    for record in records:
        quote = session.get(
            DailyQuote,
            {"trade_date": record.trade_date, "asset_code": record.asset_code},
        )
        if quote is None:
            quote = DailyQuote(trade_date=record.trade_date, asset_code=record.asset_code)
            session.add(quote)
        quote.asset_name = record.asset_name
        quote.return_pct = record.return_pct
        quote.source = record.source
        count += 1

    session.commit()
    return count


def import_actual_returns_from_csv(session: Session, csv_path: str | Path) -> ImportReport:
    csv_file = Path(csv_path)
    count = 0
    warnings: list[str] = []
    required_fields = ["trade_date", "fund_code", "source"]

    with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        validate_required_columns(reader.fieldnames, required_fields, csv_file)

        for row_number, row in enumerate(reader, start=2):
            trade_date = parse_csv_date(read_required_value(row, "trade_date", row_number, csv_file), "trade_date", row_number, csv_file)
            fund_code = read_required_value(row, "fund_code", row_number, csv_file)
            if session.get(Fund, fund_code) is None:
                raise DataImportError(f"Fund {fund_code} not found. Import funds first.")

            actual_field_name, actual_value = get_actual_return_value(row, row_number, csv_file)
            actual_return_value = parse_percent_to_decimal(
                actual_value,
                actual_field_name,
                row_number,
                csv_file,
            )
            validate_extreme_actual_return(warnings, fund_code, trade_date, actual_return_value)
            upsert_actual_return(
                session,
                trade_date,
                fund_code,
                actual_return_value,
                read_required_value(row, "source", row_number, csv_file),
            )
            count += 1

    session.commit()
    return ImportReport(imported_count=count, warnings=warnings)


def import_navs_from_csv(session: Session, csv_path: str | Path) -> ImportReport:
    csv_file = Path(csv_path)
    count = 0
    warnings: list[str] = []
    generated_actual_returns = 0
    required_fields = ["trade_date", "fund_code", "unit_nav", "source"]
    imported_dates_by_fund: dict[str, set[date]] = defaultdict(set)

    with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        validate_required_columns(reader.fieldnames, required_fields, csv_file)

        for row_number, row in enumerate(reader, start=2):
            trade_date = parse_csv_date(read_required_value(row, "trade_date", row_number, csv_file), "trade_date", row_number, csv_file)
            fund_code = read_required_value(row, "fund_code", row_number, csv_file)
            if session.get(Fund, fund_code) is None:
                raise DataImportError(f"Fund {fund_code} not found. Import funds first.")

            unit_nav = parse_decimal(
                read_required_value(row, "unit_nav", row_number, csv_file),
                "unit_nav",
                row_number,
                csv_file,
            )
            if unit_nav <= 0:
                raise DataImportError(
                    f"{csv_path} row {row_number}: unit_nav must be greater than 0."
                )

            accumulated_nav_value = read_optional_value(row, "accumulated_nav")
            accumulated_nav = None
            if accumulated_nav_value is not None:
                accumulated_nav = parse_decimal(
                    accumulated_nav_value,
                    "accumulated_nav",
                    row_number,
                    csv_file,
                )

            nav = session.get(FundNav, {"trade_date": trade_date, "fund_code": fund_code})
            if nav is None:
                nav = FundNav(trade_date=trade_date, fund_code=fund_code)
                session.add(nav)

            nav.unit_nav = unit_nav
            nav.accumulated_nav = accumulated_nav
            nav.source = read_required_value(row, "source", row_number, csv_file)
            imported_dates_by_fund[fund_code].add(trade_date)
            count += 1

    session.flush()

    for fund_code, imported_dates in imported_dates_by_fund.items():
        navs = session.scalars(
            select(FundNav)
            .where(FundNav.fund_code == fund_code)
            .order_by(FundNav.trade_date.asc())
        ).all()

        previous_nav: FundNav | None = None
        for nav in navs:
            if previous_nav is None:
                if nav.trade_date in imported_dates:
                    warnings.append(
                        f"Warning: fund {fund_code} on {nav.trade_date} is missing previous "
                        "trade day nav, actual_return not generated."
                    )
                previous_nav = nav
                continue

            actual_return_value = round(nav.unit_nav / previous_nav.unit_nav - 1, 8)
            validate_extreme_actual_return(warnings, fund_code, nav.trade_date, actual_return_value)
            upsert_actual_return(
                session,
                nav.trade_date,
                fund_code,
                actual_return_value,
                f"nav:{nav.source}",
            )
            if nav.trade_date in imported_dates:
                generated_actual_returns += 1
            previous_nav = nav

    session.commit()
    return ImportReport(
        imported_count=count,
        warnings=warnings,
        generated_actual_returns=generated_actual_returns,
    )


def import_nav_records(session: Session, records: Iterable[FundNavRecord]) -> ImportReport:
    count = 0
    warnings: list[str] = []
    generated_actual_returns = 0
    imported_dates_by_fund: dict[str, set[date]] = defaultdict(set)

    for record in records:
        if session.get(Fund, record.fund_code) is None:
            raise DataImportError(f"Fund {record.fund_code} not found. Import funds first.")
        if record.unit_nav <= 0:
            raise DataImportError(
                f"Fund {record.fund_code} on {record.trade_date} has non-positive unit_nav."
            )

        nav = session.get(FundNav, {"trade_date": record.trade_date, "fund_code": record.fund_code})
        if nav is None:
            nav = FundNav(trade_date=record.trade_date, fund_code=record.fund_code)
            session.add(nav)
        nav.unit_nav = record.unit_nav
        nav.accumulated_nav = record.accumulated_nav
        nav.source = record.source
        imported_dates_by_fund[record.fund_code].add(record.trade_date)
        count += 1

    session.flush()

    for fund_code, imported_dates in imported_dates_by_fund.items():
        navs = session.scalars(
            select(FundNav)
            .where(FundNav.fund_code == fund_code)
            .order_by(FundNav.trade_date.asc())
        ).all()

        previous_nav: FundNav | None = None
        for nav in navs:
            if previous_nav is None:
                if nav.trade_date in imported_dates:
                    warnings.append(
                        f"Warning: fund {fund_code} on {nav.trade_date} is missing previous "
                        "trade day nav, actual_return not generated."
                    )
                previous_nav = nav
                continue

            actual_return_value = round(nav.unit_nav / previous_nav.unit_nav - 1, 8)
            validate_extreme_actual_return(warnings, fund_code, nav.trade_date, actual_return_value)
            upsert_actual_return(
                session,
                nav.trade_date,
                fund_code,
                actual_return_value,
                f"nav:{nav.source}",
            )
            if nav.trade_date in imported_dates:
                generated_actual_returns += 1
            previous_nav = nav

    session.commit()
    return ImportReport(
        imported_count=count,
        warnings=warnings,
        generated_actual_returns=generated_actual_returns,
    )


def parse_optional_percent_to_decimal(
    row: dict[str, str],
    field_name: str,
    row_number: int,
    csv_path: Path,
) -> float:
    raw_value = row.get(field_name)
    if raw_value is None or raw_value.strip() == "":
        return 0.0
    return parse_percent_to_decimal(raw_value, field_name, row_number, csv_path)


def import_asset_allocations_from_rows(
    session: Session,
    rows: Iterable[dict[str, object]],
    source_path: Path | None = None,
) -> int:
    csv_path = source_path or WEB_INPUT_PATH
    normalized_rows = filter_blank_rows(normalize_rows(rows))
    required_fields = ["fund_code", "report_date", "source"]
    rows_by_key: dict[tuple[str, date, str], dict[str, float]] = {}
    latest_keys: dict[str, tuple[str, date, str]] = {}

    for row_number, row in enumerate(normalized_rows, start=2):
        validate_required_columns(list(row.keys()), required_fields, csv_path)
        fund_code = read_required_value(row, "fund_code", row_number, csv_path)
        report_date = parse_csv_date(
            read_required_value(row, "report_date", row_number, csv_path),
            "report_date",
            row_number,
            csv_path,
        )
        source = read_required_value(row, "source", row_number, csv_path)
        if session.get(Fund, fund_code) is None:
            raise DataImportError(f"Fund {fund_code} not found. Import funds first.")

        key = (fund_code, report_date, source)
        rows_by_key[key] = {
            "stock_weight": parse_optional_percent_to_decimal(row, "stock_weight_pct", row_number, csv_path),
            "bond_weight": parse_optional_percent_to_decimal(row, "bond_weight_pct", row_number, csv_path),
            "cash_weight": parse_optional_percent_to_decimal(row, "cash_weight_pct", row_number, csv_path),
            "other_weight": parse_optional_percent_to_decimal(row, "other_weight_pct", row_number, csv_path),
        }

        latest_key = latest_keys.get(fund_code)
        if latest_key is None or (report_date, source) > (latest_key[1], latest_key[2]):
            latest_keys[fund_code] = key

    count = 0
    for key, weights in rows_by_key.items():
        fund_code, report_date, source = key
        allocation = session.scalar(
            select(FundAssetAllocation).where(
                FundAssetAllocation.fund_code == fund_code,
                FundAssetAllocation.report_date == report_date,
                FundAssetAllocation.source == source,
            )
        )
        if allocation is None:
            allocation = FundAssetAllocation(
                fund_code=fund_code,
                report_date=report_date,
                source=source,
            )
            session.add(allocation)

        allocation.stock_weight = weights["stock_weight"]
        allocation.bond_weight = weights["bond_weight"]
        allocation.cash_weight = weights["cash_weight"]
        allocation.other_weight = weights["other_weight"]
        allocation.is_active = latest_keys[fund_code] == key
        count += 1

    for fund_code, latest_key in latest_keys.items():
        session.query(FundAssetAllocation).filter(
            FundAssetAllocation.fund_code == fund_code,
        ).update({"is_active": False}, synchronize_session=False)
        session.query(FundAssetAllocation).filter(
            FundAssetAllocation.fund_code == fund_code,
            FundAssetAllocation.report_date == latest_key[1],
            FundAssetAllocation.source == latest_key[2],
        ).update({"is_active": True}, synchronize_session=False)

    session.commit()
    return count


def import_asset_allocations_from_csv(session: Session, csv_path: str | Path) -> int:
    csv_file = Path(csv_path)
    with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return import_asset_allocations_from_rows(session, list(reader), csv_file)


def import_industry_allocations_from_rows(
    session: Session,
    rows: Iterable[dict[str, object]],
    source_path: Path | None = None,
) -> int:
    csv_path = source_path or WEB_INPUT_PATH
    normalized_rows = filter_blank_rows(normalize_rows(rows))
    required_fields = ["fund_code", "report_date", "source", "industry_name", "weight_pct"]
    grouped_rows: dict[tuple[str, date, str], list[dict[str, str]]] = defaultdict(list)
    latest_keys: dict[str, tuple[str, date, str]] = {}

    for row_number, row in enumerate(normalized_rows, start=2):
        validate_required_columns(list(row.keys()), required_fields, csv_path)
        fund_code = read_required_value(row, "fund_code", row_number, csv_path)
        report_date = parse_csv_date(
            read_required_value(row, "report_date", row_number, csv_path),
            "report_date",
            row_number,
            csv_path,
        )
        source = read_required_value(row, "source", row_number, csv_path)
        if session.get(Fund, fund_code) is None:
            raise DataImportError(f"Fund {fund_code} not found. Import funds first.")

        key = (fund_code, report_date, source)
        grouped_rows[key].append(
            {
                "industry_name": read_required_value(row, "industry_name", row_number, csv_path),
                "industry_code": read_optional_value(row, "industry_code") or "",
                "weight": f"{parse_percent_to_decimal(read_required_value(row, 'weight_pct', row_number, csv_path), 'weight_pct', row_number, csv_path):.12f}",
            }
        )

        latest_key = latest_keys.get(fund_code)
        if latest_key is None or (report_date, source) > (latest_key[1], latest_key[2]):
            latest_keys[fund_code] = key

    count = 0
    for key, rows in grouped_rows.items():
        fund_code, report_date, source = key
        allocation_rows = session.scalars(
            select(FundIndustryAllocation).where(
                FundIndustryAllocation.fund_code == fund_code,
                FundIndustryAllocation.report_date == report_date,
                FundIndustryAllocation.source == source,
            )
        ).all()
        for allocation in allocation_rows:
            session.delete(allocation)
        session.flush()

        seen_industries: set[tuple[str, str]] = set()
        for row in rows:
            industry_key = (row["industry_name"], row["industry_code"])
            if industry_key in seen_industries:
                raise DataImportError(
                    f"{csv_path} duplicate industry allocation {industry_key} for fund {fund_code} "
                    f"report_date {report_date} source {source}."
                )
            seen_industries.add(industry_key)
            session.add(
                FundIndustryAllocation(
                    fund_code=fund_code,
                    report_date=report_date,
                    source=source,
                    industry_name=row["industry_name"],
                    industry_code=row["industry_code"] or None,
                    weight=float(row["weight"]),
                    is_active=latest_keys[fund_code] == key,
                )
            )
            count += 1

    for fund_code, latest_key in latest_keys.items():
        session.query(FundIndustryAllocation).filter(
            FundIndustryAllocation.fund_code == fund_code,
        ).update({"is_active": False}, synchronize_session=False)
        session.query(FundIndustryAllocation).filter(
            FundIndustryAllocation.fund_code == fund_code,
            FundIndustryAllocation.report_date == latest_key[1],
            FundIndustryAllocation.source == latest_key[2],
        ).update({"is_active": True}, synchronize_session=False)

    session.commit()
    return count


def import_industry_allocations_from_csv(session: Session, csv_path: str | Path) -> int:
    csv_file = Path(csv_path)
    with csv_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return import_industry_allocations_from_rows(session, list(reader), csv_file)
