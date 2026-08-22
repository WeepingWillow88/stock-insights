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
    score = 0
    notes = []
    readings = {"vix": None, "spy_vs_50d": None, "us10y": None}

    spy, qqq, smh = _series(prices, "SPY"), _series(prices, "QQQ"), _series(prices, "SMH")
    vix, tnx = _series(prices, "^VIX"), _series(prices, "^TNX")
    hyg = _series(prices, "HYG")

    if len(spy) >= 200:
        price, s50, s200 = spy.iloc[-1], spy.tail(50).mean(), spy.tail(200).mean()
        readings["spy_vs_50d"] = round((price / s50 - 1) * 100, 1)
        if price > s50 and s50 > s200:
            score += 2; notes.append("S&P 500 in a clear uptrend (+2)")
        elif price > s50:
            score += 1; notes.append("S&P 500 above its 50-day average (+1)")
        else:
            score -= 2; notes.append("S&P 500 below its 50-day average — market weak (-2)")

    if len(qqq) >= 50:
        if qqq.iloc[-1] > qqq.tail(50).mean():
            score += 1; notes.append("Nasdaq-100 above its 50-day average (+1)")
        else:
            score -= 1; notes.append("Nasdaq-100 below its 50-day average (-1)")

    if len(smh) >= 50:
        if smh.iloc[-1] > smh.tail(50).mean():
            score += 1; notes.append("Semiconductors (SMH) above their 50-day average (+1)")
        else:
            score -= 1; notes.append("Semiconductors (SMH) below their 50-day average (-1)")

    if len(vix):
        v = float(vix.iloc[-1]); readings["vix"] = round(v, 1)
        if v < 20:
            score += 1; notes.append(f"VIX {v:.0f} — calm markets (+1)")
        elif v <= 30:
            notes.append(f"VIX {v:.0f} — elevated volatility (0)")
        else:
            score -= 2; notes.append(f"VIX {v:.0f} — high fear (-2)")

    if len(tnx) >= 20:
        y_now = _norm_yield(float(tnx.iloc[-1]))
        y_prev = _norm_yield(float(tnx.iloc[-20]))
        readings["us10y"] = round(y_now, 2)
        if y_now - y_prev > 0.30:
            score -= 1
            notes.append(f"10-year yield rising fast ({y_prev:.2f}%->{y_now:.2f}%) — pressures high beta (-1)")

    # Credit: high-yield bonds tend to crack before equities in a real scare.
    if len(hyg) >= 50:
        if hyg.iloc[-1] > hyg.tail(50).mean():
            score += 1; notes.append("Credit calm — high-yield bonds above their 50-day average (+1)")
        else:
            score -= 2; notes.append("Credit stress — high-yield bonds below their 50-day average (-2)")

    # Breadth: how much of the tradable universe is actually in an uptrend (not just the megacaps).
    breadth = _breadth(universe_prices)
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
