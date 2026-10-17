"""Command line for the v1 negotiation engine. Opens the database read-only.

Example:
    python src/negotiate_cli.py --seller-club-id 762 --player-id 12345
    python src/negotiate_cli.py --seller-club-id 762 --player-id 12345 --buyers 11,31 --rule not_red
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

from advise_cli import DEFAULT_DB, open_read_only
from advisor import load_advisor_config
from negotiation import (
    REPO_ROOT, RULES, format_text, load_fee_fallback, load_negotiation_config, run_negotiation,
)
from scr import load_config


def build_parser():
    parser = argparse.ArgumentParser(
        description="One round of sealed bids for one player (baseline policy, no LLM). "
                    "Results are illustrative, not real-world conclusions.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path")
    # Application default (v1 runs one season), not finance data.
    parser.add_argument("--season", default="2026/27")
    parser.add_argument("--seller-club-id", type=int, required=True)
    parser.add_argument("--player-id", type=int, required=True)
    parser.add_argument("--buyers", help="comma-separated buyer club_ids (2 or 3); default: "
                                         "the 3 eligible clubs with the largest headroom")
    parser.add_argument("--rule", choices=sorted(RULES),
                        help="acceptance rule; default from config/negotiation.toml")
    parser.add_argument("--fee-results", help="fee model results file; default from "
                                              "config/negotiation.toml")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not Path(args.db).exists():
        print(f"ERROR: database not found: {args.db}", file=sys.stderr)
        return 1
    try:
        buyers = ([int(x) for x in args.buyers.split(",")] if args.buyers else None)
        rules_config = load_config(args.season)
        neg_config = load_negotiation_config()
        fee_path = args.fee_results or REPO_ROOT / neg_config["sources"]["fee_results_path"]
        fallback = load_fee_fallback(fee_path)
        conn = open_read_only(args.db)
        try:
            result = run_negotiation(conn, rules_config, neg_config, load_advisor_config(),
                                     fallback, args.seller_club_id, args.player_id,
                                     buyer_ids=buyers, rule=args.rule)
        finally:
            conn.close()
    except (ValueError, LookupError, OSError, sqlite3.Error) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, allow_nan=False) if args.json else format_text(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
