"""Track-record ledger: log every recommended trade and resolve its outcome over time.

On each pipeline run:
  - record_recommendations() logs today's selected BUYs as OPEN positions.
  - update_open_positions() checks each OPEN position against later price bars and closes
    it as a win (target hit), loss (stop hit), or timeout (held too long) — in R multiples.
This produces a live, honest hit rate that accrues from the day you start running it.
"""
import datetime as dt

import pandas as pd

from . import db

COLUMNS = ["ticker", "record_date", "entry", "stop", "target", "shares", "risk_gbp",
           "status", "sell_signal_date", "sell_reason",
           "exit_date", "exit_price", "r_multiple", "outcome"]
# status lifecycle: "open" -> "sell_pending" (exit flagged; shown as SELL today) -> "closed"
# (filled at the next session's price the following run, i.e. next day).
# Columns that must stay numeric — pandas 3.0 reads text-ish SQLite columns as a strict `str`
# dtype, and later assigning a float into one (e.g. a fresh exit_price) raises TypeError. Coercing
# on load keeps them float so the read-modify-write in update_open_positions is safe on any pandas.
_NUMERIC_COLS = ["entry", "stop", "target", "shares", "risk_gbp", "exit_price", "r_multiple"]


def _load(cfg):
    if db.table_exists("ledger", cfg.db_path):
        df = db.read_df("SELECT * FROM ledger", cfg.db_path)
        for col in COLUMNS:
            if col not in df.columns:
                df[col] = None
        for col in _NUMERIC_COLS:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    return pd.DataFrame(columns=COLUMNS)


def record_recommendations(sig, cfg, today=None):
    today = today or dt.date.today().isoformat()
    if sig is None or sig.empty or "selected" not in sig.columns:
        return 0
    led = _load(cfg)
    # Don't re-buy something we already hold OR are about to sell.
    held = set(led[led["status"].isin(["open", "sell_pending"])]["ticker"]) if not led.empty else set()
    open_tickers = held
    sel = sig[sig["selected"].astype(bool)]
    rows = []
    for _, r in sel.iterrows():
        if r["ticker"] in open_tickers:
            continue  # already holding this recommendation
        if r.get("entry") is None or pd.isna(r.get("entry")):
            continue
        rows.append({
            "ticker": r["ticker"], "record_date": today,
            "entry": r.get("entry"), "stop": r.get("stop"), "target": r.get("target"),
            "shares": r.get("shares"), "risk_gbp": r.get("risk_gbp"),
            "status": "open", "exit_date": None, "exit_price": None,
            "r_multiple": None, "outcome": None,
        })
    if rows:
        led = pd.concat([led, pd.DataFrame(rows)], ignore_index=True)
        db.write_df(led, "ledger", cfg.db_path)
    return len(rows)


def _first_exit(row, prices, cfg):
    """First bar since entry where the exit triggers (trailing stop / trend break / time limit).
    Returns (exit_date, reason) or (None, None) if still healthy."""
    p = prices[prices["ticker"] == row["ticker"]].sort_values("date").copy()
    if p.empty:
        return None, None
    p["sma_trend"] = p["close"].astype(float).rolling(cfg.trend_exit_sma).mean()
    bars = p[p["date"] > str(row["record_date"])]
    if bars.empty:
        return None, None
    entry, stop = float(row["entry"]), float(row["stop"])
    atr0 = (entry - stop) / cfg.atr_stop_mult if cfg.atr_stop_mult else (entry - stop)
    hh, held = entry, 0
    for _, b in bars.iterrows():
        held += 1
        close, low = float(b["close"]), float(b["low"])
        hh = max(hh, close)
        trail = max(stop, hh - cfg.trail_atr_mult * atr0)
        if low <= trail:
            return b["date"], "hit the trailing stop"
        if pd.notna(b["sma_trend"]) and close < float(b["sma_trend"]):
            return b["date"], f"closed below its {cfg.trend_exit_sma}-day average (trend break)"
        if held >= cfg.ledger_max_hold_days:
            return b["date"], f"held the {cfg.ledger_max_hold_days}-day limit"
    return None, None


def update_open_positions(prices, cfg):
    """Two-phase, manual-sell lifecycle:
      • FLAG — an OPEN position whose exit triggers becomes **sell_pending** (shown as 'SELL today').
      • FINALISE — a sell_pending position is **closed at the next session's price** (the following
        run, i.e. the next day), so realised P&L reflects a realistic manual fill, not the exact
        stop. Exit model = trailing stop / trend break / time limit (same as the backtest)."""
    led = _load(cfg)
    if led.empty:
        return 0
    changed = 0

    # Phase 1 — flag freshly-triggered exits as sell_pending (record the date it triggered).
    for idx, row in led[led["status"] == "open"].iterrows():
        exit_date, reason = _first_exit(row, prices, cfg)
        if exit_date:
            led.loc[idx, ["status", "sell_signal_date", "sell_reason"]] = \
                ["sell_pending", exit_date, reason]
            changed += 1

    # Phase 2 — finalise anything pending once a *later* session's price exists (next-day fill).
    for idx, row in led[led["status"] == "sell_pending"].iterrows():
        p = prices[prices["ticker"] == row["ticker"]].sort_values("date")
        after = p[p["date"] > str(row["sell_signal_date"])]
        if after.empty:
            continue  # no new bar yet — stays 'SELL today' until the next run
        bar = after.iloc[0]
        entry, stop = float(row["entry"]), float(row["stop"])
        exit_price = float(bar["close"])
        atr0 = (entry - stop) / cfg.atr_stop_mult if cfg.atr_stop_mult else (entry - stop)
        stop_dist = entry - stop
        trade_cost = cfg.backtest_cost_pct + cfg.backtest_spread_atr_coef * (atr0 / entry)
        net = (exit_price / entry - 1) - trade_cost
        r = net / (stop_dist / entry) if stop_dist > 0 else 0.0
        led.loc[idx, ["status", "exit_date", "exit_price", "r_multiple", "outcome"]] = \
            ["closed", bar["date"], round(exit_price, 2), round(r, 2), "win" if r > 0 else "loss"]
        changed += 1

    if changed:
        db.write_df(led, "ledger", cfg.db_path)
    return changed


def open_positions_view(prices, cfg):
    """Read-only 'what do I do today' view of every OPEN position (does NOT close anything).

    For each holding, using the same trailing-stop + trend-break model as update_open_positions,
    returns: latest price, the live **trailing stop** (the level to keep at your broker), the
    trend average, days held, unrealised R + £, and a HOLD / SELL-today action with the reason."""
    led = _load(cfg)
    openp = led[led["status"] == "open"] if not led.empty else led
    rows = []
    for _, row in openp.iterrows():
        p = prices[prices["ticker"] == row["ticker"]].sort_values("date")
        if p.empty:
            continue
        closes = p["close"].astype(float)
        entry, stop = float(row["entry"]), float(row["stop"])
        atr0 = (entry - stop) / cfg.atr_stop_mult if cfg.atr_stop_mult else (entry - stop)
        bars = p[p["date"] > str(row["record_date"])]
        held = int(len(bars))
        hh = max(entry, float(bars["close"].astype(float).max())) if not bars.empty else entry
        current = float(closes.iloc[-1])
        trail = max(stop, hh - cfg.trail_atr_mult * atr0)  # live trailing stop (never below initial)
        sma = closes.rolling(cfg.trend_exit_sma).mean().iloc[-1]
        trend_ok = not pd.notna(sma) or current >= float(sma)
        action, reason = "HOLD", "trend intact, above the trailing stop"
        if current <= trail:
            action, reason = "SELL", "at/through the trailing stop"
        elif pd.notna(sma) and current < float(sma):
            action, reason = "SELL", f"closed below its {cfg.trend_exit_sma}-day average (trend break)"
        elif held >= cfg.ledger_max_hold_days:
            action, reason = "SELL", f"held {held} days (time limit)"
        stop_dist = entry - stop
        unreal_r = (current - entry) / stop_dist if stop_dist > 0 else 0.0
        rows.append({
            "ticker": row["ticker"], "shares": row.get("shares"),
            "record_date": row["record_date"], "days_held": held,
            "entry": round(entry, 2), "current": round(current, 2),
            "trail_stop": round(trail, 2), "target": row.get("target"),
            "unrealised_r": round(unreal_r, 2),
            "pnl_gbp": round(unreal_r * float(row.get("risk_gbp") or 0), 0),
            "action": action, "reason": reason, "as_of": str(p["date"].iloc[-1]),
        })
    return pd.DataFrame(rows)


def pending_sells_view(prices, cfg):
    """Positions flagged 'sell_pending' — the 'SELL these today' list. Read-only; they close at the
    next session's price on the following run. Includes why, and the P&L so far at the latest price."""
    led = _load(cfg)
    pend = led[led["status"] == "sell_pending"] if not led.empty else led
    rows = []
    for _, row in pend.iterrows():
        p = prices[prices["ticker"] == row["ticker"]].sort_values("date")
        entry, stop = float(row["entry"]), float(row["stop"])
        current = float(p["close"].astype(float).iloc[-1]) if not p.empty else entry
        stop_dist = entry - stop
        unreal_r = (current - entry) / stop_dist if stop_dist > 0 else 0.0
        rows.append({
            "ticker": row["ticker"], "shares": row.get("shares"),
            "entry": round(entry, 2), "current": round(current, 2),
            "flagged": row.get("sell_signal_date"), "reason": row.get("sell_reason"),
            "unrealised_r": round(unreal_r, 2),
            "pnl_gbp": round(unreal_r * float(row.get("risk_gbp") or 0), 0),
        })
    return pd.DataFrame(rows)


def stats(cfg):
    led = _load(cfg)
    closed = led[led["status"] == "closed"] if not led.empty else led
    n_open = int((led["status"] == "open").sum()) if not led.empty else 0
    if closed.empty:
        return {"closed": 0, "open": n_open, "note": "Building the track record — no trades "
                "have closed yet. Check back after a few runs."}
    r = closed["r_multiple"].astype(float)
    wins = closed[r > 0]
    return {
        "closed": int(len(closed)), "open": n_open,
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "expectancy_r": round(float(r.mean()), 3),
        "avg_win_r": round(float(r[r > 0].mean()), 2) if (r > 0).any() else 0.0,
        "avg_loss_r": round(float(r[r <= 0].mean()), 2) if (r <= 0).any() else 0.0,
        "total_r": round(float(r.sum()), 2),
        "note": "Live results from recommendations this app actually logged (includes news + "
                "macro layers, unlike the backtest).",
    }
