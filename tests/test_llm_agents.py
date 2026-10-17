"""Tests for src/llm_agents.py. No network: the pure parts run anywhere, the ADK parts use a
FAKE model and are skipped when google-adk is not installed (run them in .venv-adk)."""
import json
import math

import pandas as pd
import pytest

import llm_agents as la

BAND = (0.4895, 2.0428)

# Pinned so that any edit of a template (which needs a new criteria tag) fails this test.
PINNED_SHA256 = {
    "system": "07e0d4a6779c8ac0403587d72a6201b3fc903f46bd20e7adf2f870556f79226b",
    "seller_blind": "af1509ae7f92ecf338b39ede72996e5c386220aee52e4305a3130a22c29628d8",
    "buyer_blind": "04fe2c0927a7230b411dc05e61bd3144d56dcb17fa71ddb8fc18c7fba62d4bdb",
    "seller_named": "77af1a2bd3767a5272a73eef1260c5f07d430520fd6da17e7bce5bb4f7995db3",
    "buyer_named": "57cfb9ff64b70b66458c58b80ef060b4cd33f7865c8499e24294ca0c1ec9cdbf",
}


def row(**overrides):
    base = {"position": "Defender", "age": 24.6, "mv_eur": 49_600_000.0, "window": "S2025",
            "player_id": 123, "transfer_date": pd.Timestamp("2025-07-15"), "split": "test"}
    base.update(overrides)
    return pd.Series(base)


# ---------- Templates and rounding ----------

def test_template_hashes_are_pinned_and_stable():
    templates = la.load_templates()
    assert set(templates) == set(la.TEMPLATE_NAMES)
    assert {k: la.template_hash(v) for k, v in templates.items()} == PINNED_SHA256
    assert {k: la.template_hash(v) for k, v in la.load_templates().items()} == PINNED_SHA256


@pytest.mark.parametrize("age, expected", [(24.49, 24), (24.5, 25), (24.6, 25), (17.0, 17)])
def test_age_rounding_half_up(age, expected):
    assert la.prompt_age(age) == expected


@pytest.mark.parametrize("mv, expected", [
    (300_000, 1),        # below 0.5m -> minimum 1m
    (1_500_000, 2),      # half up (Python's round would give 2 here too)
    (2_500_000, 3),      # half up, NOT banker's rounding (round(2.5) == 2)
    (49_600_000, 50),
    (49_400_000, 49),
])
def test_market_value_rounding(mv, expected):
    assert la.prompt_mv_m(mv) == expected


def test_band_from_q_matches_criteria():
    # q from results/fee_model_eval.json -> [0.4895, 2.0428]
    assert la.band_from_q(0.714309272881752) == BAND


def test_blind_prompt_has_only_registered_features():
    text = la.render_prompt(la.load_templates()["seller_blind"], row(), 1.25, BAND)
    assert "Position: Defender" in text
    assert "Age at transfer: 25" in text                      # 24.6 -> 25
    assert "Market value at transfer: 50 million EUR" in text  # 49.6m -> 50
    assert "Reference fee: 62.50 million EUR (market value x 1.25)" in text   # 1.25 x 50
    assert "between 0.4895 and 2.0428" in text
    assert "Transfer window: summer" in text
    # never: ids, dates, seasons
    for forbidden in ("123", "2025", "S2025", "Player:", "club:"):
        assert forbidden not in text
    assert text.endswith('{"factor": <number>, "rationale": "<at most 40 words>"}')


def test_named_prompt_adds_only_name_and_clubs():
    names = {"player_name": "Test Player", "seller_club": "Alpha FC", "buyer_club": "Beta FC"}
    named = la.render_prompt(la.load_templates()["buyer_named"], row(window="W2026"), 1.25,
                             BAND, names)
    blind = la.render_prompt(la.load_templates()["buyer_blind"], row(window="W2026"), 1.25, BAND)
    assert "- Player: Test Player\n- Selling club: Alpha FC\n- Buying club: Beta FC" in named
    # Blind and named differ ONLY by the player, selling club and buying club lines
    extra = ["- Player: Test Player", "- Selling club: Alpha FC", "- Buying club: Beta FC"]
    assert [line for line in named.splitlines() if line not in extra] == blind.splitlines()


def test_reference_sentence_explains_factor_one():
    templates = la.load_templates()
    for name in ("seller_blind", "buyer_blind", "seller_named", "buyer_named"):
        text = la.render_prompt(templates[name], row(), 1.25, BAND,
                                {"player_name": "P", "seller_club": "S", "buyer_club": "B"})
        assert "differs from the reference fee (1.0 means equal to the reference fee). " \
               "The factor must be between 0.4895 and 2.0428." in text


@pytest.mark.parametrize("window, expected", [("S2025", "summer"), ("W2026", "winter"),
                                              ("S2013", "summer"), ("W2024", "winter")])
def test_window_label_has_no_year(window, expected):
    assert la.window_label(window) == expected
    text = la.render_prompt(la.load_templates()["seller_blind"], row(window=window), 1.25, BAND)
    assert f"- Transfer window: {expected}\n" in text
    assert window[1:] not in text and "January" not in text


def test_window_label_rejects_unknown_codes():
    with pytest.raises(ValueError):
        la.window_label("X2025")


# ---------- Parsing and the validity rules ----------

@pytest.mark.parametrize("text, expected", [
    ('{"factor": 1.1, "rationale": "ok"}', 1.1),
    ('  {"factor": 2, "rationale": ""}  ', 2.0),
    ('{"factor": "1.1", "rationale": "ok"}', None),     # string, not a number
    ('{"factor": true, "rationale": "ok"}', None),      # bool is not a number here
    ('{"factor": NaN, "rationale": "ok"}', None),
    ('{"factor": 1.1}', None),                          # rationale missing
    ('```json\n{"factor": 1.1, "rationale": "ok"}\n```', None),   # strict: no fences
    ("not json", None),
    ("[1.1]", None),
])
def test_parse_factor(text, expected):
    assert la.parse_factor(text) == expected


def ok(f):
    return json.dumps({"factor": f, "rationale": "r"})


def test_valid_on_first_attempt():
    d = la.decide([ok(1.2)], BAND)
    assert (d["factor"], d["valid"], d["attempts"], d["clipped"], d["fallback"]) == \
        (1.2, True, 1, False, False)


def test_band_edges_are_inside():
    assert la.decide([ok(0.4895)], BAND)["valid"] is True
    assert la.decide([ok(2.0428)], BAND)["valid"] is True


def test_retry_valid():
    d = la.decide(["garbage", ok(0.9)], BAND)
    assert (d["factor"], d["valid"], d["attempts"]) == (0.9, True, 2)


def test_out_of_band_first_then_valid_retry():
    d = la.decide([ok(3.0), ok(1.5)], BAND)
    assert (d["factor"], d["valid"], d["attempts"], d["clipped"]) == (1.5, True, 2, False)


@pytest.mark.parametrize("retry, clipped_to", [(3.0, 2.0428), (0.1, 0.4895)])
def test_retry_out_of_band_is_clipped_and_not_valid(retry, clipped_to):
    d = la.decide([ok(5.0), ok(retry)], BAND)
    assert (d["factor"], d["valid"], d["clipped"], d["fallback"]) == \
        (clipped_to, False, True, False)


def test_retry_unparseable_falls_back_to_one():
    d = la.decide([ok(5.0), "still not json"], BAND)
    assert (d["factor"], d["valid"], d["clipped"], d["fallback"], d["attempts"]) == \
        (1.0, False, False, True, 2)


def test_decide_requires_retry_when_first_invalid():
    with pytest.raises(ValueError):
        la.decide(["garbage"], BAND)


def test_summarise_counts():
    def cr(valid, attempts, clipped=False, fallback=False):
        return la.CallResult("seller", 1.0, valid, attempts, clipped, fallback, None, ())
    counts = la.summarise([cr(True, 1), cr(True, 2), cr(False, 2, clipped=True),
                           cr(False, 2, fallback=True)])
    assert counts == {"calls": 4, "valid": 2, "retried": 3, "clipped": 1, "fallback": 1,
                      "valid_share": 0.5}


def test_geometric_factor():
    assert la.geometric_factor(0.5, 2.0) == pytest.approx(1.0)
    assert la.geometric_factor(1.21, 1.0) == pytest.approx(1.1)


# ---------- Cache ----------

def test_cache_key_depends_on_every_input():
    base = la.cache_key("m1", "seller", 1, "sys", "prompt")
    assert base == la.cache_key("m1", "seller", 1, "sys", "prompt")
    variants = [la.cache_key("m2", "seller", 1, "sys", "prompt"),
                la.cache_key("m1", "buyer", 1, "sys", "prompt"),
                la.cache_key("m1", "seller", 2, "sys", "prompt"),
                la.cache_key("m1", "seller", 1, "sys2", "prompt"),
                la.cache_key("m1", "seller", 1, "sys", "prompt!"),
                la.cache_key("m1", "seller", 1, "sys", "prompt",
                             {**la.GENERATION, "temperature": 0.5})]
    assert len({base, *variants}) == 7


def entry(key="k1", **overrides):
    e = {"key": key, "model_id": "fake-model", "role": "seller", "attempt": 1,
         "generation": la.GENERATION, "system_text": "sys", "prompt_text": "p",
         "response_text": ok(1.1)}
    e.update(overrides)
    return e


def test_cache_miss_hit_and_no_overwrite(tmp_path):
    cache = la.DiskCache(tmp_path / "c")
    assert cache.get("k1") is None
    assert cache.put(entry()) is True
    assert cache.get("k1")["response_text"] == ok(1.1)
    assert cache.put(entry(response_text=ok(2.0))) is False      # never overwritten
    assert cache.get("k1")["response_text"] == ok(1.1)


def test_cache_refuses_extra_fields(tmp_path):
    with pytest.raises(ValueError):
        la.DiskCache(tmp_path).put(entry(headers={"x": "y"}))


@pytest.mark.parametrize("text", ["AIza" + "x" * 35, "x-goog-api-key: abc",
                                  "Authorization: Bearer abc", "my api_key is here"])
def test_cache_refuses_secret_like_text(tmp_path, text):
    with pytest.raises(ValueError):
        la.DiskCache(tmp_path).put(entry(response_text=text))


def test_cache_scan_passes_on_clean_entries(tmp_path):
    cache = la.DiskCache(tmp_path)
    cache.put(entry("k1"))
    cache.put(entry("k2"))
    cache.scan_for_secrets()


# ---------- ADK with a fake model (skipped without google-adk) ----------

SAFETY_BLOCK = "<safety-block>"


def fake_model(texts, fail=False):
    pytest.importorskip("google.adk")
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from pydantic import PrivateAttr

    class FakeLlm(BaseLlm):
        model: str = "fake-model"
        _texts: list = PrivateAttr(default_factory=list)
        _calls: int = PrivateAttr(default=0)
        _fail: bool = PrivateAttr(default=False)

        async def generate_content_async(self, llm_request, stream=False):
            self._calls += 1
            if self._fail:
                raise RuntimeError("quota exceeded")
            text = self._texts.pop(0)
            if text == SAFETY_BLOCK:   # a safety-blocked answer: no content at all
                yield LlmResponse(content=None, finish_reason=types.FinishReason.SAFETY,
                                  error_code="SAFETY", error_message="blocked")
                return
            yield LlmResponse(content=types.Content(
                role="model", parts=[types.Part(text=text)]))

    model = FakeLlm()
    model._texts, model._fail = list(texts), fail
    return model


def test_adk_valid_first_attempt_then_cache_hit(tmp_path):
    model = fake_model([ok(1.1)])
    cache = la.DiskCache(tmp_path)
    r = la.evaluate_call("seller", "sys", "prompt A", BAND, cache, model, "fake-model")
    assert (r.factor, r.valid, r.attempts, model._calls) == (1.1, True, 1, 1)
    again = la.evaluate_call("seller", "sys", "prompt A", BAND, cache, model, "fake-model")
    assert (again.factor, model._calls) == (1.1, 1)     # served by before_model_callback


def test_adk_retry_is_a_new_call(tmp_path):
    model = fake_model(["not json", ok(0.8)])
    r = la.evaluate_call("buyer", "sys", "prompt B", BAND, la.DiskCache(tmp_path), model,
                         "fake-model")
    assert (r.factor, r.valid, r.attempts, model._calls) == (0.8, True, 2, 2)
    assert len(list(tmp_path.glob("*.json"))) == 2      # attempt 1 and attempt 2 cached


def test_adk_clipped_and_fallback(tmp_path):
    clipped = la.evaluate_call("seller", "sys", "p1", BAND, la.DiskCache(tmp_path),
                               fake_model([ok(9.0), ok(9.0)]), "fake-model")
    assert (clipped.factor, clipped.valid, clipped.clipped) == (2.0428, False, True)
    fallback = la.evaluate_call("seller", "sys", "p2", BAND, la.DiskCache(tmp_path),
                                fake_model([ok(9.0), "nope"]), "fake-model")
    assert (fallback.factor, fallback.valid, fallback.fallback) == (1.0, False, True)


def test_adk_external_error_is_not_an_invalid_answer(tmp_path):
    with pytest.raises(la.LlmCallError):
        la.evaluate_call("seller", "sys", "p", BAND, la.DiskCache(tmp_path),
                         fake_model([], fail=True), "fake-model")
    assert list(tmp_path.glob("*.json")) == []          # nothing cached on failure


@pytest.mark.parametrize("answers, expected_factor", [
    (["", ""], 1.0),                        # empty twice -> fallback
    ([SAFETY_BLOCK, SAFETY_BLOCK], 1.0),    # blocked twice -> fallback
    ([SAFETY_BLOCK, ok(1.4)], 1.4),         # blocked, then a valid retry
])
def test_adk_empty_or_blocked_is_invalid_not_external(tmp_path, answers, expected_factor):
    # Model behaviour, not an external failure: no LlmCallError, retried, counted
    r = la.evaluate_call("seller", "sys", "p", BAND, la.DiskCache(tmp_path),
                         fake_model(answers), "fake-model")
    assert r.attempts == 2 and r.factor == expected_factor
    assert r.valid is (expected_factor != 1.0)


def test_adk_cache_holds_only_whitelisted_fields(tmp_path):
    la.evaluate_call("seller", "sys", "prompt C", BAND, la.DiskCache(tmp_path),
                     fake_model([ok(1.3)]), "fake-model")
    stored = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert set(stored) == set(la.CACHE_FIELDS)
    assert stored["prompt_text"] == "prompt C" and stored["attempt"] == 1
    la.DiskCache(tmp_path).scan_for_secrets()
