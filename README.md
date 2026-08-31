# High-Beta Stock Insights

A decision-support app that screens the liquid US market for **high-beta** stocks in an uptrend,
turns them into **BUY / HOLD / SELL** calls with **position sizing**, overlays a **market-regime
gate**, **macro-event** and **earnings** risk, **sentiment**, and **post-earnings drift**, keeps a
**track-record ledger**, and validates the rules with a **10-year backtest** (Monte-Carlo +
stop-width sweep). See `TRADING_STRATEGY_BRIEF.md` for a plain-English walkthrough of the strategy.

> Decision-support only. Not financial advice. You place every trade yourself.

## Your settings (in `src/config.py`)
- Capital: **£50,000** (override with the `CAPITAL_GBP` env var / repo variable)
- Risk per trade: **1.5%** (~£750 max loss)
- Max concurrent positions: **8** (portfolio heat cap ~12%; applied per run — see the strategy brief)
- Benchmark: **SPY**

## Setup
```bash
cd stock-insights
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run the data pipeline (populates the local DB)
```bash
python -m src.pipeline              # full S&P 500 universe
MAX_TICKERS=25 python -m src.pipeline   # quick smoke test on 25 names
```

## Launch the dashboard
```bash
streamlit run app.py
```

## What each piece does
| File | Role |
|------|------|
| `src/universe.py` | Builds the ticker universe (S&P 500 via Wikipedia; fallback list if offline) |
| `src/data.py`     | Downloads daily OHLCV + FX via yfinance (free, no API key) |
| `src/metrics.py`  | Beta vs SPY, 1m/3m momentum, trend (SMA50/200), ATR%, avg $ volume |
| `src/screen.py`   | Filters for tradability + min beta, ranks by beta+momentum+trend |
| `src/signals.py`  | BUY/HOLD/SELL calls, conviction, entry gates, position sizing + portfolio construction |
| `src/regime.py`   | Market-regime gate (SPY/QQQ/VIX/SMH/10-yr/HYG + breadth) → RISK-ON/OFF sizing |
| `src/events.py`   | Macro-event calendar (CPI/FOMC/jobs) + earnings dates |
| `src/news.py`     | Sentiment: StockTwits crowd → FinBERT → keywords; once-daily Claude insight |
| `src/av.py`       | Alpha Vantage article-level news sentiment for the day's actionable names |
| `src/pead.py`     | Post-earnings drift (PEAD) — momentum-aligned earnings tilt |
| `src/marketdata.py` | Options IV + short interest + analyst-revision overlays (best-effort) |
| `src/ledger.py`   | Track-record ledger: logs recommendations, resolves exits (trail/trend/time) |
| `src/backtest.py` | 10-year replay with the same exits + Monte-Carlo range and stop-width sweep |
| `src/notify.py` · `src/shock.py` | Email/file digests; hourly news-shock check |
| `src/pipeline.py` | Orchestrates a full refresh and writes results to SQLite |
| `app.py`          | Streamlit dashboard: signals & sizing, macro & news, track record, screener, how-it-works |

## Deploy to Streamlit Community Cloud (free shareable link)

The app ships with a slim **`data/seed.db`** snapshot, so a fresh deploy opens with data
immediately (the app copies it into place on first run).

1. **Put this folder on GitHub** (one time):
   ```bash
   cd stock-insights
   git init && git add . && git commit -m "High-beta stock insights app"
   git branch -M main
   git remote add origin https://github.com/<you>/stock-insights.git
   git push -u origin main
   ```
2. Go to **share.streamlit.io** → *Create app* → pick your repo, branch `main`, main file **`app.py`**.
3. **(Optional) Secrets** — in the app's *Settings → Secrets* (and as GitHub Action secrets for the
   scheduled refresh), add any of:
   ```toml
   APP_PASSWORD = "choose-a-password"        # gate the app (free tier is public)
   ALPHAVANTAGE_API_KEY = "KEY1,KEY2"        # article-level news on actionable names (free; comma-
   #                                           separate several free keys to rotate past 25/day)
   ANTHROPIC_API_KEY = "sk-ant-..."          # once-daily Claude insight column (display-only)
   FINNHUB_API_KEY = "..."                   # richer headline feed (free tier)
   ```
4. Click **Deploy**. Share the resulting `https://<you>-stock-insights.streamlit.app` link.

**Cloud notes**
- **Sentiment** runs on free sources by default: **StockTwits** crowd mood for every name (no key),
  reinforced by **Alpha Vantage** article-level news on the day's actionable names, with a
  once-daily **Claude insight** for colour. Fallback when the crowd is quiet: FinBERT → keywords.
  (**FinBERT is off in the cloud** — PyTorch is too heavy for the free tier — so the cloud fallback
  is keywords.) Claude no longer drives the BUY/HOLD/SELL signal.
- The **🔄 Refresh** button works on the cloud (macro + news + signals). **Run backtest** will
  re-download history and may be slow/limited on the free tier — run it locally and it ships in
  the seed snapshot.
- Cloud storage is ephemeral: refreshes update the running app but reset on redeploy. The baseline
  advances via the scheduled GitHub Action (below), which rebuilds and pushes `data/seed.db`.

## Scheduled data refresh (hands-off)

`.github/workflows/refresh-data.yml` runs the full pipeline **twice each weekday** — **pre-open**
(refreshes the Claude insight) and **pre-close** (refreshes Alpha Vantage) — rebuilds `data/seed.db`
and pushes it, which triggers a Streamlit redeploy so the live app stays fresh. It needs
**Actions → workflow permissions = read/write** and the repo secrets above.

> **Timing note:** GitHub's built-in cron is best-effort and has delivered runs hours late. For
> precise timing, the workflow accepts a `kind` input (`pre-open`/`pre-close`) via
> `workflow_dispatch`, so an **external scheduler** (e.g. cron-job.org) can POST to the dispatch API
> at exact ET times. The built-in crons remain a fallback; an idempotency guard prevents
> double-refreshes.

## Deploy to Heroku (legacy — Streamlit Cloud is the live host)

> The `Procfile` / `.python-version` are leftovers from an earlier host; the app runs on Streamlit
> Community Cloud. These steps are kept only for reference.

Ships with a `Procfile` and `.python-version`. FinBERT is off here too (PyTorch is too big
for a Heroku slug) — sentiment falls back to keywords.

```bash
cd stock-insights
heroku login
heroku create <your-app-name>              # or omit for a random name
heroku config:set APP_PASSWORD=pick-a-password        # optional: gate the public URL
heroku config:set ANTHROPIC_API_KEY=sk-ant-...        # optional: Claude news sentiment
git push heroku main                        # builds & deploys
heroku open
```

Heroku config vars are read straight from the environment (no `.env` needed). The app copies
`data/seed.db` into place on first boot, so it opens with data. Dyno storage is ephemeral, so
refreshes reset on restart/redeploy — update the shared baseline by re-running the pipeline,
rebuilding the seed (`python make_seed.py`), and pushing again.

> Alternatively, deploy from the Heroku Dashboard: *New → Create app → Deploy → GitHub*, connect
> `WeepingWillow88/stock-insights`, and enable automatic deploys from `main`.

## Status / roadmap
Delivered: signal engine + position sizing (GBP→USD), macro-regime gate, macro-event & earnings
risk, sentiment (StockTwits + Alpha Vantage + once-daily Claude insight), post-earnings drift,
track-record ledger, 10-year backtest, and a twice-daily hands-off data refresh.

- **Next** — graded (not binary) sentiment contribution to conviction; enforce the position cap
  across live holdings, not just per run.
- **Later** — widen universe to Russell 1000–3000; optional IBKR position sync.
