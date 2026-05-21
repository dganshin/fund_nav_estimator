from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.db import get_session_factory
    from src.estimator import (
        build_estimate_errors,
        build_fund_estimates,
        calculate_error_stats,
        format_hit_rate,
        format_missing_assets,
        format_percent,
        format_ratio,
    )
    from src.import_data import (
        DataImportError,
        import_actual_returns_from_csv,
        import_funds_from_csv,
        import_funds_from_yaml,
        import_holdings_from_csv,
        import_navs_from_csv,
        import_quotes_from_csv,
        parse_date,
    )
    from src.init_db import init_db
else:
    from .db import get_session_factory
    from .estimator import (
        build_estimate_errors,
        build_fund_estimates,
        calculate_error_stats,
        format_hit_rate,
        format_missing_assets,
        format_percent,
        format_ratio,
    )
    from .import_data import (
        DataImportError,
        import_actual_returns_from_csv,
        import_funds_from_csv,
        import_funds_from_yaml,
        import_holdings_from_csv,
        import_navs_from_csv,
        import_quotes_from_csv,
        parse_date,
    )
    from .init_db import init_db


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FUNDS_CSV = PROJECT_ROOT / "data" / "example_funds.csv"
DEFAULT_FUNDS_YAML = PROJECT_ROOT / "config" / "fund_pool.example.yaml"
DEFAULT_HOLDINGS_CSV = PROJECT_ROOT / "data" / "example_holdings.csv"
DEFAULT_QUOTES_CSV = PROJECT_ROOT / "data" / "example_quotes.csv"
DEFAULT_ACTUALS_CSV = PROJECT_ROOT / "data" / "example_actual_returns.csv"
DEFAULT_NAVS_CSV = PROJECT_ROOT / "data" / "example_fund_navs.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fund NAV estimator stage 2 CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create SQLite tables")

    parser_funds = subparsers.add_parser("import-funds", help="Import funds from CSV or YAML")
    parser_funds_group = parser_funds.add_mutually_exclusive_group()
    parser_funds_group.add_argument("--csv", default=str(DEFAULT_FUNDS_CSV))
    parser_funds_group.add_argument("--yaml")

    parser_holdings = subparsers.add_parser("import-holdings", help="Import holdings from CSV")
    parser_holdings.add_argument("--csv", default=str(DEFAULT_HOLDINGS_CSV))

    parser_quotes = subparsers.add_parser("import-quotes", help="Import daily quotes from CSV")
    parser_quotes.add_argument("--csv", default=str(DEFAULT_QUOTES_CSV))

    parser_actuals = subparsers.add_parser("import-actuals", help="Import actual returns from CSV")
    parser_actuals.add_argument("--csv", default=str(DEFAULT_ACTUALS_CSV))

    parser_navs = subparsers.add_parser("import-navs", help="Import fund navs from CSV")
    parser_navs.add_argument("--csv", default=str(DEFAULT_NAVS_CSV))

    parser_estimate = subparsers.add_parser("estimate", help="Build raw fund estimates")
    parser_estimate.add_argument("--trade-date", required=True)

    parser_reconcile = subparsers.add_parser("reconcile", help="Build estimate error records")
    parser_reconcile.add_argument("--trade-date", required=True)

    parser_stats = subparsers.add_parser("stats", help="Show historical estimate error stats")
    parser_stats.add_argument("--fund-code")
    parser_stats.add_argument("--window", type=int)

    parser_demo = subparsers.add_parser("demo-run", help="Run the full example flow")
    parser_demo.add_argument("--trade-date", required=True)

    return parser


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(str(value)))

    print(" | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    for row in rows:
        print(" | ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))


def print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(warning)


def print_estimate_table(results) -> None:
    headers = ["基金代码", "基金名称", "原始估值", "覆盖权重", "缺失权重", "缺失资产", "warning"]
    rows = [
        [
            result.fund_code,
            result.fund_name,
            format_percent(result.raw_estimate, signed=True),
            format_percent(result.covered_weight),
            format_percent(result.missing_weight),
            format_missing_assets(result.missing_assets),
            result.warning,
        ]
        for result in results
    ]
    print_table(headers, rows)


def print_reconcile_table(results) -> None:
    headers = ["基金代码", "基金名称", "原始估值", "真实涨跌", "误差", "绝对误差", "方向命中"]
    rows = [
        [
            result.fund_code,
            result.fund_name,
            format_percent(result.raw_estimate, signed=True),
            format_percent(result.actual_return, signed=True),
            format_percent(result.error, signed=True),
            format_percent(result.abs_error),
            "是" if result.direction_hit else "否",
        ]
        for result in results
    ]
    print_table(headers, rows)


def print_stats_table(results) -> None:
    headers = ["基金代码", "基金名称", "样本数", "平均误差", "平均绝对误差", "最大绝对误差", "方向命中率", "相关系数", "最近误差", "最近交易日"]
    rows = [
        [
            result.fund_code,
            result.fund_name,
            str(result.sample_count),
            format_percent(result.mean_error, signed=True),
            format_percent(result.mean_abs_error),
            format_percent(result.max_abs_error),
            format_hit_rate(result.direction_hit_rate),
            format_ratio(result.estimate_actual_corr),
            format_percent(result.latest_error, signed=True),
            result.latest_trade_date.isoformat(),
        ]
        for result in results
    ]
    print_table(headers, rows)


def load_trade_dates_from_quotes(csv_path: Path, max_trade_date: str) -> list[str]:
    latest_date = parse_date(max_trade_date)
    trade_dates: set[str] = set()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            trade_date = parse_date(row["trade_date"])
            if trade_date <= latest_date:
                trade_dates.add(trade_date.isoformat())
    return sorted(trade_dates)


def run_demo(trade_date: str) -> None:
    session_factory = get_session_factory()
    init_db()
    trade_dates = load_trade_dates_from_quotes(DEFAULT_QUOTES_CSV, trade_date)

    with session_factory() as session:
        print(f"Imported funds: {import_funds_from_csv(session, DEFAULT_FUNDS_CSV)}")
        print(f"Imported holding versions: {import_holdings_from_csv(session, DEFAULT_HOLDINGS_CSV)}")
        print(f"Imported daily quotes: {import_quotes_from_csv(session, DEFAULT_QUOTES_CSV)}")
        nav_report = import_navs_from_csv(session, DEFAULT_NAVS_CSV)
        print(f"Imported fund nav rows: {nav_report.imported_count}")
        print(f"Generated actual returns from navs: {nav_report.generated_actual_returns}")
        print_warnings(nav_report.warnings)

        for current_trade_date in trade_dates:
            results = build_fund_estimates(session, parse_date(current_trade_date))
            print(f"Built estimates for {current_trade_date}: {len(results)}")
            print_estimate_table(results)

            reconcile_report = build_estimate_errors(session, parse_date(current_trade_date))
            print(f"Built estimate errors for {current_trade_date}: {len(reconcile_report.results)}")
            print_reconcile_table(reconcile_report.results)
            print_warnings(reconcile_report.warnings)

        stats_results = calculate_error_stats(session)
        print("Historical stats:")
        print_stats_table(stats_results)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "init-db":
            init_db()
            print("Database initialized.")
            return

        if args.command == "demo-run":
            run_demo(args.trade_date)
            return

        session_factory = get_session_factory()
        with session_factory() as session:
            if args.command == "import-funds":
                if args.yaml:
                    count = import_funds_from_yaml(session, args.yaml)
                else:
                    count = import_funds_from_csv(session, args.csv)
                print(f"Imported funds: {count}")
            elif args.command == "import-holdings":
                count = import_holdings_from_csv(session, args.csv)
                print(f"Imported holding versions: {count}")
            elif args.command == "import-quotes":
                count = import_quotes_from_csv(session, args.csv)
                print(f"Imported daily quotes: {count}")
            elif args.command == "import-actuals":
                report = import_actual_returns_from_csv(session, args.csv)
                print(f"Imported actual returns: {report.imported_count}")
                print_warnings(report.warnings)
            elif args.command == "import-navs":
                report = import_navs_from_csv(session, args.csv)
                print(f"Imported fund nav rows: {report.imported_count}")
                print(f"Generated actual returns from navs: {report.generated_actual_returns}")
                print_warnings(report.warnings)
            elif args.command == "estimate":
                results = build_fund_estimates(session, parse_date(args.trade_date))
                print(f"Built estimates: {len(results)}")
                print_estimate_table(results)
            elif args.command == "reconcile":
                report = build_estimate_errors(session, parse_date(args.trade_date))
                print(f"Built estimate errors: {len(report.results)}")
                print_reconcile_table(report.results)
                print_warnings(report.warnings)
            elif args.command == "stats":
                results = calculate_error_stats(
                    session,
                    fund_code=args.fund_code,
                    window=args.window,
                )
                print_stats_table(results)
    except DataImportError as exc:
        print(f"Import error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
