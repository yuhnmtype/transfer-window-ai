# Results

All numbers below are copied from the committed result files (`results/*.json`) or the
criteria documents (`docs/*.md`), rounded to three decimals unless stated. Every evaluation
was pre-registered: its criteria were committed and tagged before the evaluation script existed
and before any model code touched the test split, and each registered evaluation produced
exactly one result file (run 1; there is no run 2); the blind LLM arm was completed in two
invocations, an earlier one that wrote 11 cache entries and stopped, and the invocation that
completed the run, which reused them from the on-disk cache. Results are descriptive for this
data, this split and this configuration; none of them is a general claim about transfer markets
or about language models.

## Data and split

Source: `results/fee_model_eval.json` (`split_sizes`, `exclusions`). The same split is used by
the fee model and by the LLM evaluation.

| Set | Windows | Rows |
|---|---|---|
| Train | up to and including January 2024 | 1,154 |
| Calibration | summer 2024 and January 2025 | 185 |
| Test | summer 2025 and January 2026 | 206 |

Rows removed by each registered filter: fee not > 0: 4,705; no positive point-in-time
valuation: 33; valuation older than 365 days: 30; loan-fee heuristic: 50; outside the window
months: 0; summer 2026 (incomplete): 17; missing date of birth or position: 0.

Data snapshot: 2026-07-11 (`provenance.db_meta.snapshot_date` in `results/fee_model_eval.json`
and `results/wage_model_eval.json`).

## Wage model (criteria: `docs/wage_model_criteria.md`, tag `wage-criteria-v1`)

Source: `results/wage_model_eval.json`.

| Quantity | Value |
|---|---|
| Selected exponent alpha (fit clubs) | 0.30, at the lower grid boundary |
| Test clubs passing: model / equal split / alpha = 1 | 3 / 5 / 0 of 8 |
| Wilson 95% interval of the model pass rate | [0.137, 0.694] |
| Reverse direction (report only): passes on fit clubs | 4 of 9 |
| Condition A (at least 6 of 8) | failed |
| Condition B (more passes than equal split) | failed |
| Verdict | not accepted |

Consequence (registered): wages are user inputs; simulations use labelled scenarios.

## Fee model (criteria: `docs/fee_model_criteria.md`, tag `fee-criteria-v1`)

Source: `results/fee_model_eval.json`. Point accuracy is measured on the 206 test transfers.

| Quantity | Value |
|---|---|
| MdAPE: OLS model | 0.310 |
| MdAPE: B1, fee = market value | 0.293 |
| MdAPE: B2, fee = market value x 1.25 (training median ratio) | 0.250 |
| Best baseline (fixed once on the test set) | B2 |
| MdAPE ratio model / best baseline | 1.242 (threshold: 0.90 or lower) |
| Paired bootstrap 95% interval of MdAPE_model - MdAPE_B2 (2,000 resamples, seed 20261015) | [-0.016, 0.093] |
| Criterion A (point accuracy) | failed |
| 80% interval coverage (registered band [0.72, 0.88]) | 0.801 |
| Mean interval width | 34.463m EUR |
| Conformal quantile q (log space) | 0.714 |
| Criterion B (interval calibration) | passed |

Consequence (registered): the seller reserve is the fallback **market value x training median
ratio (1.25)**, labelled as a fallback. The 80% interval is kept only as the allowed band for
later adjustments.

Sensitivity report (train from 2015 only, 998 rows; never gates): model MdAPE 0.319, coverage
0.767, ratio to B2 1.278.

## LLM layer (criteria: `docs/llm_criteria.md`, tag `llm-criteria-v1`)

Model `gemini-3.5-flash-lite` via google-adk 2.12.0, temperature 0, minimal thinking. A seller
agent and a buyer agent each return one factor for the reference fee (market value x 1.25);
code clips factors into the band [0.4895, 2.0428] and combines them as `sqrt(f_s x f_b)`.

### Pilot (10 train rows, before the tag)

Source: `results/llm_pilot_run1.json`. 20 responses, 20 valid, 0 retried, 0 clipped,
0 fallback. Pilot outputs were not scored.

### Blind arm run 1 (registered)

Source: `results/llm_eval_blind_run1.json`. 206 test rows, 412 calls.

| Quantity | Value |
|---|---|
| MdAPE: fallback (market value x 1.25) | 0.250 |
| MdAPE: LLM-adjusted | 0.337 |
| MAPE: fallback / LLM-adjusted | 0.942 / 1.022 |
| MdAPE ratio LLM / fallback | 1.348 (threshold: 0.90 or lower) |
| Paired bootstrap 95% interval of MdAPE_LLM - MdAPE_fallback (2,000 resamples, seed 20261015) | [+0.041, +0.140] |
| Criterion A (accuracy) | **failed** |
| Valid share of the 412 calls (threshold 0.95) | 1.000 |
| Share of rows with both agents valid | 1.000 |
| Criterion V (validity) | **passed** |
| Registered interpretation | "A failed: the LLM did not beat the fallback under these conditions (informative)" |

The LLM-adjusted fee did not beat the fallback. On this split its median error was higher than
the fallback's, and the bootstrap interval of the difference lies above zero; this is a
descriptive result for this split and configuration, not a general claim.

Report-only quantities (they gate nothing):

| Quantity | Value |
|---|---|
| Seller-only MdAPE ratio (`m x MV x f_s`) | 1.75 |
| Buyer-only MdAPE ratio (`m x MV x f_b`) | 1.20 |
| Placebo (1,000 draws, `ln f` uniform on [-q, q], seed 20261016): share of placebo ratios at or below the LLM ratio | 4.2% |
| Placebo ratio: median, and 2.5th to 97.5th percentile | 1.539, [1.329, 1.764] |

The seller-only ratio is higher than the buyer-only ratio, which is consistent with an upward
lean of the seller agent; the pilot showed the same direction (all ten seller factors at least
1.15, `docs/llm_criteria.md`, Pilot record). This is an observation, not an explanation.

Validity counts per agent:

| Agent | Calls | Valid | Retried | Clipped | Fallback |
|---|---|---|---|---|---|
| Seller | 206 | 206 | 0 | 0 | 0 |
| Buyer | 206 | 206 | 0 | 0 | 0 |

The invocation that completed the run made 381 live requests and served 31 calls from the
cache. The 412 calls map to 392 cache files: 196 distinct prompts per agent, because 10 test
rows have the same rendered prompt as another row (same position, rounded age, rounded market
value and window type) and share its cached response by design.

### Named-prompt diagnostic run 1 (report only)

Source: `results/llm_eval_named_run1.json`. The 50 registered test positions, with the player
name and both club names added to the prompt; 100 calls, all valid.

| Quantity | Value |
|---|---|
| MdAPE: named prompts | 0.308 |
| MdAPE: blind prompts on the same 50 rows | 0.357 |

This arm gates nothing. The cause of the difference is not established. Leakage through the
model's memory cannot be excluded: the model's knowledge cutoff (March 2026) covers the whole
test window (summer 2025 and January 2026).

### Consequences (registered)

- The baseline policy (factor 1, reserve and valuation = market value x 1.25) stays the
  default in the negotiation engine.
- The LLM policy is experimental and is used only for the ablation.
- Nothing is rerun or tuned on these results. Any new prompt experiment needs a new
  pre-registration and a new tag (`llm-criteria-v2`).

## Provenance

| Item | Value | Source |
|---|---|---|
| Fee evaluation code commit | `fab6766` | `results/fee_model_eval.json` |
| LLM criteria tag commit | `8f5e7cf` | `results/llm_eval_blind_run1.json` |
| Blind run code commit | `26f38e0` | `results/llm_eval_blind_run1.json` |
| Named run code commit | `a906af1` | `results/llm_eval_named_run1.json` |
| Python / google-adk | 3.13.16 / 2.12.0 | `results/llm_eval_blind_run1.json` |
| Template SHA-256 | listed in `docs/llm_criteria.md` (Pilot record) | `results/llm_pilot_run1.json` |
| Blind-run cache SHA-256 | `c6d52e6c...` | `results/llm_eval_blind_run1.json` |

## How to reproduce

The evaluation scripts refuse to run on a dirty working tree, when the criteria or templates
differ from their tag, or when the results file already exists; they never overwrite a result.

```powershell
# Data and finance seed
python src\phase1_build_db.py
python src\phase2_load_seed.py
python src\phase2_check_sources.py

# Tests (no network; ADK tests need the google-adk venv)
.\.venv-adk\Scripts\python.exe -m pytest

# Check that the frozen criteria and templates match their tags (no output = unchanged)
git diff --quiet llm-criteria-v1 -- docs/llm_criteria.md prompts/llm_templates.toml
git diff --quiet fee-criteria-v1 -- docs/fee_model_criteria.md

# The registered evaluations, as they were run
python src\phase3_eval_wage_model.py
python src\phase3_eval_fee_model.py
.\.venv-adk\Scripts\python.exe src\phase3_eval_llm.py --arm blind
.\.venv-adk\Scripts\python.exe src\phase3_eval_llm.py --arm named
```

The LLM evaluation reads the API key from the `GOOGLE_API_KEY` environment variable through the
ADK/GenAI client; the key is never written to any file, and the committed cache contains no
key or request headers. Because the response cache is committed, a re-run with the same code
and prompts serves every call from the cache. This cache-hit path is covered by the test suite
with a fake model; a full re-run against the committed cache has not been performed.
