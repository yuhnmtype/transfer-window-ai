"""Squad Cost Ratio (SCR) calculator for one club and one season.

All money values are GBP millions (suffix _gbp_m). The calculation functions are pure.
I/O lives only in load_config and load_club_finance at the bottom of the file.
Rule values come from config/rules_<season>.toml; see CLAUDE.md "Rules" for their sources.
"""
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# Ratios within this distance of a threshold count as on it (float rounding only).
STATUS_EPSILON = 1e-9

WARNING_TEXT = {
    "wage_bill_club_wide": "Wage bill is club-wide (includes non-playing staff), "
                           "so squad cost is overstated.",
    "impairment_excluded": "Player impairment is not in the seed and is counted as 0, "
                           "so squad cost is understated.",
    "profit_on_sales_not_net": "Seed holds profit on player sales, not net player trading "
                               "profit as the rule defines.",
    "wage_scope_factor_applied": "wage_scope_factor is not 1.0: sensitivity scenario only, "
                                 "not a fact.",
    "not_in_pl_next_season": "Club is not in the Premier League next season, so Premier "
                             "League SCR does not apply to it as a buyer.",
    "championship_revenue": "Revenue is Championship revenue. The ratio is not representative "
                            "of Premier League income and must not be presented as a real "
                            "conclusion.",
    "non_12_month_period": "Accounts do not cover 12 months. Figures are not converted.",
    "low_confidence_seed": "Seed row has confidence 'low'.",
}


@dataclass(frozen=True)
class SCRResult:
    club_id: int
    club_name: str
    season: str
    fiscal_year: str
    adjusted_revenue_gbp_m: float
    squad_cost_gbp_m: float
    ratio: float
    green: float
    red: float
    headroom_gbp_m: float
    status: str
    confidence: str
    source_url: str
    warnings: dict  # code -> text, see WARNING_TEXT


@dataclass(frozen=True)
class Deal:
    """One permanent deal: an incoming player and, optionally, an outgoing one.

    in_wage_gbp_m is supplied by the user (the DB has no wages); it is never estimated.
    out_amortisation_gbp_m is the outgoing player's annual amortisation charge.
    """
    in_fee_gbp_m: float
    in_wage_gbp_m: float
    in_contract_years: float
    in_agent_fee_gbp_m: float = 0.0
    out_wage_gbp_m: float = 0.0
    out_amortisation_gbp_m: float = 0.0
    deal_type: str = "permanent"


# ---------- Pure calculation ----------

def adjusted_revenue(revenue_gbp_m, profit_on_player_sales_gbp_m, growth):
    """Football revenue grown by `growth`, plus profit on player sales.

    Growth applies to revenue only, not to profit on sales. Wages are never grown, so any
    growth other than 0 makes headroom optimistic.
    """
    return revenue_gbp_m * (1 + growth) + profit_on_player_sales_gbp_m


def baseline_squad_cost(wage_bill_gbp_m, amortisation_gbp_m, wage_scope_factor):
    """Published wages (scaled by wage_scope_factor) plus published amortisation.

    Impairment is counted as 0 because the seed has no impairment column, so the result
    understates squad cost. Agents' fees are not in the seed either.
    """
    return wage_bill_gbp_m * wage_scope_factor + amortisation_gbp_m


def threshold_for_club(uefa_next_season, config):
    """Return (green, red) thresholds for the club's UEFA status next season."""
    uefa = config["scr"]["uefa"]
    if uefa_next_season in uefa["competitions"]:
        return uefa["green"], uefa["red"]
    if uefa_next_season == uefa["no_competition"]:
        standard = config["scr"]["standard"]
        return standard["green"], standard["red"]
    raise ValueError(f"Unknown uefa_next_season value: {uefa_next_season!r}")


def scr_ratio(squad_cost_gbp_m, adjusted_revenue_gbp_m):
    """Squad cost divided by adjusted revenue."""
    if adjusted_revenue_gbp_m <= 0:
        raise ValueError(f"Adjusted revenue must be positive, got {adjusted_revenue_gbp_m}")
    return squad_cost_gbp_m / adjusted_revenue_gbp_m


def headroom(squad_cost_gbp_m, adjusted_revenue_gbp_m, green):
    """GBP m the club can still add before reaching the green limit (negative = over)."""
    return green * adjusted_revenue_gbp_m - squad_cost_gbp_m


def status(ratio, green, red):
    """Return "green", "amber" or "red".

    "amber" is our own label for the band between the green and red thresholds, not an
    official Premier League term. A ratio up to STATUS_EPSILON above a threshold still
    counts as within it, so float rounding cannot flip an exact boundary case.
    """
    if ratio <= green + STATUS_EPSILON:
        return "green"
    if ratio <= red + STATUS_EPSILON:
        return "amber"
    return "red"


def eur_to_gbp(amount_eur_m, config):
    """Convert EUR millions to GBP millions with the fixed config rate."""
    return amount_eur_m * config["fx"]["gbp_per_eur"]


def annual_amortisation(amount_gbp_m, contract_years, cap_years):
    """Straight-line annual charge over min(contract_years, cap_years)."""
    if contract_years <= 0:
        raise ValueError(f"contract_years must be positive, got {contract_years}")
    return amount_gbp_m / min(contract_years, cap_years)


def deal_delta(deal, config):
    """Change in annual squad cost (GBP m) caused by one permanent deal.

    Incoming: (fee + agent fee) spread over min(contract years, cap), plus annual wage.
    Spreading the agent fee like the transfer fee is an unsourced assumption.
    Outgoing: minus the player's annual wage and annual amortisation.
    Profit on the outgoing player's sale is ignored, so this understates the benefit of
    selling. Loans are not supported yet.
    """
    if deal.deal_type == "loan":
        raise NotImplementedError("Loan deals are not supported yet")
    if deal.deal_type != "permanent":
        raise ValueError(f"Unknown deal_type: {deal.deal_type!r}")
    money = [deal.in_fee_gbp_m, deal.in_wage_gbp_m, deal.in_agent_fee_gbp_m,
             deal.out_wage_gbp_m, deal.out_amortisation_gbp_m]
    if any(value < 0 for value in money):
        raise ValueError("Deal money values cannot be negative")

    cap_years = config["amortisation"]["cap_years"]
    incoming = (annual_amortisation(deal.in_fee_gbp_m + deal.in_agent_fee_gbp_m,
                                    deal.in_contract_years, cap_years)
                + deal.in_wage_gbp_m)
    outgoing = deal.out_wage_gbp_m + deal.out_amortisation_gbp_m
    return incoming - outgoing


def club_warnings(row, config):
    """Return {code: text} for every caveat that applies to this club_finance row."""
    codes = ["wage_bill_club_wide", "impairment_excluded", "profit_on_sales_not_net"]
    if config["assumptions"]["wage_scope_factor"] != 1.0:
        codes.append("wage_scope_factor_applied")
    if not row["in_pl_next_season"]:
        codes.append("not_in_pl_next_season")
    if row["revenue_basis"] == "CHAMPIONSHIP":
        codes.append("championship_revenue")
    if row["period_months"] != 12:
        codes.append("non_12_month_period")
    if row["confidence"] == "low":
        codes.append("low_confidence_seed")
    return {code: WARNING_TEXT[code] for code in codes}


def _result(row, config, extra_cost_gbp_m):
    """Build an SCRResult for the row, with extra_cost_gbp_m added to the baseline."""
    expected_year = config["data_vintage"]["finance_fiscal_year"]
    if row["fiscal_year"] != expected_year:
        raise ValueError(f"Row fiscal_year {row['fiscal_year']!r} does not match config "
                         f"finance_fiscal_year {expected_year!r}")
    assumptions = config["assumptions"]
    adj_rev = adjusted_revenue(row["revenue_gbp_m"], row["profit_on_player_sales_gbp_m"],
                               assumptions["revenue_growth_assumption"])
    cost = baseline_squad_cost(row["wage_bill_gbp_m"], row["player_amortisation_gbp_m"],
                               assumptions["wage_scope_factor"]) + extra_cost_gbp_m
    green, red = threshold_for_club(row["uefa_next_season"], config)
    ratio = scr_ratio(cost, adj_rev)
    return SCRResult(
        club_id=row["club_id"], club_name=row["club_name"], season=config["season"],
        fiscal_year=row["fiscal_year"], adjusted_revenue_gbp_m=adj_rev,
        squad_cost_gbp_m=cost, ratio=ratio, green=green, red=red,
        headroom_gbp_m=headroom(cost, adj_rev, green), status=status(ratio, green, red),
        confidence=row["confidence"], source_url=row["source_url"],
        warnings=club_warnings(row, config),
    )


def evaluate_club(row, config):
    """SCR position of one club (a club_finance row as a dict) before any deal."""
    return _result(row, config, extra_cost_gbp_m=0.0)


def evaluate_with_deal(row, deal, config):
    """Return (SCRResult after the deal, change in headroom in GBP m)."""
    before = _result(row, config, extra_cost_gbp_m=0.0)
    after = _result(row, config, extra_cost_gbp_m=deal_delta(deal, config))
    return after, after.headroom_gbp_m - before.headroom_gbp_m


# ---------- Config validation ----------

def _number(config, section, key, low=None, high=None):
    """Return config[section...][key] as a number, raising ValueError if invalid."""
    node = config
    for part in section.split("."):
        node = node.get(part)
        if not isinstance(node, dict):
            raise ValueError(f"config: missing section [{section}]")
    value = node.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"config: [{section}] {key} must be a number, got {value!r}")
    if (low is not None and value <= low) or (high is not None and value > high):
        raise ValueError(f"config: [{section}] {key}={value} is out of range")
    return value


def validate_config(config, season):
    """Fail fast if the config is incomplete, inconsistent or uses unsupported options."""
    if config.get("season") != season:
        raise ValueError(f"config: season {config.get('season')!r} != requested {season!r}")
    fiscal_year = config.get("data_vintage", {}).get("finance_fiscal_year")
    if not isinstance(fiscal_year, str) or not fiscal_year:
        raise ValueError("config: [data_vintage] finance_fiscal_year is required")
    for band in ("scr.standard", "scr.uefa"):
        green = _number(config, band, "green", low=0)
        red = _number(config, band, "red", low=0)
        if green >= red:
            raise ValueError(f"config: [{band}] green must be below red")
    uefa = config["scr"]["uefa"]
    if not uefa.get("competitions") or not all(isinstance(c, str) for c in uefa["competitions"]):
        raise ValueError("config: [scr.uefa] competitions must be a non-empty list of strings")
    if not isinstance(uefa.get("no_competition"), str):
        raise ValueError("config: [scr.uefa] no_competition must be a string")
    _number(config, "amortisation", "cap_years", low=0)
    _number(config, "fx", "gbp_per_eur", low=0)
    _number(config, "assumptions", "revenue_growth_assumption", low=-1)
    _number(config, "assumptions", "wage_scope_factor", low=0)
    avg_years = _number(config, "assumptions", "player_sales_avg_years", low=0)
    if not isinstance(avg_years, int):
        raise ValueError("config: [assumptions] player_sales_avg_years must be a whole number")
    if avg_years > 1:
        raise NotImplementedError("player_sales_avg_years > 1 is not supported yet")
    _number(config, "sanction", "red_breach_base_points", low=0)
    _number(config, "sanction", "red_breach_gbp_m_per_point", low=0)


# ---------- I/O ----------

def load_config(season, config_dir=CONFIG_DIR):
    """Read config/rules_<season>.toml (e.g. "2026/27" -> rules_2026_27.toml) and validate it."""
    path = Path(config_dir) / f"rules_{season.replace('/', '_')}.toml"
    with open(path, "rb") as f:
        config = tomllib.load(f)
    validate_config(config, season)
    return config


def load_club_finance(conn, club_id, fiscal_year):
    """Return one club_finance row as a dict. Raises LookupError if it does not exist."""
    cursor = conn.execute(
        "SELECT * FROM club_finance WHERE club_id = ? AND fiscal_year = ?",
        (club_id, fiscal_year),
    )
    row = cursor.fetchone()
    if row is None:
        raise LookupError(f"No club_finance row for club_id={club_id}, fiscal_year={fiscal_year}")
    return dict(zip([col[0] for col in cursor.description], row))
