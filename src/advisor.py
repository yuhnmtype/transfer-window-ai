"""Rule-based advisor for ONE permanent deal (no LLM, no database writes).

All SCR arithmetic is delegated to src/scr.py. This module adds input checks, fee currency
conversion, a wage sensitivity table and the maximum affordable incoming wage.
Wages are always user inputs: the DB has no wages and the wage model was not accepted
(results/wage_model_eval.json). No result is a real-world conclusion.
"""
import math
import tomllib
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from scr import (
    CONFIG_DIR, Deal, SCRResult, annual_amortisation, deal_delta, eur_to_gbp,
    evaluate_club, evaluate_with_deal, load_club_finance, threshold_for_club,
)
from wages import WEEKS_PER_YEAR

CURRENCIES = {"EUR", "GBP"}

# Club warnings that apply to every club; the text output shows them once, as caveats.
ALWAYS_ON_WARNINGS = {"wage_bill_club_wide", "impairment_excluded", "profit_on_sales_not_net"}
# Warnings the text output leaves out because a caveat already says the same (JSON keeps them).
TEXT_HIDDEN_WARNINGS = ALWAYS_ON_WARNINGS | {"sale_profit_ignored"}

DEAL_WARNING_TEXT = {
    "user_supplied_wages": "Wages are user inputs. The DB has no wages and the wage model "
                           "was not accepted.",
    "agent_fee_assumed_zero": "Agent fee was not given and is assumed to be 0.",
    "amortisation_capped": "The contract is longer than the amortisation cap, so the fee is "
                           "spread over the cap instead.",
    "fee_converted_from_eur": "The fee was converted from EUR at the fixed config rate.",
    "sale_profit_ignored": "Profit on the outgoing player's sale is ignored, which "
                           "understates the benefit of selling.",
    "outgoing_amortisation_estimated": "The outgoing amortisation is an approximation from "
                                       "the transfer history (confidence low).",
}

AMBER_NOTE = ('"amber" is our own label for the band between the green and red thresholds, '
              'not an official Premier League term.')

MAX_WAGE_NOTE = "Only the incoming wage changes; all other inputs stay the same."

UNDERSTATED_NOTE = ("Estimate may be understated (the contract was probably extended): "
                    "supply --out-amortisation-gbp-m if you know the real figure.")

CAP_APPLIED_NOTE = ("Amortisation cap applied: if the cap does not apply to this older "
                    "contract, the real annual charge may be lower than the estimate.")


class IneligibleBuyerError(ValueError):
    """The club cannot be a buyer under Premier League SCR (not in the PL next season)."""


@dataclass(frozen=True)
class IncomingPlayer:
    """User-supplied deal terms. No field is ever inferred from the database."""
    fee_amount: float
    fee_currency: str
    annual_wage_gbp_m: float
    contract_years: float
    agent_fee_gbp_m: float | None = None  # None = not given (assumed 0, with a warning)


@dataclass(frozen=True)
class OutgoingPlayer:
    annual_wage_gbp_m: float
    annual_amortisation_gbp_m: float  # 0 is allowed (academy or free signing)
    amortisation_source: str = "user-supplied"  # or "estimated from transfer history"
    amortisation_detail: dict | None = None     # the lookup result, when estimated


@dataclass(frozen=True)
class MaxWage:
    annual_gbp_m: float | None  # None when even a zero wage exceeds the limit
    weekly_gbp_k: float | None
    note: str


@dataclass(frozen=True)
class AdviceResult:
    club_id: int
    club_name: str
    season: str
    fiscal_year: str
    threshold_basis: str
    before: SCRResult
    after: SCRResult
    status_change: str
    headroom_change_gbp_m: float
    fee_gbp_m: float
    gbp_per_eur_used: float | None  # None when the fee was given in GBP
    incoming_cost: dict
    outgoing: dict | None
    sensitivity: list
    max_wage_green: MaxWage
    max_wage_red: MaxWage
    warnings: dict  # code -> text, club-level then deal-level
    caveats: list
    amber_note: str


# ---------- Advisor config ----------

def validate_advisor_config(config):
    """Fail fast if the sensitivity offsets are missing or invalid."""
    offsets = config.get("sensitivity", {}).get("wage_offsets")
    if not isinstance(offsets, list) or not offsets:
        raise ValueError("advisor config: [sensitivity] wage_offsets must be a non-empty list")
    for value in offsets:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > -1:
            raise ValueError(f"advisor config: wage offset {value!r} must be a number > -1")


def load_advisor_config(path=CONFIG_DIR / "advisor.toml"):
    with open(path, "rb") as f:
        config = tomllib.load(f)
    validate_advisor_config(config)
    return config


# ---------- Pure helpers ----------

def _check_number(name, value, *, minimum, strict):
    """Raise ValueError unless value is a finite number above (or at) minimum."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    if value < minimum or (strict and value == minimum):
        sign = ">" if strict else ">="
        raise ValueError(f"{name} must be {sign} {minimum}, got {value}")


def validate_inputs(incoming, outgoing):
    """Check every user input before any calculation."""
    if incoming.fee_currency not in CURRENCIES:
        raise ValueError(f"fee_currency must be one of {sorted(CURRENCIES)}, "
                         f"got {incoming.fee_currency!r}")
    _check_number("fee_amount", incoming.fee_amount, minimum=0, strict=False)
    _check_number("annual_wage_gbp_m", incoming.annual_wage_gbp_m, minimum=0, strict=True)
    _check_number("contract_years", incoming.contract_years, minimum=0, strict=True)
    if incoming.agent_fee_gbp_m is not None:
        _check_number("agent_fee_gbp_m", incoming.agent_fee_gbp_m, minimum=0, strict=False)
    if outgoing is not None:
        _check_number("outgoing annual_wage_gbp_m", outgoing.annual_wage_gbp_m,
                      minimum=0, strict=False)
        _check_number("outgoing annual_amortisation_gbp_m",
                      outgoing.annual_amortisation_gbp_m, minimum=0, strict=False)


def fee_to_gbp(amount, currency, config):
    """Fee in GBP m; EUR is converted with the fixed config rate."""
    if currency == "GBP":
        return amount
    if currency == "EUR":
        return eur_to_gbp(amount, config)
    raise ValueError(f"Unknown currency: {currency!r}")


def threshold_basis(uefa_next_season, config):
    """Explain which thresholds apply and why."""
    green, red = threshold_for_club(uefa_next_season, config)
    if uefa_next_season in config["scr"]["uefa"]["competitions"]:
        reason = f"UEFA competition next season ({uefa_next_season})"
    else:
        reason = "No UEFA competition next season"
    return f"{reason}: green {green:.0%}, red {red:.0%}"


def max_wage(headroom_gbp_m, non_wage_delta_gbp_m, limit_name):
    """Largest incoming annual wage that keeps squad cost at the limit.

    squad cost after = baseline + non_wage_delta + wage, and adjusted revenue does not change,
    so the limit is reached at wage = headroom - non_wage_delta.
    """
    value = headroom_gbp_m - non_wage_delta_gbp_m
    if value < 0:
        return MaxWage(None, None, f"Even a zero wage exceeds the {limit_name} limit. "
                                   + MAX_WAGE_NOTE)
    return MaxWage(value, value * 1000 / WEEKS_PER_YEAR, MAX_WAGE_NOTE)


def standing_caveats(config):
    return [
        "The club-wide wage bill overstates squad costs (non-playing staff included).",
        "Player impairment is counted as 0.",
        "Profit on the outgoing player's sale is ignored.",
        f"Finance data is FY {config['data_vintage']['finance_fiscal_year']} from secondary "
        "sources, not checked against Companies House.",
        f"EUR to GBP uses a fixed rate of {config['fx']['gbp_per_eur']}, not point-in-time.",
        "Rules come from secondary sources; the Premier League Handbook text was not checked.",
        "This is not a real-world conclusion.",
    ]


# ---------- Advisor ----------

def advise_deal(conn, config, buyer_club_id, incoming, outgoing=None, *, advisor_config):
    """SCR impact of one permanent deal for the buyer club. Reads the DB, never writes."""
    validate_inputs(incoming, outgoing)
    row = load_club_finance(conn, buyer_club_id, config["data_vintage"]["finance_fiscal_year"])
    if not row["in_pl_next_season"]:
        raise IneligibleBuyerError(
            f"{row['club_name']} (club_id {buyer_club_id}) is not in the Premier League next "
            "season, so it cannot be a buyer under Premier League SCR.")

    fee_gbp_m = fee_to_gbp(incoming.fee_amount, incoming.fee_currency, config)
    agent_fee = incoming.agent_fee_gbp_m or 0.0
    out_wage = outgoing.annual_wage_gbp_m if outgoing else 0.0
    out_amort = outgoing.annual_amortisation_gbp_m if outgoing else 0.0
    deal = Deal(in_fee_gbp_m=fee_gbp_m, in_wage_gbp_m=incoming.annual_wage_gbp_m,
                in_contract_years=incoming.contract_years, in_agent_fee_gbp_m=agent_fee,
                out_wage_gbp_m=out_wage, out_amortisation_gbp_m=out_amort)

    before = evaluate_club(row, config)
    after, headroom_change = evaluate_with_deal(row, deal, config)

    sensitivity = []
    for offset in advisor_config["sensitivity"]["wage_offsets"]:
        wage = incoming.annual_wage_gbp_m * (1 + offset)
        scenario, _ = evaluate_with_deal(row, replace(deal, in_wage_gbp_m=wage), config)
        sensitivity.append({
            "offset": offset, "annual_wage_gbp_m": wage,
            "weekly_wage_gbp_k": wage * 1000 / WEEKS_PER_YEAR,
            "squad_cost_gbp_m": scenario.squad_cost_gbp_m, "ratio": scenario.ratio,
            "headroom_gbp_m": scenario.headroom_gbp_m, "status": scenario.status,
        })

    non_wage_delta = deal_delta(replace(deal, in_wage_gbp_m=0.0), config)
    red_headroom = before.headroom_gbp_m + (before.red - before.green) * before.adjusted_revenue_gbp_m

    cap = config["amortisation"]["cap_years"]
    deal_codes = ["user_supplied_wages"]
    if incoming.agent_fee_gbp_m is None:
        deal_codes.append("agent_fee_assumed_zero")
    if incoming.contract_years > cap:
        deal_codes.append("amortisation_capped")
    if incoming.fee_currency == "EUR":
        deal_codes.append("fee_converted_from_eur")
    if outgoing is not None:
        deal_codes.append("sale_profit_ignored")
        if outgoing.amortisation_detail is not None:
            deal_codes.append("outgoing_amortisation_estimated")

    return AdviceResult(
        club_id=row["club_id"], club_name=row["club_name"], season=config["season"],
        fiscal_year=row["fiscal_year"],
        threshold_basis=threshold_basis(row["uefa_next_season"], config),
        before=before, after=after, status_change=f"{before.status} -> {after.status}",
        headroom_change_gbp_m=headroom_change, fee_gbp_m=fee_gbp_m,
        gbp_per_eur_used=config["fx"]["gbp_per_eur"] if incoming.fee_currency == "EUR" else None,
        incoming_cost={
            "amortisation_years": min(incoming.contract_years, cap),
            "annual_amortisation_gbp_m": annual_amortisation(fee_gbp_m + agent_fee,
                                                             incoming.contract_years, cap),
            "agent_fee_gbp_m": agent_fee,
            "annual_wage_gbp_m": incoming.annual_wage_gbp_m,
        },
        outgoing=asdict(outgoing) if outgoing else None,
        sensitivity=sensitivity,
        max_wage_green=max_wage(before.headroom_gbp_m, non_wage_delta, "green"),
        max_wage_red=max_wage(red_headroom, non_wage_delta, "red"),
        warnings={**after.warnings, **{c: DEAL_WARNING_TEXT[c] for c in deal_codes}},
        caveats=standing_caveats(config), amber_note=AMBER_NOTE,
    )


def eligible_clubs(conn, fiscal_year):
    """[(club_id, club_name, uefa_next_season)] of clubs that can be buyers."""
    return conn.execute(
        "SELECT club_id, club_name, uefa_next_season FROM club_finance "
        "WHERE fiscal_year = ? AND in_pl_next_season = 1 ORDER BY club_name",
        (fiscal_year,),
    ).fetchall()


# ---------- Output ----------

def to_dict(result):
    """Plain dict for JSON output (keeps every warning, including the always-on ones)."""
    return asdict(result)


def format_text(result):
    """Human-readable report. The most serious warning comes first."""
    lines = []
    if "championship_revenue" in result.warnings:
        lines += ["!" * 78,
                  "!!! WARNING: " + result.warnings["championship_revenue"],
                  "!" * 78, ""]

    b, a = result.before, result.after
    cost = result.incoming_cost
    lines += [
        f"SCR advice: {result.club_name} (club_id {result.club_id}), season {result.season}, "
        f"finance FY {result.fiscal_year}",
        f"Thresholds: {result.threshold_basis}",
        "",
    ]
    fee_line = f"Fee: {result.fee_gbp_m:.2f} GBP m"
    if result.gbp_per_eur_used is not None:
        fee_line += f" (converted from EUR at {result.gbp_per_eur_used})"
    lines += [
        fee_line,
        f"Incoming annual cost: amortisation {cost['annual_amortisation_gbp_m']:.2f} "
        f"(fee + agent fee {cost['agent_fee_gbp_m']:.2f}, over {cost['amortisation_years']:g} "
        f"years) + wage {cost['annual_wage_gbp_m']:.2f} GBP m",
    ]
    if result.outgoing:
        out = result.outgoing
        lines.append(f"Outgoing annual cost removed: wage {out['annual_wage_gbp_m']:.2f} + "
                     f"amortisation {out['annual_amortisation_gbp_m']:.2f} GBP m "
                     f"(amortisation {out['amortisation_source']})")
        detail = out["amortisation_detail"]
        if detail:
            if detail["possibly_understated"]:
                lines.append(f"  !!! {UNDERSTATED_NOTE}")
            if detail["cap_applied"]:
                lines.append(f"  Note: {CAP_APPLIED_NOTE}")
            lines.append(f"  {detail['status']}: {detail['reason']}")
            lines += [f"  - {w}" for w in detail["warnings"]]
    lines += [
        "",
        f"{'':22}{'Before':>10}{'After':>10}",
        f"{'Adjusted revenue':22}{b.adjusted_revenue_gbp_m:>10.2f}{a.adjusted_revenue_gbp_m:>10.2f}",
        f"{'Squad cost':22}{b.squad_cost_gbp_m:>10.2f}{a.squad_cost_gbp_m:>10.2f}",
        f"{'SCR ratio':22}{b.ratio:>10.1%}{a.ratio:>10.1%}",
        f"{'Status':22}{b.status:>10}{a.status:>10}",
        f"{'Headroom to green':22}{b.headroom_gbp_m:>10.2f}{a.headroom_gbp_m:>10.2f}"
        f"   (change {result.headroom_change_gbp_m:+.2f})",
        "",
        "Wage sensitivity (only the incoming wage changes):",
        f"{'offset':>8}{'GBP m/yr':>10}{'GBP k/wk':>10}{'ratio':>8}{'headroom':>10}{'status':>8}",
    ]
    for s in result.sensitivity:
        lines.append(f"{s['offset']:>+8.0%}{s['annual_wage_gbp_m']:>10.2f}"
                     f"{s['weekly_wage_gbp_k']:>10.1f}{s['ratio']:>8.1%}"
                     f"{s['headroom_gbp_m']:>10.2f}{s['status']:>8}")

    lines += ["", f"Maximum incoming wage ({MAX_WAGE_NOTE})"]
    for label, mw in (("to stay green", result.max_wage_green),
                      ("to stay at or below red", result.max_wage_red)):
        if mw.annual_gbp_m is None:
            lines.append(f"  {label}: none (even a zero wage exceeds this limit)")
        else:
            lines.append(f"  {label}: {mw.annual_gbp_m:.2f} GBP m/yr "
                         f"({mw.weekly_gbp_k:.1f} GBP k/week)")

    others = {c: t for c, t in result.warnings.items()
              if c not in TEXT_HIDDEN_WARNINGS and c != "championship_revenue"}
    if others:
        lines += ["", "Warnings:"] + [f"  - {t}" for t in others.values()]
    lines += ["", f"Note: {result.amber_note}", "", "Caveats:"]
    lines += [f"  - {c}" for c in result.caveats]
    return "\n".join(lines)
