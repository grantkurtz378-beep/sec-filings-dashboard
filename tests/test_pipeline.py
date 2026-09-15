"""Tests for src/pipeline.py - specifically the real XBRL data-quality bugs this
project found and fixed (see the README's "Why this is harder than 'call an API'"
section). Each test reproduces one bug on minimal synthetic data, independent of
SEC's live data ever changing.
"""
from __future__ import annotations

from src.pipeline import build_annual_dataframe


def test_comparative_year_mislabeling_is_fixed(mislabeled_comparatives_json):
    """The core bug: SEC's `fy` field reflects the FILING's fiscal year, not the period
    each fact covers, so a naive groupby('fy') parser lets a 10-K's comparative-year
    figures silently overwrite the real current-year value under the same fy label."""
    df = build_annual_dataframe(mislabeled_comparatives_json)

    assert sorted(df["fy"].tolist()) == [2010, 2011, 2012]
    assert df["fy"].nunique() == len(df), "one row per fiscal year - no label collisions"

    by_fy = df.set_index("fy")
    assert by_fy.loc[2010, "NetIncomeLoss"] == 14_013_000_000
    assert by_fy.loc[2011, "NetIncomeLoss"] == 25_922_000_000
    assert by_fy.loc[2012, "NetIncomeLoss"] == 41_733_000_000, (
        "FY2012's own figure must win, not the FY2010 comparative both stamped fy=2012"
    )


def test_revenue_tag_drift_is_merged_into_continuous_history(revenue_tag_drift_json):
    """Most companies moved off `Revenues` onto the ASC-606 contract-revenue tag around
    2018. Pulling only one tag truncates history; the alias chain should merge both into
    one continuous column."""
    df = build_annual_dataframe(revenue_tag_drift_json)

    assert sorted(df["fy"].tolist()) == [2016, 2017, 2018, 2019]
    by_fy = df.set_index("fy")
    assert by_fy.loc[2016, "Revenues"] == 100_000_000_000
    assert by_fy.loc[2018, "Revenues"] == 125_000_000_000, "post-ASC-606 tag must be picked up"


def test_quarterly_entries_excluded_from_annual_duration_fields():
    """A duration concept (income statement / cash flow) must only pick up full-year
    (~365 day) periods, not the quarterly stub periods XBRL also reports under the same
    tag and fp='FY' is not sufficient alone to guarantee that."""
    from tests.conftest import fact, facts_json

    facts = facts_json(
        {
            "NetIncomeLoss": [
                fact(fy=2023, start="2023-01-01", end="2023-03-31", val=1_000_000_000),  # Q1 stub
                fact(fy=2023, start="2023-01-01", end="2023-12-31", val=10_000_000_000),  # real annual
            ]
        }
    )
    df = build_annual_dataframe(facts)
    assert df.loc[df["fy"] == 2023, "NetIncomeLoss"].iloc[0] == 10_000_000_000


def test_non_10k_filings_are_excluded():
    """A 10-Q reporting the same concept must never leak into the annual series."""
    from tests.conftest import fact, facts_json

    facts = facts_json(
        {
            "Assets": [
                fact(fy=2023, end="2023-06-30", val=999_000_000_000, form="10-Q", fp="Q2"),
                fact(fy=2023, end="2023-12-31", val=500_000_000_000, form="10-K", fp="FY"),
            ]
        }
    )
    df = build_annual_dataframe(facts)
    assert len(df) == 1
    assert df.iloc[0]["Assets"] == 500_000_000_000


def test_bank_style_filer_gets_no_classified_balance_sheet_columns(bank_style_json):
    """A financial institution's companyfacts response has no AssetsCurrent /
    LiabilitiesCurrent, no GrossProfit / CostOfRevenue - the parser must not invent
    columns that were never tagged."""
    df = build_annual_dataframe(bank_style_json)
    assert "AssetsCurrent" not in df.columns
    assert "LiabilitiesCurrent" not in df.columns
    assert "GrossProfit" not in df.columns
    assert "CapitalExpenditures" not in df.columns
    # but the concepts a bank DOES tag should still be present
    assert {"Revenues", "NetIncomeLoss", "Assets", "Liabilities", "StockholdersEquity"} <= set(df.columns)


def test_missing_liabilities_tag_does_not_break_the_pipeline(missing_liabilities_json):
    """build_annual_dataframe itself shouldn't try to recover Liabilities (that fallback
    lives in ratios.py, tested separately) - it should just omit the column cleanly."""
    df = build_annual_dataframe(missing_liabilities_json)
    assert "Liabilities" not in df.columns
    assert df.iloc[0]["Assets"] == 250_000_000_000


def test_empty_facts_returns_empty_dataframe():
    df = build_annual_dataframe({"facts": {"us-gaap": {}}})
    assert df.empty
