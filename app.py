"""High-Beta Stock Insights dashboard (Phase 1 + 2).

Run with:  streamlit run app.py
(Populate data first with:  python -m src.pipeline)
"""
import datetime as dt
import os
import shutil
import sqlite3

import pandas as pd
import streamlit as st

from src.config import CONFIG

st.set_page_config(page_title="High-Beta Stock Insights", layout="wide")

# --- Cloud bootstrap: make secrets available as env vars, and seed the DB on first run ---
try:
    for _k, _v in st.secrets.items():
        os.environ.setdefault(_k, str(_v))
except Exception:  # noqa: BLE001 - no secrets file locally is fine
    pass

if not os.path.exists(CONFIG.db_path) and os.path.exists("data/seed.db"):
    os.makedirs(os.path.dirname(CONFIG.db_path) or ".", exist_ok=True)
    shutil.copy("data/seed.db", CONFIG.db_path)

# --- Optional password gate (set APP_PASSWORD in Streamlit secrets to enable) ---
_pw = os.environ.get("APP_PASSWORD")
if _pw and not st.session_state.get("authed"):
    st.title("📈 High-Beta Stock Insights")
    entered = st.text_input("Password", type="password")
    if entered and entered == _pw:
        st.session_state["authed"] = True
        st.rerun()
    elif entered:
        st.error("Incorrect password.")
    st.stop()


@st.cache_data(ttl=300)
def load_table(table):
    conn = sqlite3.connect(CONFIG.db_path)
    try:
        return pd.read_sql_query(f"SELECT * FROM {table}", conn)
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


SIG_EMOJI = {"BUY": "🟢 BUY", "SELL": "🔴 SELL", "HOLD": "🟡 HOLD"}


def freshness(iso):
    """Return (pretty datetime, 'x ago', is_stale) for an ISO timestamp string."""
    if not iso or pd.isna(iso):
        return (None, None, False)
    try:
        t = dt.datetime.fromisoformat(str(iso))
    except Exception:  # noqa: BLE001 - a plain date string (no time) still formats below
        return (str(iso), None, False)
    secs = (dt.datetime.now() - t).total_seconds()
    if secs < 3600:
        ago = f"{max(int(secs // 60), 0)} min ago"
    elif secs < 86400:
        ago = f"{int(secs // 3600)} h ago"
    else:
        d = int(secs // 86400)
        ago = f"{d} day{'s' if d != 1 else ''} ago"
    pretty = t.strftime("%a %d %b %Y, %H:%M") if t.hour or t.minute else t.strftime("%a %d %b %Y")
    return (pretty, ago, secs > 36 * 3600)  # stale after ~1.5 days

st.title("📈 High-Beta Stock Insights")
st.caption("Decision-support only. Not financial advice. You place every trade yourself.")

with st.expander("📖 How to read this dashboard", expanded=False):
    st.markdown(
        """
### What this app does
It scans the liquid US market (S&P 500), finds **high-beta** stocks (ones that swing more
than the market) that are **trending up**, turns them into **buy / hold / sell** calls with
exact **position sizing**, checks the **market backdrop and the news**, and keeps a
**track record** of how the calls turn out. Everything technical runs under the hood — hover
any column's ℹ️ for a plain-English definition.

### The five tabs
- **🎯 Signals & sizing** — today's picks: what to buy, how many shares, the safety-exit
  (stop) and profit target, £ at risk, and a **Confidence %**. The "take these" table is your
  ready-to-trade shortlist (diversified across sectors).
- **📰 Macro & News** — the market mood (are conditions good for high-beta right now?),
  scheduled events that move everything (inflation/Fed/jobs), and a per-stock **news read**
  you can expand to see the actual headlines. Has two refresh buttons: **🔄 Refresh macro &
  news** (quick — regime/news on today's cached prices) and **⬇️ Pull fresh prices**
  (re-downloads price history and advances the market-data date).
- **📈 Track record** — proof, not promises: a **live scorecard** of the app's own past calls,
  and a **backtest** of the rules over ~10 years. Also holds the **circuit-breaker** warning.
- **📊 Screener** — the raw ranked candidate list plus a price chart for any name.
- **🧠 How it works** — the full method in plain English, and the settings you can ask to change.

### How the buy/hold/sell call is decided
- **🟢 BUY** — uptrend (price above its 50-day average, 50-day above 200-day) + positive
  momentum + healthy RSI, **and** the market backdrop allows it and the news isn't strongly negative.
- **🟡 HOLD** — uptrend but overbought (wait for a dip), momentum cooling, earnings within a
  few days, or fresh bad news — reasons to wait.
- **🔴 SELL** — price dropped below its 50-day average (trend broken) or it's weak.

### How much to buy (your rules)
- **Risk per trade = 1.5% of £50,000 ≈ £750** — the most you'd *lose* if the safety-exit is hit
  (not how much you put in). **Safety-exit = 2 average daily swings below entry**; **target =
  twice that distance** (a 2:1 reward-to-risk trade). Shares are set so the loss can't exceed
  your limit, and the portfolio takes at most **3 picks per sector** so it's genuinely diversified.

### Key terms you'll see
| Term | Plain meaning |
|---|---|
| **Beta / "jumpiness"** | How much a stock moves vs the market. 2 ≈ twice as much, up *and* down |
| **Confidence %** | How many independent checks agree (trend, momentum, RSI, news). Higher = surer |
| **RSI / "momentum"** | 0–100 gauge. >70 overheated, <30 beaten-down; 45–65 is the healthy buy zone |
| **Daily swing (ATR)** | Typical size of a day's move — sets how wide the safety-exit sits |
| **News mood** | 🔴/⚪/🟢 read of recent headlines (engine shown on the Macro & News tab) |
| **R (risk multiple)** | Track-record unit: +1R = made what you risked, −1R = hit your stop |
| **Regime** | Whether market conditions favour high-beta (RISK-ON) or not (RISK-OFF) |

### Good to know
- **News engine:** uses Claude AI when a key is set, otherwise **FinBERT** (a finance-trained
  model), otherwise a simple keyword scan — the active one is labelled on the Macro & News tab.
- **How fresh is this?** A **🕒 Last updated** stamp sits at the top (and in the sidebar) showing
  when data was last pulled and how long ago — it turns amber if the data looks stale. Each tab
  also shows its own "as of" time. There are **two** freshness levers on the Macro & News tab:
  **🔄 Refresh macro & news** re-reads the regime/events/news on the *cached* prices (quick), while
  **⬇️ Pull fresh prices** re-downloads the full price history and advances the **market-data
  through** date. The hosted app also auto-refreshes on a daily schedule (before the open / after
  the close), so most of the time you don't need to press anything.

> ⚠️ High beta moves fast **both ways**. This is decision-support and research — **not financial
> advice**, and no tool guarantees profit. Your stops, sizing and diversification are what protect you.
"""
    )

shortlist = load_table("screen_results")
signals_df = load_table("signals")
regime_df = load_table("regime")
events_df = load_table("macro_events")
news_df = load_table("news")
ledger_df = load_table("ledger")
bt_summary_df = load_table("backtest_summary")
bt_trades_df = load_table("backtest_trades")
bt_sens_df = load_table("backtest_sensitivity")
meta_df = load_table("meta")

if shortlist.empty:
    st.warning("No data yet. Run the pipeline first:  `python -m src.pipeline`")
    st.stop()

run_date = shortlist["run_date"].iloc[0] if "run_date" in shortlist.columns else "?"
fx_rate = float(signals_df["fx_rate"].iloc[0]) if (not signals_df.empty and "fx_rate" in signals_df.columns) else CONFIG.fx_fallback

# ---- Freshness: when was the data last pulled, and how old is it? ----
_last_updated = meta_df.iloc[0].get("last_updated") if not meta_df.empty else run_date
_market_through = meta_df.iloc[0].get("market_through") if not meta_df.empty else None
_run_kind = meta_df.iloc[0].get("run_kind") if not meta_df.empty else None
upd_pretty, upd_ago, upd_stale = freshness(_last_updated)

_bits = [f"🕒 **Last updated:** {upd_pretty}" + (f" ({upd_ago})" if upd_ago else "")]
if _market_through and not pd.isna(_market_through):
    _bits.append(f"**market data through** {_market_through}")
if _run_kind and not pd.isna(_run_kind):
    _bits.append(f"via {_run_kind}")
_fresh_line = "  ·  ".join(_bits)
if upd_stale:
    st.warning(_fresh_line + "  —  this looks **stale**; open the 📰 Macro & News tab and hit "
               "**⬇️ Pull fresh prices** (or re-run the pipeline) to advance the market-data date.")
else:
    st.success(_fresh_line)

# ---- Sidebar ----
with st.sidebar:
    st.header("Filters")
    min_beta = st.slider(
        "Minimum 'beta' (how jumpy)", 0.5, 3.0, float(CONFIG.min_beta), 0.1,
        help="Beta measures how much a stock moves compared to the whole market. "
             "Beta 1 = moves with the market. Beta 2 = moves about twice as much, up AND down. "
             "Higher beta = bigger, faster swings (more reward and more risk). "
             "Drag right to show only the jumpiest stocks.")
    st.caption(f"Showing stocks that swing at least **{min_beta:.1f}×** as much as the market.")
    only_uptrend = st.checkbox(
        "Only stocks trending up", value=False,
        help="Show only stocks whose price is above its recent averages — i.e. currently "
             "in an uptrend rather than falling.")
    st.markdown("---")
    st.header("Your money & safety limits")
    st.write(f"Money to trade: **£{CONFIG.capital_gbp:,.0f}**")
    st.write(f"Most you'll risk per trade: **{CONFIG.risk_per_trade * 100:.1f}%** "
             f"(~£{CONFIG.capital_gbp * CONFIG.risk_per_trade:,.0f})")
    st.caption("This is the maximum you'd *lose* on one trade if its stop-loss is hit — "
               "not how much you put in.")
    st.write(f"Max positions at once: **{CONFIG.max_positions}**")
    st.write(f"Total-risk ceiling: **{CONFIG.max_portfolio_heat * 100:.0f}%** of your money")
    st.caption("('Heat' = everything at risk across all open trades combined.)")
    st.write(f"£→$ rate used: **{fx_rate:.4f}**")
    st.markdown("---")
    st.caption(f"🕒 Data last pulled: **{upd_pretty}**" + (f" ({upd_ago})" if upd_ago else ""))
    if _market_through and not pd.isna(_market_through):
        st.caption(f"Covers prices through **{_market_through}**")

tab_sig, tab_news, tab_track, tab_screen, tab_how = st.tabs(
    ["🎯 Signals & sizing", "📰 Macro & News", "📈 Track record",
     "📊 Screener", "🧠 How it works"])

# =================== SIGNALS TAB ===================
with tab_sig:
    if signals_df.empty:
        st.info("No signals yet. Re-run `python -m src.pipeline` to generate them.")
    else:
        sig = signals_df.copy()
        sig["selected"] = sig["selected"].astype(bool)
        selected = sig[sig["selected"]]

        # ---- Layer A: market-regime banner ----
        if not regime_df.empty:
            r = regime_df.iloc[0]
            label = str(r["label"])
            bits = []
            if pd.notna(r.get("vix")):
                bits.append(f"VIX {r['vix']:.0f}")
            if pd.notna(r.get("spy_vs_50d")):
                bits.append(f"S&P vs 50-day {r['spy_vs_50d']:+.1f}%")
            if pd.notna(r.get("us10y")):
                bits.append(f"US 10Y {r['us10y']:.2f}%")
            headline = f"Market regime: **{label}**   ·   " + "   ·   ".join(bits)
            gate = {"RISK-ON": "new BUYs at full size",
                    "CAUTION": "new BUYs allowed at reduced size",
                    "RISK-OFF": "new BUYs paused"}.get(label, "")
            banner = st.success if label == "RISK-ON" else st.warning if label == "CAUTION" else st.error
            banner(f"{headline}   →   {gate}")
            st.caption(
                "**What this means.** The *market regime* is the overall weather for risky stocks "
                "right now — the app checks it before trusting any BUY, because high-beta names "
                "live or die by the broad market. **🟢 RISK-ON** = market rising and calm, so these "
                "stocks tend to do well (trades at full size). **🟡 CAUTION** = mixed signals, so it "
                "trades smaller. **🔴 RISK-OFF** = market weak or fearful, when high beta falls "
                "hardest, so new buys are paused.  \nThe three readings: **VIX** = the market's fear "
                "level (under 20 = calm, over 30 = fearful); **S&P vs 50-day** = is the market itself "
                "trending up (positive) or down; **US 10-year yield** rising fast is a headwind for "
                "these stocks.")
            with st.expander("See the full reasoning + upcoming market-moving events"):
                for note in str(r.get("notes", "")).split(" • "):
                    if note:
                        st.markdown(f"- {note}")
                if not events_df.empty and "label" in events_df.columns:
                    st.markdown("**Upcoming scheduled events** (seeded calendar — verify dates):")
                    st.dataframe(events_df[["date", "label", "days_until"]],
                                 width="stretch", hide_index=True)
                else:
                    st.caption("No major scheduled macro events in the window.")

        capital_usd = CONFIG.capital_gbp * fx_rate
        total_pos_usd = float(selected["pos_value_usd"].sum())
        total_risk_gbp = float(selected["risk_gbp"].sum())
        heat_pct = total_risk_gbp / CONFIG.capital_gbp * 100 if CONFIG.capital_gbp else 0
        deployed_pct = total_pos_usd / capital_usd * 100 if capital_usd else 0

        st.subheader("Today's suggested portfolio")
        st.caption(f"🕒 Based on data from **{upd_pretty}**" + (f" ({upd_ago})" if upd_ago else "")
                   + " — re-check prices before you trade.")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("BUY signals", int((sig["signal"] == "BUY").sum()))
        c2.metric("Positions taken", f"{int(selected.shape[0])} / {CONFIG.max_positions}")
        c3.metric("Capital deployed", f"${total_pos_usd:,.0f}", f"{deployed_pct:.0f}% of capital")
        c4.metric("Portfolio heat", f"£{total_risk_gbp:,.0f}", f"{heat_pct:.1f}% (cap {CONFIG.max_portfolio_heat*100:.0f}%)")
        c5.metric("Cash free", f"${max(capital_usd - total_pos_usd, 0):,.0f}")

        money_cfg = {
            "price": st.column_config.NumberColumn("Price", format="$%.2f",
                     help="Latest share price, in US dollars."),
            "entry": st.column_config.NumberColumn("Buy at", format="$%.2f",
                     help="Suggested price to buy around."),
            "stop": st.column_config.NumberColumn("Safety exit", format="$%.2f",
                     help="Stop-loss: if the price falls here, sell to cap your loss. "
                          "Set two average daily swings (2×ATR) below the buy price."),
            "target": st.column_config.NumberColumn("Profit target", format="$%.2f",
                     help="Where to consider taking profit — set so the potential gain is "
                          "twice the potential loss (a 2:1 reward-to-risk trade)."),
            "shares": st.column_config.NumberColumn("Shares", format="%d",
                     help="How many shares to buy so your risk stays within your limit."),
            "pos_value_usd": st.column_config.NumberColumn("Amount ($)", format="$%d",
                     help="Cash this position uses = shares × buy price."),
            "risk_gbp": st.column_config.NumberColumn("At risk (£)", format="£%d",
                     help="The most you'd lose on this trade if the safety exit is hit. "
                          "Kept at or under your per-trade limit."),
            "beta": st.column_config.NumberColumn("Jumpiness (beta)", format="%.2f",
                     help="How much the stock moves vs the market. 2 ≈ twice as much, both ways."),
            "rsi": st.column_config.NumberColumn("Momentum (RSI)", format="%d",
                     help="0–100 gauge. Above 70 = possibly overheated; below 30 = beaten down; "
                          "45–65 is the healthy zone the app buys in."),
            "news": st.column_config.TextColumn("News mood",
                     help="How recent headlines read for this stock (see the Macro & News tab)."),
            "conviction": st.column_config.NumberColumn("Confidence", format="%d%%",
                     help="How many independent checks agree (trend, medium- & short-term "
                          "momentum, healthy RSI, news not against it). Higher = more sure."),
            "sector": st.column_config.TextColumn("Sector",
                     help="Industry group. The portfolio caps how many picks come from one "
                          "sector so your positions aren't secretly one big bet."),
        }

        st.markdown("**✅ Take these (top BUY signals within your 8-position limit):**")
        if selected.empty:
            st.info("No BUY signals qualify for the portfolio right now.")
        else:
            pcols = ["rank", "ticker", "sector", "conviction", "beta", "entry", "stop",
                     "target", "shares", "pos_value_usd", "risk_gbp", "flags", "reason"]
            pcols = [c for c in pcols if c in selected.columns]
            st.dataframe(selected[pcols], width="stretch", hide_index=True, column_config=money_cfg)

        st.markdown("---")
        st.subheader("All signals")
        types = st.multiselect("Show signal types", ["BUY", "HOLD", "SELL"],
                               default=["BUY", "HOLD", "SELL"])
        view = sig[sig["signal"].isin(types)].copy()
        view["signal"] = view["signal"].map(SIG_EMOJI).fillna(view["signal"])
        acols = ["rank", "ticker", "signal", "conviction", "beta", "price", "stop",
                 "target", "shares", "pos_value_usd", "risk_gbp", "news", "flags", "reason"]
        acols = [c for c in acols if c in view.columns]
        st.dataframe(view[acols], width="stretch", hide_index=True, column_config=money_cfg)
        st.caption("Sizing (shares/stop/target/risk) is shown for BUY signals only. "
                   "Stop = entry − 2×ATR · Target = 2:1 reward:risk · Risk £ kept ≤ £750.")

# =================== SCREENER TAB ===================
with tab_screen:
    st.subheader(f"Ranked high-beta shortlist — run {run_date}")
    view = shortlist[shortlist["beta"] >= min_beta]
    if only_uptrend and "uptrend" in view.columns:
        view = view[view["uptrend"] == 1]

    disp = view.copy()
    for pct_col in ["mom_3m", "mom_1m", "atr_pct"]:
        if pct_col in disp.columns:
            disp[pct_col] = (disp[pct_col] * 100).round(1)
    if "avg_dollar_vol" in disp.columns:
        disp["avg_$vol_m"] = (disp["avg_dollar_vol"] / 1e6).round(0)
    if "uptrend" in disp.columns:
        disp["uptrend"] = disp["uptrend"].map(lambda v: "Yes" if v in (1, True) else "No")

    cols = [c for c in ["rank", "ticker", "price", "beta", "mom_3m", "mom_1m",
                        "atr_pct", "avg_$vol_m", "uptrend"] if c in disp.columns]
    screen_cfg = {
        "rank": st.column_config.NumberColumn("Rank", help="1 = best mix of jumpiness + uptrend today."),
        "ticker": st.column_config.TextColumn("Ticker", help="Stock symbol."),
        "price": st.column_config.NumberColumn("Price", format="$%.2f", help="Latest share price (USD)."),
        "beta": st.column_config.NumberColumn("Jumpiness (beta)", format="%.2f",
                help="How much it moves vs the market. 2 ≈ twice as much, both ways."),
        "mom_3m": st.column_config.NumberColumn("3-month change", format="%.1f%%",
                help="Price change over the last ~3 months. Positive = trending up."),
        "mom_1m": st.column_config.NumberColumn("1-month change", format="%.1f%%",
                help="Price change over the last ~1 month."),
        "atr_pct": st.column_config.NumberColumn("Daily swing", format="%.1f%%",
                help="Typical size of a day's price move, as a % of price. Higher = more volatile."),
        "avg_$vol_m": st.column_config.NumberColumn("Liquidity ($M/day)", format="%d",
                help="Average dollars traded per day (millions). Higher = easier to buy/sell."),
        "uptrend": st.column_config.TextColumn("Trending up?",
                help="Yes = price is above its recent averages (trend is on your side)."),
    }
    st.dataframe(disp[cols], width="stretch", hide_index=True, column_config=screen_cfg)
    st.caption("Hover any column header (ℹ️) for a plain-English definition. "
               "This tab is the raw candidate list — the 🎯 Signals tab turns it into buy/sell calls.")

    st.subheader("Price history")
    tickers = view["ticker"].tolist()
    if tickers:
        pick = st.selectbox("Ticker", tickers)
        prices = load_table("prices")
        p = prices[prices["ticker"] == pick].copy()
        if not p.empty:
            p["date"] = pd.to_datetime(p["date"])
            p = p.sort_values("date").set_index("date")
            st.line_chart(p["adj_close"])

# =================== MACRO & NEWS TAB ===================
NEWS_EMOJI = {"negative": "🔴 negative", "positive": "🟢 positive", "neutral": "⚪ neutral"}

with tab_news:
    top = st.columns([2, 1, 1])
    with top[0]:
        st.subheader("Today's market picture, in plain English")
        st.caption(f"🕒 Last refreshed: **{upd_pretty}**" + (f" ({upd_ago})" if upd_ago else "")
                   + (f"  ·  prices through {_market_through}" if _market_through
                      and not pd.isna(_market_through) else ""))
    with top[1]:
        if st.button("🔄 Refresh macro & news", use_container_width=True,
                     help="Re-pull the market regime, macro events, earnings and news and rebuild "
                          "signals. Reuses CACHED prices (does not re-download price history), so "
                          "it's quick (~1–2 min) and does NOT advance the 'market data through' "
                          "date. Use ‘Pull fresh prices’ for that."):
            from src import pipeline
            with st.spinner("Refreshing macro + news (regime, events, earnings, headlines)…"):
                try:
                    res = pipeline.refresh_macro_news(CONFIG)
                    load_table.clear()
                    st.success(f"Macro & news refreshed — regime {res['regime']}, "
                               f"news via {res['news_source']}.")
                    st.rerun()
                except SystemExit as e:
                    st.error(str(e))
                except Exception as e:  # noqa: BLE001
                    st.error(f"Refresh failed: {e}")
    with top[2]:
        if st.button("⬇️ Pull fresh prices", use_container_width=True,
                     help="Re-download the full price history (~500 stocks) and rebuild "
                          "everything — this is what advances 'market data through' to the latest "
                          "trading day. Slower (~3–5 min). Note: on the hosted app this updates "
                          "your current session only; the scheduled daily job updates the shared "
                          "baseline everyone sees."):
            from src import pipeline
            with st.spinner("Downloading fresh prices for the full universe and rebuilding — "
                            "this can take a few minutes…"):
                try:
                    pipeline.run_pipeline(CONFIG, send_digest=False)
                    load_table.clear()
                    st.success("Fresh prices pulled — market data advanced to the latest trading day.")
                    st.rerun()
                except SystemExit as e:
                    st.error(str(e))
                except Exception as e:  # noqa: BLE001
                    st.error(f"Price refresh failed: {e}")
    st.caption("Tip: the hosted app auto-refreshes daily via a scheduled job (GitHub Actions) "
               "before the open and after the close. **Refresh macro & news** updates the "
               "regime/news on cached prices; **Pull fresh prices** re-downloads price history and "
               "advances the market-data date. Locally you can also run `python -m src.pipeline`.")

    # ---- The market backdrop ----
    st.markdown("### 🌡️ The market backdrop")
    if regime_df.empty:
        st.info("No regime data yet — hit Refresh or run the pipeline.")
    else:
        r = regime_df.iloc[0]
        label = str(r["label"])
        plain = {
            "RISK-ON": "**Good conditions for high-beta stocks.** The broad market is trending "
                       "up and fear is low, so the app takes new BUYs at full size.",
            "CAUTION": "**Mixed conditions.** Some warning signs are showing, so the app still "
                       "allows new BUYs but at reduced size — be selective.",
            "RISK-OFF": "**Poor conditions for high-beta stocks.** The market is weak or fearful. "
                        "High beta falls hardest here, so the app pauses new BUYs.",
        }.get(label, "")
        (st.success if label == "RISK-ON" else st.warning if label == "CAUTION" else st.error)(
            f"**{label}** — {plain}")
        m = st.columns(3)
        m[0].metric("VIX (fear gauge)", f"{r.get('vix')}",
                    help="Under 20 = calm, 20–30 = jittery, over 30 = fearful.")
        m[1].metric("S&P 500 vs its 50-day avg", f"{r.get('spy_vs_50d')}%",
                    help="Positive = market in an uptrend.")
        m[2].metric("US 10-year yield", f"{r.get('us10y')}%",
                    help="Rising fast tends to pressure high-beta / growth stocks.")
        with st.expander("What went into this call?"):
            for note in str(r.get("notes", "")).split(" • "):
                if note:
                    st.markdown(f"- {note}")

    # ---- Upcoming events ----
    st.markdown("### 📅 Scheduled events that move the whole market")
    if events_df.empty or "label" not in events_df.columns or events_df["label"].isna().all():
        st.caption("No major CPI / Fed / jobs events in the next few days.")
    else:
        st.caption("These are volatility days — the app sizes down around them. "
                   "(Seeded calendar; verify exact dates against BLS / the Fed.)")
        st.dataframe(events_df[["date", "label", "days_until"]].sort_values("days_until"),
                     width="stretch", hide_index=True)

    # ---- Per-stock news ----
    st.markdown("### 📰 What the news is saying about your shortlist")
    if news_df.empty:
        st.info("No news scored yet — hit Refresh or run the pipeline.")
    else:
        _srcmode = news_df["source"].mode() if "source" in news_df.columns else pd.Series([], dtype=object)
        src = _srcmode.iloc[0] if not _srcmode.empty else "?"
        st.caption(f"Each stock's recent headlines, scored for a short-term trader "
                   f"(sentiment engine: **{src}**). Expand a stock to read the headlines yourself.")
        only = st.radio("Show", ["All", "Only negative", "Only positive"],
                        horizontal=True, label_visibility="collapsed")
        view = news_df.copy()
        if only == "Only negative":
            view = view[view["label"] == "negative"]
        elif only == "Only positive":
            view = view[view["label"] == "positive"]
        # Sort: negative + high materiality first
        order = {"high": 0, "medium": 1, "low": 2}
        view["_lab"] = view["label"].map({"negative": 0, "neutral": 1, "positive": 2})
        view["_mat"] = view["materiality"].map(order).fillna(3)
        view = view.sort_values(["_lab", "_mat"])

        summary = view.copy()
        summary["sentiment_label"] = summary["label"].map(NEWS_EMOJI).fillna(summary["label"])
        st.dataframe(
            summary[["ticker", "sentiment_label", "sentiment", "materiality",
                     "macro_driver", "rationale"]],
            width="stretch", hide_index=True,
            column_config={
                "sentiment_label": st.column_config.TextColumn("News"),
                "sentiment": st.column_config.NumberColumn("Score", format="%.2f",
                             help="-1 very negative … +1 very positive"),
                "macro_driver": st.column_config.TextColumn("Driver"),
                "rationale": st.column_config.TextColumn("Plain-English read", width="large"),
            })
        st.markdown("#### Dig into the headlines")
        for _, row in view.iterrows():
            heads = str(row.get("headlines", "")).split(" || ") if row.get("headlines") else []
            tag = NEWS_EMOJI.get(row["label"], row["label"])
            with st.expander(f"{row['ticker']} — {tag}  ·  {str(row.get('rationale',''))[:90]}"):
                if heads and heads != [""]:
                    for h in heads:
                        st.markdown(f"- {h}")
                else:
                    st.caption("No recent headlines found.")

# =================== TRACK RECORD TAB ===================
with tab_track:
    st.subheader("How the recommendations actually perform")
    st.caption("Results are in **R** — multiples of what you risk per trade. "
               "+1R = you made what you risked · −1R = you hit your stop. "
               "Positive average over many trades = a real edge.")

    with st.expander("📖 What these numbers mean (plain English)"):
        st.markdown(
            "- **R (risk multiple)** — the unit everything is measured in. If you risk £750 on a "
            "trade, **+1R = +£750**, **−1R = −£750**. Comparing trades in R makes a £2,000 position "
            "and a £6,000 one directly comparable.\n"
            "- **Trades** — how many buy setups the rules produced over the period.\n"
            "- **Win rate** — the share of trades that ended in profit. (On its own it's not enough — "
            "a 50% win rate is great if wins are bigger than losses.)\n"
            "- **Avg trade (R)** — *the single most important number.* The average result per trade. "
            "Positive means the rules have a genuine edge once wins and losses are combined.\n"
            "- **Profit factor** — total money won ÷ total money lost. **Above 1 = profitable**; "
            "1.5+ is strong; below 1 loses money.\n"
            "- **Avg win / Avg loss (R)** — the typical size of a winner vs a loser. You want winners "
            "bigger than losers (our stop/target rule aims for ~2:1).\n"
            "- **Worst drop (max drawdown)** — the biggest peak-to-trough fall along the way — i.e. "
            "the losing streak you'd have had to stomach. Smaller is easier to live with.\n"
            "- **Avg hold** — how many days a trade lasted on average.\n"
            "- **Timed out** — the share of trades that never hit their target or stop and were "
            "closed on the time limit (your 'don't hold too long' rule).")

    # Circuit-breaker: today's realized losses
    if not ledger_df.empty and "status" in ledger_df.columns:
        ct = ledger_df[(ledger_df["status"] == "closed") & (ledger_df["exit_date"] == run_date)]
        if not ct.empty:
            acct_today = float(ct["r_multiple"].astype(float).sum()) * CONFIG.risk_per_trade
            if acct_today <= -CONFIG.daily_loss_limit_pct:
                st.error(f"🛑 **Circuit-breaker:** today's closed trades are down "
                         f"{acct_today*100:.1f}% of capital (limit "
                         f"{CONFIG.daily_loss_limit_pct*100:.0f}%). Consider pausing new trades today.")

    st.markdown("### 🟢 Live scorecard — trades this app actually logged")
    from src import ledger as _ledger
    ls = _ledger.stats(CONFIG)
    if ls.get("closed", 0) == 0:
        st.info(ls.get("note", "Building the track record…"))
    else:
        c = st.columns(5)
        c[0].metric("Closed trades", ls["closed"])
        c[1].metric("Win rate", f"{ls['win_rate']}%")
        c[2].metric("Avg trade", f"{ls['expectancy_r']:+.2f}R",
                    help="Average result per trade in multiples of your risk. Positive = edge.")
        c[3].metric("Avg win / loss", f"{ls['avg_win_r']:+.1f}R / {ls['avg_loss_r']:+.1f}R")
        c[4].metric("Total", f"{ls['total_r']:+.1f}R")
        st.caption(ls.get("note", ""))

    if not ledger_df.empty:
        opn = ledger_df[ledger_df["status"] == "open"]
        st.metric("Currently tracking (open)", int(len(opn)))
        if not opn.empty:
            st.dataframe(opn[["ticker", "record_date", "entry", "stop", "target", "risk_gbp"]],
                         width="stretch", hide_index=True)
        cl = ledger_df[ledger_df["status"] == "closed"]
        if not cl.empty:
            st.markdown("**Closed trades:**")
            st.dataframe(cl[["ticker", "record_date", "exit_date", "outcome", "r_multiple"]]
                         .sort_values("exit_date", ascending=False),
                         width="stretch", hide_index=True)

    st.markdown("---")
    st.markdown(f"### 🧪 Backtest — how the rules did over ~{CONFIG.backtest_years}")
    st.caption("Uses the **upgraded rules**: trailing-stop + trend-break exits (gap-aware fills), "
               "and entries filtered for conviction, volume, relative strength and a rising market.")
    on_cloud = bool(os.environ.get("DYNO"))  # Heroku sets DYNO on every dyno
    bcols = st.columns([3, 1])
    with bcols[1]:
        if on_cloud:
            st.button("▶️ Run backtest", use_container_width=True, disabled=True,
                      help="Disabled on the hosted app — it downloads years of data and would "
                           "overload the dyno. Run it locally (python -m src.backtest); results "
                           "ship in the app's data snapshot.")
        elif st.button("▶️ Run backtest", use_container_width=True,
                       help=f"Replays the rules over ~{CONFIG.backtest_years}. First run "
                            "downloads history (several minutes)."):
            from src import backtest
            with st.spinner(f"Running the backtest over ~{CONFIG.backtest_years} of history…"):
                try:
                    backtest.run_backtest(CONFIG)
                    load_table.clear()
                    st.success("Backtest complete.")
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"Backtest failed: {e}")

    if bt_summary_df.empty or int(bt_summary_df.iloc[0].get("trades", 0) or 0) == 0:
        with bcols[0]:
            if on_cloud:
                st.info("Backtesting runs locally, not on the hosted app (it's too heavy for the "
                        "dyno). Run `python -m src.backtest` and the results ship in the snapshot.")
            else:
                st.info(f"No backtest yet — click **Run backtest**. The first run downloads "
                        f"~{CONFIG.backtest_years} of prices, so give it a few minutes.")
    else:
        s = bt_summary_df.iloc[0]
        _gp, _ga, _ = freshness(s.get("generated"))
        if _gp:
            st.caption(f"🕒 Backtest generated **{_gp}**" + (f" ({_ga})" if _ga else "")
                       + f"  ·  {s.get('years', '?')} of history")
        c = st.columns(5)
        c[0].metric("Trades", int(s["trades"]))
        c[1].metric("Win rate", f"{s['win_rate']}%")
        c[2].metric("Avg trade", f"{s['expectancy_r']:+.2f}R",
                    help="The edge per trade. Positive over many trades = the rules add value.")
        c[3].metric("Profit factor", s.get("profit_factor"),
                    help="Total winnings ÷ total losses. Above 1 = profitable; above 1.5 = strong.")
        c[4].metric("Worst drop", f"{s['max_drawdown_pct']}%",
                    help="Largest peak-to-trough fall of the strategy — the pain you'd have sat through.")
        c2 = st.columns(4)
        c2[0].metric("Avg win", f"{s['avg_win_r']:+.1f}R")
        c2[1].metric("Avg loss", f"{s['avg_loss_r']:+.1f}R")
        c2[2].metric("Avg hold", f"{s['avg_hold_days']} days")
        c2[3].metric("Timed out", f"{s.get('timeout_pct')}%",
                     help="Share of trades that neither hit target nor stop and were closed on time.")
        if s.get("exp_r_downtrend") is None:
            st.caption(f"Average edge {s.get('exp_r_uptrend')}R/trade. The market-regime filter "
                       "means it only trades when the S&P is rising, so there are no down-market trades.")
        else:
            st.caption(f"Average edge in up-markets: {s.get('exp_r_uptrend')}R/trade · "
                       f"in down-markets: {s.get('exp_r_downtrend')}R/trade.")

        # Plain-English interpretation of THIS backtest's numbers
        risk_gbp = CONFIG.capital_gbp * CONFIG.risk_per_trade
        per_trade = s["expectancy_r"] * risk_gbp
        win_gbp = s["avg_win_r"] * risk_gbp
        loss_gbp = s["avg_loss_r"] * risk_gbp
        wins_in_10 = round(s["win_rate"] / 10)
        pf = s.get("profit_factor")
        pf_word = ("above break-even but modest" if pf and pf < 1.3
                   else "solid" if pf and pf < 1.7 else "strong" if pf else "")
        pf_line = (f"- **Profit factor {pf}** → for every £1 lost, about **£{pf:.2f} was won** "
                   f"({pf_word}).\n" if pf else "")
        verdict = ("a small but positive edge" if s["expectancy_r"] > 0
                   else "no edge (it lost money over the period)")
        st.info(
            f"**In plain terms.** About **{wins_in_10} in 10** trades made money — close to a "
            f"coin-flip. What makes it work is that **winners were bigger than losers**: a typical "
            f"win was **{s['avg_win_r']:+.1f}R (≈ £{win_gbp:,.0f})** while a typical loss was only "
            f"**{s['avg_loss_r']:+.1f}R (≈ £{loss_gbp:,.0f})**. Net, every trade added on average "
            f"**{s['expectancy_r']:+.2f}R ≈ £{per_trade:,.0f}** for every £{risk_gbp:,.0f} risked — "
            f"{verdict}.\n\n"
            f"{pf_line}"
            f"- The edge is **small per trade**, so it only shows up over **many** trades "
            f"(here, {int(s['trades']):,}). Don't judge it over a handful.\n"
            f"- **{s['timeout_pct']}%** of trades neither hit target nor stop — they were closed on "
            f"the {CONFIG.backtest_max_hold_days}-day time limit (the 'don't hold too long' rule).\n"
            f"- The **worst losing stretch was {s['max_drawdown_pct']}%** — expect bumpy periods even "
            f"when the strategy works. High beta is a rough ride, which is exactly why the sizing, "
            f"sector cap and circuit-breaker matter.\n"
            + ("- The **market-regime filter** kept it out of falling markets entirely (no "
               "down-market trades), which is how it should behave.\n"
               if s.get("exp_r_downtrend") is None else
               f"- Edge in falling markets was {s.get('exp_r_downtrend')}R/trade.\n"))
        if not bt_trades_df.empty and "equity" in bt_trades_df.columns:
            eq = bt_trades_df.sort_values("entry_date")[["entry_date", "equity"]].copy()
            eq["entry_date"] = pd.to_datetime(eq["entry_date"])
            st.line_chart(eq.set_index("entry_date")["equity"], height=220)
            st.caption("⚠️ **Illustrative shape only** — this compounds every trade one-after-another "
                       "as if taken alone, so the *height* is unrealistic; read it for the trend and "
                       "the size of the dips, not as an account balance. The trustworthy numbers are "
                       "the win rate, average edge (R) and profit factor above.")
        # Robustness (6): Monte-Carlo range + parameter sensitivity
        if s.get("mc_exp_p50") is not None:
            st.markdown("#### 🎲 How robust is this? (not just one lucky run)")
            r1 = st.columns(3)
            r1[0].metric("Edge — likely range",
                         f"{s.get('mc_exp_p5')} to {s.get('mc_exp_p95')}R",
                         help="5th–95th percentile of the average trade across "
                              f"{CONFIG.backtest_mc_runs} resamples of the trade history.")
            r1[1].metric("Chance the edge is real", f"{s.get('mc_prob_positive')}%",
                         help="Share of resamples where the average trade stayed positive. "
                              "Higher = less likely the result was luck.")
            r1[2].metric("Bad-case drawdown", f"{s.get('mc_maxdd_p95')}%",
                         help="A rough 1-in-20 worst losing streak — plan to stomach at least this.")
            st.caption("If the low end of the edge range is still comfortably positive and the "
                       "'chance it's real' is high, the result is trustworthy rather than a fluke.")
        if not bt_sens_df.empty:
            st.markdown("**Does it depend on one exact setting?** (stop-width sensitivity)")
            st.dataframe(bt_sens_df, width="stretch", hide_index=True, column_config={
                "stop_atr_mult": st.column_config.NumberColumn("Stop width (×ATR)", format="%.1f"),
                "trades": st.column_config.NumberColumn("Trades"),
                "expectancy_r": st.column_config.NumberColumn("Avg trade (R)", format="%.3f"),
                "win_rate": st.column_config.NumberColumn("Win rate", format="%.1f%%"),
                "profit_factor": st.column_config.NumberColumn("Profit factor", format="%.2f"),
            })
            st.caption("The edge should stay positive across nearby stop widths — if it only works "
                       "at one exact number, that's a red flag for over-fitting.")

        with st.expander("⚠️ Read this before trusting the backtest"):
            st.markdown(
                "- **Survivorship bias:** uses today's S&P 500 — failed companies are excluded, "
                "so real-world results would be somewhat worse.\n"
                "- **Technicals only:** the news + macro layers aren't replayed (no historical news feed) — "
                "the *live scorecard* above does include them.\n"
                f"- **Modelled fills:** entries at daily close with a flat "
                f"{CONFIG.backtest_cost_pct*100:.1f}% cost. Stops are **gap-aware** (filled at the "
                "open if price gaps below the stop), so overnight-gap losses are captured — but real "
                "spreads on volatile names can still be a touch worse.\n"
                "- **Past ≠ future.** This shows whether the rules *had* an edge, not a promise they will.")

# =================== HOW IT WORKS TAB ===================
with tab_how:
    c = CONFIG
    risk_gbp = c.capital_gbp * c.risk_per_trade
    st.subheader("How a recommendation is built — step by step")
    st.markdown(
        f"""
Everything below runs top-to-bottom each time the data is refreshed. **Every number in
bold is a setting you can ask to change** (they live in `src/config.py`).

---

#### Step 1 — Pick the universe
Start with the **S&P 500** (a reliable, liquid list). Anyone can ask to widen this to the
Russell 1000–3000 later.

#### Step 2 — Keep only *tradable* stocks
A stock must pass **both** filters or it's dropped:
- Price ≥ **${c.min_price:,.0f}** (avoid penny stocks)
- Average daily dollar-volume ≥ **${c.min_avg_dollar_volume/1e6:,.0f} million** (enough liquidity to get in/out)

#### Step 3 — Measure each stock
- **Beta** — how much the stock moves versus the market (**{c.benchmark}**), using the last
  **{c.beta_window} trading days**. Formula: `beta = covariance(stock returns, market returns) ÷ variance(market returns)`.
  Beta 1 = moves with the market; beta 2 = moves ~twice as much (both directions).
- **Momentum** — % price change over the last ~1 month and ~3 months.
- **Trend** — is price above its 50-day average, and is the 50-day above the 200-day?
- **ATR (volatility)** — the average daily price swing (how far it typically moves in a day).
- **RSI** — a 0–100 momentum gauge over **{c.rsi_period} days** (high = overbought, low = oversold).

#### Step 4 — Rank the high-beta shortlist
Keep only stocks with **beta ≥ {c.min_beta}**, then score and sort them:

> **score = 0.6 × (beta) + 0.4 × (3-month momentum) + 0.5 bonus if up-trending**
>
> *(beta and momentum are standardised first so they're comparable.)*

The top **{c.shortlist_size}** by score become the shortlist. **#1 = the strongest mix of high
beta and positive trend.** The weights (0.6 / 0.4 / 0.5 bonus) are all adjustable.

#### Step 5 — Turn each into a signal
| Signal | Triggered when |
|---|---|
| 🔴 **SELL** | Price drops **below its 50-day average** (trend broken), or it's weak (RSI < 35) and not trending |
| 🟡 **HOLD** | Up-trend but **overbought** (RSI ≥ **{c.rsi_overbought:.0f}**, wait for a pullback), or momentum cooling (RSI < **{c.rsi_min_buy:.0f}**), or no clear setup |
| 🟢 **BUY** | Up-trend **and** positive momentum **and** RSI in the healthy band (**{c.rsi_min_buy:.0f}–{c.rsi_overbought:.0f}**) |

#### Step 6 — Size the position (for BUY signals)
Your rules drive the maths:
- **Money at risk per trade** = {c.risk_per_trade*100:.1f}% of £{c.capital_gbp:,.0f} = **£{risk_gbp:,.0f}** (converted to USD at the live rate).
- **Stop-loss** = entry − **{c.atr_stop_mult:.0f} × ATR** (a wide stop suits volatile names).
- **Target** = entry + **{c.reward_risk:.0f} ×** the stop distance (a **{c.reward_risk:.0f}:1** reward-to-risk trade).
- **Shares to buy** = the **smaller** of:
  1. `£{risk_gbp:,.0f} at risk ÷ stop distance` (risk-based), and
  2. `(£{c.capital_gbp:,.0f} ÷ {c.max_positions} positions) ÷ entry price` (equal-weight capital slot).

  Taking the smaller means you **never risk more than £{risk_gbp:,.0f}** *and* **never overspend one slot**.
  The **"binding"** column tells you which of the two capped the size.

#### Step 7 — Build the portfolio
Take the **top {c.max_positions} BUY signals** by rank (your max positions). Because each risks
~£{risk_gbp:,.0f}, total risk ("heat") stays near **{c.max_positions*c.risk_per_trade*100:.0f}%**, under your
**{c.max_portfolio_heat*100:.0f}%** cap.
"""
    )

    with st.expander("📊 Worked example (using the maths above)"):
        st.markdown(
            f"""
Say a stock triggers a 🟢 BUY at **entry $100**, with an **ATR of $4** and the live rate at **{fx_rate:.2f}**:

1. Stop distance = **{c.atr_stop_mult:.0f} × $4 = $8** → **stop = $92**, **target = $100 + {c.reward_risk:.0f}×$8 = ${100 + c.reward_risk*8:.0f}**.
2. Risk budget = £{risk_gbp:,.0f} × {fx_rate:.2f} = **${risk_gbp*fx_rate:,.0f}**.
3. Risk-based shares = ${risk_gbp*fx_rate:,.0f} ÷ $8 = **{int(risk_gbp*fx_rate/8)} shares**.
4. Capital-slot shares = (£{c.capital_gbp:,.0f} × {fx_rate:.2f} ÷ {c.max_positions}) ÷ $100 = **{int(c.capital_gbp*fx_rate/c.max_positions/100)} shares**.
5. Buy the **smaller → {min(int(risk_gbp*fx_rate/8), int(c.capital_gbp*fx_rate/c.max_positions/100))} shares** (that constraint is the "binding" one).
"""
        )

    st.markdown(
        f"""
---
### Macro overlay — the market context (Phase 3, Layers A & B)
Before trusting a BUY, the app now checks the *weather*, not just the individual stock:

**Layer A — market-regime gate.** It reads free public gauges — the **S&P 500** and
**Nasdaq-100** trends, the **VIX** (fear gauge), the **semiconductor ETF (SMH)**, and the
**US 10-year yield** — and scores them into one of three regimes:
- 🟢 **RISK-ON** → BUYs at full size.
- 🟡 **CAUTION** → BUYs allowed but **sized down to 50%**.
- 🔴 **RISK-OFF** → **new BUYs paused** (they become HOLD). High beta is dangerous in a falling market.

**Layer B — event risk.** Scheduled 'landmines' that whip these stocks around:
- **Earnings** within **{c.earnings_block_days} days** → a BUY becomes HOLD (don't open a swing right before earnings).
- **Macro events** (CPI, FOMC, jobs) within **{c.macro_event_sizedown_days} days** → positions **sized down**, and tagged in the **flags** column.

This is exactly the "Micron dips on inflation/Iran news but recovers" instinct, encoded:
the app won't pile into high beta into a CPI print or a risk-off tape.

**Layer C — news sentiment (live).** For each shortlist stock the app pulls recent
headlines and scores them for a short-term trader — sentiment (−1…+1), a plain-English
read, the macro driver (inflation/rates/geopolitical/earnings), and an action bias. A
**strongly negative, material** news read turns a 🟢 BUY into a 🟡 HOLD; milder concerns
get flagged. Scoring uses the best engine available, in order: the **Claude API**
(`{c.claude_model}`, when `ANTHROPIC_API_KEY` / `ant auth login` is set) → **FinBERT**, a
free finance-trained model that runs locally (when installed) → a simple **keyword** scan —
so it always runs. The active engine is labelled on the Macro & News tab. All of this is
compiled in the **📰 Macro & News tab**, which you can **refresh on demand** or on a schedule.

**Two refresh modes.** *Refresh macro & news* re-pulls the regime, events, earnings and news
and rebuilds signals **on the prices already stored** — it's quick and is what the daily "did
anything change?" check uses, but it does **not** move the market-data date. *Pull fresh prices*
(and the full `python -m src.pipeline`) re-downloads the whole price history and recomputes
everything — that's what advances **market data through** to the latest trading day. On the
hosted app a **scheduled job (GitHub Actions)** runs the full pipeline daily before the open and
after the close, rebuilds the shared `seed.db` baseline and pushes it, so the live app redeploys
with current prices without anyone pressing a button. (Pressing *Pull fresh prices* in the hosted
app refreshes your current session only — the cloud filesystem is temporary.)

**Alerts.** Each run (before the open / after the close) emails a digest — or saves it to
`data/alerts/` if email isn't configured. Between runs, an **hourly news-shock check**
(`python -m src.shock`) scans for a big intraday move + fresh headline and alerts you.

---
"""
    )

    st.markdown(
        f"""
### 🛡️ Reliability & safety features
- **Confidence score** — each pick shows what % of independent checks agree (trend, medium-
  and short-term momentum, healthy RSI, news not against it). Higher = more sure.
- **Diversification cap** — the portfolio takes at most **{c.max_per_sector} picks per sector**,
  so your {c.max_positions} slots can't secretly become one big bet on (say) chips.
- **Steadier beta** — beta is nudged **{int(c.beta_shrink*100)}% of the way** to its raw value and
  the rest toward the market average (a standard technique) so a noisy estimate doesn't mislead.
- **Data checks** — stale or gap-filled tickers are dropped before they can produce a signal.
- **Circuit-breaker** — if trades closed today are down more than
  **{c.daily_loss_limit_pct*100:.0f}% of your capital**, the Track-record tab warns you to pause.
- **Track record + backtest** (📈 tab) — every recommendation is logged and scored over time
  (live scorecard), and you can replay the rules over ~{c.backtest_years} of history — including
  bear markets — to see if they have an edge. The backtest uses **trailing-stop + trend-break
  exits** (gap-aware), **filtered entries** (conviction, volume, relative strength, rising-market
  only), and a **robustness check** (a Monte-Carlo range for the edge + a stop-width sensitivity
  sweep) so a good result isn't just luck or one lucky setting. Everything is shown in **R**
  (multiples of your risk).

*Honest note: this reduces risk and measures the edge — it does not guarantee profit. Short-term
moves are close to random; the discipline (stops, sizing, diversification) is what protects you.*

---
"""
    )

    st.subheader("Dials you can ask to change")
    params = pd.DataFrame([
        {"Setting": "min_beta", "Now": c.min_beta, "What it does": "Minimum beta to make the shortlist (higher = only the wildest movers)"},
        {"Setting": "shortlist_size", "Now": c.shortlist_size, "What it does": "How many names the shortlist holds"},
        {"Setting": "min_price", "Now": c.min_price, "What it does": "Cheapest share price allowed"},
        {"Setting": "min_avg_dollar_volume", "Now": c.min_avg_dollar_volume, "What it does": "Minimum daily liquidity ($)"},
        {"Setting": "beta_window", "Now": c.beta_window, "What it does": "Trading days used to measure beta"},
        {"Setting": "rsi_overbought", "Now": c.rsi_overbought, "What it does": "RSI above this = HOLD (too hot to buy)"},
        {"Setting": "rsi_min_buy", "Now": c.rsi_min_buy, "What it does": "RSI below this = HOLD (momentum too weak)"},
        {"Setting": "atr_stop_mult", "Now": c.atr_stop_mult, "What it does": "Stop width = this × ATR (bigger = wider stop, fewer shares)"},
        {"Setting": "reward_risk", "Now": c.reward_risk, "What it does": "Target size as a multiple of the stop distance"},
        {"Setting": "risk_per_trade", "Now": c.risk_per_trade, "What it does": "Fraction of capital risked per trade"},
        {"Setting": "max_positions", "Now": c.max_positions, "What it does": "Most positions held at once"},
        {"Setting": "capital_gbp", "Now": c.capital_gbp, "What it does": "Total trading capital (£)"},
        {"Setting": "earnings_block_days", "Now": c.earnings_block_days, "What it does": "Downgrade BUY→HOLD if earnings within this many days"},
        {"Setting": "macro_event_sizedown_days", "Now": c.macro_event_sizedown_days, "What it does": "Size down if a CPI/FOMC/jobs event is this close"},
        {"Setting": "claude_model", "Now": c.claude_model, "What it does": "Model for news sentiment (Haiku = cheap; claude-opus-5 = max quality)"},
        {"Setting": "use_claude_news", "Now": c.use_claude_news, "What it does": "Use Claude for news; falls back to keywords if unavailable"},
        {"Setting": "shock_move_pct", "Now": c.shock_move_pct, "What it does": "Intraday % move that triggers the hourly news-shock alert"},
        {"Setting": "max_per_sector", "Now": c.max_per_sector, "What it does": "Max portfolio positions from one sector (diversification cap)"},
        {"Setting": "beta_shrink", "Now": c.beta_shrink, "What it does": "How much to trust raw beta vs nudge it toward the market (robustness)"},
        {"Setting": "daily_loss_limit_pct", "Now": c.daily_loss_limit_pct, "What it does": "Circuit-breaker: warn if today's realized losses exceed this % of capital"},
        {"Setting": "backtest_max_hold_days", "Now": c.backtest_max_hold_days, "What it does": "Time-based exit: close a trade after this many days"},
    ])
    # 'Now' mixes numbers, booleans and the model string — render as text so Arrow
    # doesn't try (and fail) to coerce the whole column to a number.
    params["Now"] = params["Now"].astype(str)
    st.dataframe(params, width="stretch", hide_index=True)
    st.caption("Want a change? Just tell me e.g. \"only show beta ≥ 2\", \"use a 3×ATR stop\", "
               "or \"risk 1% per trade\" — I'll update `src/config.py` and re-run.")
    st.info("These are **rules-based** signals from price data. Phase 3 adds a Claude news/"
            "sentiment layer that can nudge a BUY to HOLD when the headlines say caution.")
