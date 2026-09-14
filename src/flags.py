"""Flags ratios whose year-over-year change exceeds a threshold."""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_THRESHOLD = 0.20

# YoY growth columns are themselves already a % change - flag on their own size.
# Everything else is a level (a margin, a ratio) - flag on its relative move vs last year.
_GROWTH_COLUMNS = ["revenue_yoy_growth", "net_income_yoy_growth"]
_LEVEL_COLUMNS = ["gross_margin", "net_margin", "current_ratio", "debt_to_equity", "roe", "roa"]
FLAGGABLE_COLUMNS = _LEVEL_COLUMNS + _GROWTH_COLUMNS


def compute_flags(ratios_df: pd.DataFrame, threshold: float = DEFAULT_THRESHOLD) -> pd.DataFrame:
    """Adds a `<col>_flag` boolean column per ratio present, plus `any_flag` for row highlighting."""
    out = ratios_df.copy()

    for col in _LEVEL_COLUMNS:
        if col not in out.columns:
            continue
        prev = out[col].shift(1)
        rel_change = np.where(
            (prev == 0) | prev.isna() | out[col].isna(), np.nan, (out[col] - prev) / prev.abs()
        )
        out[f"{col}_flag"] = np.abs(rel_change) > threshold

    for col in _GROWTH_COLUMNS:
        if col not in out.columns:
            continue
        out[f"{col}_flag"] = out[col].abs() > threshold

    flag_cols = [f"{c}_flag" for c in FLAGGABLE_COLUMNS if f"{c}_flag" in out.columns]
    out["any_flag"] = out[flag_cols].fillna(False).any(axis=1) if flag_cols else False
    return out
