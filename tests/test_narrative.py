"""Tests for src/narrative.py, split out of app.py specifically so this business logic
is testable without pulling in streamlit. See the DuPont test below for the real bug
that motivated the split: it was found live (not in a test) while manually checking the
app against AT&T's real financials."""
from __future__ import annotations

import math

import pandas as pd

from src.narrative import _signed_growth, format_currency, format_value, generate_summary


def test_format_currency_scales_by_magnitude():
    assert format_currency(416_200_000_000) == "$416.2B"
    assert format_currency(112_000_000) == "$112.0M"
    assert format_currency(5_000) == "$5.0K"
    assert format_currency(-19_400_000_000) == "-$19.4B"


def test_format_currency_nan_is_em_dash():
    assert format_currency(float("nan")) == "—"


def test_format_value_percent_and_multiple():
    assert format_value("net_margin", 0.269) == "26.9%"
    assert format_value("debt_to_equity", 3.87) == "3.87x"


def test_signed_growth_keeps_sign_meaningful_across_a_negative_base():
    """The exact real bug: dividing by a negative base flips the sign of an improvement.
    AT&T's net margin went from -3.6% (FY2020) to +17.5% (FY2025) - a genuine, large
    improvement - but new/old - 1 reports this as -583% (looks like a huge decline)."""
    plain_pct_change = 0.174718 / -0.036183 - 1
    assert plain_pct_change < -5, "sanity check: the naive formula really does blow up"

    signed = _signed_growth(0.174718, -0.036183)
    assert signed > 0, "an improvement from negative to positive must read as positive"
    assert math.isclose(signed, (0.174718 - (-0.036183)) / abs(-0.036183), rel_tol=1e-9)


def test_dupont_bullet_displays_natural_units_not_percent_of_a_percent():
    """End-to-end version of the bug: build a DataFrame shaped like AT&T's real ratios
    (net margin crosses zero over the lookback window). Ranking "biggest mover" by
    relative change is fine even near zero, but DISPLAYING that relative change as a
    percentage is not: (new-old)/abs(old) for -3.6% -> +17.5% is +586% even with the sign
    fixed. The bullet must show the move in the metric's own natural unit (percentage
    points for a margin, "x" for a multiple) instead."""
    fy = list(range(2019, 2026))
    net_margin = [0.077, -0.036, 0.150, -0.071, 0.118, 0.089, 0.175]  # crosses zero twice
    asset_turnover = [0.30, 0.27, 0.24, 0.30, 0.30, 0.31, 0.30]
    equity_multiplier = [2.7, 2.9, 3.0, 3.8, 3.5, 3.3, 3.3]
    df = pd.DataFrame(
        {
            "fy": fy,
            "Revenues": [1000] * len(fy),
            "NetIncomeLoss": [m * 1000 for m in net_margin],
            "net_margin": net_margin,
            "asset_turnover": asset_turnover,
            "equity_multiplier": equity_multiplier,
            "roe": [nm * at * em for nm, at, em in zip(net_margin, asset_turnover, equity_multiplier)],
        }
    )
    bullets = generate_summary(df)
    dupont_bullets = [b for b in bullets if b.startswith("DuPont breakdown")]
    assert dupont_bullets, "expected a DuPont bullet to be generated"
    bullet = dupont_bullets[0]

    assert "%" not in bullet, f"DuPont bullet should show pp/x, not percent-of-a-percent: {bullet}"
    assert "net margin" in bullet, "the dramatic near-zero swing should be identified as the biggest mover"

    import re

    pp_values = [float(p) for p in re.findall(r"([+-]?\d+\.\d+) pp", bullet)]
    x_values = [float(p) for p in re.findall(r"([+-]?\d+\.\d+)x", bullet)]
    assert pp_values and x_values, f"expected both a pp value and x values in: {bullet}"
    assert all(abs(p) <= 100 for p in pp_values), f"a margin can't move more than 100pp: {bullet}"
    assert all(abs(x) <= 20 for x in x_values), f"implausible multiple swing, likely a bug: {bullet}"


def test_generate_summary_handles_a_single_fiscal_year_without_crashing():
    df = pd.DataFrame(
        {
            "fy": [2023],
            "Revenues": [1000.0],
            "NetIncomeLoss": [100.0],
            "net_margin": [0.10],
            "current_ratio": [1.5],
        }
    )
    bullets = generate_summary(df)
    assert isinstance(bullets, list)


def test_generate_summary_notes_missing_current_ratio_for_banks():
    df = pd.DataFrame({"fy": [2022, 2023], "Revenues": [100.0, 110.0], "NetIncomeLoss": [10.0, 12.0]})
    bullets = generate_summary(df)
    assert any("classified" in b for b in bullets)


def test_high_roe_caveat_only_fires_above_threshold():
    low_roe = pd.DataFrame({"fy": [2022, 2023], "roe": [0.10, 0.15]})
    high_roe = pd.DataFrame({"fy": [2022, 2023], "roe": [0.10, 1.50]})
    assert not any("unusually high" in b for b in generate_summary(low_roe))
    assert any("unusually high" in b for b in generate_summary(high_roe))
