"""
Extract + Load step of the ELT pipeline.

Downloads daily OHLCV prices from Yahoo Finance (yfinance) for every ticker
listed in dbt_project/seeds/companies.csv and upserts them into raw.prices
in the DuckDB warehouse.

Key design points
- Backfill vs incremental: a ticker with no rows gets ~2 years of history;
  a ticker that already has data gets everything from (last loaded date - 7 days).
  The 7-day overlap re-pulls recent days so late corrections overwrite old values,
  and starting from the last loaded date (not "today") means a missed week of
  runs is filled in automatically.
- Idempotency: PRIMARY KEY (ticker, date) + INSERT OR REPLACE, so running the
  script twice never creates duplicate rows.
- Fault isolation: each ticker is retried up to 3 times; one failing ticker does
  not stop the others. If every ticker fails, the script exits with code 1 so the
  scheduled job shows as failed.

Usage:  python extract/extract_prices.py
"""
from __future__ import annotations

import csv
import logging
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("DUCKDB_PATH", ROOT / "data" / "warehouse.duckdb"))
SEED_PATH = ROOT / "dbt_project" / "seeds" / "companies.csv"

BACKFILL_DAYS = 730          # ~2 years of history on first load
OVERLAP_DAYS = 7             # re-pull window on incremental loads
MAX_RETRIES = 3
RETRY_WAIT_SECONDS = 5

COLUMNS = ["ticker", "date", "open", "high", "low", "close", "volume"]

DDL = """
CREATE SCHEMA IF NOT EXISTS raw;
CREATE TABLE IF NOT EXISTS raw.prices (
    ticker       VARCHAR   NOT NULL,
    date         DATE      NOT NULL,
    open         DOUBLE,
    high         DOUBLE,
    low          DOUBLE,
    close        DOUBLE,
    volume       BIGINT,
    ingested_at  TIMESTAMP NOT NULL,
    PRIMARY KEY (ticker, date)
);
"""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("extract")


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create raw schema/table if missing. Each statement's result is fully consumed
    (newer DuckDB versions require that before running the next statement)."""
    for stmt in filter(str.strip, DDL.split(";")):
        con.execute(stmt).fetchall()


def load_tickers(seed_path: Path = SEED_PATH) -> list[str]:
    """The dbt seed is the single source of truth for which companies we track."""
    with open(seed_path, newline="", encoding="utf-8") as f:
        return [row["ticker"].strip() for row in csv.DictReader(f) if row["ticker"].strip()]


def get_start_date(con: duckdb.DuckDBPyConnection, ticker: str) -> tuple[date, str]:
    """Return (start_date, mode) for one ticker: backfill if new, else incremental."""
    last = con.execute(
        "SELECT MAX(date) FROM raw.prices WHERE ticker = ?", [ticker]
    ).fetchall()[0][0]
    if last is None:
        return date.today() - timedelta(days=BACKFILL_DAYS), "backfill"
    return last - timedelta(days=OVERLAP_DAYS), "incremental"


def reshape(hist: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Turn a yfinance history() frame into our raw.prices column layout."""
    if hist is None or hist.empty:
        raise ValueError(f"No rows returned for {ticker}")
    df = hist.reset_index()
    df = df.rename(columns={
        "Date": "date", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume",
    })
    # Index is tz-aware (exchange time zone); .dt.date keeps the exchange's trading date.
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["ticker"] = ticker
    df["volume"] = df["volume"].fillna(0).astype("int64")
    df = df[COLUMNS].drop_duplicates(subset=["ticker", "date"], keep="last")
    return df


def fetch_ticker(ticker: str, start: date) -> pd.DataFrame:
    import yfinance as yf  # imported here so tests can run without network

    end = date.today() + timedelta(days=1)  # yfinance's end date is exclusive
    hist = yf.Ticker(ticker).history(
        start=start.isoformat(), end=end.isoformat(),
        interval="1d", auto_adjust=False, actions=False,
    )
    return reshape(hist, ticker)


def upsert(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Idempotent load: existing (ticker, date) rows are replaced, new ones inserted."""
    df = df.copy()
    df["ingested_at"] = pd.Timestamp.now(tz="UTC").tz_localize(None)
    con.register("incoming", df)
    con.execute("""
        INSERT OR REPLACE INTO raw.prices
            (ticker, date, open, high, low, close, volume, ingested_at)
        SELECT ticker, date, open, high, low, close, volume, ingested_at
        FROM incoming
    """).fetchall()
    con.unregister("incoming")
    return len(df)


def fetch_with_retry(ticker: str, start: date, fetch=fetch_ticker) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fetch(ticker, start)
        except Exception as exc:  # network errors, empty responses, rate limits...
            last_error = exc
            log.warning("%s attempt %d/%d failed: %s", ticker, attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT_SECONDS * attempt)
    raise RuntimeError(f"{ticker}: all {MAX_RETRIES} attempts failed") from last_error


def run(fetch=fetch_ticker) -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tickers = load_tickers()
    log.info("Warehouse: %s | %d tickers", DB_PATH, len(tickers))

    con = duckdb.connect(str(DB_PATH))
    try:
        init_schema(con)
        succeeded, failed, total_rows = [], [], 0

        for ticker in tickers:
            start, mode = get_start_date(con, ticker)
            try:
                df = fetch_with_retry(ticker, start, fetch)
                rows = upsert(con, df)
                total_rows += rows
                succeeded.append(ticker)
                log.info("%-14s %-11s from %s -> %4d rows", ticker, mode, start, rows)
            except Exception as exc:
                failed.append(ticker)
                log.error("%-14s FAILED: %s", ticker, exc)

        con.execute("CHECKPOINT").fetchall()  # flush the write-ahead log into the .duckdb file
        count = con.execute("SELECT COUNT(*) FROM raw.prices").fetchall()[0][0]
    finally:
        con.close()

    log.info("Done: %d ok, %d failed, %d rows upserted, raw.prices now has %d rows",
             len(succeeded), len(failed), total_rows, count)
    if failed:
        log.warning("Failed tickers: %s", ", ".join(failed))
    if not succeeded:
        log.error("Every ticker failed - exiting with error so the job is marked failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
