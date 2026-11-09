# Finance verification against Companies House filings

Record of checking finance seed rows (`seed/club_finance_seed.csv`, FY 2024/25) against filed
accounts at Companies House. Units are GBP million. This page records what was found; it does
not change any seed value.

## Club list and status

| Club | Checked | Status |
|---|---|---|
| Tottenham Hotspur | 2026-10-27 | confirmed against consolidated accounts (see `seed/SOURCES.md`) |
| Arsenal FC | 2026-11-09 | confirmed, matches within rounding (group basis) |
| West Ham United | 2026-11-09 | confirmed, matches within rounding (group basis) |
| Newcastle United | 2026-11-09 | confirmed, matches within rounding (group basis); amortisation matches the combined amortisation and impairment figure |
| Brighton & Hove Albion | 2026-11-09 | mixed basis, row unchanged |

- The list was confirmed on 2026-11-09, before any comparison between filings and seed values.
  Aston Villa (initial pick) was replaced by Brighton (pre-declared first backup) at the
  author's discretion, not because of missing filings.
- The remaining 15 clubs are not checked.

## Definitions

- Primary comparison: the filed consolidated (group) accounts. Secondary comparison: the
  filed single-company accounts of the football club company.
- Revenue, primary definition: turnover plus other operating income (the same basis as the
  Tottenham row, "revenue and other income"). Turnover alone is also recorded. Where a filing
  has no separate other operating income line, or its turnover already includes other income,
  the two are equal.
- Wage: total staff costs (total payroll costs for Brighton), the same basis as the Tottenham row.
- Amortisation: amortisation of player registrations, excluding impairment.
- Profit on sales: profit on disposal of player registrations.
- Difference = filing value minus seed value.

## Arsenal FC

Status: confirmed, matches within rounding (group basis).

### Primary: consolidated accounts

Arsenal Holdings Limited, Companies House 04250459, year ended 31 May 2025, filed 2026-02-25.

| Metric | Seed | Filing | Difference |
|---|---|---|---|
| Revenue (group turnover) | 691.0 | 690.998 | -0.002 |
| Wage (total staff costs) | 347.0 | 346.804 | -0.196 |
| Amortisation | 172.0 | 171.627 | -0.373 |
| Profit on sales | 81.2 | 81.237 | +0.037 |

- Group turnover 690.998 excludes the share of joint venture turnover, 1.028. Including it,
  turnover is 692.026.
- Wages and salaries 303.171 (part of total staff costs).
- Amortisation of player registrations excludes impairment of player registrations, 15.241.

### Secondary: single-company accounts

The Arsenal Football Club Limited, Companies House 00109244, year ended 31 May 2025, filed
2026-02-25. Income statement p16, note 3 p23.

| Metric | Seed | Filing | Difference |
|---|---|---|---|
| Revenue (turnover; no separate other operating income line) | 691.0 | 643.289 | -47.711 |
| Wage (total staff costs) | 347.0 | 331.227 | -15.773 |
| Amortisation | 172.0 | 170.786 | -1.214 |
| Profit on sales | 81.2 | 81.137 | -0.063 |

- Turnover 643.289 includes 0.454 player trading.
- Wages and salaries 289.233. Amortisation excludes an exceptional impairment of 15.241.
- The 47.7 revenue gap is group scope, confirmed: the seed matches the consolidated accounts.

## West Ham United

Status: confirmed, matches within rounding (group basis).

### Primary: consolidated accounts

WH Holding Limited, Companies House 05993863, year ended 31 May 2025, filed 2026-02-26.
Income statement p28, notes pp43-44.

| Metric | Seed | Filing | Difference |
|---|---|---|---|
| Revenue (turnover plus other operating income) | 227.6 | 231.448 | +3.848 |
| Revenue (turnover alone) | 227.6 | 227.552 | -0.048 |
| Wage (total staff costs) | 175.9 | 175.893 | -0.007 |
| Amortisation | 99.4 | 99.408 | +0.008 |
| Profit on sales | 20.0 | 19.954 | -0.046 |

- Other operating income 3.896. The seed revenue matches group turnover excluding other
  operating income; the +3.848 under the primary definition is a definition difference only.
- Wages and salaries 152.926.
- Amortisation of intangible fixed assets 99.408. Profit on sales is profit on disposal of
  players.

### Secondary: single-company accounts

West Ham United Football Club Limited, Companies House 00066516, year ended 31 May 2025, filed
2026-02-26. Income statement p28, note 5 p38.

| Metric | Seed | Filing | Difference |
|---|---|---|---|
| Revenue (turnover plus other operating income) | 227.6 | 229.954 | +2.354 |
| Revenue (turnover alone) | 227.6 | 226.058 | -1.542 |
| Wage (total staff costs) | 175.9 | 173.335 | -2.565 |
| Amortisation | 99.4 | 99.408 | +0.008 |
| Profit on sales | 20.0 | 19.954 | -0.046 |

- Other operating income 3.896. Wages and salaries 150.667.

## Newcastle United

Status: confirmed, matches within rounding (group basis); amortisation matches the combined
amortisation and impairment figure.

### Primary: consolidated accounts

Newcastle United Limited, Companies House 02529667, year ended 30 June 2025, filed 2026-03-31.
Income statement p16, notes pp32-33.

| Metric | Seed | Filing | Difference |
|---|---|---|---|
| Revenue (group turnover, includes other income) | 335.0 | 335.322 | +0.322 |
| Wage (total staff costs) | 243.0 | 243.477 | +0.477 |
| Amortisation | 99.9 | 99.502 | -0.398 |
| Amortisation and impairment combined (face of income statement) | 99.9 | 99.868 | -0.032 |
| Profit on sales | 19.9 | 19.875 | -0.025 |

- Group turnover 335.322 includes other income 2.517.
- Wages and salaries 213.185.
- Amortisation 99.502 and impairment 0.366 are shown combined on the face of the income
  statement as 99.868. The split comes from OCR, and the impairment figure is inferred from the
  face total.
- Profit on sales is profit on disposal of players' and staff registrations. Non-player items
  are excluded: profit on disposal of tangible fixed assets 128.975, and of a subsidiary 4.158.

### Secondary: single-company accounts

Newcastle United Football Company Limited, Companies House 00031014, year ended 30 June 2025,
filed 2026-03-31. Income statement p14, operating profit note p25.

| Metric | Seed | Filing | Difference |
|---|---|---|---|
| Revenue (turnover, includes other income) | 335.0 | 323.124 | -11.876 |
| Wage (total staff costs) | 243.0 | 241.344 | -1.656 |
| Amortisation | 99.9 | 99.377 | -0.523 |
| Profit on sales | 19.9 | 19.870 | -0.030 |

- Turnover 323.124 includes other income 2.817. Wages and salaries 211.297.
- Non-player items excluded: profit on disposal of tangible fixed assets 116.568, and of a
  subsidiary 2.761.
- Inconsistency inside this filing, recorded and not resolved: the operating profit note gives
  amortisation of intangibles 99.377 and impairment 0.366, while the face of the income
  statement shows amortisation and impairment combined as 99.833. That differs from
  99.377 + 0.366 = 99.743 by 0.090.

## Brighton & Hove Albion

Status: mixed basis, row unchanged.

### Primary: consolidated accounts

Brighton & Hove Albion Holdings Limited, Companies House 02849319, year ended 30 June 2025,
filed 2026-03-17. Income statement p20, notes pp33-34.

| Metric | Seed | Filing | Difference |
|---|---|---|---|
| Revenue (turnover plus other operating income) | 222.0 | 247.732 | +25.732 |
| Revenue (turnover alone) | 222.0 | 242.672 | +20.672 |
| Wage (total payroll costs) | 165.0 | 164.552 | -0.448 |
| Amortisation | 81.8 | 81.976 | +0.176 |
| Profit on sales | 57.0 | 57.005 | +0.005 |

- Group turnover 242.672: Matchday 27.878, Broadcasting 151.090, Commercial 22.774, Other
  40.930 (the single-company Other is 20.477). Other operating income 5.060.
- Wages and salaries 144.454.
- Profit on sales is the income statement line "Profit on player trading" 57.005. Its
  explanatory note is not yet checked.

### Secondary: single-company accounts

The Brighton and Hove Albion Football Club Limited, Companies House 00081077, year ended
30 June 2025, filed 2026-03-17. Income statement p16, note 5 p26, note 7 p27.

| Metric | Seed | Filing | Difference |
|---|---|---|---|
| Revenue (turnover plus other operating income) | 222.0 | 226.063 | +4.063 |
| Revenue (turnover alone) | 222.0 | 221.063 | -0.937 |
| Wage (total payroll costs) | 165.0 | 158.679 | -6.321 |
| Amortisation | 81.8 | 81.840 | +0.040 |
| Profit on sales | 57.0 | 56.911 | -0.089 |

- Other operating income 5.000. Wages and salaries 139.300. Impairment nil.
- Profit on sales is the income statement line "Player trading" 56.911. Its explanatory note is
  not yet checked.

## Findings

1. Arsenal, West Ham and Newcastle: the seed rows match the filed consolidated accounts within
   rounding. For West Ham, the revenue matches group turnover excluding other operating income;
   under the primary definition (turnover plus other operating income) the difference is
   +3.848, a definition difference only. The gaps to the single-company accounts are group
   scope.
2. Brighton: the seed row mixes bases. Revenue 222.0 matches the single-company turnover
   221.063 (group turnover 242.672, difference +20.672, mostly in the Other category: 40.930 in
   the group against 20.477 in the single company). Wage 165.0 and profit on sales 57.0 match
   the group figures (164.552 and 57.005). Amortisation 81.8 lies between the two (81.840
   single-company, 81.976 group). The seed's own sources table rejects a revenue figure of 242.0
   because it includes about 20 of property income, so excluding that income from revenue
   looks deliberate.
3. The seed treats other operating income inconsistently across clubs. The Tottenham revenue
   (565.3) includes it (revenue and other income 565.266; revenue alone 564.881). The West Ham
   revenue (227.6) matches group turnover without it (227.552; with it, 231.448). This is
   recorded only; nothing was changed.
4. The seed treats impairment inconsistently across clubs. The Arsenal seed amortisation
   (172.0) excludes impairment, as its own seed note says. The Newcastle seed amortisation
   (99.9) matches amortisation and impairment combined (99.868, difference -0.032), not
   amortisation alone (99.502, difference -0.398); this is inferred from the numbers, not
   stated by any source. Recorded only; nothing was changed.

## Effect on the frozen wage-model split

The frozen split (`docs/wage_model_criteria.md`, tag `wage-criteria-v1`) sorts the 17 clubs with
`revenue_basis = PL` by `revenue_gbp_m` and assigns fit, test, fit, ... in turn. If a filing
revenue replaced the seed revenue:

- Brighton at group turnover 242.672 (or 247.732 under the primary definition) would rise
  above West Ham (227.6) and swap fit/test with it. At single-company turnover 221.063 it would
  fall below Nottingham Forest (221.7) and swap with Forest. At 226.063 it would not change rank.
- West Ham and Newcastle would not change rank under any definition, group or single-company.
- Arsenal: the single-company figure 643.289 would put Arsenal below Manchester United (666.5)
  and swap them, but the group figure 690.998, which the seed matches, does not change the rank.

The author saw the effect of both revenue definitions before fixing the primary definition, so
both are recorded here. The seed is unchanged.

## Why the seed was left unchanged

`revenue_gbp_m`, `wage_bill_gbp_m`, `revenue_basis` and `club_name` drive the frozen wage-model
split and wage pool. Following the precedent of the Tottenham correction (commit 723192e,
`DATA_VERSION.md` change log), only columns that the wage model does not read may change.
Arsenal, West Ham and Newcastle already match the consolidated accounts within rounding.
Changing the Brighton revenue to either group figure would swap its fit/test assignment with
West Ham. The filing values are therefore recorded in the `notes` cell of each seed row and as
Companies House rows in `seed/club_finance_sources.csv`, and no seed number or confidence value
was changed.

## Still open

- Brighton: the explanatory notes of "Profit on player trading" (group) and "Player trading"
  (single company) are not yet checked.
- Newcastle: the 0.090 inconsistency inside the single-company filing.
- Newcastle: the split of the consolidated amortisation and impairment (99.502 and 0.366) is
  from OCR; the impairment figure is inferred from the face total 99.868.
- The remaining 15 clubs.
