# Step-by-step build guide

All the code is already in this folder and tested. This guide walks you through setting it up, running each piece, **checking it works**, putting it on GitHub, scheduling it, and deploying the dashboard. Do the steps in order. Every step ends with a ✅ check, so don't move on until the check passes.

Commands are shown for **Mac/Linux**. Windows (PowerShell) differences are marked 🪟.

---

## What's in the folder

```
auto-market-pipeline/
├── extract/extract_prices.py          # Step 1: pulls prices, upserts into raw.prices
├── dbt_project/
│   ├── dbt_project.yml                # dbt config (schemas, materializations)
│   ├── profiles.yml                   # tells dbt where the DuckDB file is
│   ├── seeds/companies.csv            # your company list (single source of truth)
│   ├── macros/generate_schema_name.sql# makes schemas "staging"/"analytics" (not main_analytics)
│   ├── macros/test_positive_value.sql # custom test: close > 0
│   ├── models/staging/                # sources.yml, stg_prices.sql, schema.yml (tests)
│   ├── models/marts/                  # dim_company, fct_daily_prices, mart_monthly_performance + tests
│   └── tests/assert_high_not_below_low.sql   # singular test
├── dashboard/app.py                   # Step 5: Streamlit app
├── tests/test_extract.py              # pytest unit tests (idempotency etc.)
├── .github/workflows/pipeline.yml     # Step 4: weekday schedule
├── run_pipeline.py                    # Step 3: runs everything in order
├── requirements.txt
├── .gitignore
├── README.md                          # project front page (fill in your links)
└── data/                              # warehouse.duckdb is created here
```

---

## Step 0 — Setup (≈ 30–45 min)

**0.1 Install the basics** (skip what you have)
- Python 3.11 or 3.12 → python.org (🪟 tick "Add Python to PATH" in the installer)
- Git → git-scm.com
- VS Code (recommended)
- A GitHub account

Check: `python --version` (🪟 or `py --version`) and `git --version` both print versions.

**0.2 Create the GitHub repo**
1. github.com → **New repository** → name `auto-market-pipeline` → **Public** → *don't* add a README (you already have one) → **Create**.

**0.3 Put the code in place**
1. Unzip `auto-market-pipeline.zip` wherever you keep projects.
2. Open a terminal in that folder:
   ```bash
   cd path/to/auto-market-pipeline
   git init
   git branch -M main
   git remote add origin https://github.com/<your-username>/auto-market-pipeline.git
   ```

**0.4 Virtual environment + packages**
```bash
python -m venv .venv
source .venv/bin/activate            # 🪟 .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```
🪟 If activation is blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then try again.

> Why `requirements.txt` has version ranges instead of `pip freeze`: a freeze from Windows/Mac can include packages that won't install on GitHub's Linux runner. Ranges install cleanly everywhere.

✅ **Check:** `dbt --version` shows dbt-core and the duckdb plugin. Your prompt shows `(.venv)`.

---

## Step 1 — Extract & load (≈ 1–2 h, mostly reading/understanding)

**1.1 Check the tickers.** Open `dbt_project/seeds/companies.csv`. Search each ticker on finance.yahoo.com to confirm it exists. To add or remove a company, edit **only this file** — the extract script reads its ticker list from here.

**1.2 Read `extract/extract_prices.py` top to bottom.** What each function does:

| Function | Job | Interview concept |
|---|---|---|
| `init_schema` | creates `raw.prices` with `PRIMARY KEY (ticker, date)` | schema design |
| `get_start_date` | new ticker → 730 days back (backfill); existing → last loaded date − 7 days (incremental) | backfill vs incremental, overlap window |
| `fetch_ticker` + `reshape` | calls yfinance, renames columns to `ticker, date, open, high, low, close, volume` | the only source-specific code |
| `upsert` | `INSERT OR REPLACE` + `ingested_at` timestamp | idempotency |
| `fetch_with_retry` | 3 attempts with growing waits | fault tolerance |
| `run` | loops tickers, logs rows per ticker, exits `1` if all failed | failure signalling to the scheduler |

**1.3 Run it**
```bash
python extract/extract_prices.py
```
You should see one log line per ticker: `backfill from <date> -> ~500 rows`.

**1.4 Run it a second time** — now each line says `incremental` with ~5–6 rows.

**1.5 Prove idempotency**
```bash
python -c "import duckdb; print(duckdb.connect('data/warehouse.duckdb', read_only=True).sql('SELECT COUNT(*) FROM raw.prices').fetchall())"
```
Run the extract again, then this count again.

✅ **Check:** the count is identical both times (≈ 4,500 for 9 tickers × 2 years). Also run `pytest -q` → `5 passed`.

**If yfinance fails** (`rate limited`, empty data, 429):
- `pip install --upgrade yfinance` (Yahoo changes things often, and new versions fix it)
- wait 15–30 min and retry
- last resort: rewrite only `fetch_ticker` for Stooq or Alpha Vantage, keeping the same output columns. Nothing else changes; that's the point of isolating the source.

---

## Step 2 — Transform with dbt (≈ 2 h)

The dbt project is already set up (you don't need `dbt init`). Here's what each piece does:

- `profiles.yml` → connection: DuckDB file at `../data/warehouse.duckdb` (or `$DUCKDB_PATH`)
- `models/staging/sources.yml` → declares `raw.prices` so models can use `{{ source('raw','prices') }}`
- `seeds/companies.csv` → loaded as a table by `dbt seed`
- **Staging** `stg_prices` (view): renames to `trade_date`, `close_price` etc., casts types, drops null closes, dedupes, builds `price_id = ticker_YYYY-MM-DD`
- **Marts** (tables):
  - `dim_company` — the dimension
  - `fct_daily_prices` — the fact. `LAG(close_price)` → `daily_return`; `AVG … ROWS BETWEEN 19 PRECEDING` → `ma_20d` (null until 20 days exist); `MAX(high) … 251 PRECEDING` → `high_52w`
  - `mart_monthly_performance` — last close per month (`arg_max`), monthly return vs previous month, average volume, volatility (`stddev_samp(daily_return)`)

**2.1 Run dbt by hand**
```bash
cd dbt_project
dbt debug --profiles-dir .        # "All checks passed!"
dbt build --profiles-dir .        # seeds, models and tests in dependency order
cd ..
```
✅ **Check:** `Done. PASS=23 WARN=0 ERROR=0` (1 seed + 4 models + 18 tests).

**2.2 Look at the results**
```bash
python -c "import duckdb; c=duckdb.connect('data/warehouse.duckdb', read_only=True); print(c.sql('SELECT company_name, month_start, round(monthly_return*100,2) AS ret_pct FROM analytics.mart_monthly_performance ORDER BY month_start DESC, ret_pct DESC LIMIT 9'))"
```

**2.3 See a test catch bad data** (great for understanding, takes 2 min)
```bash
python -c "import duckdb; c=duckdb.connect('data/warehouse.duckdb'); c.execute(\"INSERT INTO raw.prices VALUES ('FAKE.NS','2026-01-05',1,1,1,-5,10,now()::timestamp)\").fetchall()"
cd dbt_project && dbt build --profiles-dir . ; cd ..
```
You'll see `FAIL 1 positive_value_stg_prices_close_price` and `SKIP=10`: dbt refused to build anything downstream. Clean up:
```bash
python -c "import duckdb; c=duckdb.connect('data/warehouse.duckdb'); c.execute(\"DELETE FROM raw.prices WHERE ticker='FAKE.NS'\").fetchall()"
```
🪟 PowerShell quoting is awkward. Run these lines in a small `.py` file instead, or skip 2.3.

**2.4 Lineage graph for the README**
```bash
cd dbt_project
dbt docs generate --profiles-dir .
dbt docs serve --profiles-dir .     # opens a browser
```
Click the blue lineage icon (bottom-right), screenshot it, and save as `docs/lineage.png` (create the `docs/` folder). Press Ctrl+C to stop, then `cd ..`.

---

## Step 3 — One-command pipeline (≈ 15 min)

```bash
python run_pipeline.py
```
It runs `extract_load` then `dbt_build`, logging START/END and duration for each. If either step fails, it stops and exits with code 1 (like an Airflow DAG, where a failed task blocks the tasks after it).

✅ **Check:** last line is `Pipeline succeeded in X s`.

---

## Step 4 — Schedule with GitHub Actions (≈ 45 min)

The workflow `.github/workflows/pipeline.yml` is ready:
- runs at `0 22 * * 1-5` (22:00 UTC Mon–Fri, which is 5 pm EST / 6 pm EDT, after all three markets close)
- `workflow_dispatch` adds a manual **Run workflow** button
- `permissions: contents: write` lets it push the updated `data/warehouse.duckdb`
- `concurrency` stops two runs from writing the database at once

**4.1 First push** (include your locally built database so the dashboard has data right away)
```bash
git add .
git commit -m "Initial pipeline: extract, dbt models/tests, dashboard, schedule"
git push -u origin main
```
When Git asks for a password, use a **Personal Access Token** (GitHub → Settings → Developer settings → Tokens), or sign in with GitHub Desktop / VS Code.

**4.2 Allow the workflow to push:** repo → **Settings → Actions → General → Workflow permissions** → choose **Read and write permissions** → Save.

**4.3 Test it manually:** repo → **Actions** tab → *daily-pipeline* → **Run workflow** → main → Run.

✅ **Check:** the run goes green (≈ 2–4 min), and a new commit `data: daily refresh YYYY-MM-DD` from github-actions[bot] appears (or the log says "No data changes" if nothing changed since your push).

Good to know:
- Scheduled runs can start 5–30 min late. That's normal for GitHub.
- GitHub pauses schedules in repos with no activity for 60 days. The daily data commits keep it active.
- If yfinance gets rate-limited on GitHub's servers, the run turns red. Click **Re-run jobs**; the retries usually handle it.
- After each run, `git pull` locally before you commit, since the bot changes `warehouse.duckdb`.

---

## Step 5 — Dashboard (≈ 1–1.5 h)

**5.1 Run locally**
```bash
streamlit run dashboard/app.py
```
It opens http://localhost:8501 with:
- a sidebar for companies and date range
- KPI row: best and worst performer this month (month to date)
- close price + 20-day moving average for one chosen company
- relative performance indexed to 100. This chart exists because prices are in INR/USD/JPY, and you can't compare raw prices across currencies
- monthly returns bar chart
- month-to-date leaderboard, and two-wheeler vs four-wheeler averages
- "Data last updated" from `MAX(ingested_at)`

✅ **Check:** no red errors, and the charts change when you change the filters.

**5.2 Deploy free on Streamlit Community Cloud**
1. share.streamlit.io → sign in with GitHub.
2. **Create app** → *Deploy a public app from GitHub* → repo `auto-market-pipeline`, branch `main`, main file `dashboard/app.py`.
3. **Advanced settings** → Python 3.11 → **Deploy**.
4. Copy the URL. It's your live link.

Every bot commit updates the repo, so Streamlit redeploys with fresh data. The app also caches queries for 1 hour.

✅ **Check:** the public URL loads in an incognito window.

---

## Step 6 — README + diagram (≈ 1 h)

1. Draw the architecture in **Excalidraw** (excalidraw.com) or draw.io: yfinance → Python → DuckDB raw → dbt (staging → marts + tests) → DuckDB analytics → Streamlit, with GitHub Actions around it. Export as `docs/architecture.png`.
2. In `README.md`, replace `<paste your Streamlit link here>` with your URL.
3. Check that `docs/lineage.png` (from step 2.4) is there.
4. Push:
   ```bash
   git pull               # get the bot's latest data commit first
   git add .
   git commit -m "Add README diagrams and live link"
   git push
   ```

✅ **Final check:** open the repo on GitHub as a stranger would. In 30 seconds, can you see what it is, the live link, the diagram, and the tests? Then you're done.

---

## Troubleshooting

| Error | Fix |
|---|---|
| `dbt: command not found` | venv not active → `source .venv/bin/activate` (🪟 `.venv\Scripts\activate`) |
| `IO Error: Could not set lock on file ... warehouse.duckdb` | another process (the Streamlit app, a Python shell, DBeaver) has the file open for writing. Close it and rerun. DuckDB allows one writer at a time |
| `Catalog Error: Table with name prices does not exist` | run the extract before dbt (`python run_pipeline.py` does both in order) |
| `Resource In Use Error: connection has an unfinished result` | newer DuckDB: use `.fetchall()` instead of `.fetchone()` in your own scripts |
| relationships / `not_null company_name` test fails | a ticker in raw isn't in `companies.csv`. Add it to the seed |
| `accepted_values` fails | typo in `segment` in the seed (must be exactly `Two-wheeler` or `Four-wheeler`) |
| Actions: `Permission denied ... 403` on push | Step 4.2 (Read and write permissions) |
| Streamlit Cloud: "Warehouse not found" | `data/warehouse.duckdb` isn't committed. Run the pipeline locally or trigger the workflow, then check the file is on GitHub |
| dbt warns about deprecated test syntax | the YAML already uses the new `arguments:` format. If you're on an older dbt (< 1.10), remove the `arguments:` level and indent its contents one level up |

---

## Interview answers (short versions)

- **Why ELT?** Raw data is kept unchanged, so I can rebuild or fix any model from it with SQL. Warehouse compute is cheap, and transformations are versioned and tested in dbt.
- **What if it runs twice?** Nothing changes. The primary key plus upsert make it idempotent, and I verified the row count stays the same.
- **API down?** Each ticker retries 3× with backoff. A partial failure still loads the others. A total failure exits non-zero, so the Actions run goes red, and the next run's overlap window backfills the missed days.
- **Why a 7-day overlap?** Providers revise recent data, and runs get missed (holidays, outages). Re-pulling a week is cheap and the upsert overwrites corrections. I anchor it on the last loaded date, so even a two-week outage self-heals.
- **Grain of the fact table?** One row per ticker per trading day. `dim_company` joins on ticker. `price_id` is the surrogate key, tested for uniqueness.
- **Scale to 10,000 tickers or minute data?** Batch or parallelise the API calls (async, a queue), land files in S3 partitioned by date, move to Snowflake/BigQuery, use dbt incremental models, and orchestrate with Airflow with retries and alerting.
- **Production changes?** State in S3 or a warehouse instead of git, secrets manager, Airflow/Dagster, alerting (Slack/PagerDuty), source freshness checks, CI that runs `dbt build` on pull requests.

## Resume bullet (update the numbers if you change things)

> Built an automated ELT pipeline ingesting daily market data for 9 global automotive companies using Python, DuckDB, and dbt, with idempotent incremental loads, 18 data quality tests, a star-schema data model, and weekday scheduling via GitHub Actions, serving a live Streamlit dashboard.
