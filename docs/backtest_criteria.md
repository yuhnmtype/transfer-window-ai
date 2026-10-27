# Window-level backtest of the fallback fee estimate: criteria

**Frozen** at the git tag `backtest-criteria-v1`. After the tag, the criteria are not changed; any change is a new commit with a reason and a new tag (`backtest-criteria-v2`), never an edit of v1.

## What was examined before writing this

Only row counts per transfer window, computed with the filters of `src/fee_model.py`
`build_dataset` (see Data). No per-window statistic of fees against market values (APE,
MdAPE, ratios, shares) has been computed or seen. The aggregate test-split result of the
fallback is already public in `results/fee_model_eval.json` (fee-criteria-v1 evaluation) and in
`docs/results.md`. Row-level fallback APEs of the 206 test rows (windows S2025 and W2026) are
already public in `results/llm_eval_blind_run1.json`, so the numbers of these two windows are
not blind; no per-window statistic has been computed from them.

## Purpose

A descriptive, window-level stability table for the registered fallback fee estimate

    fee_hat = m x MV_pit

where `MV_pit` is the point-in-time market value and `m` is the training median ratio,
`train_median_ratio_m` in `results/fee_model_eval.json` (1.25). `m` is read from that file and
is never refitted. The word backtest in the tag name only means that past windows are
examined; this is not a predictive validation.

This table is **not** a validation of the SCR engine, has **no pass/fail criterion**, and has
**no consequence** for any default (the fallback stays the seller reserve and the buyer
valuation as registered in `fee-criteria-v1`).

## Data

- Rows: exactly the rows returned by `build_dataset` in `src/fee_model.py`, with the same seven
  filters as `docs/fee_model_criteria.md` (raw `transfer_fee > 0`; positive point-in-time
  valuation at most 365 days old; loan-fee heuristic; window by month; S2026 excluded; date of
  birth and position present). `transfer_fee_clean` is never read.
- Windows: W(year) = transfer months 1 to 3, S(year) = months 6 to 10.
- Windows in the table: S2023, W2024, S2024, W2025, S2025, W2026.
- S2026 is excluded from interpretation: only its row count is reported, computed with filters
  1 to 5 and 7 (filter 6 removes S2026 by design). The latest `transfer_date` in the database
  is 2026-07-01 (snapshot 2026-07-11), so the window is incomplete.

Upstream status (external statement, not independently verified by this repository): the
Kaggle dataset page text, copied by the project owner on 2026-10-27, reads: "Updates are
paused. This dataset is current to 6 July 2026 ... games contains nothing after 2026-07-06,
appearances nothing after 2026-06-28, player_valuations nothing after 2026-06-12, and 2026/27
squads are not covered" (details:
https://github.com/dcaribou/transfermarkt-datasets/discussions/383). This is consistent with
the maximum dates in our database, read on 2026-10-27: `MAX(transfers.transfer_date)` =
2026-07-01 and `MAX(valuations.date)` = 2026-06-10.

Expected row counts (from the scoping count; the script must reproduce them and stops before
computing any statistic if any count differs):

| Window | Registered role (fee-criteria-v1) | Expected rows |
|---|---|---|
| S2023 | train | 147 |
| W2024 | train | 16 |
| S2024 | calibration | 153 |
| W2025 | calibration | 32 |
| S2025 | test | 177 |
| W2026 | test | 29 |
| S2026 | not registered (count only) | 17 |

Role labels:
- **train:** `m` was fitted on the train set, so these windows are in-sample for the fallback.
- **calibration:** not used to fit `m`.
- **test:** the registered test windows (206 rows), already used in the fee-model and LLM
  evaluations.

## Statistics per window (S2023 to W2026)

- `n`: number of rows.
- APE per row: `|fee_hat - fee| / fee`, with fee and market value both in EUR.
- MdAPE of the fallback: median APE.
- 95% bootstrap interval of the MdAPE, 2,000 resamples with replacement:
  1. One generator, `rng = numpy.random.default_rng(20261027)`, is created once.
  2. Windows are processed in the fixed order S2023, W2024, S2024, W2025, S2025, W2026.
  3. For each window with `n` rows, one index matrix is drawn with
     `rng.integers(0, n, size=(2000, n))`; each of its 2,000 rows is one resample, and the
     median APE of each resample row is computed.
  4. The interval is `np.percentile(resampled_medians, [2.5, 97.5])` with linear
     interpolation.

  Each interval is a separate 95% interval; there is no adjustment for the six windows.
- Median of `fee / MV_pit`.
- Share of rows with APE <= 0.25.

## Interpretation rules (written before any result exists)

- The table is descriptive only.
- Windows with `n < 30` are flagged **low precision** (on the expected counts: W2024 and
  W2026).
- No claim of stability or drift is made beyond what the bootstrap intervals show;
  overlapping intervals are not reported as evidence of stability, and non-overlapping
  intervals are not reported as evidence of a cause.
- No parameter (`m`, the 80% band of `fee-criteria-v1`) may be changed because of this table.
- The test windows are reused for description only; this changes no registered conclusion of
  the fee-model or LLM evaluations.
- Train windows are in-sample for `m` and must not be read as out-of-sample performance.

## Non-goals and reasons

- **No SCR check and no buyer-recall metric.** The finance seed holds FY 2024/25 accounts
  only, and they were published between December 2025 and May 2026 (`DATA_VERSION.md`), so
  they are not point-in-time for any closed window in this table. The repo does not record
  filing dates per club.
- **No forward validation on S2026.** Upstream dataset updates are paused (snapshot
  2026-07-11). A forward validation would need a new criteria version registered before any
  new data is pulled.

## Execution rules (for the script, written only after the freeze)

The script:
- refuses to run on a dirty working tree (including untracked files);
- refuses to run unless the tag `backtest-criteria-v1` exists and both this file and
  `results/fee_model_eval.json` are unchanged since that tag;
- refuses to run if the evaluation script itself exists at the commit of the tag (the script
  must be written after the freeze);
- refuses to run if any expected row count differs;
- writes `results/backtest_windows_run1.json` atomically and never overwrites it;
- records provenance: code commit, tag commit, data checksums and snapshot from the database
  `meta` table, the checksum of `results/fee_model_eval.json`, the value of `m` read, the
  seed, and the Python, pandas and numpy versions.

## Limitations (stated in every report of the result)

- Descriptive only; window sizes are small, and two windows are below 30 rows.
- Inherits the limitations of `docs/fee_model_criteria.md`: fee > 0 only (conditional on a fee
  being paid), selection bias (transfers involving the 20 clubs of the 2025/26 Premier League
  only), position from current data, no point-in-time contract length, possible fee
  inflation over time, heuristic loan filter.
- The train windows are in-sample for `m`; the test windows have already been used.
- Point-in-time market values can be up to 365 days old.
