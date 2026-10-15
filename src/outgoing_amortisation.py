"""Approximate annual amortisation of an outgoing player, from the transfer history.

The result is an approximation with confidence "low":
- original contract length = contract expiry (current, after any extension) - purchase date,
  capped at the config amortisation cap;
- straight-line amortisation, measured at config [amortisation] as_of;
- the historical EUR fee is converted at the fixed config rate.
The transfers table has no loan flag: loans usually appear as fee-0 moves. A fee-0 move back
INTO a club the player had left before is treated as a loan return and skipped.
"Unknown" is never turned into 0. transfer_fee_clean is never used (it is filled with
market values).
"""
import math
from dataclasses import dataclass, field
from datetime import date, datetime

from scr import eur_to_gbp

DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class AmortisationEstimate:
    status: str                 # "estimated", "zero_fee", "likely_fully_amortised" or "unknown"
    annual_gbp_m: float | None  # None only when status is "unknown"
    reason: str
    confidence: str = "low"
    # True only for likely_fully_amortised: the contract was probably extended, and an
    # extension spreads the remaining book value over the new term, so the real charge may
    # be above 0.
    possibly_understated: bool = False
    # True when the cap shortened the amortisation period and the player is still being
    # amortised. If the cap does not apply to an older contract, the real charge may be lower.
    cap_applied: bool = False
    purchase_date: str | None = None
    fee_eur_m: float | None = None
    fee_gbp_m: float | None = None
    contract_years: float | None = None
    years_used: float | None = None
    remaining_years: float | None = None
    warnings: list = field(default_factory=list)


def to_date(value):
    """Accept a date, a datetime or an ISO string such as '2021-08-31 00:00:00'."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def years_between(start, end):
    return (to_date(end) - to_date(start)).days / DAYS_PER_YEAR


def _is_missing(fee):
    return fee is None or (isinstance(fee, float) and math.isnan(fee))


def pick_purchase(transfers, club_id):
    """Return (purchase record or None, ever_left) for one player and one club.

    transfers: dicts with transfer_date, from_club_id, to_club_id, transfer_fee (any order).
    A fee-0 move into the club after the player had already left it is a loan return and is
    skipped; the most recent remaining move into the club is the purchase.
    ever_left: the player moved out of the club at least once.
    """
    ordered = sorted(transfers, key=lambda t: to_date(t["transfer_date"]))
    exits = [to_date(t["transfer_date"]) for t in ordered if t["from_club_id"] == club_id]
    for record in reversed(ordered):
        if record["to_club_id"] != club_id:
            continue
        moved_on = to_date(record["transfer_date"])
        is_return = record["transfer_fee"] == 0 and any(d < moved_on for d in exits)
        if not is_return:
            return record, bool(exits)
    return None, bool(exits)


def standard_warnings(config):
    return [
        "Approximation (confidence low): straight-line amortisation estimated from the "
        "transfer history.",
        "The contract length uses the current expiry date. When a contract is extended, the "
        "remaining book value is spread over the new term, so after an extension the real "
        "charge can be higher than this estimate, and above 0 even when it says fully "
        "amortised.",
        f"Whether the {config['amortisation']['cap_years']}-year amortisation cap applies to "
        "contracts signed before 2026/27 is unverified.",
        f"The historical EUR fee is converted at the fixed rate {config['fx']['gbp_per_eur']}, "
        "not the rate at the time of the purchase.",
        "A recorded fee may be a loan fee; the data does not flag loans.",
    ]


def estimate_annual_amortisation(purchase, ever_left, contract_expiry, as_of, config):
    """Annual amortisation charge (GBP m) at as_of for the chosen purchase record.

    charge = fee_gbp / years_used, limited to the remaining book value when less than one
    year is left: fee_gbp * remaining / years_used, where
    years_used = min(contract length, cap) and remaining = purchase + years_used - as_of.
    """
    if purchase is None:
        return AmortisationEstimate("unknown", None, "No transfer into the club was found "
                                    "(academy player, or before the data starts).")
    fee = purchase["transfer_fee"]
    purchase_date = to_date(purchase["transfer_date"])
    if _is_missing(fee):
        return AmortisationEstimate("unknown", None, "The transfer fee was not recorded.",
                                    purchase_date=purchase_date.isoformat())
    if fee == 0:
        warnings = ["Approximation (confidence low): a recorded fee of 0 is treated as a "
                    "free transfer."]
        if ever_left:
            warnings.append("The player has moved out of this club before: this may be a "
                            "free transfer, a loan or an academy player, and the data cannot "
                            "tell them apart.")
        return AmortisationEstimate("zero_fee", 0.0, "The recorded fee is 0.",
                                    purchase_date=purchase_date.isoformat(), fee_eur_m=0.0,
                                    fee_gbp_m=0.0, warnings=warnings)
    if contract_expiry is None:
        return AmortisationEstimate("unknown", None, "The contract expiry date is missing.",
                                    purchase_date=purchase_date.isoformat())

    contract_years = years_between(purchase_date, contract_expiry)
    if contract_years <= 0:
        return AmortisationEstimate("unknown", None, "The contract expiry is not after the "
                                    "purchase date.", purchase_date=purchase_date.isoformat())

    fee_eur_m = fee / 1_000_000
    fee_gbp_m = eur_to_gbp(fee_eur_m, config)
    cap = config["amortisation"]["cap_years"]
    years_used = min(contract_years, cap)
    remaining = years_used - years_between(purchase_date, as_of)
    details = dict(purchase_date=purchase_date.isoformat(), fee_eur_m=fee_eur_m,
                   fee_gbp_m=fee_gbp_m, contract_years=contract_years,
                   years_used=years_used, remaining_years=remaining)
    warnings = standard_warnings(config)
    if remaining <= 0:
        return AmortisationEstimate("likely_fully_amortised", 0.0,
                                    "The fee is likely fully amortised by the as_of date.",
                                    possibly_understated=True, warnings=warnings, **details)
    annual = fee_gbp_m / years_used * min(1.0, remaining)
    reason = ("Less than one year left: limited to the remaining book value."
              if remaining < 1 else "Full-year straight-line charge.")
    cap_applied = contract_years > cap
    if cap_applied:
        warnings.append(f"The {cap}-year cap shortened the amortisation period of this "
                        f"{contract_years:.1f}-year contract. If the cap does not apply to "
                        "this older contract, the real annual charge may be lower than this "
                        "estimate.")
    return AmortisationEstimate("estimated", annual, reason, cap_applied=cap_applied,
                                warnings=warnings, **details)


# ---------- Database ----------

def load_transfers(conn, player_id):
    rows = conn.execute(
        "SELECT transfer_date, from_club_id, to_club_id, transfer_fee FROM transfers "
        "WHERE player_id = ?", (player_id,)).fetchall()
    keys = ("transfer_date", "from_club_id", "to_club_id", "transfer_fee")
    return [dict(zip(keys, row)) for row in rows]


def lookup_outgoing_amortisation(conn, config, player_id, club_id):
    """Estimate for a player in the club's current squad. Reads the DB, never writes."""
    player = conn.execute(
        "SELECT current_club_id, is_current_squad, contract_expiration_date FROM players "
        "WHERE player_id = ?", (player_id,)).fetchone()
    if player is None:
        raise LookupError(f"No player with player_id={player_id}")
    current_club_id, in_squad, expiry = player
    if current_club_id != club_id or not in_squad:
        raise ValueError(f"player_id {player_id} is not in the current squad of "
                         f"club_id {club_id}")
    purchase, ever_left = pick_purchase(load_transfers(conn, player_id), club_id)
    return estimate_annual_amortisation(purchase, ever_left, expiry,
                                        to_date(config["amortisation"]["as_of"]), config)
