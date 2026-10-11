"""Unit tests for src/scr.py. Finance figures are made up; rule values come from the real config."""
import copy
import sqlite3

import pytest

from scr import (
    Deal, adjusted_revenue, annual_amortisation, baseline_squad_cost, deal_delta,
    eur_to_gbp, evaluate_club, evaluate_with_deal, load_club_finance, load_config,
    scr_ratio, status, threshold_for_club, validate_config,
)


@pytest.fixture
def config():
    return load_config("2026/27")


def make_row(**overrides):
    """A made-up club_finance row: adjusted revenue 220, squad cost 187 (exactly 85%)."""
    row = {
        "club_id": 1, "club_name": "Test FC", "fiscal_year": "2024/25",
        "revenue_gbp_m": 200.0, "wage_bill_gbp_m": 150.0,
        "profit_on_player_sales_gbp_m": 20.0, "player_amortisation_gbp_m": 37.0,
        "uefa_next_season": "NO", "in_pl_next_season": 1,
        "source_url": "https://example.com", "confidence": "high", "notes": "",
        "revenue_basis": "PL", "period_months": 12,
    }
    row.update(overrides)
    return row


# ---------- Config ----------

def test_load_config_real_file(config):
    assert config["season"] == "2026/27"
    assert config["data_vintage"]["finance_fiscal_year"] == "2024/25"
    assert config["scr"]["standard"]["red"] == 1.15
    assert config["scr"]["uefa"]["red"] == 1.00


def test_load_config_unknown_season():
    with pytest.raises(FileNotFoundError):
        load_config("1999/00")


def test_validate_config_season_mismatch(config):
    with pytest.raises(ValueError):
        validate_config(config, "2027/28")


def test_validate_config_green_must_be_below_red(config):
    bad = copy.deepcopy(config)
    bad["scr"]["uefa"]["green"] = 1.00
    with pytest.raises(ValueError):
        validate_config(bad, "2026/27")


def test_validate_config_requires_fiscal_year(config):
    bad = copy.deepcopy(config)
    del bad["data_vintage"]
    with pytest.raises(ValueError):
        validate_config(bad, "2026/27")


def test_validate_config_avg_years_above_one_not_implemented(config):
    bad = copy.deepcopy(config)
    bad["assumptions"]["player_sales_avg_years"] = 3
    with pytest.raises(NotImplementedError):
        validate_config(bad, "2026/27")


# ---------- Adjusted revenue and squad cost ----------

def test_adjusted_revenue_zero_profit():
    assert adjusted_revenue(100.0, 0.0, 0.0) == 100.0


def test_adjusted_revenue_negative_profit():
    assert adjusted_revenue(100.0, -10.0, 0.0) == 90.0


def test_growth_applies_to_revenue_only():
    # 100 * (1 + 0.1) + 20 = 130 (not 120 * 1.1 = 132)
    assert adjusted_revenue(100.0, 20.0, 0.1) == pytest.approx(130.0)


def test_wage_scope_factor_scales_wages_only():
    # 200 * 0.8 + 40 = 160 + 40 = 200
    assert baseline_squad_cost(200.0, 40.0, 0.8) == pytest.approx(200.0)


# ---------- Thresholds, ratio, status ----------

@pytest.mark.parametrize("value", ["CL", "EL", "CONF_LEAGUE_PHASE", "CONF_PLAYOFF"])
def test_threshold_uefa(value, config):
    assert threshold_for_club(value, config) == (0.70, 1.00)


def test_threshold_non_uefa(config):
    assert threshold_for_club("NO", config) == (0.85, 1.15)


def test_threshold_unknown_value(config):
    with pytest.raises(ValueError):
        threshold_for_club("UCL", config)


@pytest.mark.parametrize("adj_rev", [0.0, -5.0])
def test_scr_ratio_needs_positive_revenue(adj_rev):
    with pytest.raises(ValueError):
        scr_ratio(100.0, adj_rev)


@pytest.mark.parametrize("ratio, expected", [
    (0.85, "green"),
    (0.85 + 1e-12, "green"),   # float noise above the threshold is still green
    (0.85 + 1e-6, "amber"),
    (1.15, "amber"),
    (1.15 + 1e-12, "amber"),
    (1.20, "red"),
])
def test_status_standard_bands(ratio, expected):
    assert status(ratio, 0.85, 1.15) == expected


@pytest.mark.parametrize("ratio, expected", [(0.70, "green"), (1.00, "amber"), (1.01, "red")])
def test_status_uefa_bands(ratio, expected):
    assert status(ratio, 0.70, 1.00) == expected


# ---------- Whole club ----------

def test_evaluate_club_exactly_85_percent(config):
    # Hand calculation (T1):
    #   adjusted revenue = 200 * (1 + 0) + 20 = 220
    #   squad cost       = 150 * 1.0 + 37     = 187
    #   ratio            = 187 / 220          = 0.85  -> green (boundary counts as green)
    #   headroom         = 0.85 * 220 - 187   = 0
    result = evaluate_club(make_row(), config)
    assert result.adjusted_revenue_gbp_m == 220.0
    assert result.squad_cost_gbp_m == 187.0
    assert result.ratio == pytest.approx(0.85)
    assert result.headroom_gbp_m == pytest.approx(0.0, abs=1e-9)
    assert result.status == "green"


def test_evaluate_club_exactly_70_percent_uefa_vs_non_uefa(config):
    # Hand calculation (T2):
    #   adjusted revenue = 90 + 10 = 100, squad cost = 50 + 20 = 70, ratio = 0.70
    #   UEFA ("CL"):  headroom = 0.70 * 100 - 70 = 0   -> green
    #   no UEFA:      headroom = 0.85 * 100 - 70 = 15  -> green
    numbers = dict(revenue_gbp_m=90.0, profit_on_player_sales_gbp_m=10.0,
                   wage_bill_gbp_m=50.0, player_amortisation_gbp_m=20.0)
    uefa = evaluate_club(make_row(uefa_next_season="CL", **numbers), config)
    domestic = evaluate_club(make_row(uefa_next_season="NO", **numbers), config)
    assert uefa.status == "green"
    assert uefa.headroom_gbp_m == pytest.approx(0.0, abs=1e-9)
    assert domestic.headroom_gbp_m == pytest.approx(15.0)


def test_evaluate_club_wrong_fiscal_year(config):
    with pytest.raises(ValueError):
        evaluate_club(make_row(fiscal_year="2023/24"), config)


def test_wage_scope_factor_changes_result_and_warns(config):
    # wages 150 * 0.8 = 120, squad cost = 120 + 37 = 157, headroom = 187 - 157 = 30
    scenario = copy.deepcopy(config)
    scenario["assumptions"]["wage_scope_factor"] = 0.8
    result = evaluate_club(make_row(), scenario)
    assert result.headroom_gbp_m == pytest.approx(30.0)
    assert "wage_scope_factor_applied" in result.warnings


# ---------- Warnings ----------

def test_always_on_warnings_only_for_a_normal_row(config):
    warnings = evaluate_club(make_row(), config).warnings
    assert set(warnings) == {"wage_bill_club_wide", "impairment_excluded",
                             "profit_on_sales_not_net"}


@pytest.mark.parametrize("override, code", [
    ({"in_pl_next_season": 0}, "not_in_pl_next_season"),
    ({"revenue_basis": "CHAMPIONSHIP"}, "championship_revenue"),
    ({"period_months": 13}, "non_12_month_period"),
    ({"confidence": "low"}, "low_confidence_seed"),
])
def test_special_case_warnings(override, code, config):
    assert code in evaluate_club(make_row(**override), config).warnings


def test_championship_warning_text(config):
    text = evaluate_club(make_row(revenue_basis="CHAMPIONSHIP"), config).warnings[
        "championship_revenue"]
    assert "not representative of Premier League income" in text
    assert "must not be presented as a real conclusion" in text


# ---------- Amortisation and deals ----------

def test_annual_amortisation_cap():
    assert annual_amortisation(60.0, 6, 5) == 12.0   # capped: 60 / min(6, 5)
    assert annual_amortisation(60.0, 4, 5) == 15.0   # not capped: 60 / 4


@pytest.mark.parametrize("years", [0, -1])
def test_annual_amortisation_needs_positive_years(years):
    with pytest.raises(ValueError):
        annual_amortisation(60.0, years, 5)


def test_eur_to_gbp(config):
    # 10 EUR m * 0.86 = 8.6 GBP m
    assert eur_to_gbp(10.0, config) == pytest.approx(8.6)


def test_deal_delta_cap_and_outgoing(config):
    # Hand calculation (T3):
    #   incoming amortisation = 60 / min(6, 5) = 12
    #   incoming cost         = 12 + wage 5    = 17
    #   outgoing cost         = wage 3 + amort 2 = 5
    #   delta                 = 17 - 5          = 12
    deal = Deal(in_fee_gbp_m=60.0, in_wage_gbp_m=5.0, in_contract_years=6,
                out_wage_gbp_m=3.0, out_amortisation_gbp_m=2.0)
    assert deal_delta(deal, config) == pytest.approx(12.0)


def test_deal_delta_with_agent_fee(config):
    # Hand calculation:
    #   (fee 40 + agent fee 5) / min(3, 5) = 45 / 3 = 15
    #   plus wage 6                        = 21, no outgoing player
    deal = Deal(in_fee_gbp_m=40.0, in_wage_gbp_m=6.0, in_contract_years=3,
                in_agent_fee_gbp_m=5.0)
    assert deal_delta(deal, config) == pytest.approx(21.0)


def test_deal_delta_loan_not_implemented(config):
    deal = Deal(in_fee_gbp_m=2.0, in_wage_gbp_m=3.0, in_contract_years=1, deal_type="loan")
    with pytest.raises(NotImplementedError):
        deal_delta(deal, config)


def test_deal_delta_unknown_type(config):
    deal = Deal(in_fee_gbp_m=2.0, in_wage_gbp_m=3.0, in_contract_years=1, deal_type="swap")
    with pytest.raises(ValueError):
        deal_delta(deal, config)


def test_deal_delta_rejects_negative_money(config):
    deal = Deal(in_fee_gbp_m=-1.0, in_wage_gbp_m=3.0, in_contract_years=2)
    with pytest.raises(ValueError):
        deal_delta(deal, config)


def test_evaluate_with_deal(config):
    # Baseline (T1): cost 187, adjusted revenue 220, headroom 0.
    # Deal delta (T3) = 12, so cost = 199, ratio = 199 / 220 = 0.9045 -> amber,
    # headroom = 0.85 * 220 - 199 = -12, change in headroom = -12 - 0 = -12.
    deal = Deal(in_fee_gbp_m=60.0, in_wage_gbp_m=5.0, in_contract_years=6,
                out_wage_gbp_m=3.0, out_amortisation_gbp_m=2.0)
    after, change = evaluate_with_deal(make_row(), deal, config)
    assert after.squad_cost_gbp_m == pytest.approx(199.0)
    assert after.headroom_gbp_m == pytest.approx(-12.0)
    assert after.status == "amber"
    assert change == pytest.approx(-12.0)


# ---------- Loader ----------

@pytest.fixture
def conn():
    conn = sqlite3.connect(":memory:")
    row = make_row()
    columns = ", ".join(row)
    conn.execute(f"CREATE TABLE club_finance ({columns})")
    conn.execute(f"INSERT INTO club_finance VALUES ({', '.join('?' * len(row))})",
                 list(row.values()))
    yield conn
    conn.close()


def test_load_club_finance(conn):
    row = load_club_finance(conn, 1, "2024/25")
    assert row["club_name"] == "Test FC"
    assert row["revenue_basis"] == "PL"


@pytest.mark.parametrize("club_id, fiscal_year", [(2, "2024/25"), (1, "2023/24")])
def test_load_club_finance_missing_row(conn, club_id, fiscal_year):
    with pytest.raises(LookupError):
        load_club_finance(conn, club_id, fiscal_year)
