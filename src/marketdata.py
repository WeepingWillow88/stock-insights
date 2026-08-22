"""B2 + B3 overlays: options-implied volatility and short interest (best-effort via yfinance).

For a high-beta swing trader these two data points are among the most predictive extras:
  - Short interest / days-to-cover (B3): heavily-shorted names are squeeze fuel (violent up moves)
    but also crowded and crash-prone — worth knowing before sizing a swing.
  - Implied volatility vs realized (B2): when options price a much bigger move than the stock has
    recently made, the market is bracing for an event (often earnings) — buying into rich IV is a
    common way to be right on direction yet lose on the vol crush.

Everything here is best-effort: Yahoo's info/options endpoints are flaky, so any failure just
means that ticker has no extras (never raises), exactly like the earnings/news layers.
"""
import math


def _atm_iv(yft, spot):
    """Annualised at-the-money implied vol from the nearest expiry, or None."""
    try:
        exps = yft.options
        if not exps:
            return None
        calls = yft.option_chain(exps[0]).calls
        if calls is None or calls.empty:
            return None
        calls = calls.dropna(subset=["impliedVolatility"])
        calls = calls[calls["impliedVolatility"] > 0]
        if calls.empty:
            return None
        idx = (calls["strike"] - spot).abs().idxmin()
        return float(calls.loc[idx, "impliedVolatility"])
    except Exception:  # noqa: BLE001
        return None


def _analyst_net(yft):
    """Net analyst upgrades − downgrades over the most recent actions (a revision-momentum proxy).
    Free/best-effort via yfinance; a paid estimate-revisions feed would be sharper. None on failure."""
    try:
        ud = yft.upgrades_downgrades
        if ud is None or ud.empty or "Action" not in ud.columns:
            return None
        acts = ud.sort_index().tail(15)["Action"].astype(str).str.lower()
        return int((acts == "up").sum() - (acts == "down").sum())
    except Exception:  # noqa: BLE001
        return None


def _realized_vol(prices, ticker, window=21):
    """Annualised realized vol from the last `window` daily returns, or None."""
    p = prices[prices["ticker"] == ticker].sort_values("date")["adj_close"].astype(float)
    if len(p) < window + 1:
        return None
    rets = p.pct_change().dropna().tail(window)
    if rets.empty:
        return None
    sd = float(rets.std())
    return sd * math.sqrt(252) if sd > 0 else None


def extras_for(ticker, prices, cfg, yft=None):
    """Return the IV + short-interest overlay for one ticker (all fields optional)."""
    import yfinance as yf

    rec = {"short_pct_float": None, "short_ratio": None, "iv_atm": None,
           "iv_vs_realized": None, "analyst_net": None, "flags": []}
    try:
        yft = yft or yf.Ticker(ticker)
        info = yft.info or {}
        spf, sr = info.get("shortPercentOfFloat"), info.get("shortRatio")
        rec["short_pct_float"] = round(float(spf), 4) if spf else None
        rec["short_ratio"] = round(float(sr), 1) if sr else None
        spot = info.get("currentPrice") or info.get("regularMarketPrice")
        iv = _atm_iv(yft, float(spot)) if spot else None
        rec["iv_atm"] = round(iv, 4) if iv else None
        rec["analyst_net"] = _analyst_net(yft)
    except Exception:  # noqa: BLE001
        pass

    rv = _realized_vol(prices, ticker)
    if rec["iv_atm"] and rv:
        rec["iv_vs_realized"] = round(rec["iv_atm"] / rv, 2)

    if rec["short_pct_float"] is not None and rec["short_pct_float"] >= cfg.high_short_pct_float:
        rec["flags"].append(f"high short interest ({rec['short_pct_float'] * 100:.0f}% of float)")
    if rec["iv_vs_realized"] is not None and rec["iv_vs_realized"] >= cfg.iv_rich_ratio:
        rec["flags"].append("rich options (IV >> realized — big move priced in)")
    if rec["analyst_net"] is not None and rec["analyst_net"] >= 2:
        rec["flags"].append(f"analysts upgrading (+{rec['analyst_net']} net)")
    elif rec["analyst_net"] is not None and rec["analyst_net"] <= -2:
        rec["flags"].append(f"analysts downgrading ({rec['analyst_net']} net)")
    return rec


def build_extras_map(tickers, prices, cfg):
    """{ticker: {short_pct_float, short_ratio, iv_atm, iv_vs_realized, flags}} — best-effort."""
    return {t: extras_for(t, prices, cfg) for t in tickers}
