"""Pick one demo deal by an objective, seeded draw (one draw only, no redraws).

Candidates: players with is_current_squad = 1 at one of the 20 database clubs, whose latest
valuation on or before as_of is at most 365 days old (the negotiation engine's guard) and
at least EUR 20m. Candidates are sorted by player_id; the index is
numpy.random.default_rng(20261017).integers(len(candidates)). The seller is that player's
current club. The database is only read.
"""
import sys

import numpy as np

from advise_cli import DEFAULT_DB, open_read_only
from negotiation import player_market_value
from scr import load_config

SEED = 20261017
MIN_MARKET_VALUE_EUR = 20_000_000


def candidates(conn, as_of):
    """[(player_id, name, club_id, club_name, mv_eur, valuation_date)] sorted by player_id."""
    rows = conn.execute(
        "SELECT p.player_id, p.name, p.current_club_id, f.club_name FROM players p "
        "JOIN club_finance f ON f.club_id = p.current_club_id "
        "WHERE p.is_current_squad = 1 ORDER BY p.player_id").fetchall()
    out = []
    for player_id, name, club_id, club_name in rows:
        try:
            mv, when = player_market_value(conn, player_id, as_of)
        except ValueError:   # no valuation, stale (> 365 days) or not positive
            continue
        if mv >= MIN_MARKET_VALUE_EUR:
            out.append((player_id, name, club_id, club_name, mv, when))
    return out


def draw_index(n, seed=SEED):
    """One seeded draw of an index in [0, n)."""
    if n <= 0:
        raise ValueError("No candidates to draw from")
    return int(np.random.default_rng(seed).integers(n))


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    db = args[0] if args else DEFAULT_DB
    as_of = load_config("2026/27")["amortisation"]["as_of"]
    conn = open_read_only(db)
    try:
        pool = candidates(conn, as_of)
    finally:
        conn.close()
    index = draw_index(len(pool))
    player_id, name, club_id, club_name, mv, when = pool[index]
    print(f"Candidates: {len(pool)} (as_of {as_of}, market value >= EUR 20m, seed {SEED})")
    print(f"Index: {index}")
    print(f"Player: {name} (player_id {player_id}), market value {mv / 1e6:.2f}m EUR on {when}")
    print(f"Seller: {club_name} (club_id {club_id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
