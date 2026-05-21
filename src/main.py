from __future__ import annotations

import argparse
from pathlib import Path

from .db import get_session_factory
from .estimator import build_estimate_errors, build_fund_estimates
from .import_data import import_actual_returns_from_csv, import_funds_from_yaml, import_holdings_from_csv, import_quotes_from_csv, parse_date
from .init_db import init_db


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FUNDS_YAML = PROJECT_ROOT / "config" / "fund_pool.example.yaml"
DEFAULT_HOLDINGS_CSV = PROJECT_ROOT / "data" / "example_holdings.csv"
DEFAULT_QUOTES_CSV = PROJECT_ROOT / "data" / "example_quotes.csv"
DEFAULT_ACTUALS_CSV = PROJECT_ROOT / "data" / "example_actual_returns.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fund NAV estimator stage 0 CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create SQLite tables")

    parser_funds = subparsers.add_parser("import-funds", help="Import funds from YAML")
    parser_funds.add_argument("--yaml", default=str(DEFAULT_FUNDS_YAML))

    parser_holdings = subparsers.add_parser("import-holdings", help="Import holdings from CSV")
    parser_holdings.add_argument("--csv", default=str(DEFAULT_HOLDINGS_CSV))

    parser_quotes = subparsers.add_parser("import-quotes", help="Import daily quotes from CSV")
    parser_quotes.add_argument("--csv", default=str(DEFAULT_QUOTES_CSV))

    parser_actuals = subparsers.add_parser("import-actuals", help="Import actual returns from CSV")
    parser_actuals.add_argument("--csv", default=str(DEFAULT_ACTUALS_CSV))

    parser_estimate = subparsers.add_parser("estimate", help="Build raw fund estimates")
    parser_estimate.add_argument("--trade-date", required=True)

    parser_reconcile = subparsers.add_parser("reconcile", help="Build estimate error records")
    parser_reconcile.add_argument("--trade-date", required=True)

    parser_demo = subparsers.add_parser("demo-run", help="Run the full example flow")
    parser_demo.add_argument("--trade-date", required=True)

    return parser


def run_demo(trade_date: str) -> None:
    session_factory = get_session_factory()
    init_db()

    with session_factory() as session:
        print(f"Imported funds: {import_funds_from_yaml(session, DEFAULT_FUNDS_YAML)}")
        print(f"Imported holding versions: {import_holdings_from_csv(session, DEFAULT_HOLDINGS_CSV)}")
        print(f"Imported daily quotes: {import_quotes_from_csv(session, DEFAULT_QUOTES_CSV)}")
        print(f"Imported actual returns: {import_actual_returns_from_csv(session, DEFAULT_ACTUALS_CSV)}")
        print(f"Built estimates: {build_fund_estimates(session, parse_date(trade_date))}")
        print(f"Built estimate errors: {build_estimate_errors(session, parse_date(trade_date))}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

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
            count = import_funds_from_yaml(session, args.yaml)
            print(f"Imported funds: {count}")
        elif args.command == "import-holdings":
            count = import_holdings_from_csv(session, args.csv)
            print(f"Imported holding versions: {count}")
        elif args.command == "import-quotes":
            count = import_quotes_from_csv(session, args.csv)
            print(f"Imported daily quotes: {count}")
        elif args.command == "import-actuals":
            count = import_actual_returns_from_csv(session, args.csv)
            print(f"Imported actual returns: {count}")
        elif args.command == "estimate":
            count = build_fund_estimates(session, parse_date(args.trade_date))
            print(f"Built estimates: {count}")
        elif args.command == "reconcile":
            count = build_estimate_errors(session, parse_date(args.trade_date))
            print(f"Built estimate errors: {count}")


if __name__ == "__main__":
    main()
