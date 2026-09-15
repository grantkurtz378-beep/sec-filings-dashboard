"""Tests for src/flags.py - decoupled from the pipeline, so these build the ratios-shaped
DataFrame directly rather than going through real XBRL parsing."""
from __future__ import annotations

import pandas as pd

from src.flags import compute_flags


def test_level_column_flagged_only_past_threshold():
    df = pd.DataFrame({"fy": [2021, 2022, 2023], "net_margin": [0.20, 0.23, 0.35]})
    out = compute_flags(df, threshold=0.20)
    # 2022: (0.23-0.20)/0.20 = 15% -> not flagged
    # 2023: (0.35-0.23)/0.23 = ~52% -> flagged
    assert out.loc[out["fy"] == 2022, "net_margin_flag"].iloc[0] == False  # noqa: E712
    assert out.loc[out["fy"] == 2023, "net_margin_flag"].iloc[0] == True  # noqa: E712


def test_first_year_is_never_flagged_no_prior_to_compare():
    df = pd.DataFrame({"fy": [2021], "net_margin": [0.90]})
    out = compute_flags(df, threshold=0.20)
    assert out.loc[0, "net_margin_flag"] == False  # noqa: E712


def test_growth_column_flagged_on_its_own_magnitude():
    """revenue_yoy_growth IS already a % change - it's flagged on its own size, not a
    second derivative of itself."""
    df = pd.DataFrame({"fy": [2022, 2023], "revenue_yoy_growth": [0.05, 0.25]})
    out = compute_flags(df, threshold=0.20)
    assert out.loc[out["fy"] == 2022, "revenue_yoy_growth_flag"].iloc[0] == False  # noqa: E712
    assert out.loc[out["fy"] == 2023, "revenue_yoy_growth_flag"].iloc[0] == True  # noqa: E712


def test_negative_growth_flags_on_absolute_size():
    df = pd.DataFrame({"fy": [2022, 2023], "revenue_yoy_growth": [0.05, -0.30]})
    out = compute_flags(df, threshold=0.20)
    assert out.loc[out["fy"] == 2023, "revenue_yoy_growth_flag"].iloc[0] == True  # noqa: E712


def test_zero_prior_value_does_not_crash():
    df = pd.DataFrame({"fy": [2022, 2023], "debt_to_equity": [0.0, 1.5]})
    out = compute_flags(df, threshold=0.20)
    # relative change from a zero base is undefined - must not raise, and must not flag
    assert out.loc[out["fy"] == 2023, "debt_to_equity_flag"].iloc[0] == False  # noqa: E712


def test_missing_column_is_skipped_not_an_error():
    df = pd.DataFrame({"fy": [2022, 2023], "net_margin": [0.1, 0.2]})
    out = compute_flags(df, threshold=0.20)
    assert "roe_flag" not in out.columns


def test_any_flag_true_when_any_metric_flagged():
    df = pd.DataFrame({"fy": [2022, 2023], "net_margin": [0.20, 0.20], "roe": [0.10, 0.50]})
    out = compute_flags(df, threshold=0.20)
    assert out.loc[out["fy"] == 2023, "any_flag"].iloc[0] == True  # noqa: E712
    assert out.loc[out["fy"] == 2022, "any_flag"].iloc[0] == False  # noqa: E712


def test_custom_threshold_is_respected():
    df = pd.DataFrame({"fy": [2022, 2023], "net_margin": [0.20, 0.25]})  # +25% relative move
    strict = compute_flags(df, threshold=0.10)
    loose = compute_flags(df, threshold=0.50)
    assert strict.loc[strict["fy"] == 2023, "net_margin_flag"].iloc[0] == True  # noqa: E712
    assert loose.loc[loose["fy"] == 2023, "net_margin_flag"].iloc[0] == False  # noqa: E712
