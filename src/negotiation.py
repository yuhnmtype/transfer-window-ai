"""Negotiation engine v1: one seller, 2-3 buyers, one round of sealed bids (no LLM).

Seller reserve and buyer valuation are both the registered fee-model fallback,
m x market value, with m read from results/fee_model_eval.json (never hard-coded).
A buyer's ceiling is min(valuation, the largest fee that keeps it within the acceptance
limit), computed analytically for each wage scenario. Because reserve and valuation are the
same number, every buyer that can afford the reserve bids exactly the reserve, so the winner
is decided by the tie-break (smaller club_id). The headline result is therefore the SET of
buyers that can afford the fee; the winner is secondary and always shows won_by_tiebreak.
All arithmetic on SCR comes from scr.py and advisor.py. The database is only read.
"""
import hashlib
import json
import tomllib
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from advisor import IncomingPlayer, advise_deal, eligible_clubs
from outgoing_amortisation import to_date
from scr import CONFIG_DIR, eur_to_gbp, evaluate_club, load_club_finance

REPO_ROOT = Path(__file__).resolve().parent.parent
FALLBACK_LABEL = "fallback: market value x training median ratio"
RULES = {"green", "not_red"}
MAX_VALUATION_AGE_DAYS = 365

CAVEATS = [
    "Wages are illustrative scenarios (a share of market value), not estimates.",
    "Seller reserve and buyer valuation are both the fee-model fallback "
    "(market value x training median ratio); the learned fee model was not accepted.",
    "Because reserve and valuation are equal, the winner among affordable buyers is decided "
    "by the tie-break (smaller club_id), which carries no economic meaning.",
    "One round only: no counter rounds, no player decision, no GBE check, no seller-side SCR.",
    "Contract length and agent fee are assumptions from config/negotiation.toml.",
    "SCR figures inherit the advisor caveats (club-wide wage bill, impairment 0, FY 2024/25 "
    "secondary sources, fixed EUR to GBP rate).",
    "This is not a real-world conclusion.",
]


# ---------- Config and inputs ----------

def validate_negotiation_config(config):
    ratios = config.get("wages", {}).get("ratios")
    if not isinstance(ratios, list) or not ratios or not all(
            isinstance(r, (int, float)) and not isinstance(r, bool) and r > 0 for r in ratios):
        raise ValueError("negotiation config: [wages] ratios must be a non-empty list of "
                         "numbers > 0")
    years = config.get("contract", {}).get("contract_years")
    if isinstance(years, bool) or not isinstance(years, (int, float)) or not years > 0:
        raise ValueError("negotiation config: [contract] contract_years must be > 0")
    agent = config.get("agent_fee", {}).get("agent_fee_gbp_m")
    if isinstance(agent, bool) or not isinstance(agent, (int, float)) or agent < 0:
        raise ValueError("negotiation config: [agent_fee] agent_fee_gbp_m must be >= 0")
    if config.get("acceptance", {}).get("rule") not in RULES:
        raise ValueError(f"negotiation config: [acceptance] rule must be one of {sorted(RULES)}")
    if config.get("buyers", {}).get("n_default") not in (2, 3):
        raise ValueError("negotiation config: [buyers] n_default must be 2 or 3")
    if not isinstance(config.get("sources", {}).get("fee_results_path"), str):
        raise ValueError("negotiation config: [sources] fee_results_path must be a string")


def load_negotiation_config(path=CONFIG_DIR / "negotiation.toml"):
    with open(path, "rb") as f:
        config = tomllib.load(f)
    validate_negotiation_config(config)
    return config


@dataclass(frozen=True)
class FeeFallback:
    ratio_m: float
    source: dict   # path, md5 of the results file, code commit, criteria tag commit


def load_fee_fallback(path):
    """Read m from the fee-model results; fail unless the file says the fallback is in force."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Fee model results not found: {path}")
    raw = path.read_bytes()
    results = json.loads(raw)
    if results.get("consequences", {}).get("seller_reserve") != FALLBACK_LABEL:
        raise ValueError(f"{path} does not say the fallback is in force; this engine only "
                         "implements the fallback reserve")
    m = results.get("train_median_ratio_m")
    if isinstance(m, bool) or not isinstance(m, (int, float)) or not m > 0:
        raise ValueError(f"{path} has no valid train_median_ratio_m")
    provenance = results.get("provenance", {})
    return FeeFallback(float(m), {
        "path": str(path),
        "md5_lf": hashlib.md5(raw.replace(b"\r\n", b"\n")).hexdigest(),
        "code_commit": provenance.get("code_commit"),
        "criteria_tag_commit": provenance.get("criteria_tag_commit"),
    })


def check_player_in_squad(conn, player_id, seller_id):
    """The player must have players.current_club_id = seller and is_current_squad = 1."""
    row = conn.execute("SELECT name, current_club_id, is_current_squad FROM players "
                       "WHERE player_id = ?", (player_id,)).fetchone()
    if row is None:
        raise LookupError(f"No player with player_id={player_id}")
    name, club_id, in_squad = row
    if club_id != seller_id or not in_squad:
        raise ValueError(f"player_id {player_id} is not in the current squad of "
                         f"club_id {seller_id}")
    return name


def player_market_value(conn, player_id, as_of):
    """Latest valuation on or before as_of: (mv_eur, valuation_date). Fails if none, not
    positive, or older than 365 days."""
    as_of = to_date(as_of)
    row = conn.execute(
        "SELECT market_value_in_eur, date FROM valuations WHERE player_id = ? AND date <= ? "
        "ORDER BY date DESC LIMIT 1", (player_id, f"{as_of} 23:59:59")).fetchone()
    if row is None:
        raise ValueError(f"player_id {player_id} has no valuation on or before {as_of}")
    mv, when = float(row[0]), to_date(row[1])
    if as_of - when > timedelta(days=MAX_VALUATION_AGE_DAYS):
        raise ValueError(f"player_id {player_id}: latest valuation ({when}) is more than "
                         f"{MAX_VALUATION_AGE_DAYS} days before {as_of}")
    if not mv > 0:
        raise ValueError(f"player_id {player_id}: market value is not positive")
    return mv, when


# ---------- Pure arithmetic ----------

def wage_for_scenario(ratio, mv_eur, rules_config):
    """Annual wage in GBP m: ratio x market value (EUR), converted at the fixed rate."""
    return eur_to_gbp(ratio * mv_eur / 1_000_000, rules_config)


def limit_headroom(before, rule):
    """Headroom (GBP m) to the limit of the acceptance rule, from an SCRResult."""
    if rule == "green":
        return before.headroom_gbp_m
    if rule == "not_red":
        return before.headroom_gbp_m + (before.red - before.green) * before.adjusted_revenue_gbp_m
    raise ValueError(f"Unknown acceptance rule: {rule!r}")


def max_fee_for_limit(headroom_gbp_m, wage_gbp_m, years_used, agent_fee_gbp_m=0.0,
                      outgoing_gbp_m=0.0):
    """Largest fee (GBP m) that keeps squad cost at the limit.

    squad cost after = base + (fee + agent fee) / years_used + wage - outgoing, and adjusted
    revenue does not change, so the limit is reached at
    fee = years_used * (headroom - wage + outgoing) - agent fee. Negative = infeasible.
    """
    return years_used * (headroom_gbp_m - wage_gbp_m + outgoing_gbp_m) - agent_fee_gbp_m


# ---------- Policy ----------

@dataclass(frozen=True)
class BidContext:
    club_id: int
    reserve_gbp_m: float
    valuation_gbp_m: float
    ceiling_gbp_m: float | None   # None when infeasible even at zero fee


@dataclass(frozen=True)
class Bid:
    club_id: int
    price_gbp_m: float


class BaselinePolicy:
    """Deterministic rules, no LLM. An LLM policy can replace it later for ablation."""

    name = "baseline"

    def buyer_bid(self, ctx):
        """Bid min(valuation, ceiling) when that reaches the reserve; otherwise no bid."""
        if ctx.ceiling_gbp_m is None:
            return None
        price = min(ctx.valuation_gbp_m, ctx.ceiling_gbp_m)
        return Bid(ctx.club_id, price) if price >= ctx.reserve_gbp_m else None

    def seller_choose(self, bids, reserve_gbp_m):
        """Highest bid at or above the reserve; ties go to the smaller club_id.
        Returns (winning bid or None, club_ids tied at the winning price)."""
        valid = [b for b in bids if b.price_gbp_m >= reserve_gbp_m]
        if not valid:
            return None, []
        best = max(b.price_gbp_m for b in valid)
        tied = sorted(b.club_id for b in valid if b.price_gbp_m == best)
        return Bid(tied[0], best), tied


# ---------- Buyers ----------

def _club_position(conn, rules_config, club_id, rule):
    row = load_club_finance(conn, club_id, rules_config["data_vintage"]["finance_fiscal_year"])
    before = evaluate_club(row, rules_config)
    return row, before, limit_headroom(before, rule)


def select_default_buyers(conn, rules_config, seller_id, n, rule):
    """The n eligible clubs with the largest headroom to green (GBP m), smaller club_id first
    on ties. Excludes the seller, Championship-revenue clubs and clubs already above the
    acceptance limit before the deal."""
    candidates = []
    for club_id, _name, _uefa in eligible_clubs(
            conn, rules_config["data_vintage"]["finance_fiscal_year"]):
        if club_id == seller_id:
            continue
        row, before, headroom_limit = _club_position(conn, rules_config, club_id, rule)
        if row["revenue_basis"] == "CHAMPIONSHIP" or headroom_limit < 0:
            continue
        candidates.append((-before.headroom_gbp_m, club_id))
    chosen = [club_id for _, club_id in sorted(candidates)[:n]]
    if len(chosen) < 2:
        raise ValueError("Fewer than 2 eligible default buyers")
    return chosen


def validate_buyer_list(conn, rules_config, buyer_ids, seller_id):
    eligible = {c for c, _, _ in eligible_clubs(
        conn, rules_config["data_vintage"]["finance_fiscal_year"])}
    if len(set(buyer_ids)) != len(buyer_ids):
        raise ValueError("Buyer list has duplicates")
    if not 2 <= len(buyer_ids) <= 3:
        raise ValueError("Give 2 or 3 buyers")
    if seller_id in buyer_ids:
        raise ValueError("The seller cannot be a buyer")
    not_eligible = [b for b in buyer_ids if b not in eligible]
    if not_eligible:
        raise ValueError(f"Not eligible buyers (not in the Premier League next season or "
                         f"unknown): {not_eligible}")


# ---------- Engine ----------

def run_negotiation(conn, rules_config, neg_config, advisor_config, fee_fallback, seller_id,
                    player_id, buyer_ids=None, policy=None, rule=None):
    """One round of sealed bids for every wage scenario. Returns a dict with a step log."""
    policy = policy or BaselinePolicy()
    rule = rule or neg_config["acceptance"]["rule"]
    if rule not in RULES:
        raise ValueError(f"Unknown acceptance rule: {rule!r}")
    log = []

    seller_row = load_club_finance(conn, seller_id,
                                   rules_config["data_vintage"]["finance_fiscal_year"])
    player_name = check_player_in_squad(conn, player_id, seller_id)
    as_of = rules_config["amortisation"]["as_of"]
    mv_eur, valuation_date = player_market_value(conn, player_id, as_of)
    reserve_eur_m = fee_fallback.ratio_m * mv_eur / 1_000_000
    reserve_gbp_m = eur_to_gbp(reserve_eur_m, rules_config)
    valuation_gbp_m = reserve_gbp_m   # design decision (a): valuation = reserve
    log.append(f"Player {player_name} (player_id {player_id}) at {seller_row['club_name']}; "
               f"market value {mv_eur / 1e6:.2f}m EUR on {valuation_date}.")
    log.append(f"Reserve = valuation = {fee_fallback.ratio_m} x MV = {reserve_eur_m:.2f}m EUR "
               f"= {reserve_gbp_m:.2f}m GBP ({FALLBACK_LABEL}).")

    if buyer_ids is None:
        buyer_ids = select_default_buyers(conn, rules_config, seller_id,
                                          neg_config["buyers"]["n_default"], rule)
        buyer_source = "default: largest headroom to green, excluding Championship-revenue " \
                       "clubs and clubs already above the limit"
    else:
        buyer_ids = list(buyer_ids)
        validate_buyer_list(conn, rules_config, buyer_ids, seller_id)
        buyer_source = "user-supplied"
    log.append(f"Buyers ({buyer_source}): {buyer_ids}. Acceptance rule: {rule}.")

    years = neg_config["contract"]["contract_years"]
    years_used = min(years, rules_config["amortisation"]["cap_years"])
    agent_fee = neg_config["agent_fee"]["agent_fee_gbp_m"]
    positions = {b: _club_position(conn, rules_config, b, rule) for b in buyer_ids}

    scenarios = []
    for ratio in neg_config["wages"]["ratios"]:
        wage = wage_for_scenario(ratio, mv_eur, rules_config)
        log.append(f"Scenario wage = {ratio:.0%} of MV = {wage:.2f}m GBP a year "
                   f"({neg_config['wages']['label']}).")
        buyers, bids = [], []
        for b in buyer_ids:
            row, before, headroom_limit = positions[b]
            f_max = max_fee_for_limit(headroom_limit, wage, years_used, agent_fee)
            if f_max < 0:
                ceiling, binding = None, "infeasible even at zero fee"
            else:
                ceiling = min(valuation_gbp_m, f_max)
                binding = "SCR" if f_max < valuation_gbp_m else "valuation"
            bid = policy.buyer_bid(BidContext(b, reserve_gbp_m, valuation_gbp_m, ceiling))
            if bid:
                bids.append(bid)
            buyers.append({"club_id": b, "club_name": row["club_name"],
                           "status_before": before.status,
                           "headroom_to_limit_gbp_m": headroom_limit,
                           "max_fee_gbp_m": f_max, "ceiling_gbp_m": ceiling,
                           "binding": binding,
                           "bid_gbp_m": bid.price_gbp_m if bid else None,
                           "warnings": list(before.warnings)})
            log.append(f"  {row['club_name']}: headroom to {rule} limit {headroom_limit:.2f}, "
                       f"max fee {f_max:.2f}, binding {binding}, "
                       f"bid {'%.2f' % bid.price_gbp_m if bid else 'none'}.")

        winner, tied = policy.seller_choose(bids, reserve_gbp_m)
        affordable = [{"club_id": x["club_id"], "club_name": x["club_name"]}
                      for x in buyers if x["bid_gbp_m"] is not None]
        result = {"wage_ratio": ratio, "wage_gbp_m": wage, "affordable_buyers": affordable,
                  "buyers": buyers, "deal": winner is not None, "winner": None}
        if winner:
            advice = advise_deal(conn, rules_config, winner.club_id, IncomingPlayer(
                fee_amount=winner.price_gbp_m, fee_currency="GBP", annual_wage_gbp_m=wage,
                contract_years=years, agent_fee_gbp_m=agent_fee),
                advisor_config=advisor_config)
            allowed = {"green"} if rule == "green" else {"green", "amber"}
            if advice.after.status not in allowed:
                raise RuntimeError(f"Bug: winner ends at {advice.after.status} under rule {rule}")
            result["winner"] = {
                "club_id": winner.club_id, "club_name": advice.club_name,
                "price_gbp_m": winner.price_gbp_m, "price_eur_m": reserve_eur_m,
                "won_by_tiebreak": len(tied) > 1, "tied_with": [c for c in tied if c != winner.club_id],
                "status_before": advice.before.status, "status_after": advice.after.status,
                "headroom_before_gbp_m": advice.before.headroom_gbp_m,
                "headroom_after_gbp_m": advice.after.headroom_gbp_m,
            }
            log.append(f"  Affordable: {[a['club_name'] for a in affordable]}. Winner "
                       f"{advice.club_name} at {winner.price_gbp_m:.2f}m GBP "
                       f"(won_by_tiebreak={len(tied) > 1}).")
        else:
            log.append("  No buyer can afford the reserve: no deal.")
        scenarios.append(result)

    return {
        "policy": policy.name, "rule": rule,
        "seller": {"club_id": seller_id, "club_name": seller_row["club_name"]},
        "player": {"player_id": player_id, "name": player_name, "market_value_eur_m": mv_eur / 1e6,
                   "valuation_date": str(valuation_date)},
        "fair_value": {"ratio_m": fee_fallback.ratio_m, "label": FALLBACK_LABEL,
                       "source": fee_fallback.source},
        "reserve_eur_m": reserve_eur_m, "reserve_gbp_m": reserve_gbp_m,
        "valuation_gbp_m": valuation_gbp_m,
        "assumptions": {"contract_years": years, "amortisation_years_used": years_used,
                        "agent_fee_gbp_m": agent_fee, "wage_label": neg_config["wages"]["label"]},
        "buyer_source": buyer_source, "buyer_ids": buyer_ids,
        "scenarios": scenarios, "log": log, "caveats": CAVEATS,
    }


def format_text(result):
    """Human-readable report: for each wage scenario the affordable buyers come first."""
    lines = ["NEGOTIATION v1 (baseline policy, one round of sealed bids). Illustrative only.", ""]
    p, fv = result["player"], result["fair_value"]
    lines += [
        f"Seller: {result['seller']['club_name']} (club_id {result['seller']['club_id']}); "
        f"player {p['name']} (player_id {p['player_id']}), market value "
        f"{p['market_value_eur_m']:.2f}m EUR on {p['valuation_date']}",
        f"Fair-value ratio m = {fv['ratio_m']} ({fv['label']}), from {fv['source']['path']} "
        f"(code {str(fv['source']['code_commit'])[:7]}, md5 {fv['source']['md5_lf'][:8]})",
        f"Reserve = buyer valuation = {result['reserve_eur_m']:.2f}m EUR = "
        f"{result['reserve_gbp_m']:.2f}m GBP",
        f"Rule: buyer must stay {'green' if result['rule'] == 'green' else 'at or below red'}. "
        f"Contract {result['assumptions']['contract_years']} years (assumption); agent fee "
        f"{result['assumptions']['agent_fee_gbp_m']} (assumed). Buyers: {result['buyer_source']}.",
    ]
    for s in result["scenarios"]:
        names = ", ".join(a["club_name"] for a in s["affordable_buyers"]) or "none"
        lines += ["", f"=== Wage scenario {s['wage_ratio']:.0%} of market value "
                      f"({s['wage_gbp_m']:.2f}m GBP a year, "
                      f"{result['assumptions']['wage_label']}) ===",
                  f"Buyers that can afford the reserve: {names}",
                  f"  {'buyer':24}{'before':>8}{'headroom':>10}{'max fee':>10}{'bid':>9}  binding"]
        for b in s["buyers"]:
            bid = f"{b['bid_gbp_m']:.2f}" if b["bid_gbp_m"] is not None else "-"
            lines.append(f"  {b['club_name'][:23]:24}{b['status_before']:>8}"
                         f"{b['headroom_to_limit_gbp_m']:>10.2f}{b['max_fee_gbp_m']:>10.2f}"
                         f"{bid:>9}  {b['binding']}")
        w = s["winner"]
        if w:
            tie = f"yes, tied with {w['tied_with']}" if w["won_by_tiebreak"] else "no"
            lines.append(f"Deal: yes. Winner {w['club_name']} at {w['price_gbp_m']:.2f}m GBP "
                         f"(won_by_tiebreak: {tie}); status {w['status_before']} -> "
                         f"{w['status_after']}, headroom {w['headroom_before_gbp_m']:.2f} -> "
                         f"{w['headroom_after_gbp_m']:.2f}")
        else:
            lines.append("Deal: no (no buyer can afford the reserve).")
    lines += ["", "Log:"] + [f"  {entry}" for entry in result["log"]]
    lines += ["", "Caveats:"] + [f"  - {c}" for c in result["caveats"]]
    return "\n".join(lines)
