# LLM valuation layer: pre-registered evaluation criteria (v1)

Frozen by the tag `llm-criteria-v1`. After the tag, the criteria are not changed; any change is
a new commit with a reason and a new tag (`llm-criteria-v2`), never an edit of v1.

## What was examined before writing this

No LLM output on the test split has been produced or seen. No prompt has been sent to any
model for any row of the test split. The aggregate result of the fallback baseline on the
same test split is already public in `results/fee_model_eval.json` (MdAPE 0.25); prompt
design must not use any row-level test data.

## Purpose and expectations

Two ADK agents (a seller agent and a buyer agent) each return one bounded valuation factor
for a transfer. Code, not the LLM, turns factors into fees. The question is whether the
LLM-adjusted fee is more accurate than the registered fallback fee (`m x MV`).

The blind prompt contains only features that the OLS fee model already saw (position, age,
market value, window type). The OLS model did not beat the fallback (`fee-criteria-v1`,
criterion A failed). **No improvement is expected a priori. A negative result is an
acceptable outcome and will be reported as such.**

## Model

- Model id: **`gemini-3.5-flash-lite`**. Status Stable. Knowledge cutoff March 2026, with
  some domains limited to January 2025 (official model card, as provided by the owner).
- **The cutoff covers the whole test window (S2025 and W2026).** The real transfers of the
  test split may be in the model's training data, so test-split leakage through the model's
  memory cannot be ruled out, even with blind prompts.
- If this exact model id is unavailable at run time, the run is reported as **NOT RUN**. Using
  a different model requires a new tag `llm-criteria-v2`.

### Generation settings (for the most deterministic runs)

Source: https://ai.google.dev/gemini-api/docs/thinking (page last updated 2026-10-14,
accessed 2026-10-17). Its table lists `gemini-3.5-flash-lite` with default thinking "On
(minimal)" and supported levels minimal, low, medium, high, so thinking cannot be switched
off for this model.

- `thinking_level = "minimal"` (the lowest supported level), thought summaries off.
- `temperature = 0`, one candidate, and the SDK `seed` parameter set to 20261015.
- No small `max_output_tokens`: the same page says the limit includes thinking tokens and a
  response that hits it is truncated.
- Temperature 0 and a seed do **not** guarantee identical outputs across calls. The
  committed response cache (below) is the reproducibility mechanism, not the model.

## Data and split

- The dataset is built by `fee_model.build_dataset` with the 7 filters of
  `docs/fee_model_criteria.md` (tag `fee-criteria-v1`). The evaluation script verifies the
  split sizes are exactly 1154 / 185 / 206 before any LLM call, and aborts otherwise.
- The registered blind run scores only the **206 test rows**, in the order produced by
  `build_dataset`: transfer date, then `player_id`, ascending.
- Fallback ratio `m` and conformal quantile `q` are read from `results/fee_model_eval.json`
  (never hard-coded); the evaluation records that file's MD5. With the current file,
  `m = 1.25` and `q = 0.714309...`, so the band is `[exp(-q), exp(q)] = [0.4895, 2.0428]`.

## Prompts

### Blind prompt (registered arm)

- Features, all point-in-time valid: position; age at transfer rounded to the nearest
  integer; market value at transfer rounded to the nearest 1m EUR (half up, with a floor of
  1m EUR); window type (summer or winter).
- The prompt states the reference value `m x MV_rounded` and the band `[0.4895, 2.0428]`, and
  asks for one factor. Scoring uses the exact (unrounded) market value.
- Never included: player or club names, club ids, player ids, dates, seasons, fees.
- Indirect leakage through the model's memory remains possible and cannot be ruled out.

### Named prompt (leakage diagnostic arm only)

Same as the blind prompt plus the player's name and the selling and buying clubs' names.

### Templates

The prompt templates (system, seller blind, buyer blind, seller named, buyer named) are in
`prompts/llm_templates.toml`, committed before the tag. The SHA-256 of each template string
(exactly as parsed from that file) is recorded in the Pilot record below, as written by the
pilot script. The band is shown in the prompt rounded to 4 decimals (`0.4895`, `2.0428`), and
these same rounded values are used for validation and clipping. The reference value is shown
with 2 decimals.

## Agents and output

- Two separate ADK agents: seller (reserve adjustment) and buyer (valuation adjustment).
  Each returns JSON `{"factor": <number>, "rationale": <short text>}`.
- The output **parses** when the response text, after trimming whitespace, is a JSON object
  with a finite numeric `factor` (not a string, not a boolean) and a string `rationale`. No
  code fences or extra text are accepted. The 40-word limit on the rationale is requested in
  the prompt but is not part of validity. This parse rule (for example accepting code fences)
  may only be changed before the tag `llm-criteria-v1`, and only on the basis of the pilot.
- An **empty or safety-blocked** model response is model behaviour: it does not parse, so it
  is **invalid** (retried once, then fallback), and it is never treated as an external
  failure. Only quota, network and API errors are external failures (see "Complete run").
- A call is **valid** when its output parses and `factor` is a finite number inside the band
  `[0.4895, 2.0428]`, on the first attempt or on the single retry.
- If the first attempt is not valid, the call is retried once with the same prompt.
- If the retry parses but the factor is outside the band, the factor is clipped into the band
  and used for scoring, but the call counts as **not valid**.
- If the retry does not parse, the factor is 1 (fallback) and the call counts as not valid.
- The LLM never computes fees, SCR figures or any other number in the pipeline.

## Reproducibility and cache

- Runtime: venv `.venv-adk`, Python 3.13, google-adk 2.12.0.
- Every response is cached on disk, keyed by the SHA-256 of (model id, generation settings,
  agent role, attempt number, system text, prompt text). The attempt number makes the single
  retry a real new call rather than a cache hit on the first answer. A cached response is
  always reused; cache entries are never deleted or edited.
- The cache is served through ADK callbacks: `before_model_callback` returns the cached
  response, which makes ADK skip the model call; `after_model_callback` stores a new one.
- The cache is committed with the results. Each entry stores only the cache key, model id,
  generation settings, agent role, attempt number, system text, prompt text and response
  text, and **never the API key or any request headers**; a test checks this.
- The results file records: model id, ADK and Python versions, generation settings, prompt
  template hashes, cache file hash, code commit, tag commit, MD5 of
  `results/fee_model_eval.json`, seeds, and the counts of valid, retried, clipped and fallback
  calls per agent.

## Scoring

- Fallback fee (fixed baseline): `fee_B = m x MV`.
- LLM-adjusted fee: `fee_LLM = m x MV x sqrt(f_s x f_b)` (geometric mean of the seller factor
  `f_s` and the buyer factor `f_b`).
- Report only (never tested): seller-only `m x MV x f_s` and buyer-only `m x MV x f_b`.
- APE, MdAPE and MAPE as in `docs/fee_model_criteria.md`.

### Placebo (report only)

1,000 placebo draws with root seed 20261016: for each draw, every test row gets independent
`f_s` and `f_b` with `ln f` uniform on `[-q, q]`, combined with the same geometric mean. For
each draw compute `MdAPE_placebo / MdAPE_fallback`. Report the percentile of the LLM ratio
within these 1,000 ratios (share of placebo ratios less than or equal to the LLM ratio).

### Leakage diagnostic arm (report only)

- 50 test rows chosen with `numpy.random.default_rng(20261015).choice(206, 50,
  replace=False)` as positions in the ordered test split (transfer date, then `player_id`),
  then sorted.
- Each of the 50 rows is run a second time with the named prompt (both agents).
- Report MdAPE of the named arm versus the blind arm on the same 50 rows.
- This arm necessarily uses test rows. It is **diagnostic only and never gates any
  criterion**.

## Acceptance

### A: accuracy (both required)

1. `MdAPE_LLM / MdAPE_fallback <= 0.90`. The 0.90 is the same unsourced
   practical-significance choice as in `fee-criteria-v1`.
2. Paired bootstrap over the 206 test rows: 2,000 resamples with replacement,
   `numpy.random.default_rng(20261015)`, all resamples drawn at once as one index matrix. For
   each resample, `MdAPE_LLM - MdAPE_fallback` on the same rows, always against the fixed
   fallback baseline (never re-selected). The 97.5th percentile (`np.percentile`, linear
   interpolation) must be < 0.

### V: validity

The share of valid calls among the 412 blind calls (206 rows x 2 agents) must be `>= 0.95`.
The share of rows where both agents are valid is also reported. If V fails, A is reported but
the run does not count as a pass.

### Interpretation rule (pre-registered)

- **A failing is informative:** the LLM did not beat the fallback under these conditions.
- **A passing is reported as "passed, leakage not excluded"** and must never be described as
  evidence of useful judgement, because the model's knowledge cutoff covers the test window.

### Complete run

Every one of the 412 blind calls must have a response, valid or not. If any call fails for an
external reason (quota, network, API error) after the retry, the run is reported as
**incomplete** and no metric is reported, never a partial result. The run may be completed in
batches: a later batch uses the same model id, prompts and code, and reuses the cache, so it
cannot change earlier answers. The named arm is reported as incomplete on its own if any of
its calls is missing; it never affects the blind result.

## Pilot (before the tag)

`src/llm_pilot.py` sends at most 10 **train** rows (never calibration or test rows) through
both blind agents to check the output format. The rows are positions
`numpy.random.default_rng(20261017).choice(n_train, 10, replace=False)` in the ordered train
split, then sorted; the script refuses any row that is not in the train split. Its cache
(`results/llm_cache/pilot/`) is kept separate from the evaluation cache, and it writes
`results/llm_pilot_run1.json` once, never overwritten. Pilot outputs are not scored and do
not change any criterion.

### Pilot record

- Result file: `results/llm_pilot_run1.json` (10 train rows, blind seller and buyer agents).
- Counts: 20 responses, 20 valid, 0 retried, 0 clipped, 0 fallback.
- Code commit: `2583b97`. Model `gemini-3.5-flash-lite`, google-adk 2.12.0, Python 3.13.16,
  generation settings as registered above.
- Template SHA-256 (as recorded in the result file):
  - system `07e0d4a6779c8ac0403587d72a6201b3fc903f46bd20e7adf2f870556f79226b`
  - seller_blind `af1509ae7f92ecf338b39ede72996e5c386220aee52e4305a3130a22c29628d8`
  - buyer_blind `04fe2c0927a7230b411dc05e61bd3144d56dcb17fa71ddb8fc18c7fba62d4bdb`
  - seller_named `77af1a2bd3767a5272a73eef1260c5f07d430520fd6da17e7bce5bb4f7995db3`
  - buyer_named `57cfb9ff64b70b66458c58b80ef060b4cd33f7865c8499e24294ca0c1ec9cdbf`

Provenance of the 20 responses:
- 16 come from an earlier invocation of `src/llm_pilot.py` by the project owner on 2026-10-17
  at 19:22:24 to 19:22:36 +0100, with a different API key (same model id and configuration).
  That run stopped before completing and wrote no results file. The script did not record the
  cause; the AI Studio rate-limit page later showed a peak of 16 requests per minute on
  2026-10-17, against a limit of 15 per minute.
- The other 4 came from live calls on 2026-10-18 at 12:35 +0100, in the run that wrote the
  result file.
- The result file's "20 calls" therefore counts cache hits: 16 cached plus 4 live.

Observations (plain facts; no accuracy was computed on pilot rows):
- (a) All seller factors were at least 1.15, so the combined factor tends to be above 1: the
  geometric mean of the 10 combined factors `sqrt(f_s x f_b)` is about 1.13.
- (b) The rationales mention details that are not in the prompt features (for example
  leadership, versatility or importance to the squad). They are narrative, not evidence of
  reasoning.
- (c) The safety-block path was not exercised.
- (d) The response text format varied between calls at temperature 0 (6 compact and 14
  indented JSON texts). This is why the committed cache, not the model, is the
  reproducibility mechanism.

No template, parse rule or criterion was changed after the pilot.

## Quota estimate

This section is an operational estimate. It is not part of the pre-registered criteria, and
it does not constrain or change any criterion.

How the numbers are computed:
- Blind arm: 206 test rows x 2 agents = 412 primary calls.
- Named arm: 50 rows x 2 agents = 100 primary calls.
- The "worst case" column assumes one retry for every call, as in the retry rule above, so it
  doubles the primary calls.

| Part | Primary calls | Worst case with one retry each |
|---|---|---|
| Pilot (train, done) | 20 responses: 16 from the run on 2026-10-17 and 4 live on 2026-10-18; no retries | done |
| Blind arm (registered) | 412 | 824 |
| Named arm (diagnostic) | 100 | 200 |
| **Planned remaining (blind + named)** | **512** | **1,024** |

The placebo arm needs no calls. Batches resumed from the cache are allowed (see "Complete
run").

Operational note: the AI Studio rate-limit page on 2026-10-18 for `gemini-3.5-flash-lite`
(free tier, Default Gemini Project) shows limits of 15 requests per minute, 250K tokens per
minute and 500 requests per day, and a recorded peak on 2026-10-17 of 16 requests per minute
(above the limit), 3.16K tokens per minute and 17 requests per day. This explains the 429
TooManyRequests on that day; the page does not say which request was refused. The limits apply
per project, count failed and retried requests, may change, and the daily limit resets at
midnight Pacific time. They must be rechecked before the run.

Operational plan for the main run (**not a criterion, and it does not change the cache key**):
- A throttle of at most 10 requests per minute.
- A backoff on 429 and on other external errors, with a bounded number of attempts; when the
  attempts are used up, the "Complete run" rule applies.
- A daily budget below 500 requests, counting failed and retried requests.
- The blind arm first; the named arm in a later batch, after the daily reset.
- The planned remaining 512 primary calls therefore need at least 2 Pacific days (412 blind,
  then 100 named), and the worst case of 1,024 needs at least 3.
- Batches resume from the cache.

## Guards and order of commits

1. The criteria file, the prompt templates and `src/llm_pilot.py` are committed; the pilot is
   run on train rows only; the pilot record and template hashes are written here; this file
   is committed and tagged `llm-criteria-v1`.
2. The main evaluation script that touches the test split is committed **only after** the
   tag. Its guard refuses to run if that script already existed at the tag commit.
3. The evaluation script refuses: a dirty working tree, where the only allowed exception is
   untracked files inside the evaluation cache directory (`results/llm_cache/eval/`) named
   `<64 lowercase hex characters>.json` (cache entries from earlier batches); any other
   untracked or modified file still makes the script refuse; a missing tag
   `llm-criteria-v1`; this file or the prompt templates changed since the tag; split sizes
   other than 1154 / 185 / 206; an existing results file (atomic write, no overwrite);
   a model id in the code that differs from `gemini-3.5-flash-lite`. There is no option to
   skip the guards.

## Consequences

- A and V pass: the result is reported as "passed, leakage not excluded". The negotiation
  engine may use the LLM policy, with factors clipped into the band; the baseline policy is
  kept for ablation.
- A or V fails: the baseline policy (factor 1) stays the default. The LLM policy is labelled
  experimental and used only for the ablation. The README reports the failure.
- Model unavailable: NOT RUN (see Model).

## Limitations

- The blind prompt uses the same features as the OLS model, so the LLM receives no extra
  information from the data.
- The model's knowledge cutoff covers the test window: memory leakage of real transfers
  cannot be ruled out, and the named arm can only indicate it, not exclude it.
- Anchoring: the prompt states the reference value `m x MV` and the band, which can pull
  factors towards 1.
- Rounding age and market value in the prompt removes a little information.
- Temperature 0 does not guarantee identical outputs across calls; the cache makes the
  registered run reproducible, not the model.
- Small test set (206 rows); all results carry sampling noise.
- The fallback baseline's test MdAPE is already known; only row-level LLM outputs are unseen.
- Inherits the limitations of `docs/fee_model_criteria.md` (fee > 0 only, selection bias,
  current position, no contract length, heuristic loan filter).
