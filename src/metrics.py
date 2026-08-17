"""Per-ticker metrics: beta (vs benchmark), momentum, trend, ATR%, $-volume."""
import numpy as np
import pandas as pd


def _pivot(prices, field):
    return prices.pivot_table(index="date", columns="ticker", values=field).sort_index()


def compute_metrics(prices, benchmark="SPY", beta_window=252, beta_shrink=0.67, stale_days=5):
    adj = _pivot(prices, "adj_close")
    high = _pivot(prices, "high")
    low = _pivot(prices, "low")
    close = _pivot(prices, "close")
    vol = _pivot(prices, "volume")

    if benchmark not in adj.columns:
        raise ValueError(f"Benchmark {benchmark!r} not present in price data")

    rets = adj.pct_change(fill_method=None)
    mkt = rets[benchmark].dropna()

    rows = []
    for t in adj.columns:
        if t == benchmark:
            continue
        s = adj[t].dropna()
        if len(s) < 60:
            continue
        # Data validation: skip stale/delisted names with no recent bars
        if adj[t].tail(stale_days).notna().sum() == 0:
            continue

        pair = pd.concat([rets[t], mkt], axis=1, keys=["s", "m"]).dropna().tail(beta_window)
        if len(pair) < 60 or pair["m"].var() == 0:
            beta = np.nan
        else:
            beta = pair["s"].cov(pair["m"]) / pair["m"].var()
            # Blume shrinkage toward the market (1.0) — less noisy, more robust
            beta = beta_shrink * beta + (1 - beta_shrink) * 1.0

        price = s.iloc[-1]
        sma50 = s.tail(50).mean()
        sma200 = s.tail(200).mean() if len(s) >= 200 else np.nan
        mom_3m = (s.iloc[-1] / s.iloc[-63] - 1) if len(s) >= 63 else np.nan
        mom_1m = (s.iloc[-1] / s.iloc[-21] - 1) if len(s) >= 21 else np.nan
        atr_pct = _atr_pct(high.get(t), low.get(t), close.get(t))
        avg_dollar_vol = float((close[t].tail(30) * vol[t].tail(30)).mean())

        uptrend = bool(price > sma50 and (np.isnan(sma200) or sma50 > sma200))

        rows.append({
            "ticker": t,
            "price": _r(price, 2),
            "beta": _r(beta, 2),
            "mom_3m": _r(mom_3m, 4),
            "mom_1m": _r(mom_1m, 4),
            "atr_pct": _r(atr_pct, 4),
            "avg_dollar_vol": avg_dollar_vol,
            "above_sma50": bool(price > sma50) if pd.notna(sma50) else False,
            "sma50_gt_sma200": bool(sma50 > sma200) if pd.notna(sma200) else False,
            "uptrend": uptrend,
        })
    return pd.DataFrame(rows)


def _atr_pct(high, low, close, window=14):
    if high is None or low is None or close is None:
        return np.nan
    df = pd.concat([high, low, close], axis=1, keys=["h", "l", "c"]).dropna()
    if len(df) < window + 1:
        return np.nan
    prev_close = df["c"].shift(1)
    tr = pd.concat([
        df["h"] - df["l"],
        (df["h"] - prev_close).abs(),
        (df["l"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.tail(window).mean()
    last_close = df["c"].iloc[-1]
    return atr / last_close if last_close else np.nan


def _r(x, n):
    return round(float(x), n) if pd.notna(x) else np.nan
