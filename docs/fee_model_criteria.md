# Fee model: pre-registered evaluation criteria

Registered on 2026-10-15, BEFORE any fee-model code or evaluation exists. This commit will be
tagged `fee-criteria-v1`. If the model fails, the result is reported as a failure. The
criteria are not changed after seeing results; any change must be a new commit with a reason
and a new tag (`fee-criteria-v2`), never an edit of v1.

## What was examined before writing this

Only the schema, data availability and row counts per window (listed below). **No statistic
of transfer fees against market values (ratios, errors, correlations or plots) was examined
before writing these criteria.**

## Purpose

Predict the transfer fee of a permanent transfer from point-in-time data. The prediction is
used as the seller's reserve price in the negotiation engine (step (c), part 3). It is an
estimate, never a fact.

## Data

Source: the project database built by `src/phase1_build_db.py` (snapshot 2026-07-11).

- **Transfers:** rows of `transfers` with `transfer_fee > 0`, using the raw `transfer_fee`
  column only. `transfer_fee_clean` is never used (it fills missing fees with market values).
- **Market value:** point-in-time, from the `valuations` table: the latest valuation with
  `date <= transfer_date` for that player. Values from after the transfer date are never
  used. (Observed while checking availability: `transfers.market_value_in_eur` equalled this
  value on all 1,639 rows where both exist; the `valuations` table is used regardless.)
- **Age at transfer:** `(transfer_date - players.date_of_birth)` in days / 365.25.
- **Position:** `players.position` (Goalkeeper, Defender, Midfield, Attack).

### Filters, applied in this order (every exclusion is counted in the results)

1. `transfer_fee > 0` (raw column).
2. A point-in-time valuation exists and is positive.
3. The valuation is at most 365 days older than the transfer date.
4. Loan-fee heuristic: exclude a transfer A -> B if the same player moves B -> A within
   1 to 400 days after it (probably a loan fee, not a permanent fee).
5. Window assignment by calendar month of `transfer_date`: January to March = winter window
   W(year); June to October = summer window S(year). Transfers in other months are excluded.
6. Summer 2026 (S2026) is excluded: the window is incomplete at the snapshot.
7. Rows with a missing date of birth or position are excluded.

Availability observed before registration (counts only): 1,675 transfers with fee > 0; 33
without a point-in-time valuation, 30 with a valuation older than 365 days, 54 matching the
loan-fee heuristic; all fee > 0 transfers fall in months 1, 2, 3, 6, 7, 8, 9 or 10; date of
birth present for all.

## Time-based split (complete windows only, up to January 2026)

| Set | Windows | Expected rows |
|---|---|---|
| Train | all windows up to and including W2024 | 1,154 |
| Calibration | S2024 and W2025 | 185 |
| Test | S2025 and W2026 | 206 |

The evaluation script must reproduce these counts. If any count differs, it stops before
computing any metric and reports the discrepancy.

Rows are ordered by (`transfer_date`, `player_id`) so that every computation is deterministic.

## Target

`y = ln(fee / MV_pit)`. Fee and market value are both in EUR, so no currency conversion is
involved.

## Features (few, all from the database)

- `ln(MV_pit)`
- age at transfer and age squared
- position: three dummies, reference category Midfield
- winter-window dummy (1 for W windows)

Not used:
- **Contract years remaining:** `players.contract_expiration_date` is the contract at the
  2026 snapshot, not the contract in force at the transfer date. Using it would be wrong and
  would leak future information.
- Whether the buyer is one of the 20 database clubs (it overlaps with how the sample was
  selected).
- A time trend.

## Model

Ordinary least squares on the train set (`numpy.linalg.lstsq`, with an intercept). pandas
and numpy only; no randomness in fitting. The model is fitted on train only and is not refit
on train + calibration.

Point prediction: `fee_hat = MV_pit * exp(y_hat)`.

## 80% prediction interval (split conformal)

- Residuals on the calibration set: `r_i = |y_i - y_hat_i|`, with the train-fitted model.
- `q` = the k-th smallest residual, `k = ceil((n_cal + 1) * 0.8)` (k = 149 for n_cal = 185).
- Interval for a test transfer: `[MV_pit * exp(y_hat - q), MV_pit * exp(y_hat + q)]`.

## Metrics (on the test set)

- APE per transfer: `|fee_hat - fee| / fee`.
- MdAPE: median APE. MAPE: mean APE (reported only).
- Coverage: share of test fees inside the 80% interval. Mean interval width (EUR m).

## Baselines (point predictions only)

- B1: `fee_hat = MV_pit`.
- B2: `fee_hat = MV_pit * m`, where `m` is the median of `fee / MV_pit` on the train set.

The best baseline is fixed once: the one of B1 and B2 with the lower point MdAPE on the full
test set (the stronger comparison). It is not re-selected anywhere else, including inside the
bootstrap below.

## Acceptance (both are reported; each has its own consequence)

### A: point accuracy

Both conditions are required:
1. `MdAPE_model / MdAPE_best_baseline <= 0.90`. The 0.90 (a 10% relative improvement) is an
   unsourced practical-significance choice: a smaller gain would not justify a model over a
   one-number baseline.
2. Paired bootstrap over test transfers: 2,000 resamples of the test rows with replacement,
   `numpy.random.default_rng(20261015)`. For each resample compute
   `MdAPE_model - MdAPE_best_baseline` on the same rows, always against the baseline fixed
   above (the best baseline is NOT re-selected within each resample). The upper bound of the
   95% percentile interval (97.5th percentile) must be < 0.

### B: interval calibration

Decision rule (registered constants, used exactly as written): `0.72 <= coverage <= 0.88`.

Derivation (explanatory only; the decision rule uses 0.72 and 0.88, not the unrounded
values below): coverage is a proportion near p = 0.8, so a single sample of size n has standard
error `sqrt(p(1 - p) / n) = sqrt(0.16 / n)`. Both the calibration quantile and the test
coverage are estimated from finite samples, so their errors are combined:

    SE = sqrt(0.16 / n_cal + 0.16 / n_test) = sqrt(0.16 / 185 + 0.16 / 206)
       = sqrt(0.000865 + 0.000777) ~ 0.0405

    0.80 +/- 2 * SE = [0.719, 0.881], rounded to [0.72, 0.88].

## Consequences

- **A fails:** the negotiation engine does not use the model. The seller's reserve price is
  `MV_pit * m` (the B2 baseline), labelled "fallback: market value x training median ratio".
- **B fails:** the intervals are reported but not used by the negotiation engine.
- A failure is reported as a failure; the criteria are not loosened afterwards.

## Sensitivity report (not a criterion)

The model is also refitted with the train set restricted to transfers from 2015-01-01 onward
(same calibration and test sets), and the same metrics are reported. This report never
decides acceptance.

## Reproducibility

The evaluation script (step (c), part 2):
- refuses to run on a dirty working tree (including untracked files);
- verifies that the tag `fee-criteria-v1` exists and that this file is unchanged since that
  tag;
- refuses to overwrite an existing results file and writes it atomically;
- records the code commit hash, the tag commit, the dataset checksums and snapshot from the
  database `meta` table, the row counts and every exclusion count, the seed, and the Python,
  pandas and numpy versions.

## Limitations (stated in every report of the result)

- Only transfers with a recorded fee > 0 are modelled: the prediction is conditional on a fee
  being paid.
- Selection bias: the database only holds transfers involving the 20 clubs of the 2025/26
  Premier League.
- Position comes from current data, not from the transfer date.
- No point-in-time contract length is available, although it matters for fees.
- Fees may inflate over time; there is no time trend, so the model may under-predict recent
  fees.
- The loan-fee filter is a heuristic; some loan fees may remain and some permanent fees may be
  removed.
- MAPE is dominated by small fees, which is why MdAPE is the primary metric.
- The test set is small (206 transfers), so all results carry sampling noise.
