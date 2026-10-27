"""Fee model, exactly as registered in docs/fee_model_criteria.md (tag fee-criteria-v1).

Target y = ln(fee / MV_pit); OLS on a few features; split conformal 80% interval; two
baselines. pandas and numpy only, deterministic. build_dataset is the only function that
reads the database. Where the criteria leave a case undefined (duplicate valuation dates, an
unregistered position, a window outside the split, a tie between baselines), the code raises
instead of choosing.
"""
import math

import numpy as np
import pandas as pd

POSITIONS = ("Goalkeeper", "Defender", "Midfield", "Attack")
POSITION_DUMMIES = ("Goalkeeper", "Defender", "Attack")   # reference category: Midfield
FEATURES = ("const", "ln_mv", "age", "age_sq", "pos_goalkeeper", "pos_defender",
            "pos_attack", "winter")
FILTERS = ("1_fee_positive", "2_pit_valuation_positive", "3_valuation_within_365_days",
           "4_loan_fee_heuristic", "5_window_month", "6_summer_2026", "7_missing_dob_or_position")
SPLITS = {"calibration": {"S2024", "W2025"}, "test": {"S2025", "W2026"}}
TRAIN_LAST_WINDOW = (2024, 0)   # W2024 (winter sorts before summer within a year)
DAYS_PER_YEAR = 365.25


# ---------- Dataset ----------

def window_of(month, year):
    """W(year) for months 1-3, S(year) for months 6-10, otherwise None."""
    if 1 <= month <= 3:
        return f"W{year}"
    if 6 <= month <= 10:
        return f"S{year}"
    return None


def split_of(window):
    """Registered split for a window; raises for a window the criteria do not place."""
    for name, windows in SPLITS.items():
        if window in windows:
            return name
    key = (int(window[1:]), 0 if window[0] == "W" else 1)
    if key <= TRAIN_LAST_WINDOW:
        return "train"
    raise ValueError(f"Window {window} is not placed by the registered split")


def build_dataset(conn, keep_summer_2026=False):
    """Apply the 7 registered filters in order.

    Returns (dataset, exclusions): dataset has one row per kept transfer with fee_eur, mv_eur,
    window, split, age and position, ordered by (transfer_date, player_id); exclusions maps
    each filter to the number of rows it removed.
    keep_summer_2026=True skips filter 6 (used only to count S2026 for
    docs/backtest_criteria.md); those rows get split None. The default is the registered
    behaviour.
    """
    transfers = pd.read_sql("SELECT player_id, transfer_date, from_club_id, to_club_id, "
                            "transfer_fee FROM transfers", conn, parse_dates=["transfer_date"])
    valuations = pd.read_sql("SELECT player_id, date, market_value_in_eur FROM valuations",
                             conn, parse_dates=["date"])
    players = pd.read_sql("SELECT player_id, date_of_birth, position FROM players", conn,
                          parse_dates=["date_of_birth"])
    if valuations.duplicated(["player_id", "date"]).any():
        raise ValueError("Duplicate valuation dates: 'latest valuation' is undefined")

    exclusions = {}

    def keep(frame, mask, name):
        exclusions[name] = int((~mask).sum())
        return frame[mask].copy()

    # 1. Raw transfer_fee > 0 (transfer_fee_clean is never read)
    df = keep(transfers, transfers["transfer_fee"].fillna(0) > 0, FILTERS[0])

    # 2. Latest valuation on or before the transfer date, positive
    df = df.sort_values("transfer_date", kind="mergesort")
    vals = valuations.sort_values("date", kind="mergesort")
    df = pd.merge_asof(df, vals, left_on="transfer_date", right_on="date", by="player_id",
                       direction="backward", allow_exact_matches=True)
    df = keep(df, df["market_value_in_eur"].fillna(0) > 0, FILTERS[1])

    # 3. Valuation at most 365 days older than the transfer
    df = keep(df, (df["transfer_date"] - df["date"]).dt.days <= 365, FILTERS[2])

    # 4. Loan-fee heuristic: the player moves back B -> A within 1 to 400 days
    back = transfers[["player_id", "transfer_date", "from_club_id", "to_club_id"]].rename(
        columns={"transfer_date": "back_date", "from_club_id": "to_club_id",
                 "to_club_id": "from_club_id"})
    pairs = df.reset_index().merge(back, on=["player_id", "from_club_id", "to_club_id"])
    gap = (pairs["back_date"] - pairs["transfer_date"]).dt.days
    loan_rows = set(pairs.loc[(gap >= 1) & (gap <= 400), "index"])
    df = keep(df, ~df.index.isin(loan_rows), FILTERS[3])

    # 5. Window by calendar month
    df["window"] = [window_of(d.month, d.year) for d in df["transfer_date"]]
    df = keep(df, df["window"].notna(), FILTERS[4])

    # 6. Summer 2026 is incomplete
    if not keep_summer_2026:
        df = keep(df, df["window"] != "S2026", FILTERS[5])
    else:
        exclusions[FILTERS[5]] = 0

    # 7. Date of birth and position must be present
    df = df.merge(players, on="player_id", how="left")
    df = keep(df, df["date_of_birth"].notna() & df["position"].notna(), FILTERS[6])
    unknown = set(df["position"]) - set(POSITIONS)
    if unknown:
        raise ValueError(f"Unregistered position values: {unknown}")

    df["split"] = [None if w == "S2026" else split_of(w) for w in df["window"]]
    df["age"] = (df["transfer_date"] - df["date_of_birth"]).dt.days / DAYS_PER_YEAR
    df = df.rename(columns={"transfer_fee": "fee_eur", "market_value_in_eur": "mv_eur",
                            "date": "valuation_date"})
    df = df.sort_values(["transfer_date", "player_id"], kind="mergesort").reset_index(drop=True)
    columns = ["player_id", "transfer_date", "from_club_id", "to_club_id", "fee_eur", "mv_eur",
               "valuation_date", "window", "split", "age", "position"]
    return df[columns], exclusions


# ---------- Features and model ----------

def make_features(df):
    """Return (X, y): X has the registered columns in FEATURES order; y = ln(fee / MV)."""
    X = np.column_stack([
        np.ones(len(df)),
        np.log(df["mv_eur"].to_numpy(dtype=float)),
        df["age"].to_numpy(dtype=float),
        df["age"].to_numpy(dtype=float) ** 2,
        *[(df["position"] == p).to_numpy(dtype=float) for p in POSITION_DUMMIES],
        df["window"].str.startswith("W").to_numpy(dtype=float),
    ])
    y = np.log(df["fee_eur"].to_numpy(dtype=float) / df["mv_eur"].to_numpy(dtype=float))
    return X, y


def fit_ols(X, y):
    """OLS coefficients via numpy.linalg.lstsq (X already holds the intercept column)."""
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return coef


def predict(coef, X):
    return X @ coef


def conformal_quantile(abs_residuals, level=0.8):
    """The k-th smallest absolute residual, k = ceil((n + 1) * level)."""
    values = np.sort(np.asarray(abs_residuals, dtype=float))
    n = len(values)
    # The tiny subtraction stops float noise (e.g. 148.80000000000001) from adding 1 to k.
    k = math.ceil((n + 1) * level - 1e-9)
    if not 1 <= k <= n:
        raise ValueError(f"Calibration set too small: k={k} for n={n}")
    return float(values[k - 1])


def predict_interval(coef, X, mv, q):
    """[MV * exp(y_hat - q), MV * exp(y_hat + q)] in EUR."""
    y_hat = predict(coef, X)
    mv = np.asarray(mv, dtype=float)
    return mv * np.exp(y_hat - q), mv * np.exp(y_hat + q)


def point_fee(coef, X, mv):
    """fee_hat = MV * exp(y_hat)."""
    return np.asarray(mv, dtype=float) * np.exp(predict(coef, X))


# ---------- Baselines ----------

def baseline_b1(mv):
    """B1: fee = MV."""
    return np.asarray(mv, dtype=float)


def train_median_ratio(train):
    """m = median of fee / MV on the train set (also the fallback multiplier)."""
    return float(np.median(train["fee_eur"].to_numpy(dtype=float)
                           / train["mv_eur"].to_numpy(dtype=float)))


def baseline_b2(mv, m):
    """B2: fee = MV * m."""
    return np.asarray(mv, dtype=float) * m


# ---------- Metrics ----------

def ape(pred, fee):
    fee = np.asarray(fee, dtype=float)
    return np.abs(np.asarray(pred, dtype=float) - fee) / fee


def mdape(ape_values):
    return float(np.median(ape_values))


def mape(ape_values):
    return float(np.mean(ape_values))


def coverage(fee, lower, upper):
    """Share of fees inside the closed interval [lower, upper]."""
    fee = np.asarray(fee, dtype=float)
    return float(np.mean((lower <= fee) & (fee <= upper)))


def mean_width_eur_m(lower, upper):
    return float(np.mean(np.asarray(upper) - np.asarray(lower)) / 1_000_000)


def choose_best_baseline(mdape_b1, mdape_b2):
    """Fixed once from the point MdAPE on the full test set; a tie is not defined."""
    if mdape_b1 == mdape_b2:
        raise ValueError("B1 and B2 tie on test MdAPE: the best baseline is undefined")
    return "B1" if mdape_b1 < mdape_b2 else "B2"


def paired_bootstrap(model_ape, baseline_ape, n=2000, seed=20261015):
    """Percentile 95% interval of MdAPE_model - MdAPE_baseline over paired resamples.

    The baseline is the one fixed beforehand; it is never re-selected inside a resample.
    All n resamples are drawn at once as an (n, rows) index matrix from
    numpy.random.default_rng(seed).
    """
    model_ape = np.asarray(model_ape, dtype=float)
    baseline_ape = np.asarray(baseline_ape, dtype=float)
    if model_ape.shape != baseline_ape.shape:
        raise ValueError("Paired arrays must have the same length")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(model_ape), size=(n, len(model_ape)))
    diffs = np.median(model_ape[idx], axis=1) - np.median(baseline_ape[idx], axis=1)
    return {"lower_2_5": float(np.percentile(diffs, 2.5)),
            "upper_97_5": float(np.percentile(diffs, 97.5)),
            "n_resamples": n, "seed": seed}
