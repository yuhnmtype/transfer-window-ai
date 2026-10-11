# Transfer Window AI: project context for Claude Code

## Goal
Multi-agent system that simulates and advises Premier League transfer negotiations
under real 2026/27 regulations (SCR, GBE), built on real Transfermarkt data.
Portfolio project for an AI Engineering CV. Later stages use Google ADK + Gemini.

## Working conventions
- Do one step at a time and wait for the owner to confirm before the next step.
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
- Known limitation: current squads are approximated (mean abs diff 3.55 players vs official).
- Rejected source: Nefedov "Global Football Transfer Market 2010-2026" (mostly synthetic).
  Mention this in the README.

## Finance seed (`seed/club_finance_seed.csv`, references in `seed/SOURCES.md`)
- Loaded and validated by `python src/phase2_load_seed.py`. Joins on `club_id`.
  Primary key is (club_id, fiscal_year). Sanity range for wages/revenue is 0.30 to 1.50.
- Columns: club_id, club_name, fiscal_year, revenue_gbp_m, wage_bill_gbp_m,
  profit_on_player_sales_gbp_m (not net player trading), uefa_next_season, in_pl_next_season,
  source_url, confidence, notes. Column names do not hard-code a season on purpose.
- Fiscal year is 2024/25 for all 20 clubs. FY 2025/26 accounts are expected from late 2026,
  so a new row per club can be added later without overwriting these.
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

## Rules (2026/27, verify before relying on them)
- SCR: squad costs (player and head-coach wages, amortised transfer fees, agent fees) must
  not exceed 85% of adjusted revenue, 70% for clubs in UEFA competitions. Extra 30%
  multi-year allowance with a red threshold of 115%. Levies start in 2027/28.
- Adjusted revenue = football revenue + net player-trading profit.
- GBE: 15 points auto-qualify, at most 4 ESC players. The points table is unverified, so a
  proxy (international caps) is used for now.
- Windows: summer 15 Jun to 1 Sep 2026, January 1 Jan to 1 Feb 2027.
- Still to verify: 3-year averaging of player-trading profit in the PL Handbook, the GBE
  points table.

## Evaluation rules
- No leakage: use point-in-time valuations, anonymise names in prompts, backtest only on
  complete windows (up to January 2026). Summer 2026 is incomplete.
- Metrics: fee MAPE, loan vs permanent classification, top-k buyer recall, constraint
  compliance rate, LLM vs baseline.
- Fee, loan and buyer-recall metrics need no per-season finance data. Any backtest that checks
  SCR feasibility of past deals would need finance for that season.

## Decisions made
- Keep all 20 DB clubs in the seed. West Ham, Burnley and Wolves (relegated after 2025/26)
  have `in_pl_next_season = false`: not buyers under Premier League SCR, still valid sellers.
- Do not add the three newly promoted clubs in v1. Check later whether the dataset has enough
  data for them (v2) and document this limitation in the README.

## Status
- Phase 1 (data foundation): done and reproducible.
- Phase 2 (finance seed): done for all 20 clubs. Weakest rows: Fulham and West Ham (single
  source), Leeds, Sunderland and Burnley (Championship revenue), Tottenham and Crystal Palace
  (source disagreement).
- Next: Phase 3, the rules engine with unit tests (SCR calculator, GBE proxy, reserve price,
  squad needs). Then negotiation engine plus baseline, ADK LLM layer, evaluation/backtest,
  advisor, README and demo.