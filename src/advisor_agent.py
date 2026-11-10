"""Advisor tool agent (docs/agent_criteria.md, DRAFT): one ADK LlmAgent that answers a question
about one hypothetical permanent transfer by calling deterministic tools, then returns a
structured JSON answer.

The tools compute every number; the LLM only picks the tool, fills its arguments from the
question and writes the answer. The pure parts (tools, output parsing, cache) do not import
ADK. No function here reads, prints or logs an API key or any environment variable; the
ADK/GenAI client reads its own credentials.

ADK note (read from google-adk 2.12.0): when a model cannot pair an output schema with tools
(any Gemini model outside Vertex AI, and any fake model), ADK adds a `set_model_response`
tool and turns its validated arguments into the final text with json.dumps.
"""
import asyncio
import hashlib
import json
import math
import os
import tempfile
import tomllib
from pathlib import Path
from typing import Literal, Optional

from advisor import IncomingPlayer, advise_deal, eligible_clubs, to_dict
from llm_agents import GENERATION, MODEL_ID, SECRET_PATTERNS, LlmCallError, generate_content_config

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = REPO_ROOT / "prompts" / "agent_prompt.toml"
QUESTIONS_PATH = REPO_ROOT / "prompts" / "agent_questions.toml"
OUTCOMES = ("answered", "out_of_scope", "tool_error")
OUT_OF_SCOPE_TYPES = ("unknown_club", "ineligible_club", "real_world_conclusion", "loan",
                      "other_season")
EXPECTED_KEYS = {"club_id", "fee", "fee_currency", "annual_wage_gbp_m", "contract_years"}
MAX_MODEL_CALLS = 6   # [PROPOSED] in docs/agent_criteria.md

# Quotable fields of the advise_deal tool result: name -> (path in advisor.to_dict, decimals)
QUOTABLE = {
    "fee_gbp_m": (("fee_gbp_m",), 2),
    "annual_amortisation_gbp_m": (("incoming_cost", "annual_amortisation_gbp_m"), 2),
    "before_adjusted_revenue_gbp_m": (("before", "adjusted_revenue_gbp_m"), 2),
    "after_adjusted_revenue_gbp_m": (("after", "adjusted_revenue_gbp_m"), 2),
    "before_squad_cost_gbp_m": (("before", "squad_cost_gbp_m"), 2),
    "after_squad_cost_gbp_m": (("after", "squad_cost_gbp_m"), 2),
    "before_ratio": (("before", "ratio"), 4),
    "after_ratio": (("after", "ratio"), 4),
    "before_headroom_gbp_m": (("before", "headroom_gbp_m"), 2),
    "after_headroom_gbp_m": (("after", "headroom_gbp_m"), 2),
    "headroom_change_gbp_m": (("headroom_change_gbp_m",), 2),
    "max_wage_green_gbp_m": (("max_wage_green", "annual_gbp_m"), 2),
    "max_wage_red_gbp_m": (("max_wage_red", "annual_gbp_m"), 2),
}

# Only these fields are ever written to the cache; anything else is refused.
CACHE_FIELDS = ("key", "model_id", "generation", "question_id", "run_index", "call_number",
                "request", "response")


class ModelCallLimitReached(RuntimeError):
    """The run asked the model more than MAX_MODEL_CALLS times (counts as not parsed)."""


def load_system_prompt(path=PROMPT_PATH):
    with open(path, "rb") as f:
        return tomllib.load(f)["system"]


def prompt_hash(text):
    """SHA-256 of the system prompt exactly as parsed (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path):
    """SHA-256 of a file after normalising line endings to LF."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _check_question(q, where):
    if q.get("kind") == "in_scope":
        expected = q.get("expected", {})
        missing = EXPECTED_KEYS - set(expected)
        extra = set(expected) - EXPECTED_KEYS - {"agent_fee_gbp_m"}
        if missing or extra:
            raise ValueError(f"{where} {q.get('id')}: expected keys missing {sorted(missing)} "
                             f"or unknown {sorted(extra)}")
        if expected["fee_currency"] not in ("GBP", "EUR"):
            raise ValueError(f"{where} {q['id']}: bad fee_currency")
    elif q.get("kind") == "out_of_scope":
        if q.get("type") not in OUT_OF_SCOPE_TYPES or "expected" in q:
            raise ValueError(f"{where} {q.get('id')}: out-of-scope needs a known type, no expected")
    else:
        raise ValueError(f"{where} {q.get('id')}: unknown kind {q.get('kind')!r}")
    if not isinstance(q.get("text"), str) or not q["text"].strip():
        raise ValueError(f"{where} {q.get('id')}: empty text")


def load_questions(path=QUESTIONS_PATH):
    """{"question": [...], "pilot": [...]} from the question file, after structural checks:
    unique ids, known kinds and types, the expected keys of in-scope questions, and no pilot
    question that repeats a registered id or text."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    questions, pilot = data.get("question", []), data.get("pilot", [])
    for where, items in (("question", questions), ("pilot", pilot)):
        for q in items:
            _check_question(q, where)
    ids = [q["id"] for q in questions + pilot]
    if len(ids) != len(set(ids)):
        raise ValueError("Question ids must be unique across the set and the pilot")
    if {q["text"] for q in pilot} & {q["text"] for q in questions}:
        raise ValueError("A pilot question repeats a registered question")
    return {"question": questions, "pilot": pilot}


# ---------- Tools (pure, deterministic, never raise into the agent) ----------

def tool_list_buyer_clubs(conn, rules_config):
    """[{club_id, club_name}] of the clubs that can be buyers in the configured season."""
    year = rules_config["data_vintage"]["finance_fiscal_year"]
    return [{"club_id": cid, "club_name": name} for cid, name, _ in eligible_clubs(conn, year)]


def _whole_number(value):
    """An int from an int or an integral float (models may send 1148.0); else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def tool_advise_deal(conn, rules_config, advisor_config, club_id, fee, fee_currency,
                     annual_wage_gbp_m, contract_years, agent_fee_gbp_m=None):
    """advisor.advise_deal for a permanent deal without an outgoing player, as a flat dict of
    rounded quotable fields; {"error": message} on bad input instead of an exception."""
    cid = _whole_number(club_id)
    if cid is None:
        return {"error": f"club_id must be a whole number, got {club_id!r}"}
    if fee_currency not in ("GBP", "EUR"):
        return {"error": f"fee_currency must be GBP or EUR, got {fee_currency!r}"}
    try:
        incoming = IncomingPlayer(fee_amount=fee, fee_currency=fee_currency,
                                  annual_wage_gbp_m=annual_wage_gbp_m,
                                  contract_years=contract_years, agent_fee_gbp_m=agent_fee_gbp_m)
        result = to_dict(advise_deal(conn, rules_config, cid, incoming,
                                     advisor_config=advisor_config))
    except (ValueError, LookupError, TypeError, NotImplementedError) as error:
        return {"error": str(error)}
    out = {"club_id": result["club_id"], "club_name": result["club_name"],
           "before_status": result["before"]["status"], "after_status": result["after"]["status"]}
    for name, (path, decimals) in QUOTABLE.items():
        value = result
        for key in path:
            value = value[key]
        out[name] = None if value is None else round(value, decimals)
    out["warnings"] = sorted(result["warnings"])
    return out


# ---------- Output parsing ----------

def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_output(text):
    """The answer dict if text is a JSON object with a valid outcome, an int-or-null club_id,
    a string answer and a list of {field: str, value: finite number}; otherwise None.
    Strict: no code fences or extra text."""
    try:
        data = json.loads(text.strip())
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(data, dict) or data.get("outcome") not in OUTCOMES:
        return None
    club_id = data.get("club_id")
    if club_id is not None and (isinstance(club_id, bool) or not isinstance(club_id, int)):
        return None
    if not isinstance(data.get("answer"), str) or not isinstance(data.get("quoted"), list):
        return None
    for item in data["quoted"]:
        if not (isinstance(item, dict) and isinstance(item.get("field"), str)
                and _is_number(item.get("value"))):
            return None
    return data


# ---------- Disk cache ----------

def _strip_ids(value):
    """Drop the random function-call ids ADK adds, so a replay produces the same cache key."""
    if isinstance(value, dict):
        return {k: _strip_ids(v) for k, v in value.items() if k != "id"}
    if isinstance(value, list):
        return [_strip_ids(v) for v in value]
    return value


def request_snapshot(llm_request):
    """The model request as JSON-safe data: config (system text, tool declarations) and
    contents so far (question, earlier tool calls and tool results), without call ids."""
    config = llm_request.config.model_dump(mode="json", exclude_none=True)
    contents = [c.model_dump(mode="json", exclude_none=True) for c in llm_request.contents]
    return _strip_ids({"config": config, "contents": contents})


def cache_key(model_id, question_id, run_index, request):
    payload = json.dumps({"model_id": model_id, "generation": GENERATION,
                          "question_id": question_id, "run_index": run_index,
                          "request": request}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def check_no_secrets(entry):
    """Raise if an entry has fields outside CACHE_FIELDS or text that looks like a secret."""
    extra = set(entry) - set(CACHE_FIELDS)
    if extra:
        raise ValueError(f"Cache entry has non-whitelisted fields: {sorted(extra)}")
    blob = json.dumps(entry)
    for pattern in SECRET_PATTERNS:
        if pattern.search(blob):
            raise ValueError("Cache entry contains text that looks like a secret or header")


class AgentCache:
    """One JSON file per key. Entries are written once and never edited or deleted."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def get(self, key):
        path = self.directory / f"{key}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def put(self, entry):
        check_no_secrets(entry)
        path = self.directory / f"{entry['key']}.json"
        if path.exists():
            return False   # never overwrite
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(entry, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return True


# ---------- ADK agent ----------

def _output_schema():
    from pydantic import BaseModel

    class Quoted(BaseModel):
        field: str
        value: float

    class AgentOutput(BaseModel):
        outcome: Literal["answered", "out_of_scope", "tool_error"]
        club_id: Optional[int]
        answer: str
        quoted: list[Quoted]

    return AgentOutput


def build_agent(conn, rules_config, advisor_config, system_text, model, model_id, cache,
                question_id, run_index, counter, max_model_calls=MAX_MODEL_CALLS):
    """An LlmAgent with the two tools. Callbacks count model calls (stopping the run past the
    limit) and serve or fill the disk cache; a cache hit makes ADK skip the model call."""
    from google.adk.agents import LlmAgent
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    def list_buyer_clubs() -> list:
        """List the clubs that can be buyers in 2026/27, as club_id and club_name."""
        return tool_list_buyer_clubs(conn, rules_config)

    def advise_deal_tool(club_id: int, fee: float, fee_currency: str, annual_wage_gbp_m: float,
                         contract_years: float, agent_fee_gbp_m: Optional[float] = None) -> dict:
        """SCR before and after one permanent transfer for the buyer club.

        Args:
          club_id: buyer club_id from list_buyer_clubs.
          fee: transfer fee in millions, in fee_currency.
          fee_currency: "GBP" or "EUR".
          annual_wage_gbp_m: the incoming player's annual wage in GBP millions.
          contract_years: contract length in years.
          agent_fee_gbp_m: agent fee in GBP millions, only if the question states one.
        """
        return tool_advise_deal(conn, rules_config, advisor_config, club_id, fee, fee_currency,
                                annual_wage_gbp_m, contract_years, agent_fee_gbp_m)

    advise_deal_tool.__name__ = "advise_deal"

    def before_model(callback_context, llm_request):
        counter["model_calls"] += 1
        if counter["model_calls"] > max_model_calls:
            raise ModelCallLimitReached(f"more than {max_model_calls} model calls")
        request = request_snapshot(llm_request)
        key = cache_key(model_id, question_id, run_index, request)
        counter["pending"] = (key, request)
        cached = cache.get(key)
        if cached is None:
            return None
        counter["pending"] = None
        return LlmResponse(content=types.Content.model_validate(cached["response"]))

    def after_model(callback_context, llm_response):
        pending = counter.get("pending")
        if pending and llm_response.content is not None:
            key, request = pending
            cache.put({"key": key, "model_id": model_id, "generation": GENERATION,
                       "question_id": question_id, "run_index": run_index,
                       "call_number": counter["model_calls"], "request": request,
                       "response": llm_response.content.model_dump(mode="json",
                                                                   exclude_none=True)})
        counter["pending"] = None
        return None

    return LlmAgent(name="advisor_agent", model=model, instruction=system_text,
                    generate_content_config=generate_content_config(),
                    tools=[list_buyer_clubs, advise_deal_tool], output_schema=_output_schema(),
                    before_model_callback=before_model, after_model_callback=after_model,
                    disallow_transfer_to_parent=True, disallow_transfer_to_peers=True)


async def _run(agent, question_text, record):
    """Run the agent; tool calls, tool results and the final text go into `record` as they
    arrive, so a run stopped part-way keeps what happened before the stop."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    runner = Runner(app_name="transfer_window_ai_agent", agent=agent,
                    session_service=InMemorySessionService(), auto_create_session=True)
    async for event in runner.run_async(user_id="local", session_id="question",
                                        new_message=types.Content(
                                            role="user", parts=[types.Part(text=question_text)])):
        for call in event.get_function_calls():
            record["transcript"].append({"type": "call", "name": call.name,
                                         "args": dict(call.args or {})})
        for response in event.get_function_responses():
            record["transcript"].append({"type": "result", "name": response.name,
                                         "result": response.response})
        if event.is_final_response() and event.content and event.content.parts:
            record["final_text"] = "".join(p.text for p in event.content.parts
                                           if p.text and not p.thought)


def run_question(question_id, question_text, run_index, *, conn, rules_config, advisor_config,
                 system_text, cache, model, model_id=MODEL_ID, max_model_calls=MAX_MODEL_CALLS):
    """One run of one question: the tool transcript, the final text and its parse.

    model_calls counts every model request, including a refused one past the limit. A run past
    the limit returns stopped="model_call_limit" and output None. External failures (quota,
    network, API errors) raise LlmCallError."""
    counter = {"model_calls": 0, "pending": None}
    record = {"transcript": [], "final_text": None}
    agent = build_agent(conn, rules_config, advisor_config, system_text, model, model_id, cache,
                        question_id, run_index, counter, max_model_calls)
    stopped = None
    try:
        asyncio.run(_run(agent, question_text, record))
    except ModelCallLimitReached:
        stopped = "model_call_limit"
    except Exception as error:   # quota, network or API error: not an invalid answer
        raise LlmCallError(f"agent run failed: {type(error).__name__}") from error
    final_text = record["final_text"]
    output = None if stopped or final_text is None else parse_output(final_text)
    return {"question_id": question_id, "run_index": run_index,
            "model_calls": counter["model_calls"], "stopped": stopped,
            "transcript": record["transcript"], "final_text": final_text, "output": output}
