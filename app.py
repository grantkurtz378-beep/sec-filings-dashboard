"""SEC Filings Financial Health Dashboard - Streamlit UI."""
import hashlib
from urllib.parse import quote

import altair as alt
import pandas as pd
import streamlit as st

from src.edgar_client import TickerNotFoundError, get_ticker_map
from src.flags import DEFAULT_THRESHOLD, FLAGGABLE_COLUMNS, compute_flags
from src.pipeline import get_annual_fundamentals
from src.ratios import compute_ratios

PERCENT_COLUMNS = ["gross_margin", "net_margin", "roe", "roa", "revenue_yoy_growth", "net_income_yoy_growth"]
RATIO_COLUMNS = ["current_ratio", "debt_to_equity"]
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
    "Revenues": "Revenue",
    "NetIncomeLoss": "Net Income",
}
PALETTE = ["#4F8EF7", "#F59E0B", "#34D399", "#F472B6", "#A78BFA", "#FB923C", "#22D3EE", "#94A3B8"]
FAVORABLE_COLOR = "background-color: rgba(52, 211, 153, 0.30)"
UNFAVORABLE_COLOR = "background-color: rgba(248, 113, 113, 0.32)"

st.set_page_config(page_title="SEC Filings Financial Health Dashboard", page_icon="\U0001F4CA", layout="wide")
alt.themes.enable("dark")


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


def company_title(ticker: str) -> str:
    try:
        return get_ticker_map().get(ticker, {}).get("title", ticker)
    except Exception:
        return ticker


def badge_color(ticker: str) -> str:
    """Deterministic color per ticker, so the same company always gets the same badge."""
    digest = hashlib.md5(ticker.encode()).hexdigest()
    return PALETTE[int(digest, 16) % len(PALETTE)]


def render_header(ticker: str, name: str) -> None:
    """Logo with graceful fallback: try a real logo image, fall back to a colored
    letter badge (rendered underneath) if it 404s - no network dependency required
    for the page to look finished, no broken-image icon if the lookup ever fails."""
    color = badge_color(ticker)
    initials = ticker[:2]
    logo_url = f"https://assets.parqet.com/logos/symbol/{quote(ticker)}"
    st.markdown(
        f"""
        <div style="display:flex;align-items:center;gap:16px;margin:4px 0 8px 0;">
          <div style="position:relative;width:56px;height:56px;flex-shrink:0;">
            <div style="width:56px;height:56px;border-radius:12px;background:{color};
                        display:flex;align-items:center;justify-content:center;
                        font-weight:700;font-size:20px;color:#0B0E14;">{initials}</div>
            <img src="{logo_url}" referrerpolicy="no-referrer" onerror="this.style.display='none'"
                 style="position:absolute;inset:0;width:56px;height:56px;border-radius:12px;
                        background:#fff;object-fit:contain;padding:6px;box-sizing:border-box;" />
          </div>
          <div>
            <div style="font-size:26px;font-weight:700;line-height:1.25;">{name}</div>
            <div style="font-size:14px;opacity:0.6;">{ticker}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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


def load_ticker(ticker: str, threshold: float, force_refresh: bool):
    """Fetch + compute ratios/flags for one ticker. Returns (df, error_message)."""
    try:
        fundamentals = get_annual_fundamentals(ticker, force_refresh=force_refresh)
    except TickerNotFoundError:
        return None, f"'{ticker}' isn't in SEC's company_tickers.json. Check the ticker symbol."
    except Exception as e:
        return None, f"Failed to fetch data for {ticker}: {e}"
    if fundamentals.empty:
        return None, f"No annual XBRL data found for {ticker}."
    return compute_flags(compute_ratios(fundamentals), threshold=threshold), None


def line_chart(df: pd.DataFrame, cols: list, axis_format: str = None, axis_title: str = None, scale: float = 1) -> None:
    """One Altair line chart, multiple metrics as separate colored lines with readable labels.

    `scale` pre-divides values before charting (e.g. 1e9 to show dollars in billions) - this
    sidesteps d3-format's SI-prefix convention, which uses "G" for giga/billion, not "B".
    """
    cols = [c for c in cols if c in df.columns]
    if not cols:
        st.caption("N/A for this company")
        return
    long_df = df[["fy"] + cols].melt("fy", var_name="metric", value_name="value")
    long_df["value"] = long_df["value"] / scale
    long_df["metric"] = long_df["metric"].map(lambda c: COLUMN_LABELS.get(c, c))
    chart = (
        alt.Chart(long_df)
        .mark_line(point=True, strokeWidth=2.5)
        .encode(
            x=alt.X("fy:O", title=None),
            y=alt.Y("value:Q", title=axis_title, axis=alt.Axis(format=axis_format) if axis_format else alt.Axis()),
            color=alt.Color("metric:N", title=None, scale=alt.Scale(range=PALETTE)),
            tooltip=[
                alt.Tooltip("fy:O", title="Fiscal Year"),
                alt.Tooltip("metric:N", title="Metric"),
                alt.Tooltip("value:Q", title="Value", format=axis_format or ",.2f"),
            ],
        )
        .properties(height=260)
        .configure_legend(orient="bottom", direction="horizontal", labelLimit=200)
        .configure_axis(grid=True, gridOpacity=0.15)
        .configure_view(strokeWidth=0)
    )
    st.altair_chart(chart, use_container_width=True)


def render_kpi_row(flagged_df: pd.DataFrame) -> None:
    sorted_df = flagged_df.sort_values("fy")
    latest = sorted_df.iloc[-1]
    prior = sorted_df.iloc[-2] if len(sorted_df) > 1 else None

    def pct_delta(col: str) -> str:
        if prior is None or col not in flagged_df.columns:
            return None
        prev_val = prior[col]
        if pd.isna(prev_val) or prev_val == 0 or pd.isna(latest[col]):
            return None
        return f"{(latest[col] / prev_val - 1):+.1%}"

    def pp_delta(col: str) -> str:
        if prior is None or col not in flagged_df.columns:
            return None
        if pd.isna(prior[col]) or pd.isna(latest[col]):
            return None
        return f"{(latest[col] - prior[col]) * 100:+.1f} pp"

    cards = [
        ("Revenue", format_currency(latest.get("Revenues")), pct_delta("Revenues")),
        ("Net Income", format_currency(latest.get("NetIncomeLoss")), pct_delta("NetIncomeLoss")),
        ("Net Margin", format_value("net_margin", latest.get("net_margin")), pp_delta("net_margin")),
        ("ROE", format_value("roe", latest.get("roe")), pp_delta("roe")),
    ]
    st.caption(f"Fiscal year {int(latest['fy'])}")
    # 2x2 rather than a single row of 4: st.metric's value font is a fixed size that
    # doesn't shrink to fit, so a narrower window truncates a 4-wide row ("$416...").
    kpi_cols = st.columns(2) + st.columns(2)
    for col, (label, value, delta) in zip(kpi_cols, cards):
        with col:
            with st.container(border=True):
                st.metric(label, value, delta)


def render_key_metrics_table(flagged_df: pd.DataFrame) -> None:
    """One row per ratio with its latest value, YoY change, and a full-history sparkline -
    a faster scan than the year-by-year table below it."""
    sorted_df = flagged_df.sort_values("fy")
    rows = []
    for raw_col in FLAGGABLE_COLUMNS:
        if raw_col not in sorted_df.columns:
            continue
        series = sorted_df[raw_col]
        latest_val, prior_val = series.iloc[-1], (series.iloc[-2] if len(series) > 1 else None)
        delta = "—"
        if prior_val is not None and pd.notna(prior_val) and pd.notna(latest_val):
            if raw_col in RATIO_COLUMNS:
                delta = f"{(latest_val - prior_val):+.2f}x"
            else:
                delta = f"{(latest_val - prior_val) * 100:+.1f} pp"
        rows.append(
            {
                "Metric": COLUMN_LABELS[raw_col],
                "Latest": format_value(raw_col, latest_val),
                "vs Prior Year": delta,
                "History": series.dropna().tolist(),
            }
        )
    if not rows:
        return
    st.dataframe(
        pd.DataFrame(rows),
        column_config={"History": st.column_config.LineChartColumn("History", width="small")},
        hide_index=True,
        width="stretch",
    )


def render_single_company(threshold_pct: float, force_refresh: bool) -> None:
    ticker = st.text_input("Ticker", value="AAPL").strip().upper()
    if not ticker:
        st.info("Enter a ticker to get started.")
        return

    with st.spinner(f"Fetching {ticker} filings from SEC EDGAR..."):
        flagged_df, error = load_ticker(ticker, threshold_pct / 100, force_refresh)
    if error:
        st.error(error)
        return

    name = company_title(ticker)
    render_header(ticker, name)
    st.caption(f"{len(flagged_df)} fiscal years covered: {flagged_df['fy'].min()}–{flagged_df['fy'].max()}")

    render_kpi_row(flagged_df)

    missing = [c for c in ["current_ratio", "gross_margin"] if c not in flagged_df.columns]
    if missing:
        st.caption(
            f"Not shown: {', '.join(COLUMN_LABELS.get(c, c) for c in missing)} "
            "(SEC filers with an unclassified balance sheet, e.g. banks, don't tag these concepts)."
        )

    st.markdown("### At a Glance")
    summary_bullets = generate_summary(flagged_df)
    if summary_bullets:
        with st.container(border=True):
            for bullet in summary_bullets:
                st.markdown(f"- {bullet}")
            st.caption("Auto-generated directly from the ratios below — a starting point, not advice.")

    st.markdown("### Key Metrics")
    render_key_metrics_table(flagged_df)

    st.markdown("### Trends")
    chart_row1 = st.columns(2)
    with chart_row1[0]:
        with st.container(border=True):
            st.caption("Revenue & Net Income ($B)")
            line_chart(flagged_df, ["Revenues", "NetIncomeLoss"], axis_format=",.0f", scale=1e9)
    with chart_row1[1]:
        with st.container(border=True):
            st.caption("Margins & Returns")
            margin_cols = [c for c in ["gross_margin", "net_margin", "roe", "roa"] if c in flagged_df.columns]
            line_chart(flagged_df, margin_cols, axis_format=".0%")

    chart_row2 = st.columns(2)
    with chart_row2[0]:
        with st.container(border=True):
            st.caption("Current Ratio")
            line_chart(flagged_df, ["current_ratio"], axis_format=",.2f")
    with chart_row2[1]:
        with st.container(border=True):
            st.caption("Debt / Equity")
            line_chart(flagged_df, ["debt_to_equity"], axis_format=",.2f")

    st.markdown("### Ratio Table")
    st.caption(
        f"Cells that moved more than {threshold_pct}% year-over-year are highlighted — "
        "green for a move in the healthy direction, red for the other way."
    )

    display_cols = [c for c in FLAGGABLE_COLUMNS if c in flagged_df.columns]
    flags_indexed = flagged_df.set_index("fy")

    table = pd.DataFrame(index=flags_indexed.index)
    for raw_col in display_cols:
        table[COLUMN_LABELS[raw_col]] = flags_indexed[raw_col].map(lambda v, c=raw_col: format_value(c, v))

    def highlight_flags(data: pd.DataFrame) -> pd.DataFrame:
        """Green for a flagged move in the healthy direction, red for the other way -
        a plain "any big move is red" scheme makes good news look like a warning."""
        styles = pd.DataFrame("", index=data.index, columns=data.columns)
        for raw_col, label in COLUMN_LABELS.items():
            flag_col = f"{raw_col}_flag"
            if label not in data.columns or flag_col not in flags_indexed.columns:
                continue
            flagged = flags_indexed[flag_col].reindex(data.index).fillna(False)
            values = flags_indexed[raw_col].reindex(data.index)
            if raw_col in GROWTH_COLUMNS:
                favorable = values > 0
            elif raw_col in HIGHER_IS_BETTER:
                increased = values > values.shift(1)
                favorable = increased if HIGHER_IS_BETTER[raw_col] else ~increased
            else:
                favorable = pd.Series(False, index=values.index)
            colors = favorable.map({True: FAVORABLE_COLOR, False: UNFAVORABLE_COLOR})
            styles.loc[flagged, label] = colors[flagged]
        return styles

    st.dataframe(table.style.apply(highlight_flags, axis=None), width="stretch")

    with st.expander("Raw underlying financials"):
        raw_cols = [
            c
            for c in [
                "Revenues", "CostOfRevenue", "GrossProfit", "NetIncomeLoss", "Assets",
                "Liabilities", "StockholdersEquity", "AssetsCurrent", "LiabilitiesCurrent",
                "NetCashProvidedByUsedInOperatingActivities",
            ]
            if c in flagged_df.columns
        ]
        raw_indexed = flagged_df.set_index("fy")
        raw_table = pd.DataFrame(index=raw_indexed.index)
        raw_table["Period End"] = raw_indexed["end"]
        for c in raw_cols:
            raw_table[COLUMN_LABELS.get(c, c)] = raw_indexed[c].map(format_currency)
        st.dataframe(raw_table, width="stretch")


def render_comparison(threshold_pct: float, force_refresh: bool) -> None:
    raw_input = st.text_input("Tickers (comma-separated, 3-5)", value="JPM,GS,MS,WFC,C")
    tickers = [t.strip().upper() for t in raw_input.split(",") if t.strip()]
    if not (2 <= len(tickers) <= 8):
        st.info("Enter 3-5 tickers separated by commas (2-8 supported).")
        return

    per_ticker = {}
    for t in tickers:
        with st.spinner(f"Fetching {t}..."):
            flagged_df, error = load_ticker(t, threshold_pct / 100, force_refresh)
        if error:
            st.warning(error)
            continue
        per_ticker[t] = flagged_df

    if not per_ticker:
        st.error("None of the requested tickers returned data.")
        return

    metric_options = [c for c in FLAGGABLE_COLUMNS if any(c in df.columns for df in per_ticker.values())]
    default_index = metric_options.index("net_margin") if "net_margin" in metric_options else 0
    metric = st.selectbox(
        "Metric to compare", metric_options, index=default_index, format_func=lambda c: COLUMN_LABELS.get(c, c)
    )

    st.markdown(f"### {COLUMN_LABELS.get(metric, metric)} over time")
    combined = pd.DataFrame(
        {t: df.set_index("fy")[metric] for t, df in per_ticker.items() if metric in df.columns}
    ).reset_index()

    if len(combined.columns) <= 1:
        st.caption("No company in this comparison reports this metric.")
    else:
        axis_fmt = ".0%" if metric in PERCENT_COLUMNS else ",.2f"
        with st.container(border=True):
            long_df = combined.melt("fy", var_name="ticker", value_name="value").dropna(subset=["value"])
            chart = (
                alt.Chart(long_df)
                .mark_line(point=True, strokeWidth=2.5)
                .encode(
                    x=alt.X("fy:O", title=None),
                    y=alt.Y("value:Q", title=None, axis=alt.Axis(format=axis_fmt)),
                    color=alt.Color("ticker:N", title=None, scale=alt.Scale(range=PALETTE)),
                    tooltip=[
                        alt.Tooltip("fy:O", title="Fiscal Year"),
                        alt.Tooltip("ticker:N", title="Ticker"),
                        alt.Tooltip("value:Q", title="Value", format=axis_fmt),
                    ],
                )
                .properties(height=320)
                .configure_legend(orient="bottom", direction="horizontal")
                .configure_axis(grid=True, gridOpacity=0.15)
                .configure_view(strokeWidth=0)
            )
            st.altair_chart(chart, use_container_width=True)

    st.markdown("### Most recent fiscal year, side by side")
    snapshot_cols = [c for c in FLAGGABLE_COLUMNS if any(c in df.columns for df in per_ticker.values())]
    rows = {}
    for t, df in per_ticker.items():
        latest = df.sort_values("fy").iloc[-1]
        rows[f"{t} (FY{int(latest['fy'])})"] = {
            COLUMN_LABELS[c]: format_value(c, latest[c]) if c in df.columns else "—" for c in snapshot_cols
        }
    st.dataframe(pd.DataFrame(rows).T, width="stretch")


st.title("SEC Filings Financial Health Dashboard")
st.caption(
    "Pulls XBRL data directly from SEC EDGAR's companyfacts API, computes standard "
    "ratios, and flags large year-over-year swings. No scraping, no API key."
)

with st.sidebar:
    mode = st.radio("Mode", ["Single Company", "Compare Companies"])
    threshold_pct = st.slider("Flag threshold (YoY % change)", 5, 100, int(DEFAULT_THRESHOLD * 100), step=5)
    force_refresh = st.checkbox("Force refresh from SEC (ignore local cache)", value=False)
    st.markdown("---")
    st.caption("Starter tickers")
    st.caption("Banks: JPM, GS, MS, WFC, C")
    st.caption("Big tech: AAPL, MSFT, GOOGL, AMZN, META")
    st.caption("Retail: WMT, TGT, COST")

if mode == "Single Company":
    render_single_company(threshold_pct, force_refresh)
else:
    render_comparison(threshold_pct, force_refresh)

st.divider()
st.caption(
    "Source: SEC EDGAR XBRL companyfacts API (data.sec.gov). Cached locally in data/sec_cache.db; "
    "use \"Force refresh\" in the sidebar to re-pull the latest filings."
)
