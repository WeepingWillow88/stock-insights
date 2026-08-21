"""Phase 2: buy/sell/hold signals + position sizing.

Signal logic (interpretable, rules-based — a foundation the Phase 3 news layer
will adjust):
  SELL  : price below 50-day average (trend broken), or weak & not trending
  HOLD  : uptrend but overbought (wait for pullback) or momentum cooling
  BUY   : healthy uptrend + positive momentum + RSI in a constructive band

Position sizing uses the user's rules:
  - risk per trade = 1.5% of £50,000 = £750, converted to USD
  - stop = entry - 2 x ATR   (wide stop suits high-beta swings)
  - target = entry + 2 x stop distance  (2:1 reward:risk)
  - shares = min(risk-based, equal-weight capital slot); never exceeds capital
"""
import math

import numpy as np
import pandas as pd


def _rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    # No down-days -> avg_loss 0 -> RSI is 100 (maximally overbought), not NaN
    return rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)


def _atr_abs(high, low, close, window=14):
    df = pd.concat([high, low, close], axis=1, keys=["h", "l", "c"]).dropna()
    if len(df) < window + 1:
        return np.nan
    prev = df["c"].shift(1)
    tr = pd.concat([df["h"] - df["l"], (df["h"] - prev).abs(), (df["l"] - prev).abs()],
                   axis=1).max(axis=1)
    return float(tr.tail(window).mean())


def _indicators(p, cfg):
    """p = one ticker's price rows (sorted by date). Returns latest-bar indicators."""
    p = p.sort_values("date")
    adj = p["adj_close"].astype(float)
    if len(adj) < 60:
        return None
    # Trend/RSI/momentum are judged on the split/dividend-adjusted series (`price`), so the
    # comparison to the SMAs is like-for-like. `entry` stays the raw last close — the actual
    # tradeable price you'd place an order at (and what the ATR-based stop/target hang off).
    price = float(adj.iloc[-1])
    entry = float(p["close"].astype(float).iloc[-1])
    sma20 = float(adj.tail(20).mean())
    sma50 = float(adj.tail(50).mean())
    sma200 = float(adj.tail(200).mean()) if len(adj) >= 200 else np.nan
    rsi_series = _rsi(adj, cfg.rsi_period)
    rsi = float(rsi_series.iloc[-1]) if not np.isnan(rsi_series.iloc[-1]) else np.nan
    mom_3m = float(adj.iloc[-1] / adj.iloc[-63] - 1) if len(adj) >= 63 else np.nan
    mom_1m = float(adj.iloc[-1] / adj.iloc[-21] - 1) if len(adj) >= 21 else np.nan
    atr = _atr_abs(p["high"].astype(float), p["low"].astype(float), p["close"].astype(float))
    return {
        "price": price, "entry": entry, "sma20": sma20, "sma50": sma50, "sma200": sma200,
        "rsi": rsi, "mom_3m": mom_3m, "mom_1m": mom_1m, "atr": atr,
    }


def generate_signal(ind, cfg):
    price, sma50, sma200 = ind["price"], ind["sma50"], ind["sma200"]
    rsi, mom_3m, mom_1m = ind["rsi"], ind["mom_3m"], ind["mom_1m"]
    uptrend = price > sma50 and (np.isnan(sma200) or sma50 > sma200)
    pos_mom = (mom_3m or 0) > 0 or (mom_1m or 0) > 0

    if price < sma50:
        return "SELL", "Price below 50-day average — trend broken; exit / avoid"
    if not np.isnan(rsi) and rsi < 35 and not uptrend:
        return "SELL", f"Weak (RSI {rsi:.0f}) and not trending up"
    if uptrend and pos_mom:
        if not np.isnan(rsi) and rsi >= cfg.rsi_overbought:
            return "HOLD", f"Uptrend but overbought (RSI {rsi:.0f}) — wait for a pullback"
        if not np.isnan(rsi) and rsi < cfg.rsi_min_buy:
            return "HOLD", f"Uptrend but momentum cooling (RSI {rsi:.0f}) — watch for re-entry"
        return "BUY", f"Healthy uptrend, RSI {rsi:.0f}, positive momentum"
    return "HOLD", "No clear setup — sideways / mixed signals"


def size_position(entry, atr, cfg, fx_rate):
    """Return sizing dict in USD + GBP, or None if unsizable."""
    if not atr or atr <= 0 or entry <= 0:
        return None
    stop_dist = cfg.atr_stop_mult * atr
    capital_usd = cfg.capital_gbp * fx_rate
    risk_usd = cfg.capital_gbp * cfg.risk_per_trade * fx_rate      # £750 in USD
    per_pos_cap = capital_usd / cfg.max_positions                  # equal-weight slot
    shares_by_risk = risk_usd / stop_dist
    shares_by_cap = per_pos_cap / entry
    shares = int(math.floor(min(shares_by_risk, shares_by_cap)))
    if shares < 1:
        return None
    actual_risk_usd = shares * stop_dist
    return {
        "entry": round(entry, 2),
        "stop": round(entry - stop_dist, 2),
        "target": round(entry + cfg.reward_risk * stop_dist, 2),
        "stop_dist": round(stop_dist, 2),
        "shares": shares,
        "pos_value_usd": round(shares * entry, 0),
        "risk_usd": round(actual_risk_usd, 0),
        "risk_gbp": round(actual_risk_usd / fx_rate, 0),
        "rr": cfg.reward_risk,
        "binding": "risk" if shares_by_risk <= shares_by_cap else "capital",
    }


def _conviction(ind, nws, cfg):
    """0–100% agreement across independent checks — a plain 'how sure are we'."""
    price, sma50, sma200 = ind["price"], ind["sma50"], ind["sma200"]
    rsi, mom_3m, mom_1m = ind["rsi"], ind["mom_3m"], ind["mom_1m"]
    checks = [
        price > sma50 and (np.isnan(sma200) or sma50 > sma200),   # trend
        (mom_3m or 0) > 0,                                        # medium-term momentum
        (mom_1m or 0) > 0,                                        # short-term momentum
        (not np.isnan(rsi)) and cfg.rsi_min_buy <= rsi < cfg.rsi_overbought,  # healthy RSI
        (nws is None) or nws.get("label") != "negative",         # news not against it
    ]
    return int(round(sum(bool(c) for c in checks) / len(checks) * 100))


def _scale_sizing(sizing, mult, fx_rate):
    """Shrink a position by a size multiplier (regime / event). None if it rounds to 0."""
    shares = int(math.floor(sizing["shares"] * mult))
    if shares < 1:
        return None
    stop_dist = sizing["entry"] - sizing["stop"]
    s = dict(sizing)
    s["shares"] = shares
    s["pos_value_usd"] = round(shares * sizing["entry"], 0)
    s["risk_usd"] = round(shares * stop_dist, 0)
    s["risk_gbp"] = round(shares * stop_dist / fx_rate, 0)
    return s


def build_signals(prices, shortlist, cfg, fx_rate, regime=None, macro_events=None,
                  earnings_map=None, news_map=None):
    """shortlist: DataFrame with [rank, ticker, beta]. Applies the macro layers:
    Layer A (regime gate), Layer B (earnings + macro event risk), Layer C (news)."""
    macro_events = macro_events or []
    earnings_map = earnings_map or {}
    news_map = news_map or {}
    regime_label = (regime or {}).get("label", "RISK-ON")
    regime_mult = (regime or {}).get("size_multiplier", 1.0)

    imminent = [e for e in macro_events if e["days_until"] <= cfg.macro_event_sizedown_days]
    event_mult = 0.5 if imminent else 1.0

    rows = []
    for _, sr in shortlist.sort_values("rank").iterrows():
        t = sr["ticker"]
        p = prices[prices["ticker"] == t]
        ind = _indicators(p, cfg)
        if ind is None:
            continue
        signal, reason = generate_signal(ind, cfg)
        flags = []

        # Layer B: earnings blackout
        ern = earnings_map.get(t)
        if ern:
            flags.append(f"Earnings in {ern['days_until']}d")
            if signal == "BUY" and ern["days_until"] <= cfg.earnings_block_days:
                signal = "HOLD"
                reason = f"Earnings in {ern['days_until']}d — avoid a new swing; " + reason

        # Layer B: imminent macro events (portfolio-wide)
        for e in imminent:
            flags.append(f"{e['label']} in {e['days_until']}d")

        # Layer C: news sentiment
        nws = news_map.get(t)
        if nws:
            lbl = nws.get("label", "")
            bias = nws.get("action_bias", "none")
            if lbl and lbl != "neutral":
                flags.append(f"news:{lbl}")
            if signal == "BUY" and bias == "avoid" and cfg.news_avoid_downgrades_buy:
                signal = "HOLD"
                reason = (f"Fresh negative news ({nws.get('macro_driver', 'company_specific')}) — "
                          f"{nws.get('rationale', '')[:70]}; ") + reason
            elif signal == "BUY" and bias == "caution_hold":
                reason += " (news caution)"

        # Layer A: regime gate
        eff_mult = 1.0
        if signal == "BUY":
            if regime_label == "RISK-OFF":
                signal = "HOLD"
                reason = "Market risk-off — new buys paused; " + reason
            else:
                eff_mult = regime_mult * event_mult
                if regime_label == "CAUTION":
                    reason += " (market caution — size reduced)"
                if imminent:
                    reason += " (major data imminent — size reduced)"

        sizing = size_position(ind["entry"], ind["atr"], cfg, fx_rate) if signal == "BUY" else None
        if sizing and eff_mult < 1.0:
            sizing = _scale_sizing(sizing, eff_mult, fx_rate)
            if sizing is None:
                signal = "HOLD"
                reason = "Sized to zero by market conditions; " + reason

        row = {
            "rank": int(sr["rank"]),
            "ticker": t,
            "beta": sr.get("beta", np.nan),
            "signal": signal,
            "reason": reason,
            "sector": sr.get("sector", "Unknown"),
            "conviction": _conviction(ind, nws, cfg),
            "flags": ", ".join(flags),
            "news": (news_map.get(t, {}).get("label", "") if news_map.get(t) else ""),
            "news_note": (news_map.get(t, {}).get("rationale", "")[:140] if news_map.get(t) else ""),
            "rsi": round(ind["rsi"], 0) if not np.isnan(ind["rsi"]) else np.nan,
            "price": round(ind["entry"], 2),
            "entry": np.nan, "stop": np.nan, "target": np.nan,
            "shares": np.nan, "pos_value_usd": np.nan,
            "risk_usd": np.nan, "risk_gbp": np.nan, "binding": "",
        }
        if sizing:
            row.update({k: sizing[k] for k in
                        ["entry", "stop", "target", "shares", "pos_value_usd",
                         "risk_usd", "risk_gbp", "binding"]})
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Portfolio selection: top BUY signals by rank, capped per sector so 8 slots aren't
    # secretly one bet (e.g. all semiconductors).
    df["selected"] = False
    sector_count = {}
    chosen = 0
    heat_gbp = 0.0
    heat_cap_gbp = cfg.max_portfolio_heat * cfg.capital_gbp  # total £-at-risk ceiling
    for i, r in df[df["signal"] == "BUY"].sort_values("rank").iterrows():
        if chosen >= cfg.max_positions:
            break
        sec = r.get("sector", "Unknown")
        if sector_count.get(sec, 0) >= cfg.max_per_sector:
            continue  # sector full — skip to keep the portfolio diversified
        r_gbp = float(r.get("risk_gbp") or 0)
        if heat_cap_gbp and heat_gbp + r_gbp > heat_cap_gbp:
            continue  # would breach the portfolio-heat ceiling — skip (a smaller pick may still fit)
        df.loc[i, "selected"] = True
        sector_count[sec] = sector_count.get(sec, 0) + 1
        heat_gbp += r_gbp
        chosen += 1
    return df
