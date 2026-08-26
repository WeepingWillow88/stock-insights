"""High-Beta Stock Insights dashboard (Phase 1 + 2).

Run with:  streamlit run app.py
(Populate data first with:  python -m src.pipeline)
"""
import datetime as dt
import os
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

# Seed the working DB on first run, and re-seed when a Cloud redeploy ships a snapshot covering
# a later market date than a stale in-container copy. (Shared with the pipeline — see db.py.)
from src import db as _db
from src import ledger as _ledger
_db.bootstrap_working_db(CONFIG.db_path, "data/seed.db", refresh_if_newer=True)

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


def _short_date(s):
    """'2026-08-19' -> 'Aug 19'; fall back to the raw string if it won't parse."""
    try:
        return pd.to_datetime(str(s)).strftime("%b %-d")
    except Exception:
        return str(s)


def _ledger_labels(ledger_df, tickers):
    """Map each ticker to a one-line summary of its history in the track-record ledger.

    Open positions win (you're holding it now); otherwise show the most recent close.
    Returns a list aligned to `tickers` ('' when the ticker has never been logged)."""
    if ledger_df is None or ledger_df.empty or "ticker" not in ledger_df.columns:
        return ["" for _ in tickers]
    labels = {}
    for tkr, grp in ledger_df.groupby("ticker"):
        openp = grp[grp["status"] == "open"]
        closed = grp[grp["status"] == "closed"]
        if not openp.empty:
            since = _short_date(openp.sort_values("record_date").iloc[-1]["record_date"])
            labels[tkr] = f"🟡 Holding since {since}"
        elif not closed.empty:
            last = closed.sort_values("exit_date").iloc[-1]
            r = float(last["r_multiple"]) if pd.notna(last["r_multiple"]) else 0.0
            won = str(last.get("outcome", "")).lower() == "win"
            tag = f"{'🟢 Won' if won else '🔴 Lost'} {r:+.2f}R · {_short_date(last['exit_date'])}"
            if len(closed) > 1:
                tag += f" · {len(closed)} trades"
            labels[tkr] = tag
    return [labels.get(t, "") for t in tickers]


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


def intraday_stale(iso):
    """True when it's a US trading day and the latest data is older than it should be — catches a
    dropped/late scheduled refresh even when it's under the coarse 36h `freshness()` threshold.

    The `last_updated` timestamp is naive UTC (written by the CI runner). We convert it to Eastern
    and flag two cases, independent of any CI cron:
      • morning miss  — market day underway (past ~10:00 ET) but the newest data is from a prior day
      • pre-close miss — it's past ~16:20 ET but the newest data is still the morning (pre-open) run
    Returns a short reason string (falsy when the data is as fresh as expected)."""
    try:
        from zoneinfo import ZoneInfo
        t = dt.datetime.fromisoformat(str(iso))
    except Exception:  # noqa: BLE001 - unparseable / no zoneinfo -> lean on the 36h check instead
        return ""
    et = ZoneInfo("America/New_York")
    now_et = dt.datetime.now(et)
    if now_et.weekday() >= 5:                       # weekend — no refresh expected
        return ""
    upd_et = t.replace(tzinfo=dt.timezone.utc).astimezone(et)
    afternoon_cut = now_et.replace(hour=15, minute=40, second=0, microsecond=0)  # ~pre-close time
    past_close_grace = now_et.hour > 16 or (now_et.hour == 16 and now_et.minute >= 20)  # >16:20 ET
    if past_close_grace and upd_et < afternoon_cut:
        return ("today's ~15-min-before-close refresh hasn't landed — you're seeing this morning's "
                "pre-open data")
    if now_et.hour >= 10 and upd_et.date() < now_et.date():
        return "today's data hasn't loaded yet — you're seeing a previous day's run"
    return ""

st.title("📈 High-Beta Stock Insights")
st.caption("Decision-support only. Not financial advice. You place every trade yourself.")

with st.expander("📖 How to read this dashboard", expanded=False):
    st.markdown(
        """
### What this app does
It scans the S&P 500 for **high-beta** stocks (ones that swing more than the market) that are
**trending up**, turns them into **buy / hold / sell** calls with exact **position sizing**,
checks the **market backdrop and news**, and keeps a **track record**. Hover any column's ℹ️ for
a plain-English definition; the **🧠 How it works** tab explains the full method, the buy/sell
rules, the sizing maths, and how the daily refresh works.

### The five tabs
- **🎯 Signals & sizing** — today's timestamped action list: **🟢 BUY these** (new positions to
  open) and **🔴 SELL these** (holdings to exit), with shares, safety-exit, profit target, £ at
  risk and a **Confidence %**.
- **📰 Macro & News** — the market mood, scheduled market-moving events, and per-stock news you
  can expand. The two refresh buttons live here.
- **📈 Track record** — **your open positions with today's action** (live trailing-stop level +
  a HOLD/SELL-today call), a **live scorecard** of closed calls with £ P&L, plus a **backtest** and
  the **circuit-breaker** warning.
- **📊 Screener** — the raw ranked candidate list plus a price chart for any name.
- **🧠 How it works** — the full method, the freshness/refresh explainer, and the settings.

### Key terms you'll see
| Term | Plain meaning |
|---|---|
| **Beta / "jumpiness"** | How much a stock moves vs the market. 2 ≈ twice as much, up *and* down |
| **Confidence %** | How many independent checks agree (trend, momentum, RSI, news). A BUY must clear a minimum bar |
| **RSI / "momentum"** | 0–100 gauge. >70 overheated, <30 beaten-down; 45–65 is the healthy buy zone |
| **Daily swing (ATR)** | Typical size of a day's move — sets how wide the safety-exit sits |
| **News mood** | 🔴/⚪/🟢 read of recent headlines (sources incl. **CNBC**; engine shown on the Macro & News tab) |
| **Post-earnings drift** | After a stock reports, a big beat the market gapped up on tends to keep drifting up for weeks (a big miss keeps sinking). Nudges Confidence up/down; a strong down-gap blocks a fresh BUY |
| **Short interest** | Shares bet against the stock, as % of float. High = squeeze fuel but crowded |
| **Implied vol (IV)** | The move options are pricing in. IV well above recent *realized* moves = event brewing |
| **R (risk multiple)** | Track-record unit: +1R = made what you risked, −1R = hit your stop |
| **Regime** | Whether market conditions favour high-beta (RISK-ON) or not (RISK-OFF) |
| **Track record (per signal)** | On each *All signals* row: this ticker's history in the ledger — 🟡 *Holding* an open position, or 🟢/🔴 the last closed result in R. It reflects a *past* call, so it can differ from today's fresh signal |

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
bt_year_df = load_table("backtest_by_year")
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
_intraday_reason = intraday_stale(_last_updated)
if _intraday_reason:
    st.error(_fresh_line + f"  —  ⚠️ **{_intraday_reason}.** A scheduled refresh was likely dropped "
             "or delayed. Open the 📰 Macro & News tab and hit **⬇️ Pull fresh prices** to update now.")
elif upd_stale:
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
    st.session_state.setdefault("wf_capital", float(CONFIG.capital_gbp))
    st.session_state.setdefault("wf_maxpos", int(CONFIG.max_positions))
    with st.form("wf_form"):
        st.number_input(
            "Money to trade (£)", min_value=1000.0, step=1000.0, format="%.0f", key="wf_capital",
            help="Your total trading pot. Change it and hit Recalculate — the shares, £-at-risk per "
                 "trade, the total-risk cap and the deployed / cash-free tiles all recompute.")
        st.number_input(
            "Max positions at once", min_value=1, max_value=30, step=1, key="wf_maxpos",
            help="Most stocks to hold at once. Fewer = each position is bigger; more = each is "
                 "smaller (the pot is split into that many equal slots).")
        _recalced = st.form_submit_button("♻️ Recalculate", use_container_width=True)
    eff_capital = float(st.session_state["wf_capital"])
    eff_maxpos = int(st.session_state["wf_maxpos"])
    st.caption(f"Most you'll risk per trade: **{CONFIG.risk_per_trade * 100:.1f}%** "
               f"(~£{eff_capital * CONFIG.risk_per_trade:,.0f}) — the most you'd *lose* if a "
               "stop-loss is hit, not how much you put in.")
    st.caption(f"Total-risk ceiling: **{CONFIG.max_portfolio_heat * 100:.0f}%** of your money "
               "('heat' = everything at risk across all open trades combined).")
    st.caption(f"£→$ rate used: **{fx_rate:.4f}**")
    _wf_changed = (eff_capital != float(CONFIG.capital_gbp)) or (eff_maxpos != int(CONFIG.max_positions))
    if _wf_changed:
        st.info(f"**What-if:** £{eff_capital:,.0f} / {eff_maxpos} slots. Sizing & picks below are "
                "recalculated for these — but this is **display-only**: your track record and the "
                "automated daily run are untouched. To make it the default, set the `CAPITAL_GBP` / "
                "`MAX_POSITIONS` repo variables.")
    st.markdown("---")

# Apply the capital / max-positions what-if to the displayed signals (offline, no re-run, no
# ledger write). A no-op at the default settings; needs the stop_dist/eff_mult helper columns.
if not signals_df.empty and {"stop_dist", "eff_mult"}.issubset(signals_df.columns):
    from src import signals as _signals
    signals_df = _signals.resize_and_select(signals_df, load_table("prices"), CONFIG,
                                             eff_capital, eff_maxpos, fx_rate)
elif _wf_changed:
    st.sidebar.warning("Recalculate needs a fresh data pull first — open **📰 Macro & News → "
                       "⬇️ Pull fresh prices**, then adjust these.")
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

        capital_usd = eff_capital * fx_rate
        # Portfolio summary reflects your ACTUAL open holdings (the running book), NOT today's fresh
        # BUY picks — otherwise it reads £0 on a no-new-buys day while you're holding positions.
        _open = (ledger_df[ledger_df["status"] == "open"].copy()
                 if (not ledger_df.empty and "status" in ledger_df.columns) else ledger_df.iloc[0:0])
        n_open = int(len(_open))
        _prices_now = load_table("prices")
        _last_close = (_prices_now.sort_values("date").groupby("ticker")["close"].last()
                       if not _prices_now.empty else pd.Series(dtype=float))
        total_pos_gbp = 0.0
        for _, _r in _open.iterrows():
            _cur = float(_last_close.get(_r["ticker"], _r.get("entry") or 0) or 0)
            total_pos_gbp += float(_r.get("shares") or 0) * _cur
        total_pos_gbp = total_pos_gbp / fx_rate if fx_rate else 0.0
        total_risk_gbp = (float(pd.to_numeric(_open["risk_gbp"], errors="coerce").fillna(0).sum())
                          if n_open else 0.0)
        cash_free_gbp = max(eff_capital - total_pos_gbp, 0.0)
        deployed_pct = total_pos_gbp / eff_capital * 100 if eff_capital else 0
        heat_pct = total_risk_gbp / eff_capital * 100 if eff_capital else 0

        money_cfg = {
            "price": st.column_config.NumberColumn("Price", format="$%.2f",
                     help="Latest share price, in US dollars."),
            "entry": st.column_config.NumberColumn("Buy at", format="$%.2f",
                     help="The signal price (last close when the recommendation was made)."),
            "buy_up_to": st.column_config.NumberColumn("Buy up to", format="$%.2f",
                     help="Don't chase above this. It's the buy price plus ¼ of the distance to your "
                          "stop — pay more and your reward-to-risk gets too thin. If the live price is "
                          "already above it, skip the trade or wait for a pullback."),
            "status": st.column_config.TextColumn("Buyable?",
                     help="✅ = the latest price is still within range (at or below 'Buy up to' and "
                          "still a fresh BUY). ⚠️ = it's run past the entry or no longer rates BUY — "
                          "the clean entry has passed, so skip it or wait for a pullback."),
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
            "pead": st.column_config.TextColumn("Post-earnings",
                     help="Post-earnings drift: after a stock reports, a big beat the market "
                          "rewarded with a gap-up tends to keep drifting up for weeks (and a big "
                          "miss keeps sinking). 'positive' nudges Confidence up; 'negative' nudges "
                          "it down, and a strong down-gap blocks a fresh BUY. Blank = no recent "
                          "report or the reaction was too small to matter."),
            "conviction": st.column_config.NumberColumn("Confidence", format="%d%%",
                     help="How many independent checks agree (trend, medium- & short-term "
                          "momentum, healthy RSI, news not against it), then nudged up or down for "
                          "a fresh post-earnings drift. Higher = more sure."),
            "sector": st.column_config.TextColumn("Sector",
                     help="Industry group. The portfolio caps how many picks come from one "
                          "sector so your positions aren't secretly one big bet."),
            "flags": st.column_config.TextColumn("Flags",
                     help="Extra context: upcoming earnings, a market-moving event soon, a notable "
                          "news read, high short interest (squeeze fuel), or rich options "
                          "(IV pricing a big move). Full IV/short table on the Macro & News tab."),
            "reason": st.column_config.TextColumn("Why", width="large",
                     help="Plain-English reason behind this call. A BUY must also beat the S&P "
                          "(relative strength), trade on above-average volume, and clear the "
                          "confidence bar."),
        }

        # ---- Today's action list: what to BUY and what to SELL ----
        st.subheader("Today's suggested portfolio")
        _rk = "" if (_run_kind is None or pd.isna(_run_kind)) else str(_run_kind)
        if "pre-close" in _rk:
            _run_label = "pre-close run (~15 min before the bell)"
        elif "pre-open" in _rk:
            _run_label = "pre-open run (morning)"
        elif _rk:
            _run_label = f"{_rk} run"
        else:
            _run_label = ""
        st.caption(f"🕒 Recommendations as of **{upd_pretty}**" + (f" ({upd_ago})" if upd_ago else "")
                   + (f" · {_run_label}" if _run_label else "")
                   + " — re-check the live price before you trade.")

        # BUY these — the positions actually OPENED on the latest run date (today's buys). They
        # persist for the whole day (both runs) and refresh out to 'your open positions' next day,
        # rather than vanishing the instant they're logged.
        _today_open = ledger_df.iloc[0:0]
        if not ledger_df.empty and {"record_date", "status"}.issubset(ledger_df.columns):
            _today_open = ledger_df[(ledger_df["record_date"].astype(str) == str(run_date))
                                    & (ledger_df["status"].isin(["open", "sell_pending"]))].copy()
        _sig_extra = (signals_df[[c for c in ["ticker", "sector", "conviction", "beta", "price",
                                  "signal", "flags", "reason"] if c in signals_df.columns]]
                      if not signals_df.empty else pd.DataFrame(columns=["ticker"]))
        new_buys = (_today_open.merge(_sig_extra, on="ticker", how="left")
                    if not _today_open.empty else _today_open)
        st.markdown(f"**🟢 BUY these — new positions recommended today ({run_date}):**")
        if new_buys.empty:
            st.info("No new BUYs recommended today. (Names you already hold appear under "
                    "**📌 your open positions** on the Track record tab, not here.)")
        else:
            new_buys = new_buys.copy()
            new_buys["buy_up_to"] = (
                new_buys["entry"].astype(float)
                + CONFIG.max_chase_frac * (new_buys["entry"].astype(float) - new_buys["stop"].astype(float))
            ).round(2)

            def _chase_status(r):
                now = r.get("price")
                if pd.notna(now) and float(now) > float(r["buy_up_to"]):
                    return "⚠️ ran past — don't chase"
                if r.get("signal") and str(r.get("signal")) != "BUY":
                    return f"⚠️ now rates {r.get('signal')}"
                return "✅ still buyable"
            new_buys["status"] = new_buys.apply(_chase_status, axis=1)
            pcols = ["ticker", "sector", "conviction", "beta", "entry", "buy_up_to", "price",
                     "status", "stop", "target", "shares", "risk_gbp", "flags", "reason"]
            pcols = [c for c in pcols if c in new_buys.columns]
            st.dataframe(new_buys[pcols], width="stretch", hide_index=True, column_config=money_cfg)
            st.caption("**Buy at** = the signal price · **Buy up to** = the most it's worth paying "
                       "today · **Price** = latest market price · **Buyable?** = ✅ still in range, or "
                       "⚠️ it's run past the entry / no longer a fresh BUY, so skip it or wait for a "
                       "pullback.")

        # SELL these — holdings the exit rules flagged today; they close at the next session's price.
        _pending = _ledger.pending_sells_view(load_table("prices"), CONFIG)
        st.markdown("**🔴 SELL these — exit today (a stop / trend / time exit has triggered):**")
        if _pending.empty:
            st.caption("Nothing to sell today — your open positions are all still healthy.")
        else:
            st.dataframe(_pending, width="stretch", hide_index=True, column_config={
                "ticker": st.column_config.TextColumn("Ticker"),
                "shares": st.column_config.NumberColumn("Shares", format="%d"),
                "entry": st.column_config.NumberColumn("Entry", format="$%.2f"),
                "current": st.column_config.NumberColumn("Now", format="$%.2f"),
                "flagged": st.column_config.TextColumn("Flagged"),
                "reason": st.column_config.TextColumn("Why sell"),
                "unrealised_r": st.column_config.NumberColumn("R so far", format="%+.2f"),
                "pnl_gbp": st.column_config.NumberColumn("P&L so far (£)", format="£%d"),
            })
            st.caption("Sell these at your next opportunity. They move to **Closed trades** (Track "
                       "record) once filled at the next session's price — that's the two-phase, "
                       "manual-sell flow.")

        # ---- Your portfolio right now (your ACTUAL holdings — all £, no delta arrows) ----
        st.markdown("**Your portfolio right now** (what you currently hold):")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("New BUYs today", int(len(new_buys)),
                  help="New positions suggested to open today (after all the gates/caps).")
        c2.metric("Positions held", f"{n_open} / {eff_maxpos}",
                  help="Open positions you're currently holding, vs your maximum.")
        c3.metric("Capital deployed", f"£{total_pos_gbp:,.0f}",
                  help=f"Value of your open holdings at the latest price — {deployed_pct:.0f}% of your £{eff_capital:,.0f}.")
        c4.metric("Portfolio heat", f"£{total_risk_gbp:,.0f}",
                  help=f"£ at risk across your open positions if their stops hit — {heat_pct:.1f}% of "
                       f"capital (ceiling {CONFIG.max_portfolio_heat*100:.0f}%). Money *at risk*, not invested.")
        c5.metric("Cash free", f"£{cash_free_gbp:,.0f}", help="Uninvested cash.")
        st.caption(f"**{deployed_pct:.0f}%** of your £{eff_capital:,.0f} deployed across "
                   f"**{n_open}** open position(s) · **{heat_pct:.1f}%** at risk "
                   f"(cap {CONFIG.max_portfolio_heat*100:.0f}%) · **{max(100 - deployed_pct, 0):.0f}%** in cash.")

        # ---- One-line market-regime badge (full detail on 📰 Macro & News) ----
        if not regime_df.empty:
            _rl = str(regime_df.iloc[0]["label"])
            _gate = {"RISK-ON": "new BUYs at full size",
                     "CAUTION": "new BUYs at reduced size — be selective",
                     "RISK-OFF": "new BUYs paused"}.get(_rl, "")
            (st.success if _rl == "RISK-ON" else st.warning if _rl == "CAUTION" else st.error)(
                f"Market regime: **{_rl}** → {_gate}.  Full picture on the **📰 Macro & News** tab.")

        st.markdown("---")
        st.subheader("All signals")
        types = st.multiselect("Show signal types", ["BUY", "HOLD", "SELL"],
                               default=["BUY", "HOLD", "SELL"])
        view = sig[sig["signal"].isin(types)].copy()
        view["signal"] = view["signal"].map(SIG_EMOJI).fillna(view["signal"])

        # ---- Track-record link: today's signal is a fresh call; the ledger is the honest
        # history of past calls. They intentionally differ (a BUY can be logged, closed as a
        # win, and rate HOLD again days later). Surface any ledger trade so the two tabs
        # don't look contradictory. Open positions take precedence, else the latest close.
        view["ledger"] = _ledger_labels(ledger_df, view["ticker"])
        money_cfg["ledger"] = st.column_config.TextColumn("Track record",
                 help="This ticker's history in the 📈 Track record ledger. 'Holding' = an open "
                      "logged position; 'Won/Lost …R' = the most recent closed trade. Blank means "
                      "the app has never logged a trade here. It reflects *past* calls, so it can "
                      "differ from today's fresh signal on the left.")

        acols = ["rank", "ticker", "signal", "conviction", "beta", "price", "stop",
                 "target", "shares", "pos_value_usd", "risk_gbp", "news", "pead", "ledger",
                 "flags", "reason"]
        acols = [c for c in acols if c in view.columns]
        st.dataframe(view[acols], width="stretch", hide_index=True, column_config=money_cfg)
        st.caption("Sizing (shares/stop/target/risk) is shown for BUY signals only. "
                   "Stop = entry − 2×ATR · Target = 2:1 reward:risk · Risk £ kept ≤ £750. "
                   "**Track record** links each row to the ledger of past calls (📈 tab) — it can "
                   "differ from today's signal because that's a *fresh* call, not the logged trade.")

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
    with top[1]:
        if st.button("🔄 Refresh signals & regime", use_container_width=True,
                     help="Re-pull the market regime, macro events and earnings and rebuild "
                          "signals on CACHED prices — quick (~1 min), does NOT advance the 'market "
                          "data through' date. News sentiment is REUSED from the last scheduled run "
                          "(no new Claude calls); fresh Claude scoring runs on the 2×/day schedule. "
                          "Use ‘Pull fresh prices’ to advance the price date."):
            from src import pipeline
            with st.spinner("Rebuilding signals (regime, events, earnings; reusing cached news)…"):
                try:
                    res = pipeline.refresh_macro_news(CONFIG, reuse_news=True)
                    load_table.clear()
                    st.success(f"Signals & regime refreshed — regime {res['regime']}. "
                               f"News reused from the last scheduled run (no Claude spend).")
                    st.rerun()
                except SystemExit as e:
                    st.error(str(e))
                except Exception as e:  # noqa: BLE001
                    st.error(f"Refresh failed: {e}")
    with top[2]:
        if st.button("⬇️ Pull fresh prices", use_container_width=True,
                     help="Re-download the full price history (~500 stocks) and rebuild "
                          "everything — this is what advances 'market data through' to the latest "
                          "trading day. Slower (~3–5 min). News sentiment is REUSED from the last "
                          "scheduled run (no new Claude calls). Note: on the hosted app this updates "
                          "your current session only; the scheduled daily job updates the shared "
                          "baseline everyone sees."):
            from src import pipeline
            with st.spinner("Downloading fresh prices for the full universe and rebuilding — "
                            "this can take a few minutes…"):
                try:
                    pipeline.run_pipeline(CONFIG, send_digest=False, reuse_news=True)
                    load_table.clear()
                    st.success("Fresh prices pulled — market data advanced to the latest trading day.")
                    st.rerun()
                except SystemExit as e:
                    st.error(str(e))
                except Exception as e:  # noqa: BLE001
                    st.error(f"Price refresh failed: {e}")
    st.caption("Tip: the hosted app auto-refreshes twice daily via a scheduled job (GitHub Actions) "
               "before the open and after the close — that's when the AI news scoring runs. "
               "**Refresh signals & regime** rebuilds signals on cached prices; **Pull fresh prices** "
               "re-downloads price history and advances the market-data date. Both reuse the latest "
               "scheduled news read (no extra AI cost). Locally you can also run `python -m src.pipeline`.")

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
        def _fmt(v, suffix="", dp=1):
            return f"{float(v):.{dp}f}{suffix}" if pd.notna(v) else "—"
        m = st.columns(3)
        m[0].metric("VIX (fear gauge)", _fmt(r.get('vix'), dp=1),
                    help="Under 20 = calm, 20–30 = jittery, over 30 = fearful.")
        m[1].metric("S&P 500 vs its 50-day avg", _fmt(r.get('spy_vs_50d'), '%', 1),
                    help="Positive = market in an uptrend.")
        m[2].metric("US 10-year yield", _fmt(r.get('us10y'), '%', 2),
                    help="Rising fast tends to pressure high-beta / growth stocks.")
        with st.expander("What went into this call?"):
            for note in str(r.get("notes", "")).split(" • "):
                if note:
                    st.markdown(f"- {note}")

    # ---- Upcoming events ----
    st.markdown("### 📅 Scheduled events that move the whole market")
    if events_df.empty or "label" not in events_df.columns or events_df["label"].isna().all():
        from src.events import MACRO_EVENTS
        _last_seeded = MACRO_EVENTS[-1][0]
        if dt.date.today() > dt.date.fromisoformat(_last_seeded):
            st.warning("⚠️ The built-in macro-event calendar has run out (seeded through "
                       f"**{_last_seeded}**), so event-risk sizing is paused until it's topped "
                       "up in `src/events.py`.")
        else:
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
        st.caption(f"Each stock's recent headlines (Finnhub / Google News, with **CNBC** always "
                   f"included), scored for a short-term trader (sentiment engine: **{src}**). "
                   f"Expand a stock to read the headlines yourself.")
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

    # ---- B2/B3: options-implied vol + short-interest overlay ----
    st.markdown("### 🔎 Squeeze & volatility watch")
    if signals_df.empty or "iv_atm" not in signals_df.columns:
        st.caption("No options / short-interest data yet — run a refresh or the pipeline.")
    else:
        _ex = signals_df.copy()
        _ex = _ex[_ex[["short_pct_float", "iv_atm"]].notna().any(axis=1)]
        if _ex.empty:
            st.caption("No options / short-interest data available right now.")
        else:
            _ex["short_pct"] = (_ex["short_pct_float"] * 100).round(1)
            _ex["iv_pct"] = (_ex["iv_atm"] * 100).round(0)
            _ex = _ex.sort_values("short_pct_float", ascending=False)
            st.caption("Two extras that move high-beta names hard: **short interest** (heavily "
                       "shorted = squeeze fuel, but crowded) and **implied vs realized volatility** "
                       "(options pricing a bigger move than the stock has been making — often a "
                       "coming event). Shown for context; they flag a name but don't block a BUY.")
            st.dataframe(
                _ex[["ticker", "signal", "short_pct", "short_ratio", "iv_pct", "iv_vs_realized"]].head(25),
                width="stretch", hide_index=True,
                column_config={
                    "signal": st.column_config.TextColumn("Call"),
                    "short_pct": st.column_config.NumberColumn("Short % float", format="%.1f%%",
                        help="Shares sold short as a % of tradable float. High = squeeze potential and a crowded short."),
                    "short_ratio": st.column_config.NumberColumn("Days to cover", format="%.1f",
                        help="Days of average volume for shorts to buy back — higher = more squeeze fuel."),
                    "iv_pct": st.column_config.NumberColumn("Implied vol", format="%d%%",
                        help="At-the-money options' annualised implied volatility — the move the market is pricing in."),
                    "iv_vs_realized": st.column_config.NumberColumn("IV ÷ realized", format="%.2f",
                        help="Implied vs recent realized vol. Well above 1 = options pricing a much bigger move than the stock has made (often pre-earnings)."),
                })

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
            "- **Profit / loss (£)** — in the closed-trades table, the actual £ you'd have made or "
            "lost on the recommended number of shares, net of modelled costs. It's simply "
            "**R × the £ you risked** on that trade.\n"
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
            "- **How trades are closed** — a **trailing stop** (ratchets up as the trade runs, locking "
            "in gains) or a **trend break** (a close below the trend average), with a time limit as a "
            "backstop. This is the exit the live scorecard scores, so it matches how you'd manage it.\n"
            "- **Timed out** — the share of trades that hit neither the trailing stop nor a trend break "
            "and were closed on the time limit (the 'don't hold too long' rule).")

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
        # D3: let the live results referee the backtest
        if not bt_summary_df.empty and pd.notna(bt_summary_df.iloc[0].get("expectancy_r")):
            _bt = float(bt_summary_df.iloc[0]["expectancy_r"])
            st.caption(f"📏 **Live vs backtest:** live is **{ls['expectancy_r']:+.2f}R/trade** vs the "
                       f"backtest's **{_bt:+.2f}R**. Over enough live trades these should converge — a "
                       "big, persistent gap means the edge is decaying (or the live sample is still tiny).")

    if not ledger_df.empty:
        opn = ledger_df[ledger_df["status"] == "open"]
        st.markdown(f"### 📌 Your open positions — today's action ({len(opn)} held)")
        if opn.empty:
            st.caption("No open positions right now.")
        else:
            ov = _ledger.open_positions_view(load_table("prices"), CONFIG)
            if ov.empty:
                st.dataframe(opn[["ticker", "record_date", "entry", "stop", "target", "risk_gbp"]],
                             width="stretch", hide_index=True)
            else:
                _sell = ov[ov["action"] == "SELL"]
                if not _sell.empty:
                    st.warning("🔴 **Consider selling today:** " + " · ".join(
                        f"**{r.ticker}** ({r.reason})" for r in _sell.itertuples()))
                _asof = ov["as_of"].iloc[0] if "as_of" in ov.columns else "?"
                st.caption(f"Unrealised **£{float(ov['pnl_gbp'].sum()):,.0f}** across {len(ov)} open · "
                           f"prices as of {_asof}. Keep the **trailing stop** as a stop-loss order at "
                           "your broker — the app only checks at the scheduled refreshes, so the broker "
                           "order is what protects you intraday.")
                ovcols = [c for c in ["ticker", "shares", "days_held", "entry", "current",
                                      "trail_stop", "target", "unrealised_r", "pnl_gbp", "action"]
                          if c in ov.columns]
                st.dataframe(ov[ovcols], width="stretch", hide_index=True, column_config={
                    "ticker": st.column_config.TextColumn("Ticker"),
                    "shares": st.column_config.NumberColumn("Shares", format="%d"),
                    "days_held": st.column_config.NumberColumn("Days held", format="%d"),
                    "entry": st.column_config.NumberColumn("Entry", format="$%.2f"),
                    "current": st.column_config.NumberColumn("Now", format="$%.2f",
                        help="Latest close in the data (see the 'as of' date) — re-check the live price."),
                    "trail_stop": st.column_config.NumberColumn("Trailing stop", format="$%.2f",
                        help="Sell if it trades here. Ratchets up as the stock rises; never below the "
                             "initial stop. Place this as a stop-loss order at your broker."),
                    "target": st.column_config.NumberColumn("Target", format="$%.2f",
                        help="Reference profit target (the trailing stop usually manages the exit)."),
                    "unrealised_r": st.column_config.NumberColumn("Unreal. R", format="%+.2f"),
                    "pnl_gbp": st.column_config.NumberColumn("Unreal. £", format="£%d",
                        help="Unrealised profit/loss so far on the recommended shares."),
                    "action": st.column_config.TextColumn("Today",
                        help="HOLD = keep it; SELL = a trailing-stop / trend-break / time exit has triggered."),
                })
        cl = ledger_df[ledger_df["status"] == "closed"].copy()
        if not cl.empty:
            # Actual £ P&L on the recommended share count = R × the £ risked (net of modelled costs).
            cl["pnl_gbp"] = (cl["r_multiple"].astype(float) * cl["risk_gbp"].astype(float)).round(0)
            cl = cl.sort_values("exit_date", ascending=False)
            total_pnl = float(cl["pnl_gbp"].sum())
            st.markdown(f"**Closed trades** — net **£{total_pnl:,.0f}** across {len(cl)} closed:")
            ccols = [c for c in ["ticker", "record_date", "exit_date", "shares", "entry",
                                 "exit_price", "outcome", "r_multiple", "pnl_gbp"] if c in cl.columns]
            st.dataframe(cl[ccols], width="stretch", hide_index=True, column_config={
                "ticker": st.column_config.TextColumn("Ticker"),
                "record_date": st.column_config.TextColumn("Opened"),
                "exit_date": st.column_config.TextColumn("Closed"),
                "shares": st.column_config.NumberColumn("Shares", format="%d",
                    help="Share count from the original recommendation."),
                "entry": st.column_config.NumberColumn("Entry", format="$%.2f"),
                "exit_price": st.column_config.NumberColumn("Exit", format="$%.2f"),
                "outcome": st.column_config.TextColumn("Result"),
                "r_multiple": st.column_config.NumberColumn("R", format="%+.2f",
                    help="Profit/loss in multiples of the amount risked (+1R = made what you risked)."),
                "pnl_gbp": st.column_config.NumberColumn("Profit / loss (£)", format="£%d",
                    help="Actual £ you'd have made/lost on the recommended shares, net of modelled trading costs."),
            })
            st.caption("Profit/loss is what the recommended share count would have made or lost, "
                       "in £, net of modelled trading costs. Each trade was resolved under the "
                       "exit model live at the time it closed; a few early trades pre-date the "
                       "current trailing-stop/trend-break model (which lets winners run further), "
                       "so their results are the honest record of what was called then, not a "
                       "re-simulation under today's rules.")

    st.markdown("---")
    st.markdown(f"### 🧪 Backtest — how the rules did over ~{CONFIG.backtest_years}")
    st.caption("Uses the **upgraded rules** — trailing-stop + trend-break exits (gap-aware), entries "
               "filtered for conviction, volume, relative strength and a rising market — with "
               "**realistic vol-scaled costs** and an **out-of-sample** check below.")
    # Detect a hosted deployment: Streamlit Community Cloud checks the repo out under /mount/src;
    # (legacy Heroku set DYNO). The heavy multi-year backtest download is disabled there.
    on_cloud = os.path.isdir("/mount/src") or bool(os.environ.get("DYNO"))
    bcols = st.columns([3, 1])
    with bcols[1]:
        if on_cloud:
            st.button("▶️ Run backtest", use_container_width=True, disabled=True,
                      help="Disabled on the hosted app — it downloads years of data and would "
                           "overload the server. Run it locally (python -m src.backtest); results "
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
                        "server). Run `python -m src.backtest` and the results ship in the snapshot.")
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
            eq = eq.set_index("entry_date")
            eq["drawdown_%"] = (eq["equity"] / eq["equity"].cummax() - 1.0) * 100
            st.markdown("**Drawdown over the backtest** — how far below its own peak the strategy sat")
            st.area_chart(eq["drawdown_%"], height=220)
            st.caption("This is the honest picture of the ride: each dip is a losing stretch, in % of "
                       "the (compounding) account. The headline numbers to trust are the win rate, "
                       "average edge (R) and profit factor above.")
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

        # D2 — out-of-sample stability (time split) + per-year edge
        _oe, _or = s.get("oos_earlier_exp_r"), s.get("oos_recent_exp_r")
        if _oe is not None and _or is not None:
            st.markdown("**Does the edge hold out-of-sample?** (a time split — not a re-shuffle of the same trades)")
            oc = st.columns(2)
            oc[0].metric("Earlier ~70% of history", f"{float(_oe):+.3f}R/trade")
            oc[1].metric(f"Recent ~30% (from {str(s.get('oos_split_date'))[:7]})", f"{float(_or):+.3f}R/trade",
                         help=f"{int(s.get('oos_recent_trades') or 0)} trades in the slice the earlier period never informed.")
            st.caption("If the recent, unseen slice is still positive and close to the earlier one, the "
                       "edge is stable rather than a relic of one era.")
        if not bt_year_df.empty:
            st.markdown("**Edge by year** — steady, or lumpy?")
            _by = bt_year_df.copy()
            _per_r = CONFIG.risk_per_trade * CONFIG.capital_gbp  # £ per 1R (≈ £750)
            _by["pnl_gbp"] = (_by["expectancy_r"].astype(float) * _by["trades"].astype(float)
                              * _per_r).round(0)
            st.dataframe(_by, width="stretch", hide_index=True, column_config={
                "year": st.column_config.TextColumn("Year"),
                "trades": st.column_config.NumberColumn("Trades"),
                "expectancy_r": st.column_config.NumberColumn("Avg trade (R)", format="%.3f"),
                "win_rate": st.column_config.NumberColumn("Win rate", format="%.1f%%"),
                "pnl_gbp": st.column_config.NumberColumn("Profit / loss (£)", format="£%d",
                    help=f"Total R for the year × £{_per_r:,.0f} risked per trade. This is the RULES' "
                         "aggregate — the backtest takes every qualifying setup across the universe "
                         "(far more than your 8-position account holds at once), so read it as the "
                         "edge's shape by year, not a single account's yearly return."),
            })
            st.caption("High-beta momentum makes money in trending years and bleeds in choppy/bear ones "
                       "(e.g. 2018, 2022) — expected, and exactly why the regime gate and sizing exist. "
                       "The £ column assumes ~£{:,.0f} risk per trade across *all* the year's setups "
                       "(more than 8 slots can hold), so it sizes the edge, not a live account.".format(_per_r))

        with st.expander("⚠️ Read this before trusting the backtest"):
            st.markdown(
                "- **Survivorship bias:** uses today's S&P 500 — failed companies are excluded, "
                "so real-world results would be somewhat worse.\n"
                "- **Technicals only:** the news + macro layers aren't replayed (no historical news feed) — "
                "the *live scorecard* above does include them.\n"
                f"- **Realistic, vol-scaled costs:** a base {CONFIG.backtest_cost_pct*100:.1f}% plus an "
                "extra slice proportional to each name's daily swing (jumpier stocks cost more to "
                "trade) — the same cost the live scorecard applies. Stops are **gap-aware** (filled at "
                "the open if price gaps below), so overnight-gap losses are captured.\n"
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
Start with the **S&P 500** (a reliable, liquid list). Can be widened to the **S&P 1500** (adds
mid- and small-caps — more high-beta candidates; the liquidity filters still prune the illiquid
ones) via the `universe_scope` setting.

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
| 🟢 **BUY** | Up-trend **and** positive momentum **and** healthy RSI (**{c.rsi_min_buy:.0f}–{c.rsi_overbought:.0f}**) — **and** it clears three quality gates: **beating the S&P** (relative strength), **above-average volume**, and **confidence ≥ {c.min_conviction}%**. These mirror the backtested rules, so a live BUY is the same setup the backtest validated. |

#### Step 6 — Size the position (for BUY signals)
Your rules drive the maths:
- **Money at risk per trade** = {c.risk_per_trade*100:.1f}% of £{c.capital_gbp:,.0f} = **£{risk_gbp:,.0f}** (converted to USD at the live rate).
- **Stop-loss** = entry − **{c.atr_stop_mult:.0f} × ATR** (a wide stop suits volatile names).
- **Target** = entry + **{c.reward_risk:.0f} ×** the stop distance (a **{c.reward_risk:.0f}:1** reward-to-risk trade).
- **Shares to buy** = the **smaller** of:
  1. `£{risk_gbp:,.0f} at risk ÷ stop distance` (risk-based), and
  2. `(£{c.capital_gbp:,.0f} ÷ {c.max_positions} positions) ÷ entry price` (equal-weight capital slot).

  Taking the smaller means you **never risk more than £{risk_gbp:,.0f}** *and* **never overspend one slot**.
- **Don't chase (Buy up to)** — the "Buy at" price is the signal's last close; by the time you look,
  the live price may have moved. **Buy up to = entry + {c.max_chase_frac:.0%} of the stop distance**
  (≈ half an ATR) is the most it's worth paying that day. Above it, your fixed stop makes the
  reward-to-risk too thin — skip it or wait for a pullback.
- **Managing the exit** — the initial stop is your safety net, but the trade is then **trailed**: as it
  runs, ratchet the stop up to **{c.trail_atr_mult:.1f} × ATR below the highest close so far**, and also
  **exit on a close below its {c.trend_exit_sma}-day average** (a broken trend). The 2:1 target is a
  reference, not a hard ceiling — trailing lets winners run and cuts losers early. The **track record
  scores trades exactly this way**, so the live scorecard reflects the rules you'd actually follow.
  Each row in the **All signals** table carries a **Track record** tag linking it to this ledger
  (🟡 *Holding*, or 🟢/🔴 the last closed result in R). That's a *past* call and can legitimately differ
  from today's fresh signal — a name can be bought, closed as a win, and rate HOLD again days later.

#### Step 7 — Build the portfolio
Fill up to **{c.max_positions} positions** from the BUY signals by rank, subject to three guards:
- **Sector cap** — at most **{c.max_per_sector} per sector**.
- **Correlation cap** — skip a pick moving ≥ **{c.max_position_correlation:.0%}** in lockstep with one already
  held, so your slots aren't secretly one macro bet (real diversification, not just different tickers).
- **Heat ceiling** — total £-at-risk stays under **{c.max_portfolio_heat*100:.0f}%** of capital.

Sizes are **edge-weighted**: higher-confidence setups get closer to full size, the lowest-confidence
ones as little as **{c.edge_size_floor:.0%}** — putting more capital where the edge is stronger.
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
**Nasdaq-100** trends, the **VIX** (fear gauge), the **semiconductor ETF (SMH)**, the **US 10-year
yield**, **high-yield credit (HYG)** (credit tends to crack before equities), and **market breadth**
(how much of the universe is actually in an uptrend, not just the megacaps) — and scores them into
one of three regimes:
- 🟢 **RISK-ON** → BUYs at full size.
- 🟡 **CAUTION** → BUYs allowed but **sized down to 50%**.
- 🔴 **RISK-OFF** → **new BUYs paused** (they become HOLD). High beta is dangerous in a falling market.

**Layer B — event risk.** Scheduled 'landmines' that whip these stocks around:
- **Earnings** within **{c.earnings_block_days} days** → a BUY becomes HOLD (don't open a swing right before earnings).
- **Macro events** (CPI, FOMC, jobs) within **{c.macro_event_sizedown_days} days** → positions **sized down**, and tagged in the **flags** column.

This is exactly the "Micron dips on inflation/Iran news but recovers" instinct, encoded:
the app won't pile into high beta into a CPI print or a risk-off tape.

**Layer C — news sentiment (live).** For each shortlist stock the app pulls recent
headlines (Finnhub / Google News, with **CNBC** coverage always blended in) and scores
them for a short-term trader — sentiment (−1…+1), a plain-English
read, the macro driver (inflation/rates/geopolitical/earnings), and an action bias. A
**strongly negative, material** news read turns a 🟢 BUY into a 🟡 HOLD; milder concerns
get flagged. Scoring uses the best engine available, in order: the **Claude API**
(`{c.claude_model}`, when `ANTHROPIC_API_KEY` / `ant auth login` is set) → **FinBERT**, a
free finance-trained model that runs locally (when installed) → a simple **keyword** scan —
so it always runs. The active engine is labelled on the Macro & News tab. To keep AI cost
predictable, the **Claude scoring runs on the twice-daily schedule** and only for the names a
headline can actually move — today's **BUY candidates** and your **open positions**; every other
name takes the free keyword scan (its news can't flip a non-candidate into a trade). The on-demand
refresh buttons **reuse** that latest scored read rather than paying to re-score. All of this is
compiled in the **📰 Macro & News tab**.

**Layer D — options & short interest.** Two extras that move high-beta names hard, shown in the
**🔎 Squeeze & volatility watch** table and as **flags**:
- **Short interest** — shares sold short as a % of float, plus *days-to-cover*. Heavily-shorted
  names (≥ **{c.high_short_pct_float*100:.0f}%** of float) are **squeeze fuel** (violent up moves) but
  crowded and crash-prone; the app flags them so you size with eyes open.
- **Implied vs realized volatility** — when at-the-money options price a move **≥ {c.iv_rich_ratio:.1f}×**
  the stock's recent realized move, the market is bracing for something (often earnings). Buying
  into rich IV is how you can be right on direction yet lose on the vol crush — so it's flagged.
- **Analyst revisions** — net recent upgrades minus downgrades (revision momentum is one of the
  more robust short-horizon drivers); flagged when analysts are clearly moving one way. *(Free
  best-effort via Yahoo; a paid estimate-revisions feed would sharpen it.)*

These are **informational** — they flag a name and inform sizing, but don't by themselves block a BUY.

**Layer E — post-earnings drift (PEAD).** Layer B keeps the app *out* of a stock right before it
reports (the print is a coin-flip). This layer handles the *other* side of the event: once a stock
**has** reported, a strong beat the market rewarded with a **gap-up** tends to keep drifting up for
weeks — and a big miss keeps bleeding. It's one of the most durable anomalies in markets, and it's
**momentum-aligned** (it rewards a move that already happened, not a bet on an unknown result). The
app reads the drift from the **price reaction** (the 2-session move around the report, which already
bakes in the beat/miss *and* the guidance), using the reported EPS surprise only for labelling:
- A **positive** drift within the last **{c.pead_drift_days} days** (reaction **≥ {c.pead_min_gap*100:.0f}%**) adds
  **+{c.pead_conviction_bonus}** to Confidence — the one place a good earnings event is *allowed* to help.
- A **negative** drift subtracts **{c.pead_conviction_penalty}**, and a **strong** down-gap (**≥ {c.pead_strong_gap*100:.0f}%**)
  turns a 🟢 BUY into a 🟡 HOLD outright (the drift is against a fresh long).

It can only *nudge* an otherwise-valid setup — the price/trend/momentum technicals still lead, and
the pre-earnings blackout still fires first. *(Best-effort earnings data via Yahoo; like the news
layer, this live tilt is **not** in the backtest, since point-in-time historical surprises aren't
reliably free — so treat it as a live nudge, not part of the validated edge.)*

**Two refresh modes.** *Refresh signals & regime* re-pulls the regime, events and earnings and
rebuilds signals **on the prices already stored**, reusing the latest AI-scored news — it's quick
and does **not** move the market-data date or spend on AI. *Pull fresh prices*
(and the full `python -m src.pipeline`) re-downloads the whole price history and recomputes
everything — that's what advances **market data through** to the latest trading day. On the
hosted app a **scheduled job (GitHub Actions)** runs the full pipeline twice each weekday — once
**pre-open** (morning) and once **~15 min before the close** — rebuilds the shared `seed.db` and
pushes it, so the live app refreshes without anyone pressing a button. Each recommendation is
**timestamped** with which run produced it. (Pressing *Pull fresh prices* in the hosted app
refreshes your current session only — the cloud filesystem is temporary.)

**The daily buy/sell rhythm.** The Signals tab is a clean two-list action plan:
- **🟢 BUY these** — *new* positions to open today (names you already hold aren't repeated; they
  live under *your open positions* on the Track record tab). Each run's buy list is a fresh
  snapshot at that run's prices.
- **🔴 SELL these** — holdings whose exit has triggered (trailing stop / trend break / time limit).
  This is a **two-phase, manual-sell** flow: a name is flagged **SELL today**, you sell at your
  next opportunity, and on the **next run it's closed at that session's price** and moves to
  **Closed trades** — so the recorded P&L reflects a realistic manual fill, not the exact stop.

**Alerts.** Each run (before the open / after the close) emails a digest — or saves it to
`data/alerts/` if email isn't configured. Between runs, an **hourly news-shock check**
(`python -m src.shock`) scans for a big intraday move + fresh headline and alerts you.

---

### Where each piece of data comes from

Everything runs on **free sources by default**, and transparently upgrades when an API key is set.
Keys live in a local `.env` (or the hosted app's GitHub Action secrets) — never in the code.

| What | Source today | Upgrades to (if key set) | Verdict / what would make it better |
|---|---|---|---|
| **Prices** (signals, regime, backtest) | yfinance — free, unofficial | — | Accurate & fine. A paid feed (Polygon/Tiingo) only matters for *reliability* if this ever drives real money |
| **Market backdrop / regime** | yfinance: SPY, QQQ, VIX, SMH, US 10-yr, HYG credit + breadth | — | **Strong as-is — no paid source needed.** Well-diversified multi-factor read |
| **Per-stock news** | Google News RSS *titles* → **Finnhub** company-news (`FINNHUB_API_KEY`) — headline + summary + source, screener-noise filtered — plus **CNBC** headlines blended in (site-restricted Google News RSS, no key) | Finnhub **free** tier | Finnhub free is enough; the app drops generic "most-active" aggregator spam, always surfaces CNBC coverage, and falls back to RSS if too few real items remain |
| **News sentiment** | keyword scan → local **FinBERT** → **Claude** (`ANTHROPIC_API_KEY`, `{c.claude_model}`) | Claude (pennies/run) | **Biggest free-ish win: set `ANTHROPIC_API_KEY`.** Claude reads the *reaction* in market context; keywords can't |
| **Macro calendar** (CPI/FOMC/jobs) | curated seeded list (`events.py`) | **FMP** economic-calendar (`FMP_API_KEY`, **paid tier only**) | **The one thing worth paying for.** Free FMP/Finnhub tiers 402 here; the seeded list works but must be kept current |
| **Earnings dates** | **FMP** earnings-calendar (free, where it has the name) → yfinance fallback | FMP paid = full coverage | Free tier samples a subset, so it *supplements* yfinance rather than replacing it |
| **Post-earnings drift** | yfinance earnings dates + surprise, drift read from our own price bars | Paid earnings feed = cleaner dates/surprises | Momentum-aligned tilt; best-effort dates, not in the backtest |
| **Options IV · short interest · analyst revisions** | yfinance best-effort | — | Informational flags only; a paid options/estimate-revisions feed would sharpen them |

**Bottom line:** the market backdrop is solid on free data. The single largest accuracy gain is
adding `ANTHROPIC_API_KEY` (near-free) so news is *read*, not keyword-counted. The only source
genuinely worth *paying* for is a real macro/econ calendar (upgrade FMP or Finnhub premium).

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
    st.caption("The settings you're most likely to touch — the full set lives in `src/config.py`.")
    params = pd.DataFrame([
        {"Setting": "Minimum jumpiness (beta)", "Now": str(c.min_beta),
         "What it does": "Only show stocks at least this jumpy vs the market"},
        {"Setting": "Risk per trade", "Now": f"{c.risk_per_trade*100:.1f}%",
         "What it does": f"Most you'd lose on one trade (~£{c.capital_gbp*c.risk_per_trade:,.0f})"},
        {"Setting": "Stop width", "Now": f"{c.atr_stop_mult:.0f}x ATR",
         "What it does": "How far the safety-exit sits below the buy price"},
        {"Setting": "Reward : risk", "Now": f"{c.reward_risk:.0f}:1",
         "What it does": "Profit target set this many times the risk taken"},
        {"Setting": "Min confidence to buy", "Now": f"{c.min_conviction}%",
         "What it does": "A BUY must clear this + beat the S&P + above-average volume"},
        {"Setting": "Max positions", "Now": str(c.max_positions),
         "What it does": "Most trades held at once (set MAX_POSITIONS, or use the sidebar what-if)"},
        {"Setting": "Trading capital", "Now": f"£{c.capital_gbp:,.0f}",
         "What it does": "Total money position sizing is based on (set CAPITAL_GBP, or use the sidebar what-if)"},
    ])
    st.dataframe(params, width="stretch", hide_index=True)
    st.caption("💡 **Try different numbers live:** the sidebar's *Money to trade* and *Max positions "
               "at once* inputs + **♻️ Recalculate** re-size every BUY and re-pick the portfolio "
               "instantly — a display-only what-if that leaves your track record and the automated "
               "daily run untouched. Set the `CAPITAL_GBP` / `MAX_POSITIONS` repo variables to make "
               "a value the permanent default the daily refresh uses.")
    st.caption("Want a change? Just tell me e.g. \"only show beta ≥ 2\", \"use a 3×ATR stop\", "
               "or \"risk 1% per trade\" — I'll update `src/config.py` and re-run.")
    st.info("These are **rules-based** signals from price data, with a news/sentiment layer that "
            "can nudge a BUY to HOLD when the headlines say caution.")
