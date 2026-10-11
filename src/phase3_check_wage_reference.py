"""Validate the wage reference table used to evaluate the wage model (pre-registered)."""
import sys

import pandas as pd

REFERENCE_PATH = "seed/wage_reference.csv"
SEED_PATH = "seed/club_finance_seed.csv"

EXPECTED_COLUMNS = [
    "club_id", "club_name", "split", "pf_weekly_gbp_k", "f365_weekly_gbp_k",
    "ref_min_weekly_gbp_k", "ref_max_weekly_gbp_k", "max_is_lower_bound", "n_sources",
    "source_urls", "player_names_as_reported", "confidence", "notes",
]
# Source column -> the only URL allowed for it
SOURCES = {
    "pf_weekly_gbp_k": "https://www.planetfootball.com/premier-league/"
                       "every-premier-league-club-highest-paid-player-in-2025-26-"
                       "xhaka-joins-haaland-on-elite-list",
    "f365_weekly_gbp_k": "https://www.football365.com/news/"
                         "every-premier-league-club-highest-earner-beat-paid-player",
}


def expected_split(seed: pd.DataFrame) -> dict:
    """Pre-registered split: PL-revenue clubs sorted by revenue (desc, ties by club_id),
    alternately assigned fit, test, fit, ..."""
    pl = seed[seed["revenue_basis"] == "PL"].sort_values(
        ["revenue_gbp_m", "club_id"], ascending=[False, True])
    return {cid: ("fit" if i % 2 == 0 else "test") for i, cid in enumerate(pl["club_id"])}


def check(ref: pd.DataFrame, seed: pd.DataFrame) -> list:
    """Return a list of error messages (empty list means valid)."""
    errors = []

    # Schema check
    if list(ref.columns) != EXPECTED_COLUMNS:
        return [f"Columns mismatch: {list(ref.columns)}"]
    if ref["club_id"].duplicated().any():
        errors.append("Duplicate club_id in reference")

    # Clubs must be exactly the PL-revenue clubs of the seed, with matching names and split
    split = expected_split(seed)
    if set(ref["club_id"]) != set(split):
        errors.append(f"Clubs differ from PL-revenue seed clubs: "
                      f"{set(ref['club_id']) ^ set(split)}")
    names = dict(zip(seed["club_id"], seed["club_name"]))
    for _, row in ref.iterrows():
        cid = row["club_id"]
        if names.get(cid) != row["club_name"]:
            errors.append(f"club_id {cid}: name '{row['club_name']}' != seed '{names.get(cid)}'")
        if split.get(cid) != row["split"]:
            errors.append(f"club_id {cid}: split '{row['split']}' != pre-registered "
                          f"'{split.get(cid)}'")

    # Domain checks
    if set(ref["confidence"]) != {"low"}:
        errors.append("confidence must be 'low' on every row")
    if ref["max_is_lower_bound"].dtype != bool:
        errors.append("max_is_lower_bound must contain only true/false")

    # Value checks: min/max and n_sources must follow from the per-source columns
    for _, row in ref.iterrows():
        label = f"{row['club_name']}"
        values = {col: row[col] for col in SOURCES if pd.notna(row[col])}
        if any(v <= 0 for v in values.values()):
            errors.append(f"{label}: source values must be positive")
        if not values:
            errors.append(f"{label}: no source value")
            continue
        if row["n_sources"] != len(values):
            errors.append(f"{label}: n_sources={row['n_sources']} but {len(values)} values")
        if row["ref_min_weekly_gbp_k"] != min(values.values()):
            errors.append(f"{label}: ref_min is not the minimum of the source values")
        if row["ref_max_weekly_gbp_k"] != max(values.values()):
            errors.append(f"{label}: ref_max is not the maximum of the source values")
        urls = set(str(row["source_urls"]).split(";"))
        if urls != {SOURCES[col] for col in values}:
            errors.append(f"{label}: source_urls do not match the sources with values")
    return errors


def main():
    ref = pd.read_csv(REFERENCE_PATH)
    seed = pd.read_csv(SEED_PATH)
    errors = check(ref, seed)
    if errors:
        print("WAGE REFERENCE CHECK FAILED:")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    counts = ref["split"].value_counts()
    print(f"Wage reference check passed: {len(ref)} clubs "
          f"(fit {counts.get('fit', 0)}, test {counts.get('test', 0)})")


if __name__ == "__main__":
    main()
