"""Layer A: market-regime gate.

Reads a handful of public 'weather' gauges and decides whether it's a good day to
be holding high-beta stocks at all. High beta only pays when the market is rising,
so this scales how aggressive the BUY signals are:
  RISK-ON  -> full size
  CAUTION  -> half size
  RISK-OFF -> no new buys (BUYs become HOLD)

All data is free via yfinance.
"""
import pandas as pd

# ^VIX = volatility/fear gauge, ^TNX = US 10-year yield index,
# SPY = S&P 500, QQQ = Nasdaq-100, SMH = semiconductors (your shortlist skews here),
# HYG = high-yield corporate bonds (a credit-spread proxy — sells off before equities in a scare).
MACRO_TICKERS = ["^VIX", "^TNX", "SPY", "QQQ", "SMH", "HYG"]


def _series(prices, ticker):
    p = prices[prices["ticker"] == ticker].sort_values("date")
    return p["adj_close"].astype(float) if not p.empty else pd.Series(dtype=float)


def _norm_yield(raw):
    # ^TNX sometimes prints yield x10 (e.g. 42.5 == 4.25%). Normalise to a % number.
    return raw / 10.0 if raw > 20 else raw


def _breadth(universe_prices, ma=50):
    """Fraction of the tradable universe trading above its `ma`-day average — market breadth.
    Returns None if there aren't enough names/bars. Broad participation is a healthier tape than
    a handful of megacaps carrying the index."""
    if universe_prices is None or getattr(universe_prices, "empty", True):
        return None
    try:
        piv = universe_prices.pivot_table(index="date", columns="ticker",
                                          values="adj_close").sort_index()
    except Exception:  # noqa: BLE001
        return None
    if len(piv) < ma:
        return None
    last, sma = piv.iloc[-1], piv.tail(ma).mean()
    valid = last.notna() & sma.notna()
    n = int(valid.sum())
    if n == 0:
        return None
    return float((last[valid] > sma[valid]).sum()) / n


def compute_regime(prices, universe_prices=None):
    """Live regime from the latest bars: gather the readings, then score them with score_regime
    (the same scorer the backtest replays day by day, so both apply identical thresholds)."""
    spy, qqq, smh = _series(prices, "SPY"), _series(prices, "QQQ"), _series(prices, "SMH")
    vix, tnx = _series(prices, "^VIX"), _series(prices, "^TNX")
    hyg = _series(prices, "HYG")

    def vs50(s):
        return (float(s.iloc[-1]), float(s.tail(50).mean())) if len(s) >= 50 else None

    return score_regime({
        "spy": ((float(spy.iloc[-1]), float(spy.tail(50).mean()), float(spy.tail(200).mean()))
                if len(spy) >= 200 else None),
        "qqq": vs50(qqq),
        "smh": vs50(smh),
        "vix": float(vix.iloc[-1]) if len(vix) else None,
        "tnx": ((_norm_yield(float(tnx.iloc[-20])), _norm_yield(float(tnx.iloc[-1])))
                if len(tnx) >= 20 else None),
        "hyg": vs50(hyg),
        "breadth": _breadth(universe_prices),
    })


def score_regime(r):
    """Score one day's readings. r = {spy: (price, sma50, sma200), qqq/smh/hyg: (price, sma50),
    vix: level, tnx: (yield 19 sessions ago, yield now), breadth: fraction} — any may be None
    (missing data simply doesn't score). Returns {label, score, size_multiplier, readings, notes}."""
    score = 0
    notes = []
    readings = {"vix": None, "spy_vs_50d": None, "us10y": None}

    if r.get("spy") is not None:
        price, s50, s200 = r["spy"]
        readings["spy_vs_50d"] = round((price / s50 - 1) * 100, 1)
        if price > s50 and s50 > s200:
            score += 2; notes.append("S&P 500 in a clear uptrend (+2)")
        elif price > s50:
            score += 1; notes.append("S&P 500 above its 50-day average (+1)")
        else:
            score -= 2; notes.append("S&P 500 below its 50-day average — market weak (-2)")

    if r.get("qqq") is not None:
        if r["qqq"][0] > r["qqq"][1]:
            score += 1; notes.append("Nasdaq-100 above its 50-day average (+1)")
        else:
            score -= 1; notes.append("Nasdaq-100 below its 50-day average (-1)")

    if r.get("smh") is not None:
        if r["smh"][0] > r["smh"][1]:
            score += 1; notes.append("Semiconductors (SMH) above their 50-day average (+1)")
        else:
            score -= 1; notes.append("Semiconductors (SMH) below their 50-day average (-1)")

    if r.get("vix") is not None:
        v = float(r["vix"]); readings["vix"] = round(v, 1)
        if v < 20:
            score += 1; notes.append(f"VIX {v:.0f} — calm markets (+1)")
        elif v <= 30:
            notes.append(f"VIX {v:.0f} — elevated volatility (0)")
        else:
            score -= 2; notes.append(f"VIX {v:.0f} — high fear (-2)")

    if r.get("tnx") is not None:
        y_prev, y_now = r["tnx"]
        readings["us10y"] = round(y_now, 2)
        if y_now - y_prev > 0.30:
            score -= 1
            notes.append(f"10-year yield rising fast ({y_prev:.2f}%->{y_now:.2f}%) — pressures high beta (-1)")

    # Credit: high-yield bonds tend to crack before equities in a real scare.
    if r.get("hyg") is not None:
        if r["hyg"][0] > r["hyg"][1]:
            score += 1; notes.append("Credit calm — high-yield bonds above their 50-day average (+1)")
        else:
            score -= 2; notes.append("Credit stress — high-yield bonds below their 50-day average (-2)")

    # Breadth: how much of the tradable universe is actually in an uptrend (not just the megacaps).
    breadth = r.get("breadth")
    if breadth is not None:
        readings["breadth"] = round(breadth * 100, 0)
        if breadth >= 0.60:
            score += 1; notes.append(f"Broad participation — {breadth*100:.0f}% of names above their 50-day average (+1)")
        elif breadth < 0.40:
            score -= 1; notes.append(f"Narrow/weak breadth — only {breadth*100:.0f}% of names above their 50-day average (-1)")

    # Thresholds nudged up to absorb the two extra factors (credit + breadth).
    if score >= 4:
        label, mult = "RISK-ON", 1.0
    elif score >= 0:
        label, mult = "CAUTION", 0.5
    else:
        label, mult = "RISK-OFF", 0.0

    return {
        "label": label,
        "score": score,
        "size_multiplier": mult,
        "readings": readings,
        "notes": notes,
    }
