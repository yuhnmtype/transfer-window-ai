"""LLM valuation agents (docs/llm_criteria.md): templates, parsing, validity, disk cache, and
ADK LlmAgent seller/buyer agents run through ADK's Runner.

The LLM only returns a factor; code validates it against the registered band, retries once,
clips or falls back exactly as the criteria define, and never lets the LLM compute numbers.
The pure parts (templates, rounding, parsing, cache) do not import ADK, so they run and are
tested in any environment; ADK is imported only inside the functions that build and run
agents. No function here reads, prints or logs an API key or any environment variable; the
ADK/GenAI client reads its own credentials.
"""
import asyncio
import hashlib
import json
import math
import os
import re
import tempfile
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_PATH = REPO_ROOT / "prompts" / "llm_templates.toml"
TEMPLATE_NAMES = ("system", "seller_blind", "buyer_blind", "seller_named", "buyer_named")
ROLES = ("seller", "buyer")

MODEL_ID = "gemini-3.5-flash-lite"
# Registered generation settings (docs/llm_criteria.md, "Generation settings").
GENERATION = {"temperature": 0.0, "seed": 20261015, "candidate_count": 1,
              "thinking_level": "MINIMAL", "include_thoughts": False,
              "response_schema": "FactorOutput{factor: number, rationale: string}"}
BAND_DECIMALS = 4

# Only these fields are ever written to the cache; anything else is refused.
CACHE_FIELDS = ("key", "model_id", "role", "attempt", "generation", "system_text",
                "prompt_text", "response_text")
# Patterns that must never appear in a cache entry (API keys, auth headers).
SECRET_PATTERNS = (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), re.compile(r"(?i)x-goog-api-key"),
                   re.compile(r"(?i)authorization\s*:"), re.compile(r"(?i)bearer\s+[a-z0-9]"),
                   re.compile(r"(?i)api[_-]?key"))


class LlmCallError(RuntimeError):
    """An external failure (quota, network, API error): the run is incomplete, not invalid."""


# ---------- Templates and prompt features ----------

def load_templates(path=TEMPLATES_PATH):
    with open(path, "rb") as f:
        templates = tomllib.load(f)
    missing = set(TEMPLATE_NAMES) - set(templates)
    if missing:
        raise ValueError(f"Missing templates: {sorted(missing)}")
    return {name: templates[name] for name in TEMPLATE_NAMES}


def template_hash(text):
    """SHA-256 of a template string exactly as parsed (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def round_half_up(x):
    return math.floor(x + 0.5)


def prompt_age(age_years):
    """Age rounded to the nearest integer, half up."""
    return round_half_up(age_years)


def prompt_mv_m(mv_eur):
    """Market value rounded half up to the nearest 1m EUR, with a minimum of 1m."""
    return max(1, round_half_up(mv_eur / 1_000_000))


def band_from_q(q):
    """Registered band [exp(-q), exp(q)], rounded to 4 decimals as shown in the prompt; the
    same rounded values are used for validation and clipping."""
    return round(math.exp(-q), BAND_DECIMALS), round(math.exp(q), BAND_DECIMALS)


def window_label(window):
    """Only "summer" or "winter", never a year."""
    if window.startswith("W"):
        return "winter"
    if window.startswith("S"):
        return "summer"
    raise ValueError(f"Unknown window code: {window!r}")


def render_prompt(template, row, m, band, names=None):
    """Fill a template from one dataset row. Only the registered features are used; the
    reference value is m x the rounded market value. names adds player and clubs (named arm)."""
    mv_m = prompt_mv_m(row["mv_eur"])
    fields = {"position": row["position"], "age": prompt_age(row["age"]), "mv_m": mv_m,
              "window": window_label(row["window"]), "reference_m": f"{m * mv_m:.2f}",
              "m": m, "band_low": f"{band[0]:.4f}", "band_high": f"{band[1]:.4f}"}
    if names:
        fields.update(player_name=names["player_name"], seller_club=names["seller_club"],
                      buyer_club=names["buyer_club"])
    return template.format(**fields)


# ---------- Parsing and validity ----------

def parse_factor(text):
    """Return the factor if text is a JSON object with a finite numeric "factor" and a string
    "rationale"; otherwise None. Strict: no code fences or extra text are accepted."""
    try:
        data = json.loads(text.strip())
    except (json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("rationale"), str):
        return None
    factor = data.get("factor")
    if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not math.isfinite(factor):
        return None
    return float(factor)


def in_band(factor, band):
    return band[0] <= factor <= band[1]


@dataclass(frozen=True)
class CallResult:
    role: str
    factor: float          # the factor used for scoring
    valid: bool            # parsed and inside the band, on attempt 1 or the single retry
    attempts: int          # 1 or 2
    clipped: bool          # retry parsed but outside the band -> clipped, not valid
    fallback: bool         # retry did not parse -> factor 1, not valid
    rationale: str | None
    responses: tuple       # raw response texts, one per attempt


def decide(responses, band):
    """Apply the registered validity rules to one or two raw responses (pure).

    responses[0] is the first attempt; responses[1] (if present) is the single retry."""
    first = parse_factor(responses[0])
    if first is not None and in_band(first, band):
        return dict(factor=first, valid=True, attempts=1, clipped=False, fallback=False,
                    text=responses[0])
    if len(responses) < 2:
        raise ValueError("A retry is required when the first attempt is not valid")
    second = parse_factor(responses[1])
    if second is None:
        return dict(factor=1.0, valid=False, attempts=2, clipped=False, fallback=True,
                    text=responses[1])
    if in_band(second, band):
        return dict(factor=second, valid=True, attempts=2, clipped=False, fallback=False,
                    text=responses[1])
    return dict(factor=min(max(second, band[0]), band[1]), valid=False, attempts=2,
                clipped=True, fallback=False, text=responses[1])


def _rationale(text):
    try:
        value = json.loads(text.strip()).get("rationale")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def summarise(results):
    """Counts of valid, retried, clipped and fallback calls, and the valid share."""
    n = len(results)
    counts = {"calls": n, "valid": sum(r.valid for r in results),
              "retried": sum(r.attempts == 2 for r in results),
              "clipped": sum(r.clipped for r in results),
              "fallback": sum(r.fallback for r in results)}
    counts["valid_share"] = counts["valid"] / n if n else None
    return counts


def geometric_factor(f_seller, f_buyer):
    """Registered combination: sqrt(f_s * f_b)."""
    return math.sqrt(f_seller * f_buyer)


# ---------- Disk cache ----------

def cache_key(model_id, role, attempt, system_text, prompt_text, generation=GENERATION):
    """SHA-256 of model id, generation config, role, attempt and prompt text.

    The attempt number is part of the key so that the single retry is a real new call and
    not a cache hit on the first (invalid) answer."""
    payload = json.dumps({"model_id": model_id, "generation": generation, "role": role,
                          "attempt": attempt, "system_text": system_text,
                          "prompt_text": prompt_text}, sort_keys=True, separators=(",", ":"))
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


class DiskCache:
    """One JSON file per key. Entries are written once and never edited or deleted."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def _path(self, key):
        return self.directory / f"{key}.json"

    def get(self, key):
        path = self._path(key)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def put(self, entry):
        check_no_secrets(entry)
        path = self._path(entry["key"])
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

    def scan_for_secrets(self):
        """Check every stored entry (used by tests and before committing the cache)."""
        for path in sorted(self.directory.glob("*.json")):
            check_no_secrets(json.loads(path.read_text(encoding="utf-8")))


# ---------- ADK agents ----------

def _output_schema():
    from pydantic import BaseModel

    class FactorOutput(BaseModel):
        factor: float
        rationale: str

    return FactorOutput


def generate_content_config():
    """The registered generation settings as a GenerateContentConfig."""
    from google.genai import types
    return types.GenerateContentConfig(
        temperature=GENERATION["temperature"], seed=GENERATION["seed"],
        candidate_count=GENERATION["candidate_count"],
        thinking_config=types.ThinkingConfig(
            thinking_level=types.ThinkingLevel[GENERATION["thinking_level"]],
            include_thoughts=GENERATION["include_thoughts"]))


def build_agent(role, system_text, prompt_text, attempt, key, cache, model, model_id):
    """An ADK LlmAgent whose callbacks serve and fill the disk cache for one call.

    before_model_callback returns the cached response (ADK then skips the model call);
    after_model_callback stores a fresh response. No output_key is set, so ADK does not
    validate the JSON itself; parse_factor applies the registered rules."""
    from google.adk.agents import LlmAgent
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    def before_model(callback_context, llm_request):
        cached = cache.get(key)
        if cached is None:
            return None
        return LlmResponse(content=types.Content(
            role="model", parts=[types.Part(text=cached["response_text"])]))

    def after_model(callback_context, llm_response):
        if cache.get(key) is None:
            text = "".join(p.text for p in (llm_response.content.parts if llm_response.content
                                            else []) if p.text and not p.thought)
            cache.put({"key": key, "model_id": model_id, "role": role, "attempt": attempt,
                       "generation": GENERATION, "system_text": system_text,
                       "prompt_text": prompt_text, "response_text": text})
        return None

    return LlmAgent(name=f"{role}_valuation", model=model, instruction=system_text,
                    generate_content_config=generate_content_config(),
                    output_schema=_output_schema(), before_model_callback=before_model,
                    after_model_callback=after_model, disallow_transfer_to_parent=True,
                    disallow_transfer_to_peers=True, include_contents="none")


async def _run_agent(agent, prompt_text):
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    runner = Runner(app_name="transfer_window_ai", agent=agent,
                    session_service=InMemorySessionService(), auto_create_session=True)
    text = ""
    async for event in runner.run_async(user_id="local", session_id="valuation",
                                        new_message=types.Content(
                                            role="user", parts=[types.Part(text=prompt_text)])):
        if event.is_final_response() and event.content and event.content.parts:
            text = "".join(p.text for p in event.content.parts if p.text and not p.thought)
    return text


def call_once(role, system_text, prompt_text, attempt, cache, model, model_id=MODEL_ID):
    """One agent call (through the cache). External failures raise LlmCallError."""
    key = cache_key(model_id, role, attempt, system_text, prompt_text)
    agent = build_agent(role, system_text, prompt_text, attempt, key, cache, model, model_id)
    try:
        return asyncio.run(_run_agent(agent, prompt_text))
    except Exception as error:   # quota, network or API error: not an invalid answer
        raise LlmCallError(f"{role} call failed: {type(error).__name__}") from error


def evaluate_call(role, system_text, prompt_text, band, cache, model, model_id=MODEL_ID):
    """First attempt, a single retry when needed, then the registered decision."""
    responses = [call_once(role, system_text, prompt_text, 1, cache, model, model_id)]
    first = parse_factor(responses[0])
    if first is None or not in_band(first, band):
        responses.append(call_once(role, system_text, prompt_text, 2, cache, model, model_id))
    d = decide(responses, band)
    return CallResult(role=role, factor=d["factor"], valid=d["valid"], attempts=d["attempts"],
                      clipped=d["clipped"], fallback=d["fallback"],
                      rationale=_rationale(d["text"]), responses=tuple(responses))


def result_dict(result):
    return asdict(result)
