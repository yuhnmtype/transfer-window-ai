"""Tests for src/fee_model.py on synthetic data only (the real database is never read)."""
import math
import sqlite3

import numpy as np
import pandas as pd
import pytest

from fee_model import (
    FEATURES, FILTERS, ape, baseline_b1, baseline_b2, build_dataset, choose_best_baseline,
    conformal_quantile, coverage, fit_ols, make_features, mape, mdape, mean_width_eur_m,
    paired_bootstrap, predict_interval, split_of, train_median_ratio, window_of,
)


def make_db(transfers, valuations, players):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE transfers (player_id INTEGER, transfer_date TIMESTAMP, "
                 "from_club_id INTEGER, to_club_id INTEGER, transfer_fee REAL)")
    conn.execute("CREATE TABLE valuations (player_id INTEGER, date TIMESTAMP, "
                 "market_value_in_eur INTEGER)")
    conn.execute("CREATE TABLE players (player_id INTEGER, date_of_birth TIMESTAMP, "
                 "position TEXT)")
    conn.executemany("INSERT INTO transfers VALUES (?, ?, ?, ?, ?)", transfers)
    conn.executemany("INSERT INTO valuations VALUES (?, ?, ?)", valuations)
    conn.executemany("INSERT INTO players VALUES (?, ?, ?)", players)
    return conn


# ---------- Windows and split ----------

@pytest.mark.parametrize("month, expected", [
    (1, "W2024"), (3, "W2024"), (4, None), (5, None), (6, "S2024"), (10, "S2024"),
    (11, None), (12, None),
])
def test_window_of(month, expected):
    assert window_of(month, 2024) == expected


@pytest.mark.parametrize("window, expected", [
    ("S2012", "train"), ("S2023", "train"), ("W2024", "train"),
    ("S2024", "calibration"), ("W2025", "calibration"),
    ("S2025", "test"), ("W2026", "test"),
])
def test_split_of(window, expected):
    assert split_of(window) == expected


def test_split_of_unplaced_window_raises():
    with pytest.raises(ValueError):
        split_of("W2027")


# ---------- Filters ----------

PLAYERS = [(p, "2000-07-15 00:00:00", "Midfield") for p in range(1, 13)]
PLAYERS[9] = (10, None, "Midfield")               # player 10: no date of birth


def filter_fixture():
    """One transfer per filter, plus two kept rows (players 11 and 12)."""
    transfers = [
        (1, "2023-07-10", 1, 2, 0.0),             # f1: fee 0
        (2, "2023-07-10", 1, 2, None),            # f1: fee missing
        (3, "2023-07-10", 1, 2, 10e6),            # f2: no valuation at all
        (4, "2023-07-10", 1, 2, 10e6),            # f2: only a valuation AFTER the transfer
        (5, "2023-07-10", 1, 2, 10e6),            # f2: latest valuation is 0
        (6, "2023-07-10", 1, 2, 10e6),            # f3: valuation 400 days old
        (7, "2023-07-10", 1, 2, 5e6),             # f4: moves back 100 days later ...
        (7, "2023-10-18", 2, 1, 0.0),             # ... (this return row is removed by f1)
        (8, "2023-11-15", 1, 2, 10e6),            # f5: November is outside both windows
        (9, "2026-07-01", 1, 2, 10e6),            # f6: summer 2026
        (10, "2023-07-10", 1, 2, 10e6),           # f7: no date of birth
        (11, "2024-07-15", 1, 2, 20e6),           # kept: S2024 -> calibration
        (12, "2023-01-20", 1, 2, 8e6),            # kept: W2023 -> train, same-day valuation
    ]
    valuations = [
        (4, "2023-08-01", 10e6),
        (5, "2022-07-01", 9e6), (5, "2023-07-01", 0),
        (6, "2022-06-05", 10e6),                  # 400 days before 2023-07-10
        (7, "2023-07-01", 5e6), (8, "2023-11-01", 10e6), (9, "2026-06-01", 10e6),
        (10, "2023-07-01", 10e6),
        (11, "2024-01-01", 10e6), (11, "2024-06-01", 15e6), (11, "2024-08-01", 30e6),
        (12, "2023-01-20", 6e6),
    ]
    return make_db([(p, f"{d} 00:00:00", a, b, fee) for p, d, a, b, fee in transfers],
                   [(p, f"{d} 00:00:00", v) for p, d, v in valuations], PLAYERS)


def test_build_dataset_exclusion_counts():
    data, exclusions = build_dataset(filter_fixture())
    assert exclusions == {
        FILTERS[0]: 3,   # players 1, 2 and the return row of player 7
        FILTERS[1]: 3,   # players 3, 4, 5
        FILTERS[2]: 1,   # player 6
        FILTERS[3]: 1,   # player 7
        FILTERS[4]: 1,   # player 8
        FILTERS[5]: 1,   # player 9
        FILTERS[6]: 1,   # player 10
    }
    assert list(data["player_id"]) == [12, 11]   # ordered by transfer date


def test_build_dataset_point_in_time_value_age_and_split():
    data, _ = build_dataset(filter_fixture())
    row = data.set_index("player_id").loc[11]
    # Latest valuation on or before 2024-07-15 is 15m (the 30m from August is ignored)
    assert row["mv_eur"] == 15e6
    # 2000-07-15 -> 2024-07-15 = 24 * 365 + 6 leap days = 8766 days = 24.0 years
    assert row["age"] == pytest.approx(24.0)
    assert (row["window"], row["split"]) == ("S2024", "calibration")
    same_day = data.set_index("player_id").loc[12]
    assert (same_day["mv_eur"], same_day["split"]) == (6e6, "train")


def test_build_dataset_never_reads_transfer_fee_clean():
    # The fixture has no transfer_fee_clean column at all; build_dataset must still work.
    data, _ = build_dataset(filter_fixture())
    assert "transfer_fee_clean" not in data.columns


def test_unregistered_position_raises():
    conn = make_db([(1, "2023-07-10 00:00:00", 1, 2, 10e6)],
                   [(1, "2023-07-01 00:00:00", 10e6)],
                   [(1, "2000-01-01 00:00:00", "Missing")])
    with pytest.raises(ValueError):
        build_dataset(conn)


def test_duplicate_valuation_dates_raise():
    conn = make_db([(1, "2023-07-10 00:00:00", 1, 2, 10e6)],
                   [(1, "2023-07-01 00:00:00", 10e6), (1, "2023-07-01 00:00:00", 12e6)],
                   [(1, "2000-01-01 00:00:00", "Attack")])
    with pytest.raises(ValueError):
        build_dataset(conn)


# ---------- Features and OLS ----------

def test_make_features_hand_values():
    df = pd.DataFrame({
        "mv_eur": [math.exp(2) * 1, 10.0], "fee_eur": [math.exp(2) * math.exp(0.5), 5.0],
        "age": [20.0, 30.0], "position": ["Goalkeeper", "Midfield"],
        "window": ["W2020", "S2020"],
    })
    X, y = make_features(df)
    assert X.shape == (2, len(FEATURES))
    # const, ln_mv, age, age^2, GK, DEF, ATT, winter
    assert X[0] == pytest.approx([1, 2.0, 20, 400, 1, 0, 0, 1])
    assert X[1] == pytest.approx([1, math.log(10), 30, 900, 0, 0, 0, 0])   # Midfield = reference
    assert y == pytest.approx([0.5, math.log(0.5)])


def test_fit_ols_recovers_known_coefficients():
    rng = np.random.default_rng(0)
    X = np.column_stack([np.ones(50), rng.normal(size=50), rng.normal(size=50)])
    beta = np.array([0.3, -1.2, 2.5])
    assert fit_ols(X, X @ beta) == pytest.approx(beta)   # no noise -> exact recovery


# ---------- Conformal quantile and intervals ----------

def test_conformal_quantile_tiny_example():
    # n = 5, k = ceil(6 * 0.8) = ceil(4.8) = 5 -> the 5th smallest = 0.5
    assert conformal_quantile([0.5, 0.1, 0.4, 0.2, 0.3], 0.8) == 0.5
    # n = 4, k = ceil(5 * 0.8) = 4 (exactly 4, float noise must not make it 5)
    assert conformal_quantile([0.4, 0.1, 0.3, 0.2], 0.8) == 0.4
    # n = 9, k = ceil(10 * 0.8) = 8 -> the 8th smallest of 0.1 .. 0.9 = 0.8
    assert conformal_quantile([i / 10 for i in range(1, 10)], 0.8) == pytest.approx(0.8)


def test_conformal_quantile_registered_size():
    # n_cal = 185 -> k = ceil(186 * 0.8) = ceil(148.8) = 149 (as in the criteria)
    assert conformal_quantile(np.arange(1, 186), 0.8) == 149


def test_conformal_quantile_too_small_raises():
    with pytest.raises(ValueError):
        conformal_quantile([0.1, 0.2, 0.3], 0.8)   # k = ceil(3.2) = 4 > 3


def test_predict_interval():
    # y_hat = 0.1 for every row (intercept only), q = 0.2, MV = 100
    lower, upper = predict_interval(np.array([0.1]), np.ones((2, 1)), [100.0, 100.0], 0.2)
    assert lower == pytest.approx([100 * math.exp(-0.1)] * 2)
    assert upper == pytest.approx([100 * math.exp(0.3)] * 2)


# ---------- Baselines and metrics ----------

def test_baselines():
    train = pd.DataFrame({"fee_eur": [10.0, 40.0, 90.0], "mv_eur": [10.0, 20.0, 30.0]})
    m = train_median_ratio(train)                      # ratios 1, 2, 3 -> median 2
    assert m == 2.0
    assert baseline_b1([5.0]) == pytest.approx([5.0])
    assert baseline_b2([5.0], m) == pytest.approx([10.0])


def test_metrics():
    fee = np.array([100.0, 200.0, 50.0, 400.0])
    a = ape([110.0, 150.0, 50.0, 600.0], fee)          # 0.1, 0.25, 0, 0.5
    assert a == pytest.approx([0.1, 0.25, 0.0, 0.5])
    assert mdape(a) == pytest.approx((0.1 + 0.25) / 2)  # even n: mean of the middle two
    assert mape(a) == pytest.approx(0.2125)
    lower, upper = np.array([100.0, 0.0, 60.0, 0.0]), np.array([150.0, 1.0, 70.0, 400.0])
    # 100 on the lower edge (in), 200 above (out), 50 below (out), 400 on the upper edge (in)
    assert coverage(fee, lower, upper) == 0.5
    assert mean_width_eur_m([0.0, 1e6], [2e6, 5e6]) == pytest.approx(3.0)


def test_choose_best_baseline():
    assert choose_best_baseline(0.30, 0.40) == "B1"
    assert choose_best_baseline(0.40, 0.30) == "B2"
    with pytest.raises(ValueError):
        choose_best_baseline(0.35, 0.35)


# ---------- Paired bootstrap ----------

def test_bootstrap_constant_difference():
    # Every row: model 0.1, baseline 0.3 -> every resample difference is exactly -0.2
    result = paired_bootstrap(np.full(20, 0.1), np.full(20, 0.3), n=200, seed=1)
    assert result["lower_2_5"] == pytest.approx(-0.2)
    assert result["upper_97_5"] == pytest.approx(-0.2)


def test_bootstrap_is_deterministic_with_fixed_seed():
    rng = np.random.default_rng(7)
    model, base = rng.random(60), rng.random(60)
    assert paired_bootstrap(model, base) == paired_bootstrap(model, base)
    assert paired_bootstrap(model, base, seed=1) != paired_bootstrap(model, base, seed=2)
    assert paired_bootstrap(model, base)["seed"] == 20261015
    assert paired_bootstrap(model, base)["n_resamples"] == 2000


def test_bootstrap_rejects_unpaired_arrays():
    with pytest.raises(ValueError):
        paired_bootstrap(np.ones(5), np.ones(6))
