"""Command line for the one-deal SCR advisor. Opens the database read-only.

Example:
    python src/advise_cli.py --club-id 762 --fee 50 --fee-currency GBP \
        --wage-gbp-m 5.2 --contract-years 5
    python src/advise_cli.py --list-clubs
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

from advisor import (
    IncomingPlayer, OutgoingPlayer, advise_deal, eligible_clubs, format_text,
    load_advisor_config, to_dict,
)
from scr import load_config

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "transfer_window_ai.db"


def open_read_only(path):
    """Open SQLite read-only via a file URI (safe for Windows paths with spaces)."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def build_parser():
    parser = argparse.ArgumentParser(
        description="SCR impact of one permanent deal (rule-based, no LLM). "
                    "Wages are user inputs; results are not real-world conclusions.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path")
    # Application default (v1 runs one season), not finance data.
    parser.add_argument("--season", default="2026/27")
    parser.add_argument("--list-clubs", action="store_true",
                        help="list the clubs that can be buyers, then exit")
    parser.add_argument("--club-id", type=int, help="buyer club_id")
    parser.add_argument("--fee", type=float, help="transfer fee, in millions")
    parser.add_argument("--fee-currency", choices=["EUR", "GBP"])
    parser.add_argument("--wage-gbp-m", type=float, help="incoming annual wage, GBP m")
    parser.add_argument("--contract-years", type=float, help="new contract length in years")
    parser.add_argument("--agent-fee-gbp-m", type=float,
                        help="optional; assumed 0 (with a warning) if omitted")
    parser.add_argument("--out-wage-gbp-m", type=float, help="outgoing annual wage, GBP m")
    parser.add_argument("--out-amortisation-gbp-m", type=float,
                        help="outgoing annual amortisation, GBP m (0 allowed)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not Path(args.db).exists():
        print(f"ERROR: database not found: {args.db}", file=sys.stderr)
        return 1

    try:
        config = load_config(args.season)
        conn = open_read_only(args.db)
        try:
            if args.list_clubs:
                for club_id, name, uefa in eligible_clubs(
                        conn, config["data_vintage"]["finance_fiscal_year"]):
                    print(f"{club_id:>6}  {name}  (UEFA next season: {uefa})")
                return 0

            required = {"--club-id": args.club_id, "--fee": args.fee,
                        "--fee-currency": args.fee_currency, "--wage-gbp-m": args.wage_gbp_m,
                        "--contract-years": args.contract_years}
            missing = [name for name, value in required.items() if value is None]
            if missing:
                parser.error(f"missing required arguments: {', '.join(missing)}")
            out_given = [args.out_wage_gbp_m is not None,
                         args.out_amortisation_gbp_m is not None]
            if any(out_given) and not all(out_given):
                parser.error("give both --out-wage-gbp-m and --out-amortisation-gbp-m, "
                             "or neither")

            incoming = IncomingPlayer(
                fee_amount=args.fee, fee_currency=args.fee_currency,
                annual_wage_gbp_m=args.wage_gbp_m, contract_years=args.contract_years,
                agent_fee_gbp_m=args.agent_fee_gbp_m)
            outgoing = (OutgoingPlayer(args.out_wage_gbp_m, args.out_amortisation_gbp_m)
                        if all(out_given) else None)
            result = advise_deal(conn, config, args.club_id, incoming, outgoing,
                                 advisor_config=load_advisor_config())
        finally:
            conn.close()
    except (ValueError, LookupError, OSError, sqlite3.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(to_dict(result), indent=2, allow_nan=False))
    else:
        print(format_text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
