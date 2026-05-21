from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy import select

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.backfill import (
        backfill_history,
        fetch_and_store_fund_navs,
        fetch_and_store_stock_quotes,
        get_active_holding_asset_codes,
    )
    from src.data_sources import AKShareDataSource, DataSourceError
    from src.db import get_session_factory
    from src.estimator import (
        build_calibrated_estimates,
        build_calibration_history,
        build_estimate_errors,
        build_fund_estimates,
        build_estimate_history,
        build_reconcile_history,
        calculate_calibration_stats,
        calculate_error_stats,
        format_hit_rate,
        format_missing_assets,
        format_percent,
        format_ratio,
    )
    from src.import_data import (
        DataImportError,
        import_actual_returns_from_csv,
        import_asset_allocations_from_csv,
        import_funds_from_csv,
        import_funds_from_yaml,
        import_holdings_from_csv,
        import_industry_allocations_from_csv,
        import_navs_from_csv,
        import_quotes_from_csv,
        parse_date,
    )
    from src.init_db import init_db
    from src.models import ActualReturn, DailyQuote, FundNav
else:
    from .backfill import (
        backfill_history,
        fetch_and_store_fund_navs,
        fetch_and_store_stock_quotes,
        get_active_holding_asset_codes,
    )
    from .data_sources import AKShareDataSource, DataSourceError
    from .db import get_session_factory
    from .estimator import (
        build_calibrated_estimates,
        build_calibration_history,
        build_estimate_errors,
        build_fund_estimates,
        build_estimate_history,
        build_reconcile_history,
        calculate_calibration_stats,
        calculate_error_stats,
        format_hit_rate,
        format_missing_assets,
        format_percent,
        format_ratio,
    )
    from .import_data import (
        DataImportError,
        import_actual_returns_from_csv,
        import_asset_allocations_from_csv,
        import_funds_from_csv,
        import_funds_from_yaml,
        import_holdings_from_csv,
        import_industry_allocations_from_csv,
        import_navs_from_csv,
        import_quotes_from_csv,
        parse_date,
    )
    from .init_db import init_db
    from .models import ActualReturn, DailyQuote, FundNav


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FUNDS_CSV = PROJECT_ROOT / "data" / "example_funds.csv"
DEFAULT_FUNDS_YAML = PROJECT_ROOT / "config" / "fund_pool.example.yaml"
DEFAULT_HOLDINGS_CSV = PROJECT_ROOT / "data" / "example_holdings.csv"
DEFAULT_QUOTES_CSV = PROJECT_ROOT / "data" / "example_quotes.csv"
DEFAULT_ACTUALS_CSV = PROJECT_ROOT / "data" / "example_actual_returns.csv"
DEFAULT_NAVS_CSV = PROJECT_ROOT / "data" / "example_fund_navs.csv"
DEFAULT_ASSET_ALLOCATIONS_CSV = PROJECT_ROOT / "data" / "example_asset_allocations.csv"
DEFAULT_INDUSTRY_ALLOCATIONS_CSV = PROJECT_ROOT / "data" / "example_industry_allocations.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fund NAV estimator stage 4 CLI")
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

    parser_asset = subparsers.add_parser("import-asset-allocation", help="Import asset allocations from CSV")
    parser_asset.add_argument("--csv", default=str(DEFAULT_ASSET_ALLOCATIONS_CSV))

    parser_industry = subparsers.add_parser("import-industry-allocation", help="Import industry allocations from CSV")
    parser_industry.add_argument("--csv", default=str(DEFAULT_INDUSTRY_ALLOCATIONS_CSV))

    parser_estimate = subparsers.add_parser("estimate", help="Build raw fund estimates")
    parser_estimate.add_argument("--trade-date", required=True)
    parser_estimate.add_argument("--fund-code")

    parser_estimate_history = subparsers.add_parser("estimate-history", help="Build raw fund estimates for a date range")
    parser_estimate_history.add_argument("--start-date", required=True)
    parser_estimate_history.add_argument("--end-date", required=True)
    parser_estimate_history.add_argument("--fund-code")

    parser_reconcile = subparsers.add_parser("reconcile", help="Build estimate error records")
    parser_reconcile.add_argument("--trade-date", required=True)
    parser_reconcile.add_argument("--fund-code")

    parser_reconcile_history = subparsers.add_parser("reconcile-history", help="Build estimate errors for a date range")
    parser_reconcile_history.add_argument("--start-date", required=True)
    parser_reconcile_history.add_argument("--end-date", required=True)
    parser_reconcile_history.add_argument("--fund-code")

    parser_stats = subparsers.add_parser("stats", help="Show historical estimate error stats")
    parser_stats.add_argument("--fund-code")
    parser_stats.add_argument("--window", type=int)

    parser_calibrate = subparsers.add_parser("calibrate", help="Build calibrated estimates")
    parser_calibrate.add_argument("--trade-date", required=True)
    parser_calibrate.add_argument("--window", type=int, default=20)
    parser_calibrate.add_argument("--fund-code")
    parser_calibrate.add_argument("--base", choices=["raw", "coverage_adjusted"], default="raw")
    parser_calibrate.add_argument("--min-samples", type=int, default=5)

    parser_calibrate_history = subparsers.add_parser("calibrate-history", help="Build calibrated estimates for a date range")
    parser_calibrate_history.add_argument("--start-date", required=True)
    parser_calibrate_history.add_argument("--end-date", required=True)
    parser_calibrate_history.add_argument("--window", type=int, default=20)
    parser_calibrate_history.add_argument("--fund-code")
    parser_calibrate_history.add_argument("--base", choices=["raw", "coverage_adjusted"], default="raw")
    parser_calibrate_history.add_argument("--min-samples", type=int, default=5)

    parser_calibration_stats = subparsers.add_parser("calibration-stats", help="Compare raw and calibrated estimate performance")
    parser_calibration_stats.add_argument("--fund-code")
    parser_calibration_stats.add_argument("--window", type=int, default=20)
    parser_calibration_stats.add_argument("--base", choices=["raw", "coverage_adjusted"], default="raw")

    parser_fetch_navs = subparsers.add_parser("fetch-fund-navs", help="Fetch historical fund navs from data source")
    parser_fetch_navs.add_argument("--fund-code", required=True)
    parser_fetch_navs.add_argument("--start-date", required=True)
    parser_fetch_navs.add_argument("--end-date", required=True)

    parser_fetch_quotes = subparsers.add_parser("fetch-stock-quotes", help="Fetch historical stock daily quotes from data source")
    parser_fetch_quotes_group = parser_fetch_quotes.add_mutually_exclusive_group(required=True)
    parser_fetch_quotes_group.add_argument("--asset-code")
    parser_fetch_quotes_group.add_argument("--from-active-holdings", action="store_true")
    parser_fetch_quotes.add_argument("--fund-code")
    parser_fetch_quotes.add_argument("--start-date", required=True)
    parser_fetch_quotes.add_argument("--end-date", required=True)
    parser_fetch_quotes.add_argument("--sleep-seconds", type=float, default=0.2)

    parser_backfill = subparsers.add_parser("backfill-history", help="Fetch historical data and run the full backfill pipeline")
    parser_backfill.add_argument("--fund-code", required=True)
    parser_backfill.add_argument("--start-date", required=True)
    parser_backfill.add_argument("--end-date", required=True)
    parser_backfill.add_argument("--window", type=int, default=20)
    parser_backfill.add_argument("--base", choices=["raw", "coverage_adjusted"], default="coverage_adjusted")
    parser_backfill.add_argument("--min-samples", type=int, default=5)
    parser_backfill.add_argument("--sleep-seconds", type=float, default=0.2)

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


def print_calibration_table(results) -> None:
    headers = ["基金代码", "基金名称", "原始估值", "覆盖率修正", "校准估值", "alpha", "beta", "样本数", "窗口", "训练区间", "MAE", "方向命中率", "状态", "置信度"]
    rows = []
    for result in results:
        train_range = "N/A"
        if result.train_start_date and result.train_end_date:
            train_range = f"{result.train_start_date.isoformat()}~{result.train_end_date.isoformat()}"
        rows.append(
            [
                result.fund_code,
                result.fund_name,
                format_percent(result.raw_estimate, signed=True),
                format_percent(result.coverage_adjusted_estimate, signed=True),
                format_percent(result.calibrated_estimate, signed=True),
                format_percent(result.alpha, signed=True),
                format_ratio(result.beta),
                str(result.sample_count),
                str(result.window),
                train_range,
                format_percent(result.mean_abs_error),
                format_hit_rate(result.direction_hit_rate),
                result.model_status,
                result.confidence_level or "N/A",
            ]
        )
    print_table(headers, rows)


def print_calibration_stats_table(results) -> None:
    headers = ["基金代码", "基金名称", "样本数", "raw_MAE", "calibrated_MAE", "改进比例", "raw_方向命中率", "calibrated_方向命中率", "raw_corr", "calibrated_corr"]
    rows = [
        [
            result.fund_code,
            result.fund_name,
            str(result.sample_count),
            format_percent(result.raw_mean_abs_error),
            format_percent(result.calibrated_mean_abs_error),
            format_percent(result.improvement_pct, signed=True),
            format_hit_rate(result.raw_direction_hit_rate),
            format_hit_rate(result.calibrated_direction_hit_rate),
            format_ratio(result.raw_corr),
            format_ratio(result.calibrated_corr),
        ]
        for result in results
    ]
    print_table(headers, rows)


def print_nav_table(records) -> None:
    headers = ["日期", "基金代码", "单位净值", "累计净值", "真实涨跌", "来源"]
    rows = [
        [
            record["trade_date"],
            record["fund_code"],
            f"{record['unit_nav']:.4f}",
            "N/A" if record["accumulated_nav"] is None else f"{record['accumulated_nav']:.4f}",
            record["actual_return"],
            record["source"],
        ]
        for record in records
    ]
    print_table(headers, rows)


def print_quote_table(records) -> None:
    headers = ["日期", "股票代码", "股票名称", "涨跌幅", "来源"]
    rows = [
        [
            record["trade_date"],
            record["asset_code"],
            record["asset_name"],
            format_percent(record["return_pct"], signed=True),
            record["source"],
        ]
        for record in records
    ]
    print_table(headers, rows)


def print_backfill_summary_table(summaries) -> None:
    headers = ["基金代码", "基金名称", "日期区间", "估算样本数", "raw_MAE", "calibrated_MAE", "improvement", "方向命中率", "置信等级"]
    rows = [
        [
            summary.fund_code,
            summary.fund_name,
            f"{summary.start_date.isoformat()}~{summary.end_date.isoformat()}",
            str(summary.estimate_sample_count),
            format_percent(summary.raw_mean_abs_error),
            format_percent(summary.calibrated_mean_abs_error),
            format_percent(summary.improvement_pct, signed=True),
            format_hit_rate(summary.calibrated_direction_hit_rate),
            summary.confidence_level or "N/A",
        ]
        for summary in summaries
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
        print(f"Imported asset allocations: {import_asset_allocations_from_csv(session, DEFAULT_ASSET_ALLOCATIONS_CSV)}")
        print(f"Imported industry allocations: {import_industry_allocations_from_csv(session, DEFAULT_INDUSTRY_ALLOCATIONS_CSV)}")
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

        calibrate_results = build_calibrated_estimates(
            session,
            trade_date=parse_date(trade_date),
            window=20,
            base="coverage_adjusted",
            min_samples=5,
        )
        print(f"Built calibrated estimates for {trade_date}: {len(calibrate_results)}")
        print_calibration_table(calibrate_results)
        for result in calibrate_results:
            print_warnings(result.warnings)

        history_count = build_calibration_history(
            session,
            start_date=parse_date(trade_dates[0]),
            end_date=parse_date(trade_date),
            window=20,
            base="coverage_adjusted",
            min_samples=5,
        )
        print(f"Built calibration history rows: {history_count}")

        stats_results = calculate_error_stats(session)
        print("Historical stats:")
        print_stats_table(stats_results)

        calibration_stats_results = calculate_calibration_stats(
            session,
            window=20,
            base="coverage_adjusted",
        )
        print("Calibration stats:")
        print_calibration_stats_table(calibration_stats_results)


def build_nav_preview(session, fund_code: str, start_date: str, end_date: str) -> list[dict[str, str | float | None]]:
    start = parse_date(start_date)
    end = parse_date(end_date)
    stmt = (
        select(FundNav, ActualReturn)
        .join(
            ActualReturn,
            (ActualReturn.trade_date == FundNav.trade_date) & (ActualReturn.fund_code == FundNav.fund_code),
            isouter=True,
        )
        .where(
            FundNav.fund_code == fund_code,
            FundNav.trade_date >= start,
            FundNav.trade_date <= end,
        )
        .order_by(FundNav.trade_date.asc())
    )
    rows = []
    for nav, actual in session.execute(stmt).all():
        rows.append(
            {
                "trade_date": nav.trade_date.isoformat(),
                "fund_code": nav.fund_code,
                "unit_nav": nav.unit_nav,
                "accumulated_nav": nav.accumulated_nav,
                "actual_return": "N/A" if actual is None else format_percent(actual.actual_return, signed=True),
                "source": nav.source,
            }
        )
    return rows


def build_quote_preview(session, asset_codes: list[str], start_date: str, end_date: str) -> list[dict[str, str | float]]:
    start = parse_date(start_date)
    end = parse_date(end_date)
    stmt = (
        select(DailyQuote)
        .where(
            DailyQuote.asset_code.in_(asset_codes),
            DailyQuote.trade_date >= start,
            DailyQuote.trade_date <= end,
        )
        .order_by(DailyQuote.trade_date.asc(), DailyQuote.asset_code.asc())
    )
    return [
        {
            "trade_date": quote.trade_date.isoformat(),
            "asset_code": quote.asset_code,
            "asset_name": quote.asset_name,
            "return_pct": quote.return_pct,
            "source": quote.source,
        }
        for quote in session.scalars(stmt).all()
    ]


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
            elif args.command == "import-asset-allocation":
                count = import_asset_allocations_from_csv(session, args.csv)
                print(f"Imported asset allocations: {count}")
            elif args.command == "import-industry-allocation":
                count = import_industry_allocations_from_csv(session, args.csv)
                print(f"Imported industry allocations: {count}")
            elif args.command == "estimate":
                results = build_fund_estimates(session, parse_date(args.trade_date), fund_code=args.fund_code)
                print(f"Built estimates: {len(results)}")
                print_estimate_table(results)
            elif args.command == "estimate-history":
                report = build_estimate_history(
                    session,
                    start_date=parse_date(args.start_date),
                    end_date=parse_date(args.end_date),
                    fund_code=args.fund_code,
                )
                print(f"Built historical estimates: {report.total_count}")
                print_warnings(report.warnings)
            elif args.command == "reconcile":
                report = build_estimate_errors(session, parse_date(args.trade_date), fund_code=args.fund_code)
                print(f"Built estimate errors: {len(report.results)}")
                print_reconcile_table(report.results)
                print_warnings(report.warnings)
            elif args.command == "reconcile-history":
                report = build_reconcile_history(
                    session,
                    start_date=parse_date(args.start_date),
                    end_date=parse_date(args.end_date),
                    fund_code=args.fund_code,
                )
                print(f"Built historical estimate errors: {report.total_count}")
                print_warnings(report.warnings)
            elif args.command == "stats":
                results = calculate_error_stats(
                    session,
                    fund_code=args.fund_code,
                    window=args.window,
                )
                print_stats_table(results)
            elif args.command == "calibrate":
                results = build_calibrated_estimates(
                    session,
                    trade_date=parse_date(args.trade_date),
                    window=args.window,
                    base=args.base,
                    fund_code=args.fund_code,
                    min_samples=args.min_samples,
                )
                print_calibration_table(results)
                for result in results:
                    print_warnings(result.warnings)
            elif args.command == "calibrate-history":
                count = build_calibration_history(
                    session,
                    start_date=parse_date(args.start_date),
                    end_date=parse_date(args.end_date),
                    window=args.window,
                    base=args.base,
                    fund_code=args.fund_code,
                    min_samples=args.min_samples,
                )
                print(f"Built calibration history rows: {count}")
            elif args.command == "calibration-stats":
                results = calculate_calibration_stats(
                    session,
                    fund_code=args.fund_code,
                    window=args.window,
                    base=args.base,
                )
                print_calibration_stats_table(results)
            elif args.command == "fetch-fund-navs":
                data_source = AKShareDataSource()
                report = fetch_and_store_fund_navs(
                    session=session,
                    data_source=data_source,
                    fund_code=args.fund_code,
                    start_date=parse_date(args.start_date),
                    end_date=parse_date(args.end_date),
                )
                print(f"Imported fund nav rows: {report.imported_count}")
                print(f"Generated actual returns from navs: {report.generated_actual_returns}")
                print_nav_table(build_nav_preview(session, args.fund_code, args.start_date, args.end_date))
                print_warnings(report.warnings)
            elif args.command == "fetch-stock-quotes":
                data_source = AKShareDataSource()
                if args.from_active_holdings:
                    if not args.fund_code:
                        raise DataImportError("--fund-code is required with --from-active-holdings.")
                    asset_codes = get_active_holding_asset_codes(
                        session,
                        fund_code=args.fund_code,
                        trade_date=parse_date(args.end_date),
                    )
                    if not asset_codes:
                        raise DataImportError(f"No active holding asset codes found for fund {args.fund_code}.")
                else:
                    asset_codes = [args.asset_code]
                report = fetch_and_store_stock_quotes(
                    session=session,
                    data_source=data_source,
                    start_date=parse_date(args.start_date),
                    end_date=parse_date(args.end_date),
                    asset_codes=asset_codes,
                    sleep_seconds=args.sleep_seconds,
                )
                print(f"Imported daily quotes: {report.imported_count}")
                print_quote_table(build_quote_preview(session, asset_codes, args.start_date, args.end_date)[:20])
                print_warnings(report.warnings)
            elif args.command == "backfill-history":
                data_source = AKShareDataSource()
                nav_report, quote_report, estimate_report, reconcile_report, calibration_count, summaries = backfill_history(
                    session=session,
                    data_source=data_source,
                    fund_code=args.fund_code,
                    start_date=parse_date(args.start_date),
                    end_date=parse_date(args.end_date),
                    window=args.window,
                    base=args.base,
                    min_samples=args.min_samples,
                    sleep_seconds=args.sleep_seconds,
                )
                print(f"Imported fund nav rows: {nav_report.imported_count}")
                print(f"Imported daily quotes: {quote_report.imported_count}")
                print(f"Built historical estimates: {estimate_report.total_count}")
                print(f"Built historical estimate errors: {reconcile_report.total_count}")
                print(f"Built calibration history rows: {calibration_count}")
                print_warnings(nav_report.warnings + quote_report.warnings + estimate_report.warnings + reconcile_report.warnings)
                print_backfill_summary_table(summaries)
    except (DataImportError, DataSourceError) as exc:
        print(f"Import error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
