"""Local Gradio demo of the one-deal advisor and the v1 negotiation engine.

UI only: every number comes from advisor.advise_deal and negotiation.run_negotiation, which
the CLIs also use. The demo never calls a language model and needs no API key: it imports
neither google-adk, google-genai nor llm_agents, the negotiation always uses the rule-based
BaselinePolicy, and every negotiation result shows all three wage scenarios. The
illustrative banner is part of every output. The database is opened read-only and must be
built locally first (python src/phase1_build_db.py, then src/phase2_load_seed.py).

Launch: .venv-demo\\Scripts\\python.exe src\\demo_app.py   (local only, http://127.0.0.1:7860)
"""
import math
import re
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


# First-load examples, used only when present in the data. Advisor: the README example
# (README.md "How to run": advise_cli.py --club-id 1148 --fee 20 --fee-currency GBP
# --wage-gbp-m 3 --contract-years 4). Negotiation: docs/demo_deals.md Deal 1 (Khusanov).
EXAMPLE_ADVISOR = {"club_id": 1148, "fee": 20, "currency": "GBP", "wage_gbp_m": 3,
                   "contract_years": 4}
EXAMPLE_SELLER_ID = 281
EXAMPLE_PLAYER_ID = 763079
FALLBACK_CONTRACT_YEARS = 5   # the form's previous default, kept when the example is absent


def default_form_values(ctx):
    """Choices and first-load values for both tabs (no gradio). The examples are used only
    when they exist in the data; otherwise the Advisor keeps its previous defaults (first
    buyer, empty fee and wage) and the Negotiation shows the first seller's squad with no
    player selected."""
    buyers, sellers = buyer_choices(ctx), seller_choices(ctx)
    if EXAMPLE_ADVISOR["club_id"] in {v for _, v in buyers}:
        advisor = dict(EXAMPLE_ADVISOR)
    else:
        advisor = {"club_id": buyers[0][1] if buyers else None, "fee": None,
                   "currency": DEFAULT_CURRENCY, "wage_gbp_m": None,
                   "contract_years": FALLBACK_CONTRACT_YEARS}
    seller_ids = [v for _, v in sellers]
    seller_id = (EXAMPLE_SELLER_ID if EXAMPLE_SELLER_ID in seller_ids
                 else seller_ids[0] if seller_ids else None)
    players = squad_choices(ctx, seller_id)
    player_id = (EXAMPLE_PLAYER_ID if seller_id == EXAMPLE_SELLER_ID
                 and EXAMPLE_PLAYER_ID in {v for _, v in players} else None)
    return {"advisor": {**advisor, "out_player_id": "", "out_wage_gbp_m": "",
                        "out_amortisation_gbp_m": "", "club_choices": buyers},
            "negotiation": {"seller_id": seller_id, "seller_choices": sellers,
                            "player_id": player_id, "player_choices": players}}


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
    """An optional outgoing field after parsing: empty, or a 0 that the handler treats as empty."""
    return value in (None, 0)


def parse_optional_number(value, label, integer=False):
    """Blank (None, empty or spaces) is None, never 0; "0" is a real 0. Plain non-negative
    decimals only: no thousands separators, exponents or currency symbols."""
    expected = ("a whole number such as 12345" if integer else
                "0 or more, such as 5 or 5.25, with no thousands separators, exponents or "
                "currency symbols")
    error = DemoInputError(f"{label}: enter {expected}, or leave it blank.")
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # At most 18 digits, so a player_id always fits an SQLite integer.
        if not re.fullmatch(r"[0-9]{1,18}" if integer else r"[0-9]*\.?[0-9]+", text):
            raise error
        number = int(text) if integer else float(text)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = value
    else:
        raise error
    if not 0 <= number < math.inf:   # also rejects NaN
        raise error
    if integer:
        if number != int(number):
            raise error
        return int(number)
    return number


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
    out_player_id = parse_optional_number(out_player_id, "Outgoing player_id", integer=True)
    out_wage_gbp_m = parse_optional_number(out_wage_gbp_m, "Outgoing annual wage")
    out_amortisation_gbp_m = parse_optional_number(out_amortisation_gbp_m,
                                                   "Outgoing annual amortisation")
    validate_deal_inputs(club_id, fee, wage_gbp_m, contract_years)
    # All-zero rule: no player with wage 0 and amortisation 0 removes nothing, so it is no player.
    if all(_blank(v) for v in (out_player_id, out_wage_gbp_m, out_amortisation_gbp_m)):
        out_player_id = out_wage_gbp_m = out_amortisation_gbp_m = None   # no outgoing player
    elif _blank(out_player_id):
        out_player_id = None
    with _conn(ctx) as conn:
        outgoing = None
        if out_player_id is not None:
            if out_wage_gbp_m is None:
                raise DemoInputError("The outgoing wage is a user input: enter it.")
            # Also the squad check, so it runs even when the amortisation is typed.
            estimate = lookup_outgoing_amortisation(conn, ctx["rules"], int(out_player_id),
                                                    int(club_id))
            if out_amortisation_gbp_m is not None:
                outgoing = OutgoingPlayer(out_wage_gbp_m, out_amortisation_gbp_m)
            else:
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


# ---------- Look and feel (Gradio 6 takes theme, css and js in launch(), not in Blocks()) ----

SANS_FONTS = ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica",
              "Arial", "sans-serif"]
MONO_FONTS = ["ui-monospace", "Cascadia Code", "Consolas", "monospace"]

# The only colour definitions: light values on :root, dark values under .dark (Gradio puts the
# dark class on <body>). No external fonts, images or imports.
CSS = """
:root {
  color-scheme: light;
  --twai-page: #F6F8FB;
  --twai-card: #FFFFFF;
  --twai-border: #D5DDE8;
  --twai-input-border: #7A8AA0;
  --twai-text: #1E293B;
  --twai-muted: #586579;
  --twai-accent: #2457C5;
  --twai-on-accent: #FFFFFF;
  --twai-callout: #EAF1FD;
  --twai-button: #2457C5;
  --twai-button-hover: #1E4BAE;
  --twai-button-active: #1A4096;
  --twai-on-button: #FFFFFF;
}
/* Gradio puts the dark class on <body>; :has() also switches <html>, so values that Gradio
   resolves on :root (its own body rule in index.html) follow the theme. */
:root.dark, .dark, :root:has(body.dark) {
  color-scheme: dark;
  --twai-page: #11161D;
  --twai-card: #1A212B;
  --twai-border: #2C3644;
  --twai-input-border: #6B7A8F;
  --twai-text: #D3DAE3;
  --twai-muted: #9AA7B8;
  --twai-accent: #78AEF5;
  --twai-on-accent: #0E1622;
  --twai-callout: #16263F;
  --twai-button: #3566C9;
  --twai-button-hover: #3B6FD4;
  --twai-button-active: #2F5DB8;
  --twai-on-button: #FFFFFF;
}

/* Gradio's index.html sets body background/colour from values resolved on :root and switches
   them only with prefers-color-scheme, not with ?__theme; html body beats its body selector. */
html body, .gradio-container { background: var(--twai-page); color: var(--twai-text); }

.gradio-container h1, .gradio-container h2, .gradio-container h3 { text-wrap: balance; }

/* The "Illustrative only" banner and the notes are Markdown blockquotes: show them as callouts */
.twai-header blockquote, .twai-result blockquote {
  background: var(--twai-callout);
  border-left: 4px solid var(--twai-accent);
  border-radius: 6px;
  color: var(--twai-text);
  margin: 0.5rem 0;
  padding: 0.6rem 0.9rem;
}
.twai-note { color: var(--twai-muted); }

/* Advisor report: the text code fence becomes a bordered card */
.twai-report pre {
  background: var(--twai-card);
  border: 1px solid var(--twai-border);
  border-radius: 8px;
  color: var(--twai-text);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  overflow-x: auto;
  padding: 0.9rem 1rem;
}
.twai-report pre code { background: transparent; font-variant-numeric: tabular-nums; }

/* Three scenarios: as many columns as fit at 320px or more, one column on a phone.
   .gradio-container .row.twai-scenarios beats Gradio's div.svelte-* flex rule for Row. */
.gradio-container .row.twai-scenarios {
  display: grid;
  gap: var(--layout-gap);
  grid-template-columns: repeat(auto-fit, minmax(min(320px, 100%), 1fr));
}
.gradio-container .row.twai-scenarios > * { min-width: 0; }

/* Scenario tables: Buyer | Max fee (GBP m) | Binding | Bid; columns 2 and 4 are numbers.
   Gradio's Markdown lets table text break inside words; break only at spaces here, and
   scroll sideways as a last resort. */
.gradio-container .twai-scenario { overflow-x: auto; }
.gradio-container .twai-scenario table {
  border-collapse: collapse;
  font-variant-numeric: tabular-nums;
  overflow-wrap: normal;
  width: 100%;
  word-break: normal;
}
.gradio-container .twai-scenario th, .gradio-container .twai-scenario td {
  border: 1px solid var(--twai-border);
  padding: clamp(0.25rem, 0.8vw, 0.35rem) clamp(0.35rem, 1.2vw, 0.6rem);
}
.gradio-container .twai-scenario th { background: var(--twai-callout); }
.gradio-container .twai-scenario th:nth-child(n+2), .gradio-container .twai-scenario td:nth-child(n+2) {
  white-space: nowrap;
}
.gradio-container .twai-scenario th:nth-child(2), .gradio-container .twai-scenario td:nth-child(2),
.gradio-container .twai-scenario th:nth-child(4), .gradio-container .twai-scenario td:nth-child(4) {
  text-align: right;
}

/* Interaction */
.gradio-container :focus-visible { outline: 2px solid var(--twai-accent); outline-offset: 2px; }
.gradio-container button { touch-action: manipulation; }
.gradio-container button:hover:not(:disabled):not(.primary) { border-color: var(--twai-accent); }
.gradio-container button:active:not(:disabled):not(.primary) { opacity: 0.85; }
.gradio-container button.primary:active:not(:disabled) { background: var(--twai-button-active); }
.gradio-container select { background-color: var(--twai-card); color: var(--twai-text); }
/* Gradio draws the dropdown box on .wrap with the pale border-color-primary; use the input
   border like number inputs. Structure seen in the live DOM: div.container > div.wrap >
   div.wrap-inner. (0,4,0) beats Gradio's .container.svelte-* .wrap (0,3,0). */
.gradio-container .container .wrap:has(> .wrap-inner) { border-color: var(--twai-input-border); }
.gradio-container .container .wrap:has(> .wrap-inner):focus-within {
  border-color: var(--input-border-color-focus);
}
@media (prefers-reduced-motion: reduce) {
  .gradio-container *, .gradio-container *::before, .gradio-container *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
  }
}

.twai-toolbar { justify-content: flex-end; }
"""

# Theme toggle: set __theme to the opposite of the current mode and reload (the same URL
# parameter Gradio's own Settings panel uses). Nothing is stored in the browser.
TOGGLE_JS = """() => {
  const url = new URL(window.location.href);
  url.searchParams.set("__theme", document.body.classList.contains("dark") ? "light" : "dark");
  window.location.href = url.toString();
}"""

# Runs once on page load: report the current mode on the toggle (no-op if it is not found).
PAGE_JS = """() => {
  const el = document.getElementById("twai-theme-toggle");
  const button = el && (el.tagName === "BUTTON" ? el : el.querySelector("button"));
  if (button) button.setAttribute("aria-pressed", String(document.body.classList.contains("dark")));
}"""

# Gradio theme variables mapped onto the tokens; the same expression serves both modes because
# the tokens themselves switch under .dark.
THEME_VARS = {
    "body_background_fill": "var(--twai-page)",
    "background_fill_primary": "var(--twai-card)",
    "background_fill_secondary": "var(--twai-page)",
    "block_background_fill": "var(--twai-card)",
    "block_border_color": "var(--twai-border)",
    "border_color_primary": "var(--twai-border)",
    # Inputs sit on a card, so they take the page colour; Base defaults input_border_width to 0px.
    "input_background_fill": "var(--twai-page)",
    "input_border_color": "var(--twai-input-border)",
    "input_border_color_focus": "var(--twai-accent)",
    "input_border_width": "1px",
    "body_text_color": "var(--twai-text)",
    "body_text_color_subdued": "var(--twai-muted)",
    "color_accent_soft": "var(--twai-callout)",
    "link_text_color": "var(--twai-accent)",
    # Buttons have their own tokens: the dark accent is too bright as a fill.
    "button_primary_background_fill": "var(--twai-button)",
    "button_primary_background_fill_hover": "var(--twai-button-hover)",
    "button_primary_border_color": "var(--twai-button)",
    "button_primary_border_color_hover": "var(--twai-button-hover)",
    "button_primary_text_color": "var(--twai-on-button)",
    "button_primary_text_color_hover": "var(--twai-on-button)",
}


def make_theme():
    """A plain Base theme with system fonts; colours come from the CSS tokens."""
    import gradio as gr
    dark = {f"{name}_dark": value for name, value in THEME_VARS.items()}
    return gr.themes.Base(font=SANS_FONTS, font_mono=MONO_FONTS).set(
        color_accent="var(--twai-accent)", **THEME_VARS, **dark)


def launch_kwargs():
    return {"theme": make_theme(), "css": CSS, "js": PAGE_JS}


# ---------- UI ----------

def guard_errors(fn, ui_error):
    """Wrap a UI callback so user errors become ui_error (gr.Error in the app) with the same
    message. A DemoInputError is an expected user mistake, so its context is suppressed
    (from None); any other ValueError or LookupError keeps the original as __cause__.
    DemoInputError is a ValueError subclass, so its clause must come first."""
    def wrapper(*args):
        try:
            return fn(*args)
        except DemoInputError as error:
            raise ui_error(str(error)) from None
        except (ValueError, LookupError) as error:
            raise ui_error(str(error)) from error
    return wrapper


def build_ui(ctx):
    """The Gradio app (not launched here). gradio is imported only when the UI is built."""
    import gradio as gr

    def guarded(fn):
        return guard_errors(fn, gr.Error)

    defaults = default_form_values(ctx)
    adv, neg = defaults["advisor"], defaults["negotiation"]

    with gr.Blocks(title="Transfer Window AI demo", analytics_enabled=False) as app:
        gr.Markdown(PAGE_HEADER, elem_classes=["twai-header"])
        with gr.Row(elem_classes=["twai-toolbar"]):
            toggle = gr.Button("Switch light / dark theme", size="sm", variant="secondary",
                               scale=0, elem_id="twai-theme-toggle")
        toggle.click(fn=None, js=TOGGLE_JS)
        with gr.Tab("Advisor"):
            gr.Markdown(BUYER_NOTE, elem_classes=["twai-note"])
            club = gr.Dropdown(adv["club_choices"], value=adv["club_id"], label="Buyer club")
            with gr.Row():
                fee = gr.Number(label="Fee (millions)", value=adv["fee"], minimum=0)
                currency = gr.Radio(["GBP", "EUR"], value=adv["currency"], label="Fee currency")
                wage = gr.Number(label="Incoming annual wage (GBP m)", value=adv["wage_gbp_m"],
                                 minimum=0)
                years = gr.Number(label="Contract years", value=adv["contract_years"], minimum=1)
            with gr.Accordion("Optional outgoing player", open=False):
                # Textbox, not Number: Gradio's Number turns an empty value into 0 once mounted.
                out_player = gr.Textbox(label="Outgoing player_id (estimate amortisation; "
                                              "leave blank to skip)",
                                        value=adv["out_player_id"], placeholder="optional")
                out_wage = gr.Textbox(label="Outgoing annual wage (GBP m)",
                                      value=adv["out_wage_gbp_m"], placeholder="optional")
                out_amort = gr.Textbox(label="Outgoing annual amortisation (GBP m, overrides the "
                                             "estimate; leave blank to use the estimate)",
                                       value=adv["out_amortisation_gbp_m"],
                                       placeholder="optional")
            advise = gr.Button("Advise", variant="primary")
            advice_out = gr.Markdown(f"> **{BANNER}**", elem_classes=["twai-result", "twai-report"])
            advise.click(guarded(lambda *a: advisor_view(ctx, *a)),
                         [club, fee, currency, wage, years, out_player, out_wage, out_amort],
                         advice_out)

        with gr.Tab("Negotiation"):
            gr.Markdown(SELLER_NOTE, elem_classes=["twai-note"])
            seller = gr.Dropdown(neg["seller_choices"], value=neg["seller_id"],
                                 label="Seller club")
            player = gr.Dropdown(neg["player_choices"], value=neg["player_id"],
                                 label="Player (current squad)")
            rule = gr.Radio([("green", "green"), ("not red", "not_red")], value="green",
                            label="Acceptance rule for buyers")
            run = gr.Button("Run one round of sealed bids (baseline policy)", variant="primary")
            header = gr.Markdown(f"> **{BANNER}**", elem_classes=["twai-result"])
            with gr.Row(elem_classes=["twai-scenarios"]):
                columns = [gr.Markdown(elem_classes=["twai-result", "twai-scenario"])
                           for _ in range(SCENARIO_COUNT)]
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
    build_ui(ctx).launch(server_name="127.0.0.1", share=False, **launch_kwargs())
    return 0


if __name__ == "__main__":
    sys.exit(main())
