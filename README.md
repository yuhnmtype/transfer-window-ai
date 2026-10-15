# Transfer Window AI

A rules engine and advisor for Premier League transfer deals under the 2026/27 squad cost ratio (SCR) rules, built on real Transfermarkt data. A multi-agent negotiation layer (Google ADK + Gemini) is planned.

> **Status: work in progress.** Done: data foundation, finance seed, SCR calculator, a rule-based one-deal advisor, an outgoing-player amortisation helper (approximation), and a pre-registered evaluation of a wage model (which failed, see below) and of a fee model (point accuracy not accepted, fallback adopted; the 80% interval is calibrated but wide). Not started: negotiation engine, LLM layer, backtest.

All figures are illustrative. Finance data is FY 2024/25 from secondary sources and the rules come from secondary sources. **Nothing here is a real-world conclusion about any club.**

## What it does

Given a buying club and a hypothetical permanent deal (fee, wage, contract length, optional outgoing player), the advisor computes the club's SCR before and after the deal, the threshold that applies (green 85% / red 115%, or 70% / 100% for clubs in UEFA competitions), the headroom, a wage sensitivity table, and the maximum wage that keeps the club within the green or red limit.

For the outgoing player, the advisor can look up the annual amortisation from the transfer history (`--out-player-id`) or take a user-supplied figure (`--out-amortisation-gbp-m`). The outgoing wage stays a user input.

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

## Fee model evaluation: pre-registered, failed, fallback adopted

The negotiation engine needs a seller reserve price, so I tested a small fee model (OLS on `ln(fee / point-in-time market value)` with market value, age, position and window type). As with the wage model, the data filters, time-based split, metrics and acceptance rules were committed before any model code and frozen with the git tag [`fee-criteria-v1`](https://github.com/yuhnmtype/transfer-window-ai/tree/fee-criteria-v1); see [`docs/fee_model_criteria.md`](docs/fee_model_criteria.md).

Result (`results/fee_model_eval.json`, 206 test transfers from summer 2025 and January 2026):

- **Point accuracy (criterion A): not accepted.** The learned model did not beat a simple market-value-ratio baseline (not shown to be better; the paired bootstrap interval includes zero). Median absolute percentage error: model 0.310, market value x training median ratio (1.25) 0.250; ratio 1.242 against a required 0.90 or lower; paired bootstrap 95% interval of the difference [-0.016, 0.093].
- **80% prediction interval (criterion B): accepted.** Coverage 0.801, within the registered band [0.72, 0.88]. The interval is wide (mean width 34.5m EUR), so it is calibrated but carries little information.

Consequently the seller reserve uses the registered fallback, market value x 1.25, labelled as a fallback. The interval is kept only as the allowed band for later adjustments.

## Outgoing-player amortisation (approximation)

Method: the purchase fee (EUR, converted at the fixed rate) is spread over the shorter of the contract length (from the purchase to the current expiry) and the 5-year cap; the remaining book value limits the 2026/27 charge. Loan returns at zero fee are skipped. A missing fee or a missing contract expiry returns "unknown", never 0.

A report-only diagnostic (`src/phase3_diag_amortisation.py`) sums the estimates over each buyer club's current squad and compares them with the seed's FY 2024/25 amortisation. For 14 of 17 clubs the ratio is between 0.58 and 1.25 (median 0.89). The outliers are Aston Villa (0.36), Manchester United (0.55) and Sunderland (4.02; Sunderland's seed is a Championship year). The squad (July 2026) and the seed (FY 2024/25) are different periods, so this is a plausibility check, not a validation. Between 3 and 16 players per club come back as "unknown" and are left out of the sums.

## Limitations

- Finance data is FY 2024/25 and from secondary sources; no revenue growth is applied.
- The seed wage bill is club-wide (includes non-playing staff), which overstates squad costs; player impairment is counted as 0; profit on the outgoing player's sale is ignored.
- Leeds, Sunderland and Burnley played FY 2024/25 in the Championship, so their revenue is not representative of Premier League income.
- Rules (squad cost definition, thresholds, 5-year amortisation cap) come from secondary sources; the Premier League Handbook text was not checked.
- Fixed EUR to GBP rate of 0.86 (medium confidence).
- Current squads are approximated from the dataset (mean absolute difference of 3.55 players against official squads).
- Permanent deals only; loans and the GBE (work permit) rules are not implemented.
- Outgoing amortisation: contract extensions make the estimate for long-held players possibly understated (flagged); whether the 5-year cap applies to older contracts is unverified (flagged when applied); loan fees and free transfers cannot be told apart in the data; historical fees are converted at the fixed 0.86 rate.

## Roadmap

1. Negotiation engine and rule-based baseline.
2. LLM layer (Google ADK + Gemini) and an ablation against the baseline.
3. Point-in-time backtest without leakage.

## License

Code: MIT, see `LICENSE`. The Kaggle dataset is CC0. Finance figures were compiled from the public sources listed in `seed/`.