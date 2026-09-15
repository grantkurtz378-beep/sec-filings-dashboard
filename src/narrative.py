"""Formatting helpers and the "At a Glance" narrative generator.

Split out from app.py (which imports streamlit and executes UI code at import time,
making it awkward to unit test) so this pure business logic - and the real bug found in
it - has direct test coverage. See tests/test_narrative.py.
"""
from __future__ import annotations

import pandas as pd

from src.flags import FLAGGABLE_COLUMNS

PERCENT_COLUMNS = [
    "gross_margin", "net_margin", "roe", "roa", "revenue_yoy_growth", "net_income_yoy_growth", "fcf_margin",
]
RATIO_COLUMNS = ["current_ratio", "debt_to_equity", "asset_turnover", "equity_multiplier"]
GROWTH_COLUMNS = ["revenue_yoy_growth", "net_income_yoy_growth"]
# Whether a larger value is generally the healthier direction, for flag coloring and the
# summary. Deliberately simple (real analysis is more nuanced - e.g. an extremely high
# current ratio can mean idle capital) but a reasonable default for a move in isolation.
HIGHER_IS_BETTER = {
    "gross_margin": True,
    "net_margin": True,
    "roe": True,
    "roa": True,
    "current_ratio": True,
    "debt_to_equity": False,
    "fcf_margin": True,
}
COLUMN_LABELS = {
    "gross_margin": "Gross Margin",
    "net_margin": "Net Margin",
    "current_ratio": "Current Ratio",
    "debt_to_equity": "Debt / Equity",
    "roe": "ROE",
    "roa": "ROA",
    "revenue_yoy_growth": "Revenue YoY",
    "net_income_yoy_growth": "Net Income YoY",
    "fcf_margin": "FCF Margin",
    "asset_turnover": "Asset Turnover",
    "equity_multiplier": "Equity Multiplier",
    "free_cash_flow": "Free Cash Flow",
    "Revenues": "Revenue",
    "NetIncomeLoss": "Net Income",
}


def format_currency(val: float) -> str:
    if pd.isna(val):
        return "—"
    sign = "-" if val < 0 else ""
    abs_val = abs(val)
    if abs_val >= 1e9:
        return f"{sign}${abs_val / 1e9:,.1f}B"
    if abs_val >= 1e6:
        return f"{sign}${abs_val / 1e6:,.1f}M"
    if abs_val >= 1e3:
        return f"{sign}${abs_val / 1e3:,.1f}K"
    return f"{sign}${abs_val:,.0f}"


def format_value(raw_col: str, val: float) -> str:
    if pd.isna(val):
        return "—"
    if raw_col in PERCENT_COLUMNS:
        return f"{val:.1%}"
    if raw_col in RATIO_COLUMNS:
        return f"{val:.2f}x"
    return str(val)


def _signed_growth(new: float, old: float) -> float:
    """(new-old)/abs(old), not a plain percent change: a level like net margin (or,
    more rarely, equity multiplier when equity itself is negative) can cross zero, and
    dividing by a negative base flips the sign so an improvement reads as a huge decline.
    Same reasoning as ratios._signed_pct_change, just for two scalars instead of a Series."""
    return (new - old) / abs(old)


def generate_summary(df: pd.DataFrame) -> list:
    """Plain-English reads of the computed ratios - rule-based, not an LLM call, so every
    line is a direct, checkable statement about the numbers already on the page."""
    d = df.sort_values("fy").reset_index(drop=True)
    latest = d.iloc[-1]
    prior = d.iloc[-2] if len(d) > 1 else None
    bullets = []

    if "revenue_yoy_growth" in d.columns and pd.notna(latest.get("revenue_yoy_growth")):
        g = latest["revenue_yoy_growth"]
        line = f"Revenue {'grew' if g >= 0 else 'declined'} {abs(g):.1%} in FY{int(latest['fy'])}"
        lookback = min(5, len(d) - 1)
        base_row = d.iloc[-1 - lookback]
        if lookback >= 2 and pd.notna(base_row["Revenues"]) and base_row["Revenues"] > 0:
            cagr = (latest["Revenues"] / base_row["Revenues"]) ** (1 / lookback) - 1
            line += f", compounding at {cagr:.1%}/year over the last {lookback} years"
        bullets.append(line + ".")

    if "net_margin" in d.columns:
        recent = d["net_margin"].dropna().tail(4)
        if len(recent) >= 2 and pd.notna(latest.get("net_margin")):
            diffs = recent.diff().dropna()
            up, down = (diffs > 0).sum(), (diffs < 0).sum()
            trend = "improving" if up > down else ("declining" if down > up else "roughly stable")
            bullets.append(
                f"Net margin is {latest['net_margin']:.1%} in the latest fiscal year, "
                f"{trend} over the last {len(recent)} years."
            )

    if "current_ratio" in d.columns:
        if pd.notna(latest.get("current_ratio")):
            cr = latest["current_ratio"]
            note = " — current liabilities exceed current assets" if cr < 1.0 else ""
            bullets.append(f"Current ratio is {cr:.2f}x as of FY{int(latest['fy'])}{note}.")
    else:
        bullets.append(
            "Current ratio isn't shown — this filer doesn't report a classified "
            "balance sheet, typical for banks and broker-dealers."
        )

    if "debt_to_equity" in d.columns:
        series = d["debt_to_equity"].dropna()
        if len(series) >= 2:
            first_idx, last_idx = series.index[0], series.index[-1]
            direction = "risen" if series.iloc[-1] > series.iloc[0] else "fallen"
            bullets.append(
                f"Debt-to-equity has {direction} from {series.iloc[0]:.2f}x in FY{int(d.loc[first_idx, 'fy'])} "
                f"to {series.iloc[-1]:.2f}x in FY{int(d.loc[last_idx, 'fy'])}."
            )

    if "roe" in d.columns and pd.notna(latest.get("roe")) and latest["roe"] > 0.4:
        bullets.append(
            f"ROE of {latest['roe']:.1%} is unusually high — often a sign of significant "
            "leverage or an equity base thinned by buybacks rather than operating performance "
            "alone; worth reading alongside debt-to-equity."
        )

    dupont_cols = ["net_margin", "asset_turnover", "equity_multiplier"]
    if all(c in d.columns for c in dupont_cols):
        lookback = min(5, len(d) - 1)
        base = d.iloc[-1 - lookback] if lookback >= 2 else None
        if base is not None and all(pd.notna(latest.get(c)) and pd.notna(base.get(c)) and base[c] != 0 for c in dupont_cols):
            # Relative (signed) change is the right way to RANK which factor moved most -
            # it's comparable across factors in different units (a margin vs. a multiple).
            # But it's the wrong thing to DISPLAY: a margin that crosses near zero (a real
            # case - AT&T's net margin went from -3.6% to +17.5%) makes relative change
            # balloon to +586% even with the sign fixed, which reads as a bug, not an
            # insight. Display each factor's move in its own natural unit instead.
            relative_move = {c: _signed_growth(latest[c], base[c]) for c in dupont_cols}
            driver_label = {
                "net_margin": "net margin",
                "asset_turnover": "asset turnover",
                "equity_multiplier": "leverage (equity multiplier)",
            }
            biggest = max(relative_move, key=lambda c: abs(relative_move[c]))
            diff = {c: latest[c] - base[c] for c in dupont_cols}
            bullets.append(
                f"DuPont breakdown: over the last {lookback} years, {driver_label[biggest]} has moved the most "
                f"toward the current ROE — net margin {diff['net_margin'] * 100:+.1f} pp, "
                f"asset turnover {diff['asset_turnover']:+.2f}x, leverage {diff['equity_multiplier']:+.2f}x."
            )

    if "free_cash_flow" in d.columns and pd.notna(latest.get("free_cash_flow")):
        fcf = latest["free_cash_flow"]
        ni = latest.get("NetIncomeLoss")
        line = f"Free cash flow was {format_currency(fcf)} in FY{int(latest['fy'])}"
        if pd.notna(ni) and ni != 0:
            line += (
                f", {fcf / ni:.0%} of net income — a useful cross-check against reported earnings, "
                "since it's less sensitive to non-cash accounting choices"
            )
        bullets.append(line + ".")

    if prior is not None:
        flag_cols = [c for c in FLAGGABLE_COLUMNS if f"{c}_flag" in d.columns and bool(latest.get(f"{c}_flag", False))]
        if flag_cols:
            def move_size(c):
                if c in GROWTH_COLUMNS:
                    return abs(latest[c])
                p = prior.get(c)
                return 0 if pd.isna(p) or p == 0 else abs((latest[c] - p) / abs(p))

            biggest = max(flag_cols, key=move_size)
            bullets.append(
                f"Biggest move in FY{int(latest['fy'])}: {COLUMN_LABELS.get(biggest, biggest)} went from "
                f"{format_value(biggest, prior.get(biggest))} to {format_value(biggest, latest[biggest])}."
            )

    return bullets
