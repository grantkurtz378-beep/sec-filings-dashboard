"""Shared fixtures for building minimal synthetic companyfacts JSON.

Real SEC responses run thousands of lines; these helpers build the smallest possible
fact list that still exercises the real quirk under test, so each test is fast,
deterministic, and doesn't depend on SEC's data (or the network) staying unchanged.
"""
from __future__ import annotations

import pytest


def fact(fy, end, val, start=None, filed=None, form="10-K", fp="FY"):
    """One raw XBRL fact entry, matching the shape SEC's companyfacts API returns."""
    entry = {"fy": fy, "end": end, "val": val, "form": form, "fp": fp, "filed": filed or f"{end}"}
    if start:
        entry["start"] = start
    return entry


def facts_json(tags: dict) -> dict:
    """tags: {tag_name: [fact(...), ...]} -> a full companyfacts-shaped dict."""
    return {"facts": {"us-gaap": {tag: {"units": {"USD": entries}} for tag, entries in tags.items()}}}


@pytest.fixture
def mislabeled_comparatives_json():
    """Reproduces the real bug found in AAPL's data: the FY2012 10-K re-reports FY2010
    and FY2011 net income as comparatives, all three stamped fy=2012 (SEC's `fy` field
    reflects the FILING's fiscal year focus, not the period each fact covers). A naive
    "group by fy" parser would let the FY2010 comparative silently overwrite the real
    FY2012 figure."""
    return facts_json(
        {
            "NetIncomeLoss": [
                # FY2010's own 10-K, correctly labeled
                fact(fy=2010, start="2009-09-27", end="2010-09-25", val=14_013_000_000, filed="2010-10-27"),
                # FY2011's own 10-K, correctly labeled
                fact(fy=2011, start="2010-09-26", end="2011-09-24", val=25_922_000_000, filed="2011-10-26"),
                # FY2012's 10-K reports THREE years of comparatives, all stamped fy=2012
                fact(fy=2012, start="2009-09-27", end="2010-09-25", val=14_013_000_000, filed="2012-10-31"),
                fact(fy=2012, start="2010-09-26", end="2011-09-24", val=25_922_000_000, filed="2012-10-31"),
                fact(fy=2012, start="2011-09-25", end="2012-09-29", val=41_733_000_000, filed="2012-10-31"),
            ],
            "Revenues": [
                fact(fy=2010, start="2009-09-27", end="2010-09-25", val=65_225_000_000, filed="2010-10-27"),
                fact(fy=2011, start="2010-09-26", end="2011-09-24", val=108_249_000_000, filed="2011-10-26"),
                fact(fy=2012, start="2009-09-27", end="2010-09-25", val=65_225_000_000, filed="2012-10-31"),
                fact(fy=2012, start="2010-09-26", end="2011-09-24", val=108_249_000_000, filed="2012-10-31"),
                fact(fy=2012, start="2011-09-25", end="2012-09-29", val=156_508_000_000, filed="2012-10-31"),
            ],
        }
    )


@pytest.fixture
def revenue_tag_drift_json():
    """Reproduces ASC 606 tag drift: early years under `Revenues`, later years under
    `RevenueFromContractWithCustomerExcludingAssessedTax`. Pulling only one tag would
    truncate history for most large companies (they all made this switch ~2018)."""
    return facts_json(
        {
            "Revenues": [
                fact(fy=2016, start="2016-01-01", end="2016-12-31", val=100_000_000_000),
                fact(fy=2017, start="2017-01-01", end="2017-12-31", val=110_000_000_000),
            ],
            "RevenueFromContractWithCustomerExcludingAssessedTax": [
                fact(fy=2018, start="2018-01-01", end="2018-12-31", val=125_000_000_000),
                fact(fy=2019, start="2019-01-01", end="2019-12-31", val=140_000_000_000),
            ],
        }
    )


@pytest.fixture
def bank_style_json():
    """A financial institution: no classified balance sheet (no AssetsCurrent /
    LiabilitiesCurrent), no GrossProfit/CostOfRevenue, no PP&E capex line - all
    realistic omissions for a bank or broker-dealer, none of them missing data."""
    return facts_json(
        {
            "Revenues": [fact(fy=2023, start="2023-01-01", end="2023-12-31", val=50_000_000_000)],
            "NetIncomeLoss": [fact(fy=2023, start="2023-01-01", end="2023-12-31", val=10_000_000_000)],
            "Assets": [fact(fy=2023, end="2023-12-31", val=1_000_000_000_000)],
            "Liabilities": [fact(fy=2023, end="2023-12-31", val=900_000_000_000)],
            "StockholdersEquity": [fact(fy=2023, end="2023-12-31", val=100_000_000_000)],
            "NetCashProvidedByUsedInOperatingActivities": [
                fact(fy=2023, start="2023-01-01", end="2023-12-31", val=5_000_000_000)
            ],
        }
    )


@pytest.fixture
def bank_with_capex_tag_json():
    """The real Goldman Sachs / Citigroup case: a broker-dealer that DOES tag a small
    PP&E capex line (office/tech infrastructure) despite having no classified balance
    sheet. Free cash flow must still be suppressed - see ratios.py's comment on why."""
    return facts_json(
        {
            "Revenues": [fact(fy=2023, start="2023-01-01", end="2023-12-31", val=50_000_000_000)],
            "NetIncomeLoss": [fact(fy=2023, start="2023-01-01", end="2023-12-31", val=10_000_000_000)],
            "Assets": [fact(fy=2023, end="2023-12-31", val=1_000_000_000_000)],
            "StockholdersEquity": [fact(fy=2023, end="2023-12-31", val=100_000_000_000)],
            "NetCashProvidedByUsedInOperatingActivities": [
                fact(fy=2023, start="2023-01-01", end="2023-12-31", val=-15_000_000_000)
            ],
            "PaymentsToAcquirePropertyPlantAndEquipment": [
                fact(fy=2023, start="2023-01-01", end="2023-12-31", val=2_000_000_000)
            ],
        }
    )


@pytest.fixture
def missing_liabilities_json():
    """Reproduces Walmart's real quirk: no `Liabilities` tag at all, only Assets and
    StockholdersEquity - Liabilities is recoverable via Assets = Liabilities + Equity."""
    return facts_json(
        {
            "Assets": [fact(fy=2023, end="2023-01-31", val=250_000_000_000)],
            "StockholdersEquity": [fact(fy=2023, end="2023-01-31", val=85_000_000_000)],
            "NetIncomeLoss": [fact(fy=2023, start="2022-02-01", end="2023-01-31", val=11_000_000_000)],
        }
    )


@pytest.fixture
def full_company_json():
    """A "normal" non-financial company with two full years of every tracked tag, clean
    enough to check the DuPont identity and free-cash-flow math against hand-computed values."""
    return facts_json(
        {
            "Revenues": [
                fact(fy=2022, start="2022-01-01", end="2022-12-31", val=200_000_000_000),
                fact(fy=2023, start="2023-01-01", end="2023-12-31", val=220_000_000_000),
            ],
            "CostOfRevenue": [
                fact(fy=2022, start="2022-01-01", end="2022-12-31", val=120_000_000_000),
                fact(fy=2023, start="2023-01-01", end="2023-12-31", val=125_000_000_000),
            ],
            "NetIncomeLoss": [
                fact(fy=2022, start="2022-01-01", end="2022-12-31", val=40_000_000_000),
                fact(fy=2023, start="2023-01-01", end="2023-12-31", val=50_000_000_000),
            ],
            "Assets": [
                fact(fy=2022, end="2022-12-31", val=300_000_000_000),
                fact(fy=2023, end="2023-12-31", val=330_000_000_000),
            ],
            "Liabilities": [
                fact(fy=2022, end="2022-12-31", val=180_000_000_000),
                fact(fy=2023, end="2023-12-31", val=190_000_000_000),
            ],
            "StockholdersEquity": [
                fact(fy=2022, end="2022-12-31", val=120_000_000_000),
                fact(fy=2023, end="2023-12-31", val=140_000_000_000),
            ],
            "AssetsCurrent": [
                fact(fy=2022, end="2022-12-31", val=100_000_000_000),
                fact(fy=2023, end="2023-12-31", val=110_000_000_000),
            ],
            "LiabilitiesCurrent": [
                fact(fy=2022, end="2022-12-31", val=80_000_000_000),
                fact(fy=2023, end="2023-12-31", val=95_000_000_000),
            ],
            "NetCashProvidedByUsedInOperatingActivities": [
                fact(fy=2022, start="2022-01-01", end="2022-12-31", val=55_000_000_000),
                fact(fy=2023, start="2023-01-01", end="2023-12-31", val=60_000_000_000),
            ],
            "PaymentsToAcquirePropertyPlantAndEquipment": [
                fact(fy=2022, start="2022-01-01", end="2022-12-31", val=15_000_000_000),
                fact(fy=2023, start="2023-01-01", end="2023-12-31", val=18_000_000_000),
            ],
        }
    )
