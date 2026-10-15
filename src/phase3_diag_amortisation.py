"""Report-only diagnostic for the outgoing-player amortisation helper. No pass/fail threshold.

For each club that can be a buyer, sum the estimated annual amortisation over the current
squad and compare it with player_amortisation_gbp_m in the finance seed. The squad is the
July 2026 snapshot and the seed is FY 2024/25: different periods, so the ratio is only a
rough consistency check, never a validation.
"""
import sys
from collections import Counter
from pathlib import Path

from advise_cli import DEFAULT_DB, open_read_only
from advisor import eligible_clubs
from outgoing_amortisation import estimate_annual_amortisation, load_transfers, pick_purchase, to_date
from scr import load_config

NOTES = [
    "Report only: no pass/fail threshold.",
    "Different periods: squad = July 2026 snapshot, seed amortisation = FY 2024/25 accounts. "
    "The seed includes players sold since then and excludes later signings.",
    "Estimates are approximations (confidence low). Unknown players are excluded from the sum.",
]
STATUSES = ("estimated", "zero_fee", "likely_fully_amortised", "unknown")


def diagnose(conn, config):
    """One dict per buyer club: status counts, estimated sum, seed figure and their ratio."""
    fiscal_year = config["data_vintage"]["finance_fiscal_year"]
    as_of = to_date(config["amortisation"]["as_of"])
    rows = []
    for club_id, club_name, _uefa in eligible_clubs(conn, fiscal_year):
        squad = conn.execute(
            "SELECT player_id, contract_expiration_date FROM players "
            "WHERE current_club_id = ? AND is_current_squad = 1", (club_id,)).fetchall()
        counts, total, understated, capped = Counter(), 0.0, 0, 0
        for player_id, expiry in squad:
            purchase, ever_left = pick_purchase(load_transfers(conn, player_id), club_id)
            estimate = estimate_annual_amortisation(purchase, ever_left, expiry, as_of, config)
            counts[estimate.status] += 1
            understated += estimate.possibly_understated
            capped += estimate.cap_applied
            if estimate.annual_gbp_m is not None:
                total += estimate.annual_gbp_m
        seed = conn.execute(
            "SELECT player_amortisation_gbp_m FROM club_finance "
            "WHERE club_id = ? AND fiscal_year = ?", (club_id, fiscal_year)).fetchone()[0]
        rows.append({"club_id": club_id, "club_name": club_name, "n_players": len(squad),
                     **{s: counts[s] for s in STATUSES}, "possibly_understated": understated,
                     "cap_applied": capped,
                     "estimated_sum_gbp_m": total, "seed_amortisation_gbp_m": seed,
                     "ratio": total / seed if seed else None})
    return rows


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    db = Path(args[0]) if args else DEFAULT_DB
    config = load_config("2026/27")   # application default season, as in advise_cli.py
    conn = open_read_only(db)
    try:
        rows = diagnose(conn, config)
    finally:
        conn.close()
    for note in NOTES:
        print("NOTE:", note)
    print()
    print("Columns: est = estimated, zero = zero_fee, full = likely_fully_amortised, "
          "unk = unknown, under = possibly_understated (probably extended, real charge may "
          "be higher), capped = cap_applied (still amortising, real charge may be lower).")
    print(f"{'club':24}{'n':>4}{'est':>5}{'zero':>5}{'full':>5}{'unk':>5}{'under':>6}"
          f"{'capped':>7}{'sum est':>10}{'seed':>9}{'ratio':>7}")
    for r in rows:
        ratio = f"{r['ratio']:.2f}" if r["ratio"] is not None else "n/a"
        print(f"{r['club_name'][:23]:24}{r['n_players']:>4}{r['estimated']:>5}"
              f"{r['zero_fee']:>5}{r['likely_fully_amortised']:>5}{r['unknown']:>5}"
              f"{r['possibly_understated']:>6}{r['cap_applied']:>7}"
              f"{r['estimated_sum_gbp_m']:>10.1f}{r['seed_amortisation_gbp_m']:>9.1f}{ratio:>7}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
