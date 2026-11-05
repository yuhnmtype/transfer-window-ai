"""Tests for src/demo_app.py on the existing synthetic fixtures. No model, no network, no API key.
UI-building tests are skipped when gradio is not installed (e.g. in .venv-adk)."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

import demo_app as demo
import test_advisor as adv_fixture
import test_negotiation as neg_fixture
import test_outgoing_amortisation as out_fixture

SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture
def ctx(tmp_path):
    """Negotiation fixture: buyers 1, 2, 3, 5, 7 (3 above green, 5 Championship revenue),
    relegated 6 and seller 9; player 100 at the seller with a valid valuation."""
    path = tmp_path / "demo db.sqlite"
    neg_fixture.make_db(path).close()
    return demo.load_context(path)


def advise(ctx, **kw):
    args = dict(club_id=1, fee=60.0, currency="GBP", wage_gbp_m=10.0, contract_years=6)
    args.update(kw)
    return demo.advisor_view(ctx, **args)


# ---------- Banner and the three scenarios ----------

def test_banner_text():
    assert demo.BANNER == ("Illustrative only, not a real-world conclusion about any club. Wage "
                           "scenarios are assumptions. Evaluation and limits: "
                           "https://github.com/yuhnmtype/transfer-window-ai/blob/main/docs/results.md")


def test_advisor_output_has_banner_and_reuses_the_advisor(ctx):
    out = advise(ctx)
    assert demo.BANNER in out
    assert "SCR advice: Alpha FC (club_id 1)" in out          # advisor.format_text
    assert "This is not a real-world conclusion." in out


def test_page_header_has_the_banner():
    assert demo.BANNER in demo.PAGE_HEADER


def test_negotiation_banner_once_above_the_three_scenarios(ctx):
    header, columns = demo.negotiation_view(ctx, 9, 100, "green")
    assert len(columns) == 3
    assert header.count(demo.BANNER) == 1                    # the result block
    assert all(demo.BANNER not in c for c in columns)        # not inside the columns
    assert [c.splitlines()[0] for c in columns] == ["### Wage 5% of market value",
                                                   "### Wage 10% of market value",
                                                   "### Wage 15% of market value"]
    for c in columns:                                        # buyers table and winner line
        assert "| Buyer | Max fee (GBP m) | Binding | Bid |" in c
        assert c.splitlines()[-1].startswith(("Winner: ", "No deal."))
    assert "tie-break and has no economic meaning" in header
    # Fixture numbers (tests/test_negotiation.py): reserve 43.0m GBP; at 10% Beta cannot afford it
    assert "Reserve = buyer valuation = 43.00m GBP" in header
    assert "**Buyers that can afford the reserve:** Alpha FC, Delta FC" in columns[1]


def test_negotiation_not_red_rule(ctx):
    _, columns = demo.negotiation_view(ctx, 9, 100, "not_red")
    assert len(columns) == 3


# ---------- Outgoing player: unknown is an error, never 0 ----------

def test_unknown_outgoing_amortisation_is_a_visible_error(tmp_path):
    path = tmp_path / "out.sqlite"
    out_fixture.make_db(path).close()                       # club 10; player 2 has no transfer
    ctx = demo.load_context(path)
    with pytest.raises(demo.DemoInputError, match="unknown is never treated as 0"):
        demo.advisor_view(ctx, 10, 60.0, "GBP", 10.0, 5, out_player_id=2, out_wage_gbp_m=4.0)
    # A known player works: estimated from the transfer history
    ok = demo.advisor_view(ctx, 10, 60.0, "GBP", 10.0, 5, out_player_id=1, out_wage_gbp_m=4.0)
    assert "amortisation estimated from transfer history" in ok


@pytest.mark.parametrize("kw, message", [
    (dict(out_player_id=1), "outgoing wage is a user input"),
    (dict(out_wage_gbp_m=4.0), "enter both the wage and the annual amortisation"),
    (dict(club_id=None), "Choose a buyer club"),
    (dict(fee=None), "Enter the fee"),
    (dict(wage_gbp_m=None), "Enter the wage"),
    (dict(contract_years=None), "Enter the contract years"),
    (dict(fee=-1.0), "fee cannot be negative"),
    (dict(wage_gbp_m=-1.0), "wage must be greater than 0"),
    (dict(contract_years=0.5), "at least 1"),
    (dict(contract_years=0), "at least 1"),
])
def test_input_errors_are_explicit(ctx, kw, message):
    with pytest.raises(demo.DemoInputError, match=message):
        advise(ctx, **kw)


def test_zero_fee_is_a_valid_free_transfer(ctx):
    assert "SCR advice: Alpha FC (club_id 1)" in advise(ctx, fee=0.0)


# ---------- Empty outgoing player = no outgoing player ----------

@pytest.mark.parametrize("empty", [None, 0, "", "  "])
def test_empty_outgoing_section_matches_advise_deal_without_outgoing(ctx, empty):
    out = advise(ctx, fee=20.0, wage_gbp_m=3.0, contract_years=4, out_player_id=empty,
                 out_wage_gbp_m=empty, out_amortisation_gbp_m=empty)
    with demo._conn(ctx) as conn:
        direct = demo.advise_deal(conn, ctx["rules"], 1,
                                  demo.IncomingPlayer(20.0, "GBP", 3.0, 4.0), None,
                                  advisor_config=ctx["advisor"])
    assert out == demo._with_banner(f"```text\n{demo.advice_text(direct)}\n```")
    assert "Outgoing annual cost removed" not in out


# ---------- Outgoing player: blank is unknown, "0" is a real 0 ----------

@pytest.mark.parametrize("value, expected", [
    (None, None), ("", None), ("  ", None), ("0", 0), ("5", 5), (" 5.25 ", 5.25), (".5", 0.5),
    (3, 3), (3.5, 3.5), (0, 0),
])
def test_parse_optional_number_accepts(value, expected):
    assert demo.parse_optional_number(value, "Field") == expected


def test_parse_optional_number_keeps_blank_and_zero_apart():
    assert demo.parse_optional_number("", "Field") is None
    assert demo.parse_optional_number("0", "Field") is not None


@pytest.mark.parametrize("value", [
    "abc", "-1", "1,5", "1,000", "1e3", "nan", "inf", "\u00a35", "5.", "\u0663", True, False,
    -1, -0.5, float("nan"), float("inf"), [1], "9" * 400,
])
def test_parse_optional_number_rejects_and_names_the_field(value):
    with pytest.raises(demo.DemoInputError, match="Some field: enter 0 or more"):
        demo.parse_optional_number(value, "Some field")


@pytest.mark.parametrize("value, expected", [("3", 3), (" 3 ", 3), ("0", 0), (3, 3), (3.0, 3),
                                             ("", None), (None, None)])
def test_parse_optional_integer_accepts(value, expected):
    result = demo.parse_optional_number(value, "Field", integer=True)
    assert result == expected and (result is None or isinstance(result, int))


@pytest.mark.parametrize("value", ["3.5", "3.0", 3.5, "-3", True, "1" * 19])
def test_parse_optional_integer_rejects(value):
    with pytest.raises(demo.DemoInputError, match="Some id: enter a whole number"):
        demo.parse_optional_number(value, "Some id", integer=True)


@pytest.fixture
def squad_ctx(tmp_path):
    """Club 10; player 1 is in its squad with a transfer history, so an estimate exists."""
    path = tmp_path / "out.sqlite"
    out_fixture.make_db(path).close()
    return demo.load_context(path)


def outgoing_line(ctx, **kw):
    out = demo.advisor_view(ctx, 10, 60.0, "GBP", 10.0, 5, out_player_id="1",
                            out_wage_gbp_m="4", **kw)
    return next(l for l in out.splitlines() if l.startswith("Outgoing annual cost removed"))


@pytest.mark.parametrize("blank", ["", "  ", None])
def test_blank_amortisation_uses_the_estimate(squad_ctx, blank):
    with demo._conn(squad_ctx) as conn:
        estimate = demo.lookup_outgoing_amortisation(conn, squad_ctx["rules"], 1, 10)
    assert estimate.annual_gbp_m > 0
    line = outgoing_line(squad_ctx, out_amortisation_gbp_m=blank)
    assert (f"amortisation {estimate.annual_gbp_m:.2f} GBP m "
            "(amortisation estimated from transfer history)") in line


@pytest.mark.parametrize("typed, shown", [("0", "0.00"), ("3.0", "3.00")])
def test_typed_amortisation_is_user_supplied(squad_ctx, typed, shown):
    line = outgoing_line(squad_ctx, out_amortisation_gbp_m=typed)
    assert f"amortisation {shown} GBP m (amortisation user-supplied)" in line


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_player_with_blank_wage_is_an_error(squad_ctx, blank):
    with pytest.raises(demo.DemoInputError, match="outgoing wage is a user input"):
        demo.advisor_view(squad_ctx, 10, 60.0, "GBP", 10.0, 5, out_player_id="1",
                          out_wage_gbp_m=blank, out_amortisation_gbp_m="")


def test_manual_route_needs_both_fields(ctx):
    with pytest.raises(demo.DemoInputError, match="enter both the wage and the annual"):
        advise(ctx, out_player_id="", out_wage_gbp_m="5", out_amortisation_gbp_m="")
    with pytest.raises(demo.DemoInputError, match="enter both the wage and the annual"):
        advise(ctx, out_player_id="", out_wage_gbp_m="", out_amortisation_gbp_m="3")


@pytest.mark.parametrize("typed", ["", "0", "3.0"])
def test_player_outside_the_buyers_squad_is_always_an_error(squad_ctx, typed):
    # Fixture: player 3 plays for club 20, not for buyer 10
    with pytest.raises(ValueError, match="player_id 3 is not in the current squad of club_id 10"):
        demo.advisor_view(squad_ctx, 10, 60.0, "GBP", 10.0, 5, out_player_id="3",
                          out_wage_gbp_m="5", out_amortisation_gbp_m=typed)


def test_unknown_player_id_is_an_error_even_with_a_typed_amortisation(squad_ctx):
    with pytest.raises(LookupError, match="No player with player_id=999999"):
        demo.advisor_view(squad_ctx, 10, 60.0, "GBP", 10.0, 5, out_player_id="999999",
                          out_wage_gbp_m="5", out_amortisation_gbp_m="0")


def test_typed_amortisation_still_works_when_the_estimate_is_unknown(squad_ctx):
    # Player 2 is in the squad but has no transfer, so only a typed amortisation can be used
    out = demo.advisor_view(squad_ctx, 10, 60.0, "GBP", 10.0, 5, out_player_id="2",
                            out_wage_gbp_m="4", out_amortisation_gbp_m="1.5")
    assert "amortisation 1.50 GBP m (amortisation user-supplied)" in out


def test_all_zero_outgoing_section_is_no_outgoing_player(ctx):
    def squad_cost(report):
        return next(l for l in report.splitlines() if l.startswith("Squad cost"))
    zero = advise(ctx, out_player_id="", out_wage_gbp_m="0", out_amortisation_gbp_m="0")
    none = advise(ctx)
    assert "Outgoing annual cost removed" not in zero
    assert squad_cost(zero) == squad_cost(none)


def test_manual_route_accepts_a_deliberate_zero(ctx):
    out = advise(ctx, out_player_id="", out_wage_gbp_m="5", out_amortisation_gbp_m="0")
    assert "wage 5.00 + amortisation 0.00 GBP m (amortisation user-supplied)" in out


@pytest.mark.parametrize("kw, field", [
    (dict(out_player_id="abc"), "Outgoing player_id"),
    (dict(out_wage_gbp_m="1,5"), "Outgoing annual wage"),
    (dict(out_amortisation_gbp_m="-1"), "Outgoing annual amortisation"),
])
def test_advisor_view_names_the_bad_outgoing_field(ctx, kw, field):
    with pytest.raises(demo.DemoInputError, match=f"{field}: enter"):
        advise(ctx, **kw)


def test_defaults_leave_the_outgoing_fields_blank(ctx, monkeypatch):
    for use_examples in (False, True):
        if use_examples:
            use_fixture_examples(monkeypatch)
        adv = demo.default_form_values(ctx)["advisor"]
        assert (adv["out_player_id"], adv["out_wage_gbp_m"],
                adv["out_amortisation_gbp_m"]) == ("", "", "")


def test_default_fee_currency_is_gbp():
    assert demo.DEFAULT_CURRENCY == "GBP"


# ---------- Tottenham Hotspur finance caveat ----------

def test_tottenham_note_as_buyer(tmp_path):
    path = tmp_path / "spurs.sqlite"
    adv_fixture.make_db(path, {"club_id": 148, "club_name": "Tottenham Hotspur"}).close()
    out = demo.advisor_view(demo.load_context(path), 148, 20.0, "GBP", 3.0, 4)
    assert out.count(demo.TOTTENHAM_NOTE) == 1


def test_no_tottenham_note_for_other_clubs(ctx):
    assert demo.TOTTENHAM_NOTE not in advise(ctx)
    header, columns = demo.negotiation_view(ctx, 9, 100, "green")
    assert demo.TOTTENHAM_NOTE not in header + "".join(columns)


@pytest.mark.parametrize("club_id", [9, 1])    # the fixture's seller, then one of its buyers
def test_tottenham_note_in_negotiation_as_seller_or_buyer(ctx, monkeypatch, club_id):
    monkeypatch.setattr(demo, "TOTTENHAM_CLUB_ID", club_id)
    header, columns = demo.negotiation_view(ctx, 9, 100, "green")
    assert header.count(demo.TOTTENHAM_NOTE) == 1
    assert all(demo.TOTTENHAM_NOTE not in c for c in columns)


def test_missing_database_is_an_error(tmp_path):
    with pytest.raises(demo.DemoInputError, match="Database not found"):
        demo.load_context(tmp_path / "nope.db")


# ---------- Choices ----------

def test_choices(ctx):
    assert sorted(v for _, v in demo.buyer_choices(ctx)) == [1, 2, 3, 5, 7]      # no 6, no 9
    assert sorted(v for _, v in demo.seller_choices(ctx)) == [1, 2, 3, 5, 6, 7, 9]
    # Player 100 only: 101 has a stale valuation, 102 none, 104 is not in the squad
    assert [v for _, v in demo.squad_choices(ctx, 9)] == [100]
    assert demo.squad_choices(ctx, None) == []


# ---------- First-load defaults ----------

def use_fixture_examples(monkeypatch, club_id=1, seller_id=9, player_id=100):
    """Point the example constants at fixture ids (the real ids 1148/281/763079 are absent)."""
    monkeypatch.setattr(demo, "EXAMPLE_ADVISOR", {**demo.EXAMPLE_ADVISOR, "club_id": club_id})
    monkeypatch.setattr(demo, "EXAMPLE_SELLER_ID", seller_id)
    monkeypatch.setattr(demo, "EXAMPLE_PLAYER_ID", player_id)


def test_example_constants_match_the_documented_deals():
    # README.md "How to run" advise_cli example; docs/demo_deals.md Deal 1
    assert demo.EXAMPLE_ADVISOR == {"club_id": 1148, "fee": 20, "currency": "GBP",
                                    "wage_gbp_m": 3, "contract_years": 4}
    assert (demo.EXAMPLE_SELLER_ID, demo.EXAMPLE_PLAYER_ID) == (281, 763079)


def test_defaults_use_the_examples_when_present(ctx, monkeypatch):
    use_fixture_examples(monkeypatch)
    d = demo.default_form_values(ctx)
    adv, neg = d["advisor"], d["negotiation"]
    assert {k: adv[k] for k in ("club_id", "fee", "currency", "wage_gbp_m", "contract_years")} \
        == {"club_id": 1, "fee": 20, "currency": "GBP", "wage_gbp_m": 3, "contract_years": 4}
    assert (neg["seller_id"], neg["player_id"]) == (9, 100)
    assert neg["player_choices"] == demo.squad_choices(ctx, 9)


def test_defaults_fall_back_when_the_examples_are_absent(ctx):
    # The fixture has neither club 1148 nor seller 281
    d = demo.default_form_values(ctx)
    adv, neg = d["advisor"], d["negotiation"]
    first_buyer = demo.buyer_choices(ctx)[0][1]
    assert {k: adv[k] for k in ("club_id", "fee", "currency", "wage_gbp_m", "contract_years")} \
        == {"club_id": first_buyer, "fee": None, "currency": "GBP", "wage_gbp_m": None,
            "contract_years": 5}
    first_seller = demo.seller_choices(ctx)[0][1]
    assert neg["seller_id"] == first_seller
    assert neg["player_choices"] == demo.squad_choices(ctx, first_seller)
    assert neg["player_id"] is None


def test_defaults_leave_the_player_unselected_when_the_example_player_is_absent(ctx,
                                                                               monkeypatch):
    use_fixture_examples(monkeypatch, player_id=999)
    neg = demo.default_form_values(ctx)["negotiation"]
    assert (neg["seller_id"], neg["player_id"]) == (9, None)
    assert [v for _, v in neg["player_choices"]] == [100]


def test_advisor_defaults_produce_a_report(ctx, monkeypatch):
    use_fixture_examples(monkeypatch)
    adv = demo.default_form_values(ctx)["advisor"]
    out = demo.advisor_view(ctx, adv["club_id"], adv["fee"], adv["currency"],
                            adv["wage_gbp_m"], adv["contract_years"])
    assert "SCR advice: Alpha FC (club_id 1)" in out


# ---------- No language model, no key ----------

def _modules_after(code):
    out = subprocess.run([sys.executable, "-c", code], cwd=SRC, capture_output=True, text=True,
                         check=True)
    return out.stdout.strip()


def test_importing_the_app_loads_no_llm_modules():
    code = ("import sys, demo_app\n"
            "bad = [m for m in sys.modules if m.startswith(('google.adk', 'google.genai'))"
            " or m == 'llm_agents']\nprint(bad)")
    assert _modules_after(code) == "[]"


def test_source_has_no_key_or_environment_access():
    source = (SRC / "demo_app.py").read_text(encoding="utf-8")
    for forbidden in ("GOOGLE_API_KEY", "os.environ", "getenv", "llm_agents."):
        assert forbidden not in source
    assert not re.search(r"^\s*(import|from)\s+(google|llm_agents)\b", source, re.MULTILINE)
    assert "BaselinePolicy()" in source


# ---------- Error wrapper used by the UI callbacks ----------

class FakeUIError(Exception):
    """Stands in for gr.Error so these tests also run without gradio."""


def _raises(error):
    def fn(*_):
        raise error
    return fn


def test_guard_errors_passes_results_through():
    assert demo.guard_errors(lambda a, b: a + b, FakeUIError)(2, 3) == 5


def test_guard_errors_demo_input_error_has_no_chained_context():
    with pytest.raises(FakeUIError, match="^Enter the fee.$") as exc:
        demo.guard_errors(_raises(demo.DemoInputError("Enter the fee.")), FakeUIError)()
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__ is True


@pytest.mark.parametrize("original", [ValueError("bad value"), LookupError("no such row")])
def test_guard_errors_other_errors_keep_the_cause(original):
    with pytest.raises(FakeUIError, match=f"^{original}$") as exc:
        demo.guard_errors(_raises(original), FakeUIError)()
    assert exc.value.__cause__ is original


def test_guard_errors_does_not_catch_other_exceptions():
    # RuntimeError is neither a ValueError nor a LookupError (KeyError would be: it is a
    # LookupError subclass), so it is not turned into a UI error.
    with pytest.raises(RuntimeError):
        demo.guard_errors(_raises(RuntimeError("x")), FakeUIError)()


def test_guard_errors_with_gr_error():
    gr = pytest.importorskip("gradio")
    with pytest.raises(gr.Error) as exc:
        demo.guard_errors(_raises(demo.DemoInputError("Enter the fee.")), gr.Error)()
    assert exc.value.message == "Enter the fee."
    assert exc.value.__cause__ is None and exc.value.__suppress_context__ is True
    for original in (ValueError("bad value"), LookupError("no such row")):
        with pytest.raises(gr.Error) as exc:
            demo.guard_errors(_raises(original), gr.Error)()
        assert exc.value.message == str(original)
        assert exc.value.__cause__ is original


def test_advise_button_handler_uses_the_wrapper(ctx):
    pytest.importorskip("gradio")
    import gradio as gr
    app = demo.build_ui(ctx)
    handler = next(f.fn for f in app.fns.values() if len(f.inputs) == 8)   # Advise button
    with pytest.raises(gr.Error) as exc:
        handler(1, None, "GBP", 3.0, 4, None, None, None)
    assert exc.value.message == "Enter the fee."
    assert exc.value.__cause__ is None and exc.value.__suppress_context__ is True


# ---------- UI (only when gradio is installed) ----------

def test_build_ui_without_launching(ctx):
    gr = pytest.importorskip("gradio")
    app = demo.build_ui(ctx)
    assert isinstance(app, gr.Blocks)
    components = list(app.blocks.values())
    radio = next(c for c in components if getattr(c, "label", None) == "Fee currency")
    assert radio.value == "GBP"
    assert any(getattr(c, "value", None) == demo.PAGE_HEADER for c in components)


def _component(app, label):
    return next(c for c in app.blocks.values() if getattr(c, "label", None) == label)


def test_build_ui_outgoing_fields_are_blank_textboxes(ctx):
    gr = pytest.importorskip("gradio")
    app = demo.build_ui(ctx)
    for label in ("Outgoing player_id (estimate amortisation; leave blank to skip)",
                  "Outgoing annual wage (GBP m)",
                  "Outgoing annual amortisation (GBP m, overrides the estimate; leave blank to "
                  "use the estimate)"):
        box = _component(app, label)
        assert isinstance(box, gr.Textbox)
        assert box.value == "" and box.placeholder == "optional"


def test_build_ui_other_advisor_inputs_are_unchanged(ctx):
    gr = pytest.importorskip("gradio")
    app = demo.build_ui(ctx)
    assert isinstance(_component(app, "Buyer club"), gr.Dropdown)
    assert isinstance(_component(app, "Fee currency"), gr.Radio)
    for label in ("Fee (millions)", "Incoming annual wage (GBP m)", "Contract years"):
        assert isinstance(_component(app, label), gr.Number)


def test_build_ui_player_list_is_filled_on_first_load(ctx, monkeypatch):
    pytest.importorskip("gradio")
    use_fixture_examples(monkeypatch)
    app = demo.build_ui(ctx)
    seller, player = _component(app, "Seller club"), _component(app, "Player (current squad)")
    assert seller.value == 9
    assert [v for _, v in player.choices] == [100]
    assert player.value == 100


def test_build_ui_fallback_shows_the_first_sellers_squad(ctx):
    pytest.importorskip("gradio")
    app = demo.build_ui(ctx)
    first_seller = demo.seller_choices(ctx)[0][1]
    assert _component(app, "Seller club").value == first_seller
    player = _component(app, "Player (current squad)")
    assert [v for _, v in player.choices] == [v for _, v in demo.squad_choices(ctx, first_seller)]
    assert player.value is None


def test_advise_handler_with_the_defaults_returns_a_report(ctx, monkeypatch):
    pytest.importorskip("gradio")
    use_fixture_examples(monkeypatch)
    app = demo.build_ui(ctx)
    handler = next(f.fn for f in app.fns.values() if len(f.inputs) == 8)   # Advise button
    values = [_component(app, label).value for label in (
        "Buyer club", "Fee (millions)", "Fee currency", "Incoming annual wage (GBP m)",
        "Contract years")]
    assert values == [1, 20, "GBP", 3, 4]
    assert "SCR advice: Alpha FC (club_id 1)" in handler(*values, None, None, None)


def test_importing_the_app_with_gradio_loads_no_llm_modules():
    pytest.importorskip("gradio")
    code = ("import sys, demo_app, gradio\n"
            "bad = [m for m in sys.modules if m.startswith(('google.adk', 'google.genai'))"
            " or m == 'llm_agents']\nprint(bad)")
    assert _modules_after(code) == "[]"


# ---------- Look and feel ----------
# Only the source is checked here. Rendering, colour contrast and the toggle's real behaviour
# (reload with ?__theme, aria-pressed) need a browser.

TOKENS = ["page", "card", "border", "input-border", "text", "muted", "accent", "on-accent",
          "callout", "button", "button-hover", "button-active", "on-button"]
DARK_SELECTOR = ":root.dark, .dark, :root:has(body.dark)"


def _block(css, selector):
    return re.search(r"^" + re.escape(selector) + r" \{(.*?)^\}", css, re.S | re.M).group(1)


@pytest.mark.parametrize("selector", [":root", DARK_SELECTOR])
def test_css_defines_every_token_in_both_modes(selector):
    block = _block(demo.CSS, selector)
    for token in TOKENS:
        assert re.search(rf"--twai-{token}: #[0-9A-F]{{6}};", block), token


def test_css_has_the_interaction_and_number_rules():
    for needed in (":focus-visible", "prefers-reduced-motion", "tabular-nums", "color-scheme",
                   "touch-action: manipulation", "text-wrap: balance"):
        assert needed in demo.CSS, needed


@pytest.mark.parametrize("text", [demo.CSS, demo.TOGGLE_JS, demo.PAGE_JS])
def test_css_and_js_have_nothing_external_or_unsafe(text):
    for banned in ("transition: all", "outline: none", "url(", "@import", "http"):
        assert banned not in text, banned


def test_toggle_js_flips_the_theme_url_parameter():
    assert 'searchParams.set("__theme"' in demo.TOGGLE_JS
    assert 'classList.contains("dark")' in demo.TOGGLE_JS
    assert "localStorage" not in demo.TOGGLE_JS + demo.PAGE_JS


def test_launch_kwargs_hold_theme_css_and_js(monkeypatch):
    monkeypatch.setattr(demo, "make_theme", lambda: "THEME")
    assert demo.launch_kwargs() == {"theme": "THEME", "css": demo.CSS, "js": demo.PAGE_JS}


def test_main_passes_the_look_and_feel_to_launch(monkeypatch):
    calls = []

    class FakeApp:
        def launch(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(demo, "load_context", lambda: "CTX")
    monkeypatch.setattr(demo, "build_ui", lambda ctx: FakeApp())
    monkeypatch.setattr(demo, "make_theme", lambda: "THEME")
    assert demo.main() == 0
    assert calls == [{"server_name": "127.0.0.1", "share": False, "theme": "THEME",
                      "css": demo.CSS, "js": demo.PAGE_JS}]


def test_theme_uses_system_fonts_only():
    pytest.importorskip("gradio")
    css = demo.make_theme()._get_theme_css()
    assert "@font-face" not in css and "googleapis" not in css
    assert "var(--twai-page)" in css


def test_build_ui_has_the_theme_toggle(ctx):
    pytest.importorskip("gradio")
    app = demo.build_ui(ctx)
    toggle = next(c for c in app.blocks.values()
                  if getattr(c, "elem_id", None) == "twai-theme-toggle")
    assert toggle.value == "Switch light / dark theme"
    handler = next(f for f in app.fns.values() if toggle._id in [t[0] for t in f.targets])
    assert handler.fn is None and handler.js == demo.TOGGLE_JS


def test_build_ui_result_components_carry_the_style_classes(ctx):
    pytest.importorskip("gradio")
    app = demo.build_ui(ctx)
    by_value = {}
    classes = []
    for c in app.blocks.values():
        if type(c).__name__ == "Markdown":
            by_value.setdefault(c.value, []).append(c.elem_classes or [])
            classes.append(c.elem_classes or [])
    assert by_value[demo.PAGE_HEADER] == [["twai-header"]]
    assert by_value[demo.BUYER_NOTE] == [["twai-note"]]
    assert by_value[demo.SELLER_NOTE] == [["twai-note"]]
    assert sorted(by_value[f"> **{demo.BANNER}**"]) == [["twai-result"],
                                                        ["twai-result", "twai-report"]]
    assert classes.count(["twai-result", "twai-scenario"]) == demo.SCENARIO_COUNT


def test_theme_vars_give_inputs_a_visible_box():
    dark = {f"{name}_dark": value for name, value in demo.THEME_VARS.items()}
    assert demo.THEME_VARS["input_border_width"] == dark["input_border_width_dark"] == "1px"
    assert demo.THEME_VARS["input_background_fill"] == "var(--twai-page)"
    assert demo.THEME_VARS["input_border_color"] == "var(--twai-input-border)"
    assert demo.THEME_VARS["input_border_color_focus"] == "var(--twai-accent)"


def test_theme_css_has_the_input_border_width_in_both_modes():
    pytest.importorskip("gradio")
    theme = demo.make_theme()
    assert theme.input_border_width == theme.input_border_width_dark == "1px"
    light, dark = theme._get_theme_css().split(".dark", 1)
    assert "--input-border-width: 1px;" in light and "--input-border-width: 1px;" in dark


def test_action_buttons_are_primary_and_the_toggle_is_secondary(ctx):
    pytest.importorskip("gradio")
    app = demo.build_ui(ctx)
    buttons = {c.value: c.variant for c in app.blocks.values() if type(c).__name__ == "Button"}
    assert buttons["Advise"] == "primary"
    assert buttons["Run one round of sealed bids (baseline policy)"] == "primary"
    assert buttons["Switch light / dark theme"] == "secondary"


def _token(selector, name):
    return re.search(rf"--twai-{name}: (#[0-9A-F]{{6}});", _block(demo.CSS, selector)).group(1)


def test_button_tokens_are_separate_from_the_accent():
    assert [_token(DARK_SELECTOR, t) for t in ("button", "button-hover", "button-active",
                                               "on-button")] == ["#3566C9", "#3B6FD4", "#2F5DB8",
                                                                 "#FFFFFF"]
    assert _token(DARK_SELECTOR, "accent") == "#78AEF5"
    assert _token(":root", "button") == _token(":root", "accent") == "#2457C5"
    assert _token(":root", "on-button") == "#FFFFFF"
    assert demo.THEME_VARS["button_primary_background_fill"] == "var(--twai-button)"
    assert demo.THEME_VARS["button_primary_background_fill_hover"] == "var(--twai-button-hover)"
    assert demo.THEME_VARS["button_primary_text_color"] == "var(--twai-on-button)"
    assert demo.THEME_VARS["link_text_color"] == "var(--twai-accent)"
    assert (".gradio-container button.primary:active:not(:disabled) "
            "{ background: var(--twai-button-active); }") in demo.CSS
    assert ":focus-visible { outline: 2px solid var(--twai-accent);" in demo.CSS


def test_body_and_container_colours_come_from_the_tokens():
    assert ("html body, .gradio-container { background: var(--twai-page); "
            "color: var(--twai-text); }") in demo.CSS


def test_scenarios_use_an_auto_fit_grid():
    assert "grid-template-columns: repeat(auto-fit, minmax(min(320px, 100%), 1fr));" in demo.CSS
    assert ".gradio-container .row.twai-scenarios > * { min-width: 0; }" in demo.CSS
    assert "!important" not in demo.CSS.split("prefers-reduced-motion")[0]


def test_scenario_tables_break_only_at_spaces():
    assert "word-break: normal;" in demo.CSS and "overflow-wrap: normal;" in demo.CSS
    assert "white-space: nowrap;" in demo.CSS and "overflow-x: auto;" in demo.CSS
    for banned in ("break-all", "break-word", "overflow-wrap: anywhere"):
        assert banned not in demo.CSS, banned


def test_build_ui_scenario_row_carries_the_grid_class(ctx):
    pytest.importorskip("gradio")
    app = demo.build_ui(ctx)
    rows = [c for c in app.blocks.values() if type(c).__name__ == "Row"
            and "twai-scenarios" in (c.elem_classes or [])]
    assert len(rows) == 1


def test_primary_hover_comes_only_from_the_theme():
    assert ("button:hover:not(:disabled):not(.primary) { border-color: var(--twai-accent); }"
            in demo.CSS)
    assert "button:hover:not(:disabled) {" not in demo.CSS


def test_dropdown_box_uses_the_input_border_and_keeps_the_focus_colour():
    box = ".gradio-container .container .wrap:has(> .wrap-inner)"
    assert f"{box} {{ border-color: var(--twai-input-border); }}" in demo.CSS
    assert re.search(re.escape(box) + r":focus-within \{\s*border-color: "
                     r"var\(--input-border-color-focus\);", demo.CSS)
    assert demo.THEME_VARS["input_border_color_focus"] == "var(--twai-accent)"
