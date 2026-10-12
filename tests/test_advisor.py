"""Tests for src/advisor.py and src/advise_cli.py on fake clubs (no real data).

Fake club (club_id 1, no UEFA):
  adjusted revenue = 280 + 20 = 300, squad cost = 150 + 50 = 200
  green limit 0.85 * 300 = 255 -> headroom 55; red limit 1.15 * 300 = 345 -> red headroom 145
Base deal: fee 60 GBP, 6-year contract (capped to 5) -> amortisation 60 / 5 = 12 a year,
  wage 10, no agent fee, no outgoing player -> delta 22, cost after 222, headroom 33.
"""
import json
import math
import sqlite3

import pytest

from advise_cli import main, open_read_only
from advisor import (
    AMBER_NOTE, IncomingPlayer, IneligibleBuyerError, OutgoingPlayer, advise_deal,
    format_text, load_advisor_config, to_dict, validate_advisor_config,
)
from scr import Deal, evaluate_with_deal, load_config

BASE_ROW = {
    "club_id": 1, "club_name": "Test FC", "fiscal_year": "2024/25",
    "revenue_gbp_m": 280.0, "wage_bill_gbp_m": 150.0,
    "profit_on_player_sales_gbp_m": 20.0, "player_amortisation_gbp_m": 50.0,
    "uefa_next_season": "NO", "in_pl_next_season": 1,
    "source_url": "https://example.com", "confidence": "high", "notes": "",
    "revenue_basis": "PL", "period_months": 12,
}


def make_db(path, *overrides):
    """Create a club_finance table with BASE_ROW variants (each override is a dict)."""
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE club_finance ({', '.join(BASE_ROW)})")
    for override in overrides or ({},):
        row = {**BASE_ROW, **override}
        conn.execute(f"INSERT INTO club_finance VALUES ({', '.join('?' * len(row))})",
                     list(row.values()))
    conn.commit()
    return conn


@pytest.fixture
def config():
    return load_config("2026/27")


@pytest.fixture
def advisor_config():
    return load_advisor_config()


def base_incoming(**overrides):
    terms = dict(fee_amount=60.0, fee_currency="GBP", annual_wage_gbp_m=10.0,
                 contract_years=6, agent_fee_gbp_m=None)
    terms.update(overrides)
    return IncomingPlayer(**terms)


def advise(conn, config, advisor_config, incoming=None, outgoing=None, club_id=1):
    return advise_deal(conn, config, club_id, incoming or base_incoming(), outgoing,
                       advisor_config=advisor_config)


# ---------- Core result ----------

def test_before_and_after(config, advisor_config):
    result = advise(make_db(":memory:"), config, advisor_config)
    assert result.before.squad_cost_gbp_m == pytest.approx(200.0)
    assert result.after.squad_cost_gbp_m == pytest.approx(222.0)
    assert result.after.headroom_gbp_m == pytest.approx(33.0)
    assert result.headroom_change_gbp_m == pytest.approx(-22.0)
    assert result.status_change == "green -> green"
    assert result.incoming_cost["annual_amortisation_gbp_m"] == pytest.approx(12.0)
    assert result.incoming_cost["amortisation_years"] == 5


def test_max_wage_feeds_back_to_the_limits(config, advisor_config):
    # Hand calculation:
    #   max green wage = headroom 55 - non-wage delta 12 = 43  (43k*1000/52 = 826.9k a week)
    #   max red wage   = red headroom 145 - 12          = 133
    result = advise(make_db(":memory:"), config, advisor_config)
    assert result.max_wage_green.annual_gbp_m == pytest.approx(43.0)
    assert result.max_wage_green.weekly_gbp_k == pytest.approx(43.0 * 1000 / 52)
    assert result.max_wage_red.annual_gbp_m == pytest.approx(133.0)

    # Feeding the max wages back through scr.py lands exactly on the limits.
    row = dict(BASE_ROW)
    green_after, _ = evaluate_with_deal(
        row, Deal(in_fee_gbp_m=60.0, in_wage_gbp_m=result.max_wage_green.annual_gbp_m,
                  in_contract_years=6), config)
    assert green_after.headroom_gbp_m == pytest.approx(0.0, abs=1e-9)
    assert green_after.status == "green"
    red_after, _ = evaluate_with_deal(
        row, Deal(in_fee_gbp_m=60.0, in_wage_gbp_m=result.max_wage_red.annual_gbp_m,
                  in_contract_years=6), config)
    assert red_after.ratio == pytest.approx(1.15)   # 345 / 300
    assert red_after.status == "amber"


def test_max_wage_none_when_zero_wage_already_exceeds_green(config, advisor_config):
    # wages 250 -> cost 300 > green limit 255: headroom -45, max green = -45 - 12 < 0 -> None
    # red: 345 - 300 = 45, max red = 45 - 12 = 33
    result = advise(make_db(":memory:", {"wage_bill_gbp_m": 250.0}), config, advisor_config)
    assert result.max_wage_green.annual_gbp_m is None
    assert "zero wage exceeds the green limit" in result.max_wage_green.note
    assert result.max_wage_red.annual_gbp_m == pytest.approx(33.0)


def test_sensitivity_table(config, advisor_config):
    # Offsets -20%, 0, +20% on wage 10 -> wages 8, 10, 12
    #   cost   = 200 + 12 + wage  -> 220, 222, 224
    #   headroom = 255 - cost      -> 35, 33, 31
    result = advise(make_db(":memory:"), config, advisor_config)
    rows = result.sensitivity
    assert [r["offset"] for r in rows] == [-0.2, 0.0, 0.2]
    assert [r["annual_wage_gbp_m"] for r in rows] == pytest.approx([8.0, 10.0, 12.0])
    assert [r["squad_cost_gbp_m"] for r in rows] == pytest.approx([220.0, 222.0, 224.0])
    assert [r["headroom_gbp_m"] for r in rows] == pytest.approx([35.0, 33.0, 31.0])
    assert all(r["status"] == "green" for r in rows)


def test_eur_fee_conversion(config, advisor_config):
    # 100 EUR m * 0.86 = 86 GBP m; 5-year contract -> 86 / 5 = 17.2 a year
    incoming = base_incoming(fee_amount=100.0, fee_currency="EUR", contract_years=5)
    result = advise(make_db(":memory:"), config, advisor_config, incoming)
    assert result.fee_gbp_m == pytest.approx(86.0)
    assert result.gbp_per_eur_used == 0.86
    assert result.incoming_cost["annual_amortisation_gbp_m"] == pytest.approx(17.2)
    assert "fee_converted_from_eur" in result.warnings
    assert "amortisation_capped" not in result.warnings


def test_outgoing_player_reduces_cost(config, advisor_config):
    # Outgoing wage 4 + amortisation 3 = 7 removed: delta 22 - 7 = 15, cost 215
    # Max green wage = 55 - (12 - 7) = 50
    result = advise(make_db(":memory:"), config, advisor_config,
                    outgoing=OutgoingPlayer(4.0, 3.0))
    assert result.after.squad_cost_gbp_m == pytest.approx(215.0)
    assert result.max_wage_green.annual_gbp_m == pytest.approx(50.0)
    assert "sale_profit_ignored" in result.warnings


def test_threshold_basis(config, advisor_config):
    domestic = advise(make_db(":memory:"), config, advisor_config)
    assert domestic.threshold_basis == "No UEFA competition next season: green 85%, red 115%"
    uefa = advise(make_db(":memory:", {"uefa_next_season": "CL"}), config, advisor_config)
    assert uefa.threshold_basis == "UEFA competition next season (CL): green 70%, red 100%"


def test_agent_fee_warning_only_when_not_given(config, advisor_config):
    conn = make_db(":memory:")
    assert "agent_fee_assumed_zero" in advise(conn, config, advisor_config).warnings
    given = advise(conn, config, advisor_config, base_incoming(agent_fee_gbp_m=0.0))
    assert "agent_fee_assumed_zero" not in given.warnings


def test_non_pl_buyer_raises(config, advisor_config):
    conn = make_db(":memory:", {"in_pl_next_season": 0})
    with pytest.raises(IneligibleBuyerError):
        advise(conn, config, advisor_config)


def test_unknown_club_raises(config, advisor_config):
    with pytest.raises(LookupError):
        advise(make_db(":memory:"), config, advisor_config, club_id=999)


@pytest.mark.parametrize("incoming, outgoing", [
    (base_incoming(fee_amount=-1.0), None),
    (base_incoming(annual_wage_gbp_m=0.0), None),     # incoming wage must be > 0
    (base_incoming(annual_wage_gbp_m=-5.0), None),
    (base_incoming(annual_wage_gbp_m=math.nan), None),
    (base_incoming(contract_years=0), None),
    (base_incoming(fee_currency="USD"), None),
    (base_incoming(agent_fee_gbp_m=-1.0), None),
    (base_incoming(), OutgoingPlayer(-1.0, 0.0)),
    (base_incoming(), OutgoingPlayer(1.0, -0.5)),
])
def test_invalid_inputs_raise(incoming, outgoing, config, advisor_config):
    with pytest.raises(ValueError):
        advise(make_db(":memory:"), config, advisor_config, incoming, outgoing)


def test_free_transfer_and_fractional_contract_allowed(config, advisor_config):
    result = advise(make_db(":memory:"), config, advisor_config,
                    base_incoming(fee_amount=0.0, contract_years=2.5))
    assert result.incoming_cost["annual_amortisation_gbp_m"] == 0.0


# ---------- Advisor config ----------

def test_advisor_config_real_file(advisor_config):
    assert advisor_config["sensitivity"]["wage_offsets"] == [-0.2, 0.0, 0.2]


@pytest.mark.parametrize("offsets", [[], [-1.0], [0.0, -2.0], ["0.1"], [True]])
def test_advisor_config_rejects_bad_offsets(offsets):
    with pytest.raises(ValueError):
        validate_advisor_config({"sensitivity": {"wage_offsets": offsets}})


# ---------- Output ----------

def test_text_output_amber_note_once_and_caveats(config, advisor_config):
    text = format_text(advise(make_db(":memory:"), config, advisor_config))
    assert text.count(AMBER_NOTE) == 1
    assert "This is not a real-world conclusion." in text
    # Always-on club warnings are shown once, as caveats, not repeated as warnings
    assert "Wage bill is club-wide" not in text


def test_sale_profit_line_once_in_text_but_kept_in_json(config, advisor_config):
    result = advise(make_db(":memory:"), config, advisor_config,
                    outgoing=OutgoingPlayer(4.0, 3.0))
    text = format_text(result)
    assert text.count("Profit on the outgoing player's sale is ignored") == 1   # caveat only
    assert "sale_profit_ignored" in to_dict(result)["warnings"]


def test_championship_warning_comes_first(config, advisor_config):
    conn = make_db(":memory:", {"revenue_basis": "CHAMPIONSHIP"})
    lines = format_text(advise(conn, config, advisor_config)).splitlines()
    assert lines[0].startswith("!!!")
    assert lines[1].startswith("!!! WARNING: Revenue is Championship revenue")


def test_json_keeps_all_warnings(config, advisor_config):
    data = json.loads(json.dumps(to_dict(advise(make_db(":memory:"), config,
                                                advisor_config)), allow_nan=False))
    assert "wage_bill_club_wide" in data["warnings"]
    assert data["max_wage_green"]["annual_gbp_m"] == pytest.approx(43.0)


# ---------- Command line (file DB in a folder with a space in its name) ----------

DEAL_ARGS = ["--club-id", "1", "--fee", "60", "--fee-currency", "GBP",
             "--wage-gbp-m", "10", "--contract-years", "6"]


@pytest.fixture
def db_path(tmp_path):
    folder = tmp_path / "my data"
    folder.mkdir()
    path = folder / "fake club.db"
    make_db(path, {}, {"club_id": 2, "club_name": "Relegated FC", "in_pl_next_season": 0},
            {"club_id": 3, "club_name": "Promoted FC", "revenue_basis": "CHAMPIONSHIP"}).close()
    return path


def test_cli_text(db_path, capsys):
    assert main(["--db", str(db_path), *DEAL_ARGS]) == 0
    out = capsys.readouterr().out
    assert "SCR advice: Test FC (club_id 1)" in out
    assert out.count(AMBER_NOTE) == 1


def test_cli_json(db_path, capsys):
    assert main(["--db", str(db_path), *DEAL_ARGS, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["after"]["squad_cost_gbp_m"] == pytest.approx(222.0)


def test_cli_list_clubs_excludes_non_pl(db_path, capsys):
    assert main(["--db", str(db_path), "--list-clubs"]) == 0
    out = capsys.readouterr().out
    assert "Test FC" in out and "Promoted FC" in out
    assert "Relegated FC" not in out


def test_cli_non_pl_buyer_is_an_error(db_path, capsys):
    args = ["--db", str(db_path), *DEAL_ARGS]
    args[args.index("--club-id") + 1] = "2"
    assert main(args) == 1
    assert "not in the Premier League next season" in capsys.readouterr().err


def test_cli_missing_argument_exits(db_path):
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(db_path), "--club-id", "1"])
    assert exc.value.code == 2


def test_cli_outgoing_needs_both_values(db_path):
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(db_path), *DEAL_ARGS, "--out-wage-gbp-m", "4"])
    assert exc.value.code == 2


def test_cli_missing_database(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "nope.db"), *DEAL_ARGS]) == 1
    assert "database not found" in capsys.readouterr().err


def test_read_only_connection_cannot_write(db_path):
    conn = open_read_only(db_path)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE should_fail (x)")
    conn.close()
