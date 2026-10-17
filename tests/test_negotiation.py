"""Tests for src/negotiation.py and src/negotiate_cli.py on synthetic data (hand-computed).

Fake clubs (FY 2024/25, no UEFA, so green 85% and red 115%):
  1 Alpha  adj 300, cost 200 -> headroom to green 55, to red 145
  2 Beta   adj 100, cost  73 -> green 12, red 42
  3 Gamma  adj 100, cost  90 -> green -5 (already above green), red 25
  5 Promoted FC  Championship revenue (excluded from the default list)
  6 Relegated FC not in the PL next season (never a buyer)
  7 Delta  adj 200, cost 140 -> green 30
  9 Seller FC    relegated, a valid seller
Player 100 at the seller, market value 40m EUR on 2026-06-01 (as_of 2026-07-01).
With m = 1.25: reserve = valuation = 50m EUR = 50 * 0.86 = 43.0m GBP.
Wages: 5% / 10% / 15% of 40m EUR = 2 / 4 / 6m EUR = 1.72 / 3.44 / 5.16m GBP.
Contract 5 years, cap 5: max fee = 5 * (headroom - wage).
"""
import json
import sqlite3
from pathlib import Path

import pytest

from advisor import IncomingPlayer, advise_deal, load_advisor_config
from negotiate_cli import main
from negotiation import (
    FALLBACK_LABEL, BaselinePolicy, Bid, limit_headroom, load_fee_fallback,
    load_negotiation_config, max_fee_for_limit, player_market_value, run_negotiation,
    select_default_buyers, validate_buyer_list, validate_negotiation_config, wage_for_scenario,
)
from scr import evaluate_club, load_club_finance, load_config

COLUMNS = ("club_id, club_name, fiscal_year, revenue_gbp_m, wage_bill_gbp_m, "
           "profit_on_player_sales_gbp_m, player_amortisation_gbp_m, uefa_next_season, "
           "in_pl_next_season, source_url, confidence, notes, revenue_basis, period_months")
CLUBS = [  # club_id, name, revenue, wages, profit, amortisation, in_pl, revenue_basis
    (1, "Alpha FC", 280, 150, 20, 50, 1, "PL"),
    (2, "Beta FC", 90, 50, 10, 23, 1, "PL"),
    (3, "Gamma FC", 90, 60, 10, 30, 1, "PL"),
    (5, "Promoted FC", 900, 100, 0, 0, 1, "CHAMPIONSHIP"),
    (6, "Relegated FC", 300, 100, 0, 0, 0, "PL"),
    (7, "Delta FC", 180, 100, 20, 40, 1, "PL"),
    (9, "Seller FC", 200, 150, 10, 40, 0, "PL"),
]


def make_db(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE club_finance ({COLUMNS})")
    for cid, name, rev, wages, profit, amort, in_pl, basis in CLUBS:
        conn.execute(f"INSERT INTO club_finance VALUES ({', '.join('?' * 14)})",
                     (cid, name, "2024/25", rev, wages, profit, amort, "NO", in_pl,
                      "https://example.com", "high", "", basis, 12))
    conn.execute("CREATE TABLE players (player_id, name, current_club_id, is_current_squad)")
    conn.executemany("INSERT INTO players VALUES (?, ?, ?, ?)", [
        (100, "Player A", 9, 1), (101, "Player B", 9, 1), (102, "Player C", 9, 1),
        (103, "Player D", 1, 1), (104, "Player E", 9, 0)])
    conn.execute("CREATE TABLE valuations (player_id, date, market_value_in_eur)")
    conn.executemany("INSERT INTO valuations VALUES (?, ?, ?)", [
        (100, "2025-12-01 00:00:00", 30_000_000),
        (100, "2026-06-01 00:00:00", 40_000_000),
        (100, "2026-07-05 00:00:00", 99_000_000),   # after as_of: must be ignored
        (101, "2025-01-01 00:00:00", 20_000_000),   # 546 days before as_of: too old
    ])
    conn.commit()
    return conn


@pytest.fixture
def rules():
    return load_config("2026/27")


@pytest.fixture
def neg():
    return load_negotiation_config()


def fee_results(tmp_path, m=1.25, label=FALLBACK_LABEL, name="fee.json"):
    path = tmp_path / name
    path.write_text(json.dumps({
        "consequences": {"seller_reserve": label}, "train_median_ratio_m": m,
        "provenance": {"code_commit": "abc1234", "criteria_tag_commit": "def5678"}}),
        encoding="utf-8")
    return path


def run(conn, rules, neg, fallback, **kwargs):
    return run_negotiation(conn, rules, neg, load_advisor_config(), fallback, 9, 100, **kwargs)


# ---------- Arithmetic ----------

def test_max_fee_hand_values():
    # Alpha: headroom 55 (green) / 145 (red), wage 10, 5 years -> 5 * 45 = 225, 5 * 135 = 675
    assert max_fee_for_limit(55, 10, 5) == 225
    assert max_fee_for_limit(145, 10, 5) == 675
    # Agent fee 5 and an outgoing player saving 3: 5 * (55 - 10 + 3) - 5 = 235
    assert max_fee_for_limit(55, 10, 5, agent_fee_gbp_m=5, outgoing_gbp_m=3) == 235
    # Gamma is already above green: 5 * (-5 - 1.72) < 0 -> infeasible
    assert max_fee_for_limit(-5, 1.72, 5) < 0


def test_max_fee_feeds_back_into_advise_deal(rules):
    conn, advisor = make_db(), load_advisor_config()

    def after(fee):
        return advise_deal(conn, rules, 1, IncomingPlayer(fee, "GBP", 10.0, 5, 0.0),
                           advisor_config=advisor).after

    green = after(225.0)                  # amortisation 45 + wage 10 = 55 = headroom
    assert green.headroom_gbp_m == pytest.approx(0.0, abs=1e-9)
    assert green.status == "green"
    assert after(225.01).status == "amber"
    red = after(675.0)                    # cost 200 + 135 + 10 = 345 = 1.15 * 300
    assert red.ratio == pytest.approx(1.15)
    assert red.status == "amber"
    assert after(675.01).status == "red"


def test_limit_headroom(rules):
    conn = make_db()
    before = evaluate_club(load_club_finance(conn, 1, "2024/25"), rules)
    assert limit_headroom(before, "green") == pytest.approx(55.0)
    assert limit_headroom(before, "not_red") == pytest.approx(145.0)   # 55 + 0.30 * 300


def test_wage_for_scenario(rules):
    # 10% of 40m EUR = 4m EUR = 4 * 0.86 = 3.44m GBP
    assert wage_for_scenario(0.10, 40_000_000, rules) == pytest.approx(3.44)


# ---------- Inputs ----------

def test_fee_fallback_is_read_from_the_file(tmp_path):
    fallback = load_fee_fallback(fee_results(tmp_path, m=1.4))
    assert fallback.ratio_m == 1.4
    assert fallback.source["code_commit"] == "abc1234"


def test_real_results_file_says_fallback():
    path = Path(__file__).resolve().parent.parent / "results" / "fee_model_eval.json"
    real = load_fee_fallback(path)
    assert real.ratio_m == json.loads(path.read_text(encoding="utf-8"))["train_median_ratio_m"]


@pytest.mark.parametrize("kwargs", [dict(label="fee model"), dict(m=0), dict(m="1.25")])
def test_fee_fallback_refuses_bad_files(tmp_path, kwargs):
    with pytest.raises(ValueError):
        load_fee_fallback(fee_results(tmp_path, **kwargs))


def test_fee_fallback_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_fee_fallback(tmp_path / "nope.json")


def test_market_value_point_in_time_and_guards():
    conn = make_db()
    mv, when = player_market_value(conn, 100, "2026-07-01")
    assert (mv, str(when)) == (40_000_000, "2026-06-01")   # the July valuation is ignored
    with pytest.raises(ValueError, match="more than 365 days"):
        player_market_value(conn, 101, "2026-07-01")
    with pytest.raises(ValueError, match="no valuation"):
        player_market_value(conn, 102, "2026-07-01")


def test_reserve_follows_m_from_the_file(tmp_path, rules, neg):
    # m = 1.4: reserve = 1.4 * 40 = 56m EUR = 48.16m GBP
    result = run(make_db(), rules, neg, load_fee_fallback(fee_results(tmp_path, m=1.4)))
    assert result["reserve_eur_m"] == pytest.approx(56.0)
    assert result["reserve_gbp_m"] == pytest.approx(48.16)


@pytest.mark.parametrize("player_id, error", [(103, ValueError), (104, ValueError),
                                              (999, LookupError)])
def test_player_must_be_in_the_seller_squad(tmp_path, rules, neg, player_id, error):
    with pytest.raises(error):
        run_negotiation(make_db(), rules, neg, load_advisor_config(),
                        load_fee_fallback(fee_results(tmp_path)), 9, player_id)


# ---------- Buyers ----------

def test_default_buyers(rules):
    # Eligible: 1, 2, 3, 5, 7. Excluded: 5 (Championship revenue), 3 (above green already).
    # By headroom to green: Alpha 55, Delta 30, Beta 12
    assert select_default_buyers(make_db(), rules, 9, 3, "green") == [1, 7, 2]
    assert select_default_buyers(make_db(), rules, 9, 2, "green") == [1, 7]


@pytest.mark.parametrize("buyers", [[9, 1], [6, 1], [1, 1], [1], [1, 2, 3, 7], [1, 999]])
def test_invalid_buyer_lists(rules, buyers):
    with pytest.raises(ValueError):
        validate_buyer_list(make_db(), rules, buyers, 9)


# ---------- Policy ----------

def test_seller_choose_tie_and_none():
    policy = BaselinePolicy()
    winner, tied = policy.seller_choose([Bid(7, 43.0), Bid(2, 43.0), Bid(1, 43.0)], 43.0)
    assert (winner.club_id, tied) == (1, [1, 2, 7])
    assert policy.seller_choose([Bid(2, 42.9)], 43.0) == (None, [])


# ---------- Engine ----------

def test_default_run_reports_all_three_scenarios(tmp_path, rules, neg):
    result = run(make_db(), rules, neg, load_fee_fallback(fee_results(tmp_path)))
    assert result["buyer_ids"] == [1, 7, 2]
    assert result["reserve_gbp_m"] == pytest.approx(43.0)
    s5, s10, s15 = result["scenarios"]
    assert [s["wage_ratio"] for s in result["scenarios"]] == [0.05, 0.10, 0.15]

    # 5%: max fees Alpha 5*(55-1.72)=266.4, Delta 141.4, Beta 5*(12-1.72)=51.4 -> all afford 43
    assert [a["club_id"] for a in s5["affordable_buyers"]] == [1, 7, 2]
    assert s5["winner"]["club_id"] == 1
    assert s5["winner"]["won_by_tiebreak"] is True
    assert s5["winner"]["tied_with"] == [2, 7]
    # Alpha after: cost 200 + 43/5 + 1.72 = 210.32, headroom 55 - 10.32 = 44.68
    assert s5["winner"]["headroom_after_gbp_m"] == pytest.approx(44.68)
    assert s5["winner"]["status_after"] == "green"

    # 10%: Beta's max fee 5 * (12 - 3.44) = 42.8 < 43 -> SCR binds, no bid
    beta = next(b for b in s10["buyers"] if b["club_id"] == 2)
    assert beta["max_fee_gbp_m"] == pytest.approx(42.8)
    assert (beta["binding"], beta["bid_gbp_m"]) == ("SCR", None)
    assert [a["club_id"] for a in s10["affordable_buyers"]] == [1, 7]
    assert s10["winner"]["tied_with"] == [7]

    # 15%: Beta 5 * (12 - 5.16) = 34.2 < 43; Alpha and Delta still afford it
    assert [a["club_id"] for a in s15["affordable_buyers"]] == [1, 7]
    alpha = next(b for b in s15["buyers"] if b["club_id"] == 1)
    assert alpha["binding"] == "valuation"


def test_user_list_with_infeasible_buyer_green(tmp_path, rules, neg):
    result = run(make_db(), rules, neg, load_fee_fallback(fee_results(tmp_path)),
                 buyer_ids=[2, 3])
    s5, s10, _ = result["scenarios"]
    gamma = next(b for b in s5["buyers"] if b["club_id"] == 3)
    assert gamma["binding"] == "infeasible even at zero fee"
    # 5%: only Beta (51.4 >= 43), so no tie-break
    assert [a["club_id"] for a in s5["affordable_buyers"]] == [2]
    assert s5["winner"]["won_by_tiebreak"] is False
    # 10%: Beta 42.8 < 43 and Gamma infeasible -> no deal
    assert s10["deal"] is False and s10["winner"] is None and s10["affordable_buyers"] == []


def test_not_red_rule(tmp_path, rules, neg):
    # Red headroom: Beta 12 + 30 = 42, Gamma -5 + 30 = 25.
    # 5%: Beta 5 * (42 - 1.72) = 201.4, Gamma 5 * (25 - 1.72) = 116.4 -> both afford 43
    result = run(make_db(), rules, neg, load_fee_fallback(fee_results(tmp_path)),
                 buyer_ids=[2, 3], rule="not_red")
    s5 = result["scenarios"][0]
    assert [a["club_id"] for a in s5["affordable_buyers"]] == [2, 3]
    assert (s5["winner"]["club_id"], s5["winner"]["tied_with"]) == (2, [3])
    # Beta after: cost 73 + 8.6 + 1.72 = 83.32 <= 85 -> still green
    assert s5["winner"]["status_after"] == "green"


@pytest.mark.parametrize("section, key, value", [
    ("wages", "ratios", []), ("wages", "ratios", [0.1, 0]), ("contract", "contract_years", 0),
    ("agent_fee", "agent_fee_gbp_m", -1), ("acceptance", "rule", "amber"),
    ("buyers", "n_default", 4),
])
def test_negotiation_config_validation(neg, section, key, value):
    bad = json.loads(json.dumps(neg))
    bad[section][key] = value
    with pytest.raises(ValueError):
        validate_negotiation_config(bad)


def test_real_negotiation_config(neg):
    assert neg["wages"]["ratios"] == [0.05, 0.10, 0.15]
    assert neg["wages"]["label"] == "illustrative, not estimated"
    assert neg["acceptance"]["rule"] == "green"


# ---------- Command line (folder with a space in its name) ----------

@pytest.fixture
def cli_paths(tmp_path):
    folder = tmp_path / "neg data"
    folder.mkdir()
    db = folder / "fake db.sqlite"
    make_db(db).close()
    return db, fee_results(folder)


def test_cli_text_prints_all_scenarios(cli_paths, capsys):
    db, fee = cli_paths
    assert main(["--db", str(db), "--fee-results", str(fee), "--seller-club-id", "9",
                 "--player-id", "100"]) == 0
    out = capsys.readouterr().out
    assert out.count("=== Wage scenario") == 3
    assert out.count("Buyers that can afford the reserve:") == 3
    assert "won_by_tiebreak: yes, tied with [2, 7]" in out
    assert "This is not a real-world conclusion." in out


def test_cli_json(cli_paths, capsys):
    db, fee = cli_paths
    assert main(["--db", str(db), "--fee-results", str(fee), "--seller-club-id", "9",
                 "--player-id", "100", "--buyers", "2,3", "--rule", "not_red", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["rule"] == "not_red" and len(data["scenarios"]) == 3


def test_cli_refuses_when_fallback_not_in_force(cli_paths, tmp_path, capsys):
    db, _ = cli_paths
    bad = fee_results(tmp_path, label="fee model", name="bad.json")
    assert main(["--db", str(db), "--fee-results", str(bad), "--seller-club-id", "9",
                 "--player-id", "100"]) == 1
    assert "fallback is in force" in capsys.readouterr().err
