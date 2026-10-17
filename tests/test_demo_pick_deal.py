"""Tests for src/demo_pick_deal.py on synthetic data."""
import sqlite3

import numpy as np
import pytest

from demo_pick_deal import SEED, candidates, draw_index


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE club_finance (club_id, club_name)")
    conn.executemany("INSERT INTO club_finance VALUES (?, ?)", [(1, "Alpha FC"), (2, "Beta FC")])
    conn.execute("CREATE TABLE players (player_id, name, current_club_id, is_current_squad)")
    conn.executemany("INSERT INTO players VALUES (?, ?, ?, ?)", [
        (30, "Kept C", 2, 1), (10, "Kept A", 1, 1), (20, "Kept B", 1, 1),
        (40, "Too cheap", 1, 1), (50, "Not in squad", 1, 0), (60, "Other club", 99, 1),
        (70, "Stale value", 2, 1), (80, "Only after as_of", 2, 1)])
    conn.execute("CREATE TABLE valuations (player_id, date, market_value_in_eur)")
    conn.executemany("INSERT INTO valuations VALUES (?, ?, ?)", [
        (10, "2026-05-01 00:00:00", 25_000_000), (20, "2026-05-01 00:00:00", 20_000_000),
        (30, "2026-05-01 00:00:00", 60_000_000), (40, "2026-05-01 00:00:00", 19_999_999),
        (50, "2026-05-01 00:00:00", 90_000_000), (60, "2026-05-01 00:00:00", 90_000_000),
        (70, "2025-01-01 00:00:00", 90_000_000), (80, "2026-07-05 00:00:00", 90_000_000)])
    return conn


def test_candidates_filter_and_order():
    pool = candidates(make_db(), "2026-07-01")
    # Kept: 10 (25m), 20 (exactly 20m), 30 (60m), sorted by player_id
    assert [c[0] for c in pool] == [10, 20, 30]
    assert pool[2][3] == "Beta FC"


def test_draw_is_deterministic_and_matches_numpy():
    assert draw_index(3) == draw_index(3)
    assert draw_index(3) == int(np.random.default_rng(SEED).integers(3))
    assert 0 <= draw_index(1000) < 1000


def test_draw_needs_candidates():
    with pytest.raises(ValueError):
        draw_index(0)
