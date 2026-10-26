# High-Beta Stock Insights

A decision-support app that screens the liquid US market for **high-beta** stocks in an uptrend,
turns them into **BUY / HOLD / SELL** calls with **position sizing**, overlays a **market-regime
gate**, **macro-event** and **earnings** risk, **sentiment**, and **post-earnings drift**, keeps a
**track-record ledger**, and replays the rules in a **10-year point-in-time backtest** — one
account on the S&P 500 as it was at the time, against holding the S&P 500 or cash, with a
random-entry baseline. See `TRADING_STRATEGY_BRIEF.md` for a plain-English walkthrough of the strategy.

> Decision-support only. Not financial advice. You place every trade yourself.

## Your settings (in `src/config.py`)
- Capital: **£50,000** (override with the `CAPITAL_GBP` env var / repo variable)
- Risk per trade: **1.5%** (~£750 max loss)
- Max concurrent positions: **8**, counting everything you already hold (portfolio heat cap ~12%)
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
| `src/universe.py` | Builds the ticker universe (S&P 500 via Wikipedia; fallback list if offline), plus the index's month-by-month membership history for the backtest |
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
| `src/backtest.py` | Replays the live rules as one account on point-in-time S&P 500 membership vs SPY + cash; random-entry baseline, block bootstrap, stop-width sweep |
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
- The **🔄 Refresh** button works on the cloud (macro + news + signals). **Run backtest** is
  disabled on the hosted app (it downloads ~10 years of prices for ~650 tickers plus the S&P 500's
  membership history) — run `python -m src.backtest` locally, then `python make_seed.py`, and the
  results ship in the seed snapshot.
- Cloud storage is ephemeral: refreshes update the running app but reset on redeploy. The baseline
  advances via the scheduled GitHub Action (below), which rebuilds and pushes `data/seed.db`.

## Scheduled data refresh (hands-off)

`.github/workflows/refresh-data.yml` runs the full pipeline **twice each weekday** — **pre-open**
(refreshes the Claude insight) and **pre-close** (refreshes Alpha Vantage) — rebuilds `data/seed.db`
and pushes it, which triggers a Streamlit redeploy so the live app stays fresh. It needs
**Actions → workflow permissions = read/write** and the repo secrets above.

> **Timing note:** GitHub's built-in cron is best-effort. For this repo it has delivered the
> 08:45 ET pre-open run 3–7 hours late (after the open) and dropped many slots, so the workflow
> offers it 72 quarter-hourly weekday slots (from 02:07 UTC, so even late ones land before the
> open), and its gate skips weekends and turns all but the first per slot into a ~20 s no-op. That's a fallback, not a guarantee — for exact times, add the external trigger below.

### Reliable timing: trigger the refresh from cron-job.org (recommended)

A `workflow_dispatch` runs as soon as it's requested, so a free external scheduler that calls
GitHub's API at fixed Eastern times makes the refresh punctual (a run takes ~7 min, ≤ 16 min). The
built-in crons stay as the fallback: if a dispatch has already refreshed the slot, their late
deliveries no-op.

1. **GitHub token** — github.com → Settings → Developer settings → Fine-grained tokens → Generate.
   Repository access: *Only select repositories* → `stock-insights`. Permissions → **Actions: Read
   and write** (nothing else). Pick the longest expiry and diarise renewing it.
2. **cron-job.org** (free account) → *Create cronjob*, twice:

   | | Pre-open | Pre-close |
   |---|---|---|
   | URL | `https://api.github.com/repos/WeepingWillow88/stock-insights/actions/workflows/refresh-data.yml/dispatches` | same |
   | Schedule | Mon–Fri **08:30**, time zone **America/New_York** | Mon–Fri **15:20**, America/New_York |
   | Method | `POST` | `POST` |
   | Headers | `Authorization: Bearer <token>` · `Accept: application/vnd.github+json` · `Content-Type: application/json` | same |
   | Body | `{"ref":"main","inputs":{"kind":"pre-open"}}` | `{"ref":"main","inputs":{"kind":"pre-close"}}` |

   Turn on *notify on failure*: GitHub answers `204 No Content` on success, and a `401` means
   the token expired. Use *Test run* once to see a run appear in the repo's **Actions** tab.

The America/New_York time zone handles daylight saving, so the times never need adjusting.

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
track-record ledger, a point-in-time 10-year backtest of the live rules, position / sector / risk
caps enforced across live holdings, and a twice-daily hands-off data refresh.

- **Next** — graded (not binary) sentiment contribution to conviction.
- **Later** — widen universe to Russell 1000–3000; optional IBKR position sync.
