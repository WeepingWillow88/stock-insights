"""Phase 1 orchestrator: universe -> prices -> metrics -> ranked shortlist -> DB.

Run a full refresh with:
    python -m src.pipeline
Quick smoke test on a small universe:
    MAX_TICKERS=25 python -m src.pipeline
"""
import datetime as dt
import os

import pandas as pd

from .config import CONFIG
from . import (data, db, events, ledger, marketdata, metrics, news, notify, pead, regime,
               screen, signals, universe)


def run_pipeline(cfg=CONFIG, send_digest=True, reuse_news=False):
    """Full refresh: re-download prices for the whole universe, recompute metrics,
    screen, signals, macro and news, and write everything to the DB. This is what
    advances `market_through` to the latest trading day. Set send_digest=False to
    skip the email/notify step (used by the in-app 'Pull fresh prices' button).
    reuse_news=True reuses the last run's (Claude-scored) sentiment instead of a fresh
    mass evaluation — used by the dashboard buttons so a button press costs no Claude tokens;
    the scheduled 2x/day runs leave it False and do the real scoring."""
    # Carry over accumulated state (esp. the track-record ledger) from the shipped snapshot.
    # On the daily GitHub Action only seed.db is checked out, so without this the ledger would
    # start empty every run and never show a closed trade.
    if db.bootstrap_working_db(cfg.db_path):
        print("      seeded working DB from snapshot (carrying the ledger forward).")
    ledger.reconcile(cfg)  # union with the shipped ledger so closed trades are never lost
    print("[1/6] Building universe...")
    tickers = universe.get_universe(cfg.universe_scope)
    if cfg.max_tickers:
        tickers = tickers[:cfg.max_tickers]
    if cfg.benchmark not in tickers:
        tickers = [cfg.benchmark] + tickers
    print(f"      {len(tickers)} tickers (incl. benchmark {cfg.benchmark}).")

    print("[2/6] Downloading price history...")
    prices = data.download_prices(tickers, period=cfg.history_period, batch_size=cfg.batch_size)
    n_tickers = prices["ticker"].nunique() if not prices.empty else 0
    print(f"      {n_tickers} tickers with data, {len(prices)} rows.")
    if prices.empty:
        raise SystemExit("No price data downloaded (network blocked?). Aborting.")
    db.write_df(prices, "prices", cfg.db_path, if_exists="replace")

    print("[3/6] Computing metrics...")
    m = metrics.compute_metrics(prices, benchmark=cfg.benchmark, beta_window=cfg.beta_window,
                                beta_shrink=cfg.beta_shrink, stale_days=cfg.stale_days)
    print(f"      metrics for {len(m)} tickers.")

    print("[4/6] Screening high-beta shortlist...")
    shortlist = screen.build_screen(m, cfg)
    sectors = universe.get_sectors()
    shortlist["sector"] = shortlist["ticker"].map(lambda x: sectors.get(x, "Unknown"))
    run_date = dt.date.today().isoformat()
    shortlist["run_date"] = run_date
    m["run_date"] = run_date

    print("[5/6] Macro layers: regime gate + event risk...")
    fx_rate = data.get_fx_rate(cfg.fx_pair, cfg.fx_fallback)
    macro_prices = data.download_prices(regime.MACRO_TICKERS, period=cfg.history_period,
                                        batch_size=cfg.batch_size)
    reg = regime.compute_regime(macro_prices, prices)
    macro_events = events.upcoming_macro_events(within_days=cfg.macro_event_window)
    print(f"      regime={reg['label']} (score {reg['score']}); "
          f"{len(macro_events)} macro event(s) within {cfg.macro_event_window}d.")
    print("      fetching earnings dates for shortlist (best effort)...")
    earnings = events.earnings_dates(shortlist["ticker"].tolist(),
                                     within_days=cfg.earnings_lookahead_days)
    print(f"      {len(earnings)} names with earnings within {cfg.earnings_lookahead_days}d.")

    print("      Fetching news + sentiment for shortlist...")
    news_map, n_smart = _news_map_for(cfg, prices, shortlist, reg["label"], reuse_news)
    src = ("reused (no new Claude calls)" if reuse_news and n_smart == 0
           else "claude" if any(v.get("source") == "claude" for v in news_map.values())
           else "keywords")
    print(f"      news for {len(news_map)} names — {n_smart} engine-scored (source: {src}).")

    extras_map = (marketdata.build_extras_map(shortlist["ticker"].tolist(), prices, cfg)
                  if cfg.fetch_market_extras else {})
    print(f"      market extras (IV + short interest) for {len(extras_map)} names.")

    pead_map = (pead.build_pead_map(shortlist["ticker"].tolist(), prices, cfg)
                if cfg.pead_enabled else {})
    print(f"      post-earnings drift (PEAD) for {len(pead_map)} names.")

    print("      Generating signals + position sizing...")
    sig = signals.build_signals(prices, shortlist, cfg, fx_rate, reg, macro_events,
                                earnings, news_map, extras_map, pead_map)
    sig["run_date"] = run_date
    sig["fx_rate"] = fx_rate
    n_buy = int((sig["signal"] == "BUY").sum()) if not sig.empty else 0
    print(f"      GBP/USD={fx_rate:.4f}; {n_buy} BUY signals, "
          f"{int(sig['selected'].sum()) if not sig.empty else 0} selected.")

    reg_row, events_df, news_df = _macro_news_frames(reg, macro_events, news_map, run_date)

    print("[6/6] Saving results...")
    db.write_df(m, "metrics", cfg.db_path, if_exists="replace")
    db.write_df(shortlist, "screen_results", cfg.db_path, if_exists="replace")
    _persist_signals_bundle(sig, reg_row, events_df, news_df, cfg)

    opened = ledger.record_recommendations(sig, cfg, run_date)
    changed = ledger.update_open_positions(prices, cfg)
    print(f"      ledger: +{opened} new positions logged, {changed} exit(s) flagged/closed.")

    run_kind = os.environ.get("RUN_KIND", "BOD")
    if send_digest:
        body = notify.build_digest(run_kind, sig, reg_row, macro_events, fx_rate, cfg)
        notify.send_or_save(f"[{run_kind}] High-beta signals {run_date}", body, cfg)

    _write_meta(cfg, f"full pipeline ({run_kind})", prices)
    print(f"      shortlist: {len(shortlist)} names, signals: {len(sig)} (run {run_date}).")
    return shortlist


def _write_meta(cfg, run_kind, prices):
    """Record when data was last pulled + the latest market bar it covers."""
    db.write_df(pd.DataFrame([{
        "last_updated": dt.datetime.now().isoformat(timespec="seconds"),
        "run_kind": run_kind,
        "market_through": prices["date"].max() if not prices.empty else None,
    }]), "meta", cfg.db_path, if_exists="replace")


def _macro_news_frames(reg, macro_events, news_map, run_date):
    """Flatten regime + events + news into DataFrames for storage.
    Shared by the full pipeline and the lighter macro/news refresh."""
    reg_row = {"label": reg["label"], "score": reg["score"],
               "size_multiplier": reg["size_multiplier"],
               "vix": reg["readings"]["vix"], "spy_vs_50d": reg["readings"]["spy_vs_50d"],
               "us10y": reg["readings"]["us10y"],
               "notes": " • ".join(reg["notes"]), "run_date": run_date}
    events_df = pd.DataFrame(macro_events) if macro_events else pd.DataFrame(
        columns=["date", "label", "days_until"])
    events_df["run_date"] = run_date
    news_df = pd.DataFrame([
        {"ticker": t, "label": v.get("label"), "sentiment": v.get("sentiment"),
         "materiality": v.get("materiality"), "macro_driver": v.get("macro_driver"),
         "action_bias": v.get("action_bias"), "source": v.get("source"),
         "av_score": v.get("av_score"), "av_label": v.get("av_label"),
         "av_articles": v.get("av_articles"),
         "rationale": v.get("rationale"), "headlines": " || ".join(v.get("headlines", [])),
         "run_date": run_date}
        for t, v in news_map.items()
    ])
    return reg_row, events_df, news_df


def _load_news_map(cfg):
    """Reconstruct the last run's news_map from the stored `news` table. Lets the dashboard refresh
    buttons reuse the most recent (Claude-scored) sentiment instead of re-paying for a fresh mass
    evaluation — news moves on the 2x/day cadence, prices/signals refresh on demand."""
    if not db.table_exists("news", cfg.db_path):
        return {}
    df = db.read_df("SELECT * FROM news", cfg.db_path)
    out = {}
    for _, r in df.iterrows():
        raw = str(r.get("headlines") or "")
        out[r["ticker"]] = {
            "label": r.get("label"), "sentiment": r.get("sentiment"),
            "materiality": r.get("materiality"), "macro_driver": r.get("macro_driver"),
            "action_bias": r.get("action_bias"), "source": r.get("source"),
            "av_score": r.get("av_score"), "av_label": r.get("av_label"),
            "av_articles": r.get("av_articles"),
            "rationale": r.get("rationale"),
            "headlines": [h for h in raw.split(" || ") if h],
        }
    return out


def _news_map_for(cfg, prices, shortlist, reg_label, reuse_news):
    """News map for a run. reuse_news -> reload the last scores from the DB (no new Claude calls).
    Otherwise score fresh, but focus the paid engine on the only names a news read can move:
    technical BUY candidates + current holdings. Returns (news_map, engine_scored_count)."""
    if reuse_news:
        cached = _load_news_map(cfg)
        if cached:
            return cached, 0
        # No cached news yet (first ever run) -> fall through and score fresh.
    smart = signals.technical_buy_candidates(prices, shortlist, cfg) | ledger.open_tickers(cfg)
    # Alpha Vantage (article-level) only 'doubles down' on the day's actionable names, and its free
    # tier is 25 calls/day — so run it once daily (skip the pre-open morning run; the pre-close /
    # manual run does it). StockTwits still scores every name on every run regardless.
    run_kind = os.environ.get("RUN_KIND", "").lower()
    use_av = cfg.use_alpha_vantage and "pre-open" not in run_kind
    nm = news.build_news_map(shortlist["ticker"].tolist(), cfg, prices, reg_label,
                             smart_tickers=smart, use_av=use_av)
    return nm, len(smart)


def _persist_signals_bundle(sig, reg_row, events_df, news_df, cfg):
    """Write the signals + regime + events + news tables (the outputs both refresh paths share)."""
    db.write_df(sig, "signals", cfg.db_path, if_exists="replace")
    db.write_df(pd.DataFrame([reg_row]), "regime", cfg.db_path, if_exists="replace")
    db.write_df(events_df, "macro_events", cfg.db_path, if_exists="replace")
    db.write_df(news_df, "news", cfg.db_path, if_exists="replace")


def refresh_macro_news(cfg=CONFIG, reuse_news=False):
    """Lighter refresh used by the dashboard button and a daily cron: reuse cached
    prices, re-pull macro regime + events + earnings + news, and rebuild signals.
    Avoids re-downloading the full universe. reuse_news=True skips the paid Claude news
    scoring and reuses the last run's sentiment (dashboard button); the cron leaves it False."""
    prices = db.read_df("SELECT * FROM prices", cfg.db_path)
    shortlist = db.read_df("SELECT * FROM screen_results", cfg.db_path)
    if prices.empty or shortlist.empty:
        raise SystemExit("No cached data — run the full pipeline first (python -m src.pipeline).")
    if "sector" not in shortlist.columns:
        sectors = universe.get_sectors()
        shortlist["sector"] = shortlist["ticker"].map(lambda x: sectors.get(x, "Unknown"))
    run_date = dt.date.today().isoformat()

    fx_rate = data.get_fx_rate(cfg.fx_pair, cfg.fx_fallback)
    macro_prices = data.download_prices(regime.MACRO_TICKERS, period=cfg.history_period,
                                        batch_size=cfg.batch_size)
    reg = regime.compute_regime(macro_prices, prices)
    macro_events = events.upcoming_macro_events(within_days=cfg.macro_event_window)
    earnings = events.earnings_dates(shortlist["ticker"].tolist(),
                                     within_days=cfg.earnings_lookahead_days)
    news_map, _ = _news_map_for(cfg, prices, shortlist, reg["label"], reuse_news)
    extras_map = (marketdata.build_extras_map(shortlist["ticker"].tolist(), prices, cfg)
                  if cfg.fetch_market_extras else {})
    pead_map = (pead.build_pead_map(shortlist["ticker"].tolist(), prices, cfg)
                if cfg.pead_enabled else {})
    sig = signals.build_signals(prices, shortlist, cfg, fx_rate, reg, macro_events,
                                earnings, news_map, extras_map, pead_map)
    sig["run_date"] = run_date
    sig["fx_rate"] = fx_rate

    reg_row, events_df, news_df = _macro_news_frames(reg, macro_events, news_map, run_date)
    _persist_signals_bundle(sig, reg_row, events_df, news_df, cfg)
    ledger.record_recommendations(sig, cfg, run_date)
    ledger.update_open_positions(prices, cfg)
    _write_meta(cfg, "refresh (macro + news)", prices)
    _srcmode = news_df["source"].mode() if not news_df.empty else pd.Series([], dtype=object)
    return {"regime": reg["label"],
            "news_source": _srcmode.iloc[0] if not _srcmode.empty else "none",
            "run_date": run_date}


if __name__ == "__main__":
    if os.environ.get("MAX_TICKERS"):
        CONFIG.max_tickers = int(os.environ["MAX_TICKERS"])
    sl = run_pipeline()
    cols = ["rank", "ticker", "price", "beta", "mom_3m", "atr_pct", "uptrend"]
    have = [c for c in cols if c in sl.columns]
    print("\nTop of shortlist:")
    print(sl[have].head(20).to_string(index=False))
