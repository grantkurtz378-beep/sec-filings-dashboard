"""SEC Filings Financial Health Dashboard - Streamlit UI."""
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
}

st.set_page_config(page_title="SEC Filings Financial Health Dashboard", layout="wide")
st.title("SEC Filings Financial Health Dashboard")
st.caption(
    "Pulls XBRL data directly from SEC EDGAR's companyfacts API, computes standard "
    "ratios, and flags large year-over-year swings. No scraping, no API key."
)


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


def format_value(raw_col: str, val: float) -> str:
    if pd.isna(val):
        return "—"
    if raw_col in PERCENT_COLUMNS:
        return f"{val:.1%}"
    if raw_col in RATIO_COLUMNS:
        return f"{val:.2f}x"
    return str(val)


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

    missing = [c for c in ["current_ratio", "gross_margin"] if c not in flagged_df.columns]
    if missing:
        st.caption(
            f"Not shown: {', '.join(COLUMN_LABELS.get(c, c) for c in missing)} "
            "(SEC filers with an unclassified balance sheet, e.g. banks, don't tag these concepts)."
        )

    st.markdown("### Trends")
    chart_row1 = st.columns(2)
    with chart_row1[0]:
        st.caption("Revenue & Net Income ($)")
        st.line_chart(flagged_df.set_index("fy")[["Revenues", "NetIncomeLoss"]])
    with chart_row1[1]:
        margin_cols = [c for c in ["gross_margin", "net_margin", "roe", "roa"] if c in flagged_df.columns]
        st.caption("Margins & Returns")
        st.line_chart(flagged_df.set_index("fy")[margin_cols])

    chart_row2 = st.columns(2)
    with chart_row2[0]:
        st.caption("Current Ratio")
        if "current_ratio" in flagged_df.columns:
            st.line_chart(flagged_df.set_index("fy")[["current_ratio"]])
        else:
            st.caption("N/A for this company")
    with chart_row2[1]:
        st.caption("Debt / Equity")
        if "debt_to_equity" in flagged_df.columns:
            st.line_chart(flagged_df.set_index("fy")[["debt_to_equity"]])
        else:
            st.caption("N/A for this company")

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

    with st.expander("Raw underlying financials ($)"):
        raw_cols = [
            c
            for c in [
                "end", "Revenues", "CostOfRevenue", "GrossProfit", "NetIncomeLoss", "Assets",
                "Liabilities", "StockholdersEquity", "AssetsCurrent", "LiabilitiesCurrent",
                "NetCashProvidedByUsedInOperatingActivities",
            ]
            if c in flagged_df.columns
        ]
        st.dataframe(flagged_df.set_index("fy")[raw_cols], width="stretch")


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
    )
    if combined.empty:
        st.caption("No company in this comparison reports this metric.")
    else:
        st.line_chart(combined)

    st.markdown("### Most recent fiscal year, side by side")
    snapshot_cols = [c for c in FLAGGABLE_COLUMNS if any(c in df.columns for df in per_ticker.values())]
    rows = {}
    for t, df in per_ticker.items():
        latest = df.sort_values("fy").iloc[-1]
        rows[f"{t} (FY{int(latest['fy'])})"] = {
            COLUMN_LABELS[c]: format_value(c, latest[c]) if c in df.columns else "—" for c in snapshot_cols
        }
    st.dataframe(pd.DataFrame(rows).T, width="stretch")


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

st.caption(
    "Source: SEC EDGAR XBRL companyfacts API (data.sec.gov). Cached locally in data/sec_cache.db; "
    "use \"Force refresh\" in the sidebar to re-pull the latest filings."
)
