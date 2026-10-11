"""Tests for src/phase3_eval_wage_model.py: metric functions and an end-to-end run on
synthetic fixtures. The real database is never read here."""
import copy
import json
import math
import sqlite3

import pandas as pd
import pytest

from phase3_eval_wage_model import (
    REGISTERED, alpha_grid, log_distance, passes, run_evaluation, select_alpha,
    verdict, wilson_interval, write_atomic,
)


# ---------- Metric functions ----------

def test_alpha_grid_is_registered_grid():
    grid = alpha_grid()
    assert len(grid) == 25
    assert grid[0] == 0.30 and grid[-1] == 1.50
    assert 0.55 in grid and 1.0 in grid   # exact values, no float drift


@pytest.mark.parametrize("est, expected", [
    (100.0, 0.0),                    # inside [80, 120]
    (80.0, 0.0),                     # on the lower bound
    (40.0, math.log(80 / 40)),       # below: ln(2)
    (240.0, math.log(240 / 120)),    # above: ln(2)
])
def test_log_distance(est, expected):
    assert log_distance(est, 80.0, 120.0, lower_bound=False) == pytest.approx(expected)


def test_log_distance_lower_bound_has_no_upper_side():
    assert log_distance(10_000.0, 300.0, 300.0, lower_bound=True) == 0.0
    assert log_distance(150.0, 300.0, 300.0, lower_bound=True) == pytest.approx(math.log(2))


@pytest.mark.parametrize("est, expected", [
    (64.0, True),    # 80 / 1.25 = 64, on the widened lower edge
    (63.9, False),
    (150.0, True),   # 120 * 1.25 = 150, on the widened upper edge
    (150.1, False),
])
def test_passes_widened_interval(est, expected):
    assert passes(est, 80.0, 120.0, lower_bound=False, widen=1.25) is expected


def test_passes_lower_bound_row():
    # Interval [300, inf): pass if est >= 300 / 1.25 = 240, no upper limit
    assert passes(240.0, 300.0, 300.0, lower_bound=True, widen=1.25) is True
    assert passes(239.0, 300.0, 300.0, lower_bound=True, widen=1.25) is False
    assert passes(5_000.0, 300.0, 300.0, lower_bound=True, widen=1.25) is True


def test_wilson_matches_criteria_doc():
    # docs/wage_model_criteria.md: 6/8 gives about [0.41, 0.93], 8/8 about [0.68, 1.00]
    low, high = wilson_interval(6, 8, 1.96)
    assert (round(low, 2), round(high, 2)) == (0.41, 0.93)
    low, high = wilson_interval(8, 8, 1.96)
    assert (round(low, 2), round(high, 2)) == (0.68, 1.0)


def test_wilson_rejects_bad_counts():
    with pytest.raises(ValueError):
        wilson_interval(9, 8, 1.96)


def test_select_alpha_tie_takes_smaller_and_reports_plateau():
    means = {0.3: 0.2, 0.35: 0.0, 0.4: 1e-13, 0.45: 0.0, 0.5: 0.1}
    alpha, plateau = select_alpha(means, tolerance=1e-12)
    assert alpha == 0.35
    assert plateau == [0.35, 0.4, 0.45]   # 1e-13 is within the tie tolerance


@pytest.mark.parametrize("model, equal, a, b", [
    (6, 5, True, True),     # 6/8 = 75% and more than equal split
    (6, 6, True, False),    # B fails on a draw with equal split
    (5, 2, False, True),    # A fails: 5/8 < 75%
])
def test_verdict(model, equal, a, b):
    result = verdict(model, 8, equal, 0.75)
    assert (result["condition_a"], result["condition_b"]) == (a, b)
    assert result["accepted"] is (a and b)


# ---------- End-to-end on synthetic fixtures ----------
#
# Every club has two squad players with market values 1 and 4, and a pool of 5.2m, so
#   top share  = 4^a / (1 + 4^a)
#   top weekly = 5.2 * share * 1000 / 52 = 100 * share (GBP k)
# alpha = 0 -> 50, alpha = 1 -> 100 * 4/5 = 80. Two extra players per club must be
# ignored (one not in the squad, one without a market value).

def make_fixture(ref_rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE players (player_id, current_club_id, is_current_squad, "
                 "market_value_in_eur)")
    seed_rows, players = [], []
    for i, (cid, *_rest) in enumerate(ref_rows):
        seed_rows.append({"club_id": cid, "wage_bill_gbp_m": 5.2, "revenue_basis": "PL"})
        base = 10 * i
        players += [(base + 1, cid, 1, 1_000_000), (base + 2, cid, 1, 4_000_000),
                    (base + 3, cid, 0, 99_000_000), (base + 4, cid, 1, None)]
    conn.executemany("INSERT INTO players VALUES (?, ?, ?, ?)", players)
    reference = pd.DataFrame(
        [{"club_id": cid, "club_name": f"Club {cid}", "split": split,
          "ref_min_weekly_gbp_k": lo, "ref_max_weekly_gbp_k": hi, "max_is_lower_bound": lb}
         for cid, split, lo, hi, lb in ref_rows])
    return conn, pd.DataFrame(seed_rows), reference


def test_end_to_end_accepted():
    # Fit clubs pin the estimate to exactly 80, which only alpha = 1.0 gives.
    # Test: club 3 [80, 80] -> 80 passes; club 4 [70, 90] -> 80 passes.  Model 2/2.
    # Equal split gives 50: club 3 needs >= 64, club 4 needs >= 56 -> 0/2.
    # A: 2 >= 0.75 * 2, B: 2 > 0 -> accepted.
    conn, seed, ref = make_fixture([
        (1, "fit", 80, 80, False), (2, "fit", 80, 80, False),
        (3, "test", 80, 80, False), (4, "test", 70, 90, False),
    ])
    result = run_evaluation(conn, seed, ref, copy.deepcopy(REGISTERED))
    assert result["selected_alpha"] == 1.0
    assert result["plateau_alphas"] == [1.0]
    assert result["selected_alpha_at_boundary"] is False
    assert result["test"]["passes_model"] == 2
    assert result["test"]["passes_equal_split"] == 0
    assert result["verdict"]["accepted"] is True
    assert all(row["n_players"] == 2 for row in result["clubs"])
    est_by_club = {row["club_id"]: row["est_selected_k"] for row in result["clubs"]}
    assert est_by_club[3] == pytest.approx(80.0)


def test_end_to_end_failure_is_reported():
    # Same fit clubs (alpha = 1.0). Test club 4 now [60, 60]: model 80 > 75 fails,
    # equal split 50 is in [48, 75] and passes. Model 1/2 vs equal 1/2:
    # A: 1 < 1.5 fails, B: 1 > 1 fails -> not accepted.
    conn, seed, ref = make_fixture([
        (1, "fit", 80, 80, False), (2, "fit", 80, 80, False),
        (3, "test", 80, 80, False), (4, "test", 60, 60, False),
    ])
    result = run_evaluation(conn, seed, ref, copy.deepcopy(REGISTERED))
    assert result["test"]["passes_model"] == 1
    assert result["test"]["passes_equal_split"] == 1
    assert result["verdict"] == {"condition_a": False, "condition_b": False,
                                 "accepted": False}


def test_end_to_end_plateau_and_boundary():
    # Fit intervals [60, 100]: 100 * share >= 60 needs 4^a >= 1.5, i.e. a >= 0.29, so every
    # grid alpha has d = 0. Plateau = whole grid, pick = 0.30, which is on the boundary.
    conn, seed, ref = make_fixture([
        (1, "fit", 60, 100, False), (2, "fit", 60, 100, False),
        (3, "test", 80, 80, False), (4, "test", 300, 300, True),
    ])
    result = run_evaluation(conn, seed, ref, copy.deepcopy(REGISTERED))
    assert result["plateau_alphas"] == alpha_grid()
    assert result["selected_alpha"] == 0.30
    assert result["selected_alpha_at_boundary"] is True
    # Reverse: test clubs pick alpha, fit clubs [60, 100] are evaluated
    assert result["reverse"]["n_fit"] == 2


def test_results_are_json_serialisable_and_written_atomically(tmp_path):
    # The real run must not crash at the very end on a numpy type or a NaN.
    conn, seed, ref = make_fixture([
        (1, "fit", 80, 80, False), (2, "test", 300, 300, True),
    ])
    result = run_evaluation(conn, seed, ref, copy.deepcopy(REGISTERED))
    target = tmp_path / "out" / "result.json"
    write_atomic(target, result)
    assert json.loads(target.read_text(encoding="utf-8"))["selected_alpha"] == 1.0
    assert list(target.parent.iterdir()) == [target]   # no temp file left behind


def test_end_to_end_empty_squad_raises():
    conn, seed, ref = make_fixture([(1, "fit", 80, 80, False)])
    conn.execute("DELETE FROM players")
    with pytest.raises(ValueError):
        run_evaluation(conn, seed, ref, copy.deepcopy(REGISTERED))
