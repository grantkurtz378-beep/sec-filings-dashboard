"""Computes standard financial ratios from the annual fundamentals DataFrame."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    return np.where((b == 0) | b.isna() | a.isna(), np.nan, a / b)


def _signed_pct_change(series: pd.Series) -> pd.Series:
    """Like pandas' pct_change(), but divides by abs(prior value). Plain pct_change flips
    sign when the prior value is negative (e.g. a loss narrowing from -100 to -50 reads as
    "-50%", i.e. it LOOKS worse), which is backwards for a metric meant to flag improvement
    vs deterioration. Dividing by the magnitude of the base keeps the sign meaningful."""
    prev = series.shift(1)
    return np.where((prev == 0) | prev.isna() | series.isna(), np.nan, (series - prev) / prev.abs())


def compute_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """One row per fiscal year in, same rows out with ratio + YoY growth columns appended."""
    out = df.copy().sort_values("end").reset_index(drop=True)
    cols = set(out.columns)

    if {"Revenues", "GrossProfit"} <= cols or {"Revenues", "CostOfRevenue"} <= cols:
        gross_profit = out["GrossProfit"] if "GrossProfit" in cols else pd.Series(np.nan, index=out.index)
        if "CostOfRevenue" in cols:
            gross_profit = gross_profit.fillna(out["Revenues"] - out["CostOfRevenue"])
        out["gross_margin"] = _safe_div(gross_profit, out["Revenues"])

    if {"NetIncomeLoss", "Revenues"} <= cols:
        out["net_margin"] = _safe_div(out["NetIncomeLoss"], out["Revenues"])

    if {"AssetsCurrent", "LiabilitiesCurrent"} <= cols:
        out["current_ratio"] = _safe_div(out["AssetsCurrent"], out["LiabilitiesCurrent"])

    if "StockholdersEquity" in cols:
        # Some filers (e.g. Walmart) never tag `Liabilities` as its own line item - it's
        # a subtotal, not every balance sheet presentation reports it directly. It's
        # recoverable from the fundamental identity Assets = Liabilities + Equity.
        liabilities = out["Liabilities"] if "Liabilities" in cols else pd.Series(np.nan, index=out.index)
        if "Assets" in cols:
            liabilities = liabilities.fillna(out["Assets"] - out["StockholdersEquity"])
        out["debt_to_equity"] = _safe_div(liabilities, out["StockholdersEquity"])

    if {"NetIncomeLoss", "StockholdersEquity"} <= cols:
        out["roe"] = _safe_div(out["NetIncomeLoss"], out["StockholdersEquity"])

    if {"NetIncomeLoss", "Assets"} <= cols:
        out["roa"] = _safe_div(out["NetIncomeLoss"], out["Assets"])

    if "Revenues" in cols:
        out["revenue_yoy_growth"] = out["Revenues"].pct_change(fill_method=None)

    if "NetIncomeLoss" in cols:
        out["net_income_yoy_growth"] = _signed_pct_change(out["NetIncomeLoss"])

    # Free cash flow is deliberately gated on having a classified balance sheet
    # (current_ratio computable), not just on the raw tags existing. A couple of banks
    # and broker-dealers (Goldman, Citi) DO tag a PP&E capex line, so the subtraction is
    # mechanically possible - but their "operating cash flow" is dominated by swings in
    # trading inventory, loans, and deposits, not core-business cash generation, so the
    # resulting number isn't the metric FCF is meant to be. Suppressing it here, using the
    # same classified-balance-sheet signal already used for current_ratio, beats silently
    # publishing a number that looks like FCF but means something different for financials.
    has_classified_balance_sheet = {"AssetsCurrent", "LiabilitiesCurrent"} <= cols
    if has_classified_balance_sheet and {"NetCashProvidedByUsedInOperatingActivities", "CapitalExpenditures"} <= cols:
        out["free_cash_flow"] = out["NetCashProvidedByUsedInOperatingActivities"] - out["CapitalExpenditures"]
        if "Revenues" in cols:
            out["fcf_margin"] = _safe_div(out["free_cash_flow"], out["Revenues"])

    # DuPont ROE decomposition: Net Margin x Asset Turnover x Equity Multiplier == ROE.
    # Breaks "how profitable" apart from "how much leverage is doing the work" - the
    # difference between, say, a margin-driven ROE and a leverage-driven one.
    if "Assets" in cols:
        if "Revenues" in cols:
            out["asset_turnover"] = _safe_div(out["Revenues"], out["Assets"])
        if "StockholdersEquity" in cols:
            out["equity_multiplier"] = _safe_div(out["Assets"], out["StockholdersEquity"])

    return out
