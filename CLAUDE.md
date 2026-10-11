# Transfer Window AI: project context for Claude Code

## Goal
Multi-agent system that simulates and advises Premier League transfer negotiations
under real 2026/27 regulations (SCR, GBE), built on real Transfermarkt data.
Portfolio project for an AI Engineering CV. Later stages use Google ADK + Gemini.

## Working conventions
- Do one step at a time and wait for the owner to confirm before the next step.
- When a new idea comes up, ask the owner open questions before implementing it.
- Explanations to the owner in Vietnamese. All code, code comments, commit messages
  and docs in English.
- The owner must understand every line: explain code after writing it, keep changes small.
- Prefer tools that run on Google Colab and locally on Windows. Python 3.15 locally.
- When comparing anything, use the UK as the primary frame.
- Never invent numbers. Every financial figure needs a source URL and a confidence level.

## Architecture (v1)
- The engine is domain-agnostic. Football rules live in a config file that takes `season`
  as a parameter, so adding seasons later does not require rewriting the engine.
- Code computes the numbers (SCR headroom, budget, GBE points, reserve price, ledger).
- The LLM (ADK/Gemini) only does judgement: target choice, offer within [floor, ceiling],
  rationale. Output is validated JSON, 1 retry, then a rule-based fallback.
- A rule-based baseline (no LLM) is kept for ablation. Temperature 0, response cache,
  decision log.
- Negotiation: sealed offers from 2-3 clubs, seller picks top 2 using a code-computed
  reserve price, up to 2 counter rounds, player decision, final SCR/GBE check.
- MVP is an advisor for one deal (permanent or loan, squad needs, SCR impact, success
  probability from N simulations).
- Scope: v1 runs one season (the 2026/27 window). The schema is ready for more seasons.

## Data
- Source: Kaggle `davidcariboo/player-scores` (Transfermarkt scrape, CC0), loaded with kagglehub.
- Frozen snapshot: SNAPSHOT = 2026-07-11. Upstream updates are paused. Valuations end 2026-06-12.
- Database `transfer_window_ai.db` is built by `src/phase1_build_db.py` and is git-ignored.
- Tables: clubs, transfers, players, valuations, meta, club_finance (from the seed CSV).
- The 20 clubs in the DB are the 2025/26 Premier League clubs (last_season = 2025).
- The DB has no player wages, only transfer fees (EUR) and market values.
- Known limitation: current squads are approximated (mean abs diff 3.55 players vs official).
- Rejected source: Nefedov "Global Football Transfer Market 2010-2026" (mostly synthetic).
  Mention this in the README.
- Dataset checksums and seed checksums are in `DATA_VERSION.md`.

## Finance seed
- Files: `seed/club_finance_seed.csv` (one row per club), `seed/club_finance_sources.csv`
  (one row per source, machine-readable), `seed/SOURCES.md` (human-readable mirror).
- Load and validate: `python src/phase2_load_seed.py`. Cross-check against sources:
  `python src/phase2_check_sources.py`. Rows with confidence `high` need at least two sources
  within tolerance (0.6 or 0.5%, whichever is larger) for every metric.
- Primary key is (club_id, fiscal_year). Sanity range for wages/revenue is 0.30 to 1.50.
- Columns: club_id, club_name, fiscal_year, revenue_gbp_m, wage_bill_gbp_m,
  profit_on_player_sales_gbp_m (not net player trading), player_amortisation_gbp_m,
  uefa_next_season, in_pl_next_season, source_url, confidence, notes, revenue_basis
  (PL or CHAMPIONSHIP), period_months (12, or 13 for Wolves). Column names do not
  hard-code a season on purpose. Impairment is only noted in `notes`, not a column.
- Fiscal year is 2024/25 for all 20 clubs. These accounts were published between Dec 2025 and
  May 2026. FY 2025/26 accounts are expected from late 2026 to early 2027, so a new row per
  club can be added later without overwriting these.
- Baseline SCR cost for a club = published wages + published amortisation (FY 2024/25). A deal
  advisor only adds the incremental cost of the incoming player and removes the outgoing one.
- Method: check at least two independent sources (Swiss Ramble, PSRwatch, club releases).
  Use the more precise figure when sources agree. Confidence is the weakest field in the row.
- All figures come from secondary sources and were not checked against Companies House.
- Lessons: Man City wages are 408m. The 579m from The Esk was rejected. Brighton revenue is
  222m (242m wrongly included property income). Tottenham wages are ambiguous (256m football
  company vs about 402m group).
- Leeds, Sunderland and Burnley played FY 2024/25 in the Championship, so their revenue is not
  representative of Premier League income (confidence low). The rules engine needs a separate
  Premier League revenue estimate for them.
- Wolves reports a 13-month period. Arsenal, Liverpool and West Ham end the year on 31 May,
  Burnley and Sunderland on 31 July, all others on 30 June.

## Rules (2026/27)
Source status: ALL items below come from secondary sources (premierleague.com explainer,
Farrer, The Esk 16 May 2026). The binding Handbook Section E text has NOT been checked.
Never present these as official figures.

### Squad cost ratio (SCR)
- Squad costs = player wages + head-coach wages + player amortisation + player impairment
  + agents' fees. (confidence: medium)
- Excluded: non-playing staff, assistant/other coaches, academy, women's team. (medium)
- Amortisation is capped at 5 years from 2026/27. (LOW-MEDIUM: single source, The Esk)
- Adjusted revenue = matchday + broadcast + commercial income (including non-football
  stadium events) + net profit/loss on player sales. Asset sales (stadium, training ground,
  women's team) are excluded. (medium)
- No averaging of player-sales profit was found in any source. Config flag
  `player_sales_avg_years` exists, default 1.

### Thresholds
- Standard clubs: green 85%, red 115%.
- Clubs in ANY UEFA competition: green 70%, red 100% (NOT 115%).
- Multi-year allowance: 30% (levy applies, from 2027/28; shadow year 2025/26).
- Carry-forward: a breach reduces next season's allowance; it rebuilds +10% per compliant
  season up to 115%.
- Red breach sanction: 6-point deduction + 1 point per £6.5m over.
- Compliance test 1 March; accounts confirmation test in June.
(all medium)

### GBE and windows
- GBE: 15 points auto-qualify, at most 4 ESC players. The points table is unverified, so a
  proxy (international caps) is used for now.
- Windows: summer 15 Jun to 1 Sep 2026, January 1 Jan to 1 Feb 2027.

### Known data limitations (kept for cross-checking)
- Seed `wage_bill_gbp_m` is the club-wide wage bill, so it OVERSTATES squad costs. Config
  factor `wage_scope_factor` (default 1.0) is for sensitivity analysis only, never
  presented as fact.
- Impairment is NOT in the seed (notes only, e.g. Arsenal ~15, Villa ~6.5). Baseline treats
  it as 0. Known understatement of squad costs.
- FY2025/26 accounts are not published yet. Backtests must be point-in-time.

### FX
- Constant 0.86 GBP per EUR (confidence: medium).
- Basis: Bank of England spot via poundsterlinglive. Latest 0.8475 (8 Oct 2026); 2026 range
  0.8468 to 0.8783; our own day-weighted YTD mean about 0.863 (derived by us, not a
  published figure).
- Fixed rate, so backtests are not point-in-time on FX.

### Still to verify
- Handbook Section E binding text (all rules above).
- GBE points table.
- 5-year amortisation cap (needs a second source).

## Evaluation rules
- No leakage: use point-in-time valuations, anonymise names in prompts, backtest only on
  complete windows (up to January 2026). Summer 2026 is incomplete.
- Backtests must also be point-in-time for finance: use only the accounts that were public at
  the time of the window being tested.
- Metrics: fee MAPE, loan vs permanent classification, top-k buyer recall, constraint
  compliance rate, LLM vs baseline.
- Fee, loan and buyer-recall metrics need no per-season finance data. Any backtest that checks
  SCR feasibility of past deals would need finance for that season.

## Decisions made
- Keep all 20 DB clubs in the seed. West Ham, Burnley and Wolves (relegated after 2025/26)
  have `in_pl_next_season = false`: not buyers under Premier League SCR, still valid sellers.
- Do not add the three newly promoted clubs in v1. Check later whether the dataset has enough
  data for them (v2) and document this limitation in the README.
- The config will hold `revenue_growth_assumption = 0.0` (FY 2024/25 to 2026/27). No growth
  adjustment in v1. The assumption must be stated in the README.
- v1 runs one season. The schema and config are ready for more seasons.
- Impairment kept as notes only (no seed column); baseline uses 0 and documents the
  understatement.
- Config has `wage_scope_factor` (default 1.0, sensitivity only) and
  `player_sales_avg_years` (default 1).
- FX fixed at 0.86 GBP/EUR (medium confidence).
- Incoming agent fees are spread with the transfer fee over min(contract years, cap).
  Unsourced assumption.

## Status
- Phase 1 (data foundation): done and reproducible.
- Phase 2 (finance seed): done for all 20 clubs, with a structured sources table (46 rows).
  Both check scripts pass. Weakest rows: Fulham and West Ham (single source), Leeds, Sunderland
  and Burnley (Championship revenue), Tottenham and Crystal Palace (source disagreement).
- Rules verification: done at secondary-source level (see Rules). Open items listed under
  "Still to verify".
- Next: Phase 3, the rules engine with unit tests. First module is an SCR calculator
  (adjusted revenue, 85% or 70% limit with red 115% or 100%, baseline squad cost from the
  seed, headroom). Then negotiation engine plus baseline, ADK LLM layer, evaluation/backtest,
  advisor, README and demo.