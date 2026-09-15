"""Parses raw SEC companyfacts JSON into one row per fiscal year.

Two things make raw XBRL messy enough to need this layer:

1. Tag names change over time (e.g. most companies moved off `Revenues` onto
   `RevenueFromContractWithCustomerExcludingAssessedTax` after adopting ASC 606
   around 2018). Pulling only the "obvious" tag silently truncates history for
   exactly the big-tech names this project is meant to demo on. TAG_ALIASES
   lists candidates in priority order per logical field.
2. The same fiscal year can appear multiple times in the raw fact list
   (restatements, or as prior-year comparatives in a later filing). We keep
   the most-recently-filed value for each fiscal year.
"""
from __future__ import annotations

import pandas as pd

TAG_ALIASES: dict[str, list[str]] = {
    "Revenues": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        # Broker-dealers (Goldman, Morgan Stanley) report their top line net of interest
        # expense instead, since interest is a cost of their core trading/lending business.
        "RevenuesNetOfInterestExpense",
    ],
    "CostOfRevenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
    ],
    "GrossProfit": ["GrossProfit"],
    "NetIncomeLoss": ["NetIncomeLoss"],
    "Assets": ["Assets"],
    "Liabilities": ["Liabilities"],
    "StockholdersEquity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "AssetsCurrent": ["AssetsCurrent"],
    "LiabilitiesCurrent": ["LiabilitiesCurrent"],
    "NetCashProvidedByUsedInOperatingActivities": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "CapitalExpenditures": [
        # Apple's own history shows the same tag-drift pattern as revenue: it reported
        # capex under ProductiveAssets through FY2013, then switched to
        # PropertyPlantAndEquipment (confirmed via an identical overlapping FY2013 value
        # under both tags). Banks/broker-dealers don't tag either - no material PP&E capex
        # in that business model - so free cash flow is intentionally left N/A for them.
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
}

# Balance-sheet concepts are measured at a point in time; income/cash-flow concepts
# are measured over a period and need a duration check (see _extract_tag_series).
INSTANT_FIELDS = {"Assets", "Liabilities", "StockholdersEquity", "AssetsCurrent", "LiabilitiesCurrent"}


def _best_unit_key(units: dict) -> str | None:
    if "USD" in units:
        return "USD"
    return next(iter(units), None)


def _extract_tag_series(facts_json: dict, tag: str, is_instant: bool) -> pd.DataFrame:
    """Columns [fy, end, val] for one XBRL tag, one row per real fiscal period (10-K/FY only).

    Keyed on `end` date rather than the raw `fy` field: a 10-K retags 2-3 years of
    comparatives with its OWN fiscal-year focus (e.g. the FY2012 10-K reports FY2010's
    net income again, stamped fy=2012), so grouping by `fy` silently overwrites current-year
    figures with stale comparatives. `end` uniquely identifies the real period regardless of
    which filing reported it, so it's the only safe dedup/merge key. The displayed `fy` label
    is recovered afterward as the smallest fy seen for that `end` (the filing where the period
    was actually current, and so the only one that labeled it correctly).
    """
    tag_data = facts_json.get("facts", {}).get("us-gaap", {}).get(tag)
    if tag_data is None:
        return pd.DataFrame(columns=["fy", "end", "val"])

    unit_key = _best_unit_key(tag_data.get("units", {}))
    if unit_key is None:
        return pd.DataFrame(columns=["fy", "end", "val"])

    rows = []
    for e in tag_data["units"][unit_key]:
        if e.get("form") != "10-K" or e.get("fp") != "FY":
            continue
        if e.get("fy") is None or e.get("val") is None or e.get("end") is None:
            continue
        if not is_instant:
            start = e.get("start")
            if not start:
                continue
            duration_days = (pd.Timestamp(e["end"]) - pd.Timestamp(start)).days
            if not (350 <= duration_days <= 380):
                continue  # skip stub/partial periods, keep only full-fiscal-year durations
        rows.append({"fy": e["fy"], "end": e["end"], "val": e["val"], "filed": e.get("filed", "")})

    if not rows:
        return pd.DataFrame(columns=["fy", "end", "val"])

    raw = pd.DataFrame(rows)
    fy_label_by_end = raw.groupby("end")["fy"].min()
    # groupby+idxmax (not sort+drop_duplicates) so the "most recently filed" tiebreak
    # doesn't depend on pandas' sort being stable, which sort_values is not by default
    best_idx = raw.groupby("end")["filed"].idxmax()
    deduped = raw.loc[best_idx, ["end", "val"]].copy()
    deduped["fy"] = deduped["end"].map(fy_label_by_end).astype(int)
    return deduped[["fy", "end", "val"]].sort_values("end").reset_index(drop=True)


def build_annual_dataframe(facts_json: dict) -> pd.DataFrame:
    """Merges every tracked XBRL field into one row-per-fiscal-period DataFrame."""
    field_frames: dict[str, pd.DataFrame] = {}
    global_fy_by_end: dict[str, int] = {}

    for field, candidate_tags in TAG_ALIASES.items():
        is_instant = field in INSTANT_FIELDS
        collected = pd.DataFrame(columns=["fy", "end", "val"])
        for tag in candidate_tags:
            candidate_df = _extract_tag_series(facts_json, tag, is_instant)
            if candidate_df.empty:
                continue
            missing_ends = set(candidate_df["end"]) - set(collected["end"])
            if missing_ends:
                collected = pd.concat(
                    [collected, candidate_df[candidate_df["end"].isin(missing_ends)]],
                    ignore_index=True,
                )
        for _, r in collected.iterrows():
            end, fy = r["end"], int(r["fy"])
            if end not in global_fy_by_end or fy < global_fy_by_end[end]:
                global_fy_by_end[end] = fy
        field_df = collected[["end", "val"]].rename(columns={"val": field})
        field_df[field] = field_df[field].astype(float)
        field_frames[field] = field_df

    non_empty = [df for df in field_frames.values() if not df.empty]
    if not non_empty:
        return pd.DataFrame()

    merged = non_empty[0]
    for df in non_empty[1:]:
        merged = merged.merge(df, on="end", how="outer")

    merged["fy"] = merged["end"].map(global_fy_by_end)
    # The earliest 1-3 periods before a company's own XBRL history begins can only ever
    # appear as multi-year comparatives, never as anyone's "current" year, so they can't get
    # a distinct fy label and collide with a later, correctly-labeled year. Keep only the
    # most recent period per fy label so the output is genuinely one row per fiscal year.
    merged = merged.loc[merged.groupby("fy")["end"].idxmax()]
    merged = merged.sort_values("end").reset_index(drop=True)
    ordered_cols = ["fy", "end"] + [c for c in TAG_ALIASES if c in merged.columns]
    return merged[ordered_cols]


def get_annual_fundamentals(ticker: str, max_age_days: float = 1.0, force_refresh: bool = False) -> pd.DataFrame:
    """Ticker -> cached-and-parsed annual fundamentals DataFrame. Import-local to avoid a hard
    dependency on the network client for callers that already have a facts_json in hand."""
    from src.edgar_client import get_company_facts

    facts_json = get_company_facts(ticker, max_age_days=max_age_days, force_refresh=force_refresh)
    df = build_annual_dataframe(facts_json)
    df.insert(0, "ticker", ticker.upper())
    return df
