# Advisor tool agent: pre-registered evaluation criteria (v1) - DRAFT

**Status: DRAFT for owner review. Not frozen.** After review, a pilot and the pilot record,
this file is committed and tagged `agent-criteria-v1`. After the tag it is not changed; any
change is a new commit with a reason and a new tag (`agent-criteria-v2`). Items marked
**[PROPOSED]** are choices for the owner to confirm or change before the tag.

## What was examined before writing this

No agent exists yet, so no agent output has been produced or seen. The expected tool
arguments below follow only from the wording of each question. The expected numbers are not
listed here: they are computed by code from the frozen database at evaluation time. During
design, the deterministic advisor (code, not a model) was run on the in-scope questions to
check that every deal is valid and to vary the SCR outcomes; three questions (Q02, Q07, Q08)
were changed on that basis. Only these before/after statuses are listed below.

## Purpose and expectations

One ADK agent answers a natural-language question about one hypothetical permanent transfer
for a Premier League buyer club. It calls tools that compute every number, then returns a
structured JSON answer. The evaluation asks three questions:

- **(a) Numeric faithfulness:** does every number the agent quotes equal the value the tool
  returned?
- **(b) Tool selection and arguments:** does the agent call the right tool with the right club,
  fee, currency, wage, contract length and agent fee?
- **(c) Out of scope:** does the agent decline questions the system cannot answer (unknown or
  ineligible club, real-world verdicts, loans, other seasons) without producing SCR numbers?

The agent is used for this evaluation only. It is not added to the CLI or the local demo,
and the demo stays free of ADK.

## Model

- Model id **`gemini-3.5-flash-lite`**, the same model and generation settings as
  `docs/llm_criteria.md` (temperature 0, one candidate, seed 20261015, thinking level
  minimal, thought summaries off). If this model id is unavailable at run time, the run is
  reported as **NOT RUN**.
- Runtime: venv `.venv-adk`, google-adk 2.12.0. The ADK/GenAI client reads its own
  credentials; no project code reads, prints or logs the API key.
- Temperature 0 and a seed do not guarantee identical outputs. The committed cache is the
  reproducibility mechanism.

## Tools

Both tools are deterministic Python functions in `src/advisor_agent.py`
(`tool_list_buyer_clubs`, `tool_advise_deal`). They read the database read-only and never
raise into the agent: on bad input they return `{"error": "<message>"}`.

1. `list_buyer_clubs()` returns `[{"club_id", "club_name"}]` for the clubs that can be buyers
   in 2026/27 (`advisor.eligible_clubs`, fiscal year 2024/25): 17 clubs. Burnley, West Ham and
   Wolves are not in the list. ADK wraps a list return value, so the recorded tool result is
   `{"result": [...]}`.
2. `advise_deal(club_id, fee, fee_currency, annual_wage_gbp_m, contract_years,
   agent_fee_gbp_m=None)` calls `advisor.advise_deal` (permanent deal, no outgoing player)
   and returns a flat dict of the quotable fields below, each rounded as listed, plus
   `club_name`, `before_status`, `after_status` and the warning codes.
   **[PROPOSED]** The tool rounds its own output, so the agent can copy values exactly.

| Quotable field | Source in `advisor.to_dict` | Rounding |
|---|---|---|
| `fee_gbp_m` | `fee_gbp_m` | 2 decimals |
| `annual_amortisation_gbp_m` | `incoming_cost.annual_amortisation_gbp_m` | 2 decimals |
| `before_adjusted_revenue_gbp_m`, `after_adjusted_revenue_gbp_m` | `before/after.adjusted_revenue_gbp_m` | 2 decimals |
| `before_squad_cost_gbp_m`, `after_squad_cost_gbp_m` | `before/after.squad_cost_gbp_m` | 2 decimals |
| `before_ratio`, `after_ratio` | `before/after.ratio` (a fraction, not a percentage) | 4 decimals |
| `before_headroom_gbp_m`, `after_headroom_gbp_m` | `before/after.headroom_gbp_m` | 2 decimals |
| `headroom_change_gbp_m` | `headroom_change_gbp_m` | 2 decimals |
| `max_wage_green_gbp_m`, `max_wage_red_gbp_m` | `max_wage_green/red.annual_gbp_m` (may be null) | 2 decimals |

Why rounding is needed (read from code): `to_dict` returns raw floats such as
`before.headroom_gbp_m = -8.345000000000056` for the README example, which rounds to -8.35 at
2 decimals; a 0.005 tolerance on the raw value would then fail exactly at the boundary.

## Agent output

The agent's final answer must be one JSON object, declared as the agent's `output_schema`.

How ADK 2.12.0 enforces it (read from the installed package): a model can receive a response
schema together with tools only when it reports the capability `output_schema_and_tools`.
For Gemini this is true only on Vertex AI; for `gemini-3.5-flash-lite` through AI Studio it is
false (checked with `gemini_output_schema_and_tools`), and fake test models report false too.
In that case ADK adds a tool named `set_model_response` with the schema fields as arguments,
plus an instruction to call it for the final answer. ADK validates the arguments against the
schema and turns them into the final text with `json.dumps`. So, in this evaluation:

- the final answer arrives as a `set_model_response` tool call, which is recorded in the
  transcript like the other tool calls;
- a model that answers with plain text instead produces a final text that does not parse;
- the parse rule below is applied to the final text in both cases.

The schema:

```json
{
  "outcome": "answered | out_of_scope | tool_error",
  "club_id": 1148,
  "answer": "<at most 80 words>",
  "quoted": [{"field": "after_headroom_gbp_m", "value": -16.35}]
}
```

- **Parses** when the final text, after trimming whitespace, is a JSON object with: `outcome`
  one of the three strings; `club_id` an integer or null; `answer` a string; `quoted` a list
  of objects, each with a `field` string and a finite numeric `value` (not a boolean). No code
  fences or extra text.
- `club_id` is null when `outcome` is `out_of_scope`.
- `outcome` is the agent's answer type; it is unrelated to the SCR status fields
  `before_status`/`after_status` (green, amber, red) in the tool result.
- The answer word limit is requested in the prompt but is not part of validity.
- **[PROPOSED]** No retry: a run whose final text does not parse fails every criterion for
  that run.
- **[PROPOSED]** At most 6 model calls per run (counted in `before_model_callback`). A run that
  exceeds this is stopped and counts as not parsed.

## Question set

30 questions: 22 in scope and 8 out of scope (2 of each type). Each question is run 3 times
(run index 1, 2, 3; the run index is part of the cache key), so there are 90 runs.
With temperature 0 and a fixed seed, the 3 runs of a question are a stability check, not
independent samples (inferred): they can show that the model's behaviour changes between
calls with identical input, but they cannot estimate how the agent would behave on new
questions or wordings, and agreement across runs is not evidence of correctness.
**[PROPOSED]** The machine-readable copy of these tables is `prompts/agent_questions.toml`
(`[[question]]` entries, generated from the tables below; a test checks that every question
text and id appears in this file). Its SHA-256 is recorded in the pilot record.

Expected tool arguments for the in-scope questions (fee in millions; wage is annual, GBP m;
agent fee GBP m; "-" means not stated, so the expected value is null):

| Id | Question | club_id | fee | cur | wage | years | agent |
|---|---|---|---|---|---|---|---|
| Q01 | Brentford want to sign a player for £20m on a four-year contract with wages of £3m a year. What does that do to their squad cost ratio? | 1148 | 20 | GBP | 3 | 4 | - |
| Q02 | What happens to Arsenal's SCR if they pay €80m for a midfielder on a five-year deal at £10m per year? | 11 | 80 | EUR | 10 | 5 | - |
| Q03 | Spurs are looking at a £45m signing, £6m annual wages, five years. Can they afford it under the squad cost rules? | 148 | 45 | GBP | 6 | 5 | - |
| Q04 | Man Utd: £35m fee, 4 years, £5.5m a year in wages, plus a £3m agent fee. | 985 | 35 | GBP | 5.5 | 4 | 3 |
| Q05 | Check a deal for Villa: €30m transfer, £4m yearly wage, contract of 3 years. | 405 | 30 | EUR | 4 | 3 | - |
| Q06 | Newcastle United would pay £50m for a striker with £7m a year over 5 years. Impact on headroom? | 762 | 50 | GBP | 7 | 5 | - |
| Q07 | Brighton & Hove Albion buying a defender for £90m, £16m a year, 5-year contract. | 1237 | 90 | GBP | 16 | 5 | - |
| Q08 | Forest are considering €90m for a winger on £14m per year for 5 years. | 703 | 90 | EUR | 14 | 5 | - |
| Q09 | Chelsea: £70m fee, wages of £9m a year, six-year contract. | 631 | 70 | GBP | 9 | 6 | - |
| Q10 | Liverpool want a £55m player, £7.5m per year, 5 years, agent fee £4m. | 31 | 55 | GBP | 7.5 | 5 | 4 |
| Q11 | Manchester City: €80m for a midfielder, £10m a year, 5 years. | 281 | 80 | EUR | 10 | 5 | - |
| Q12 | Everton are thinking about a £12m signing on £2m a year for three years. | 29 | 12 | GBP | 2 | 3 | - |
| Q13 | Fulham: £18m fee, £2.8m annual wage, 4 years. | 931 | 18 | GBP | 2.8 | 4 | - |
| Q14 | Crystal Palace would spend €20m on a forward, £2.5m a year, 4-year deal. | 873 | 20 | EUR | 2.5 | 4 | - |
| Q15 | Bournemouth sign a player for £22m with £3.2m a year over 5 years and a £1.5m agent fee. | 989 | 22 | GBP | 3.2 | 5 | 1.5 |
| Q16 | Leeds United: £10m, £1.8m a year, 3 years. | 399 | 10 | GBP | 1.8 | 3 | - |
| Q17 | Sunderland want a €12m player on £1.5m a year for 4 years. | 289 | 12 | EUR | 1.5 | 4 | - |
| Q18 | Can Tottenham Hotspur add a £5m player on £1m a year, 2-year contract? | 148 | 5 | GBP | 1 | 2 | - |
| Q19 | Arsenal: £90m, £12m a year, five years. | 11 | 90 | GBP | 12 | 5 | - |
| Q20 | Brentford FC: €8m fee, £1.2m per year, 3 years. | 1148 | 8 | EUR | 1.2 | 3 | - |
| Q21 | Man City buying a £40m defender on a 4-year contract at £6m a year, agent fee £2m. | 281 | 40 | GBP | 6 | 4 | 2 |
| Q22 | Newcastle: €45m, £5m per year, 5-year contract. | 762 | 45 | EUR | 5 | 5 | - |

Coverage of the in-scope set: 17 eligible clubs (Brentford, Arsenal, Tottenham, Man City and
Newcastle twice); 8 fees in EUR; 4 with an agent fee; one contract above the 5-year
amortisation cap (Q09); two Championship-revenue clubs (Q16 Leeds, Q17 Sunderland); club
names given as full names, short names and nicknames (Spurs, Man Utd, Villa, Forest). Wages
are always stated as annual GBP m, so the agent never has to convert units.

SCR outcomes of the in-scope set, computed by the advisor (code, not a model) during design:
2 green to green (Q03, Q18), 2 green to amber (Q02, Q19), 2 amber to red (Q07, Q08) and 16
amber to amber. No buyer club is red before a deal in the frozen data, so the set has no
red-to-red case. Both amber-to-red cases end just above the red threshold (ratio 1.0065
against 1.0 for Q07, 1.1591 against 1.15 for Q08).

Out-of-scope questions (expected `outcome`: `out_of_scope` or `tool_error`):

| Id | Type | Question |
|---|---|---|
| O01 | Unknown club | Real Madrid want to sign a player for £50m on £8m a year for 5 years. What is their SCR? |
| O02 | Ineligible club | Wolves: £15m fee, £2m a year, 4 years. What is the SCR impact? |
| O03 | Real-world conclusion | Will Chelsea get a points deduction this season? |
| O04 | Real-world conclusion | Is Manchester United going to break the Premier League spending rules in 2026/27? |
| O05 | Loan | Arsenal want to take a player on a season-long loan with £3m wages. What is the SCR effect? |
| O06 | Loan | Can Everton afford a loan with an option to buy, a £2m loan fee and £1.5m wages? |
| O07 | Other season | What would a £30m signing on £4m a year for 4 years do to Chelsea's SCR in 2027/28? |
| O08 | Other season | For the 2025/26 season, check Fulham buying a £10m player on £2m a year for 3 years. |

## Scoring (per run)

- **V (parse):** the final text parses (see "Agent output").
- **B (tool and arguments), in-scope runs:** passes when all hold:
  1. the run parses, `outcome` is `answered` and `club_id` equals the expected club_id;
  2. at least one `advise_deal` call returned a result without `error`;
  3. the last such call's arguments equal the expected ones: `club_id` and `fee_currency`
     exactly; `fee`, `annual_wage_gbp_m`, `contract_years` and `agent_fee_gbp_m` within
     1e-9 (null expected means the argument is absent or null; an explicit 0 counts as
     wrong).
  Report only: whether `list_buyer_clubs` was called before the first `advise_deal` call, the
  number of `advise_deal` calls per run, and the number of model calls per run. The
  `set_model_response` call is not a tool choice and is ignored by B.
- **A (numeric faithfulness), in-scope runs that pass B:** passes when every item in `quoted`
  names a quotable field present in the last successful `advise_deal` result and its `value`
  equals that rounded result within 1e-9, and `quoted` contains at least `after_ratio`,
  `after_headroom_gbp_m` and `headroom_change_gbp_m`. A null tool value cannot be quoted.
- **A2 (numbers in the free text), descriptive only, never an acceptance gate:** for in-scope
  runs that pass B, extract every number from `answer` with a fixed regex after normalising:
  remove `£`, `€`, `GBP`, `EUR`; read a trailing `m`/`million` as millions and `k` as
  thousands; read `%` as a percentage and compare it with the matching fraction times 100;
  map the Unicode minus (U+2212) and an en dash used as a sign to `-`; drop thousands
  separators. A number counts as supported when it is in the union of: (1) the rounded
  values of the last successful `advise_deal` result, also at 1 decimal and, for ratios, as
  a percentage at 1 decimal; (2) the numbers in the question; (3) fixed constants:
  thresholds 70, 85, 100, 115 (and 0.70, 0.85, 1.00, 1.15), FX 0.86, the amortisation cap
  5, and the labels 2026/27 and 2024/25 (with their parts 2026, 27, 2024, 25). Report the
  share of runs where every number is supported, and the list of unsupported numbers per
  run. A2 is descriptive: it does not affect V, B, A or C.
- **C (out of scope), out-of-scope runs:** passes when the run parses, `outcome` is
  `out_of_scope` or `tool_error`, `quoted` is empty, and no `advise_deal` call returned a
  result without `error`.

## Acceptance [PROPOSED thresholds]

Headline rates are computed per question: a question passes a criterion only if all its 3
runs pass it (V over 30 questions, B over 22, A over the questions whose 3 runs all pass B,
C over 8). Per-run rates (V over 90, B over 66, A over the runs that pass B, C over 24) are
reported as secondary figures and never gate.

| Criterion | Headline denominator | Threshold | What the threshold allows |
|---|---|---|---|
| V | 30 questions | share passing >= 0.95 | at most 1 failing question |
| B | 22 in-scope questions | share passing >= 0.90 | at most 2 failing questions |
| A | in-scope questions whose 3 runs all pass B | share passing >= 0.95 | at most 1 failing question when the denominator is 22 |
| C | 8 out-of-scope questions | share passing >= 0.90 | no failing question (7 of 8 is 0.875) |

The thresholds were set before any agent output existed, with no empirical basis; they are
owner choices, in the same spirit as the 0.90 and 0.95 of `llm-criteria-v1`. The sets are
small: with 8 out-of-scope questions, one failing question moves the question-level C rate
by 12.5 percentage points (one failing run moves the run-level rate by about 4.2 points
over 24 runs); one in-scope question moves B by about 4.5 points (1 of 22).

Also reported, never gating: per-question pass counts out of 3 (stability across runs), per
out-of-scope type, the run-level rates above and A2.

### Complete run

Every one of the 90 runs must finish with a final answer, valid or not. If any run fails for
an external reason (quota, network, API error) after backoff, the evaluation is reported as
**incomplete** and no metric is reported. Batches resume from the cache.

## Reproducibility and cache

- Every model call is cached on disk, keyed by the SHA-256 of (model id, generation settings,
  question id, run index, and the serialised model request: its config, which holds the
  system text and the tool declarations including `set_model_response`, and the contents so
  far, including earlier tool calls and tool results). The random function-call ids that ADK
  adds are removed before hashing, so a replay hits the same keys. The tools are
  deterministic, so a replay from the cache reproduces the whole run (tested with a fake
  model: a replay makes no model calls).
- Each entry stores only whitelisted fields (key, model id, generation settings, question id,
  run index, call number, request, response) and never the API key or request headers; the
  `check_no_secrets` patterns of `src/llm_agents.py` apply.
- For every run, the transcript of tool calls (name, arguments) and tool results is recorded
  in the results file, so B and A can be rescored from the file alone.
- The results file records: model id, ADK and Python versions, generation settings, system
  prompt hash, question file hash, tool declarations hash, cache hash, code commit, tag commit,
  the database MD5 and `meta` table, and the counts above.

## Pilot (before the tag)

**[PROPOSED]** 4 pilot questions that are not in the set above, each run once, to check the
output format and the tool loop: one GBP and one EUR in-scope deal (values not used above;
every buyer club already appears in the set), one unknown club, one loan. They are the
`[[pilot]]` entries of `prompts/agent_questions.toml`; the loader refuses a pilot question that
repeats a registered id or text.

| Id | Kind | Question |
|---|---|---|
| P01 | In scope | Everton want a £25m centre-back on £3.5m a year over 5 years. What happens to their SCR? |
| P02 | In scope | Aston Villa: €40m fee, £5m a year, four-year contract. |
| P03 | Unknown club | Ajax want to sign a £20m player on £2m a year for 4 years. What is their SCR? |
| P04 | Loan | Brentford want a player on loan for the season, paying £1m in wages. What is the SCR impact? |

The pilot script is `src/agent_pilot.py` (run in `.venv-adk`). It opens the database
read-only, pauses 30 seconds between questions (operational, not a criterion), uses its own
cache directory `results/agent_cache/pilot/` and writes `results/agent_pilot_run1.json` once.
If a call fails for an external reason, nothing is written and a rerun resumes from the cache. Pilot outputs are not scored and change no criterion,
except that the parse rule and the model-call limit may be adjusted before the tag on the
basis of the pilot.

## Quota estimate (operational, not a criterion)

Inferred: an in-scope run needs about 3 model calls (`list_buyer_clubs`, `advise_deal`, then
`set_model_response` for the final answer) and an out-of-scope run 1 to 2 (`list_buyer_clubs`
when a club is named, then `set_model_response`). The fake-model test of a complete in-scope
run uses exactly 3 model calls; the real number is checked in the pilot.

| Part | Typical calls | Worst case (6 calls per run) |
|---|---|---|
| Pilot | about 10 | 24 |
| In scope: 22 questions x 3 runs | about 198 | 396 |
| Out of scope: 8 questions x 3 runs | about 48 | 144 |
| **Main run** | **about 246** | **540** |

The free-tier limits recorded in `docs/llm_criteria.md` (15 per minute, 500 per day on
2026-10-18) must be rechecked before the run. The same throttle, backoff and daily budget as
`src/phase3_eval_llm.py` apply; the worst case needs at least 2 Pacific days.

## Guards and order of commits

1. The agent module (`src/advisor_agent.py`), its tests with a fake model
   (`tests/test_advisor_agent.py`), the system prompt (`prompts/agent_prompt.toml`), the
   question file (`prompts/agent_questions.toml`) and the pilot script (`src/agent_pilot.py`)
   are committed; the pilot is run; the pilot record and hashes are written here; this file
   is committed and tagged `agent-criteria-v1`.
2. The evaluation script is committed only after the tag, and refuses to run if it already
   existed at the tag commit.
3. The script refuses: a dirty working tree (except new cache entries in its own cache
   directory); a missing tag; this file, the system prompt or the question file changed since
   the tag; a model id other than `gemini-3.5-flash-lite`; an existing results file.

## Consequences

The agent is for evaluation only. A pass is reported as such and does not by itself add the
agent to the CLI or the demo; that would be a separate owner decision. A failure is reported
in the README and `docs/results.md`.

## Limitations

- 30 questions written by the project (assistant draft, owner review), not by real users; the
  phrasing may be easier or harder than real questions.
- Real club names are used. The numbers are computed by code from the frozen database, so the
  model's memory cannot supply them (inferred), but club-name mapping may benefit from it.
- Wages are always given as annual GBP m and only permanent deals without an outgoing player
  are covered.
- No buyer club is red before a deal in the frozen data, so the set has no red-to-red case,
  and the two amber-to-red cases end only just above the red threshold.
- Scoring A checks only the structured `quoted` list; the free text is checked only
  descriptively (A2).
- The final answer goes through ADK's `set_model_response` workaround (no native response
  schema with tools on AI Studio), so the result also reflects how well the model follows
  that ADK-added instruction.
- Out-of-scope scoring checks behaviour (outcome, no successful tool result), not the wording
  of the refusal.
- Inherits every limitation of the advisor and the finance seed (FY 2024/25 accounts, club-wide
  wage bill, impairment counted as 0, secondary-source rules).
