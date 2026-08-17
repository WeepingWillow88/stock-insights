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
    led = _load(cfg)
    if led.empty:
        return 0
    changed = 0
    for idx, row in led[led["status"] == "open"].iterrows():
        p = prices[prices["ticker"] == row["ticker"]].copy()
        if p.empty:
            continue
        bars = p[p["date"] > str(row["record_date"])].sort_values("date")
        if bars.empty:
            continue
        entry, stop, target = float(row["entry"]), float(row["stop"]), float(row["target"])
        outcome, exit_price, exit_date = None, None, None
        held = 0
        for _, b in bars.iterrows():
            held += 1
            if float(b["low"]) <= stop:
                outcome, exit_price, exit_date = "loss", stop, b["date"]
                break
            if float(b["high"]) >= target:
                outcome, exit_price, exit_date = "win", target, b["date"]
                break
        if outcome is None and held >= cfg.ledger_max_hold_days:
            last = bars.iloc[-1]
            outcome, exit_price, exit_date = "timeout", float(last["close"]), last["date"]
        if outcome:
            stop_dist = entry - stop
            net = (exit_price / entry - 1) - cfg.backtest_cost_pct
            r = net / (stop_dist / entry) if stop_dist > 0 else 0.0
            led.loc[idx, ["status", "exit_date", "exit_price", "r_multiple", "outcome"]] = \
                ["closed", exit_date, round(exit_price, 2), round(r, 2), outcome]
            changed += 1
    if changed:
        db.write_df(led, "ledger", cfg.db_path)
    return changed


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
