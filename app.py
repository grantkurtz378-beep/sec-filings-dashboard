"""SEC Filings Financial Health Dashboard - Streamlit UI."""
import hashlib
import html
from urllib.parse import quote

import altair as alt
import pandas as pd
import streamlit as st

from src.edgar_client import TickerNotFoundError, get_ticker_map
from src.flags import DEFAULT_THRESHOLD, FLAGGABLE_COLUMNS, compute_flags
from src.narrative import (
    COLUMN_LABELS,
    GROWTH_COLUMNS,
    HIGHER_IS_BETTER,
    PERCENT_COLUMNS,
    RATIO_COLUMNS,
    format_currency,
    format_value,
    generate_summary,
)
from src.pipeline import get_annual_fundamentals
from src.ratios import compute_ratios

PALETTE = ["#4F8EF7", "#F59E0B", "#34D399", "#F472B6", "#A78BFA", "#FB923C", "#22D3EE", "#94A3B8"]
FAVORABLE_COLOR = "background-color: rgba(52, 211, 153, 0.30)"
UNFAVORABLE_COLOR = "background-color: rgba(248, 113, 113, 0.32)"

st.set_page_config(page_title="SEC Filings Financial Health Dashboard", page_icon="\U0001F4CA", layout="wide")
alt.themes.enable("dark")


def company_title(ticker: str) -> str:
    try:
        return get_ticker_map().get(ticker, {}).get("title", ticker)
    except Exception:
        return ticker


def badge_color(ticker: str) -> str:
    """Deterministic color per ticker, so the same company always gets the same badge."""
    digest = hashlib.md5(ticker.encode()).hexdigest()
    return PALETTE[int(digest, 16) % len(PALETTE)]


def logo_url(ticker: str) -> str:
    return f"https://assets.parqet.com/logos/symbol/{quote(ticker)}"


def render_header(ticker: str, name: str) -> None:
    """Logo with graceful fallback: try a real logo image, fall back to a colored
    letter badge (rendered underneath) if it 404s - no network dependency required
    for the page to look finished, no broken-image icon if the lookup ever fails."""
    color = badge_color(ticker)
    # Both values are already validated against SEC's own ticker list by the time this
    # renders (load_ticker must have succeeded first), so real injection risk is nil -
    # escaped anyway since this is raw HTML and company names are free text SEC controls,
    # not this app.
    safe_ticker = html.escape(ticker)
    safe_name = html.escape(name)
    initials = html.escape(ticker[:2])
    st.markdown(
        f"""
        <div style="display:flex;align-items:center;gap:16px;margin:4px 0 8px 0;">
          <div style="position:relative;width:56px;height:56px;flex-shrink:0;">
            <div style="width:56px;height:56px;border-radius:12px;background:{color};
                        display:flex;align-items:center;justify-content:center;
                        font-weight:700;font-size:20px;color:#0B0E14;">{initials}</div>
            <img src="{html.escape(logo_url(ticker))}" referrerpolicy="no-referrer" onerror="this.style.display='none'"
                 style="position:absolute;inset:0;width:56px;height:56px;border-radius:12px;
                        background:#fff;object-fit:contain;padding:6px;box-sizing:border-box;" />
          </div>
          <div>
            <div style="font-size:26px;font-weight:700;line-height:1.25;">{safe_name}</div>
            <div style="font-size:14px;opacity:0.6;">{safe_ticker}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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

    def x_delta(col: str) -> str:
        if prior is None or col not in flagged_df.columns:
            return None
        if pd.isna(prior[col]) or pd.isna(latest[col]):
            return None
        return f"{(latest[col] - prior[col]):+.2f}x"

    cards = [
        ("Revenue", format_currency(latest.get("Revenues")), pct_delta("Revenues")),
        ("Net Income", format_currency(latest.get("NetIncomeLoss")), pct_delta("NetIncomeLoss")),
        (
            "Free Cash Flow",
            format_currency(latest.get("free_cash_flow")) if "free_cash_flow" in flagged_df.columns else "—",
            pct_delta("free_cash_flow"),
        ),
        ("Net Margin", format_value("net_margin", latest.get("net_margin")), pp_delta("net_margin")),
        ("ROE", format_value("roe", latest.get("roe")), pp_delta("roe")),
        ("Debt / Equity", format_value("debt_to_equity", latest.get("debt_to_equity")), x_delta("debt_to_equity")),
    ]
    st.caption(f"Fiscal year {int(latest['fy'])}")
    # 2-per-row rather than one wide row: st.metric's value font is a fixed size that
    # doesn't shrink to fit, so a wide row truncates a long value ("$416...") at moderate
    # window widths.
    kpi_cols = st.columns(2) + st.columns(2) + st.columns(2)
    for col, (label, value, delta) in zip(kpi_cols, cards):
        with col:
            with st.container(border=True):
                st.metric(label, value, delta)


def _sparkline_rows(sorted_df: pd.DataFrame, cols: list) -> list:
    """Metric/Latest/vs Prior Year/History rows for render_*_table - shared between the
    main ratio summary and the DuPont breakdown so both get the same sparkline treatment."""
    rows = []
    for raw_col in cols:
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
    return rows


def _render_sparkline_table(rows: list) -> None:
    if not rows:
        return
    st.dataframe(
        pd.DataFrame(rows),
        column_config={"History": st.column_config.LineChartColumn("History", width="small")},
        hide_index=True,
        width="stretch",
    )


def render_key_metrics_table(flagged_df: pd.DataFrame) -> None:
    """One row per ratio with its latest value, YoY change, and a full-history sparkline -
    a faster scan than the year-by-year table below it."""
    _render_sparkline_table(_sparkline_rows(flagged_df.sort_values("fy"), FLAGGABLE_COLUMNS))


def render_dupont_table(flagged_df: pd.DataFrame) -> None:
    """ROE = Net Margin x Asset Turnover x Equity Multiplier, laid out so a reader can see
    which lever is actually driving returns rather than taking ROE at face value."""
    sorted_df = flagged_df.sort_values("fy")
    dupont_cols = ["net_margin", "asset_turnover", "equity_multiplier", "roe"]
    if not any(c in sorted_df.columns for c in dupont_cols):
        return
    st.caption("ROE = Net Margin × Asset Turnover × Equity Multiplier")
    _render_sparkline_table(_sparkline_rows(sorted_df, dupont_cols))


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
    if "current_ratio" not in flagged_df.columns:
        st.caption(
            "Free cash flow is also suppressed for this filer even where the raw tags exist: "
            "a bank/broker-dealer's operating cash flow reflects swings in trading inventory, "
            "loans, and deposits rather than core-business cash generation, so it isn't the "
            "number FCF is meant to represent."
        )

    st.markdown("### At a Glance")
    st.caption(
        "Financial **health**, not valuation — this reads the statements, not the stock "
        "price. It says nothing about whether shares are cheap or expensive right now, "
        "which is a separate question this dashboard doesn't answer."
    )
    summary_bullets = generate_summary(flagged_df)
    if summary_bullets:
        with st.container(border=True):
            for bullet in summary_bullets:
                st.markdown(f"- {bullet}")
            st.caption(
                "Auto-generated directly from the ratios below — a starting point for your "
                "own research, not investment advice."
            )

    st.markdown("### Key Metrics")
    render_key_metrics_table(flagged_df)

    st.markdown("### ROE Decomposition (DuPont Analysis)")
    render_dupont_table(flagged_df)

    st.markdown("### Trends")
    chart_row1 = st.columns(2)
    with chart_row1[0]:
        with st.container(border=True):
            st.caption("Revenue, Net Income & FCF ($B)")
            line_chart(
                flagged_df, ["Revenues", "NetIncomeLoss", "free_cash_flow"], axis_format=",.0f", scale=1e9
            )
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
                "NetCashProvidedByUsedInOperatingActivities", "CapitalExpenditures",
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
    rows = []
    for t, df in per_ticker.items():
        latest = df.sort_values("fy").iloc[-1]
        row = {"Logo": logo_url(t), "Company": f"{t} (FY{int(latest['fy'])})"}
        row.update({COLUMN_LABELS[c]: format_value(c, latest[c]) if c in df.columns else "—" for c in snapshot_cols})
        rows.append(row)
    st.dataframe(
        pd.DataFrame(rows),
        column_config={"Logo": st.column_config.ImageColumn("", width="small")},
        hide_index=True,
        width="stretch",
    )


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

with st.expander("Methodology & data quality notes"):
    st.markdown(
        """
Every number on this page comes from SEC EDGAR's XBRL `companyfacts` API - the same
structured data SEC requires every 10-K and 10-Q filer to submit. No scraping, no
paid data vendor, no LLM in the numeric pipeline.

**Four real bugs this project found and fixed** (each has a dedicated regression test
in `tests/`, so a future change can't silently reintroduce them):

- **Comparative-year mislabeling.** A 10-K reports 2-3 years of comparative figures,
  and SEC's own `fy` field is stamped with the *filing's* fiscal year for all of
  them - so a company's FY2012 10-K relabels its FY2010 net income as `fy: 2012`.
  Grouping by that field naively lets a stale comparative overwrite the real
  current-year number. Fixed by keying on the fact's `end` date instead, which is
  unambiguous regardless of which filing reported it.
- **Revenue (and CapEx) tag drift.** Most companies moved off `Revenues` onto
  `RevenueFromContractWithCustomerExcludingAssessedTax` after adopting ASC 606
  (~2018); Goldman Sachs and Morgan Stanley report revenue net of interest expense
  under a different tag entirely, since interest is a cost of their core business.
  The same drift happens with the capex tag. A single fallback-chain mechanism
  handles both.
- **Missing subtotals.** Some filers (Walmart, for one) never tag `Liabilities` as
  its own line item - it's recoverable from `Assets = Liabilities + Equity`, but
  only if the parser knows to look for it.
- **A bug in the narrative layer, not the data layer.** Testing against AT&T (outside
  the original 13-ticker set) surfaced a sign bug in the DuPont "biggest mover"
  summary: dividing by a negative base year produced "net margin -583%" - backwards,
  since the underlying move was actually an improvement. Fixing the sign revealed a
  second issue underneath: percent change is unstable near a zero base even once the
  sign is right. The fix ranks by relative change but *displays* each factor in its
  own natural unit (percentage points / "x") instead of a percent-of-a-percent.

**A judgment call, not just a bug fix:** free cash flow is suppressed for banks and
broker-dealers even on the rare filer (Goldman, Citi) that happens to tag a capex
line - their operating cash flow reflects swings in trading inventory, loans, and
deposits, not core-business cash generation, so the subtraction is mechanically
possible but not economically meaningful. Same reasoning already applied to
current ratio and gross margin for financial institutions generally.

**Known simplifications:**
- ROE and ROA use ending-period balances rather than an average of beginning and
  ending balances - the standard simplification for a tool like this, but it will
  read slightly differently from a source that averages.
- A company's earliest 1-3 fiscal years in EDGAR's XBRL history can't always be
  given an unambiguous fiscal-year label (see the mislabeling bug above) and are
  dropped rather than guessed at.
- The "At a Glance" summary and DuPont breakdown are rule-based text generation
  over the ratios already on this page - not a language model, and not investment
  advice. Every line is a direct, checkable statement about a number shown
  elsewhere on the page.

Full write-up, real bug reproductions, and the test suite: see the
[GitHub repository](https://github.com/grantkurtz378-beep/sec-filings-dashboard).
"""
    )

st.caption(
    "Source: SEC EDGAR XBRL companyfacts API (data.sec.gov). Cached locally in data/sec_cache.db; "
    "use \"Force refresh\" in the sidebar to re-pull the latest filings."
)
