"""
Unit tests for the extract/load step. No network needed: yfinance is replaced
with a fake fetcher.  Run:  pytest -q
"""
import importlib.util
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("extract_prices", ROOT / "extract" / "extract_prices.py")
ep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ep)


def fake_history(n_days: int = 10) -> pd.DataFrame:
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n_days, freq="B", tz="Asia/Kolkata")
    idx.name = "Date"
    return pd.DataFrame(
        {"Open": 100.0, "High": 105.0, "Low": 95.0, "Close": 102.0, "Adj Close": 102.0, "Volume": 1000},
        index=idx,
    )


def test_reshape_columns():
    df = ep.reshape(fake_history(), "MARUTI.NS")
    assert list(df.columns) == ep.COLUMNS
    assert (df["ticker"] == "MARUTI.NS").all()
    assert isinstance(df["date"].iloc[0], date)


def test_reshape_empty_raises():
    with pytest.raises(ValueError):
        ep.reshape(pd.DataFrame(), "BAD")


def test_upsert_is_idempotent():
    con = duckdb.connect()
    ep.init_schema(con)
    df = ep.reshape(fake_history(), "TVSMOTOR.NS")
    ep.upsert(con, df)
    first = con.execute("SELECT COUNT(*) FROM raw.prices").fetchall()[0][0]
    ep.upsert(con, df)  # same data again
    second = con.execute("SELECT COUNT(*) FROM raw.prices").fetchall()[0][0]
    assert first == second == len(df)


def test_upsert_overwrites_corrections():
    con = duckdb.connect()
    ep.init_schema(con)
    df = ep.reshape(fake_history(), "HMC")
    ep.upsert(con, df)
    df.loc[df.index[-1], "close"] = 999.0  # a late correction from the source
    ep.upsert(con, df)
    assert con.execute("SELECT MAX(close) FROM raw.prices").fetchall()[0][0] == 999.0


def test_start_date_backfill_then_incremental():
    con = duckdb.connect()
    ep.init_schema(con)
    start, mode = ep.get_start_date(con, "TM")
    assert mode == "backfill"
    assert start == date.today() - timedelta(days=ep.BACKFILL_DAYS)

    ep.upsert(con, ep.reshape(fake_history(), "TM"))
    last = con.execute("SELECT MAX(date) FROM raw.prices").fetchall()[0][0]
    start, mode = ep.get_start_date(con, "TM")
    assert mode == "incremental"
    assert start == last - timedelta(days=ep.OVERLAP_DAYS)
