"""Backtester (v3): replay the LIVE rules as one account over ~10 years — and compare it honestly.

It runs the same functions the live engine runs, so the backtest and the app can't drift apart:
  • Universe — S&P 500 membership point-in-time (universe.sp500_history): a stock is only eligible
    while it was actually in the index, and beta / liquidity / momentum are measured with data
    available on the day. Former members that later left the index are included where free price
    history still exists.
  • Screen — screen.build_screen on each day's metrics: the top-75 shortlist and its ranking.
  • Signals — signals.generate_signal + _conviction + entry_gate_failures (confidence, relative
    strength, volume), and the composite market regime (regime.score_regime: RISK-OFF pauses new
    buys, CAUTION halves their size).
  • Sizing — signals.size_position on the running equity, x the regime and edge multipliers.
  • Portfolio — signals.choose_positions: max positions, sector cap, heat ceiling and correlation
    cap, all counting the positions already held.
  • Exits — ledger.exit_signal (trailing stop / trend break / time limit), flagged on the day it
    triggers and filled at the NEXT session's close, exactly like the live ledger; R and costs via
    ledger.trade_r. Idle cash earns T-bill returns (cfg.cash_ticker).

It reports the account's growth, CAGR and worst drawdown against simply holding the S&P 500 (SPY)
and cash, plus a random-entry baseline: the same shortlist, days and exits, but entries on random
days — the honest test of whether the entry signal beats chance.

NOT replayed (no free point-in-time source): news / sentiment, post-earnings drift, the earnings
blackout, macro-event half-sizing, options / short-interest flags. Residual survivorship: former
members with no free price history (bankruptcies, most takeovers) are still missing, which flatters
any stock-picking result. Entries are filled at the signal day's close, as the live ledger records
them. Past performance != future results.

Run:  python -m src.backtest
"""
import dataclasses
import datetime as dt

import numpy as np
import pandas as pd

from .config import CONFIG
from . import data, db, ledger, regime, screen, signals, universe

START_AFTER = 252   # sessions of warm-up so every indicator has the 1y history the live run sees


def _years(cfg):
    return int(str(cfg.backtest_years).rstrip("y"))


def _load(cfg, reuse_cache=True):
    """Membership history + prices for every company in the S&P 500 at any point in the window
    (where free data still exists), plus SPY, the regime gauges and the cash proxy. The download is
    cached in the DB (backtest_prices) and reused the same day for the same ticker set."""
    start = (pd.Timestamp.today() - pd.DateOffset(years=_years(cfg))).strftime("%Y-%m-%d")
    hist = universe.sp500_history(cfg.db_path, start)
    want = sorted(set(hist["ticker"]) | set(regime.MACRO_TICKERS) | {cfg.benchmark, cfg.cash_ticker})
    key = {"years": str(cfg.backtest_years), "tickers": ",".join(want),
           "built": dt.date.today().isoformat()}
    if (reuse_cache and db.table_exists("backtest_prices", cfg.db_path)
            and db.table_exists("backtest_meta", cfg.db_path)):
        meta = db.read_df("SELECT * FROM backtest_meta", cfg.db_path).iloc[0]
        if all(str(meta.get(k)) == v for k, v in key.items()):
            return db.read_df("SELECT * FROM backtest_prices", cfg.db_path), hist
    print(f"      downloading ~{cfg.backtest_years} of prices for {len(want)} tickers...")
    prices = data.download_prices(want, period=cfg.backtest_years, batch_size=cfg.batch_size)
    db.write_df(prices, "backtest_prices", cfg.db_path)
    db.write_df(pd.DataFrame([key]), "backtest_meta", cfg.db_path)
    return prices, hist


def _prep(prices, hist, cfg):
    """Wide matrices on the NYSE calendar, the point-in-time membership mask, and every input the
    live screen / signals / regime read — computed for all days at once, exactly as the live
    modules compute them for 'today' (metrics.compute_metrics, signals._indicators, regime)."""
    def piv(f):
        return prices.pivot_table(index="date", columns="ticker", values=f).sort_index()
    adj = piv("adj_close")
    if cfg.benchmark not in adj.columns:
        raise SystemExit(f"Benchmark {cfg.benchmark} missing from backtest data.")
    cal = adj[cfg.benchmark].dropna().index          # NYSE sessions (drops index-only holiday rows)
    adj = adj.loc[cal]
    close, high, low, vol = (piv(f).reindex(cal) for f in ("close", "high", "low", "volume"))

    # --- point-in-time membership: each monthly snapshot governs the sessions until the next ---
    snaps = sorted(hist["snap"].unique())
    latest = set(hist.loc[hist["snap"] == snaps[-1], "ticker"])
    last_member = hist.groupby("ticker")["snap"].max()
    members, former_dropped = [], []
    for t in sorted(set(hist["ticker"])):
        s = adj[t].dropna() if t in adj.columns else pd.Series(dtype=float)
        # A former member's data must cover its time in the index — otherwise the ticker has been
        # reused by another company, or only stray post-delisting quotes survive.
        if len(s) < 60 or (t not in latest and str(s.index[0]) > str(last_member[t])):
            if t not in latest:
                former_dropped.append(t)
            continue
        members.append(t)
    gov = np.clip(np.searchsorted(pd.to_datetime(snaps).values, pd.to_datetime(cal).values,
                                  side="right") - 1, 0, len(snaps) - 1)
    by_snap = hist.groupby("snap")["ticker"].apply(set)
    col = {t: j for j, t in enumerate(members)}
    mem = np.zeros((len(cal), len(members)), dtype=bool)
    for k, s in enumerate(snaps):
        js = [col[t] for t in by_snap[s] if t in col]
        mem[np.ix_(gov == k, js)] = True
    sector = (hist.sort_values("snap").groupby("ticker")["sector"].last()
              .reindex(members).fillna("Unknown").values)

    # --- split/dividend-adjusted OHLC for signals, fills and ATR (raw close x volume for $-volume) ---
    a = adj[members]
    f = (a / close[members]).replace([np.inf, -np.inf], np.nan)
    hi, lo = high[members] * f, low[members] * f
    prev = a.shift(1)
    tr = pd.DataFrame(np.fmax(np.fmax((hi - lo).values, (hi - prev).abs().values),
                              (lo - prev).abs().values), index=cal, columns=members)

    # --- screen metrics (metrics.compute_metrics) ---
    r = a.pct_change(fill_method=None)
    mkt = adj[cfg.benchmark].pct_change(fill_method=None)
    rm = r.where(mkt.notna(), axis=0)
    mm = pd.DataFrame(np.repeat(mkt.values[:, None], len(members), 1), index=cal,
                      columns=members).where(rm.notna())
    w = cfg.beta_window - 1   # the live run's 1y download is ~252 bars, i.e. ~251 daily returns
    beta = rm.rolling(w, min_periods=60).cov(mm, pairwise=False) / mm.rolling(w, min_periods=60).var()
    beta = cfg.beta_shrink * beta + (1 - cfg.beta_shrink) * 1.0       # Blume shrinkage
    sma20, sma50, sma200 = (a.rolling(n).mean() for n in (cfg.trend_exit_sma, 50, 200))
    mom3, mom1 = a / a.shift(62) - 1, a / a.shift(20) - 1             # = s.iloc[-1] / s.iloc[-63|-21]
    uptrend = (a > sma50) & (sma200.isna() | (sma50 > sma200))
    dvol = (close[members] * vol[members]).rolling(30, min_periods=1).mean()
    fresh = a.notna().rolling(cfg.stale_days, min_periods=1).sum() > 0
    screenable = mem & (a.notna().cumsum() >= 60).values & fresh.values & beta.notna().values

    # --- market inputs: relative strength, regime readings, cash ---
    spy = adj[cfg.benchmark]
    spy_mom3 = (spy / spy.shift(62) - 1).values
    mac = adj.ffill()

    def vs50(t):
        s = mac[t] if t in mac.columns else pd.Series(np.nan, index=cal)
        return s.values, s.rolling(50).mean().values
    above = (a > sma50).astype(float).where(sma50.notna() & a.notna()).where(mem)
    breadth = (above.sum(axis=1) / above.notna().sum(axis=1).replace(0, np.nan)).values
    tnx = mac["^TNX"] if "^TNX" in mac.columns else pd.Series(np.nan, index=cal)
    tnx = tnx.where(tnx <= 20, tnx / 10)                               # regime._norm_yield
    cash = mac[cfg.cash_ticker] if cfg.cash_ticker in mac.columns else pd.Series(1.0, index=cal)

    return {
        "dates": list(cal), "tickers": members, "sector": sector, "member": mem,
        "former_dropped": former_dropped, "former_used": [t for t in members if t not in latest],
        "a": a.values, "a_ff": a.ffill().values, "lo": lo.values, "vol": vol[members].values,
        "vol20": vol[members].rolling(20).mean().values, "atr": tr.rolling(14).mean().values,
        "sma20": sma20.values, "sma50": sma50.values, "sma200": sma200.values,
        "rsi": signals._rsi(a, cfg.rsi_period).values, "mom3": mom3.values, "mom1": mom1.values,
        "ret": r.values, "beta": beta.round(2).values, "price": close[members].round(2).values,
        "mom3_r": mom3.round(4).values, "dvol": dvol.values, "uptrend": uptrend.values,
        "screenable": screenable, "spy": spy.values, "spy_mom3": spy_mom3,
        "spy_sma": (spy.rolling(50).mean().values, spy.rolling(200).mean().values),
        "qqq": vs50("QQQ"), "smh": vs50("SMH"), "hyg": vs50("HYG"),
        "vix": (mac["^VIX"] if "^VIX" in mac.columns else pd.Series(np.nan, index=cal)).values,
        "tnx": (tnx.shift(19).values, tnx.values), "breadth": breadth,
        "cash_ret": cash.pct_change().fillna(0).values, "cash": cash.values,
    }


def _regime_by_day(P):
    """regime.score_regime on each day's readings -> (labels, size multipliers)."""
    def ok(*xs):
        return all(x is not None and not np.isnan(x) for x in xs)
    labels, mults = [], []
    for i in range(len(P["dates"])):
        s50, s200 = P["spy_sma"][0][i], P["spy_sma"][1][i]
        rd = {
            "spy": (P["spy"][i], s50, s200) if ok(P["spy"][i], s50, s200) else None,
            "qqq": (P["qqq"][0][i], P["qqq"][1][i]) if ok(*(x[i] for x in P["qqq"])) else None,
            "smh": (P["smh"][0][i], P["smh"][1][i]) if ok(*(x[i] for x in P["smh"])) else None,
            "hyg": (P["hyg"][0][i], P["hyg"][1][i]) if ok(*(x[i] for x in P["hyg"])) else None,
            "vix": P["vix"][i] if ok(P["vix"][i]) else None,
            "tnx": (P["tnx"][0][i], P["tnx"][1][i]) if ok(*(x[i] for x in P["tnx"])) else None,
            "breadth": P["breadth"][i] if ok(P["breadth"][i]) else None,
        }
        reg = regime.score_regime(rd)
        labels.append(reg["label"])
        mults.append(reg["size_multiplier"])
    return np.array(labels), np.array(mults)


def _daily_candidates(P, cfg, reg_label):
    """Each day: run the live screen, then the live signal + entry gates on the shortlist.
    Returns ({day: [(ticker idx, rank, conviction), ...]} of BUYs, shortlisted mask)."""
    n, m = len(P["dates"]), len(P["tickers"])
    cands, short = {}, np.zeros((n, m), dtype=bool)
    tick = np.array(P["tickers"])
    for i in range(START_AFTER, n):
        js = np.where(P["screenable"][i])[0]
        if not len(js):
            continue
        day = pd.DataFrame({"ticker": tick[js], "price": P["price"][i, js], "beta": P["beta"][i, js],
                            "mom_3m": P["mom3_r"][i, js], "avg_dollar_vol": P["dvol"][i, js],
                            "uptrend": P["uptrend"][i, js]})
        sl = screen.build_screen(day, cfg)
        if sl.empty:
            continue
        idx = {t: j for t, j in zip(tick[js], js)}
        buys = []
        for t, rank in zip(sl["ticker"], sl["rank"]):
            k = idx[t]
            short[i, k] = True
            ind = {"price": P["a"][i, k], "sma50": P["sma50"][i, k], "sma200": P["sma200"][i, k],
                   "rsi": P["rsi"][i, k], "mom_3m": P["mom3"][i, k], "mom_1m": P["mom1"][i, k],
                   "vol": P["vol"][i, k], "vol20": P["vol20"][i, k]}
            if any(np.isnan(ind[x]) for x in ("price", "sma50", "rsi")) or not P["atr"][i, k] > 0:
                continue
            if signals.generate_signal(ind, cfg)[0] != "BUY" or reg_label[i] == "RISK-OFF":
                continue
            conv = signals._conviction(ind, None, cfg)
            if signals.entry_gate_failures(ind, P["spy_mom3"][i], conv, cfg):
                continue
            buys.append((k, int(rank), conv))
        if buys:
            cands[i] = buys
    return cands, short


def _simulate_account(P, cands, reg_label, reg_mult, cfg):
    """One account, day by day, in the live pipeline's order: pick today's BUYs on top of the
    current book (open + pending sales), open them at today's close, then flag exits on today's
    bar and fill yesterday's flagged sales at today's close. Equity is in the stocks' currency,
    starting from the configured capital at the fallback FX rate; idle cash earns T-bill returns."""
    A, AF, LO, SMA, ATR, R = P["a"], P["a_ff"], P["lo"], P["sma20"], P["atr"], P["ret"]
    tick, sector, dates = P["tickers"], P["sector"], P["dates"]
    tix = {t: k for k, t in enumerate(tick)}
    n = len(dates)
    last_valid = np.array([np.max(np.where(~np.isnan(A[:, k]))[0]) for k in range(A.shape[1])])
    cash = cfg.capital_gbp * cfg.fx_fallback
    pos, trades, curve, n_held, invested, costs = {}, [], [], [], [], 0.0

    def px(k, i):
        return AF[i, k]           # last traded price (a holding can miss a session's bar)

    def close_out(k, i, price, why):
        nonlocal cash, costs
        p = pos.pop(k)
        cost = p["shares"] * p["entry"] * (cfg.backtest_cost_pct
                                           + cfg.backtest_spread_atr_coef * p["atr0"] / p["entry"])
        cash += p["shares"] * price - cost
        costs += cost
        r = ledger.trade_r(p["entry"], p["stop"], price, cfg)
        trades.append({"ticker": tick[k], "entry_date": dates[p["i"]], "exit_date": dates[i],
                       "entry": round(p["entry"], 2), "exit": round(price, 2),
                       "shares": p["shares"], "risk_usd": round(p["risk"], 0),
                       "r_multiple": round(r, 3), "return_pct": round((price / p["entry"] - 1) * 100, 2),
                       "outcome": "win" if r > 0 else "loss", "exit_reason": why,
                       "hold_days": i - p["i"], "regime": reg_label[p["i"]]})

    for i in range(START_AFTER, n):
        cash *= 1 + P["cash_ret"][i]
        equity = cash + sum(p["shares"] * px(k, i) for k, p in pos.items())
        # 1) today's BUYs, picked on top of the current book (pending sales still hold their slot)
        if i in cands:
            corr_cache = {}

            def corr(a_t, b_t):
                key = (a_t, b_t) if a_t < b_t else (b_t, a_t)
                if key not in corr_cache:
                    ka, kb = tix[a_t], tix[b_t]
                    w = R[max(0, i - cfg.corr_window + 1):i + 1]
                    x, y = w[:, ka], w[:, kb]
                    ok = ~(np.isnan(x) | np.isnan(y))
                    corr_cache[key] = (float(np.corrcoef(x[ok], y[ok])[0, 1])
                                       if ok.sum() >= 10 and x[ok].std() > 0 and y[ok].std() > 0 else None)
                return corr_cache[key]
            held = [{"ticker": tick[k], "sector": p["sector"], "risk": p["risk"]} for k, p in pos.items()]
            pool = []
            for k, rank, conv in sorted(cands[i], key=lambda c: c[1]):
                if k in pos:
                    continue
                sz = signals.size_position(A[i, k], ATR[i, k], cfg, 1.0, capital_gbp=equity)
                mult = reg_mult[i] * signals.edge_multiplier(conv, cfg)
                if sz is not None and mult < 1.0:
                    sz = signals._scale_sizing(sz, mult, 1.0)
                if sz is None:
                    continue
                pool.append({"ticker": tick[k], "sector": sector[k], "risk": float(sz["risk_usd"]),
                             "k": k, "sz": sz})
            picked, _ = signals.choose_positions(pool, held, cfg, cfg.max_positions,
                                                 cfg.max_portfolio_heat * equity, corr)
            for c in pool:
                if c["ticker"] in picked:
                    sz = c["sz"]
                    cash -= sz["shares"] * sz["entry"]
                    pos[c["k"]] = {"shares": sz["shares"], "entry": sz["entry"], "stop": sz["stop"],
                                   "atr0": (sz["entry"] - sz["stop"]) / cfg.atr_stop_mult,
                                   "hh": sz["entry"], "held": 0, "i": i, "risk": float(sz["risk_usd"]),
                                   "sector": c["sector"], "flag": None, "why": ""}
        # 2) exits — flag today's triggers, fill earlier flags at today's close (ledger order)
        for k in list(pos):
            p = pos[k]
            if i > last_valid[k]:                     # taken over / delisted: no more prices
                close_out(k, i, A[last_valid[k], k], "data ended (delisted / taken over)")
                continue
            if np.isnan(A[i, k]) or i == p["i"]:
                continue
            if p["flag"] is not None:
                close_out(k, i, A[i, k], p["why"])
                continue
            p["held"] += 1
            why, p["hh"] = ledger.exit_signal(p["stop"], p["atr0"], p["hh"], A[i, k], LO[i, k],
                                              SMA[i, k], p["held"], cfg)
            if why:
                p["flag"], p["why"] = i, why
        mv = sum(p["shares"] * px(k, i) for k, p in pos.items())
        curve.append(cash + mv)
        n_held.append(len(pos))
        invested.append(mv / (cash + mv) if cash + mv > 0 else 0.0)
    eq = pd.Series(curve, index=pd.to_datetime(dates[START_AFTER:]))
    return eq, pd.DataFrame(trades), {"avg_positions": float(np.mean(n_held)),
                                      "max_positions_held": int(np.max(n_held)),
                                      "avg_invested": float(np.mean(invested)), "costs": costs}


def _signal_trades(P, entry_ok, cfg, rng=None, p_rand=None):
    """Every qualifying entry per stock, one trade at a time, no account limits — the live exit
    model and fills, R via ledger.trade_r. With `rng`, enters on random eligible days instead
    (the null model): same stocks, same days on offer, same exits, no entry signal."""
    A, LO, SMA, ATR = P["a"], P["lo"], P["sma20"], P["atr"]
    n, m = A.shape
    out = []
    for k in range(m):
        i = START_AFTER
        while i < n - 1:
            go = (entry_ok[i, k] and rng.random() < p_rand) if rng is not None else entry_ok[i, k]
            if not go or np.isnan(A[i, k]) or not ATR[i, k] > 0:
                i += 1
                continue
            entry, atr0 = A[i, k], ATR[i, k]
            stop, hh, held, flag, why = entry - cfg.atr_stop_mult * atr0, entry, 0, None, ""
            for j in range(i + 1, n):
                if np.isnan(A[j, k]):
                    continue
                held += 1
                why, hh = ledger.exit_signal(stop, atr0, hh, A[j, k], LO[j, k], SMA[j, k], held, cfg)
                if why:
                    flag = j
                    break
            fill = next((f for f in range(flag + 1, n) if not np.isnan(A[f, k])), None) \
                if flag is not None else None
            if fill is None:
                break                                  # still open when the data ends
            out.append((P["tickers"][k], P["dates"][i], ledger.trade_r(entry, stop, A[fill, k], cfg),
                        fill - i))
            i = fill + 1
    return pd.DataFrame(out, columns=["ticker", "entry_date", "r_multiple", "hold_days"])


def _perf(eq, cash_ret):
    r = eq.pct_change().dropna()
    yrs = len(r) / 252
    dd = (eq / eq.cummax() - 1).min()
    ex = r - cash_ret[-len(r):]
    return {"cagr": ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100 if yrs > 0 else np.nan,
            "total": (eq.iloc[-1] / eq.iloc[0] - 1) * 100, "max_dd": dd * 100,
            "vol": r.std() * np.sqrt(252) * 100,
            "sharpe": ex.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan}


def _trade_stats(t):
    if t.empty:
        return {"trades": 0}
    r = t["r_multiple"]
    wins, losses = r[r > 0], r[r <= 0]
    mix = t["exit_reason"].str.extract(r"(trailing|trend|limit|data)")[0].value_counts(normalize=True) * 100
    return {"trades": int(len(t)), "win_rate": round(len(wins) / len(t) * 100, 1),
            "expectancy_r": round(float(r.mean()), 3),
            "avg_win_r": round(float(wins.mean()), 2) if len(wins) else 0.0,
            "avg_loss_r": round(float(losses.mean()), 2) if len(losses) else 0.0,
            "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else None,
            "avg_hold_days": round(float(t["hold_days"].mean()), 1),
            "stop_exit_pct": round(float(mix.get("trailing", 0)), 1),
            "trend_exit_pct": round(float(mix.get("trend", 0)), 1),
            "timeout_pct": round(float(mix.get("limit", 0)), 1)}


def _robustness(trades, eq, cfg):
    """Block bootstrap by calendar month — trades on the same days move together, so resampling
    single trades (as if independent) overstates the certainty. Range for the average trade, plus
    a bad-case drawdown from resampled months of account returns."""
    if len(trades) < 30:
        return {}
    rng = np.random.default_rng(42)
    g = trades.groupby(trades["entry_date"].astype(str).str[:7])["r_multiple"]
    sums, cnts = g.sum().values, g.count().values
    pick = rng.integers(0, len(sums), (cfg.backtest_mc_runs, len(sums)))
    exps = sums[pick].sum(axis=1) / cnts[pick].sum(axis=1)
    mret = eq.resample("ME").last().pct_change().dropna().values
    pick = rng.integers(0, len(mret), (cfg.backtest_mc_runs, len(mret)))
    paths = np.cumprod(1 + mret[pick], axis=1)
    dds = ((paths / np.maximum.accumulate(paths, axis=1)) - 1).min(axis=1) * 100
    return {"mc_exp_p5": round(float(np.percentile(exps, 5)), 3),
            "mc_exp_p50": round(float(np.percentile(exps, 50)), 3),
            "mc_exp_p95": round(float(np.percentile(exps, 95)), 3),
            "mc_prob_positive": round(float(np.mean(exps > 0) * 100), 1),
            "mc_maxdd_p50": round(float(np.percentile(dds, 50)), 1),
            "mc_maxdd_p95": round(float(np.percentile(dds, 5)), 1)}   # 5th pctile = bad 1-in-20


def _oos_split(trades):
    """Time-ordered check: does the edge from the earlier ~70% of history hold in the recent ~30%?"""
    if len(trades) < 40:
        return {}
    t = trades.sort_values("entry_date")
    cut = int(len(t) * 0.7)
    early, recent = t.iloc[:cut], t.iloc[cut:]
    return {"oos_split_date": str(recent["entry_date"].iloc[0])[:10],
            "oos_earlier_exp_r": round(float(early["r_multiple"].mean()), 3),
            "oos_recent_exp_r": round(float(recent["r_multiple"].mean()), 3),
            "oos_recent_trades": int(len(recent))}


def _by_year(eq, spy, cash, trades):
    """Calendar-year return of the account vs the S&P 500 and cash, with that year's trades."""
    out = []
    frames = {"strategy_pct": eq, "spy_pct": spy, "cash_pct": cash}
    ye = {k: v.groupby(v.index.year).last() for k, v in frames.items()}
    first = {k: v.iloc[0] for k, v in frames.items()}
    ty = pd.to_datetime(trades["entry_date"]).dt.year if not trades.empty else pd.Series(dtype=int)
    for y in ye["strategy_pct"].index:
        row = {"year": str(y)}
        for k, s in ye.items():
            base = s.get(y - 1, first[k])
            row[k] = round((s[y] / base - 1) * 100, 1)
        yt = trades[ty == y] if not trades.empty else trades
        row["trades"] = int(len(yt))
        row["expectancy_r"] = round(float(yt["r_multiple"].mean()), 3) if len(yt) else None
        row["win_rate"] = round(float((yt["r_multiple"] > 0).mean() * 100), 1) if len(yt) else None
        out.append(row)
    return pd.DataFrame(out)


def run_backtest(cfg=CONFIG, reuse_cache=True):
    prices, hist = _load(cfg, reuse_cache)
    if prices.empty:
        raise SystemExit("No backtest price data.")
    P = _prep(prices, hist, cfg)
    print(f"      universe: {len(P['tickers'])} S&P 500 members over the window "
          f"({len(P['former_used'])} former members with data, {len(P['former_dropped'])} without)")
    reg_label, reg_mult = _regime_by_day(P)
    cands, short = _daily_candidates(P, cfg, reg_label)

    # The account — the headline result
    eq, trades, acct = _simulate_account(P, cands, reg_label, reg_mult, cfg)
    days = eq.index
    i0 = len(P["dates"]) - len(days)
    spy = pd.Series(P["spy"][i0:], index=days)
    cash = pd.Series(P["cash"][i0:], index=days)
    cash_ret = P["cash_ret"][i0:]
    perf, perf_spy, perf_cash = _perf(eq, cash_ret), _perf(spy, cash_ret), _perf(cash, cash_ret)

    # Edge vs chance: every qualifying signal (no account limits) vs random entries on the same
    # shortlisted, regime-open days with the same exits
    buy_ok = np.zeros_like(short)
    for i, cs in cands.items():
        for k, _, _ in cs:
            buy_ok[i, k] = True
    sig_t = _signal_trades(P, buy_ok, cfg)
    open_days = short & (reg_label != "RISK-OFF")[:, None]
    free = max(1.0, open_days.sum() - len(sig_t) * max(1.0, sig_t["hold_days"].mean()))
    p_rand = min(1.0, len(sig_t) / free)
    rand = [_signal_trades(P, open_days, cfg, np.random.default_rng(s), p_rand)
            for s in range(cfg.backtest_random_runs)]
    rand_exp = np.array([t["r_multiple"].mean() for t in rand if len(t)])

    yrs = len(days) / 252
    gbp = cfg.capital_gbp
    summary = {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "years": cfg.backtest_years,
        "start_date": str(days[0].date()), "end_date": str(days[-1].date()),
        "start_capital_gbp": gbp,
        "end_value_gbp": round(gbp * eq.iloc[-1] / eq.iloc[0], 0),
        "spy_end_value_gbp": round(gbp * spy.iloc[-1] / spy.iloc[0], 0),
        "cash_end_value_gbp": round(gbp * cash.iloc[-1] / cash.iloc[0], 0),
        "cagr_pct": round(perf["cagr"], 1), "total_return_pct": round(perf["total"], 1),
        "max_drawdown_pct": round(perf["max_dd"], 1), "vol_pct": round(perf["vol"], 1),
        "sharpe": round(perf["sharpe"], 2),
        "spy_cagr_pct": round(perf_spy["cagr"], 1), "spy_total_return_pct": round(perf_spy["total"], 1),
        "spy_max_drawdown_pct": round(perf_spy["max_dd"], 1), "spy_sharpe": round(perf_spy["sharpe"], 2),
        "cash_cagr_pct": round(perf_cash["cagr"], 1),
        "cash_total_return_pct": round(perf_cash["total"], 1),
        "avg_invested_pct": round(acct["avg_invested"] * 100, 0),
        "avg_positions": round(acct["avg_positions"], 1),
        "max_positions_held": acct["max_positions_held"],
        "costs_pct_per_year": round(acct["costs"] / eq.mean() / yrs * 100, 1),
        "signal_trades": int(len(sig_t)),
        "signal_expectancy_r": round(float(sig_t["r_multiple"].mean()), 3) if len(sig_t) else None,
        "random_expectancy_r": round(float(rand_exp.mean()), 3) if len(rand_exp) else None,
        "random_exp_p5": round(float(np.percentile(rand_exp, 5)), 3) if len(rand_exp) else None,
        "random_exp_p95": round(float(np.percentile(rand_exp, 95)), 3) if len(rand_exp) else None,
        "random_trades": int(np.mean([len(t) for t in rand])) if rand else 0,
        "random_runs": len(rand),
        "universe_members": len(P["tickers"]),
        "former_members_used": len(P["former_used"]),
        "former_members_missing": len(P["former_dropped"]),
        "note": "One account replaying the live rules on point-in-time S&P 500 membership, vs "
                "the S&P 500 and cash. News/sentiment, earnings and macro-event layers not "
                "replayed; former members without free data still missing. Past ≠ future.",
    }
    summary.update(_trade_stats(trades))
    summary.update(_robustness(trades, eq, cfg))
    summary.update(_oos_split(trades))

    equity = pd.DataFrame({"date": [str(d.date()) for d in days],
                           "strategy_gbp": (gbp * eq / eq.iloc[0]).round(0).values,
                           "spy_gbp": (gbp * spy / spy.iloc[0]).round(0).values,
                           "cash_gbp": (gbp * cash / cash.iloc[0]).round(0).values})
    sens = []
    for sm in (1.5, 2.0, 2.5, 3.0):          # does the result hinge on one exact stop width?
        c2 = dataclasses.replace(cfg, atr_stop_mult=sm)
        e2, t2, _ = (eq, trades, None) if sm == cfg.atr_stop_mult else \
            _simulate_account(P, cands, reg_label, reg_mult, c2)
        p2 = _perf(e2, cash_ret)
        sens.append({"stop_atr_mult": sm, "cagr_pct": round(p2["cagr"], 1),
                     "max_drawdown_pct": round(p2["max_dd"], 1), "trades": int(len(t2)),
                     "expectancy_r": round(float(t2["r_multiple"].mean()), 3) if len(t2) else None})

    db.write_df(trades if not trades.empty else pd.DataFrame(columns=["ticker"]),
                "backtest_trades", cfg.db_path)
    db.write_df(pd.DataFrame([summary]), "backtest_summary", cfg.db_path)
    db.write_df(equity, "backtest_equity", cfg.db_path)
    db.write_df(_by_year(eq, spy, cash, trades), "backtest_by_year", cfg.db_path)
    db.write_df(pd.DataFrame(sens), "backtest_sensitivity", cfg.db_path)
    return summary


if __name__ == "__main__":
    s = run_backtest()
    for k, v in s.items():
        print(f"  {k}: {v}")
