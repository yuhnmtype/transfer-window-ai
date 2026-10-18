"""Tests for src/phase3_eval_llm.py: synthetic data and a FAKE model only. No test reads the
real test split or its labels, and nothing calls the network. ADK-dependent tests are skipped
when google-adk is not installed (run them in .venv-adk)."""
import hashlib
import json
import math
import subprocess

import numpy as np
import pandas as pd
import pytest

import llm_agents as la
import phase3_eval_llm as ev

Q = 0.714309272881752
BAND = la.band_from_q(Q)
HEX = "a" * 64


def rows(n=4):
    return pd.DataFrame([{
        "player_id": 1000 + i, "transfer_date": pd.Timestamp("2025-07-01") + pd.Timedelta(days=i),
        "from_club_id": 1, "to_club_id": 2, "fee_eur": 10e6 + i * 1e6, "mv_eur": 8e6 + i * 1e6,
        "position": "Midfield", "age": 22.0 + i, "window": "S2025", "split": "test"}
        for i in range(n)])


# ---------- Guards (temporary git repository) ----------

def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    for rel, text in ((ev.CRITERIA_REL, "criteria v1\n"), (ev.TEMPLATES_REL, "t = 'x'\n")):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "criteria")
    git(root, "tag", ev.CRITERIA_TAG)
    (root / ev.SCRIPT_REL).parent.mkdir(parents=True)
    (root / ev.SCRIPT_REL).write_text("# eval\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "eval script after the tag")
    return root


GOOD_SIZES = lambda: dict(ev.EXPECTED_SIZES)   # noqa: E731


def errors(root, sizes=GOOD_SIZES, model_id=ev.REGISTERED_MODEL_ID, target=None):
    return ev.guard_errors(root, sizes, model_id, target or root / "results" / "out.json")


def test_guards_pass(repo):
    assert errors(repo) == []


def test_untracked_hex_cache_files_are_allowed(repo):
    cache = repo / ev.EVAL_CACHE_REL
    cache.mkdir(parents=True)
    (cache / f"{HEX}.json").write_text("{}", encoding="utf-8")
    (cache / f"{'0123456789abcdef' * 4}.json").write_text("{}", encoding="utf-8")
    assert errors(repo) == []


@pytest.mark.parametrize("rel", [
    f"{ev.EVAL_CACHE_REL}/{'A' * 64}.json",           # uppercase hex
    f"{ev.EVAL_CACHE_REL}/{'a' * 63}.json",           # 63 characters
    f"{ev.EVAL_CACHE_REL}/{HEX}.json.tmp",            # leftover temp file
    f"{ev.EVAL_CACHE_REL}/notes.txt",
    f"{ev.EVAL_CACHE_REL}/sub/{HEX}.json",            # not directly in the eval dir
    f"results/llm_cache/pilot/{HEX}.json",            # another cache dir
    "src/extra.py",
])
def test_other_untracked_files_refuse(repo, rel):
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text("{}", encoding="utf-8")
    assert any("not clean" in e for e in errors(repo))


def test_modified_tracked_file_refuses(repo):
    (repo / ev.SCRIPT_REL).write_text("# edited\n", encoding="utf-8")
    assert any("not clean" in e for e in errors(repo))


def test_missing_tag_refuses(repo):
    git(repo, "tag", "-d", ev.CRITERIA_TAG)
    assert any("not found" in e for e in errors(repo))


@pytest.mark.parametrize("rel", [ev.CRITERIA_REL, ev.TEMPLATES_REL])
def test_criteria_or_templates_changed_since_tag_refuse(repo, rel):
    (repo / rel).write_text("changed\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "change")
    assert any(f"{rel} changed since tag" in e for e in errors(repo))


def test_script_existing_at_tag_refuses(repo):
    git(repo, "tag", "-f", ev.CRITERIA_TAG)            # move the tag onto the script commit
    assert any("already existed at the tag commit" in e for e in errors(repo))


def test_split_size_mismatch_refuses(repo):
    assert any("Split sizes" in e for e in errors(repo, sizes=lambda: {"train": 1, "calibration": 2,
                                                                       "test": 3}))


def test_model_id_mismatch_refuses(repo):
    assert any("Model id" in e for e in errors(repo, model_id="other-model"))


def test_existing_results_file_refuses(repo):
    target = repo / "results" / "out.json"
    target.parent.mkdir()
    target.write_text("{}", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "results")
    assert any("already exists" in e for e in errors(repo, target=target))


def test_guards_run_in_the_registered_order(repo):
    (repo / "stray.txt").write_text("x", encoding="utf-8")
    git(repo, "tag", "-d", ev.CRITERIA_TAG)
    target = repo / "out.json"
    target.write_text("{}", encoding="utf-8")
    found = errors(repo, sizes=lambda: {}, model_id="other", target=target)
    order = ["not clean", "not found", "Split sizes", "Model id", "already exists"]
    assert [next(i for i, e in enumerate(found) if key in e) for key in order] == \
        sorted(next(i for i, e in enumerate(found) if key in e) for key in order)


def test_not_a_repository(tmp_path):
    assert "not a repository" in ev.guard_errors(tmp_path, GOOD_SIZES, ev.REGISTERED_MODEL_ID,
                                                 tmp_path / "x.json")[0]


# ---------- Throttle (fake clock) ----------

class FakeClock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 9))
        self.now += seconds


def test_throttle_spaces_requests():
    c = FakeClock()
    t = ev.Throttle(10, clock=c.clock, sleep=c.sleep)   # 60 / 10 = 6 seconds apart
    t.wait()                  # first request: no wait
    c.now += 2.0
    t.wait()                  # 2s after: waits 4s
    c.now += 10.0
    t.wait()                  # 10s after: no wait
    assert c.sleeps == [4.0]


def test_throttle_rejects_non_positive_rpm():
    with pytest.raises(ValueError):
        ev.Throttle(0)


# ---------- Named positions and blind prompts ----------

def test_named_positions():
    pos = ev.named_positions()
    assert pos == sorted(np.random.default_rng(20261015).choice(206, 50, replace=False).tolist())
    assert len(pos) == 50 and len(set(pos)) == 50 and 0 <= min(pos) and max(pos) < 206


def test_ordered_test_rows_keeps_build_order():
    data = pd.concat([rows(3).assign(split="train"), rows(4)]).reset_index(drop=True)
    out = ev.ordered_test_rows(data)
    assert list(out["player_id"]) == [1000, 1001, 1002, 1003] and list(out.index) == [0, 1, 2, 3]


# ---------- Metrics on a tiny hand-checked example ----------

def result(f, valid=True, role="seller"):
    return la.CallResult(role, f, valid, 1, False, False, "r", ())


def test_blind_metrics_hand_example():
    # 2 rows, MV 10m each, m = 1.25 -> fallback 12.5m for both.
    #   row 0: fee 12.5m -> fallback APE 0;   factors 1, 1 -> LLM 12.5 -> APE 0
    #   row 1: fee 25m   -> fallback APE 0.5; factors 2, 2 -> LLM 25   -> APE 0
    # MdAPE fallback = (0 + 0.5) / 2 = 0.25; MdAPE LLM = 0; ratio = 0
    r = rows(2).assign(mv_eur=[10e6, 10e6], fee_eur=[12.5e6, 25e6])
    res = [{"seller": result(1.0), "buyer": result(1.0, role="buyer")},
           {"seller": result(2.0), "buyer": result(2.0, valid=False, role="buyer")}]
    out = ev.blind_metrics(r, res, 1.25, Q)
    assert out["fallback"]["mdape"] == pytest.approx(0.25)
    assert out["llm"]["mdape"] == pytest.approx(0.0)
    assert out["mdape_ratio_llm_to_fallback"] == pytest.approx(0.0)
    # V: 3 of 4 calls valid = 0.75 < 0.95 -> fails; rows with both valid = 1 of 2
    assert out["criterion_v"] == {"passed": False, "valid_share": 0.75,
                                  "rows_both_valid_share": 0.5, "threshold": 0.95}
    assert out["interpretation"].startswith("V failed")
    # seller-only on row 1: 1.25 * 10 * 2 = 25 -> APE 0 -> MdAPE 0
    assert out["seller_only_and_buyer_only_report_only"]["seller_only_mdape_ratio"] == 0.0


def test_interpretation_when_a_and_v_pass_and_when_a_fails():
    r = rows(40)
    good = [{"seller": result(f), "buyer": result(f, role="buyer")}
            for f in (r["fee_eur"] / (1.25 * r["mv_eur"])).clip(BAND[0], BAND[1])]
    assert ev.blind_metrics(r, good, 1.25, Q)["interpretation"] == "passed, leakage not excluded"
    flat = [{"seller": result(1.0), "buyer": result(1.0, role="buyer")} for _ in range(40)]
    assert ev.blind_metrics(r, flat, 1.25, Q)["interpretation"].startswith("A failed")


def test_bootstrap_and_placebo_are_reproducible():
    r = rows(30)
    rng = np.random.default_rng(3)
    res = [{"seller": result(float(a)), "buyer": result(float(b), role="buyer")}
           for a, b in rng.uniform(0.6, 1.8, size=(30, 2))]
    first, second = ev.blind_metrics(r, res, 1.25, Q), ev.blind_metrics(r, res, 1.25, Q)
    assert first["paired_bootstrap"] == second["paired_bootstrap"]
    assert first["placebo_report_only"] == second["placebo_report_only"]
    assert first["paired_bootstrap"]["seed"] == 20261015 and \
        first["paired_bootstrap"]["n_resamples"] == 2000
    # Placebo matches an independent computation with the registered seed
    fee, mv = r["fee_eur"].to_numpy(), r["mv_eur"].to_numpy()
    logs = np.random.default_rng(20261016).uniform(-Q, Q, size=(1000, 30, 2))
    md_b = np.median(np.abs(1.25 * mv - fee) / fee)
    ratios = [np.median(np.abs(1.25 * mv * np.exp(l.mean(axis=1)) - fee) / fee) / md_b for l in logs]
    expected = float(np.mean(np.array(ratios) <= first["mdape_ratio_llm_to_fallback"]) * 100)
    assert first["placebo_report_only"]["percentile_of_llm_ratio"] == pytest.approx(expected)


# ---------- Fake model runs (skipped without google-adk) ----------

def factor_for(prompt):
    """A deterministic factor in the band, derived from the prompt text."""
    return round(0.6 + int(hashlib.sha256(prompt.encode()).hexdigest(), 16) % 120 / 100, 2)


def deterministic_model(fail=False):
    pytest.importorskip("google.adk")
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from pydantic import PrivateAttr

    class Fake(BaseLlm):
        model: str = "fake-model"
        _calls: int = PrivateAttr(default=0)
        _prompts: list = PrivateAttr(default_factory=list)
        _fail: bool = PrivateAttr(default=False)

        async def generate_content_async(self, llm_request, stream=False):
            self._calls += 1
            if self._fail:
                raise RuntimeError("429 TooManyRequests")
            prompt = llm_request.contents[-1].parts[0].text
            self._prompts.append(prompt)
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(
                text=json.dumps({"factor": factor_for(prompt), "rationale": "r"}))]))

    model = Fake()
    model._fail = fail
    return model


def controller(max_requests=10_000):
    c = FakeClock()
    return ev.Controller(ev.Throttle(10_000, clock=c.clock, sleep=c.sleep), max_requests,
                         sleep=c.sleep), c


def run(tmp_path, model, ctrl, target_name="out.json", n=4):
    return ev.execute("blind", rows(n), 1.25, Q, la.load_templates(), la.DiskCache(tmp_path / "cache"),
                      model, ctrl, tmp_path / target_name, "fake-model")


def test_blind_run_completes_and_prompts_have_no_names(tmp_path):
    model = deterministic_model()
    ctrl, _ = controller()
    code, message = run(tmp_path, model, ctrl)
    assert code == ev.EXIT_OK and "blind arm complete: 8 calls" in message
    assert model._calls == 8 and ctrl.live_requests == 8 and ctrl.cache_hits == 0
    for prompt in model._prompts:
        assert "Player:" not in prompt and "club:" not in prompt and "1000" not in prompt
    data = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert data["arm"] == "blind" and data["n_calls"] == 8


def test_budget_stop_writes_no_results_and_resume_is_identical(tmp_path):
    model = deterministic_model()
    ctrl, _ = controller(max_requests=3)
    code, message = run(tmp_path, model, ctrl, "resumed.json")
    assert code == ev.EXIT_BUDGET and "3 of 8 calls done" in message
    assert not (tmp_path / "resumed.json").exists()
    ctrl2, _ = controller()
    code2, _ = run(tmp_path, model, ctrl2, "resumed.json")
    assert code2 == ev.EXIT_OK and ctrl2.cache_hits == 3 and ctrl2.live_requests == 5
    # A single uninterrupted run in a fresh cache gives the same metrics
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    ctrl3, _ = controller()
    assert run(fresh, deterministic_model(), ctrl3, "single.json")[0] == ev.EXIT_OK
    a = json.loads((tmp_path / "resumed.json").read_text(encoding="utf-8"))
    b = json.loads((fresh / "single.json").read_text(encoding="utf-8"))
    for key in ("llm", "fallback", "mdape_ratio_llm_to_fallback", "paired_bootstrap",
                "criterion_a", "criterion_v", "placebo_report_only", "rows"):
        assert a[key] == b[key]


def test_backoff_exhaustion_is_incomplete_and_nothing_is_cached(tmp_path):
    model = deterministic_model(fail=True)
    ctrl, clock = controller()
    code, message = run(tmp_path, model, ctrl)
    assert code == ev.EXIT_INCOMPLETE and "0 of 8 calls done" in message
    assert model._calls == 4 and ctrl.live_requests == 4      # 1 try + 3 backoff retries
    assert clock.sleeps[-3:] == [30, 60, 120]                 # the registered delays
    assert not (tmp_path / "out.json").exists()
    assert list((tmp_path / "cache").glob("*.json")) == []    # external errors are never cached


def test_backoff_counts_against_the_budget(tmp_path):
    ctrl, _ = controller(max_requests=2)
    code, _ = run(tmp_path, deterministic_model(fail=True), ctrl)
    assert code == ev.EXIT_BUDGET and ctrl.live_requests == 2


def test_cache_hits_are_not_throttled(tmp_path):
    model = deterministic_model()
    first, _ = controller()
    run(tmp_path, model, first, "a.json")
    clock = FakeClock()
    second = ev.Controller(ev.Throttle(1, clock=clock.clock, sleep=clock.sleep), 0,
                           sleep=clock.sleep)                  # budget 0: no live request allowed
    code, _ = run(tmp_path, model, second, "b.json")
    assert code == ev.EXIT_OK and second.cache_hits == 8 and clock.sleeps == []


def test_never_overwrites(tmp_path):
    (tmp_path / "out.json").write_text("{}", encoding="utf-8")
    ctrl, _ = controller()
    with pytest.raises(FileExistsError):
        run(tmp_path, deterministic_model(), ctrl)
    assert (tmp_path / "out.json").read_text(encoding="utf-8") == "{}"


def test_named_arm_metrics(tmp_path):
    model = deterministic_model()
    blind_ctrl, _ = controller()
    run(tmp_path, model, blind_ctrl, "blind.json", n=3)
    blind_rows = json.loads((tmp_path / "blind.json").read_text(encoding="utf-8"))["rows"]
    names = [{"player_name": f"P{i}", "seller_club": "Alpha", "buyer_club": "Beta"} for i in range(3)]
    ctrl, _ = controller()
    code, message = ev.execute("named", rows(3), 1.25, Q, la.load_templates(),
                               la.DiskCache(tmp_path / "cache"), model, ctrl,
                               tmp_path / "named.json", "fake-model", names, blind_rows)
    assert code == ev.EXIT_OK and "report only" in message
    data = json.loads((tmp_path / "named.json").read_text(encoding="utf-8"))
    assert data["arm"] == "named" and data["n_rows"] == 3
    assert data["blind_mdape_same_rows"] == pytest.approx(
        float(np.median([b["ape_llm"] for b in blind_rows])))
    assert any("- Player: P0" in p for p in model._prompts)


# ---------- Bootstrap sign (pure) ----------

@pytest.mark.parametrize("combined, a_passes, upper_sign", [
    (1.1, True, -1),    # LLM closer than the fallback on every row
    (0.9, False, +1),   # LLM further away on every row
])
def test_bootstrap_difference_is_llm_minus_fallback(combined, a_passes, upper_sign):
    # Every row: fee / (m x MV) = 1.2, so the fallback APE is (1.2 - 1) / 1.2 = 0.1667.
    #   combined 1.1 -> APE (1.2 - 1.1) / 1.2 = 0.0833 < 0.1667: difference -0.0833 on every
    #   resample, ratio 0.5 -> A passes.
    #   combined 0.9 -> APE (1.2 - 0.9) / 1.2 = 0.25 > 0.1667: difference +0.0833 -> A fails.
    r = rows(20).assign(mv_eur=10e6, fee_eur=1.2 * 1.25 * 10e6)
    res = [{"seller": result(combined), "buyer": result(combined, role="buyer")} for _ in range(20)]
    out = ev.blind_metrics(r, res, 1.25, Q)
    upper = out["paired_bootstrap"]["upper_97_5"]
    assert upper == pytest.approx(upper_sign * (0.25 - 0.1667 if upper_sign > 0 else 0.1667 - 0.0833),
                                  abs=1e-3)
    assert (upper < 0) is (upper_sign < 0)
    assert out["criterion_a"]["passed"] is a_passes


# ---------- Error classification (fake model raising GenAI errors) ----------

def scripted_model(outcomes):
    """Each call takes the next outcome: an exception instance to raise, or "ok"."""
    pytest.importorskip("google.adk")
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from pydantic import PrivateAttr

    class Scripted(BaseLlm):
        model: str = "fake-model"
        _outcomes: list = PrivateAttr(default_factory=list)
        _calls: int = PrivateAttr(default=0)

        async def generate_content_async(self, llm_request, stream=False):
            self._calls += 1
            outcome = self._outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(
                text=json.dumps({"factor": 1.1, "rationale": "r"}))]))

    model = Scripted()
    model._outcomes = list(outcomes)
    return model


def api_error(code):
    from google.genai import errors
    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {"error": {"code": code, "message": "test", "status": "TEST"}})


def run_one(tmp_path, model):
    # 2 rows = 4 primary calls (with 1 row the fallback APE is 0 and the ratio is undefined)
    ctrl, clock = controller()
    code, message = run(tmp_path, model, ctrl, n=2)
    return code, message, ctrl, clock


def cached_files(tmp_path):
    return list((tmp_path / "cache").glob("*.json"))


def test_429_is_retried_then_succeeds(tmp_path):
    pytest.importorskip("google.adk")
    code, _, ctrl, clock = run_one(tmp_path, scripted_model([api_error(429)] + ["ok"] * 4))
    assert code == ev.EXIT_OK and ctrl.live_requests == 5
    assert [s for s in clock.sleeps if s >= 30] == [30]
    assert len(cached_files(tmp_path)) == 4          # only the four successful responses


@pytest.mark.parametrize("status", [500, 503])
def test_5xx_is_retried(tmp_path, status):
    pytest.importorskip("google.adk")
    code, _, ctrl, clock = run_one(tmp_path, scripted_model([api_error(status), api_error(status)]
                                                            + ["ok"] * 4))
    assert code == ev.EXIT_OK and ctrl.live_requests == 6
    assert [s for s in clock.sleeps if s >= 30] == [30, 60]


def test_error_without_status_is_retried(tmp_path):
    pytest.importorskip("google.adk")
    code, _, _, clock = run_one(tmp_path, scripted_model([ConnectionError("network")] + ["ok"] * 4))
    assert code == ev.EXIT_OK and [s for s in clock.sleeps if s >= 30] == [30]


@pytest.mark.parametrize("status", [400, 401, 403])
def test_rejected_requests_fail_fast(tmp_path, status):
    pytest.importorskip("google.adk")
    model = scripted_model([api_error(status)])
    code, message, ctrl, clock = run_one(tmp_path, model)
    assert code == ev.EXIT_INCOMPLETE
    assert f"request rejected (HTTP {status}): check credentials and configuration" in message
    assert model._calls == 1 and ctrl.live_requests == 1
    assert [s for s in clock.sleeps if s >= 30] == []  # no backoff wait
    assert cached_files(tmp_path) == []


def test_other_4xx_fails_fast(tmp_path):
    pytest.importorskip("google.adk")
    model = scripted_model([api_error(422)])
    code, message, _, clock = run_one(tmp_path, model)
    assert code == ev.EXIT_INCOMPLETE and "HTTP 422" in message and model._calls == 1
    assert [s for s in clock.sleeps if s >= 30] == []


def test_404_is_not_run(tmp_path):
    pytest.importorskip("google.adk")
    model = scripted_model([api_error(404)])
    code, message, _, clock = run_one(tmp_path, model)
    assert code == ev.EXIT_NOT_RUN == 4
    assert message.startswith("NOT RUN: model gemini-3.5-flash-lite is unavailable "
                              "(docs/llm_criteria.md, Model)")
    assert model._calls == 1 and [s for s in clock.sleeps if s >= 30] == []
    assert cached_files(tmp_path) == [] and not (tmp_path / "out.json").exists()


def test_http_status_reads_the_cause_chain():
    class WithCode(Exception):
        code = 429
    inner = WithCode()
    try:
        try:
            raise inner
        except WithCode as e:
            raise la.LlmCallError("seller call failed: WithCode") from e
    except la.LlmCallError as outer:
        assert ev.http_status(outer) == 429
    assert ev.http_status(la.LlmCallError("no cause")) is None
    assert ev.is_retryable(None) and ev.is_retryable(429) and ev.is_retryable(503)
    assert not any(ev.is_retryable(s) for s in (400, 401, 403, 404, 422))


def test_help_documents_exit_codes(capsys):
    with pytest.raises(SystemExit):
        ev.build_parser().parse_args(["--help"])
    out = " ".join(capsys.readouterr().out.split())   # argparse wraps lines
    assert "4 NOT RUN" in out and "3 request budget reached" in out
