"""Per-player wage model: allocate a club's wage pool across its squad by market value.

Model (pre-registered in docs/wage_model_criteria.md, tag wage-criteria-v1):
    wage_i = pool * MV_i^alpha / sum_j(MV_j^alpha), within one club only.
The output is a labelled assumption with confidence "low", never a fact.
Money values are GBP millions per year (suffix _gbp_m) unless named _k (GBP thousands/week).
The calculation functions are pure; load_squad is the only function that reads the DB.
"""
import math

WEEKS_PER_YEAR = 52


def allocate_wages(market_values, pool_gbp_m, alpha):
    """Split pool_gbp_m across players in proportion to market_value^alpha.

    market_values: {player_id: market value} (any currency; only ratios matter).
    Returns {player_id: annual wage in GBP m}; the wages sum to pool_gbp_m.
    alpha = 0 gives an equal split. Invalid market values raise instead of being dropped:
    excluding players is the loader's job (squad definition), not this function's.
    """
    if not market_values:
        raise ValueError("Squad is empty")
    if not pool_gbp_m > 0:
        raise ValueError(f"pool_gbp_m must be positive, got {pool_gbp_m}")
    if not alpha >= 0:
        raise ValueError(f"alpha must be >= 0, got {alpha}")
    for player_id, value in market_values.items():
        if not (math.isfinite(value) and value > 0):
            raise ValueError(f"Invalid market value for player {player_id}: {value}")

    # Divide by the largest value first: same ratios, but no overflow for large alpha.
    top = max(market_values.values())
    weights = {pid: (value / top) ** alpha for pid, value in market_values.items()}
    total = sum(weights.values())
    return {pid: pool_gbp_m * weight / total for pid, weight in weights.items()}


def top_weekly_k(wages):
    """Highest annual wage (GBP m) converted to GBP thousands per week (x1000 / 52)."""
    if not wages:
        raise ValueError("No wages given")
    return max(wages.values()) * 1000 / WEEKS_PER_YEAR


def load_squad(conn, club_id):
    """Return {player_id: market_value_in_eur} for the club's current squad.

    Squad as registered: is_current_squad = 1 and a non-null, positive market value.
    """
    rows = conn.execute(
        "SELECT player_id, market_value_in_eur FROM players "
        "WHERE current_club_id = ? AND is_current_squad = 1 "
        "AND market_value_in_eur IS NOT NULL AND market_value_in_eur > 0",
        (club_id,),
    ).fetchall()
    return {player_id: float(value) for player_id, value in rows}
