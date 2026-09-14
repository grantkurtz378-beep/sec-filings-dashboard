"""SEC EDGAR API client: ticker -> CIK lookup and companyfacts fetching.

Rate-limited to stay well under SEC's 10 req/sec cap, and cached locally
(ticker map as JSON, companyfacts as SQLite) so re-running the app doesn't
re-hit the API every time.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TICKERS_CACHE = DATA_DIR / "company_tickers.json"
DB_PATH = DATA_DIR / "sec_cache.db"

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# SEC requires a real contact in the User-Agent or it blocks the request outright.
USER_AGENT = "Grant Kurtz gdkurtz@asu.edu"
MIN_REQUEST_INTERVAL = 0.15  # ~6-7 req/sec, safely under SEC's 10 req/sec limit

_last_request_time = 0.0


class TickerNotFoundError(KeyError):
    pass


def _rate_limited_get(url: str) -> requests.Response:
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    _last_request_time = time.monotonic()
    resp.raise_for_status()
    return resp


def _init_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS company_facts_raw (
            cik TEXT PRIMARY KEY,
            ticker TEXT,
            fetched_at REAL NOT NULL,
            json_blob TEXT NOT NULL
        )
        """
    )
    return conn


def get_ticker_map(force_refresh: bool = False) -> dict[str, dict]:
    """{ticker: {"cik_str": int, "title": str, "ticker": str}}, downloaded once and cached to disk."""
    if TICKERS_CACHE.exists() and not force_refresh:
        with open(TICKERS_CACHE) as f:
            raw = json.load(f)
    else:
        raw = _rate_limited_get(TICKERS_URL).json()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(TICKERS_CACHE, "w") as f:
            json.dump(raw, f)
    return {entry["ticker"].upper(): entry for entry in raw.values()}


def resolve_cik(ticker: str) -> str:
    """Ticker -> zero-padded 10-digit CIK string."""
    entry = get_ticker_map().get(ticker.upper())
    if entry is None:
        raise TickerNotFoundError(f"Ticker '{ticker}' not found in SEC company_tickers.json")
    return str(entry["cik_str"]).zfill(10)


def get_company_facts(ticker: str, max_age_days: float = 1.0, force_refresh: bool = False) -> dict:
    """Raw companyfacts JSON for a ticker's CIK, served from the SQLite cache when fresh enough."""
    cik = resolve_cik(ticker)
    conn = _init_db()
    try:
        if not force_refresh:
            row = conn.execute(
                "SELECT fetched_at, json_blob FROM company_facts_raw WHERE cik = ?", (cik,)
            ).fetchone()
            if row is not None:
                fetched_at, json_blob = row
                if (time.time() - fetched_at) / 86400 < max_age_days:
                    return json.loads(json_blob)

        facts = _rate_limited_get(FACTS_URL.format(cik=cik)).json()
        conn.execute(
            "INSERT OR REPLACE INTO company_facts_raw (cik, ticker, fetched_at, json_blob) VALUES (?, ?, ?, ?)",
            (cik, ticker.upper(), time.time(), json.dumps(facts)),
        )
        conn.commit()
        return facts
    finally:
        conn.close()
