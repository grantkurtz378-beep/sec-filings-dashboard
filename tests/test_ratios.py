"""Tests for src/ratios.py."""
from __future__ import annotations

import math

from src.pipeline import build_annual_dataframe
from src.ratios import compute_ratios


def _ratios_for(facts_json):
    return compute_ratios(build_annual_dataframe(facts_json))


def test_basic_margins_and_returns(full_company_json):
    df = _ratios_for(full_company_json).set_index("fy")

    # 2023: revenue 220B, COGS 125B -> gross profit 95B -> gross margin 95/220
    assert math.isclose(df.loc[2023, "gross_margin"], 95 / 220, rel_tol=1e-9)
    # net margin = net income / revenue = 50/220
    assert math.isclose(df.loc[2023, "net_margin"], 50 / 220, rel_tol=1e-9)
    # ROE = net income / equity = 50/140
    assert math.isclose(df.loc[2023, "roe"], 50 / 140, rel_tol=1e-9)
    # ROA = net income / assets = 50/330
    assert math.isclose(df.loc[2023, "roa"], 50 / 330, rel_tol=1e-9)
    # current ratio = current assets / current liabilities = 110/95
    assert math.isclose(df.loc[2023, "current_ratio"], 110 / 95, rel_tol=1e-9)
    # debt to equity = liabilities / equity = 190/140
    assert math.isclose(df.loc[2023, "debt_to_equity"], 190 / 140, rel_tol=1e-9)


def test_yoy_growth(full_company_json):
    df = _ratios_for(full_company_json).set_index("fy")
    assert math.isclose(df.loc[2023, "revenue_yoy_growth"], (220 - 200) / 200, rel_tol=1e-9)
    assert math.isclose(df.loc[2023, "net_income_yoy_growth"], (50 - 40) / 40, rel_tol=1e-9)


def test_signed_pct_change_keeps_sign_meaningful_for_a_negative_base():
    """A loss narrowing from -100 to -50 is an IMPROVEMENT. Plain pandas pct_change()
    reports this as -50% (looks like a decline) because dividing by a negative base flips
    the sign. The fix divides by abs(prior) so improvement stays positive."""
    from tests.conftest import fact, facts_json

    facts = facts_json(
        {
            "NetIncomeLoss": [
                fact(fy=2022, start="2022-01-01", end="2022-12-31", val=-100),
                fact(fy=2023, start="2023-01-01", end="2023-12-31", val=-50),
            ]
        }
    )
    df = _ratios_for(facts).set_index("fy")
    assert df.loc[2023, "net_income_yoy_growth"] > 0, "a shrinking loss must read as improvement, not decline"
    assert math.isclose(df.loc[2023, "net_income_yoy_growth"], 0.5, rel_tol=1e-9)


def test_gross_margin_falls_back_to_revenue_minus_cogs_when_gross_profit_not_tagged():
    from tests.conftest import fact, facts_json

    facts = facts_json(
        {
            "Revenues": [fact(fy=2023, start="2023-01-01", end="2023-12-31", val=1000)],
            "CostOfRevenue": [fact(fy=2023, start="2023-01-01", end="2023-12-31", val=600)],
        }
    )
    df = _ratios_for(facts).set_index("fy")
    assert math.isclose(df.loc[2023, "gross_margin"], 0.4, rel_tol=1e-9)


def test_debt_to_equity_recovers_liabilities_from_balance_sheet_identity(missing_liabilities_json):
    """Walmart's real quirk: no `Liabilities` tag exists, but Assets = Liabilities +
    Equity means it's recoverable without guessing."""
    df = _ratios_for(missing_liabilities_json).set_index("fy")
    expected = (250_000_000_000 - 85_000_000_000) / 85_000_000_000
    assert math.isclose(df.loc[2023, "debt_to_equity"], expected, rel_tol=1e-9)


def test_dupont_identity_holds_exactly(full_company_json):
    """ROE must equal Net Margin x Asset Turnover x Equity Multiplier by construction -
    if this ever drifts, one of the three components is wrong."""
    df = _ratios_for(full_company_json)
    reconstructed = df["net_margin"] * df["asset_turnover"] * df["equity_multiplier"]
    assert (reconstructed - df["roe"]).abs().max() < 1e-9


def test_free_cash_flow_equals_operating_cash_flow_minus_capex(full_company_json):
    df = _ratios_for(full_company_json).set_index("fy")
    assert df.loc[2022, "free_cash_flow"] == 55_000_000_000 - 15_000_000_000
    assert df.loc[2023, "free_cash_flow"] == 60_000_000_000 - 18_000_000_000
    assert math.isclose(df.loc[2023, "fcf_margin"], (60_000_000_000 - 18_000_000_000) / 220_000_000_000)


def test_free_cash_flow_suppressed_for_unclassified_balance_sheet_even_with_capex_tag(bank_with_capex_tag_json):
    """The real Goldman Sachs case: the raw tags exist (OCF, capex) so the subtraction is
    mechanically possible, but a broker-dealer's operating cash flow reflects trading/
    lending activity, not core-business cash generation - FCF must stay suppressed."""
    df = _ratios_for(bank_with_capex_tag_json)
    assert "current_ratio" not in df.columns
    assert "free_cash_flow" not in df.columns
    assert "fcf_margin" not in df.columns


def test_ratios_are_nan_not_crash_on_zero_denominator():
    from tests.conftest import fact, facts_json

    facts = facts_json(
        {
            "Revenues": [fact(fy=2023, start="2023-01-01", end="2023-12-31", val=0)],
            "NetIncomeLoss": [fact(fy=2023, start="2023-01-01", end="2023-12-31", val=5)],
        }
    )
    df = _ratios_for(facts).set_index("fy")
    assert math.isnan(df.loc[2023, "net_margin"])
