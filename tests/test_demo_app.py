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

@pytest.mark.parametrize("empty", [None, 0])
def test_empty_outgoing_section_matches_advise_deal_without_outgoing(ctx, empty):
    out = advise(ctx, fee=20.0, wage_gbp_m=3.0, contract_years=4, out_player_id=empty,
                 out_wage_gbp_m=empty, out_amortisation_gbp_m=empty)
    with demo._conn(ctx) as conn:
        direct = demo.advise_deal(conn, ctx["rules"], 1,
                                  demo.IncomingPlayer(20.0, "GBP", 3.0, 4.0), None,
                                  advisor_config=ctx["advisor"])
    assert out == demo._with_banner(f"```text\n{demo.advice_text(direct)}\n```")
    assert "Outgoing annual cost removed" not in out


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


def test_importing_the_app_with_gradio_loads_no_llm_modules():
    pytest.importorskip("gradio")
    code = ("import sys, demo_app, gradio\n"
            "bad = [m for m in sys.modules if m.startswith(('google.adk', 'google.genai'))"
            " or m == 'llm_agents']\nprint(bad)")
    assert _modules_after(code) == "[]"
