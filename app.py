"""SEC Filings Financial Health Dashboard - Streamlit UI."""
import altair as alt
import pandas as pd
import streamlit as st

from src.edgar_client import TickerNotFoundError, get_ticker_map
from src.flags import DEFAULT_THRESHOLD, FLAGGABLE_COLUMNS, compute_flags
from src.pipeline import get_annual_fundamentals
from src.ratios import compute_ratios

PERCENT_COLUMNS = ["gross_margin", "net_margin", "roe", "roa", "revenue_yoy_growth", "net_income_yoy_growth"]
RATIO_COLUMNS = ["current_ratio", "debt_to_equity"]
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
    kpi_cols = st.columns(len(cards))
    for col, (label, value, delta) in zip(kpi_cols, cards):
        with col:
            with st.container(border=True):
                st.metric(f"{label} (FY{int(latest['fy'])})", value, delta)


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

    st.subheader(f"{company_title(ticker)} ({ticker})")
    st.caption(f"{len(flagged_df)} fiscal years covered: {flagged_df['fy'].min()}–{flagged_df['fy'].max()}")

    render_kpi_row(flagged_df)

    missing = [c for c in ["current_ratio", "gross_margin"] if c not in flagged_df.columns]
    if missing:
        st.caption(
            f"Not shown: {', '.join(COLUMN_LABELS.get(c, c) for c in missing)} "
            "(SEC filers with an unclassified balance sheet, e.g. banks, don't tag these concepts)."
        )

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
    st.caption(f"Rows with a metric that moved more than {threshold_pct}% year-over-year are highlighted.")

    display_cols = [c for c in FLAGGABLE_COLUMNS if c in flagged_df.columns]
    flags_indexed = flagged_df.set_index("fy")

    table = pd.DataFrame(index=flags_indexed.index)
    for raw_col in display_cols:
        table[COLUMN_LABELS[raw_col]] = flags_indexed[raw_col].map(lambda v, c=raw_col: format_value(c, v))

    def highlight_flags(data: pd.DataFrame) -> pd.DataFrame:
        styles = pd.DataFrame("", index=data.index, columns=data.columns)
        for raw_col, label in COLUMN_LABELS.items():
            flag_col = f"{raw_col}_flag"
            if label not in data.columns or flag_col not in flags_indexed.columns:
                continue
            mask = flags_indexed[flag_col].reindex(data.index).fillna(False)
            styles.loc[mask, label] = "background-color: rgba(255, 82, 82, 0.35)"
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
