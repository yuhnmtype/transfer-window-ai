"""Registered evaluation of the LLM valuation layer (docs/llm_criteria.md, tag llm-criteria-v1).

--arm blind: the 206 test rows (transfer date, then player_id) through the blind seller and
buyer agents (412 primary calls). --arm named: the 50 registered positions through the named
templates (100 primary calls, diagnostic only), after the blind arm is complete.

Guards run before any model call and cannot be skipped. Throttling, the request budget and
backoff are operational controls, not criteria; they do not change the cache key. Results
files are written only for a complete arm, atomically, and are never overwritten. The
console shows a short summary only. Nothing here reads, prints or logs an environment
variable; the ADK/GenAI client reads its own credentials.

External errors are classified by the HTTP status found on the exception chain: 429, 5xx and
errors without a status (network, timeout) are retried with backoff; 400/401/403 and other
4xx fail fast as incomplete; 404 means the registered model is unavailable (NOT RUN).

Exit codes: 0 complete; 1 refused by a guard; 2 incomplete (external error); 3 request budget
reached (resume after the daily reset); 4 NOT RUN (model unavailable).
"""
import argparse
import hashlib
import importlib.metadata
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import llm_agents as la
from fee_model import ape, build_dataset, mape, mdape, paired_bootstrap
from llm_pilot import DB_PATH, fee_parameters   # same DB and fee-results file as the pilot
from phase3_eval_wage_model import md5_lf, write_atomic

REPO_ROOT = Path(__file__).resolve().parent.parent
CRITERIA_TAG = "llm-criteria-v1"
CRITERIA_REL = "docs/llm_criteria.md"
TEMPLATES_REL = "prompts/llm_templates.toml"
SCRIPT_REL = "src/phase3_eval_llm.py"
EVAL_CACHE_REL = "results/llm_cache/eval"
RESULTS_REL = {"blind": "results/llm_eval_blind_run1.json",
               "named": "results/llm_eval_named_run1.json"}
REGISTERED_MODEL_ID = "gemini-3.5-flash-lite"
EXPECTED_SIZES = {"train": 1154, "calibration": 185, "test": 206}

# Registered constants (docs/llm_criteria.md)
BOOTSTRAP_SEED, BOOTSTRAP_N = 20261015, 2000
PLACEBO_SEED, PLACEBO_N = 20261016, 1000
NAMED_SEED, NAMED_N = 20261015, 50
RATIO_THRESHOLD, VALIDITY_THRESHOLD = 0.90, 0.95

# Operational controls (not criteria; they do not change the cache key)
DEFAULT_RPM = 10
DEFAULT_MAX_REQUESTS = 450
BACKOFF = {"max_attempts": 4, "delays_seconds": (30, 60, 120)}   # 1 try + 3 backoff retries

EXIT_OK, EXIT_REFUSED, EXIT_INCOMPLETE, EXIT_BUDGET, EXIT_NOT_RUN = 0, 1, 2, 3, 4
HEX_CACHE_FILE = re.compile(r"^[0-9a-f]{64}\.json$")
NOT_RUN_MESSAGE = (f"NOT RUN: model {REGISTERED_MODEL_ID} is unavailable "
                   "(docs/llm_criteria.md, Model)")


class BudgetReached(RuntimeError):
    """The live-request budget for this invocation is used up."""


class IncompleteRun(RuntimeError):
    """An external error that cannot be retried, or backoff attempts were exhausted."""


class NotRun(RuntimeError):
    """The registered model is unavailable (HTTP 404)."""


def http_status(error):
    """The first HTTP status code (100-599) found on the exception's cause chain, else None."""
    current, depth = error.__cause__, 0
    while current is not None and depth < 10:
        code = getattr(current, "code", None)
        if isinstance(code, int) and not isinstance(code, bool) and 100 <= code <= 599:
            return code
        current, depth = current.__cause__ or current.__context__, depth + 1
    return None


def is_retryable(status):
    """Backoff only on 429, 5xx, or no status (network, timeout)."""
    return status is None or status == 429 or 500 <= status <= 599


PROGRESS_EVERY = 20   # completed calls between progress lines


def stderr_log(line):
    """Operational progress on stderr: counts and HTTP status codes only, never prompts,
    responses, factors, fees, rationales, cache keys or environment variables."""
    print(f"[llm-eval] {line}", file=sys.stderr, flush=True)


# ---------- Guards ----------

def _git(repo_root, *args):
    return subprocess.run(["git", *args], cwd=repo_root, capture_output=True, text=True)


def dirty_tree_errors(repo_root):
    """Clean tree, except untracked hex-named JSON files directly in the eval cache dir."""
    status = _git(repo_root, "status", "--porcelain", "--untracked-files=all")
    if status.returncode != 0:
        return [f"git is not available or this is not a repository: {status.stderr.strip()}"]
    bad = []
    for line in status.stdout.splitlines():
        code, path = line[:2], line[3:].strip().strip('"')
        folder, _, name = path.rpartition("/")
        if code == "??" and folder == EVAL_CACHE_REL and HEX_CACHE_FILE.match(name):
            continue
        bad.append(line)
    return [f"Working tree is not clean: {', '.join(bad)}"] if bad else []


def guard_errors(repo_root, sizes, model_id, target, tag=CRITERIA_TAG):
    """Every reason to refuse, in the registered order. sizes is a callable returning the split
    sizes, so the database is only read when that guard is reached."""
    errors = list(dirty_tree_errors(repo_root))
    if errors and errors[0].startswith("git is not available"):
        return errors
    tag_ok = _git(repo_root, "rev-parse", "--verify", f"{tag}^{{commit}}").returncode == 0
    if not tag_ok:
        errors.append(f"Tag {tag} not found")
    else:
        for rel in (CRITERIA_REL, TEMPLATES_REL):
            if _git(repo_root, "diff", "--quiet", tag, "--", rel).returncode != 0:
                errors.append(f"{rel} changed since tag {tag}")
        if _git(repo_root, "cat-file", "-e", f"{tag}:{SCRIPT_REL}").returncode == 0:
            errors.append(f"{SCRIPT_REL} already existed at the tag commit")
    actual = sizes()
    if actual != EXPECTED_SIZES:
        errors.append(f"Split sizes {actual} differ from {EXPECTED_SIZES}")
    if model_id != REGISTERED_MODEL_ID:
        errors.append(f"Model id {model_id!r} differs from {REGISTERED_MODEL_ID!r}")
    if Path(target).exists():
        errors.append(f"Results file already exists: {target} (never overwritten)")
    return errors


# ---------- Operational controls ----------

class Throttle:
    """Spaces live requests by at least 60 / rpm seconds."""

    def __init__(self, rpm, clock=time.monotonic, sleep=time.sleep):
        if not rpm > 0:
            raise ValueError("rpm must be > 0")
        self.interval, self.clock, self.sleep, self.last = 60.0 / rpm, clock, sleep, None

    def wait(self):
        if self.last is not None:
            gap = self.clock() - self.last
            if gap < self.interval:
                self.sleep(self.interval - gap)
        self.last = self.clock()


class Controller:
    """Wraps llm_agents.call_once: cache hits pass straight through (no throttle, no budget);
    live requests are throttled, counted against the budget and retried with backoff on
    external errors. External errors are never cached (call_once only caches responses)."""

    def __init__(self, throttle, max_requests, backoff=BACKOFF, sleep=time.sleep,
                 call_once=la.call_once, log=None):
        self.throttle, self.max_requests, self.backoff = throttle, max_requests, backoff
        self.sleep, self.call_once = sleep, call_once
        self.log = log or (lambda line: None)
        self.live_requests = self.cache_hits = 0

    def __call__(self, role, system_text, prompt_text, attempt, cache, model, model_id):
        key = la.cache_key(model_id, role, attempt, system_text, prompt_text)
        if cache.get(key) is not None:
            self.cache_hits += 1
            return self.call_once(role, system_text, prompt_text, attempt, cache, model, model_id)
        delays = self.backoff["delays_seconds"]
        for i in range(self.backoff["max_attempts"]):
            if self.live_requests >= self.max_requests:
                raise BudgetReached(f"budget of {self.max_requests} live requests reached")
            self.throttle.wait()
            self.live_requests += 1
            try:
                return self.call_once(role, system_text, prompt_text, attempt, cache, model,
                                      model_id)
            except la.LlmCallError as error:
                status = http_status(error)
                if status == 404:
                    raise NotRun(NOT_RUN_MESSAGE) from error
                if status in (400, 401, 403):
                    raise IncompleteRun(f"request rejected (HTTP {status}): check credentials "
                                        "and configuration") from error
                if not is_retryable(status):
                    raise IncompleteRun(f"request failed (HTTP {status}), not retried") from error
                if i == self.backoff["max_attempts"] - 1:
                    raise IncompleteRun(f"{error}; {self.backoff['max_attempts']} attempts "
                                        "used") from error
                delay = delays[min(i, len(delays) - 1)]
                self.log(f"backoff: attempt {i + 1} of {self.backoff['max_attempts']}, "
                         f"HTTP {status if status is not None else 'none'}, waiting {delay}s")
                self.sleep(delay)
        raise AssertionError("unreachable")


# ---------- Rows and calls ----------

def ordered_test_rows(data):
    """The 206 test rows in the registered order (as built: transfer date, then player_id)."""
    return data[data["split"] == "test"].reset_index(drop=True)


def named_positions(n_test=206):
    return sorted(np.random.default_rng(NAMED_SEED).choice(n_test, NAMED_N,
                                                          replace=False).tolist())


def run_calls(rows, kind, m, q, templates, cache, model, controller, model_id, names=None,
              progress=None):
    """Seller and buyer calls for every row. kind is "blind" or "named". progress is a dict
    whose "done" counts completed primary calls (for the budget message)."""
    band = la.band_from_q(q)
    out = []
    for i, row in rows.iterrows():
        pair = {}
        for role in la.ROLES:
            prompt = la.render_prompt(templates[f"{role}_{kind}"], row, m, band,
                                      names[i] if names is not None else None)
            pair[role] = la.evaluate_call(role, templates["system"], prompt, band, cache, model,
                                          model_id, call=controller)
            if progress is not None:
                progress["done"] += 1
                log = progress.get("log")
                if log and progress["done"] % PROGRESS_EVERY == 0:
                    elapsed = time.monotonic() - progress["start"]
                    log(f"progress: {progress['done']}/{progress['total']} calls, live "
                        f"{controller.live_requests}, cache hits {controller.cache_hits}, "
                        f"elapsed {elapsed:.0f}s")
        out.append(pair)
    return out


# ---------- Metrics ----------

def _counts(results):
    return {role: la.summarise([r[role] for r in results]) for role in la.ROLES}


def placebo(fee, mv, m, q, mdape_fallback, llm_ratio, n=PLACEBO_N, seed=PLACEBO_SEED):
    """n draws, ln f ~ U[-q, q] for each row and agent, same geometric mean (report only)."""
    rng = np.random.default_rng(seed)
    logs = rng.uniform(-q, q, size=(n, len(fee), 2))
    combined = np.exp(logs.mean(axis=2))                 # sqrt(f_s * f_b)
    ratios = np.array([mdape(ape(m * mv * c, fee)) for c in combined]) / mdape_fallback
    return {"n": n, "seed": seed, "percentile_of_llm_ratio": float(np.mean(ratios <= llm_ratio) * 100),
            "ratio_median": float(np.median(ratios)),
            "ratio_2_5_97_5": [float(np.percentile(ratios, 2.5)), float(np.percentile(ratios, 97.5))]}


def blind_metrics(rows, results, m, q):
    """Every registered quantity of the blind arm (pure)."""
    fee = rows["fee_eur"].to_numpy(dtype=float)
    mv = rows["mv_eur"].to_numpy(dtype=float)
    f_s = np.array([r["seller"].factor for r in results])
    f_b = np.array([r["buyer"].factor for r in results])
    fee_b = m * mv
    fee_llm = m * mv * np.sqrt(f_s * f_b)
    ape_b, ape_llm = ape(fee_b, fee), ape(fee_llm, fee)
    md_b, md_llm = mdape(ape_b), mdape(ape_llm)
    ratio = md_llm / md_b
    boot = paired_bootstrap(ape_llm, ape_b, n=BOOTSTRAP_N, seed=BOOTSTRAP_SEED)
    calls = [r[role] for r in results for role in la.ROLES]
    valid_share = sum(c.valid for c in calls) / len(calls)
    both_valid = sum(r["seller"].valid and r["buyer"].valid for r in results) / len(results)
    a_ratio, a_boot = ratio <= RATIO_THRESHOLD, boot["upper_97_5"] < 0
    a_pass, v_pass = a_ratio and a_boot, valid_share >= VALIDITY_THRESHOLD
    if a_pass and v_pass:
        verdict = "passed, leakage not excluded"
    elif not v_pass:
        verdict = "V failed: the run does not count as a pass" + (
            "" if a_pass else "; A failed: the LLM did not beat the fallback (informative)")
    else:
        verdict = "A failed: the LLM did not beat the fallback under these conditions (informative)"
    one_side = {name: mdape(ape(m * mv * f, fee)) / md_b for name, f in
                (("seller_only", f_s), ("buyer_only", f_b))}
    return {
        "n_rows": len(rows), "n_calls": len(calls),
        "fallback": {"mdape": md_b, "mape": mape(ape_b)},
        "llm": {"mdape": md_llm, "mape": mape(ape_llm)},
        "mdape_ratio_llm_to_fallback": ratio,
        "paired_bootstrap": boot,
        "criterion_a": {"passed": a_pass, "ratio_ok": a_ratio, "bootstrap_upper_below_zero": a_boot},
        "criterion_v": {"passed": v_pass, "valid_share": valid_share,
                        "rows_both_valid_share": both_valid, "threshold": VALIDITY_THRESHOLD},
        "interpretation": verdict,
        "placebo_report_only": placebo(fee, mv, m, q, md_b, ratio),
        "seller_only_and_buyer_only_report_only": {k + "_mdape_ratio": v for k, v in one_side.items()},
        "counts_per_agent": _counts(results),
        "rows": [{"position": int(i), "player_id": int(rows.loc[i, "player_id"]),
                  "transfer_date": str(rows.loc[i, "transfer_date"].date()),
                  "f_s": float(f_s[i]), "f_b": float(f_b[i]),
                  "seller_valid": results[i]["seller"].valid, "buyer_valid": results[i]["buyer"].valid,
                  "ape_fallback": float(ape_b[i]), "ape_llm": float(ape_llm[i])}
                 for i in range(len(rows))],
    }


def named_metrics(rows, results, m, blind_rows):
    """MdAPE of the named arm vs the blind arm on the same rows (report only)."""
    fee = rows["fee_eur"].to_numpy(dtype=float)
    mv = rows["mv_eur"].to_numpy(dtype=float)
    f_s = np.array([r["seller"].factor for r in results])
    f_b = np.array([r["buyer"].factor for r in results])
    ape_named = ape(m * mv * np.sqrt(f_s * f_b), fee)
    ape_blind = np.array([b["ape_llm"] for b in blind_rows])
    return {"n_rows": len(rows), "note": "Diagnostic only; never gates any criterion.",
            "named_mdape": mdape(ape_named), "blind_mdape_same_rows": mdape(ape_blind),
            "counts_per_agent": _counts(results)}


# ---------- Provenance and I/O ----------

def cache_hash(directory):
    """SHA-256 over the sorted cache file names and their LF-normalised MD5s."""
    digest = hashlib.sha256()
    for path in sorted(Path(directory).glob("*.json")):
        digest.update(f"{path.name}:{md5_lf(path)}\n".encode())
    return digest.hexdigest()


def provenance(repo_root, templates, fee_md5, controller):
    return {
        "model_id": la.MODEL_ID, "generation": la.GENERATION,
        "google_adk": importlib.metadata.version("google-adk"),
        "python": platform.python_version(),
        "template_sha256": {k: la.template_hash(v) for k, v in templates.items()},
        "cache_sha256": cache_hash(Path(repo_root) / EVAL_CACHE_REL),
        "code_commit": _git(repo_root, "rev-parse", "HEAD").stdout.strip(),
        "criteria_tag_commit": _git(repo_root, "rev-parse", f"{CRITERIA_TAG}^{{commit}}").stdout.strip(),
        "md5_fee_results": fee_md5,
        "seeds": {"bootstrap": BOOTSTRAP_SEED, "placebo": PLACEBO_SEED, "named_sample": NAMED_SEED,
                  "sdk": la.GENERATION["seed"]},
        "operational": {"rpm": round(60 / controller.throttle.interval, 6),
                        "max_requests": controller.max_requests, "backoff": BACKOFF,
                        "live_requests_this_invocation": controller.live_requests,
                        "cache_hits_this_invocation": controller.cache_hits},
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_new(target, data):
    """Atomic write; never overwrites (checked again right before writing)."""
    target = Path(target)
    if target.exists():
        raise FileExistsError(f"{target} already exists (never overwritten)")
    write_atomic(target, data)


def execute(arm, rows, m, q, templates, cache, model, controller, target, model_id,
            names=None, blind_rows=None, provenance_fn=None, log=None):
    """Run one arm and write its results only if complete. Returns (exit code, message).
    log (optional) receives operational progress lines; it never affects the results."""
    total = 2 * len(rows)
    progress = {"done": 0, "total": total, "log": log, "start": time.monotonic()}
    if log:
        log(f"start: arm={arm} rows={len(rows)} planned_calls={total} "
            f"rpm={60 / controller.throttle.interval:g} max_requests={controller.max_requests}")
    try:
        results = run_calls(rows, arm, m, q, templates, cache, model, controller, model_id,
                            names, progress)
    except BudgetReached as error:
        return EXIT_BUDGET, (f"STOPPED: {error}. {progress['done']} of {total} calls done; "
                             "resume after the daily reset. No results file written.")
    except IncompleteRun as error:
        return EXIT_INCOMPLETE, (f"INCOMPLETE: {error}. {progress['done']} of {total} calls "
                                 "done. No results file written.")
    except NotRun as error:
        return EXIT_NOT_RUN, f"{error}. No results file written."
    data = (blind_metrics(rows, results, m, q) if arm == "blind"
            else named_metrics(rows, results, m, blind_rows))
    data["arm"] = arm
    data["provenance"] = provenance_fn(controller) if provenance_fn else {}
    write_new(target, data)
    return EXIT_OK, summary(arm, data, controller, target)


def summary(arm, data, controller, target):
    lines = [f"{arm} arm complete: {data['n_calls'] if arm == 'blind' else 2 * data['n_rows']} "
             f"calls; live {controller.live_requests}, cache hits {controller.cache_hits}."]
    if arm == "blind":
        lines += [f"MdAPE ratio LLM/fallback {data['mdape_ratio_llm_to_fallback']:.3f}; "
                  f"criterion A {data['criterion_a']['passed']}; criterion V "
                  f"{data['criterion_v']['passed']} (valid share "
                  f"{data['criterion_v']['valid_share']:.3f}).",
                  f"Interpretation: {data['interpretation']}."]
    else:
        lines.append(f"Named MdAPE {data['named_mdape']:.3f} vs blind "
                     f"{data['blind_mdape_same_rows']:.3f} on the same rows (report only).")
    lines.append(f"Written: {target}")
    return "\n".join(lines)


def load_names(conn, rows):
    """Player name and the selling and buying club names for the named arm."""
    names = []
    for _, row in rows.iterrows():
        player = conn.execute("SELECT name FROM players WHERE player_id = ?",
                              (int(row["player_id"]),)).fetchone()[0]
        clubs = conn.execute(
            "SELECT from_club_name, to_club_name FROM transfers WHERE player_id = ? AND "
            "from_club_id = ? AND to_club_id = ? AND transfer_date = ?",
            (int(row["player_id"]), int(row["from_club_id"]), int(row["to_club_id"]),
             row["transfer_date"].strftime("%Y-%m-%d %H:%M:%S"))).fetchone()
        names.append({"player_name": player, "seller_club": clubs[0], "buyer_club": clubs[1]})
    return names


def build_parser():
    parser = argparse.ArgumentParser(
        description="Registered LLM evaluation (docs/llm_criteria.md, tag llm-criteria-v1). "
                    "Guards cannot be skipped. Operational controls do not change the cache key.",
        epilog="Exit codes: 0 complete; 1 refused by a guard; 2 incomplete (external error); "
               "3 request budget reached (resume after the daily reset); 4 NOT RUN (model "
               f"{REGISTERED_MODEL_ID} unavailable).")
    parser.add_argument("--arm", choices=("blind", "named"), required=True,
                        help="blind: 206 test rows, registered arm; named: 50 rows, diagnostic, "
                             "after the blind arm is complete")
    parser.add_argument("--rpm", type=float, default=DEFAULT_RPM,
                        help=f"max live requests per minute (default {DEFAULT_RPM}); cache hits "
                             "are not throttled")
    parser.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS,
                        help=f"live-request budget for this invocation, counting failed and "
                             f"retried requests (default {DEFAULT_MAX_REQUESTS})")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    from advise_cli import open_read_only

    target = REPO_ROOT / RESULTS_REL[args.arm]
    conn = open_read_only(DB_PATH)
    try:
        cached = {}

        def sizes():
            cached["data"], _ = build_dataset(conn)
            data = cached["data"]
            return {s: int((data["split"] == s).sum()) for s in EXPECTED_SIZES}

        errors = guard_errors(REPO_ROOT, sizes, la.MODEL_ID, target)
        blind_path = REPO_ROOT / RESULTS_REL["blind"]
        if args.arm == "named" and not blind_path.exists():
            errors.append("The named arm requires a complete blind arm "
                          f"({RESULTS_REL['blind']} not found)")
        if errors:
            print("EVALUATION REFUSED:")
            for e in errors:
                print(" -", e)
            return EXIT_REFUSED

        rows = ordered_test_rows(cached["data"])
        names = blind_rows = None
        if args.arm == "named":
            import json
            rows = rows.iloc[named_positions(len(rows))].reset_index(drop=True)
            names = load_names(conn, rows)
            blind = json.loads(blind_path.read_text(encoding="utf-8"))["rows"]
            by_position = {b["position"]: b for b in blind}
            blind_rows = [by_position[p] for p in named_positions(len(ordered_test_rows(cached["data"])))]
    finally:
        conn.close()

    m, q, fee_md5 = fee_parameters()
    templates = la.load_templates()
    controller = Controller(Throttle(args.rpm), args.max_requests, log=stderr_log)
    code, message = execute(
        args.arm, rows, m, q, templates, la.DiskCache(REPO_ROOT / EVAL_CACHE_REL), la.MODEL_ID,
        controller, target, la.MODEL_ID, names, blind_rows,
        provenance_fn=lambda c: provenance(REPO_ROOT, templates, fee_md5, c), log=stderr_log)
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
