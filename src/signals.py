"""Phase 2: buy/sell/hold signals + position sizing.

Signal logic (interpretable, rules-based — a foundation the Phase 3 news layer
will adjust):
  SELL  : price below 50-day average (trend broken), or weak & not trending
  HOLD  : uptrend but overbought (wait for pullback) or momentum cooling
  BUY   : healthy uptrend + positive momentum + RSI in a constructive band

Position sizing uses the user's rules:
  - risk per trade = 1.5% of capital (£750 at the default £50,000; CAPITAL_GBP override), USD
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
    volume = p["volume"].astype(float)
    vol_last = float(volume.iloc[-1]) if len(volume) else np.nan
    vol20 = float(volume.tail(20).mean()) if len(volume) >= 20 else np.nan
    return {
        "price": price, "entry": entry, "sma20": sma20, "sma50": sma50, "sma200": sma200,
        "rsi": rsi, "mom_3m": mom_3m, "mom_1m": mom_1m, "atr": atr,
        "vol": vol_last, "vol20": vol20,
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


def _apply_pead(conviction, pead, cfg):
    """Adjust confidence for a fresh post-earnings drift (PEAD). A positive drift ADDS conviction
    (the one place a good earnings event is allowed to help — it's momentum-aligned); a negative
    drift subtracts it. Clamped to 0–100 so it flows cleanly into the buy gate and edge-sizing."""
    if not pead or not cfg.pead_enabled:
        return conviction
    if pead["label"] == "positive":
        return min(100, conviction + cfg.pead_conviction_bonus)
    if pead["label"] == "negative":
        return max(0, conviction - cfg.pead_conviction_penalty)
    return conviction


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


def _return_corr(prices, tickers, window):
    """Correlation matrix of recent daily returns for `tickers` (or None if too little data)."""
    try:
        piv = (prices[prices["ticker"].isin(tickers)]
               .pivot_table(index="date", columns="ticker", values="adj_close").sort_index())
    except Exception:  # noqa: BLE001
        return None
    if piv.shape[1] < 2 or len(piv) < 20:
        return None
    rets = piv.pct_change().dropna(how="all").tail(window)
    if len(rets) < 10:
        return None
    return rets.corr()


def build_signals(prices, shortlist, cfg, fx_rate, regime=None, macro_events=None,
                  earnings_map=None, news_map=None, extras_map=None, pead_map=None):
    """shortlist: DataFrame with [rank, ticker, beta]. Applies the macro layers:
    Layer A (regime gate), Layer B (earnings + macro event risk), Layer C (news),
    Layer E (post-earnings drift / PEAD), plus the B2/B3 overlay (options IV + short
    interest) as informational flags."""
    macro_events = macro_events or []
    earnings_map = earnings_map or {}
    news_map = news_map or {}
    extras_map = extras_map or {}
    pead_map = pead_map or {}
    regime_label = (regime or {}).get("label", "RISK-ON")
    regime_mult = (regime or {}).get("size_multiplier", 1.0)

    imminent = [e for e in macro_events if e["days_until"] <= cfg.macro_event_sizedown_days]
    event_mult = 0.5 if imminent else 1.0

    # Benchmark 3-month momentum, for the relative-strength gate (only buy names beating the S&P).
    _bench = prices[prices["ticker"] == cfg.benchmark].sort_values("date")["adj_close"].astype(float)
    spy_mom3 = float(_bench.iloc[-1] / _bench.iloc[-63] - 1) if len(_bench) >= 63 else 0.0

    rows = []
    for _, sr in shortlist.sort_values("rank").iterrows():
        t = sr["ticker"]
        p = prices[prices["ticker"] == t]
        ind = _indicators(p, cfg)
        if ind is None:
            continue
        signal, reason = generate_signal(ind, cfg)
        nws = news_map.get(t)
        pead = pead_map.get(t)
        # Base confidence (5 technical/news checks), then nudge for a fresh post-earnings drift so
        # the buy gate and edge-weighted sizing both see the adjusted score.
        conviction = _apply_pead(_conviction(ind, nws, cfg), pead, cfg)
        flags = []

        # Entry-quality gates — mirror the backtested rules so a live BUY == what was validated:
        # relative strength (beating the S&P), above-average volume, and a minimum confidence.
        if signal == "BUY":
            rs_ok = (not cfg.require_rel_strength) or ((ind.get("mom_3m") or 0) > spy_mom3)
            _v, _v20 = ind.get("vol"), ind.get("vol20")
            vol_ok = (not cfg.require_volume_confirm) or (
                _v is not None and _v20 is not None and not np.isnan(_v)
                and not np.isnan(_v20) and _v > _v20)
            conv_ok = conviction >= cfg.min_conviction
            if not (rs_ok and vol_ok and conv_ok):
                fails = []
                if not conv_ok:
                    fails.append(f"confidence {conviction}% below the {cfg.min_conviction}% bar")
                if not rs_ok:
                    fails.append("not beating the S&P (weak relative strength)")
                if not vol_ok:
                    fails.append("volume below its 20-day average")
                signal = "HOLD"
                reason = "Setup too weak to buy — " + "; ".join(fails)

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

        # Layer C: news sentiment (nws fetched above for conviction)
        if nws:
            lbl = nws.get("label", "")
            bias = nws.get("action_bias", "none")
            # Only a real NLP engine (Claude / FinBERT) may BLOCK a technically valid BUY. The
            # keyword fallback is too crude to trust with that veto (it can't read "reiterated Buy
            # rating" and mis-scores a neutral tape as negative), so it may only add a caution note.
            trusted = nws.get("source") in ("claude", "finbert")
            if lbl and lbl != "neutral":
                flags.append(f"news:{lbl}")
            if signal == "BUY" and bias == "avoid" and cfg.news_avoid_downgrades_buy and trusted:
                signal = "HOLD"
                reason = (f"Fresh negative news ({nws.get('macro_driver', 'company_specific')}) — "
                          f"{nws.get('rationale', '')[:70]}; ") + reason
            elif signal == "BUY" and bias in ("avoid", "caution_hold"):
                reason += " (news caution — keyword scan only)" if not trusted else " (news caution)"

        # Layer E: post-earnings drift (PEAD). The conviction was already nudged above; here we
        # surface it and let a *strong* negative gap veto a fresh long (drift is against it).
        if pead and cfg.pead_enabled:
            flags.append(pead["flag"])
            if (signal == "BUY" and pead["label"] == "negative" and pead["strong"]
                    and cfg.pead_avoid_downgrades_buy):
                signal = "HOLD"
                reason = (f"Gapped {pead['gap_ret']:+.0%} on earnings {pead['days_since']}d ago — "
                          "post-earnings drift runs against a fresh long; ") + reason
            elif signal == "BUY" and pead["label"] == "positive":
                reason += " (post-earnings drift tailwind)"

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

        # C3: edge-weighted sizing — scale size by confidence (a fractional-Kelly proxy), so
        # marginal setups get less capital. Never exceeds the full per-trade risk.
        if signal == "BUY" and cfg.edge_weighted_sizing:
            span = max(1, 100 - cfg.min_conviction)
            frac = min(1.0, max(0.0, (conviction - cfg.min_conviction) / span))
            eff_mult *= cfg.edge_size_floor + (1 - cfg.edge_size_floor) * frac

        sizing = size_position(ind["entry"], ind["atr"], cfg, fx_rate) if signal == "BUY" else None
        if sizing and eff_mult < 1.0:
            sizing = _scale_sizing(sizing, eff_mult, fx_rate)
            if sizing is None:
                signal = "HOLD"
                reason = "Sized to zero by market conditions; " + reason

        ex = extras_map.get(t) or {}
        if ex.get("flags"):
            flags.extend(ex["flags"])

        row = {
            "rank": int(sr["rank"]),
            "ticker": t,
            "beta": sr.get("beta", np.nan),
            "signal": signal,
            "reason": reason,
            "sector": sr.get("sector", "Unknown"),
            "conviction": conviction,
            "flags": ", ".join(flags),
            "short_pct_float": ex.get("short_pct_float"),
            "short_ratio": ex.get("short_ratio"),
            "iv_atm": ex.get("iv_atm"),
            "iv_vs_realized": ex.get("iv_vs_realized"),
            "analyst_net": ex.get("analyst_net"),
            "pead": (pead.get("label") if pead else ""),
            "pead_gap": (pead.get("gap_ret") if pead else np.nan),
            "pead_surprise": (pead.get("surprise_pct") if pead else np.nan),
            "pead_days": (pead.get("days_since") if pead else np.nan),
            "news": (news_map.get(t, {}).get("label", "") if news_map.get(t) else ""),
            "news_note": (news_map.get(t, {}).get("rationale", "")[:140] if news_map.get(t) else ""),
            "rsi": round(ind["rsi"], 0) if not np.isnan(ind["rsi"]) else np.nan,
            "price": round(ind["entry"], 2),
            "entry": np.nan, "stop": np.nan, "target": np.nan,
            "shares": np.nan, "pos_value_usd": np.nan,
            "risk_usd": np.nan, "risk_gbp": np.nan, "binding": "",
            # Helper fields for the dashboard's capital / max-positions what-if: stop_dist and the
            # (capital-independent) size multiplier let sizing be recomputed offline without a re-run.
            "stop_dist": np.nan, "eff_mult": round(eff_mult, 4) if signal == "BUY" else np.nan,
        }
        if sizing:
            row.update({k: sizing[k] for k in
                        ["entry", "stop", "target", "shares", "pos_value_usd",
                         "risk_usd", "risk_gbp", "binding", "stop_dist"]})
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return _select_portfolio(df, prices, cfg, cfg.max_positions, cfg.capital_gbp)


def _select_portfolio(df, prices, cfg, max_positions, capital_gbp):
    """Fill up to `max_positions` slots from the BUY signals by rank, subject to the sector cap,
    the portfolio-heat ceiling (scaled by `capital_gbp`), and the correlation cap. Sets `selected`.
    Split out from build_signals so the dashboard what-if can re-select for a different slot
    count / capital without re-running the whole engine."""
    df = df.copy()
    df["selected"] = False
    sector_count = {}
    chosen = 0
    heat_gbp = 0.0
    heat_cap_gbp = cfg.max_portfolio_heat * capital_gbp  # total £-at-risk ceiling
    # C2: return-correlation matrix over the BUY candidates, to avoid stacking one macro bet.
    corr = _return_corr(prices, df[df["signal"] == "BUY"]["ticker"].tolist(), cfg.corr_window)
    picked = []
    for i, r in df[df["signal"] == "BUY"].sort_values("rank").iterrows():
        if chosen >= max_positions:
            break
        sec = r.get("sector", "Unknown")
        if sector_count.get(sec, 0) >= cfg.max_per_sector:
            continue  # sector full — skip to keep the portfolio diversified
        _rg = r.get("risk_gbp")
        r_gbp = float(_rg) if pd.notna(_rg) else 0.0
        if r_gbp <= 0:
            continue  # unsized (too small at this capital/slot) — can't take it
        if heat_cap_gbp and heat_gbp + r_gbp > heat_cap_gbp:
            continue  # would breach the portfolio-heat ceiling — skip (a smaller pick may still fit)
        t = r["ticker"]
        if corr is not None and picked and t in corr.index:  # correlation cap
            near = [abs(corr.loc[t, s]) for s in picked if s in corr.columns]
            if near and max(near) >= cfg.max_position_correlation:
                continue  # moves in lockstep with a pick already held — skip for real diversification
        df.loc[i, "selected"] = True
        sector_count[sec] = sector_count.get(sec, 0) + 1
        heat_gbp += r_gbp
        picked.append(t)
        chosen += 1
    return df


def resize_and_select(sig, prices, cfg, capital_gbp, max_positions, fx_rate):
    """What-if recompute: re-size every BUY and re-pick the portfolio for a different trading
    capital / slot count, using the already-computed signals. It touches ONLY sizing (shares, £
    risk, position value) and selection — the BUY/HOLD/SELL call, conviction, stop and target are
    capital-independent and left untouched, and nothing is written to the DB or the ledger.

    Relies on the `stop_dist` + `eff_mult` helper columns; if they're absent (signals produced
    before this feature) the frame is returned unchanged so the caller can prompt for a refresh."""
    df = sig.copy()
    if df.empty or "stop_dist" not in df.columns or "eff_mult" not in df.columns:
        return df
    max_positions = max(1, int(max_positions))
    risk_usd = capital_gbp * cfg.risk_per_trade * fx_rate       # per-trade £ risk, in USD
    per_pos_cap = (capital_gbp * fx_rate) / max_positions       # equal-weight capital slot
    for i, r in df.iterrows():
        if r.get("signal") != "BUY":
            continue
        entry = float(r.get("entry") or 0)
        sd = float(r.get("stop_dist") or 0)
        if entry <= 0 or sd <= 0:
            continue
        eff = float(r["eff_mult"]) if pd.notna(r.get("eff_mult")) else 1.0
        shares_by_risk = risk_usd / sd
        shares_by_cap = per_pos_cap / entry
        shares = int(math.floor(min(shares_by_risk, shares_by_cap)))   # base sizing
        if eff < 1.0:                                                  # apply the stored multiplier
            shares = int(math.floor(shares * eff))
        if shares < 1:                                                 # too small to size here
            df.loc[i, ["shares", "pos_value_usd", "risk_usd", "risk_gbp"]] = np.nan
            continue
        df.loc[i, "shares"] = shares
        df.loc[i, "pos_value_usd"] = round(shares * entry, 0)
        df.loc[i, "risk_usd"] = round(shares * sd, 0)
        df.loc[i, "risk_gbp"] = round(shares * sd / fx_rate, 0)
        df.loc[i, "binding"] = "risk" if shares_by_risk <= shares_by_cap else "capital"
    return _select_portfolio(df, prices, cfg, max_positions, capital_gbp)
