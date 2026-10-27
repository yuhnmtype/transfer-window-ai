"""Window-level backtest of the fallback fee estimate, as registered in
docs/backtest_criteria.md (tag backtest-criteria-v1).

Descriptive only: fee_hat = m x MV_pit with m read from results/fee_model_eval.json (never
refitted); no pass/fail criterion and no consequence for any default.

Guards, in this order; nothing is computed unless all of them pass:
1. The results file must not exist. This cheap check runs first so that a refused run never
   reads the database (the criteria list it last; moving it earlier changes no outcome).
2. Clean working tree, including untracked files.
3. The tag backtest-criteria-v1 exists.
4. docs/backtest_criteria.md and results/fee_model_eval.json are unchanged since the tag.
5. This script did not exist at the tag commit.
6. Expected row counts per window. The database is read only here, and only when guards 1 to
   5 passed, so a count mismatch stops before any statistic is computed.
There is no option to skip the guards.

Row order: build_dataset sorts by (transfer_date, player_id) only, so rows that tie on both
keep the database read order. The rows are therefore re-sorted by ORDER_KEY (a stable sort on
every column used) before the bootstrap; the key is recorded in the provenance.
"""
import platform
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from fee_model import ape, build_dataset, mdape
from negotiation import load_fee_fallback
from phase3_eval_wage_model import write_atomic

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "transfer_window_ai.db"
CRITERIA_TAG = "backtest-criteria-v1"
CRITERIA_REL = "docs/backtest_criteria.md"
FEE_RESULTS_REL = "results/fee_model_eval.json"
SCRIPT_REL = "src/phase3_eval_backtest.py"
RESULTS_REL = "results/backtest_windows_run1.json"

WINDOWS = ("S2023", "W2024", "S2024", "W2025", "S2025", "W2026")   # fixed processing order
EXPECTED_COUNTS = {"S2023": 147, "W2024": 16, "S2024": 153, "W2025": 32, "S2025": 177,
                   "W2026": 29, "S2026": 17}
SEED = 20261027
N_RESAMPLES = 2000
LOW_PRECISION_N = 30
APE_SHARE_LIMIT = 0.25
ORDER_KEY = ("transfer_date", "player_id", "from_club_id", "to_club_id", "fee_eur", "mv_eur")
EXIT_OK, EXIT_REFUSED = 0, 1

ROLE_LABELS = {
    "train": "m was fitted on the train set, so these windows are in-sample for the fallback.",
    "calibration": "Not used to fit m.",
    "test": "The registered test windows (206 rows), already used in the fee-model and LLM "
            "evaluations.",
}
INTERPRETATION_RULES = [
    "The table is descriptive only.",
    "Windows with n < 30 are flagged low precision.",
    "No claim of stability or drift is made beyond what the bootstrap intervals show; "
    "overlapping intervals are not reported as evidence of stability, and non-overlapping "
    "intervals are not reported as evidence of a cause.",
    "No parameter (m, the 80% band of fee-criteria-v1) may be changed because of this table.",
    "The test windows are reused for description only; this changes no registered conclusion "
    "of the fee-model or LLM evaluations.",
    "Train windows are in-sample for m and must not be read as out-of-sample performance.",
    "Each interval is a separate 95% interval; there is no adjustment for the six windows.",
]
LIMITATIONS = [
    "Descriptive only; window sizes are small, and two windows are below 30 rows.",
    "Inherits the limitations of docs/fee_model_criteria.md: fee > 0 only (conditional on a "
    "fee being paid), selection bias (transfers involving the 20 clubs of the 2025/26 Premier "
    "League only), position from current data, no point-in-time contract length, possible fee "
    "inflation over time, heuristic loan filter.",
    "The train windows are in-sample for m; the test windows have already been used.",
    "Point-in-time market values can be up to 365 days old.",
]


# ---------- Guards ----------

def _git(repo_root, *args):
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)


def guard_errors(repo_root, target, counts):
    """Every reason to refuse, in the order of the module docstring. counts is a callable that
    reads the database; it is called only when guards 1 to 5 all passed."""
    errors = []
    if Path(target).exists():
        errors.append(f"Results file already exists: {target} (never overwritten)")
    status = _git(repo_root, "status", "--porcelain", "--untracked-files=all")
    if status.returncode != 0:
        return errors + [f"git is not available or this is not a repository: "
                         f"{status.stderr.strip()}"]
    if status.stdout.strip():
        errors.append("Working tree is not clean (including untracked files): "
                      + ", ".join(status.stdout.splitlines()))
    if _git(repo_root, "rev-parse", "--verify", f"{CRITERIA_TAG}^{{commit}}").returncode != 0:
        errors.append(f"Tag {CRITERIA_TAG} not found")
    else:
        for rel in (CRITERIA_REL, FEE_RESULTS_REL):
            if _git(repo_root, "diff", "--quiet", CRITERIA_TAG, "--", rel).returncode != 0:
                errors.append(f"{rel} changed since tag {CRITERIA_TAG}")
        if _git(repo_root, "cat-file", "-e", f"{CRITERIA_TAG}:{SCRIPT_REL}").returncode == 0:
            errors.append(f"{SCRIPT_REL} already existed at the tag commit")
    if errors:
        return errors                       # the database is not read
    try:
        actual = counts()
    except (OSError, sqlite3.Error) as error:
        return [f"Database could not be read: {error}"]
    if actual != EXPECTED_COUNTS:
        errors.append(f"Row counts {actual} differ from {EXPECTED_COUNTS}")
    return errors


# ---------- Rows and statistics ----------

def window_counts(conn):
    """(counts per window incl. S2026, registered rows of the six windows).

    The six windows come from build_dataset with its default (registered) filters; S2026 is
    counted with filters 1 to 5 and 7 (keep_summer_2026=True)."""
    data, _ = build_dataset(conn)
    with_s2026, _ = build_dataset(conn, keep_summer_2026=True)
    counts = {w: int((data["window"] == w).sum()) for w in WINDOWS}
    counts["S2026"] = int((with_s2026["window"] == "S2026").sum())
    return counts, data[data["window"].isin(WINDOWS)]


def ordered(rows):
    """Rows in a fully deterministic order (stable sort on ORDER_KEY)."""
    return rows.sort_values(list(ORDER_KEY), kind="mergesort").reset_index(drop=True)


def window_statistics(data, m, seed=SEED, n_resamples=N_RESAMPLES):
    """One dict per window in WINDOWS order. One generator for all windows, as registered."""
    rng = np.random.default_rng(seed)
    out = []
    for window in WINDOWS:
        rows = ordered(data[data["window"] == window])
        n = len(rows)
        if n == 0:
            raise ValueError(f"No rows for window {window}")
        roles = set(rows["split"])
        if len(roles) != 1:
            raise ValueError(f"Window {window} has more than one registered role: {roles}")
        fee = rows["fee_eur"].to_numpy(dtype=float)
        mv = rows["mv_eur"].to_numpy(dtype=float)
        errors = ape(m * mv, fee)
        idx = rng.integers(0, n, size=(n_resamples, n))
        medians = np.median(errors[idx], axis=1)
        low, high = np.percentile(medians, [2.5, 97.5], method="linear")
        out.append({
            "window": window, "role": roles.pop(), "n": n,
            "mdape": mdape(errors), "mdape_ci95": [float(low), float(high)],
            "median_fee_to_mv": float(np.median(fee / mv)),
            "share_ape_le_0_25": float(np.mean(errors <= APE_SHARE_LIMIT)),
            "low_precision": n < LOW_PRECISION_N,
        })
    return out


# ---------- Output ----------

def write_new(target, data):
    """Atomic write; never overwrites (checked again right before writing)."""
    target = Path(target)
    if target.exists():
        raise FileExistsError(f"{target} already exists (never overwritten)")
    write_atomic(target, data)


def provenance(repo_root, db_meta, fallback):
    return {
        "code_commit": _git(repo_root, "rev-parse", "HEAD").stdout.strip(),
        "criteria_tag": CRITERIA_TAG,
        "criteria_tag_commit": _git(repo_root, "rev-parse",
                                    f"{CRITERIA_TAG}^{{commit}}").stdout.strip(),
        "db_meta": db_meta,
        "fee_results": fallback.source,          # path, md5_lf, its code and tag commits
        "seed": SEED, "n_resamples": N_RESAMPLES, "order_key": list(ORDER_KEY),
        "python": platform.python_version(), "pandas": pd.__version__,
        "numpy": np.__version__,
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def run(repo_root, target, load_db, fee_results_path, statistics=window_statistics):
    """Guards, then statistics, then the results file. Returns (exit code, message).

    load_db() reads the database once and returns (counts, rows of the six windows, meta)."""
    cached = {}

    def counts():
        cached["counts"], cached["rows"], cached["meta"] = load_db()
        return cached["counts"]

    errors = guard_errors(repo_root, target, counts)
    if errors:
        return EXIT_REFUSED, "EVALUATION REFUSED:\n" + "\n".join(f" - {e}" for e in errors)
    fallback = load_fee_fallback(fee_results_path)
    windows = statistics(cached["rows"], fallback.ratio_m)
    results = {
        "criteria_tag": CRITERIA_TAG,
        "m": fallback.ratio_m,
        "estimate": "fee_hat = m x MV_pit (registered fallback; m never refitted)",
        "windows": windows,
        "s2026_count": cached["counts"]["S2026"],
        "expected_counts": EXPECTED_COUNTS,
        "role_labels": ROLE_LABELS,
        "interpretation_rules": INTERPRETATION_RULES,
        "limitations": LIMITATIONS,
        "provenance": provenance(repo_root, cached["meta"], fallback),
    }
    write_new(target, results)
    lines = [f"{w['window']}  {w['role']:<11} n={w['n']:<4} MdAPE {w['mdape']:.3f} "
             f"[{w['mdape_ci95'][0]:.3f}, {w['mdape_ci95'][1]:.3f}]"
             + ("  low precision" if w["low_precision"] else "") for w in windows]
    return EXIT_OK, "\n".join(lines + [f"S2026 rows (count only): {results['s2026_count']}",
                                       f"Written: {target}"])


def main():
    from advise_cli import open_read_only

    def load_db():
        if not DB_PATH.exists():
            raise FileNotFoundError(f"Database not found: {DB_PATH}")
        conn = open_read_only(DB_PATH)
        try:
            counts, rows = window_counts(conn)
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        finally:
            conn.close()
        return counts, rows, meta

    code, message = run(REPO_ROOT, REPO_ROOT / RESULTS_REL, load_db,
                        REPO_ROOT / FEE_RESULTS_REL)
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
