"""Backtester (v2): replay improved rules over ~10 years and measure the edge robustly.

Improvements over v1:
  (1) Exits: a **trailing stop** (locks in gains, lets winners run) + a **trend-break exit**
      (sell if price closes below its 20-day average), with a long time-limit only as a backstop
      — instead of a fixed time clock that closed a third of trades near-randomly.
      Stop fills are **gap-aware**: if price gaps below the stop, you're filled at that day's
      open, so losses bigger than -1R are captured (honest, not optimistic).
  (2) Higher-quality entries: minimum conviction, above-average volume, and relative strength
      (only buy names beating the S&P).
  (3) Regime gate: only buy when the S&P 500 itself is in an uptrend.
  (4) ~10 years of history, so bear markets (2018, 2020, 2022) are included.
  (6) Robustness: a Monte-Carlo range for the edge + a parameter-sensitivity sweep.

Results are in R multiples. HONEST CAVEATS still apply: survivorship bias (today's S&P 500),
technicals only (no historical news), and modelled fills. Past performance ≠ future results.

Run:  python -m src.backtest
"""
import numpy as np
import pandas as pd

from .config import CONFIG
from . import data, db, universe


def _rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _atr(high, low, close, n=14):
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def _load_prices(cfg, reuse_cache=True):
    same = (reuse_cache and db.table_exists("backtest_prices", cfg.db_path)
            and db.table_exists("backtest_meta", cfg.db_path)
            and str(db.read_df("SELECT years FROM backtest_meta", cfg.db_path).iloc[0]["years"])
            == str(cfg.backtest_years))
    if same:
        return db.read_df("SELECT * FROM backtest_prices", cfg.db_path)
    tickers = universe.get_universe()
    if cfg.benchmark not in tickers:
        tickers = [cfg.benchmark] + tickers
    prices = data.download_prices(tickers, period=cfg.backtest_years, batch_size=cfg.batch_size)
    db.write_df(prices, "backtest_prices", cfg.db_path)
    db.write_df(pd.DataFrame([{"years": cfg.backtest_years}]), "backtest_meta", cfg.db_path)
    return prices


def _prep(prices, cfg):
    def piv(f):
        return prices.pivot_table(index="date", columns="ticker", values=f).sort_index()

    adj, high, low, close, vol = piv("adj_close"), piv("high"), piv("low"), piv("close"), piv("volume")
    openp = piv("open") if "open" in prices.columns else close
    bench = cfg.benchmark
    if bench not in adj.columns:
        raise SystemExit(f"Benchmark {bench} missing from backtest data.")
    mkt_ret = adj[bench].pct_change()
    spy_up = (adj[bench] > adj[bench].rolling(50).mean()).values
    spy_mom3 = (adj[bench] / adj[bench].shift(63) - 1).values

    # Universe: liquid, high-beta names (full-period beta — a stated simplification)
    prep = []
    for t in adj.columns:
        if t == bench:
            continue
        s = adj[t].dropna()
        if len(s) < 260:
            continue
        r = adj[t].pct_change()
        pair = pd.concat([r, mkt_ret], axis=1).dropna()
        if len(pair) < 200 or pair.iloc[:, 1].var() == 0:
            continue
        beta = pair.iloc[:, 0].cov(pair.iloc[:, 1]) / pair.iloc[:, 1].var()
        dvol = float((close[t].tail(60) * vol[t].tail(60)).mean())
        if not (beta >= cfg.min_beta and dvol >= cfg.min_avg_dollar_volume and s.iloc[-1] >= cfg.min_price):
            continue
        a = adj[t]
        prep.append({
            "ticker": t,
            "a": a.values, "c": close[t].values, "hi": high[t].values, "lo": low[t].values,
            "open": openp[t].values,
            "sma20": a.rolling(cfg.backtest_trend_exit_sma).mean().values,
            "sma50": a.rolling(50).mean().values, "sma200": a.rolling(200).mean().values,
            "rsi": _rsi(a).values, "atr": _atr(high[t], low[t], close[t]).values,
            "vol": vol[t].values, "vol20": vol[t].rolling(20).mean().values,
            "mom3": (a / a.shift(63) - 1).values, "mom1": (a / a.shift(21) - 1).values,
        })
    prep = sorted(prep, key=lambda d: -np.nan_to_num(np.nanmean(d["c"][-60:] * d["vol"][-60:])))
    return prep[:cfg.backtest_universe_max], spy_up, spy_mom3, list(adj.index)


def _simulate(prep, spy_up, spy_mom3, dates, cfg, stop_mult=None):
    stop_mult = cfg.atr_stop_mult if stop_mult is None else stop_mult
    trail_mult, max_hold, cost = cfg.backtest_trail_atr_mult, cfg.backtest_max_hold_days, cfg.backtest_cost_pct
    trades = []
    for d in prep:
        a, c, hi, lo, op = d["a"], d["c"], d["hi"], d["lo"], d["open"]
        sma20, sma50, sma200, rsi, atr = d["sma20"], d["sma50"], d["sma200"], d["rsi"], d["atr"]
        vol, vol20, mom3, mom1 = d["vol"], d["vol20"], d["mom3"], d["mom1"]
        n = len(a)
        i = 200
        while i < n - 1:
            if (np.isnan(a[i]) or np.isnan(sma50[i]) or np.isnan(rsi[i])
                    or np.isnan(atr[i]) or atr[i] <= 0):
                i += 1
                continue
            price = a[i]
            trend = price > sma50[i] and (np.isnan(sma200[i]) or sma50[i] > sma200[i])
            m3, m1 = (mom3[i] if not np.isnan(mom3[i]) else 0), (mom1[i] if not np.isnan(mom1[i]) else 0)
            rv = rsi[i]
            # base + conviction (2)
            checks = [trend, m3 > 0, m1 > 0, cfg.rsi_min_buy <= rv < cfg.rsi_overbought]
            conviction = sum(bool(x) for x in checks) / len(checks) * 100
            entry_ok = trend and (m3 > 0 or m1 > 0) and cfg.rsi_min_buy <= rv < cfg.rsi_overbought \
                and conviction >= cfg.backtest_min_conviction
            if entry_ok and cfg.backtest_use_regime:
                entry_ok = bool(spy_up[i])
            if entry_ok and cfg.backtest_use_rs:  # relative strength vs S&P
                entry_ok = m3 > (spy_mom3[i] if not np.isnan(spy_mom3[i]) else 0)
            if entry_ok and cfg.backtest_use_volume:  # volume confirmation
                entry_ok = (not np.isnan(vol20[i])) and vol[i] > vol20[i]
            if not entry_ok:
                i += 1
                continue

            entry = c[i]
            atr0 = atr[i]
            stop_dist0 = stop_mult * atr0
            init_stop = entry - stop_dist0
            hh = entry
            outcome, exit_price, exit_i, reason = None, None, None, None
            for j in range(i + 1, min(i + 1 + max_hold, n)):
                hh = max(hh, c[j])
                trail = max(init_stop, hh - trail_mult * atr0)
                if lo[j] <= trail:
                    # gap-aware fill (5): if it opened below the stop, you're filled at the open
                    fill = min(trail, op[j]) if not np.isnan(op[j]) and op[j] < trail else trail
                    outcome, exit_price, exit_i, reason = "exit", fill, j, "stop/trail"
                    break
                if not np.isnan(sma20[j]) and c[j] < sma20[j]:
                    outcome, exit_price, exit_i, reason = "exit", c[j], j, "trend-break"
                    break
            if outcome is None:
                exit_i = min(i + max_hold, n - 1)
                exit_price, reason = c[exit_i], "time"
            net = (exit_price / entry - 1) - cost
            r_mult = net / (stop_dist0 / entry) if stop_dist0 > 0 else 0.0
            trades.append({
                "ticker": d["ticker"], "entry_date": dates[i], "exit_date": dates[exit_i],
                "entry": round(entry, 2), "exit": round(exit_price, 2),
                "return_pct": round(net * 100, 2), "r_multiple": round(r_mult, 2),
                "outcome": "win" if r_mult > 0 else "loss", "exit_reason": reason,
                "hold_days": exit_i - i, "regime": "up" if bool(spy_up[i]) else "down",
            })
            i = exit_i + 1
    return pd.DataFrame(trades)


def _summarise(tdf, cfg):
    if tdf.empty:
        return {"trades": 0, "note": "No trades — entry filters may be too strict for this data."}
    r = tdf["r_multiple"]
    wins, losses = tdf[r > 0], tdf[r <= 0]
    acct = r * cfg.risk_per_trade
    equity = (1 + acct).cumprod()
    max_dd = float(((equity - equity.cummax()) / equity.cummax()).min() * 100)
    gl = float(-losses["r_multiple"].sum())

    def rex(g):
        return round(float(g["r_multiple"].mean()), 3) if len(g) else None

    reason_pct = (tdf["exit_reason"].value_counts(normalize=True) * 100).round(1).to_dict()
    return {
        "trades": int(len(tdf)),
        "win_rate": round(len(wins) / len(tdf) * 100, 1),
        "expectancy_r": round(float(r.mean()), 3),
        "avg_win_r": round(float(wins["r_multiple"].mean()), 2) if len(wins) else 0.0,
        "avg_loss_r": round(float(losses["r_multiple"].mean()), 2) if len(losses) else 0.0,
        "profit_factor": round(float(wins["r_multiple"].sum()) / gl, 2) if gl > 0 else None,
        "avg_hold_days": round(float(tdf["hold_days"].mean()), 1),
        "timeout_pct": round(reason_pct.get("time", 0.0), 1),
        "trend_exit_pct": round(reason_pct.get("trend-break", 0.0), 1),
        "stop_exit_pct": round(reason_pct.get("stop/trail", 0.0), 1),
        "total_return_pct": round(float((equity.iloc[-1] - 1) * 100), 1),
        "max_drawdown_pct": round(max_dd, 1),
        "exp_r_uptrend": rex(tdf[tdf["regime"] == "up"]),
        "exp_r_downtrend": rex(tdf[tdf["regime"] == "down"]),
        "years": cfg.backtest_years,
        "note": "Improved exits (trailing + trend-break, gap-aware) & filtered entries "
                "(conviction/volume/relative-strength/regime). Survivorship-biased (today's "
                "S&P 500), technicals only, costs modelled. Past performance ≠ future results.",
    }


def _montecarlo(tdf, cfg):
    """Resample the trade sequence to get a range for the edge and drawdown (6)."""
    if tdf.empty or len(tdf) < 30:
        return {}
    rng = np.random.default_rng(42)
    r = tdf["r_multiple"].values
    n = len(r)
    exps, dds = [], []
    for _ in range(cfg.backtest_mc_runs):
        samp = rng.choice(r, size=n, replace=True)
        exps.append(samp.mean())
        eq = np.cumprod(1 + samp * cfg.risk_per_trade)
        dds.append(float(((eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq)).min() * 100))
    return {
        "mc_exp_p5": round(float(np.percentile(exps, 5)), 3),
        "mc_exp_p50": round(float(np.percentile(exps, 50)), 3),
        "mc_exp_p95": round(float(np.percentile(exps, 95)), 3),
        "mc_maxdd_p50": round(float(np.percentile(dds, 50)), 1),
        "mc_maxdd_p95": round(float(np.percentile(dds, 5)), 1),  # 5th pctile = worst 1-in-20
        "mc_prob_positive": round(float(np.mean(np.array(exps) > 0) * 100), 1),
    }


def run_backtest(cfg=CONFIG, reuse_cache=True):
    prices = _load_prices(cfg, reuse_cache)
    if prices.empty:
        raise SystemExit("No backtest price data.")
    prep, spy_up, spy_mom3, dates = _prep(prices, cfg)

    tdf = _simulate(prep, spy_up, spy_mom3, dates, cfg)
    summary = _summarise(tdf, cfg)
    summary.update(_montecarlo(tdf, cfg))

    if not tdf.empty:
        tdf = tdf.sort_values("entry_date")
        tdf["equity"] = (1 + tdf["r_multiple"] * cfg.risk_per_trade).cumprod()
    db.write_df(tdf if not tdf.empty else pd.DataFrame(columns=["ticker"]),
                "backtest_trades", cfg.db_path)
    db.write_df(pd.DataFrame([summary]), "backtest_summary", cfg.db_path)

    # Parameter sensitivity: vary the initial stop width (6)
    sens = []
    for sm in [1.5, 2.0, 2.5, 3.0]:
        st = _simulate(prep, spy_up, spy_mom3, dates, cfg, stop_mult=sm)
        s = _summarise(st, cfg)
        sens.append({"stop_atr_mult": sm, "trades": s.get("trades", 0),
                     "expectancy_r": s.get("expectancy_r"), "win_rate": s.get("win_rate"),
                     "profit_factor": s.get("profit_factor")})
    db.write_df(pd.DataFrame(sens), "backtest_sensitivity", cfg.db_path)
    return summary


if __name__ == "__main__":
    s = run_backtest()
    for k, v in s.items():
        print(f"  {k}: {v}")
