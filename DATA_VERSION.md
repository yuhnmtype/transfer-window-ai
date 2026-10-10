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
