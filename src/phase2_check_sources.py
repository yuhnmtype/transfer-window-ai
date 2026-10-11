"""Cross-check the finance seed against the structured sources table."""
import sys

import pandas as pd

SEED_PATH = "seed/club_finance_seed.csv"
SOURCES_PATH = "seed/club_finance_sources.csv"

# Seed column -> sources column
METRICS = {
    "revenue_gbp_m": "revenue_gbp_m",
    "wage_bill_gbp_m": "wages_gbp_m",
    "profit_on_player_sales_gbp_m": "profit_on_sales_gbp_m",
    "player_amortisation_gbp_m": "amortisation_gbp_m",
}
ABS_TOL = 0.6    # sources round to whole millions, so allow 0.6
REL_TOL = 0.005  # or 0.5% of the seed value, whichever is larger
DATE_PATTERN = r"^\d{4}-\d{2}(-\d{2})?$"


def within_tolerance(seed_value: float, source_value: float) -> bool:
    return abs(seed_value - source_value) <= max(ABS_TOL, REL_TOL * abs(seed_value))


def check(seed: pd.DataFrame, sources: pd.DataFrame) -> list:
    """Return a list of error messages (empty list means valid)."""
    errors = []

    # Fail early with a clear message if the seed lacks the expected columns
    missing_seed = {"club_id", "club_name", "confidence", *METRICS.keys()} - set(seed.columns)
    if missing_seed:
        return [f"Seed is missing columns: {missing_seed}"]

    # Structural checks on the sources table
    missing_cols = {"club_id", "source_name", "url", "published_date",
                    *METRICS.values()} - set(sources.columns)
    if missing_cols:
        return [f"Sources table is missing columns: {missing_cols}"]
    if sources["url"].isna().any():
        errors.append("Every source row needs a url")
    dates = sources["published_date"].dropna().astype(str)
    bad_dates = dates[~dates.str.match(DATE_PATTERN)]
    if not bad_dates.empty:
        errors.append(f"Bad published_date values: {bad_dates.tolist()}")
    unknown_clubs = set(sources["club_id"]) - set(seed["club_id"])
    if unknown_clubs:
        errors.append(f"Sources for clubs not in the seed: {unknown_clubs}")

    # Value checks: every seed value must be supported by enough sources
    for _, row in seed.iterrows():
        club_sources = sources[sources["club_id"] == row["club_id"]]
        needed = 2 if row["confidence"] == "high" else 1
        for seed_col, src_col in METRICS.items():
            values = club_sources[src_col].dropna()
            n_match = sum(within_tolerance(row[seed_col], v) for v in values)
            if n_match < needed:
                errors.append(
                    f"{row['club_name']} {seed_col}={row[seed_col]}: "
                    f"{n_match} matching source(s), need {needed} "
                    f"(confidence={row['confidence']})"
                )
    return errors


def main():
    seed = pd.read_csv(SEED_PATH)
    sources = pd.read_csv(SOURCES_PATH)
    errors = check(seed, sources)
    if errors:
        print("SOURCE CHECK FAILED:")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print(f"Source check passed: {len(seed)} seed rows, {len(sources)} source rows")


if __name__ == "__main__":
    main()