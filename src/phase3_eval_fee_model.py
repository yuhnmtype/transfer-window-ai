"""Run the pre-registered fee-model evaluation (docs/fee_model_criteria.md, tag fee-criteria-v1).

run_evaluation() does the analysis and has no git guards, so it can be tested on synthetic
data; it still refuses to compute any metric unless the split sizes match the registered
ones. main() runs the guards, then calls run_evaluation() on the real DB and writes the
results file. There is deliberately no option to skip the guards.

If a real run exposes a code bug (not a bad result), the fix goes in a new commit with a
reason, the old file is renamed to fee_model_eval_run1.json with a one-line reason inside,
and a new run is written. Fixes never change the model, the metrics or the thresholds.
"""
import hashlib
import platform
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from fee_model import (
    FEATURES, ape, baseline_b1, baseline_b2, build_dataset, choose_best_baseline,
    conformal_quantile, coverage, fit_ols, make_features, mape, mdape, mean_width_eur_m,
    paired_bootstrap, point_fee, predict, predict_interval, train_median_ratio,
)
from phase3_eval_wage_model import md5_lf, write_atomic

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "transfer_window_ai.db"
CRITERIA_REL = "docs/fee_model_criteria.md"
RESULTS_PATH = REPO_ROOT / "results" / "fee_model_eval.json"
CRITERIA_TAG = "fee-criteria-v1"

# Every value below is fixed by the criteria. Tests may pass their own copy.
REGISTERED = {
    "criteria_tag": CRITERIA_TAG,
    "expected_sizes": {"train": 1154, "calibration": 185, "test": 206},
    "interval_level": 0.8,
    "ratio_threshold": 0.90,
    "coverage_band": [0.72, 0.88],
    "bootstrap_resamples": 2000,
    "bootstrap_seed": 20261015,
    "sensitivity_train_from": "2015-01-01",
}

LIMITATIONS = [
    "Only transfers with a recorded fee > 0 are modelled (conditional on a fee being paid).",
    "Selection bias: only transfers involving the 20 clubs of the 2025/26 Premier League.",
    "Position comes from current data, not from the transfer date.",
    "No point-in-time contract length is available.",
    "Fees may inflate over time; there is no time trend.",
    "The loan-fee filter is a heuristic.",
    "MAPE is dominated by small fees; MdAPE is the primary metric.",
    "The test set is small (206 transfers), so all results carry sampling noise.",
]


class SplitSizeError(ValueError):
    """The split sizes differ from the registered ones; no metric may be computed."""


def _fit_and_score(train, calibration, test, config):
    """Fit on train, calibrate q on calibration, score on test. Returns arrays and numbers."""
    X_train, y_train = make_features(train)
    coef = fit_ols(X_train, y_train)
    X_cal, y_cal = make_features(calibration)
    q = conformal_quantile(np.abs(y_cal - predict(coef, X_cal)), config["interval_level"])
    X_test, _ = make_features(test)
    fee, mv = test["fee_eur"].to_numpy(dtype=float), test["mv_eur"].to_numpy(dtype=float)
    lower, upper = predict_interval(coef, X_test, mv, q)
    model_ape = ape(point_fee(coef, X_test, mv), fee)
    return {"coef": coef, "q": q, "model_ape": model_ape,
            "metrics": {"mdape": mdape(model_ape), "mape": mape(model_ape),
                        "coverage": coverage(fee, lower, upper),
                        "mean_width_eur_m": mean_width_eur_m(lower, upper)}}


def run_evaluation(conn, config):
    """Run the registered evaluation and return the results as a dict."""
    data, exclusions = build_dataset(conn)
    sizes = {name: int((data["split"] == name).sum()) for name in config["expected_sizes"]}
    if sizes != config["expected_sizes"]:
        raise SplitSizeError(f"Split sizes {sizes} differ from the registered "
                             f"{config['expected_sizes']}; no metric was computed")

    train, calibration, test = (data[data["split"] == s] for s in ("train", "calibration", "test"))
    primary = _fit_and_score(train, calibration, test, config)

    fee, mv = test["fee_eur"].to_numpy(dtype=float), test["mv_eur"].to_numpy(dtype=float)
    m = train_median_ratio(train)
    baseline_ape = {"B1": ape(baseline_b1(mv), fee), "B2": ape(baseline_b2(mv, m), fee)}
    baseline_mdape = {k: mdape(v) for k, v in baseline_ape.items()}
    best = choose_best_baseline(baseline_mdape["B1"], baseline_mdape["B2"])

    ratio = primary["metrics"]["mdape"] / baseline_mdape[best]
    boot = paired_bootstrap(primary["model_ape"], baseline_ape[best],
                            config["bootstrap_resamples"], config["bootstrap_seed"])
    low, high = config["coverage_band"]
    cov = primary["metrics"]["coverage"]
    criterion_a = ratio <= config["ratio_threshold"] and boot["upper_97_5"] < 0
    criterion_b = low <= cov <= high

    # Sensitivity report (never gates): train restricted to 2015 onward
    recent = train[train["transfer_date"] >= pd.Timestamp(config["sensitivity_train_from"])]
    sensitivity = _fit_and_score(recent, calibration, test, config)

    return {
        "config": config,
        "exclusions": exclusions,
        "split_sizes": sizes,
        "features": list(FEATURES),
        "coefficients": dict(zip(FEATURES, map(float, primary["coef"]))),
        "conformal_q": primary["q"],
        "train_median_ratio_m": m,
        "test": {
            "model": primary["metrics"],
            "baselines": {k: {"mdape": baseline_mdape[k], "mape": mape(v)}
                          for k, v in baseline_ape.items()},
            "best_baseline": best,
            "mdape_ratio_model_to_best": ratio,
            "paired_bootstrap": boot,
        },
        "criterion_a": {"passed": criterion_a, "ratio_ok": ratio <= config["ratio_threshold"],
                        "bootstrap_upper_below_zero": boot["upper_97_5"] < 0},
        "criterion_b": {"passed": criterion_b, "coverage": cov, "band": [low, high]},
        "consequences": {
            "seller_reserve": ("fee model" if criterion_a
                               else "fallback: market value x training median ratio"),
            "intervals_used_by_negotiation": criterion_b,
        },
        "sensitivity_train_from_2015": {
            "note": "Report only; never decides acceptance.",
            "n_train": int(len(recent)), "conformal_q": sensitivity["q"],
            "model": sensitivity["metrics"],
            "mdape_ratio_to_primary_best_baseline":
                sensitivity["metrics"]["mdape"] / baseline_mdape[best],
        },
        "limitations": LIMITATIONS,
    }


# ---------- Guards and I/O ----------

def _git(repo_root, *args):
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)


def md5_file(path):
    digest = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def guard_errors(repo_root, db_path, results_path, tag=CRITERIA_TAG, criteria_rel=CRITERIA_REL):
    """Return the reasons the evaluation must not run (empty list means OK)."""
    errors = []
    status = _git(repo_root, "status", "--porcelain")
    if status.returncode != 0:
        return [f"git is not available or this is not a repository: {status.stderr.strip()}"]
    if status.stdout.strip():
        errors.append("Working tree is not clean (including untracked files)")
    if _git(repo_root, "rev-parse", "--verify", f"{tag}^{{commit}}").returncode != 0:
        errors.append(f"Tag {tag} not found")
    elif _git(repo_root, "diff", "--quiet", tag, "HEAD", "--", criteria_rel).returncode != 0 \
            or _git(repo_root, "diff", "--quiet", "--", criteria_rel).returncode != 0:
        errors.append(f"{criteria_rel} differs from tag {tag}")
    if not Path(db_path).exists():
        errors.append(f"Database not found: {db_path}")
    if Path(results_path).exists():
        errors.append(f"Results file already exists: {results_path} (no overwrite)")
    return errors


def main():
    errors = guard_errors(REPO_ROOT, DB_PATH, RESULTS_PATH)
    if errors:
        print("EVALUATION REFUSED:")
        for e in errors:
            print(" -", e)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        try:
            results = run_evaluation(conn, REGISTERED)
        except SplitSizeError as error:
            print(f"EVALUATION ABORTED: {error}")
            sys.exit(1)
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    finally:
        conn.close()

    results["provenance"] = {
        "code_commit": _git(REPO_ROOT, "rev-parse", "HEAD").stdout.strip(),
        "criteria_tag_commit": _git(REPO_ROOT, "rev-parse", f"{CRITERIA_TAG}^{{commit}}").stdout.strip(),
        "md5_db_file": md5_file(DB_PATH),
        "md5_criteria_file": md5_lf(REPO_ROOT / CRITERIA_REL),
        "db_meta": meta,
        "bootstrap_seed": REGISTERED["bootstrap_seed"],
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "numpy": np.__version__,
        "run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    write_atomic(RESULTS_PATH, results)

    t, a, b = results["test"], results["criterion_a"], results["criterion_b"]
    print(f"Split sizes: {results['split_sizes']}; exclusions: {results['exclusions']}")
    print(f"Test MdAPE: model {t['model']['mdape']:.3f}, B1 {t['baselines']['B1']['mdape']:.3f}, "
          f"B2 {t['baselines']['B2']['mdape']:.3f} (best: {t['best_baseline']})")
    print(f"Ratio model/best {t['mdape_ratio_model_to_best']:.3f}; bootstrap 95% "
          f"[{t['paired_bootstrap']['lower_2_5']:.3f}, {t['paired_bootstrap']['upper_97_5']:.3f}]")
    print(f"Criterion A: {a['passed']}  |  Criterion B (coverage {b['coverage']:.3f}): {b['passed']}")
    print(f"Seller reserve: {results['consequences']['seller_reserve']}")
    print(f"Written: {RESULTS_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
