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

    return out
