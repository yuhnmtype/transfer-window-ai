"""Phase 1: build the local SQLite database from the Transfermarkt dataset.

Source: Kaggle davidcariboo/player-scores. Upstream updates are paused; the last
transfers were published on 2026-07-11, so everything is a snapshot of that date.
"""
import datetime
import hashlib
import os
import sqlite3

import duckdb
import kagglehub
import pandas as pd

SNAPSHOT = "2026-07-11"  # last transfers published upstream
DB_PATH = "transfer_window_ai.db"
KAGGLE_SLUG = "davidcariboo/player-scores"

REQUIRED_COLUMNS = {
    "clubs": ["club_id", "name", "domestic_competition_id", "last_season", "squad_size"],
    "players": ["player_id", "name", "position", "sub_position", "date_of_birth",
                "country_of_citizenship", "agent_name", "contract_expiration_date",
                "market_value_in_eur", "current_club_id", "international_caps", "last_season"],
    "transfers": ["player_id", "transfer_date", "transfer_season", "from_club_id", "to_club_id",
                  "from_club_name", "to_club_name", "transfer_fee", "market_value_in_eur", "player_name"],
    "player_valuations": ["player_id", "date", "market_value_in_eur", "current_club_id"],
}


def load_views(path):
    """Expose the source CSVs as DuckDB views and fail early if a column is missing."""
    con = duckdb.connect()
    for table in REQUIRED_COLUMNS:
        con.execute(f"CREATE VIEW {table} AS SELECT * FROM read_csv_auto('{path}/{table}.csv', sample_size=-1)")
    for table, cols in REQUIRED_COLUMNS.items():
        have = {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()}
        missing = [c for c in cols if c not in have]
        assert not missing, f"{table} is missing columns {missing}. Available: {sorted(have)}"
    return con


def file_fingerprints(path):
    """MD5 of each source file, so the exact data version can be pinned in the README."""
    out = {}
    for name in REQUIRED_COLUMNS:
        with open(f"{path}/{name}.csv", "rb") as fh:
            out[f"md5_{name}"] = hashlib.md5(fh.read()).hexdigest()
    return out


def main():
    path = kagglehub.dataset_download(KAGGLE_SLUG)
    print("Dataset path:", path)
    con = load_views(path)

    # 20 Premier League clubs of the latest season in the data
    season = con.execute("SELECT MAX(last_season) FROM clubs WHERE domestic_competition_id='GB1'").fetchone()[0]
    pl_clubs = con.execute(
        "SELECT * FROM clubs WHERE domestic_competition_id='GB1' AND last_season=?", [season]).df()
    assert len(pl_clubs) == 20, f"Expected 20 clubs, got {len(pl_clubs)}"
    club_ids = tuple(int(x) for x in pl_clubs["club_id"].tolist())
    print(f"SEASON={season} | {len(pl_clubs)} clubs")

    # Players currently attached to those clubs (raw; filtered to a "current squad" below)
    current_squads = con.execute(f"""
        SELECT player_id FROM players
        WHERE CAST(current_club_id AS BIGINT) IN {club_ids} AND last_season = ?
    """, [season]).df()

    # Transfers up to the snapshot date only (later rows are scheduled moves, not completed deals)
    transfers = con.execute(f"""
        SELECT * FROM transfers
        WHERE transfer_date <= DATE '{SNAPSHOT}'
          AND (to_club_id IN {club_ids} OR from_club_id IN {club_ids})
    """).df()
    transfers["fee_is_estimated"] = transfers["transfer_fee"].isna()
    transfers["transfer_fee_clean"] = transfers["transfer_fee"].fillna(transfers["market_value_in_eur"])
    print(f"Transfers: {len(transfers)} | null fee: {transfers['transfer_fee'].isna().mean()*100:.1f}%")

    # Players and valuations for every player involved (joined, no long IN lists)
    ids = pd.DataFrame({"player_id": sorted(set(transfers["player_id"].tolist()) |
                                            set(current_squads["player_id"].tolist()))})
    con.register("ids_df", ids)
    players = con.execute("""
        SELECT p.player_id, p.name, p.position, p.sub_position, p.date_of_birth,
               p.country_of_citizenship, p.agent_name, p.contract_expiration_date,
               p.market_value_in_eur, CAST(p.current_club_id AS BIGINT) AS current_club_id,
               p.international_caps, p.last_season
        FROM players p JOIN ids_df USING (player_id)
    """).df()
    valuations = con.execute(f"""
        SELECT v.player_id, v.date, v.market_value_in_eur,
               CAST(v.current_club_id AS BIGINT) AS current_club_id
        FROM player_valuations v JOIN ids_df USING (player_id)
        WHERE v.date <= DATE '{SNAPSHOT}'
    """).df()

    # "Current squad" flag: right club + right season + has market value + contract running at snapshot
    snap = pd.Timestamp(SNAPSHOT)
    players["contract_active"] = players["contract_expiration_date"].isna() | (
        pd.to_datetime(players["contract_expiration_date"]) >= snap)
    players["is_current_squad"] = (
        players["current_club_id"].isin(club_ids)
        & (players["last_season"] == season)
        & players["market_value_in_eur"].notna()
        & players["contract_active"]
    )
    core = players[players["is_current_squad"]].copy()
    core["club_id"] = core["current_club_id"].astype("int64")
    core_agg = (core.groupby("club_id")
                    .agg(squad_value=("market_value_in_eur", "sum"), n_core=("player_id", "count"))
                    .reset_index())

    # Buying profile per club (age, foreign share, average fee actually paid)
    buys = transfers[transfers["to_club_id"].isin(club_ids)].merge(
        players[["player_id", "country_of_citizenship", "date_of_birth"]], on="player_id", how="inner")
    buys["age_at_transfer"] = (pd.to_datetime(buys["transfer_date"]) -
                               pd.to_datetime(buys["date_of_birth"])).dt.days / 365.25
    buys["is_foreign"] = buys["country_of_citizenship"] != "England"
    buys["fee_paid"] = buys["transfer_fee"].where(buys["transfer_fee"] > 0)
    club_agg = (buys.groupby("to_club_id")
                    .agg(n_signings=("player_id", "count"), foreign_pct=("is_foreign", "mean"),
                         avg_age_at_signing=("age_at_transfer", "mean"), avg_fee_paid=("fee_paid", "mean"))
                    .reset_index().rename(columns={"to_club_id": "club_id"}))

    clubs = pl_clubs[["club_id", "name", "last_season", "squad_size"]].copy()
    clubs["club_id"] = clubs["club_id"].astype("int64")
    clubs = clubs.merge(club_agg, on="club_id", how="left").merge(core_agg, on="club_id", how="left")
    clubs["squad_size_diff"] = clubs["n_core"] - clubs["squad_size"]

    meta = pd.DataFrame({
        "key": ["dataset_path", "snapshot_date", "season", "built_at"] + list(file_fingerprints(path).keys()),
        "value": [path, SNAPSHOT, str(season), datetime.datetime.now().isoformat()] +
                 list(file_fingerprints(path).values()),
    })

    db = sqlite3.connect(DB_PATH)
    for name, df in [("clubs", clubs), ("transfers", transfers), ("players", players),
                     ("valuations", valuations), ("meta", meta)]:
        df.to_sql(name, db, index=False, if_exists="replace")
    db.close()

    # Sanity checks
    print("\nDONE:", len(clubs), "clubs |", len(transfers), "transfers |",
          len(players), "players |", len(valuations), "valuations")
    print(clubs[["name", "squad_size", "n_core", "squad_size_diff", "squad_value"]]
          .sort_values("squad_value", ascending=False).to_string(index=False))
    print("Mean abs diff vs official squad_size:", round(clubs["squad_size_diff"].abs().mean(), 2))
    print("Clubs with |diff| > 4:", clubs[clubs["squad_size_diff"].abs() > 4]["name"].tolist())
    print("Transfer date range:", transfers["transfer_date"].min(), "->", transfers["transfer_date"].max())


if __name__ == "__main__":
    main()
