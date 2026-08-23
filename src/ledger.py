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
           "status", "exit_date", "exit_price", "r_multiple", "outcome"]


def _load(cfg):
    if db.table_exists("ledger", cfg.db_path):
        df = db.read_df("SELECT * FROM ledger", cfg.db_path)
        for col in COLUMNS:
            if col not in df.columns:
                df[col] = None
        return df
    return pd.DataFrame(columns=COLUMNS)


def record_recommendations(sig, cfg, today=None):
    today = today or dt.date.today().isoformat()
    if sig is None or sig.empty or "selected" not in sig.columns:
        return 0
    led = _load(cfg)
    open_tickers = set(led[led["status"] == "open"]["ticker"]) if not led.empty else set()
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


def update_open_positions(prices, cfg):
    """Resolve open positions with the same exit model as the backtest: a **trailing stop**
    (locks in gains as the trade runs) OR a **trend break** (a close below the trend SMA), with
    a time limit as a backstop. Fills are gap-aware. Outcome is win/loss by the realised R."""
    led = _load(cfg)
    if led.empty:
        return 0
    trail_mult = cfg.trail_atr_mult
    changed = 0
    for idx, row in led[led["status"] == "open"].iterrows():
        p = prices[prices["ticker"] == row["ticker"]].sort_values("date").copy()
        if p.empty:
            continue
        p["sma_trend"] = p["close"].astype(float).rolling(cfg.trend_exit_sma).mean()
        bars = p[p["date"] > str(row["record_date"])]
        if bars.empty:
            continue
        entry, stop = float(row["entry"]), float(row["stop"])
        atr0 = (entry - stop) / cfg.atr_stop_mult if cfg.atr_stop_mult else (entry - stop)
        hh = entry  # highest close since entry (the "run-up high" the trail hangs off)
        outcome = exit_price = exit_date = None
        held = 0
        for _, b in bars.iterrows():
            held += 1
            close, low = float(b["close"]), float(b["low"])
            op = float(b["open"]) if pd.notna(b["open"]) else close
            hh = max(hh, close)
            trail = max(stop, hh - trail_mult * atr0)  # never below the initial stop
            if low <= trail:  # trailing / initial stop hit — gap-aware fill at the open if it gapped
                exit_price = min(trail, op) if op < trail else trail
                outcome, exit_date = "stop", b["date"]
                break
            sma = b["sma_trend"]
            if pd.notna(sma) and close < float(sma):  # trend broke — close below the trend SMA
                exit_price, outcome, exit_date = close, "trend", b["date"]
                break
        if outcome is None and held >= cfg.ledger_max_hold_days:
            last = bars.iloc[-1]
            exit_price, outcome, exit_date = float(last["close"]), "time", last["date"]
        if outcome:
            stop_dist = entry - stop
            trade_cost = cfg.backtest_cost_pct + cfg.backtest_spread_atr_coef * (atr0 / entry)
            net = (exit_price / entry - 1) - trade_cost
            r = net / (stop_dist / entry) if stop_dist > 0 else 0.0
            led.loc[idx, ["status", "exit_date", "exit_price", "r_multiple", "outcome"]] = \
                ["closed", exit_date, round(exit_price, 2), round(r, 2), "win" if r > 0 else "loss"]
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
