# High-Beta Stock Insights — Phase 1

A decision-support app that screens the liquid US market, computes **beta vs. SPY**,
and ranks a **high-beta shortlist** that is also trending up. This is Phase 1: the
discovery layer + dashboard. Signals, position sizing, and news/sentiment alerts come
in later phases.

> Decision-support only. Not financial advice. You place every trade yourself.

## Your settings (baked into `src/config.py`)
- Capital: **£50,000**
- Risk per trade: **1.5%** (~£750 max loss)
- Max concurrent positions: **8** (portfolio heat cap ~12%)
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
| `src/data.py`     | Downloads daily OHLCV via yfinance (free, no API key) |
| `src/metrics.py`  | Beta vs SPY, 1m/3m momentum, trend (SMA50/200), ATR%, avg $ volume |
| `src/screen.py`   | Filters for tradability + min beta, ranks by beta+momentum+trend |
| `src/pipeline.py` | Orchestrates a full refresh and writes results to SQLite |
| `app.py`          | Streamlit dashboard: ranked table + price chart |

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
3. **(Optional) Secrets** — in the app's *Settings → Secrets*, add any of:
   ```toml
   APP_PASSWORD = "choose-a-password"     # gate the app (free tier is public)
   ANTHROPIC_API_KEY = "sk-ant-..."       # turn on Claude news sentiment
   ```
4. Click **Deploy**. Share the resulting `https://<you>-stock-insights.streamlit.app` link.

**Cloud notes**
- **FinBERT is off in the cloud** (PyTorch is too heavy for the free tier) — news falls back to
  Claude (if a key is set) or the keyword scorer. Add `ANTHROPIC_API_KEY` for the smart engine.
- The **🔄 Refresh** button works on the cloud (macro + news + signals). **Run backtest** will
  re-download history and may be slow/limited on the free tier — run it locally and it ships in
  the seed snapshot.
- Cloud storage is ephemeral: refreshes update the running app but reset on redeploy. Re-generate
  `data/seed.db` locally and re-push to update the baseline (`python -m src.pipeline`, then the
  seed step in the repo).

## Roadmap
- **Phase 2** — buy/sell/hold signal engine + position sizing (uses your risk settings) + GBP→USD conversion
- **Phase 3** — Claude news/sentiment layer, BOD/EOD email digests, hourly news-shock alerts
- **Later** — widen universe to Russell 1000–3000; optional IBKR position sync
