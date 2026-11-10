"""Tests for src/advisor_agent.py. No network: the pure parts run anywhere, the ADK parts use a
scripted FAKE model and are skipped when google-adk is not installed (run them in .venv-adk)."""
import json

import pytest

import advisor_agent as aa
from advisor import IncomingPlayer, advise_deal, load_advisor_config, to_dict
from scr import load_config
from test_advisor import make_db

INELIGIBLE = {"club_id": 2, "club_name": "Relegated FC", "in_pl_next_season": 0}


@pytest.fixture
def rules():
    return load_config("2026/27")


@pytest.fixture
def adv():
    return load_advisor_config()


@pytest.fixture
def conn(tmp_path):
    return make_db(tmp_path / "agent.db", {}, INELIGIBLE)


def deal(conn, rules, adv, **overrides):
    args = dict(club_id=1, fee=20, fee_currency="GBP", annual_wage_gbp_m=3, contract_years=4)
    args.update(overrides)
    return aa.tool_advise_deal(conn, rules, adv, **args)


# ---------- Tools ----------

def test_list_buyer_clubs_excludes_clubs_not_in_the_league(conn, rules):
    assert aa.tool_list_buyer_clubs(conn, rules) == [{"club_id": 1, "club_name": "Test FC"}]


def test_advise_deal_returns_rounded_advisor_values(conn, rules, adv):
    out = deal(conn, rules, adv)
    raw = to_dict(advise_deal(conn, rules, 1, IncomingPlayer(20, "GBP", 3, 4), advisor_config=adv))
    assert out["after_ratio"] == round(raw["after"]["ratio"], 4)
    assert out["after_headroom_gbp_m"] == round(raw["after"]["headroom_gbp_m"], 2)
    assert out["headroom_change_gbp_m"] == round(raw["headroom_change_gbp_m"], 2)
    assert out["annual_amortisation_gbp_m"] == 5.0
    assert set(aa.QUOTABLE) <= set(out)
    assert (out["before_status"], out["after_status"]) == (raw["before"]["status"],
                                                           raw["after"]["status"])
    assert "agent_fee_assumed_zero" in out["warnings"]


def test_advise_deal_accepts_an_integral_float_club_id(conn, rules, adv):
    assert deal(conn, rules, adv, club_id=1.0)["club_id"] == 1


@pytest.mark.parametrize("overrides, fragment", [
    ({"club_id": 999}, "No club_finance row"),
    ({"club_id": 2}, "not in the Premier League next season"),
    ({"club_id": 1.5}, "whole number"),
    ({"club_id": True}, "whole number"),
    ({"fee_currency": "USD"}, "GBP or EUR"),
    ({"annual_wage_gbp_m": -1}, ""),
    ({"contract_years": 0}, ""),
])
def test_advise_deal_errors_are_returned_not_raised(conn, rules, adv, overrides, fragment):
    out = deal(conn, rules, adv, **overrides)
    assert set(out) == {"error"} and fragment in out["error"]


def test_eur_fee_is_converted_by_code(conn, rules, adv):
    out = deal(conn, rules, adv, fee=10, fee_currency="EUR")
    assert out["fee_gbp_m"] == round(10 * rules["fx"]["gbp_per_eur"], 2)
    assert "fee_converted_from_eur" in out["warnings"]


# ---------- Output parsing ----------

GOOD = {"outcome": "answered", "club_id": 1, "answer": "ok",
        "quoted": [{"field": "after_ratio", "value": 0.9316}]}


@pytest.mark.parametrize("text, ok", [
    (json.dumps(GOOD), True),
    ("  " + json.dumps(GOOD) + "\n", True),
    (json.dumps({**GOOD, "outcome": "out_of_scope", "club_id": None, "quoted": []}), True),
    ("```json\n" + json.dumps(GOOD) + "\n```", False),
    (json.dumps(GOOD) + " thanks", False),
    (json.dumps({**GOOD, "outcome": "green"}), False),
    (json.dumps({**GOOD, "club_id": True}), False),
    (json.dumps({**GOOD, "club_id": "1"}), False),
    (json.dumps({**GOOD, "answer": None}), False),
    (json.dumps({**GOOD, "quoted": [{"field": "x", "value": True}]}), False),
    (json.dumps({**GOOD, "quoted": [{"field": "x", "value": "1"}]}), False),
    ('{"outcome": "answered", "club_id": 1, "answer": "a", "quoted": [{"field": "x", "value": NaN}]}', False),
    ("[]", False),
    ("", False),
])
def test_parse_output(text, ok):
    assert (aa.parse_output(text) is not None) is ok


# ---------- Cache ----------

def test_cache_key_ignores_function_call_ids():
    a = aa._strip_ids({"contents": [{"parts": [{"function_call": {"id": "adk-1", "name": "f"}}]}]})
    b = aa._strip_ids({"contents": [{"parts": [{"function_call": {"id": "adk-2", "name": "f"}}]}]})
    assert aa.cache_key("m", "Q01", 1, a) == aa.cache_key("m", "Q01", 1, b)
    assert aa.cache_key("m", "Q01", 1, a) != aa.cache_key("m", "Q01", 2, a)


@pytest.mark.parametrize("entry", [
    {"key": "k", "extra": 1},
    {"key": "k", "request": {"text": "AIza" + "x" * 30}},
    {"key": "k", "request": {"headers": "x-goog-api-key: abc"}},
])
def test_cache_refuses_extra_fields_and_secrets(tmp_path, entry):
    with pytest.raises(ValueError):
        aa.AgentCache(tmp_path).put(entry)


def test_system_prompt_loads_and_names_both_tools():
    text = aa.load_system_prompt()
    assert "list_buyer_clubs" in text and "advise_deal" in text
    assert len(aa.prompt_hash(text)) == 64


# ---------- ADK with a scripted fake model (skipped without google-adk) ----------

def fake_model(steps, fail=False):
    """steps: ("call", tool_name, args) or ("text", text), one per model request."""
    pytest.importorskip("google.adk")
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from pydantic import PrivateAttr

    class FakeLlm(BaseLlm):
        model: str = "fake-model"
        _steps: list = PrivateAttr(default_factory=list)
        _calls: int = PrivateAttr(default=0)
        _fail: bool = PrivateAttr(default=False)

        async def generate_content_async(self, llm_request, stream=False):
            self._calls += 1
            if self._fail:
                raise RuntimeError("quota exceeded")
            kind, *rest = self._steps.pop(0) if self._steps else ("call", "list_buyer_clubs", {})
            if kind == "call":
                part = types.Part(function_call=types.FunctionCall(name=rest[0], args=rest[1]))
            else:
                part = types.Part(text=rest[0])
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    model = FakeLlm()
    model._steps, model._fail = list(steps), fail
    return model


DEAL_ARGS = {"club_id": 1, "fee": 20, "fee_currency": "GBP", "annual_wage_gbp_m": 3,
             "contract_years": 4}


def answered(quoted):
    return ("call", "set_model_response",
            {"outcome": "answered", "club_id": 1, "answer": "Ratio rises.", "quoted": quoted})


def run(conn, rules, adv, cache, model, question_id="Q01", run_index=1, **kw):
    return aa.run_question(question_id, "Test FC: £20m, £3m a year, 4 years.", run_index,
                           conn=conn, rules_config=rules, advisor_config=adv,
                           system_text=aa.load_system_prompt(), cache=cache, model=model,
                           model_id="fake-model", **kw)


def test_adk_answered_run_records_tool_calls_results_and_output(conn, rules, adv, tmp_path):
    expected = deal(conn, rules, adv)
    quoted = [{"field": "after_ratio", "value": expected["after_ratio"]}]
    model = fake_model([("call", "list_buyer_clubs", {}), ("call", "advise_deal", DEAL_ARGS),
                        answered(quoted)])
    r = run(conn, rules, adv, aa.AgentCache(tmp_path), model)
    assert r["stopped"] is None and r["model_calls"] == 3 == model._calls
    assert r["output"]["outcome"] == "answered" and r["output"]["quoted"] == quoted
    calls = [(t["name"], t["args"]) for t in r["transcript"] if t["type"] == "call"]
    assert calls[:2] == [("list_buyer_clubs", {}), ("advise_deal", DEAL_ARGS)]
    results = {t["name"]: t["result"] for t in r["transcript"] if t["type"] == "result"}
    assert results["advise_deal"] == expected
    assert results["list_buyer_clubs"] == {"result": [{"club_id": 1, "club_name": "Test FC"}]}


def test_adk_replay_from_cache_makes_no_model_calls(conn, rules, adv, tmp_path):
    steps = [("call", "list_buyer_clubs", {}), ("call", "advise_deal", DEAL_ARGS),
             answered([{"field": "after_ratio", "value": deal(conn, rules, adv)["after_ratio"]}])]
    first = run(conn, rules, adv, aa.AgentCache(tmp_path), fake_model(steps))
    failing = fake_model([], fail=True)
    again = run(conn, rules, adv, aa.AgentCache(tmp_path), failing)
    assert failing._calls == 0
    assert (again["output"], again["transcript"]) == (first["output"], first["transcript"])
    other_run = run(conn, rules, adv, aa.AgentCache(tmp_path), fake_model(steps), run_index=2)
    assert other_run["output"] == first["output"]     # run index 2 is a separate cache entry
    assert len(list(tmp_path.glob("*.json"))) == 6


def test_adk_tool_error_is_visible_in_the_transcript(conn, rules, adv, tmp_path):
    model = fake_model([("call", "advise_deal", {**DEAL_ARGS, "club_id": 2}),
                        ("call", "set_model_response", {"outcome": "tool_error", "club_id": None,
                                                        "answer": "Not a buyer.", "quoted": []})])
    r = run(conn, rules, adv, aa.AgentCache(tmp_path), model)
    result = next(t["result"] for t in r["transcript"] if t["name"] == "advise_deal"
                  and t["type"] == "result")
    assert "error" in result and r["output"]["outcome"] == "tool_error"


def test_adk_model_call_limit_stops_the_run(conn, rules, adv, tmp_path):
    model = fake_model([])          # keeps calling list_buyer_clubs forever
    r = run(conn, rules, adv, aa.AgentCache(tmp_path), model, max_model_calls=3)
    assert r["stopped"] == "model_call_limit" and r["output"] is None
    assert r["model_calls"] == 4 and model._calls == 3
    assert sum(t["type"] == "call" for t in r["transcript"]) == 3


def test_adk_plain_text_answer_is_not_parsed(conn, rules, adv, tmp_path):
    r = run(conn, rules, adv, aa.AgentCache(tmp_path), fake_model([("text", "It is fine.")]))
    assert r["stopped"] is None and r["output"] is None


def test_adk_external_error_raises_and_caches_nothing(conn, rules, adv, tmp_path):
    with pytest.raises(aa.LlmCallError):
        run(conn, rules, adv, aa.AgentCache(tmp_path), fake_model([], fail=True))
    assert list(tmp_path.glob("*.json")) == []


def test_adk_cache_entries_hold_only_whitelisted_fields(conn, rules, adv, tmp_path):
    run(conn, rules, adv, aa.AgentCache(tmp_path),
        fake_model([("call", "list_buyer_clubs", {}),
                    ("call", "set_model_response", {"outcome": "out_of_scope", "club_id": None,
                                                    "answer": "No.", "quoted": []})]))
    for path in tmp_path.glob("*.json"):
        entry = json.loads(path.read_text(encoding="utf-8"))
        assert set(entry) == set(aa.CACHE_FIELDS)
        aa.check_no_secrets(entry)


# ---------- Question file ----------

REPO_ROOT = aa.REPO_ROOT
DB_PATH = REPO_ROOT / "transfer_window_ai.db"


def test_registered_question_file_shape():
    data = aa.load_questions()
    kinds = [q["kind"] for q in data["question"]]
    assert (kinds.count("in_scope"), kinds.count("out_of_scope"), len(data["pilot"])) == (22, 8, 4)
    types = [q["type"] for q in data["question"] if q["kind"] == "out_of_scope"]
    assert types.count("unknown_club") + types.count("ineligible_club") == 2
    for t in ("real_world_conclusion", "loan", "other_season"):
        assert types.count(t) == 2


def test_question_texts_match_the_criteria_draft():
    criteria = (REPO_ROOT / "docs" / "agent_criteria.md").read_text(encoding="utf-8")
    for q in aa.load_questions()["question"]:
        assert f"| {q['id']} |" in criteria and q["text"] in criteria


def write_questions(tmp_path, body):
    path = tmp_path / "q.toml"
    path.write_text(body, encoding="utf-8")
    return path


IN_SCOPE = ('[[question]]\nid = "{id}"\nkind = "in_scope"\ntext = "{text}"\n'
            '[question.expected]\nclub_id = 1\nfee = 1.0\nfee_currency = "GBP"\n'
            'annual_wage_gbp_m = 1.0\ncontract_years = 1.0\n')


@pytest.mark.parametrize("body, fragment", [
    (IN_SCOPE.format(id="Q1", text="a") + IN_SCOPE.format(id="Q1", text="b"), "unique"),
    (IN_SCOPE.format(id="Q1", text="a") + IN_SCOPE.replace("question", "pilot")
     .format(id="P1", text="a"), "repeats"),
    ('[[question]]\nid = "X"\nkind = "other"\ntext = "a"\n', "unknown kind"),
    ('[[question]]\nid = "O"\nkind = "out_of_scope"\ntype = "weather"\ntext = "a"\n', "type"),
    (IN_SCOPE.format(id="Q1", text="a").replace('fee_currency = "GBP"', 'fee_currency = "USD"'),
     "fee_currency"),
    (IN_SCOPE.format(id="Q1", text="a").replace("contract_years = 1.0\n", ""), "missing"),
])
def test_question_loader_refuses_bad_files(tmp_path, body, fragment):
    with pytest.raises(ValueError, match=fragment):
        aa.load_questions(write_questions(tmp_path, body))


@pytest.mark.skipif(not DB_PATH.exists(), reason="database file not present")
def test_every_expected_argument_set_is_a_valid_deal_on_the_real_database(rules, adv):
    from advise_cli import open_read_only
    conn = open_read_only(DB_PATH)
    data = aa.load_questions()
    names = {c["club_name"] for c in aa.tool_list_buyer_clubs(conn, rules)}
    for q in data["question"] + data["pilot"]:
        if q["kind"] == "in_scope":
            out = aa.tool_advise_deal(conn, rules, adv, **q["expected"])
            assert "error" not in out, (q["id"], out)
    assert not {"Real Madrid", "Ajax", "Wolverhampton Wanderers"} & names


# ---------- Pilot script ----------

def test_run_pilot_runs_each_question_once_and_counts(conn, rules, adv, tmp_path):
    import agent_pilot
    questions = [{"id": "P01", "kind": "in_scope", "text": "Test FC deal", "expected": DEAL_ARGS},
                 {"id": "P02", "kind": "out_of_scope", "type": "loan", "text": "A loan?"}]
    model = fake_model([("call", "list_buyer_clubs", {}), ("call", "advise_deal", DEAL_ARGS),
                        answered([]),
                        ("call", "set_model_response", {"outcome": "out_of_scope",
                                                        "club_id": None, "answer": "No loans.",
                                                        "quoted": []})])
    pauses = []
    record = agent_pilot.run_pilot(questions, conn, rules, adv, aa.load_system_prompt(),
                                   aa.AgentCache(tmp_path), model, "fake-model", pause=30,
                                   sleep=pauses.append)
    assert record["counts"] == {"runs": 2, "parsed": 2, "stopped": 0, "model_calls": 4}
    assert [r["output"]["outcome"] for r in record["runs"]] == ["answered", "out_of_scope"]
    assert pauses == [30]
    json.dumps(record, allow_nan=False)     # the record can be written as strict JSON


# ---------- Pilot guards (temporary git repository) ----------

HEX = "b" * 64


def git(repo, *args):
    import subprocess
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture
def pilot_repo(tmp_path):
    import agent_pilot as ap
    root = tmp_path / "repo"
    for rel in (ap.PROMPT_REL, ap.QUESTIONS_REL):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x = 1\n", encoding="utf-8")
    (root / ".gitignore").write_text("*.db\n", encoding="utf-8")
    (root / ap.DB_REL).write_text("db", encoding="utf-8")       # ignored, like the real DB
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "pilot inputs")
    return root


def pilot_errors(root):
    import agent_pilot as ap
    return ap.guard_errors(root)


def test_pilot_guards_pass_on_a_clean_tree(pilot_repo):
    assert pilot_errors(pilot_repo) == []


def test_pilot_allows_untracked_hex_cache_entries(pilot_repo):
    import agent_pilot as ap
    cache = pilot_repo / ap.CACHE_REL
    cache.mkdir(parents=True)
    (cache / f"{HEX}.json").write_text("{}", encoding="utf-8")
    assert pilot_errors(pilot_repo) == []


@pytest.mark.parametrize("make_dirty", [
    lambda root, ap: (root / ap.PROMPT_REL).write_text("x = 2\n", encoding="utf-8"),
    lambda root, ap: (root / "notes.txt").write_text("n", encoding="utf-8"),
    lambda root, ap: ((root / ap.CACHE_REL).mkdir(parents=True),
                      (root / ap.CACHE_REL / "not-a-key.json").write_text("{}", encoding="utf-8")),
])
def test_pilot_refuses_a_dirty_tree(pilot_repo, make_dirty):
    import agent_pilot as ap
    make_dirty(pilot_repo, ap)
    errors = pilot_errors(pilot_repo)
    assert len(errors) == 1 and errors[0].startswith("Working tree is not clean")


def test_pilot_refuses_missing_inputs_and_an_existing_result(pilot_repo):
    import agent_pilot as ap
    (pilot_repo / ap.DB_REL).unlink()
    (pilot_repo / ap.RESULTS_REL).parent.mkdir(parents=True)
    (pilot_repo / ap.RESULTS_REL).write_text("{}", encoding="utf-8")
    errors = pilot_errors(pilot_repo)
    assert f"Missing file: {ap.DB_REL}" in errors
    assert any(e.startswith("Results file already exists") for e in errors)


def test_pilot_refuses_outside_a_repository(tmp_path):
    assert pilot_errors(tmp_path)[0].startswith("git is not available")
