"""Pilot of the LLM valuation agents on at most 10 TRAIN rows (docs/llm_criteria.md, "Pilot").

Checks the output format before the criteria are frozen. Rows: the train split in dataset
order (transfer_date, then player_id), PILOT_N rows chosen with
numpy.random.default_rng(PILOT_SEED).choice(n_train, PILOT_N, replace=False), then sorted.
Every chosen row must be a train row, otherwise the pilot refuses to run. Blind prompts only.
The pilot has its own cache directory, writes results/llm_pilot_run1.json once (never
overwritten), and writes nothing if any call fails for an external reason (the cache keeps
the completed calls, so a rerun resumes). It never reads or prints an API key.
"""
import hashlib
import importlib.metadata
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from fee_model import build_dataset
from llm_agents import (
    GENERATION, MODEL_ID, ROLES, DiskCache, LlmCallError, band_from_q, evaluate_call,
    load_templates, render_prompt, result_dict, summarise, template_hash,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "transfer_window_ai.db"
FEE_RESULTS = REPO_ROOT / "results" / "fee_model_eval.json"
PILOT_RESULTS = REPO_ROOT / "results" / "llm_pilot_run1.json"
PILOT_CACHE = REPO_ROOT / "results" / "llm_cache" / "pilot"
PILOT_SEED = 20261017
PILOT_N = 10
EXPECTED_SIZES = {"train": 1154, "calibration": 185, "test": 206}


def select_pilot_rows(data, n=PILOT_N, seed=PILOT_SEED):
    """n train rows chosen by a fixed seed from the train split in dataset order."""
    if n > 10:
        raise ValueError("The pilot uses at most 10 rows")
    train = data[data["split"] == "train"].reset_index(drop=True)
    picks = sorted(np.random.default_rng(seed).choice(len(train), n, replace=False).tolist())
    return train.iloc[picks].reset_index(drop=True)


def assert_train_rows(rows):
    """Refuse any row that is not in the train split."""
    bad = rows[rows["split"] != "train"]
    if len(bad):
        raise ValueError(f"The pilot only uses train rows; found {sorted(set(bad['split']))}")


def fee_parameters(path=FEE_RESULTS):
    """m and q from the fee-model results file (never hard-coded), plus its MD5."""
    import json
    raw = Path(path).read_bytes()
    results = json.loads(raw)
    return (float(results["train_median_ratio_m"]), float(results["conformal_q"]),
            hashlib.md5(raw.replace(b"\r\n", b"\n")).hexdigest())


def run_pilot(rows, m, q, templates, cache, model, model_id=MODEL_ID):
    """Blind seller and buyer calls for every row; returns the pilot record (no I/O)."""
    assert_train_rows(rows)
    band = band_from_q(q)
    records, calls = [], []
    for _, row in rows.iterrows():
        entry = {"player_id": int(row["player_id"]), "transfer_date": str(row["transfer_date"].date()),
                 "split": row["split"]}
        for role in ROLES:
            prompt = render_prompt(templates[f"{role}_blind"], row, m, band)
            result = evaluate_call(role, templates["system"], prompt, band, cache, model, model_id)
            calls.append(result)
            entry[role] = {k: v for k, v in result_dict(result).items() if k != "responses"}
        records.append(entry)
    return {"rows": records, "band": list(band), "m": m, "q": q,
            "counts": {"all": summarise(calls),
                       **{role: summarise([c for c in calls if c.role == role]) for role in ROLES}}}


def main():
    if PILOT_RESULTS.exists():
        print(f"PILOT REFUSED: {PILOT_RESULTS} already exists (never overwritten)")
        return 1
    from advise_cli import open_read_only
    from phase3_eval_wage_model import write_atomic

    conn = open_read_only(DB_PATH)
    try:
        data, _ = build_dataset(conn)
    finally:
        conn.close()
    sizes = {s: int((data["split"] == s).sum()) for s in EXPECTED_SIZES}
    if sizes != EXPECTED_SIZES:
        print(f"PILOT REFUSED: split sizes {sizes} differ from {EXPECTED_SIZES}")
        return 1

    m, q, fee_md5 = fee_parameters()
    templates = load_templates()
    rows = select_pilot_rows(data)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        record = run_pilot(rows, m, q, templates, DiskCache(PILOT_CACHE), MODEL_ID)
    except LlmCallError as error:
        print(f"PILOT INCOMPLETE: {error}. Nothing written; completed calls are cached, "
              "so a rerun resumes.")
        return 1

    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                            capture_output=True, text=True).stdout.strip()
    record["provenance"] = {
        "model_id": MODEL_ID, "generation": GENERATION,
        "google_adk": importlib.metadata.version("google-adk"),
        "python": platform.python_version(),
        "template_sha256": {name: template_hash(text) for name, text in templates.items()},
        "pilot_seed": PILOT_SEED, "pilot_n": PILOT_N, "cache_dir": str(PILOT_CACHE.relative_to(REPO_ROOT)),
        "md5_fee_results": fee_md5, "code_commit": commit,
        "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    write_atomic(PILOT_RESULTS, record)
    c = record["counts"]["all"]
    print(f"Pilot complete: {c['calls']} calls, valid {c['valid']}, retried {c['retried']}, "
          f"clipped {c['clipped']}, fallback {c['fallback']}. Written: "
          f"{PILOT_RESULTS.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
