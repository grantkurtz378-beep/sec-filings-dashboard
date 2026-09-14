# SEC Filings Financial Health Dashboard

A tool that pulls real financial data straight from public companies' SEC filings,
computes the ratios analysts actually use, and flags which ones moved a lot year over
year. Runs as a local Streamlit dashboard.

Built a financial analysis dashboard parsing SEC EDGAR XBRL filings data for 13 public
companies, computing liquidity/profitability ratios and flagging significant YoY changes
(Python, pandas, Streamlit).

*(Screenshot: run `./run.sh`, open `http://localhost:8501`, and drop a screenshot of the
dashboard here before pushing — the sandboxed browser used to build this can't export
image files directly.)*

## What it does

- **Data pipeline**: given a ticker, resolves the CIK via SEC's `company_tickers.json`,
  pulls every XBRL-tagged fact from `data.sec.gov/api/xbrl/companyfacts/`, and parses it
  into one clean row per fiscal year. No scraping, no API key, no auth.
- **Ratio engine**: gross/net margin, current ratio, debt-to-equity, ROE, ROA, YoY revenue
  growth, YoY net income growth.
- **Flagging**: any ratio that swings more than a configurable threshold (default 20%)
  year over year gets highlighted in the table.
- **Dashboard**: ticker input, trend charts, a flagged ratio table, and a raw-financials
  view — plus a multi-company comparison mode (3-5 tickers side by side, e.g. a sector
  cohort like the big banks or big tech).

## Why this is harder than "call an API"

SEC's raw XBRL data has a few sharp edges that will silently corrupt your numbers if you
don't handle them:

- **Revenue tag drift.** Most companies moved off `Revenues` onto
  `RevenueFromContractWithCustomerExcludingAssessedTax` after adopting ASC 606 (~2018).
  Broker-dealers (Goldman Sachs, Morgan Stanley) report `RevenuesNetOfInterestExpense`
  instead, since interest is a cost of their core trading business. Pulling only the
  "obvious" tag truncates history for most companies in the starter list.
- **Comparative-year mislabeling.** A 10-K reports 2-3 years of comparative figures, and
  SEC's companyfacts API stamps ALL of them with the filing's own fiscal-year focus — so a
  company's FY2012 10-K relabels its FY2010 net income as "fy: 2012." Grouping facts by
  the `fy` field (the obvious approach) silently overwrites current-year numbers with
  stale prior-year values. The fix: key on the fact's `end` date (unambiguous) and derive
  the display fiscal year as the earliest `fy` ever attached to that date.
- **Missing subtotals.** Some filers (Walmart, for one) never tag `Liabilities` as its own
  line item — it's recoverable from `Assets - StockholdersEquity`, but only if you know to
  look for it.
- **Balance sheet structure varies by industry.** Banks and broker-dealers don't file a
  classified balance sheet, so `AssetsCurrent` / `LiabilitiesCurrent` (and therefore
  current ratio) and `CostOfRevenue` / `GrossProfit` (gross margin) genuinely don't exist
  for them. The dashboard detects this and shows "N/A" instead of guessing.

All of this was found by testing against real data (not assumed) and confirmed by
cross-checking output against known public figures for AAPL, JPM, and WMT.

## Setup

```bash
git clone <this-repo>
cd sec-filings-dashboard
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## Run it

```bash
./run.sh
```

Opens at `http://localhost:8501`. Enter any ticker (AAPL, JPM, WMT, ...) in the sidebar.

## Data source

- Ticker → CIK: `https://www.sec.gov/files/company_tickers.json`
- Company facts (all XBRL-tagged data ever filed): `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`

Requests are rate-limited to well under SEC's 10 req/sec cap and identify with a real
contact in the User-Agent header (SEC blocks requests without one). Responses are cached
locally in `data/sec_cache.db` (SQLite) so re-running the app doesn't re-hit the API; use
the "Force refresh" checkbox in the sidebar to bypass the cache.

## Project layout

```
app.py              Streamlit UI (single-company + comparison modes)
src/edgar_client.py  SEC API client: CIK lookup, rate limiting, SQLite caching
src/pipeline.py      Raw XBRL JSON -> one row per fiscal year
src/ratios.py        Ratio calculations
src/flags.py         YoY flagging logic
data/                SQLite cache + downloaded ticker map (gitignored)
```

## Starter tickers

- Banks: JPM, GS, MS, WFC, C
- Big tech: AAPL, MSFT, GOOGL, AMZN, META
- Retail: WMT, TGT, COST

## Known limitations

- A company's earliest 1-3 fiscal years in EDGAR's XBRL history (before it had its own
  correctly-labeled filing) can't always be given an unambiguous fiscal-year label and are
  dropped rather than mislabeled — the underlying `end` dates are always shown in the raw
  financials view regardless.
- A handful of companies have inconsistent fiscal-year tagging in their own SEC filings
  (Walmart skips a fiscal-year number in its own DEI metadata around 2014/2015). The
  underlying period (`end` date) and values are correct either way; only the fiscal-year
  label can look off for that one row.
- ROE/ROA use ending-period equity/assets rather than a period average, which is the
  standard simplification for a tool like this but will differ slightly from figures that
  average beginning and ending balances.

## Possible next steps

- AI-assisted read on risk factors: pull "Item 1A Risk Factors" from two consecutive 10-Ks
  via EDGAR's full-text search and have an LLM summarize what changed year over year.
