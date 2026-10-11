# Wage model: pre-registered evaluation criteria

Registered on 2026-10-11, BEFORE any wage-model code exists. If the model fails, the result
is reported as a failure. The criteria below are not changed after seeing results; any change
must be a new commit that states the reason.

## Purpose

The DB has no player wages. The wage model estimates per-player wages by allocating a club's
wage pool across its squad by market value. Its output is a labelled assumption with
confidence `low`, never a fact. Background: `seed/SOURCES.md`, "Wage model research".

## Model under test

- `wage_i = pool * MV_i^alpha / sum_j(MV_j^alpha)`, within one club only.
- `pool = wage_bill_gbp_m * wage_scope_factor` from `club_finance` (FY 2024/25), with
  `wage_scope_factor` fixed at 1.0. It is never tuned.
- Squad: players with `is_current_squad = 1` and a non-null, positive `market_value_in_eur`
  at the snapshot (2026-07-11). Players without a market value are excluded.
- Estimated top weekly wage: `top_weekly_k = max_i(wage_i) * 1000 / 52` (GBP thousands per
  week; the pool is GBP millions per year).
- Alpha grid: 0.30 to 1.50, step 0.05 (25 values). If the selected alpha lies on the edge of
  the grid, it is reported as "at the boundary". The grid is not widened after seeing results.

## Reference

- File: `seed/wage_reference.csv`, checked by `python src/phase3_check_wage_reference.py`.
- Per club: the weekly gross base wage of the club's highest-paid player, as an interval
  `[ref_min, ref_max]` across sources. It is not tied to a named player.
- Sources (pages read as raw page text on 2026-10-11; figures copied exactly):

| Source | Published | Season covered | Confidence |
|---|---|---|---|
| [Planet Football](https://www.planetfootball.com/premier-league/every-premier-league-club-highest-paid-player-in-2025-26-xhaka-joins-haaland-on-elite-list) | 14 Aug 2025 | 2025/26 | low |
| [Football365](https://www.football365.com/news/every-premier-league-club-highest-earner-beat-paid-player) | 18 Aug 2026 | 2026/27 | low |

- GiveMeSport (2023-24 data) is excluded as outdated.
- Both lists use Capology figures (base salary, no bonuses), so they are not independent.
- If only one source exists (West Ham and Wolves are not in the 2026/27 list),
  `ref_min = ref_max` and `n_sources = 1`.
- Arsenal: Football365 says "in excess of" 300, so `max_is_lower_bound = true`.
- Tottenham: Football365 gives 240 (Van de Ven, from reports) and says its own source has 200
  (Tonali). 240 is used.
- Football365's subtitle says three players earn "over a million a week". This contradicts
  its own body text and is ignored.
- Excluded clubs: Leeds, Sunderland and Burnley (`revenue_basis = CHAMPIONSHIP`: their
  wage pools are Championship pools). Coventry, Hull and Ipswich are not in the DB.

## Fit/test split

- Eligible: the 17 clubs with `revenue_basis = PL`.
- Rule: sort by `revenue_gbp_m` descending (ties by `club_id`), then assign fit, test, fit,
  ... in turn. This gives 9 fit and 8 test clubs. The rule uses revenue only, not the
  reference values.
- The result is frozen in the `split` column; the check script recomputes the rule and fails
  on any difference.
- Fit: Liverpool, Arsenal, Tottenham, Aston Villa, West Ham, Nottingham Forest, Crystal
  Palace, Bournemouth, Wolves.
- Test: Manchester City, Manchester United, Chelsea, Newcastle, Brighton, Everton, Fulham,
  Brentford.

## Error metric and pass rule

- Log-distance: `d = 0` if `ref_min <= est <= ref_max`, otherwise
  `d = |ln(est / nearest bound)|`.
- Pass for a club: `ref_min / 1.25 <= est <= ref_max * 1.25`, which is the same as
  `d <= ln(1.25) ≈ 0.223`.
- Lower-bound rows (`max_is_lower_bound = true`): the interval is `[ref_min, +infinity)`,
  so `d = 0` if `est >= ref_min`, otherwise `ln(ref_min / est)`. Pass if
  `est >= ref_min / 1.25`.

## Procedure (primary result)

1. For every alpha on the grid, compute `mean(d)` over the 9 fit clubs. Select the alpha with
   the smallest mean; on a tie, take the smaller alpha.
2. With that alpha, count passes on the 8 test clubs (primary result) and on the fit clubs.
3. Baselines on the test clubs:
   - Equal split: every player gets `pool / n`, so `top_weekly_k = pool / n * 1000 / 52`.
   - `alpha = 1` (pure proportional allocation): reported only, no threshold.
4. Report the test pass rate with a Wilson 95% interval (z = 1.96). For reference, with
   n = 8: 6/8 gives about [0.41, 0.93] and 8/8 about [0.68, 1.00].
5. Reverse direction (2-fold cross-validation check): select alpha on the test clubs and
   count passes on the fit clubs, also with a Wilson 95% interval. This is reported only.
   The registered primary result stays the original direction.

## Acceptance (all conditions required)

- A. Test pass count at least 6 of 8 (75%).
- B. The fitted model passes strictly more test clubs than the equal-split baseline.

If A or B fails, the model has failed. The advisor then does NOT use the wage model by
default: it falls back to user-supplied wages, and the README states the failure.

## Reproducibility

The results file of the evaluation must record:
- the LF-normalised MD5 of `seed/club_finance_seed.csv` and of `seed/wage_reference.csv`,
- the data snapshot (`snapshot_date` and dataset MD5s from the DB `meta` table),
- the git commit hash of the code that produced the results.

## Limitations (stated in every report of the result)

- n is small (8 test clubs), so the Wilson interval is wide.
- The reference is itself a Capology-derived estimate. A pass means "consistent with public
  estimates", not "accurate".
- The metric uses only the club's top weekly wage, so alpha is effectively one parameter fitted
  to one statistic per club. It says nothing about the rest of the squad.
- The pool is accounting wages (club-wide staff, bonuses and other costs), while the reference
  is base salary only. Alpha absorbs this gap, so it must not be read as the real wage
  concentration. `wage_scope_factor` is not tuned to bridge the gap.
- Time mismatch: pool FY 2024/25, squad and market values at the 2026 snapshot, reference
  from the start of 2025/26 and 2026/27. The interval mixes two seasons and often two different
  players.
- An age multiplier is out of scope here (possible later ablation).
