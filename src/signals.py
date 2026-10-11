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


def technical_buy_candidates(prices, shortlist, cfg):
    """Set of shortlist tickers whose *technical* signal (pre-news, pre-gates) is BUY — the only
    names where a news read can flip the decision (news only downgrades BUY -> HOLD). Used to focus
    paid Claude sentiment scoring on names that matter, letting the rest take the free keyword scan."""
    out = set()
    for t in shortlist["ticker"].tolist():
        ind = _indicators(prices[prices["ticker"] == t], cfg)
        if ind is None:
            continue
        if generate_signal(ind, cfg)[0] == "BUY":
            out.add(t)
    return out


def size_position(entry, atr, cfg, fx_rate, capital_gbp=None, max_positions=None):
    """Return sizing dict in USD + GBP, or None if unsizable. `capital_gbp` / `max_positions`
    default to the configured account; the backtest passes its running equity instead."""
    if not atr or atr <= 0 or entry <= 0:
        return None
    capital_gbp = cfg.capital_gbp if capital_gbp is None else capital_gbp
    max_positions = cfg.max_positions if max_positions is None else max_positions
    stop_dist = cfg.atr_stop_mult * atr
    capital_usd = capital_gbp * fx_rate
    risk_usd = capital_gbp * cfg.risk_per_trade * fx_rate          # £750 in USD
    per_pos_cap = capital_usd / max_positions                      # equal-weight slot
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


def entry_gate_failures(ind, spy_mom3, conviction, cfg):
    """Entry-quality gates a technical BUY must also clear: minimum confidence, relative strength
    (beating the S&P over 3 months) and above-average volume. Returns the plain-English failures
    (empty = passes). Shared by live signals and the backtest, so both apply identical gates."""
    fails = []
    if conviction < cfg.min_conviction:
        fails.append(f"confidence {conviction}% below the {cfg.min_conviction}% bar")
    if cfg.require_rel_strength and not ((ind.get("mom_3m") or 0) > spy_mom3):
        fails.append("not beating the S&P (weak relative strength)")
    _v, _v20 = ind.get("vol"), ind.get("vol20")
    if cfg.require_volume_confirm and not (
            _v is not None and _v20 is not None and not np.isnan(_v)
            and not np.isnan(_v20) and _v > _v20):
        fails.append("volume below its 20-day average")
    return fails


def edge_multiplier(conviction, cfg):
    """C3: edge-weighted sizing — scale size by confidence (a fractional-Kelly proxy), so marginal
    setups get less capital: the floor fraction at the minimum confidence, full size at 100%."""
    if not cfg.edge_weighted_sizing:
        return 1.0
    span = max(1, 100 - cfg.min_conviction)
    frac = min(1.0, max(0.0, (conviction - cfg.min_conviction) / span))
    return cfg.edge_size_floor + (1 - cfg.edge_size_floor) * frac


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
                  earnings_map=None, news_map=None, extras_map=None, pead_map=None,
                  holdings=None):
    """shortlist: DataFrame with [rank, ticker, beta]. Applies the macro layers:
    Layer A (regime gate), Layer B (earnings + macro event risk), Layer C (news),
    Layer E (post-earnings drift / PEAD), plus the B2/B3 overlay (options IV + short
    interest) as informational flags. `holdings` = positions already held (ledger.holdings) —
    they count toward the position, sector, heat and correlation caps when picking."""
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

        # Entry-quality gates (shared with the backtest): relative strength (beating the S&P),
        # above-average volume, and a minimum confidence.
        if signal == "BUY":
            fails = entry_gate_failures(ind, spy_mom3, conviction, cfg)
            if fails:
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

        # C3: edge-weighted sizing — marginal setups get less capital (never above full risk).
        if signal == "BUY":
            eff_mult *= edge_multiplier(conviction, cfg)

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
    return _select_portfolio(df, prices, cfg, cfg.max_positions, cfg.capital_gbp, holdings)


def choose_positions(cands, held, cfg, max_positions, heat_cap, corr=None):
    """Fill the free slots from `cands` on top of what's already `held`. Both are lists of dicts
    {ticker, sector, risk} (risk in the same currency as `heat_cap`); `cands` are best-first.

    Every cap counts the holdings — the slot cap, the per-sector cap, the total-risk (heat) ceiling
    and the correlation cap — so following each day's picks can never stack the book past the
    limits. `corr(a, b)` returns the return correlation of two tickers, or None if unknown.
    Shared by live selection and the backtest. Returns (picked tickers, {ticker: why skipped})."""
    picked, skipped = [], {}
    book = list(held)
    heat = sum(h["risk"] for h in held)
    for c in cands:
        t = c["ticker"]
        if len(book) >= max_positions:
            skipped[t] = (f"portfolio full ({len(held)} held"
                          + (f" + {len(picked)} new" if picked else "") + f" of {max_positions} slots)")
            continue
        if sum(1 for b in book if b["sector"] == c["sector"]) >= cfg.max_per_sector:
            skipped[t] = f"sector full ({cfg.max_per_sector} in {c['sector']})"
            continue  # keep the portfolio diversified
        if c["risk"] <= 0:
            skipped[t] = "too small to size at this capital"
            continue
        if heat_cap and heat + c["risk"] > heat_cap:
            skipped[t] = "would breach the total-risk (heat) ceiling"
            continue  # a smaller pick further down may still fit
        if corr is not None:
            near = [(b["ticker"], corr(t, b["ticker"])) for b in book]
            near = [(bt, v) for bt, v in near if v is not None and abs(v) >= cfg.max_position_correlation]
            if near:
                skipped[t] = f"moves in lockstep with {near[0][0]} (correlation {near[0][1]:.2f})"
                continue  # same bet in disguise — skip for real diversification
        picked.append(t)
        book.append(c)
        heat += c["risk"]
    return picked, skipped


def _held_book(holdings):
    """ledger.holdings() frame -> the [{ticker, sector, risk}] list choose_positions counts."""
    if holdings is None or len(holdings) == 0:
        return []
    out = []
    for _, h in holdings.iterrows():
        _rg = pd.to_numeric(h.get("risk_gbp"), errors="coerce")
        out.append({"ticker": h["ticker"],
                    "sector": h.get("sector") if isinstance(h.get("sector"), str) and h.get("sector") else "Unknown",
                    "risk": float(_rg) if pd.notna(_rg) else 0.0})
    return out


def _select_portfolio(df, prices, cfg, max_positions, capital_gbp, holdings=None):
    """Fill the free slots from the BUY signals by rank, on top of the positions already held,
    subject to the sector cap, the portfolio-heat ceiling (scaled by `capital_gbp`) and the
    correlation cap. Sets `selected`, plus `select_note` saying why a BUY wasn't picked.
    Split out from build_signals so the dashboard what-if can re-select for a different slot
    count / capital without re-running the whole engine."""
    df = df.copy()
    df["selected"] = False
    df["select_note"] = ""
    held = _held_book(holdings)
    held_tickers = {h["ticker"] for h in held}
    cands = []
    for i, r in df[df["signal"] == "BUY"].sort_values("rank").iterrows():
        if r["ticker"] in held_tickers:
            df.loc[i, "select_note"] = "already held"
            continue
        _rg = r.get("risk_gbp")
        cands.append({"ticker": r["ticker"], "sector": r.get("sector", "Unknown"),
                      "risk": float(_rg) if pd.notna(_rg) else 0.0})
    # C2: return correlations over the candidates AND the holdings, to avoid stacking one macro bet.
    cm = _return_corr(prices, [c["ticker"] for c in cands] + sorted(held_tickers), cfg.corr_window)

    def corr(a, b):
        if cm is None or a not in cm.index or b not in cm.columns or pd.isna(cm.loc[a, b]):
            return None
        return float(cm.loc[a, b])

    picked, skipped = choose_positions(cands, held, cfg, max_positions,
                                       cfg.max_portfolio_heat * capital_gbp, corr)
    sel = df["ticker"].isin(picked) & (df["signal"] == "BUY")
    df.loc[sel, "selected"] = True
    for t, why in skipped.items():
        df.loc[(df["ticker"] == t) & (df["signal"] == "BUY"), "select_note"] = why
    return df


def resize_and_select(sig, prices, cfg, capital_gbp, max_positions, fx_rate, holdings=None):
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
    return _select_portfolio(df, prices, cfg, max_positions, capital_gbp, holdings)
