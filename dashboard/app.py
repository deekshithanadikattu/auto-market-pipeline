"""
Streamlit dashboard for the auto-industry market pipeline.
Reads the analytics marts from DuckDB in read-only mode.

Run locally:  streamlit run dashboard/app.py
"""
from datetime import timedelta
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "warehouse.duckdb"

st.set_page_config(page_title="Auto Industry Market Pipeline", layout="wide")


@st.cache_data(ttl=3600)
def query(sql: str) -> pd.DataFrame:
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        return con.execute(sql).df()


if not DB_PATH.exists():
    st.error(f"Warehouse not found at {DB_PATH}. Run `python run_pipeline.py` first.")
    st.stop()

companies = query("SELECT * FROM analytics.dim_company ORDER BY segment, company_name")
daily = query("""
    SELECT f.*, c.company_name, c.segment, c.currency
    FROM analytics.fct_daily_prices f
    JOIN analytics.dim_company c USING (ticker)
    ORDER BY ticker, trade_date
""")
monthly = query("SELECT * FROM analytics.mart_monthly_performance ORDER BY month_start")
last_updated = query("SELECT MAX(ingested_at) AS ts FROM raw.prices")["ts"].iloc[0]

daily["trade_date"] = pd.to_datetime(daily["trade_date"])
monthly["month_start"] = pd.to_datetime(monthly["month_start"])

# ---------------- Header ----------------
st.title("🚗 Auto Industry Market Pipeline")
st.caption(
    f"Daily prices for {len(companies)} automotive companies · "
    f"Data last updated: **{pd.Timestamp(last_updated):%Y-%m-%d %H:%M} UTC** · "
    "Python → DuckDB → dbt → Streamlit, scheduled with GitHub Actions"
)

# ---------------- Sidebar filters ----------------
st.sidebar.header("Filters")
all_names = companies["company_name"].tolist()
selected = st.sidebar.multiselect("Companies", all_names, default=all_names)
min_d, max_d = daily["trade_date"].min().date(), daily["trade_date"].max().date()
default_start = max(min_d, max_d - timedelta(days=365))
date_range = st.sidebar.date_input("Date range", (default_start, max_d), min_value=min_d, max_value=max_d)
st.sidebar.caption("Prices are in each listing's local currency (INR / USD / JPY), "
                   "so compare companies using returns, not raw prices.")

if not selected:
    st.warning("Pick at least one company.")
    st.stop()
if not isinstance(date_range, (tuple, list)) or len(date_range) != 2:
    st.info("Select an end date to finish the range.")
    st.stop()

start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
d = daily[daily["company_name"].isin(selected) & daily["trade_date"].between(start, end)].copy()
m = monthly[monthly["company_name"].isin(selected)].copy()

# ---------------- KPI row ----------------
latest_month = m["month_start"].max()
mtd = m[m["month_start"] == latest_month].dropna(subset=["monthly_return"])
c1, c2, c3, c4 = st.columns(4)
c1.metric("Companies", len(selected))
c2.metric("Trading days in range", d["trade_date"].nunique())
if not mtd.empty:
    best, worst = mtd.loc[mtd["monthly_return"].idxmax()], mtd.loc[mtd["monthly_return"].idxmin()]
    c3.metric(f"Best · {latest_month:%b %Y} (MTD)", best["company_name"], f"{best['monthly_return']:.1%}")
    c4.metric(f"Worst · {latest_month:%b %Y} (MTD)", worst["company_name"], f"{worst['monthly_return']:.1%}")

# ---------------- Price + moving average ----------------
st.subheader("Close price and 20-day moving average")
company = st.selectbox("Company", selected)
one = d[d["company_name"] == company]
fig = go.Figure()
fig.add_trace(go.Scatter(x=one["trade_date"], y=one["close_price"], name="Close", line=dict(width=1.6)))
fig.add_trace(go.Scatter(x=one["trade_date"], y=one["ma_20d"], name="20-day MA", line=dict(width=2, dash="dash")))
currency = one["currency"].iloc[0] if not one.empty else ""
fig.update_layout(height=380, margin=dict(t=10, b=10), yaxis_title=f"Price ({currency})",
                  legend=dict(orientation="h", y=1.08))
st.plotly_chart(fig, width="stretch")

# ---------------- Relative performance ----------------
st.subheader("Relative performance (indexed to 100 at start of range)")
d = d.sort_values(["company_name", "trade_date"])
d["indexed"] = d["close_price"] / d.groupby("company_name")["close_price"].transform("first") * 100
fig = px.line(d, x="trade_date", y="indexed", color="company_name",
              labels={"indexed": "Index (start = 100)", "trade_date": "", "company_name": ""})
fig.update_layout(height=420, margin=dict(t=10, b=10))
st.plotly_chart(fig, width="stretch")

# ---------------- Monthly returns ----------------
st.subheader("Monthly returns by company")
m_in_range = m[m["month_start"].between(start.to_period("M").to_timestamp(), end)].dropna(subset=["monthly_return"])
m_in_range["month"] = m_in_range["month_start"].dt.strftime("%Y-%m")
fig = px.bar(m_in_range, x="month", y="monthly_return", color="company_name", barmode="group",
             labels={"monthly_return": "Monthly return", "month": "", "company_name": ""})
fig.update_layout(height=420, margin=dict(t=10, b=10), yaxis_tickformat=".0%")
st.plotly_chart(fig, width="stretch")

# ---------------- Tables ----------------
left, right = st.columns(2)
with left:
    st.subheader(f"Leaderboard · {latest_month:%B %Y} (month to date)")
    table = (mtd[["company_name", "segment", "monthly_return", "volatility", "trading_days"]]
             .sort_values("monthly_return", ascending=False)
             .rename(columns={"company_name": "Company", "segment": "Segment",
                              "monthly_return": "Return", "volatility": "Daily volatility",
                              "trading_days": "Days"}))
    st.dataframe(table.style.format({"Return": "{:.2%}", "Daily volatility": "{:.2%}"}),
                 hide_index=True, width="stretch")
with right:
    st.subheader("Two-wheeler vs four-wheeler")
    seg = (m_in_range.groupby("segment")
           .agg(avg_monthly_return=("monthly_return", "mean"), avg_volatility=("volatility", "mean"),
                companies=("ticker", "nunique"))
           .reset_index()
           .rename(columns={"segment": "Segment", "avg_monthly_return": "Avg monthly return",
                            "avg_volatility": "Avg daily volatility", "companies": "Companies"}))
    st.dataframe(seg.style.format({"Avg monthly return": "{:.2%}", "Avg daily volatility": "{:.2%}"}),
                 hide_index=True, width="stretch")

st.caption("Source: Yahoo Finance via yfinance. For educational purposes, not investment advice.")
