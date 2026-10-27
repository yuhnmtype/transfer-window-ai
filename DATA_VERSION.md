# Data version

Source: Kaggle `davidcariboo/player-scores` (Transfermarkt scrape). Upstream updates are paused since July 2026.

- Snapshot cutoff: 2026-07-11 (transfers), 2026-06-12 (player valuations)
- Season: 2025/26 (20 Premier League clubs)
- Rebuild: `python src/phase1_build_db.py`

File MD5 hashes of the dataset used:

| File | MD5 |
|---|---|
| clubs.csv | 7782360a6f3db4b2a191991b34cbc4b5 |
| players.csv | e4591226b71eb4e2dde510c9d359eb60 |
| transfers.csv | 18f699b7ea4e1666b5b4532a914bd615 |
| player_valuations.csv | d5760907843a03d31f08e91375744db7 |

Known limitation: the current squad is approximated (market value present and contract still running at the
snapshot). Mean absolute difference vs the official `squad_size` is 3.55 players per club, and 6 clubs differ
by more than 4 (Liverpool, West Ham, Newcastle, Everton, Crystal Palace, Wolves).

## Finance seed

- Seed: `seed/club_finance_seed.csv` (20 clubs, FY 2024/25), MD5 `513a5d09e09a7911e697cea9071f8e87`
- Sources: `seed/club_finance_sources.csv` (47 rows), MD5 `e6ae8d8a37687d6e6b3659da93610071`
- MD5 values are computed after normalising line endings to LF:
  `python -c "import hashlib; print(hashlib.md5(open('seed/club_finance_seed.csv','rb').read().replace(b'\r\n', b'\n')).hexdigest())"`
  (Git Bash alternative: `tr -d '\r' < seed/club_finance_seed.csv | md5sum`).
- Load: `python src/phase2_load_seed.py`. Cross-check: `python src/phase2_check_sources.py`.
- FY 2024/25 accounts were published between Dec 2025 and May 2026. Arsenal and Liverpool (earliest
  year-ends, 31 May) only had third-party estimates for 2025/26 in Oct 2026, so later year-end clubs
  are assumed not to have published FY 2025/26 either (not checked club by club).
- `published_date` in the sources table is the date of the article, not the filing date, except
  for the Companies House row, where it is the filing date.
- All figures come from secondary sources and were not checked against Companies House, except
  the Tottenham Hotspur row, which was checked against its filed consolidated accounts on
  2026-10-27.

## Change log

- 2026-10-27: data correction after the wage and fee evaluations had run. Only the Tottenham
  Hotspur row changed: profit on player sales 52.4 -> 52.6, confidence medium -> high, source
  URL and notes updated, and one source row added (Companies House, consolidated accounts of
  Tottenham Hotspur Limited), so the sources table has 47 rows. The columns read by the frozen
  wage evaluation (`revenue_gbp_m`, `wage_bill_gbp_m`, `revenue_basis`, `club_name`) are
  unchanged. The MD5 values recorded in the provenance of `results/wage_model_eval.json`
  (seed `8a9f6ca4a4b7cf23134e0b9093dc2ac6`) refer to the pre-change seed, and `md5_db_file` in
  `results/fee_model_eval.json` refers to the database before this reload. The earlier seed
  (and the earlier sources table, MD5 `2dd224703670d2872f4aeedb3583fe32`) is recoverable from
  git history at the commit before this change.