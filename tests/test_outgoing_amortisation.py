"""Tests for src/outgoing_amortisation.py, the --out-player-id CLI path and the diagnostic.
Synthetic data only. Real config: cap 5 years, 0.86 GBP per EUR, as_of 2026-07-01.
Years are days / 365.25. A 50m EUR fee is 50 * 0.86 = 43 GBP m.
"""
import copy
import json
import sqlite3
from datetime import date

import pytest

from advise_cli import main
from outgoing_amortisation import (
    estimate_annual_amortisation, lookup_outgoing_amortisation, pick_purchase, years_between,
)
from phase3_diag_amortisation import diagnose
from scr import load_config, validate_config

AS_OF = date(2026, 7, 1)
X, Y, Z = 10, 20, 30   # X is the selling club


@pytest.fixture
def config():
    return load_config("2026/27")


def move(day, frm, to, fee):
    return {"transfer_date": f"{day} 00:00:00", "from_club_id": frm, "to_club_id": to,
            "transfer_fee": fee}


def estimate(transfers, expiry, config, club=X):
    purchase, ever_left = pick_purchase(transfers, club)
    return estimate_annual_amortisation(purchase, ever_left, expiry, AS_OF, config)


# ---------- Estimates (hand-computed) ----------

def test_full_year_charge(config):
    # Bought 2025-07-01 for 50m EUR, contract to 2029-07-01: 1461 days = 4.0 years.
    # Elapsed at as_of: 365 days, remaining 3.0 years -> full year: 43 / 4 = 10.75
    result = estimate([move("2025-07-01", Y, X, 50e6)], "2029-07-01", config)
    assert result.status == "estimated"
    assert result.fee_gbp_m == pytest.approx(43.0)
    assert result.years_used == pytest.approx(4.0)
    assert result.annual_gbp_m == pytest.approx(10.75)


def test_normal_four_year_contract_is_not_flagged(config):
    # Contract 4.0 years <= cap 5 -> neither flag is set
    result = estimate([move("2025-07-01", Y, X, 50e6)], "2029-07-01", config)
    assert result.possibly_understated is False
    assert result.cap_applied is False


def test_contract_longer_than_cap(config):
    # Contract 2025-07-01 -> 2032-07-01 = 7.0007 years -> cap 5 -> 43 / 5 = 8.6.
    # Still amortising and shortened by the cap: cap_applied, not possibly_understated.
    result = estimate([move("2025-07-01", Y, X, 50e6)], "2032-07-01", config)
    assert result.years_used == 5
    assert result.annual_gbp_m == pytest.approx(8.6)
    assert result.cap_applied is True
    assert result.possibly_understated is False


def test_eight_year_contract_signed_3_years_ago_is_capped_not_understated(config):
    # Signed 2023-07-01, contract to 2031-07-01: 2922 days = 8.0 years -> years_used 5.
    # Elapsed to 2026-07-01: 1096 days = 3.0007 years -> remaining 1.9993 >= 1.
    # annual = fee_gbp / 5 = 43 / 5 = 8.6 (higher than 43 / 8 = 5.375 without the cap)
    result = estimate([move("2023-07-01", Y, X, 50e6)], "2031-07-01", config)
    assert result.status == "estimated"
    assert result.contract_years == pytest.approx(8.0)
    assert result.annual_gbp_m == pytest.approx(8.6)
    assert result.cap_applied is True
    assert result.possibly_understated is False
    assert any("may be lower than this estimate" in w for w in result.warnings)


def test_seven_year_contract_bought_4_5_years_before_uses_cap(config):
    # Bought 2022-01-01, contract to 2029-01-01 (7.0007 years) -> years_used = 5.
    # Elapsed to 2026-07-01: 1642 days = 4.49555 years (about 4.5).
    # remaining = 5 - 4.49555 = 0.50445 (NOT 7.0007 - 4.49555 = 2.5)
    # charge = 43 * 0.50445 / 5 = 4.33826 (about 43 * 0.5 / 5 = 4.3; full year would be 8.6)
    result = estimate([move("2022-01-01", Y, X, 50e6)], "2029-01-01", config)
    assert result.remaining_years == pytest.approx(0.50445, abs=1e-5)
    assert result.annual_gbp_m == pytest.approx(4.33826, abs=1e-5)


def test_less_than_one_year_left_limited_by_book_value(config):
    # Bought 2023-01-01, contract to 2027-01-01: 1461 days = 4.0 years (below the cap).
    # Elapsed: 1277 days = 3.49624 years -> remaining = 0.50376
    # charge = 43 * 0.50376 / 4 = 5.41547, not the full-year 43 / 4 = 10.75
    result = estimate([move("2023-01-01", Y, X, 50e6)], "2027-01-01", config)
    assert result.annual_gbp_m == pytest.approx(5.41547, abs=1e-5)
    assert result.annual_gbp_m < 10.75
    assert "remaining book value" in result.reason


def test_likely_fully_amortised(config):
    # Bought 2018-07-01, contract (after an extension) to 2027-06-30: 9 years -> cap 5.
    # Elapsed 8 years > 5 -> likely fully amortised, charge 0
    result = estimate([move("2018-07-01", Y, X, 50e6)], "2027-06-30", config)
    assert result.status == "likely_fully_amortised"
    assert result.annual_gbp_m == 0.0


def test_bought_7_years_ago_expiring_in_3_years_is_flagged(config):
    # Bought 2019-07-01 (2557 days = 7.0007 years before as_of 2026-07-01), contract to
    # 2029-07-01 (3 years after as_of): length 3653 days = 10.0014 years > cap 5.
    # years_used = 5, remaining = 5 - 7.0007 = -2.0007 <= 0 -> likely_fully_amortised, 0.
    # The contract was probably extended, so possibly_understated is True.
    result = estimate([move("2019-07-01", Y, X, 50e6)], "2029-07-01", config)
    assert result.status == "likely_fully_amortised"
    assert result.annual_gbp_m == 0.0
    assert result.remaining_years == pytest.approx(-2.0007, abs=1e-4)
    assert result.possibly_understated is True
    assert result.cap_applied is False   # no longer being amortised
    assert any("remaining book value is spread over the new term" in w
               for w in result.warnings)


def test_bought_loaned_out_returned_follows_original_purchase(config):
    # Bought 2023-07-01 for 30m EUR (25.8 GBP m), loaned X -> Z (fee 0) in 2024, back Z -> X
    # (fee 0) in 2025. Contract to 2028-07-01: 5.002 years -> cap 5.
    # Remaining 5 - 3.0007 = 1.9993 >= 1 -> full year: 25.8 / 5 = 5.16 (not 0)
    transfers = [move("2023-07-01", Y, X, 30e6), move("2024-08-01", X, Z, 0.0),
                 move("2025-06-30", Z, X, 0.0)]
    result = estimate(transfers, "2028-07-01", config)
    assert result.status == "estimated"
    assert result.purchase_date == "2023-07-01"
    assert result.annual_gbp_m == pytest.approx(5.16)


def test_zero_fee_free_transfer(config):
    result = estimate([move("2025-07-01", Y, X, 0.0)], "2029-07-01", config)
    assert result.status == "zero_fee"
    assert result.annual_gbp_m == 0.0
    assert not any("cannot tell them apart" in w for w in result.warnings)


def test_zero_fee_after_leaving_warns_free_loan_or_academy(config):
    # Joined X at fee 0, loaned out and returned: the return is skipped, the first move is
    # fee 0 and the player has left X before -> the ambiguity warning is required.
    transfers = [move("2020-07-01", Y, X, 0.0), move("2022-08-01", X, Z, 0.0),
                 move("2023-06-30", Z, X, 0.0)]
    result = estimate(transfers, "2029-07-01", config)
    assert result.status == "zero_fee"
    assert any("free transfer, a loan or an academy player" in w for w in result.warnings)


@pytest.mark.parametrize("transfers, expiry, reason", [
    ([], "2029-07-01", "No transfer into the club"),                      # academy / too old
    ([move("2025-07-01", Y, X, None)], "2029-07-01", "not recorded"),     # null fee
    ([move("2025-07-01", Y, X, float("nan"))], "2029-07-01", "not recorded"),
    ([move("2025-07-01", Y, X, 50e6)], None, "expiry date is missing"),
    ([move("2025-07-01", Y, X, 50e6)], "2025-01-01", "not after the purchase"),
])
def test_unknown_is_never_zero(transfers, expiry, reason, config):
    result = estimate(transfers, expiry, config)
    assert result.status == "unknown"
    assert result.annual_gbp_m is None
    assert reason in result.reason


def test_pick_purchase_ignores_other_clubs_and_takes_latest(config):
    transfers = [move("2019-07-01", Y, X, 10e6), move("2021-07-01", X, Y, 20e6),
                 move("2023-07-01", Y, X, 40e6), move("2024-07-01", Y, Z, 5e6)]
    purchase, ever_left = pick_purchase(transfers, X)
    assert purchase["transfer_fee"] == 40e6   # a paid return counts as a new purchase
    assert ever_left is True


def test_years_between():
    assert years_between("2025-07-01 00:00:00", date(2029, 7, 1)) == 4.0   # 1461 / 365.25


def test_config_as_of_must_be_a_date(config):
    bad = copy.deepcopy(config)
    bad["amortisation"]["as_of"] = "1 July 2026"
    with pytest.raises(ValueError):
        validate_config(bad, "2026/27")


# ---------- Database lookup ----------

def make_db(path):
    """club_finance (one buyer, id 10), players and transfers for three squad players."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE club_finance (club_id, club_name, fiscal_year, revenue_gbp_m, "
                 "wage_bill_gbp_m, profit_on_player_sales_gbp_m, player_amortisation_gbp_m, "
                 "uefa_next_season, in_pl_next_season, source_url, confidence, notes, "
                 "revenue_basis, period_months)")
    conn.execute("INSERT INTO club_finance VALUES (10, 'Seller FC', '2024/25', 280, 150, 20, "
                 "50, 'NO', 1, 'https://example.com', 'high', '', 'PL', 12)")
    conn.execute("CREATE TABLE players (player_id, current_club_id, is_current_squad, "
                 "contract_expiration_date)")
    conn.executemany("INSERT INTO players VALUES (?, ?, ?, ?)", [
        (1, X, 1, "2029-07-01 00:00:00"),   # bought 2025 for 50m EUR -> 10.75
        (2, X, 1, "2029-07-01 00:00:00"),   # no transfer record -> unknown
        (3, Y, 1, "2029-07-01 00:00:00"),   # plays for another club
        (4, X, 1, "2029-07-01 00:00:00"),   # bought 2019, 10-year span -> likely fully amortised
        (5, X, 1, "2031-07-01 00:00:00"),   # 8-year contract from 2023 -> capped, 43 / 5 = 8.6
    ])
    conn.execute("CREATE TABLE transfers (player_id, transfer_date, from_club_id, to_club_id, "
                 "transfer_fee)")
    conn.executemany("INSERT INTO transfers VALUES (?, ?, ?, ?, ?)", [
        (1, "2025-07-01 00:00:00", 20, 10, 50000000.0),
        (4, "2019-07-01 00:00:00", 20, 10, 20000000.0),
        (5, "2023-07-01 00:00:00", 20, 10, 50000000.0),
    ])
    conn.commit()
    return conn


def test_lookup(config):
    conn = make_db(":memory:")
    assert lookup_outgoing_amortisation(conn, config, 1, X).annual_gbp_m == pytest.approx(10.75)
    assert lookup_outgoing_amortisation(conn, config, 2, X).status == "unknown"
    with pytest.raises(ValueError):
        lookup_outgoing_amortisation(conn, config, 3, X)   # not in the seller's squad
    with pytest.raises(LookupError):
        lookup_outgoing_amortisation(conn, config, 99, X)


def test_diagnose(config):
    # Squad of X: player 1 (10.75), player 2 (unknown, excluded), player 4 (likely fully
    # amortised, 0, possibly understated), player 5 (capped, 8.6).
    # Sum 10.75 + 8.6 = 19.35; seed 50 -> ratio 0.387
    rows = diagnose(make_db(":memory:"), config)
    assert len(rows) == 1
    row = rows[0]
    assert (row["n_players"], row["estimated"], row["unknown"], row["likely_fully_amortised"],
            row["possibly_understated"], row["cap_applied"]) == (4, 2, 1, 1, 1, 1)
    assert row["estimated_sum_gbp_m"] == pytest.approx(19.35)
    assert row["ratio"] == pytest.approx(19.35 / 50)


# ---------- Command line ----------

DEAL = ["--club-id", "10", "--fee", "60", "--fee-currency", "GBP", "--wage-gbp-m", "10",
        "--contract-years", "6"]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "seller db.sqlite"
    make_db(path).close()
    return path


def test_cli_out_player_lookup(db_path, capsys):
    # Outgoing: wage 4 + estimated amortisation 10.75 removed
    assert main(["--db", str(db_path), *DEAL, "--out-player-id", "1",
                 "--out-wage-gbp-m", "4", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["outgoing"]["annual_amortisation_gbp_m"] == pytest.approx(10.75)
    assert data["outgoing"]["amortisation_source"] == "estimated from transfer history"
    assert "outgoing_amortisation_estimated" in data["warnings"]


def test_cli_override_skips_lookup(db_path, capsys):
    assert main(["--db", str(db_path), *DEAL, "--out-player-id", "2", "--out-wage-gbp-m", "4",
                 "--out-amortisation-gbp-m", "3", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["outgoing"]["amortisation_source"] == "user-supplied"
    assert data["outgoing"]["amortisation_detail"] is None


def test_cli_unknown_amortisation_is_an_error(db_path, capsys):
    assert main(["--db", str(db_path), *DEAL, "--out-player-id", "2",
                 "--out-wage-gbp-m", "4"]) == 1
    assert "--out-amortisation-gbp-m" in capsys.readouterr().err


def test_cli_out_player_needs_wage(db_path):
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(db_path), *DEAL, "--out-player-id", "1"])
    assert exc.value.code == 2


def test_cli_text_shows_estimate_warnings(db_path, capsys):
    assert main(["--db", str(db_path), *DEAL, "--out-player-id", "1",
                 "--out-wage-gbp-m", "4"]) == 0
    out = capsys.readouterr().out
    assert "(amortisation estimated from transfer history)" in out
    assert "contract is extended" in out
    assert "Estimate may be understated" not in out   # player 1: 4-year contract


UNDERSTATED_LINE = ("!!! Estimate may be understated (the contract was probably extended): "
                    "supply --out-amortisation-gbp-m if you know the real figure.")


def test_cli_understated_line_and_json_flag(db_path, capsys):
    # Player 4: long-held, extended case -> the "!!!" line, once
    args = ["--db", str(db_path), *DEAL, "--out-player-id", "4", "--out-wage-gbp-m", "4"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert out.count(UNDERSTATED_LINE) == 1
    assert "Amortisation cap applied" not in out
    assert main([*args, "--json"]) == 0
    detail = json.loads(capsys.readouterr().out)["outgoing"]["amortisation_detail"]
    assert detail["status"] == "likely_fully_amortised"
    assert (detail["possibly_understated"], detail["cap_applied"]) == (True, False)


def test_cli_capped_line_is_not_prominent(db_path, capsys):
    # Player 5: 8-year contract from 2023 -> a normal "Note:" line, no "!!!" line
    args = ["--db", str(db_path), *DEAL, "--out-player-id", "5", "--out-wage-gbp-m", "4"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "  Note: Amortisation cap applied" in out
    assert "!!! Estimate may be understated" not in out
    assert main([*args, "--json"]) == 0
    detail = json.loads(capsys.readouterr().out)["outgoing"]["amortisation_detail"]
    assert (detail["possibly_understated"], detail["cap_applied"]) == (False, True)
