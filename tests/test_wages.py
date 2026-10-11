"""Unit tests for src/wages.py on synthetic squads (no real data)."""
import math
import sqlite3

import pytest

from wages import allocate_wages, load_squad, top_weekly_k


def test_wages_sum_to_pool():
    wages = allocate_wages({1: 10.0, 2: 25.0, 3: 40.0}, pool_gbp_m=120.0, alpha=0.7)
    assert sum(wages.values()) == pytest.approx(120.0)


def test_alpha_zero_is_equal_split():
    # alpha = 0: every weight is 1, so each of 3 players gets 90 / 3 = 30
    wages = allocate_wages({1: 1.0, 2: 5.0, 3: 50.0}, pool_gbp_m=90.0, alpha=0.0)
    assert all(w == pytest.approx(30.0) for w in wages.values())


def test_hand_computed_allocation():
    # Hand calculation: market values 1 and 4, alpha 0.5, pool 9
    #   weights = 1^0.5 = 1 and 4^0.5 = 2, total 3
    #   wages   = 9 * 1/3 = 3 and 9 * 2/3 = 6
    wages = allocate_wages({"a": 1.0, "b": 4.0}, pool_gbp_m=9.0, alpha=0.5)
    assert wages["a"] == pytest.approx(3.0)
    assert wages["b"] == pytest.approx(6.0)


def test_wages_increase_with_market_value():
    wages = allocate_wages({1: 1.0, 2: 2.0, 3: 4.0, 4: 8.0}, pool_gbp_m=50.0, alpha=0.5)
    ordered = [wages[pid] for pid in (1, 2, 3, 4)]
    assert ordered == sorted(ordered) and len(set(ordered)) == 4


def test_one_player_gets_the_whole_pool():
    assert allocate_wages({7: 3.0}, pool_gbp_m=12.5, alpha=1.3) == {7: pytest.approx(12.5)}


def test_large_alpha_and_values_do_not_overflow():
    wages = allocate_wages({1: 2e8, 2: 1e8}, pool_gbp_m=10.0, alpha=1.5)
    assert sum(wages.values()) == pytest.approx(10.0)


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf])
def test_invalid_market_value_raises(bad):
    with pytest.raises(ValueError):
        allocate_wages({1: 10.0, 2: bad}, pool_gbp_m=10.0, alpha=1.0)


@pytest.mark.parametrize("squad, pool, alpha", [
    ({}, 10.0, 1.0),          # empty squad
    ({1: 1.0}, 0.0, 1.0),     # pool must be positive
    ({1: 1.0}, 10.0, -0.1),   # alpha must be >= 0
])
def test_invalid_inputs_raise(squad, pool, alpha):
    with pytest.raises(ValueError):
        allocate_wages(squad, pool, alpha)


def test_top_weekly_k_conversion():
    # 5.2m a year * 1000 / 52 = 100k a week (the top earner is the 5.2m player)
    assert top_weekly_k({1: 5.2, 2: 1.0}) == pytest.approx(100.0)


def test_top_weekly_k_empty_raises():
    with pytest.raises(ValueError):
        top_weekly_k({})


def test_load_squad_excludes_missing_zero_and_non_squad():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE players (player_id, current_club_id, is_current_squad, "
                 "market_value_in_eur)")
    conn.executemany("INSERT INTO players VALUES (?, ?, ?, ?)", [
        (1, 10, 1, 5_000_000),   # kept
        (2, 10, 1, None),        # missing market value
        (3, 10, 1, 0),           # zero market value
        (4, 10, 0, 9_000_000),   # not in the current squad
        (5, 20, 1, 7_000_000),   # other club
        (6, 10, 1, 2_000_000),   # kept
    ])
    assert load_squad(conn, 10) == {1: 5_000_000.0, 6: 2_000_000.0}
