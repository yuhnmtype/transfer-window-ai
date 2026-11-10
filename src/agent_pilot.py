"""Pilot of the advisor tool agent on the [[pilot]] questions (docs/agent_criteria.md, "Pilot").

Checks the output format and the tool loop with the real model before the criteria are
frozen. Each pilot question is run once. Pilot outputs are not scored. The pilot has its own
cache directory, writes results/agent_pilot_run1.json once (never overwritten), and writes
nothing if a call fails for an external reason (the cache keeps the completed calls, so a
rerun resumes). It opens the database read-only and never reads or prints an API key; the
ADK/GenAI client reads its own credentials.

Guards (checked before any model call, no option to skip): a clean working tree, so the
recorded code commit describes the code that ran (the only exception is untracked
hex-named cache entries directly in the pilot cache directory, from an earlier interrupted
run); the system prompt and the question file present; the database present; no existing
results file.

Run in .venv-adk:  .\\.venv-adk\\Scripts\\python.exe src\\agent_pilot.py
"""
import hashlib
import importlib.metadata
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import advisor_agent as aa
from advise_cli import open_read_only
from advisor import load_advisor_config
from llm_agents import GENERATION, MODEL_ID, LlmCallError
from phase3_eval_wage_model import write_atomic
from scr import load_config

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_REL = "transfer_window_ai.db"
RESULTS_REL = "results/agent_pilot_run1.json"
CACHE_REL = "results/agent_cache/pilot"
PROMPT_REL = "prompts/agent_prompt.toml"
QUESTIONS_REL = "prompts/agent_questions.toml"
DB_PATH = REPO_ROOT / DB_REL
PILOT_RESULTS = REPO_ROOT / RESULTS_REL
PILOT_CACHE = REPO_ROOT / CACHE_REL
HEX_CACHE_FILE = re.compile(r"^[0-9a-f]{64}\.json$")
SEASON = "2026/27"
RUN_INDEX = 1
# Operational, not a criterion: a pause between questions keeps the request rate low
# (at most 6 model calls per question, so at most 12 per minute with a 30 s pause).
PAUSE_SECONDS = 30


def _git(repo_root, *args):
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)


def guard_errors(repo_root):
    """Every reason to refuse the pilot (an empty list means it may run)."""
    repo_root = Path(repo_root)
    status = _git(repo_root, "status", "--porcelain", "--untracked-files=all")
    if status.returncode != 0:
        return [f"git is not available or this is not a repository: {status.stderr.strip()}"]
    errors, bad = [], []
    for line in status.stdout.splitlines():
        code, path = line[:2], line[3:].strip().strip('"')
        folder, _, name = path.rpartition("/")
        if code == "??" and folder == CACHE_REL and HEX_CACHE_FILE.match(name):
            continue
        bad.append(line)
    if bad:
        errors.append(f"Working tree is not clean (commit first): {', '.join(bad)}")
    for rel in (PROMPT_REL, QUESTIONS_REL, DB_REL):
        if not (repo_root / rel).exists():
            errors.append(f"Missing file: {rel}")
    if (repo_root / RESULTS_REL).exists():
        errors.append(f"Results file already exists: {RESULTS_REL} (never overwritten)")
    return errors


def run_pilot(questions, conn, rules_config, advisor_config, system_text, cache, model,
              model_id=MODEL_ID, pause=0.0, sleep=time.sleep):
    """Run every pilot question once and return the record (no scoring)."""
    runs = []
    for i, q in enumerate(questions):
        if i and pause:
            sleep(pause)
        r = aa.run_question(q["id"], q["text"], RUN_INDEX, conn=conn, rules_config=rules_config,
                            advisor_config=advisor_config, system_text=system_text, cache=cache,
                            model=model, model_id=model_id)
        runs.append({"id": q["id"], "kind": q["kind"], "type": q.get("type"),
                     "expected": q.get("expected"), "text": q["text"], **r})
    counts = {"runs": len(runs),
              "parsed": sum(r["output"] is not None for r in runs),
              "stopped": sum(r["stopped"] is not None for r in runs),
              "model_calls": sum(r["model_calls"] for r in runs)}
    return {"runs": runs, "counts": counts}


def db_provenance(conn, path=DB_PATH):
    return {"md5_db_file": hashlib.md5(Path(path).read_bytes()).hexdigest(),
            "db_meta": dict(conn.execute("SELECT key, value FROM meta").fetchall())}


def main():
    errors = guard_errors(REPO_ROOT)
    if errors:
        print("PILOT REFUSED:")
        for e in errors:
            print(" -", e)
        return 1
    questions = aa.load_questions()["pilot"]
    system_text = aa.load_system_prompt()
    conn = open_read_only(DB_PATH)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        record = run_pilot(questions, conn, load_config(SEASON), load_advisor_config(),
                           system_text, aa.AgentCache(PILOT_CACHE), MODEL_ID,
                           pause=PAUSE_SECONDS)
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
        "system_prompt_sha256": aa.prompt_hash(system_text),
        "questions_file_sha256": aa.file_sha256(aa.QUESTIONS_PATH),
        "max_model_calls": aa.MAX_MODEL_CALLS, "run_index": RUN_INDEX,
        "cache_dir": str(PILOT_CACHE.relative_to(REPO_ROOT)),
        **db_provenance(conn), "code_commit": commit, "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    write_atomic(PILOT_RESULTS, record)
    c = record["counts"]
    print(f"Pilot complete: {c['runs']} runs, parsed {c['parsed']}, stopped {c['stopped']}, "
          f"model calls {c['model_calls']}. Written: {PILOT_RESULTS.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
