"""Local Gradio demo of the one-deal advisor and the v1 negotiation engine.

UI only: every number comes from advisor.advise_deal and negotiation.run_negotiation, which
the CLIs also use. The demo never calls a language model and needs no API key: it imports
neither google-adk, google-genai nor llm_agents, the negotiation always uses the rule-based
BaselinePolicy, and every negotiation result shows all three wage scenarios. The
illustrative banner is part of every output. The database is opened read-only and must be
built locally first (python src/phase1_build_db.py, then src/phase2_load_seed.py).

Launch: .venv-demo\\Scripts\\python.exe src\\demo_app.py   (local only, http://127.0.0.1:7860)
"""
import sys
from dataclasses import asdict
from pathlib import Path

from advise_cli import DEFAULT_DB, open_read_only
from advisor import (
    IncomingPlayer, OutgoingPlayer, advise_deal, eligible_clubs, load_advisor_config,
)
from advisor import format_text as advice_text
from negotiation import (
    REPO_ROOT, BaselinePolicy, load_fee_fallback, load_negotiation_config, player_market_value,
    run_negotiation,
)
from outgoing_amortisation import lookup_outgoing_amortisation
from scr import load_config

RESULTS_URL = "https://github.com/yuhnmtype/transfer-window-ai/blob/main/docs/results.md"
BANNER = ("Illustrative only, not a real-world conclusion about any club. Wage scenarios are "
          f"assumptions. Evaluation and limits: {RESULTS_URL}")
BUYER_NOTE = ("Buyer clubs: the 17 clubs with FY 2024/25 finance data that are in the Premier "
              "League in 2026/27.")
SELLER_NOTE = ("Seller clubs: all 20 clubs with FY 2024/25 finance data (relegated clubs can "
               "sell but not buy).")
SEASON = "2026/27"   # application default (v1 runs one season), not finance data
SCENARIO_COUNT = 3
DEFAULT_CURRENCY = "GBP"
PAGE_HEADER = f"# Transfer Window AI (local demo)\n\n> **{BANNER}**"
# Tottenham Hotspur's finance row was checked against the filed FY 2024/25 consolidated
# accounts (Companies House 01706358; see seed/SOURCES.md). Its wage bill (256m) is group-wide
# staff costs, including stadium and retail staff.
TOTTENHAM_CLUB_ID = 148
TOTTENHAM_NOTE = ("Tottenham Hotspur: the finance row agrees, within rounding, with the filed "
                  "consolidated accounts for FY 2024/25. The wage bill is group-wide staff costs "
                  "and includes stadium and retail staff, so it overstates squad cost. A very "
                  "large max fee is an SCR headroom ceiling multiplied by the assumed contract "
                  "length, not a prediction and not a cash check.")


class DemoInputError(ValueError):
    """A problem the user must fix; shown in the UI, never silently replaced."""


def load_context(db_path=DEFAULT_DB):
    """Configs and the fee-model fallback, loaded once. The DB is opened per request."""
    if not Path(db_path).exists():
        raise DemoInputError(f"Database not found: {db_path}. Build it locally first with "
                             "python src/phase1_build_db.py and python src/phase2_load_seed.py.")
    rules = load_config(SEASON)
    neg = load_negotiation_config()
    if len(neg["wages"]["ratios"]) != SCENARIO_COUNT:
        raise DemoInputError("The demo shows exactly three wage scenarios; check "
                             "config/negotiation.toml")
    return {"db": Path(db_path), "rules": rules, "advisor": load_advisor_config(), "neg": neg,
            "fallback": load_fee_fallback(REPO_ROOT / neg["sources"]["fee_results_path"])}


def _fiscal_year(ctx):
    return ctx["rules"]["data_vintage"]["finance_fiscal_year"]


# ---------- Choices ----------

def buyer_choices(ctx):
    """(label, club_id) for the advisor: clubs that can be buyers."""
    with _conn(ctx) as conn:
        return [(name, club_id) for club_id, name, _ in eligible_clubs(conn, _fiscal_year(ctx))]


def seller_choices(ctx):
    """(label, club_id) for the negotiation: every club with finance data."""
    with _conn(ctx) as conn:
        rows = conn.execute("SELECT club_id, club_name FROM club_finance WHERE fiscal_year = ? "
                            "ORDER BY club_name", (_fiscal_year(ctx),)).fetchall()
    return [(name, club_id) for club_id, name in rows]


def squad_choices(ctx, seller_id):
    """(label, player_id) for players in the seller's current squad that have a usable
    point-in-time valuation (the same rule the negotiation engine enforces)."""
    if seller_id is None:
        return []
    as_of = ctx["rules"]["amortisation"]["as_of"]
    with _conn(ctx) as conn:
        rows = conn.execute("SELECT player_id, name FROM players WHERE current_club_id = ? AND "
                            "is_current_squad = 1 ORDER BY name", (int(seller_id),)).fetchall()
        choices = []
        for player_id, name in rows:
            try:
                mv, _ = player_market_value(conn, player_id, as_of)
            except ValueError:
                continue
            choices.append((f"{name} ({mv / 1e6:.1f}m EUR)", player_id))
    return choices


class _conn:
    """Read-only connection for one request (SQLite connections stay in one thread)."""

    def __init__(self, ctx):
        self.path = ctx["db"]

    def __enter__(self):
        self.conn = open_read_only(self.path)
        return self.conn

    def __exit__(self, *exc):
        self.conn.close()


# ---------- Views (no gradio needed) ----------

def _with_banner(body, club_ids=()):
    """Banner, then the Tottenham note when that club is part of the result, then the body."""
    note = f"> **Note:** {TOTTENHAM_NOTE}\n\n" if TOTTENHAM_CLUB_ID in club_ids else ""
    return f"> **{BANNER}**\n\n{note}{body}"


def _blank(value):
    """An optional outgoing field the user left empty (the UI may send None or 0)."""
    return value in (None, "", 0)


def validate_deal_inputs(club_id, fee, wage_gbp_m, contract_years):
    """Clear messages for the required advisor inputs; nothing is ever defaulted."""
    if club_id is None:
        raise DemoInputError("Choose a buyer club.")
    for label, value in (("fee", fee), ("wage", wage_gbp_m), ("contract years", contract_years)):
        if value is None or value == "":
            raise DemoInputError(f"Enter the {label}.")
    if fee < 0:
        raise DemoInputError("The fee cannot be negative (0 is allowed for a free transfer).")
    if wage_gbp_m <= 0:
        raise DemoInputError("The incoming wage must be greater than 0 (GBP m a year).")
    if contract_years < 1:
        raise DemoInputError("Contract years must be at least 1.")


def advisor_view(ctx, club_id, fee, currency, wage_gbp_m, contract_years, out_player_id=None,
                 out_wage_gbp_m=None, out_amortisation_gbp_m=None):
    """Markdown report for one deal. Raises DemoInputError for anything the user must fix."""
    validate_deal_inputs(club_id, fee, wage_gbp_m, contract_years)
    if all(_blank(v) for v in (out_player_id, out_wage_gbp_m, out_amortisation_gbp_m)):
        out_player_id = out_wage_gbp_m = out_amortisation_gbp_m = None   # no outgoing player
    elif _blank(out_player_id):
        out_player_id = None
    with _conn(ctx) as conn:
        outgoing = None
        if out_player_id is not None:
            if out_wage_gbp_m is None:
                raise DemoInputError("The outgoing wage is a user input: enter it.")
            if out_amortisation_gbp_m is not None:
                outgoing = OutgoingPlayer(out_wage_gbp_m, out_amortisation_gbp_m)
            else:
                estimate = lookup_outgoing_amortisation(conn, ctx["rules"], int(out_player_id),
                                                        int(club_id))
                if estimate.status == "unknown":
                    raise DemoInputError(
                        f"The amortisation of player {int(out_player_id)} is unknown "
                        f"({estimate.reason}). Enter it manually; unknown is never treated as 0.")
                outgoing = OutgoingPlayer(out_wage_gbp_m, estimate.annual_gbp_m,
                                          amortisation_source="estimated from transfer history",
                                          amortisation_detail=asdict(estimate))
        elif out_wage_gbp_m is not None or out_amortisation_gbp_m is not None:
            if out_wage_gbp_m is None or out_amortisation_gbp_m is None:
                raise DemoInputError("For a manual outgoing player, enter both the wage and "
                                     "the annual amortisation (or choose a player).")
            outgoing = OutgoingPlayer(out_wage_gbp_m, out_amortisation_gbp_m)
        result = advise_deal(conn, ctx["rules"], int(club_id),
                             IncomingPlayer(float(fee), currency, float(wage_gbp_m),
                                            float(contract_years)),
                             outgoing, advisor_config=ctx["advisor"])
    return _with_banner(f"```text\n{advice_text(result)}\n```", club_ids={int(club_id)})


def _scenario_markdown(scenario, result):
    lines = [f"### Wage {scenario['wage_ratio']:.0%} of market value",
             f"{scenario['wage_gbp_m']:.2f}m GBP a year ({result['assumptions']['wage_label']})",
             "",
             "**Buyers that can afford the reserve:** "
             + (", ".join(b["club_name"] for b in scenario["affordable_buyers"]) or "none"),
             "",
             "| Buyer | Max fee (GBP m) | Binding | Bid |", "|---|---|---|---|"]
    for b in scenario["buyers"]:
        bid = f"{b['bid_gbp_m']:.2f}" if b["bid_gbp_m"] is not None else "-"
        lines.append(f"| {b['club_name']} | {b['max_fee_gbp_m']:.2f} | {b['binding']} | {bid} |")
    w = scenario["winner"]
    lines += ["", (f"Winner: {w['club_name']} at {w['price_gbp_m']:.2f}m GBP "
                   f"(won by tie-break: {'yes' if w['won_by_tiebreak'] else 'no'}); status "
                   f"{w['status_before']} -> {w['status_after']}") if w else "No deal."]
    return "\n".join(lines)   # the banner is shown once, above the three columns


def negotiation_view(ctx, seller_id, player_id, rule):
    """(header markdown, [three scenario markdowns]). Baseline policy only."""
    if seller_id is None or player_id is None:
        raise DemoInputError("Choose a seller club and a player.")
    with _conn(ctx) as conn:
        result = run_negotiation(conn, ctx["rules"], ctx["neg"], ctx["advisor"], ctx["fallback"],
                                 int(seller_id), int(player_id), policy=BaselinePolicy(),
                                 rule=rule)
    if result["policy"] != "baseline" or len(result["scenarios"]) != SCENARIO_COUNT:
        raise DemoInputError("The demo only shows the baseline policy with three scenarios.")
    p = result["player"]
    header = (f"**{p['name']}** ({result['seller']['club_name']}), market value "
              f"{p['market_value_eur_m']:.2f}m EUR on {p['valuation_date']}. Reserve = buyer "
              f"valuation = {result['reserve_gbp_m']:.2f}m GBP "
              f"({result['fair_value']['label']}). Rule: {result['rule']}. Buyers: "
              f"{result['buyer_source']}. The winner among affordable buyers is decided by the "
              "tie-break and has no economic meaning.")
    clubs = {result["seller"]["club_id"]} | {b["club_id"] for s in result["scenarios"]
                                             for b in s["buyers"]}
    return (_with_banner(header, club_ids=clubs),
            [_scenario_markdown(s, result) for s in result["scenarios"]])


# ---------- UI ----------

def build_ui(ctx):
    """The Gradio app (not launched here). gradio is imported only when the UI is built."""
    import gradio as gr

    def guarded(fn):
        def wrapper(*args):
            try:
                return fn(*args)
            except (DemoInputError, ValueError, LookupError) as error:
                raise gr.Error(str(error))
        return wrapper

    with gr.Blocks(title="Transfer Window AI demo", analytics_enabled=False) as app:
        gr.Markdown(PAGE_HEADER)
        with gr.Tab("Advisor"):
            gr.Markdown(BUYER_NOTE)
            club = gr.Dropdown(buyer_choices(ctx), label="Buyer club")
            with gr.Row():
                fee = gr.Number(label="Fee (millions)", minimum=0)
                currency = gr.Radio(["GBP", "EUR"], value=DEFAULT_CURRENCY, label="Fee currency")
                wage = gr.Number(label="Incoming annual wage (GBP m)", minimum=0)
                years = gr.Number(label="Contract years", value=5, minimum=1)
            with gr.Accordion("Optional outgoing player", open=False):
                out_player = gr.Number(label="Outgoing player_id (estimate amortisation)",
                                       precision=0)
                out_wage = gr.Number(label="Outgoing annual wage (GBP m)")
                out_amort = gr.Number(label="Outgoing annual amortisation (GBP m, overrides "
                                            "the estimate)")
            advise = gr.Button("Advise")
            advice_out = gr.Markdown(f"> **{BANNER}**")
            advise.click(guarded(lambda *a: advisor_view(ctx, *a)),
                         [club, fee, currency, wage, years, out_player, out_wage, out_amort],
                         advice_out)

        with gr.Tab("Negotiation"):
            gr.Markdown(SELLER_NOTE)
            seller = gr.Dropdown(seller_choices(ctx), label="Seller club")
            player = gr.Dropdown([], label="Player (current squad)")
            rule = gr.Radio([("green", "green"), ("not red", "not_red")], value="green",
                            label="Acceptance rule for buyers")
            run = gr.Button("Run one round of sealed bids (baseline policy)")
            header = gr.Markdown(f"> **{BANNER}**")
            with gr.Row():
                columns = [gr.Markdown() for _ in range(SCENARIO_COUNT)]
            seller.change(guarded(lambda s: gr.Dropdown(choices=squad_choices(ctx, s), value=None)),
                          seller, player)
            run.click(guarded(lambda s, p, r: (lambda h, cols: (h, *cols))(
                *negotiation_view(ctx, s, p, r))), [seller, player, rule], [header, *columns])
    return app


def main():
    try:
        ctx = load_context()
    except DemoInputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    build_ui(ctx).launch(server_name="127.0.0.1", share=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
