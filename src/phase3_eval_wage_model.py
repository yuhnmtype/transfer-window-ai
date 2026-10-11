"""Run the pre-registered wage-model evaluation (docs/wage_model_criteria.md, tag wage-criteria-v1).

run_evaluation() does the analysis and has no guards, so it can be tested on synthetic data.
main() runs the guards, then calls run_evaluation() on the real DB and writes the results
file. There is deliberately no option to skip the guards.

If a real run exposes a code bug (not a bad result), the fix goes in a new commit with a
reason, the old file is renamed to wage_model_eval_run1.json with a one-line reason inside,
and a new run is written. Fixes never change the model, the metric or the thresholds.
"""
import hashlib
import json
import math
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from phase3_check_wage_reference import check as check_reference
from wages import allocate_wages, load_squad, top_weekly_k

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "transfer_window_ai.db"
SEED_PATH = REPO_ROOT / "seed" / "club_finance_seed.csv"
REFERENCE_PATH = REPO_ROOT / "seed" / "wage_reference.csv"
CRITERIA_PATH = REPO_ROOT / "docs" / "wage_model_criteria.md"
RESULTS_PATH = REPO_ROOT / "results" / "wage_model_eval.json"
CRITERIA_TAG = "wage-criteria-v1"


def alpha_grid():
    """Registered grid 0.30 to 1.50, step 0.05 (25 values), built without float drift."""
    return [round(0.30 + 0.05 * k, 2) for k in range(25)]


# Every value below is fixed by the criteria. Tests may pass their own copy.
REGISTERED = {
    "criteria_tag": CRITERIA_TAG,
    "alpha_grid": alpha_grid(),
    "wage_scope_factor": 1.0,
    "widen_factor": 1.25,
    "min_test_pass_rate": 0.75,
    "wilson_z": 1.96,
    "tie_tolerance": 1e-12,
}

LIMITATIONS = [
    "n is small, so the Wilson interval is wide.",
    "The reference is itself a Capology-derived estimate: a pass means 'consistent with "
    "public estimates', not 'accurate'.",
    "The metric uses only the club's top weekly wage: alpha is one parameter fitted to one "
    "statistic per club and says nothing about the rest of the squad.",
    "The pool is accounting wages (club-wide staff, bonuses, other costs) while the reference "
    "is base salary only. Alpha absorbs this gap and is not the real wage concentration.",
    "Time mismatch: pool FY 2024/25, squad at the 2026 snapshot, reference from the start of "
    "2025/26 and 2026/27 (often two different players).",
]


# ---------- Pure metric functions ----------

def log_distance(est, ref_min, ref_max, lower_bound):
    """0 inside [ref_min, ref_max] (or [ref_min, inf) for a lower bound), else |ln| to the
    nearest bound."""
    if est < ref_min:
        return math.log(ref_min / est)
    if not lower_bound and est > ref_max:
        return math.log(est / ref_max)
    return 0.0


def passes(est, ref_min, ref_max, lower_bound, widen):
    """ref_min / widen <= est <= ref_max * widen (no upper bound for a lower-bound row).

    Mathematically the same as log_distance <= ln(widen); the direct inequality is used so
    float rounding of the logarithm cannot flip a boundary case.
    """
    if est < ref_min / widen:
        return False
    return lower_bound or est <= ref_max * widen


def wilson_interval(k, n, z):
    """Wilson score interval for k successes out of n."""
    if n <= 0 or not 0 <= k <= n:
        raise ValueError(f"Invalid counts k={k}, n={n}")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def select_alpha(mean_d_by_alpha, tolerance):
    """Return (selected alpha, plateau). The plateau is every alpha whose mean is within
    `tolerance` of the minimum; the registered pick is the smallest alpha in it."""
    best = min(mean_d_by_alpha.values())
    plateau = sorted(a for a, m in mean_d_by_alpha.items() if m - best <= tolerance)
    return plateau[0], plateau


def verdict(test_passes, n_test, equal_split_passes, min_rate):
    """Acceptance: A = pass rate >= min_rate, B = strictly more passes than equal split."""
    condition_a = test_passes >= min_rate * n_test
    condition_b = test_passes > equal_split_passes
    return {"condition_a": condition_a, "condition_b": condition_b,
            "accepted": condition_a and condition_b}


# ---------- Evaluation (no guards) ----------

def run_evaluation(conn, seed_df, reference_df, config):
    """Run the registered evaluation and return the results as a dict.

    conn: SQLite connection with a players table. seed_df: club finance rows (pool source).
    reference_df: wage reference rows with a split column. Works on synthetic data.
    """
    pools = dict(zip(seed_df["club_id"], seed_df["wage_bill_gbp_m"]))
    grid = config["alpha_grid"]
    widen = config["widen_factor"]

    clubs = []
    for _, ref in reference_df.iterrows():
        cid = int(ref["club_id"])
        squad = load_squad(conn, cid)
        if not squad:
            raise ValueError(f"club_id {cid}: empty squad")
        pool = pools[cid] * config["wage_scope_factor"]
        clubs.append({
            "club_id": cid, "club_name": ref["club_name"], "split": ref["split"],
            "n_players": len(squad), "pool_gbp_m": pool,
            "ref_min": float(ref["ref_min_weekly_gbp_k"]),
            "ref_max": float(ref["ref_max_weekly_gbp_k"]),
            "lower_bound": bool(ref["max_is_lower_bound"]),
            "_squad": squad,
        })

    def estimate(club, alpha):
        return top_weekly_k(allocate_wages(club["_squad"], club["pool_gbp_m"], alpha))

    def distance(club, alpha):
        return log_distance(estimate(club, alpha), club["ref_min"], club["ref_max"],
                            club["lower_bound"])

    def ok(club, alpha):
        return passes(estimate(club, alpha), club["ref_min"], club["ref_max"],
                      club["lower_bound"], widen)

    def mean_d(group):
        return {a: sum(distance(c, a) for c in group) / len(group) for a in grid}

    fit = [c for c in clubs if c["split"] == "fit"]
    test = [c for c in clubs if c["split"] == "test"]
    z = config["wilson_z"]

    # Primary direction: select on fit, evaluate on test.
    curve = mean_d(fit)
    alpha, plateau = select_alpha(curve, config["tie_tolerance"])
    test_passes = sum(ok(c, alpha) for c in test)
    equal_passes = sum(ok(c, 0.0) for c in test)
    alpha1_passes = sum(ok(c, 1.0) for c in test)

    # Reverse direction (reported only): select on test, evaluate on fit.
    rev_alpha, rev_plateau = select_alpha(mean_d(test), config["tie_tolerance"])
    rev_passes = sum(ok(c, rev_alpha) for c in fit)

    rows = []
    for c in clubs:
        rows.append({
            **{k: v for k, v in c.items() if not k.startswith("_")},
            "est_selected_k": estimate(c, alpha), "d_selected": distance(c, alpha),
            "pass_selected": ok(c, alpha),
            "est_equal_split_k": estimate(c, 0.0), "pass_equal_split": ok(c, 0.0),
            "est_alpha1_k": estimate(c, 1.0), "pass_alpha1": ok(c, 1.0),
        })

    edges = (grid[0], grid[-1])
    return {
        "config": config,
        "fit_curve": [{"alpha": a, "mean_d_fit": curve[a]} for a in grid],
        "selected_alpha": alpha,
        "selected_alpha_at_boundary": alpha in edges,
        "plateau_alphas": plateau,
        "test": {
            "n": len(test), "passes_model": test_passes,
            "passes_equal_split": equal_passes, "passes_alpha1": alpha1_passes,
            "wilson_95": wilson_interval(test_passes, len(test), z),
        },
        "fit": {"n": len(fit), "passes_model": sum(ok(c, alpha) for c in fit)},
        "reverse": {
            "selected_alpha": rev_alpha, "selected_alpha_at_boundary": rev_alpha in edges,
            "plateau_alphas": rev_plateau, "n_fit": len(fit), "passes_on_fit": rev_passes,
            "wilson_95": wilson_interval(rev_passes, len(fit), z),
        },
        "verdict": verdict(test_passes, len(test), equal_passes,
                           config["min_test_pass_rate"]),
        "clubs": rows,
        "limitations": LIMITATIONS,
    }


# ---------- Guards and I/O ----------

def _git(*args):
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)


def md5_lf(path):
    """MD5 of a file after normalising line endings to LF."""
    return hashlib.md5(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def guard_errors(seed, reference):
    """Return the reasons the evaluation must not run (empty list means OK)."""
    errors = []
    status = _git("status", "--porcelain")
    if status.returncode != 0:
        return [f"git is not available or this is not a repository: {status.stderr.strip()}"]
    if status.stdout.strip():
        errors.append("Working tree is not clean (including untracked files)")
    if _git("rev-parse", "--verify", f"{CRITERIA_TAG}^{{commit}}").returncode != 0:
        errors.append(f"Tag {CRITERIA_TAG} not found")
    elif _git("diff", "--quiet", CRITERIA_TAG, "--",
              str(CRITERIA_PATH.relative_to(REPO_ROOT)),
              str(REFERENCE_PATH.relative_to(REPO_ROOT))).returncode != 0:
        errors.append(f"Criteria or reference differ from tag {CRITERIA_TAG}")
    errors += [f"Reference check: {e}" for e in check_reference(reference, seed)]
    if not DB_PATH.exists():
        errors.append(f"Database not found: {DB_PATH}")
    else:
        with sqlite3.connect(DB_PATH) as conn:
            db = pd.read_sql("SELECT club_id, wage_bill_gbp_m, revenue_basis FROM club_finance",
                             conn).sort_values("club_id").reset_index(drop=True)
        csv = seed[["club_id", "wage_bill_gbp_m", "revenue_basis"]].sort_values(
            "club_id").reset_index(drop=True)
        if not db.astype(str).equals(csv.astype(str)):
            errors.append("club_finance in the DB differs from the seed CSV; "
                          "run python src/phase2_load_seed.py")
    if RESULTS_PATH.exists():
        errors.append(f"Results file already exists: {RESULTS_PATH} (no overwrite)")
    return errors


def write_atomic(path, data):
    """Write JSON to a temp file in the same folder, then rename it into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2, allow_nan=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def main():
    seed = pd.read_csv(SEED_PATH)
    reference = pd.read_csv(REFERENCE_PATH)
    errors = guard_errors(seed, reference)
    if errors:
        print("EVALUATION REFUSED:")
        for e in errors:
            print(" -", e)
        sys.exit(1)

    with sqlite3.connect(DB_PATH) as conn:
        results = run_evaluation(conn, seed, reference, REGISTERED)
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())

    results["provenance"] = {
        "code_commit": _git("rev-parse", "HEAD").stdout.strip(),
        "criteria_tag_commit": _git("rev-parse", f"{CRITERIA_TAG}^{{commit}}").stdout.strip(),
        "md5_club_finance_seed": md5_lf(SEED_PATH),
        "md5_wage_reference": md5_lf(REFERENCE_PATH),
        "db_meta": meta,
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "numpy": np.__version__,
        "run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    write_atomic(RESULTS_PATH, results)

    t, v = results["test"], results["verdict"]
    print(f"Selected alpha (fit): {results['selected_alpha']} "
          f"(boundary: {results['selected_alpha_at_boundary']}, "
          f"plateau: {results['plateau_alphas']})")
    print(f"Test passes: model {t['passes_model']}/{t['n']}, "
          f"equal split {t['passes_equal_split']}/{t['n']}, alpha=1 {t['passes_alpha1']}/{t['n']}")
    print(f"Wilson 95%: [{t['wilson_95'][0]:.3f}, {t['wilson_95'][1]:.3f}]")
    r = results["reverse"]
    print(f"Reverse: alpha {r['selected_alpha']}, fit passes {r['passes_on_fit']}/{r['n_fit']}")
    print(f"Condition A: {v['condition_a']}, condition B: {v['condition_b']}, "
          f"ACCEPTED: {v['accepted']}")
    print(f"Written: {RESULTS_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
