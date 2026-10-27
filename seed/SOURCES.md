# Finance seed sources (FY 2024/25)

All figures are in GBP millions. Every figure comes from a **secondary source** that
summarises audited accounts, except the Tottenham Hotspur Companies House row, which is a
primary filing; the Tottenham Hotspur seed row was checked against it on 2026-10-27. No
other row was checked against the primary filing (Companies House) or the club's own annual
report PDF. Swiss Ramble articles are paywalled, so only the visible part was read. Sources
were accessed between 2026-10-10 and 2026-10-11.

The machine-readable version of this table is `club_finance_sources.csv`, which is checked
against the seed by `python src/phase2_check_sources.py`. Keep both in sync.

Abbreviations: SR = Swiss Ramble, PSR = PSRwatch, GotP = Game of the People.
"n/s" means the source did not state the figure. "Published" is the date of the article,
not the filing date of the accounts, except for the Companies House row, where it is the
filing date; "-" means unknown.

## Per-club source comparison

| Club | Source | Published | Revenue | Wages | Profit on player sales | Amortisation |
|---|---|---|---|---|---|---|
| Manchester City | [SR](https://swissramble.substack.com/p/manchester-city-finances-202425) | - | 694 | 408 | 95 | 170 |
| Manchester City | [PSR](https://psrwatch.com/insights/man-city-accounts-explained-2025) | - | 694 | 408 | 95.2 | 170 |
| Manchester City | [GotP](https://gameofthepeople.com/2025/12/19/a-minor-irritation-for-manchester-city-but-the-big-question-remains/) | 2025-12-19 | 694 | 408 | 95.2 | n/s |
| Manchester City | [Inside World Football](https://www.insideworldfootball.com/2025/12/19/man-city-post-10m-loss-694m-revenue-clubs-invests-353m-new-players/) | 2025-12-19 | 694.1 | n/s | n/s (sales income 95) | n/s |
| Manchester City | [The Esk](https://theesk.org/2026/04/14/the-analysis-series-financial-analysis-of-manchester-city-football-club-fiscal-year-2024-25-and-post-balance-sheet-developments-through-april-2026/) | 2026-04-14 | 694.1 | 579.0 (rejected) | 95.2 | n/s |
| Brighton | [SR](https://swissramble.substack.com/p/brighton-and-hove-albion-finances-28d) | - | 222 | 165 | 57 | 82 |
| Brighton | [PSR](https://psrwatch.com/insights/brighton-accounts-explained-2025) | - | 221 | 159 | 56.9 | 81.8 |
| Brighton | [GotP](https://gameofthepeople.com/2026/01/16/brightons-halo-slips-just-a-little/) | 2026-01-16 | about 242 (derived, includes 20 property income, rejected) | 165 | 57 | n/s |
| Brighton | [Inside World Football](https://www.insideworldfootball.com/2026/01/12/brighton-report-54m-loss-set-course-europe-first-major-trophy/) | 2026-01-12 | about 247 (not confirmed elsewhere, rejected) | n/s | n/s (player trading -25) | n/s |
| Brighton | [Club release](https://www.brightonandhovealbion.com/media-article/club-news-annual-accounts-published-2024-25-season-january-2026) | 2026-01 | n/s | n/s | n/s | n/s (operating loss 54.4, amortisation up 39.8) |
| Fulham | [SR](https://swissramble.substack.com/p/fulham-finances-202425) | - | 194.8 | 166.5 | 41.0 | 61.5 |
| Arsenal | [SR](https://swissramble.substack.com/p/arsenal-finances-202425) | - | 690 | 347 | 81 | 172 (excludes 15 impairment) |
| Arsenal | [PSR](https://psrwatch.com/insights/arsenal-accounts-explained-2025) | - | 691 | 347 | 81.2 | 172 |
| Liverpool | [SR](https://swissramble.substack.com/p/liverpool-finances-202425) | - | 703 | 428 | 53 | 117 |
| Liverpool | [PSR](https://psrwatch.com/insights/liverpool-accounts-explained-2025) | - | 703 | 428 | 53.3 | 117 |
| Brentford | [SR](https://swissramble.substack.com/p/brentford-finances-202425) | - | 173.1 | 130.8 | 27.2 | 47.8 |
| Brentford | [PSR](https://psrwatch.com/insights/brentford-accounts-explained-2025) | - | 173 | 131 | 27.2 | 47.6 |
| Chelsea | [SR](https://swissramble.substack.com/p/chelsea-finances-202425) | - | 491 | 359 | 58 | 212 |
| Chelsea | [PSR](https://psrwatch.com/insights/chelsea-accounts-explained-2025) | - | 491 | 359 | 57.9 | 212 |
| Manchester United | [SR](https://swissramble.substack.com/p/manchester-united-finances-202425) | - | 667 | 313 | 49 | 193 |
| Manchester United | [United in Focus](https://www.unitedinfocus.com/news/man-united-break-667m-record-as-official-paperwork-released-psr-position-now-clear/) | - | 666.5 | 313 | n/s | 196 |
| Tottenham | [PSR](https://psrwatch.com/insights/tottenham-accounts-explained-2025) | - | 565 | 256 | 52.6 | 142 |
| Tottenham | [The Esk](https://theesk.org/2026/04/17/the-analysis-series-analysis-of-tottenham-hotspur-football-athletic-co-ltd-and-tottenham-hotspur-limited-strategic-review-of-the-2024-25-fiscal-period/) | 2026-04-17 | 565.3 | 248.3 (company) / about 402 (group, not supported by the filed accounts) | 52.4 (company) | 146.6 (company line incl. impairments and other football trading) |
| Tottenham | [Inside World Football](https://www.insideworldfootball.com/2026/04/02/spurs-finances-match-pitch-failings-loses-jump-51m/) | 2026-04-02 | 565.3 | n/s | n/s | n/s |
| Tottenham | [Companies House, consolidated accounts](https://find-and-update.company-information.service.gov.uk/company/01706358/filing-history) | 2026-03-30 (filing date) | 565.266 | 255.811 | 52.565 | 141.851 |
| Newcastle | [SR](https://swissramble.substack.com/p/newcastle-united-finances-202425) | - | 335 | 243 | 20 | 100 |
| Newcastle | [PSR](https://psrwatch.com/insights/newcastle-accounts-explained-2025) | - | 335 | 243 | 19.9 | 99.9 |
| Aston Villa | [SR](https://swissramble.substack.com/p/aston-villa-finances-202425) | - | 378 | 273 | 52 | 100 (excludes 6.5 impairment) |
| Aston Villa | [PSR](https://psrwatch.com/insights/aston-villa-accounts-explained-2025) | - | 378 | 273 | 52 | 106 (probably includes impairment) |
| Everton | [SR](https://swissramble.substack.com/p/everton-finances-202425) | - | 196.7 | 152.1 | 31.3 | 50.9 |
| Everton | [PSR](https://psrwatch.com/insights/everton-accounts-explained-2025) | - | 197 | 152 | 31.3 | 50.9 |
| Crystal Palace | [SR](https://swissramble.substack.com/p/crystal-palace-finances-202425) | - | 196.6 | 148.3 | 66.1 | 54.1 |
| Crystal Palace | [PSR](https://psrwatch.com/insights/crystal-palace-accounts-explained-2025) | - | 195 | 145 | 66.1 | 54 |
| Nottingham Forest | [SR](https://swissramble.substack.com/p/nottingham-forest-finances-202425) | - | 222 | 167 | 7 | 69 |
| Nottingham Forest | [GotP](https://gameofthepeople.com/2026/03/29/nottingham-forest-record-revenues-and-a-busy-revolving-door/) | 2026-03-29 | 222 | 168 | 7 | n/s |
| Nottingham Forest | [The Esk](https://theesk.org/2026/04/17/analysis-series-analysis-of-the-financial-operational-framework-of-nottingham-forest-football-club-limited-2024-2026/) | 2026-04-17 | 221.7 | 166.7 (total staff costs about 241, not used) | n/s | 68.9 |
| Bournemouth | [SR](https://swissramble.substack.com/p/bournemouth-finances-202425) | - | 182 | 158 | 91 | 69 |
| Bournemouth | [PSR](https://psrwatch.com/insights/bournemouth-accounts-explained-2025) | - | 182 | 158 | 91 | 69.2 |
| Leeds | [PSR](https://psrwatch.com/insights/leeds-accounts-explained-2025) | - | 137 | 103 | 24.8 | 46.4 |
| Leeds | [Yorkshire Evening Post](https://www.yorkshireeveningpost.co.uk/sport/football/leeds-united/leeds-united-accounts-revenue-loss-psr-6547861) | - | 137 | 107.7 | n/s | n/s |
| Sunderland | [SR](https://swissramble.substack.com/p/sunderland-finances-202425) | - | 40.3 | 53.8 (includes promotion bonus) | 45.9 | 10.4 |
| Sunderland | [PSR](https://psrwatch.com/insights/sunderland-accounts-explained-2025) | - | 39.4 | 52.9 | 45.8 | 10.4 |
| Burnley | [SR](https://swissramble.substack.com/p/burnley-finances-202425) | - | 71.7 | 82.3 | 59.0 | 36.7 |
| Burnley | [The Esk](https://theesk.org/2026/05/07/the-analysis-series-burnley-fc-holdings-limited-annual-report-and-financial-statements-2024-25/) | 2026-05-07 | 71.7 | 82.3 | 59.0 | 36.7 |
| West Ham | [The Esk](https://theesk.org/2026/04/22/the-analysis-series-analysis-of-wh-holding-limited-financial-distillation-ownership-transition-and-structural-viability-of-west-ham-united-fy-2024-2025/) | 2026-04-22 | 227.6 | 175.9 (scope unclear) | 20.0 | 99.4 |
| Wolves | [SR](https://swissramble.substack.com/p/wolverhampton-wanderers-finances-04e) | - | 172 | 163 | 117 | 75 |
| Wolves | [The Esk](https://theesk.org/2026/05/08/the-analysis-series-wolverhampton-wanderers-annual-report-and-accounts-2024-25/) | 2026-05-08 | 172.0 | 162.1 | 117.0 | 75.4 |

## Rejected or unresolved values

- **Man City wages 579.0 (The Esk):** rejected. Three sources give 408. The 2023/24 wage bill
  was 413, so 408 fits the reported small decrease. The Esk figure is probably a broader
  staff-cost aggregate.
- **Brighton revenue 242 (derived from GotP) and 247 (Inside World Football):** rejected.
  242 includes 20 of Monks Farm property income. SR and PSR give 222 and 221.
- **Tottenham:** checked on 2026-10-27 against the filed FY 2024/25
  consolidated accounts of Tottenham Hotspur Limited (Companies House 01706358, made up to
  30 June 2025, filed 2026-03-30): revenue and other income 565.266 (revenue alone 564.881);
  staff costs 255.811 (877 average employees: 335 players and football administration, 473
  stadium operations and support, 69 retail and distribution); profit on disposal of
  intangible assets 52.565; amortisation of intangible assets 141.851 (player registrations
  141.180). The seed keeps revenue 565.3, wages 256.0 (group staff costs, so they include
  stadium and retail staff) and amortisation 142.0, uses the group profit on sales 52.6, and
  has confidence `high`.
- **What the secondary sources said about Tottenham:** PSR's 256 wages, 52.6 profit on sales
  and 142 amortisation match the group figures. The Esk's 248.3 wages and 52.4 profit on sales
  are the company-level figures of Tottenham Hotspur Football & Athletic Co. Ltd (00057186:
  staff costs 248.263, profit on disposal 52.445), and its 146.6 matches the company-level
  line "amortisation, impairments and other net football trading" (146.627; group 146.720).
  The Esk's "about 402 group-level" wage figure is not supported by the filed consolidated
  accounts.
- **Crystal Palace revenue and wages:** SR and PSR differ by more than rounding. SR is used
  because it gives one decimal. Definitions may differ.
- **Aston Villa amortisation:** 100 (SR, excludes a 6.5 impairment) is used. PSR gives 106,
  probably including impairment. This is why the row is `medium`.
- **Man Utd amortisation:** 193 (SR) is used. United in Focus gives 196.
- **Impairment:** only noted (Arsenal 15, Villa 6.5). It is not a column. Whether SCR counts
  impairment is not yet verified.
- **Fulham:** single source, partly paywalled. Needs a second source.
- **Man Utd profit on sales:** single source (SR).
- **Leeds, Sunderland, Burnley:** their FY 2024/25 was played in the Championship, so
  revenue is not representative of Premier League income. Confidence is `low` for this
  reason. The rules engine will need a separate Premier League revenue estimate for them.
  Flagged in the seed as `revenue_basis = CHAMPIONSHIP`.
- **Leeds wages:** 103 (PSR) is used. The Yorkshire Evening Post reports 107.7. Unresolved.
- **Sunderland:** Swiss Ramble and PSR differ by about 0.9 on revenue and wages. Swiss Ramble
  is used. Financial year ends 31 July 2025.
- **West Ham:** single source (The Esk, group accounts to 31 May 2025). It is unclear whether
  staff costs include non-football staff. Needs a second source.
- **Wolves:** the accounts cover 13 months to 30 June 2025 because the year-end moved from
  May to June, so figures are slightly inflated against a 12-month year. Flagged in the seed
  as `period_months = 13`.

## Other facts used

- Final 2025/26 table and UEFA places: [Wikipedia](https://en.wikipedia.org/wiki/2025%E2%80%9326_Premier_League),
  [NBC Sports](https://www.nbcsports.com/premier-league-table-2025-26-season-standings)
  (NBC has some points totals that do not match its win/draw/loss records, so it was used only
  for relegation and qualification).
- Brighton Conference League league phase: [Express & Star](https://www.expressandstar.com/uk-sports/brighton-cruise-into-conference-league-after-win-over-tromso-8946632).
- FY 2025/26 accounts: not yet published. Arsenal and Liverpool published FY 2024/25 in
  February 2026 ([Arseblog](https://arseblog.news/2026/02/arsenal-nearly-break-even-as-revenues-hit-new-heights/),
  [This Is Anfield](https://www.thisisanfield.com/2026/02/liverpool-fc-financial-results-accounts-2025-record-revenue/)).
- Financial years end 30 June, except Arsenal, Liverpool and West Ham (31 May) and Burnley
  and Sunderland (31 July). Wolves reports a 13-month period.

## Wage model research (2026-10-11)

Goal: estimate per-player wages by allocating a club's wage pool across its squad by market
value. The result is a labelled assumption with confidence `low`, never a fact.

These pages were read through a summarising tool, and the theses from their abstract pages
only. Every figure below must be re-checked against the page before it is used.

| Source | Finding | Confidence |
|---|---|---|
| [Capology explainer (Sports Reference)](https://www.sports-reference.com/blog/2022/08/capology-wage-data-explainer/) | Wages are either "Verified" or "Unverified estimation". Gross, base salary only, bonuses excluded. Sources often disagree. | medium (methodology description) |
| [Planet Football](https://www.planetfootball.com/premier-league/every-premier-league-club-highest-paid-player-in-2025-26-xhaka-joins-haaland-on-elite-list), [Football365](https://www.football365.com/news/every-premier-league-club-highest-earner-beat-paid-player) (published 18 Aug 2026), [GiveMeSport](https://www.givemesport.com/every-premier-league-club-highest-paid-player/) (2023-24 data, outdated, do not use) | Top-earner lists are Capology-derived, so not independent. They disagree for several clubs: checked 6 clubs, top-earner figures differ by about 7% to 41%, partly because they name different players. | low |
| [Che 2022, Salford PhD thesis, "Player valuation in thin markets"](https://salford-repository.worktribe.com/output/1323435) | For top-10% market-value players in the big-5 leagues, mature players earn 112% more than young players and mid-age players 64% more. Performance matters little for wages. Abstract page only. Not used as a parameter yet. | low |
| [Kouh Bor 2024, University of Victoria master's project](https://dspace.library.uvic.ca/items/ddc4fc58-9aa4-46e8-bc7f-dbc670bed842) | ML on 3,522 EPL player-seasons. Age, position, nationality and club revenue matter. No coefficients on the page. | low |
| [Frenger et al. 2019](https://econpapers.repec.org/RePEc:zbw:eiswps:30) | Bundesliga market value depends more than 50% on the club's preseason TV money. Implication: allocate within a club, never compare market values across clubs. | low-medium |

Not found:
- Any usable source for the players' share of a club's wage bill, so `wage_scope_factor`
  stays 1.0 and is used for sensitivity only.
- Any independent per-player wage ground truth. The Sporting Intelligence survey page found
  is from 2010 ([link](https://sportingintelligence.com/?p=3664)) and is unusable.

Planned design (not yet implemented):
- `wage_i = pool * MV_i^alpha / sum_j(MV_j^alpha)` within one club, reported as a range over
  `alpha`, not a single number.
- Acceptance criteria, the fit/test split and the reference file are committed BEFORE the
  model is run.
- An age multiplier is only an optional later ablation.