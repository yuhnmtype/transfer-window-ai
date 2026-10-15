"""Tests for src/phase3_eval_fee_model.py: end-to-end on SYNTHETIC data, and the guards on a
temporary git repository. The real database is never read here."""
import copy
import json
import math
import sqlite3
import subprocess
from datetime import date, timedelta

import numpy as np
import pytest

from phase3_eval_fee_model import (
    REGISTERED, CRITERIA_REL, SplitSizeError, guard_errors, run_evaluation,
)
from phase3_eval_wage_model import write_atomic

POSITIONS = ["Goalkeeper", "Defender", "Midfield", "Attack"]
# (window, transfer date): train windows on both sides of 2015, then calibration and test
WINDOWS = [("S2013", date(2013, 7, 15)), ("S2016", date(2016, 7, 15)),
           ("W2019", date(2019, 1, 20)), ("S2022", date(2022, 7, 15)),
           ("W2024", date(2024, 1, 20)),
           ("S2024", date(2024, 7, 15)), ("W2025", date(2025, 1, 20)),
           ("S2025", date(2025, 7, 15)), ("W2026", date(2026, 1, 20))]
PER_WINDOW = 40
SIZES = {"train": 5 * PER_WINDOW, "calibration": 2 * PER_WINDOW, "test": 2 * PER_WINDOW}


def synthetic_db(age_effect, noise=0.3, seed=11):
    """Fees generated as MV * exp(-0.2 + age_effect * (age - 25) + noise)."""
    rng = np.random.default_rng(seed)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE transfers (player_id INTEGER, transfer_date TIMESTAMP, "
                 "from_club_id INTEGER, to_club_id INTEGER, transfer_fee REAL)")
    conn.execute("CREATE TABLE valuations (player_id INTEGER, date TIMESTAMP, "
                 "market_value_in_eur INTEGER)")
    conn.execute("CREATE TABLE players (player_id INTEGER, date_of_birth TIMESTAMP, "
                 "position TEXT)")
    pid = 0
    for _, day in WINDOWS:
        for _ in range(PER_WINDOW):
            pid += 1
            mv = float(rng.uniform(1e6, 80e6))
            age = float(rng.uniform(18, 33))
            y = -0.2 + age_effect * (age - 25) + float(rng.normal(0, noise))
            dob = day - timedelta(days=round(age * 365.25))
            conn.execute("INSERT INTO transfers VALUES (?, ?, 1, 2, ?)",
                         (pid, f"{day} 00:00:00", mv * math.exp(y)))
            conn.execute("INSERT INTO valuations VALUES (?, ?, ?)",
                         (pid, f"{day - timedelta(days=30)} 00:00:00", mv))
            conn.execute("INSERT INTO players VALUES (?, ?, ?)",
                         (pid, f"{dob} 00:00:00", POSITIONS[pid % 4]))
    return conn


def config(**overrides):
    cfg = copy.deepcopy(REGISTERED)
    cfg["expected_sizes"] = dict(SIZES)
    cfg.update(overrides)
    return cfg


# ---------- run_evaluation ----------

def test_split_size_mismatch_aborts_before_any_metric():
    with pytest.raises(SplitSizeError):
        run_evaluation(synthetic_db(0.1), copy.deepcopy(REGISTERED))   # expects 1154/185/206


def test_end_to_end_structure_and_consistency(tmp_path):
    result = run_evaluation(synthetic_db(0.1), config())
    assert result["split_sizes"] == SIZES
    assert all(v == 0 for v in result["exclusions"].values())
    a, b, t = result["criterion_a"], result["criterion_b"], result["test"]
    # The verdicts follow the registered rules exactly
    assert a["passed"] == (t["mdape_ratio_model_to_best"] <= 0.90
                           and t["paired_bootstrap"]["upper_97_5"] < 0)
    assert b["passed"] == (0.72 <= b["coverage"] <= 0.88)
    assert t["best_baseline"] in ("B1", "B2")
    # Sensitivity: only the train windows from 2015 on (S2016, W2019, S2022, W2024)
    assert result["sensitivity_train_from_2015"]["n_train"] == 4 * PER_WINDOW
    # Results are JSON-serialisable and written atomically
    target = tmp_path / "out" / "fee.json"
    write_atomic(target, result)
    assert json.loads(target.read_text(encoding="utf-8"))["split_sizes"] == SIZES


def test_strong_signal_passes_criterion_a_and_uses_the_model():
    # Fees depend strongly on age (0.15 per year), which both baselines ignore.
    result = run_evaluation(synthetic_db(0.15, noise=0.1), config())
    assert result["coefficients"]["age"] == pytest.approx(0.15, abs=0.03)
    assert result["criterion_a"]["passed"] is True
    assert result["consequences"]["seller_reserve"] == "fee model"


def test_no_signal_fails_criterion_a_and_falls_back():
    # Fees are MV * exp(-0.2 + noise): B2 (MV x median ratio) is already the right model.
    result = run_evaluation(synthetic_db(0.0), config())
    assert result["criterion_a"]["passed"] is False
    assert result["consequences"]["seller_reserve"] == \
        "fallback: market value x training median ratio"


def test_run_is_deterministic():
    assert run_evaluation(synthetic_db(0.1), config()) == \
        run_evaluation(synthetic_db(0.1), config())


# ---------- Guards (temporary git repository) ----------

def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / CRITERIA_REL).write_text("criteria v1\n", encoding="utf-8")
    (root / ".gitignore").write_text("*.db\nresults/\n", encoding="utf-8")
    (root / "fake.db").write_bytes(b"")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "criteria")
    git(root, "tag", "fee-criteria-v1")
    return root


def errors_for(root):
    return guard_errors(root, root / "fake.db", root / "results" / "fee_model_eval.json")


def test_guards_pass_on_a_clean_tagged_repo(repo):
    assert errors_for(repo) == []


def test_guard_dirty_tree(repo):
    (repo / "untracked.py").write_text("x = 1\n", encoding="utf-8")
    assert any("not clean" in e for e in errors_for(repo))


def test_guard_missing_tag(repo):
    git(repo, "tag", "-d", "fee-criteria-v1")
    assert any("not found" in e for e in errors_for(repo))


def test_guard_criteria_changed_since_tag(repo):
    (repo / CRITERIA_REL).write_text("criteria v1, edited\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "edit criteria")   # tree is clean again
    errors = errors_for(repo)
    assert any("differs from tag" in e for e in errors)
    assert not any("not clean" in e for e in errors)


def test_guard_existing_results_file(repo):
    (repo / "results").mkdir()
    (repo / "results" / "fee_model_eval.json").write_text("{}", encoding="utf-8")
    assert any("already exists" in e for e in errors_for(repo))


def test_guard_missing_database(repo):
    (repo / "fake.db").unlink()
    assert any("Database not found" in e for e in errors_for(repo))


def test_guard_not_a_repository(tmp_path):
    errors = guard_errors(tmp_path, tmp_path / "x.db", tmp_path / "r.json")
    assert errors and "not a repository" in errors[0]
