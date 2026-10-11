"""Load the club finance seed CSV into SQLite after validating it."""
import sqlite3
import sys

import pandas as pd

SEED_PATH = "seed/club_finance_seed.csv"
DB_PATH = "transfer_window_ai.db"

EXPECTED_COLUMNS = [
    "club_id", "club_name", "fiscal_year", "revenue_gbp_m",
    "wage_bill_gbp_m", "profit_on_player_sales_gbp_m",
    "uefa_next_season", "in_pl_next_season",
    "source_url", "confidence", "notes",
]
ALLOWED_UEFA = {"CL", "EL", "CONF_LEAGUE_PHASE", "CONF_PLAYOFF", "NO"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}


def validate(seed: pd.DataFrame, db_clubs: pd.DataFrame) -> list:
    """Return a list of error messages (empty list means valid)."""
    errors = []

    # Schema check
    if list(seed.columns) != EXPECTED_COLUMNS:
        errors.append(f"Columns mismatch: {list(seed.columns)}")
        return errors

    # Primary key is (club_id, fiscal_year)
    if seed.duplicated(subset=["club_id", "fiscal_year"]).any():
        errors.append("Duplicate (club_id, fiscal_year) in seed")

    # Null checks
    required = ["club_id", "revenue_gbp_m", "wage_bill_gbp_m",
                "profit_on_player_sales_gbp_m", "uefa_next_season",
                "in_pl_next_season", "source_url", "confidence"]
    for col in required:
        if seed[col].isna().any():
            errors.append(f"Null values in {col}")

    # Domain checks
    bad_uefa = set(seed["uefa_next_season"]) - ALLOWED_UEFA
    if bad_uefa:
        errors.append(f"Unknown UEFA values: {bad_uefa}")
    if seed["in_pl_next_season"].dtype != bool:
        errors.append("in_pl_next_season must contain only true/false")
    bad_conf = set(seed["confidence"]) - ALLOWED_CONFIDENCE
    if bad_conf:
        errors.append(f"Unknown confidence values: {bad_conf}")
    if (seed["revenue_gbp_m"] <= 0).any() or (seed["wage_bill_gbp_m"] <= 0).any():
        errors.append("Revenue and wage bill must be positive")

    # Sanity: wage ratio should be plausible (30%-150%). Championship clubs can exceed 100%.
    ratio = seed["wage_bill_gbp_m"] / seed["revenue_gbp_m"]
    for cid, r in zip(seed["club_id"], ratio):
        if not 0.30 <= r <= 1.50:
            errors.append(f"club_id {cid}: wage/revenue ratio {r:.2f} looks implausible")

    # Integrity: every club_id and club_name must match the clubs table
    merged = seed.merge(db_clubs, on="club_id", how="left", suffixes=("", "_db"))
    missing = merged[merged["name"].isna()]["club_id"].tolist()
    if missing:
        errors.append(f"club_id not found in clubs table: {missing}")
    mismatch = merged[merged["name"].notna() & (merged["club_name"] != merged["name"])]
    for _, row in mismatch.iterrows():
        errors.append(f"club_id {row['club_id']}: seed name '{row['club_name']}' != DB name '{row['name']}'")

    return errors


def main():
    seed = pd.read_csv(SEED_PATH)
    con = sqlite3.connect(DB_PATH)
    db_clubs = pd.read_sql("SELECT club_id, name FROM clubs", con)
    db_clubs["club_id"] = db_clubs["club_id"].astype(int)

    errors = validate(seed, db_clubs)
    if errors:
        print("VALIDATION FAILED:")
        for e in errors:
            print(" -", e)
        con.close()
        sys.exit(1)

    seed.to_sql("club_finance", con, if_exists="replace", index=False)
    con.close()
    print(f"Loaded {len(seed)} rows into club_finance")
    print(seed[["club_name", "revenue_gbp_m", "wage_bill_gbp_m",
                "profit_on_player_sales_gbp_m", "uefa_next_season",
                "in_pl_next_season", "confidence"]].to_string(index=False))


if __name__ == "__main__":
    main()