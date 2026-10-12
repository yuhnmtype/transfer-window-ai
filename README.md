# Transfer Window AI

A rules engine and advisor for Premier League transfer deals under the 2026/27 squad cost ratio (SCR) rules, built on real Transfermarkt data. A multi-agent negotiation layer (Google ADK + Gemini) is planned.

> **Status: work in progress.** Done: data foundation, finance seed, SCR calculator, a rule-based one-deal advisor, and a pre-registered evaluation of a wage model (which failed, see below). Not started: negotiation engine, LLM layer, backtest.

All figures are illustrative. Finance data is FY 2024/25 from secondary sources and the rules come from secondary sources. **Nothing here is a real-world conclusion about any club.**

## What it does

Given a buying club and a hypothetical permanent deal (fee, wage, contract length, optional outgoing player), the advisor computes the club's SCR before and after the deal, the threshold that applies (green 85% / red 115%, or 70% / 100% for clubs in UEFA competitions), the headroom, a wage sensitivity table, and the maximum wage that keeps the club within the green or red limit.

Design rule: **code computes the numbers; an LLM will only be used for judgement** (target choice, offers, rationale), with validated JSON output and a rule-based baseline for ablation.

## Quick start

Requires Python 3.11+ for `tomllib`; the project has only been tested on Python 3.15.

```bash
git clone https://github.com/yuhnmtype/transfer-window-ai.git
cd transfer-window-ai
pip install -r requirements.txt

python src/phase1_build_db.py        # downloads the dataset via kagglehub and builds the SQLite DB
python src/phase2_load_seed.py       # validates and loads the finance seed
python src/phase2_check_sources.py   # cross-checks the seed against the source table
python -m pytest                     # unit tests

python src/advise_cli.py --list-clubs
python src/advise_cli.py --club-id 1148 --fee 20 --fee-currency GBP --wage-gbp-m 3 --contract-years 4
```

Example output (abridged; a **hypothetical** deal for Brentford, not a real transfer):

```text
SCR advice: Brentford FC (club_id 1148), season 2026/27, finance FY 2024/25
Thresholds: No UEFA competition next season: green 85%, red 115%

                          Before     After
Adjusted revenue          200.30    200.30
Squad cost                178.60    186.60
SCR ratio                  89.2%     93.2%
Status                     amber     amber
Headroom to green          -8.35    -16.35   (change -8.00)

Maximum incoming wage (Only the incoming wage changes; all other inputs stay the same.)
  to stay green: none (even a zero wage exceeds this limit)
  to stay at or below red: 46.74 GBP m/yr (898.9 GBP k/week)
```

The full output also prints the incoming-cost line, the wage sensitivity table, the warnings, the amber note and the standing caveats.

"Amber" is our own label for the band between green and red, not an official term.

## Data and provenance

- **Players, transfers, valuations:** Kaggle `davidcariboo/player-scores` (Transfermarkt scrape, CC0), frozen snapshot 2026-07-11. Checksums are in `DATA_VERSION.md`.
- **Club finances (FY 2024/25):** `seed/club_finance_seed.csv`, built from secondary sources (Swiss Ramble, PSRwatch and others) and cross-checked against `seed/club_finance_sources.csv` (46 source rows). Not checked against Companies House. Every seed row has a source URL and a confidence level; per-figure sources are in `seed/club_finance_sources.csv`.
- **Rejected source:** the "Global Football Transfer Market 2010-2026" dataset (Nefedov) was rejected because it appears to be mostly synthetic.
- The database contains the 20 clubs of the 2025/26 Premier League. The three newly promoted clubs are not included yet.

## Evaluation discipline: a pre-registered wage model that failed

The data has no player wages, so I tested a simple model that splits each club's wage bill across its squad in proportion to market value. The acceptance criteria, the fit/test split and the reference data were committed **before** any model code existed and frozen with the git tag `wage-criteria-v1`; the evaluation script refuses to run on a dirty tree and records the code commit and data checksums.

Result (`results/wage_model_eval.json`): **not accepted.** The selected exponent sat at the lower edge of the grid, the model passed 3 of 8 test clubs against 5 of 8 for a naive equal split, and both acceptance conditions failed. Consequently the advisor does not estimate wages: they are user inputs, and any simulation will use clearly labelled scenarios. My untested hypothesis is that the club-wide wage bill is too large relative to player wages; testing it needs new reference data and a new pre-registration.

## Limitations

- Finance data is FY 2024/25 and from secondary sources; no revenue growth is applied.
- The seed wage bill is club-wide (includes non-playing staff), which overstates squad costs; player impairment is counted as 0; profit on the outgoing player's sale is ignored.
- Leeds, Sunderland and Burnley played FY 2024/25 in the Championship, so their revenue is not representative of Premier League income.
- Rules (squad cost definition, thresholds, 5-year amortisation cap) come from secondary sources; the Premier League Handbook text was not checked.
- Fixed EUR to GBP rate of 0.86 (medium confidence).
- Current squads are approximated from the dataset (mean absolute difference of 3.55 players against official squads).
- Permanent deals only; loans and the GBE (work permit) rules are not implemented.

## Roadmap

1. Outgoing-player amortisation helper (approximation).
2. Negotiation engine and rule-based baseline.
3. LLM layer (Google ADK + Gemini) and an ablation against the baseline.
4. Point-in-time backtest without leakage.

## License

Code: MIT, see `LICENSE`. The Kaggle dataset is CC0. Finance figures were compiled from the public sources listed in `seed/`.