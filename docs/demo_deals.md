# Demo deals (negotiation engine v1)

Two illustrative runs of the v1 negotiation engine (baseline policy, one round of sealed bids,
no LLM). They are illustrations, not real-world conclusions.

Code: negotiation engine commit `83a8002`, demo picker commit `b1ff200`. The outputs below were
produced from commit `723192e` on a clean working tree. The runs are read-only and
deterministic.

## How the deals were chosen

### Deal 1: objective draw

- Script: `src/demo_pick_deal.py`, seed `20261017`.
- Candidates: players with `is_current_squad = 1` at one of the 20 database clubs, whose
  latest valuation on or before `as_of` (2026-07-01) is at most 365 days old (the engine's
  guard) and at least EUR 20m. Candidates are sorted by `player_id` ascending.
- Draw: `numpy.random.default_rng(20261017).integers(len(candidates))`. One draw, no redraws.
- Result: 246 candidates, index 204, Abdukodir Khusanov; seller Manchester City.

### Deal 2: chosen, not random

Chosen by Huy from the top-15 market-value list to illustrate a case where the SCR
constraint binds: Sandro Tonali, seller Newcastle United (his current club in the database).

### Excluded: Marcus Rashford

Rashford was looked up first and excluded. The snapshot's `players` table has him at
Barcelona (`current_club_id` 131) with `is_current_squad = 0`, although the `transfers` table
has a 2026-06-30 Barcelona to Man Utd row with a zero fee. This is a data inconsistency in
the snapshot. The engine follows the frozen squad definition (`players.current_club_id` and
`is_current_squad = 1`), so he is not in the current squad of any of the 20 clubs.

## How to read this

The headline result is the **set of buyers that can afford the reserve** under each wage
scenario. The winner among them is decided by the tie-break (smaller `club_id`) and has no
economic meaning. Prices equal the fallback reserve (market value x 1.25, read from
`results/fee_model_eval.json`) by construction; they are not market price forecasts. Wages are
illustrative shares of market value, not estimates. The Tottenham Hotspur finance row was
checked against its filed FY 2024/25 consolidated accounts (see `seed/SOURCES.md`); its 256m
wage bill is group-wide staff costs, including stadium and retail staff, so it overstates squad
cost. Tottenham's large max fee is an SCR headroom ceiling (headroom multiplied by the assumed
contract length), not a prediction and not a cash check.

## Deal 1: Abdukodir Khusanov (Manchester City)

Commands:

```bash
python src/demo_pick_deal.py
python src/negotiate_cli.py --seller-club-id 281 --player-id 763079 --rule green
```

Picker output:

```text
Candidates: 246 (as_of 2026-07-01, market value >= EUR 20m, seed 20261017)
Index: 204
Player: Abdukodir Khusanov (player_id 763079), market value 50.00m EUR on 2026-06-03
Seller: Manchester City (club_id 281)
```

Negotiation output:

```text
NEGOTIATION v1 (baseline policy, one round of sealed bids). Illustrative only.

Seller: Manchester City (club_id 281); player Abdukodir Khusanov (player_id 763079), market value 50.00m EUR on 2026-06-03
Fair-value ratio m = 1.25 (fallback: market value x training median ratio), from D:\transfer-window-ai\results\fee_model_eval.json (code fab6766, md5 22084719)
Reserve = buyer valuation = 62.50m EUR = 53.75m GBP
Rule: buyer must stay green. Contract 5 years (assumption); agent fee 0.0 (assumed). Buyers: default: largest headroom to green, excluding Championship-revenue clubs and clubs already above the limit.

=== Wage scenario 5% of market value (2.15m GBP a year, illustrative, not estimated) ===
Buyers that can afford the reserve: Tottenham Hotspur, Arsenal FC
  buyer                     before  headroom   max fee      bid  binding
  Tottenham Hotspur          green    127.21    625.32    53.75  valuation
  Arsenal FC                 green     21.54     96.95    53.75  valuation
Deal: yes. Winner Arsenal FC at 53.75m GBP (won_by_tiebreak: yes, tied with [148]); status green -> green, headroom 21.54 -> 8.64

=== Wage scenario 10% of market value (4.30m GBP a year, illustrative, not estimated) ===
Buyers that can afford the reserve: Tottenham Hotspur, Arsenal FC
  buyer                     before  headroom   max fee      bid  binding
  Tottenham Hotspur          green    127.21    614.57    53.75  valuation
  Arsenal FC                 green     21.54     86.20    53.75  valuation
Deal: yes. Winner Arsenal FC at 53.75m GBP (won_by_tiebreak: yes, tied with [148]); status green -> green, headroom 21.54 -> 6.49

=== Wage scenario 15% of market value (6.45m GBP a year, illustrative, not estimated) ===
Buyers that can afford the reserve: Tottenham Hotspur, Arsenal FC
  buyer                     before  headroom   max fee      bid  binding
  Tottenham Hotspur          green    127.21    603.82    53.75  valuation
  Arsenal FC                 green     21.54     75.45    53.75  valuation
Deal: yes. Winner Arsenal FC at 53.75m GBP (won_by_tiebreak: yes, tied with [148]); status green -> green, headroom 21.54 -> 4.34

Log:
  Player Abdukodir Khusanov (player_id 763079) at Manchester City; market value 50.00m EUR on 2026-06-03.
  Reserve = valuation = 1.25 x MV = 62.50m EUR = 53.75m GBP (fallback: market value x training median ratio).
  Buyers (default: largest headroom to green, excluding Championship-revenue clubs and clubs already above the limit): [148, 11]. Acceptance rule: green.
  Scenario wage = 5% of MV = 2.15m GBP a year (illustrative, not estimated).
    Tottenham Hotspur: headroom to green limit 127.21, max fee 625.32, binding valuation, bid 53.75.
    Arsenal FC: headroom to green limit 21.54, max fee 96.95, binding valuation, bid 53.75.
    Affordable: ['Tottenham Hotspur', 'Arsenal FC']. Winner Arsenal FC at 53.75m GBP (won_by_tiebreak=True).
  Scenario wage = 10% of MV = 4.30m GBP a year (illustrative, not estimated).
    Tottenham Hotspur: headroom to green limit 127.21, max fee 614.57, binding valuation, bid 53.75.
    Arsenal FC: headroom to green limit 21.54, max fee 86.20, binding valuation, bid 53.75.
    Affordable: ['Tottenham Hotspur', 'Arsenal FC']. Winner Arsenal FC at 53.75m GBP (won_by_tiebreak=True).
  Scenario wage = 15% of MV = 6.45m GBP a year (illustrative, not estimated).
    Tottenham Hotspur: headroom to green limit 127.21, max fee 603.82, binding valuation, bid 53.75.
    Arsenal FC: headroom to green limit 21.54, max fee 75.45, binding valuation, bid 53.75.
    Affordable: ['Tottenham Hotspur', 'Arsenal FC']. Winner Arsenal FC at 53.75m GBP (won_by_tiebreak=True).

Caveats:
  - Wages are illustrative scenarios (a share of market value), not estimates.
  - Seller reserve and buyer valuation are both the fee-model fallback (market value x training median ratio); the learned fee model was not accepted.
  - Because reserve and valuation are equal, the winner among affordable buyers is decided by the tie-break (smaller club_id), which carries no economic meaning.
  - One round only: no counter rounds, no player decision, no GBE check, no seller-side SCR.
  - Contract length and agent fee are assumptions from config/negotiation.toml.
  - SCR figures inherit the advisor caveats (club-wide wage bill, impairment 0, FY 2024/25 secondary sources, fixed EUR to GBP rate).
  - This is not a real-world conclusion.
```

## Deal 2: Sandro Tonali (Newcastle United)

Command:

```bash
python src/negotiate_cli.py --seller-club-id 762 --player-id 397033 --rule green
```

Negotiation output:

```text
NEGOTIATION v1 (baseline policy, one round of sealed bids). Illustrative only.

Seller: Newcastle United (club_id 762); player Sandro Tonali (player_id 397033), market value 80.00m EUR on 2026-06-03
Fair-value ratio m = 1.25 (fallback: market value x training median ratio), from D:\transfer-window-ai\results\fee_model_eval.json (code fab6766, md5 22084719)
Reserve = buyer valuation = 100.00m EUR = 86.00m GBP
Rule: buyer must stay green. Contract 5 years (assumption); agent fee 0.0 (assumed). Buyers: default: largest headroom to green, excluding Championship-revenue clubs and clubs already above the limit.

=== Wage scenario 5% of market value (3.44m GBP a year, illustrative, not estimated) ===
Buyers that can afford the reserve: Tottenham Hotspur, Arsenal FC
  buyer                     before  headroom   max fee      bid  binding
  Tottenham Hotspur          green    127.21    618.87    86.00  valuation
  Arsenal FC                 green     21.54     90.50    86.00  valuation
Deal: yes. Winner Arsenal FC at 86.00m GBP (won_by_tiebreak: yes, tied with [148]); status green -> green, headroom 21.54 -> 0.90

=== Wage scenario 10% of market value (6.88m GBP a year, illustrative, not estimated) ===
Buyers that can afford the reserve: Tottenham Hotspur
  buyer                     before  headroom   max fee      bid  binding
  Tottenham Hotspur          green    127.21    601.67    86.00  valuation
  Arsenal FC                 green     21.54     73.30        -  SCR
Deal: yes. Winner Tottenham Hotspur at 86.00m GBP (won_by_tiebreak: no); status green -> green, headroom 127.21 -> 103.13

=== Wage scenario 15% of market value (10.32m GBP a year, illustrative, not estimated) ===
Buyers that can afford the reserve: Tottenham Hotspur
  buyer                     before  headroom   max fee      bid  binding
  Tottenham Hotspur          green    127.21    584.47    86.00  valuation
  Arsenal FC                 green     21.54     56.10        -  SCR
Deal: yes. Winner Tottenham Hotspur at 86.00m GBP (won_by_tiebreak: no); status green -> green, headroom 127.21 -> 99.69

Log:
  Player Sandro Tonali (player_id 397033) at Newcastle United; market value 80.00m EUR on 2026-06-03.
  Reserve = valuation = 1.25 x MV = 100.00m EUR = 86.00m GBP (fallback: market value x training median ratio).
  Buyers (default: largest headroom to green, excluding Championship-revenue clubs and clubs already above the limit): [148, 11]. Acceptance rule: green.
  Scenario wage = 5% of MV = 3.44m GBP a year (illustrative, not estimated).
    Tottenham Hotspur: headroom to green limit 127.21, max fee 618.87, binding valuation, bid 86.00.
    Arsenal FC: headroom to green limit 21.54, max fee 90.50, binding valuation, bid 86.00.
    Affordable: ['Tottenham Hotspur', 'Arsenal FC']. Winner Arsenal FC at 86.00m GBP (won_by_tiebreak=True).
  Scenario wage = 10% of MV = 6.88m GBP a year (illustrative, not estimated).
    Tottenham Hotspur: headroom to green limit 127.21, max fee 601.67, binding valuation, bid 86.00.
    Arsenal FC: headroom to green limit 21.54, max fee 73.30, binding SCR, bid none.
    Affordable: ['Tottenham Hotspur']. Winner Tottenham Hotspur at 86.00m GBP (won_by_tiebreak=False).
  Scenario wage = 15% of MV = 10.32m GBP a year (illustrative, not estimated).
    Tottenham Hotspur: headroom to green limit 127.21, max fee 584.47, binding valuation, bid 86.00.
    Arsenal FC: headroom to green limit 21.54, max fee 56.10, binding SCR, bid none.
    Affordable: ['Tottenham Hotspur']. Winner Tottenham Hotspur at 86.00m GBP (won_by_tiebreak=False).

Caveats:
  - Wages are illustrative scenarios (a share of market value), not estimates.
  - Seller reserve and buyer valuation are both the fee-model fallback (market value x training median ratio); the learned fee model was not accepted.
  - Because reserve and valuation are equal, the winner among affordable buyers is decided by the tie-break (smaller club_id), which carries no economic meaning.
  - One round only: no counter rounds, no player decision, no GBE check, no seller-side SCR.
  - Contract length and agent fee are assumptions from config/negotiation.toml.
  - SCR figures inherit the advisor caveats (club-wide wage bill, impairment 0, FY 2024/25 secondary sources, fixed EUR to GBP rate).
  - This is not a real-world conclusion.
```
