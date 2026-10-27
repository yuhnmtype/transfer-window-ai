"""Tests for src/phase3_eval_backtest.py on synthetic data only. The real database, the real
test split and results/fee_model_eval.json are never read; nothing calls a model."""
import json
import subprocess

import numpy as np
import pandas as pd
import pytest

import phase3_eval_backtest as bt
from negotiation import FALLBACK_LABEL
from test_fee_model import filter_fixture

M = 1.25


# ---------- Synthetic rows ----------

def frame(spec):
    """spec: list of (window, split, fee_eur, mv_eur); dates and ids follow the list order."""
    return pd.DataFrame([{
        "player_id": 100 + i, "transfer_date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
        "from_club_id": 1, "to_club_id": 2, "fee_eur": fee, "mv_eur": mv,
        "window": w, "split": s} for i, (w, s, fee, mv) in enumerate(spec)])


ROLE = {"S2023": "train", "W2024": "train", "S2024": "calibration", "W2025": "calibration",
        "S2025": "test", "W2026": "test"}


def varied(window, n):
    """n rows with irregular fees, so resampled medians take many distinct values and the
    bootstrap interval depends on the random stream (not pinned at a few values)."""
    return [(window, ROLE[window], (5 + 0.37 * i + 0.11 * (i * i % 7)) * 1e6, 8e6)
            for i in range(n)]


def six_windows(extra=()):
    """Every window gets the same three hand-made rows (mv 8m, m = 1.25 -> fee_hat 10m):
    fee 10m -> APE 0; fee 8m -> APE 0.25; fee 5m -> APE 1.0."""
    spec = [(w, ROLE[w], fee, 8e6) for w in bt.WINDOWS for fee in (10e6, 8e6, 5e6)]
    return frame(spec + list(extra))


# ---------- Statistics (hand-computed) ----------

def test_window_statistics_hand_values():
    # APEs [0, 0.25, 1.0]: median 0.25; fee/MV [1.25, 1.0, 0.625]: median 1.0;
    # APE <= 0.25 for 2 of 3 rows (0.25 counts, the limit is inclusive)
    stats = bt.window_statistics(six_windows(), M)
    assert [s["window"] for s in stats] == list(bt.WINDOWS)
    for s in stats:
        assert (s["n"], s["mdape"], s["median_fee_to_mv"]) == (3, 0.25, 1.0)
        assert s["share_ape_le_0_25"] == pytest.approx(2 / 3)
        assert s["role"] == ROLE[s["window"]]
        assert s["low_precision"] is True                    # n = 3 < 30
        low, high = s["mdape_ci95"]
        assert 0.0 <= low <= 0.25 <= high <= 1.0


def test_low_precision_flag_at_30():
    big = [("S2024", "calibration", 10e6, 8e6)] * 27       # S2024 gets 3 + 27 = 30 rows
    stats = {s["window"]: s for s in bt.window_statistics(six_windows(big), M)}
    assert (stats["S2024"]["n"], stats["S2024"]["low_precision"]) == (30, False)
    assert stats["S2023"]["low_precision"] is True


def test_bootstrap_follows_the_registered_procedure():
    # Independent re-implementation: one generator, windows in the fixed order,
    # rng.integers(0, n, size=(2000, n)), median per row, np.percentile linear.
    data = six_windows(varied("W2025", 41) + varied("S2025", 60))
    stats = bt.window_statistics(data, M)
    rng = np.random.default_rng(20261027)
    for s in stats:
        rows = bt.ordered(data[data["window"] == s["window"]])
        errors = np.abs(M * rows["mv_eur"].to_numpy() - rows["fee_eur"].to_numpy()) \
            / rows["fee_eur"].to_numpy()
        idx = rng.integers(0, len(rows), size=(2000, len(rows)))
        expected = np.percentile(np.median(errors[idx], axis=1), [2.5, 97.5], method="linear")
        assert s["mdape_ci95"] == [float(expected[0]), float(expected[1])]


def test_bootstrap_is_deterministic_and_uses_one_generator():
    data = six_windows(varied("S2025", 60))
    assert bt.window_statistics(data, M) == bt.window_statistics(data, M)
    # S2025 is the fifth window: with a fresh generator for it alone, the interval differs,
    # which shows that the single shared generator (and the window order) is applied.
    rng = np.random.default_rng(bt.SEED)
    rows = bt.ordered(data[data["window"] == "S2025"])
    errors = np.abs(M * rows["mv_eur"].to_numpy() - rows["fee_eur"].to_numpy()) \
        / rows["fee_eur"].to_numpy()
    idx = rng.integers(0, len(rows), size=(2000, len(rows)))
    fresh = [float(v) for v in np.percentile(np.median(errors[idx], axis=1), [2.5, 97.5])]
    registered = {s["window"]: s for s in bt.window_statistics(data, M)}["S2025"]
    assert registered["mdape_ci95"] != fresh


def test_shuffled_input_rows_give_identical_results():
    # Two rows share transfer_date AND player_id to exercise the full ORDER_KEY.
    tie = frame([("S2025", "test", 6e6, 8e6), ("S2025", "test", 12e6, 8e6)])
    tie["player_id"], tie["transfer_date"] = 999, pd.Timestamp("2025-07-01")
    data = pd.concat([six_windows([("S2025", "test", 20e6, 8e6)]), tie], ignore_index=True)
    reference = bt.window_statistics(data, M)
    for seed in (1, 2, 3):
        shuffled = data.sample(frac=1, random_state=seed).reset_index(drop=True)
        assert bt.window_statistics(shuffled, M) == reference


def test_window_with_two_roles_raises():
    bad = six_windows([("S2025", "train", 10e6, 8e6)])
    with pytest.raises(ValueError, match="more than one registered role"):
        bt.window_statistics(bad, M)


def test_missing_window_raises():
    data = six_windows()
    with pytest.raises(ValueError, match="No rows for window W2026"):
        bt.window_statistics(data[data["window"] != "W2026"], M)


# ---------- Counting with build_dataset (synthetic fixture of tests/test_fee_model.py) ----

def test_window_counts_include_summer_2026():
    # Fixture: S2024 has 1 kept row (player 11), W2023 is outside the six windows, and the
    # summer-2026 transfer (player 9) is counted only through keep_summer_2026=True.
    counts, rows = bt.window_counts(filter_fixture())
    assert counts == {"S2023": 0, "W2024": 0, "S2024": 1, "W2025": 0, "S2025": 0,
                      "W2026": 0, "S2026": 1}
    assert list(rows["player_id"]) == [11]


# ---------- Guards (temporary git repository) ----------

def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True)


FEE_RESULTS = {"train_median_ratio_m": M,
               "consequences": {"seller_reserve": FALLBACK_LABEL},
               "provenance": {"code_commit": "abc", "criteria_tag_commit": "def"}}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    for rel, text in ((bt.CRITERIA_REL, "criteria v1\n"),
                      (bt.FEE_RESULTS_REL, json.dumps(FEE_RESULTS) + "\n")):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "criteria")
    git(root, "tag", bt.CRITERIA_TAG)
    (root / bt.SCRIPT_REL).parent.mkdir(parents=True)
    (root / bt.SCRIPT_REL).write_text("# eval\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "eval script after the tag")
    return root


class Counter:
    """A counts() stand-in that records whether the database would have been read."""

    def __init__(self, counts=None):
        self.calls, self.counts = 0, counts or dict(bt.EXPECTED_COUNTS)

    def __call__(self):
        self.calls += 1
        return self.counts


def target(root):
    return root / bt.RESULTS_REL


def test_guards_pass(repo):
    counts = Counter()
    assert bt.guard_errors(repo, target(repo), counts) == []
    assert counts.calls == 1


def test_existing_results_file_refuses_before_the_database(repo):
    target(repo).write_text("{}", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "a result")
    counts = Counter()
    errors = bt.guard_errors(repo, target(repo), counts)
    assert errors == [f"Results file already exists: {target(repo)} (never overwritten)"]
    assert counts.calls == 0


@pytest.mark.parametrize("rel", ["notes.txt", "src/extra.py", "results/other.json"])
def test_untracked_files_refuse(repo, rel):
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text("x", encoding="utf-8")
    counts = Counter()
    assert any("not clean" in e for e in bt.guard_errors(repo, target(repo), counts))
    assert counts.calls == 0


def test_modified_tracked_file_refuses(repo):
    (repo / bt.SCRIPT_REL).write_text("# changed\n", encoding="utf-8")
    assert any("not clean" in e for e in bt.guard_errors(repo, target(repo), Counter()))


def test_missing_tag_refuses(repo):
    git(repo, "tag", "-d", bt.CRITERIA_TAG)
    assert f"Tag {bt.CRITERIA_TAG} not found" in bt.guard_errors(repo, target(repo), Counter())


@pytest.mark.parametrize("rel", [bt.CRITERIA_REL, bt.FEE_RESULTS_REL])
def test_criteria_or_fee_results_changed_since_tag_refuse(repo, rel):
    (repo / rel).write_text("changed\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "change after tag")
    errors = bt.guard_errors(repo, target(repo), Counter())
    assert errors == [f"{rel} changed since tag {bt.CRITERIA_TAG}"]


def test_script_existing_at_tag_refuses(repo):
    git(repo, "tag", "-f", bt.CRITERIA_TAG, "HEAD")      # move the tag onto the script commit
    errors = bt.guard_errors(repo, target(repo), Counter())
    assert errors == [f"{bt.SCRIPT_REL} already existed at the tag commit"]


def test_row_count_mismatch_refuses(repo):
    wrong = dict(bt.EXPECTED_COUNTS, W2026=28)
    errors = bt.guard_errors(repo, target(repo), Counter(wrong))
    assert len(errors) == 1 and "differ from" in errors[0]


def test_guards_run_in_the_registered_order(repo):
    target(repo).parent.mkdir(parents=True, exist_ok=True)
    target(repo).write_text("{}", encoding="utf-8")         # 1: exists (also untracked)
    git(repo, "tag", "-f", bt.CRITERIA_TAG, "HEAD")         # 5: script at the tag
    counts = Counter()
    errors = bt.guard_errors(repo, target(repo), counts)
    assert errors[0].startswith("Results file already exists")
    assert "not clean" in errors[1]
    assert errors[2].endswith("already existed at the tag commit")
    assert counts.calls == 0


def test_not_a_repository(tmp_path):
    errors = bt.guard_errors(tmp_path, tmp_path / "x.json", Counter())
    assert "not a repository" in errors[-1]


def test_unreadable_database_is_a_refusal(repo):
    def broken():
        raise FileNotFoundError("Database not found: nope.db")
    assert bt.guard_errors(repo, target(repo), broken) == [
        "Database could not be read: Database not found: nope.db"]


# ---------- End to end with fakes ----------

def load_db_fake(counts=None):
    return lambda: (counts or dict(bt.EXPECTED_COUNTS), six_windows(), {"snapshot_date": "x"})


def test_run_writes_results_with_schema_and_provenance(repo):
    code, message = bt.run(repo, target(repo), load_db_fake(), repo / bt.FEE_RESULTS_REL)
    assert code == bt.EXIT_OK and "Written:" in message
    data = json.loads(target(repo).read_text(encoding="utf-8"))
    assert data["m"] == M and data["s2026_count"] == 17
    assert [w["window"] for w in data["windows"]] == list(bt.WINDOWS)
    assert set(data["windows"][0]) == {"window", "role", "n", "mdape", "mdape_ci95",
                                       "median_fee_to_mv", "share_ape_le_0_25",
                                       "low_precision"}
    prov = data["provenance"]
    assert prov["order_key"] == list(bt.ORDER_KEY) and prov["seed"] == 20261027
    assert prov["criteria_tag_commit"] and prov["code_commit"]
    assert prov["fee_results"]["md5_lf"] and prov["db_meta"] == {"snapshot_date": "x"}
    assert data["interpretation_rules"] and data["limitations"]
    assert not list(target(repo).parent.glob("*.tmp"))       # atomic write left no temp file


def test_count_mismatch_stops_before_any_statistic(repo):
    def must_not_run(*_):
        raise AssertionError("statistics computed after a refused guard")
    code, message = bt.run(repo, target(repo), load_db_fake(dict(bt.EXPECTED_COUNTS, S2023=1)),
                           repo / bt.FEE_RESULTS_REL, statistics=must_not_run)
    assert code == bt.EXIT_REFUSED and "REFUSED" in message
    assert not target(repo).exists()


def test_write_new_never_overwrites(tmp_path):
    out = tmp_path / "res" / "out.json"
    bt.write_new(out, {"a": 1})
    with pytest.raises(FileExistsError):
        bt.write_new(out, {"a": 2})
    assert json.loads(out.read_text(encoding="utf-8")) == {"a": 1}
    assert [p.name for p in out.parent.iterdir()] == ["out.json"]
