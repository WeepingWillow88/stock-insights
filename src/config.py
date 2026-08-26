"""Central configuration for the high-beta screener.

All portfolio/risk parameters were set with the user:
  - Capital: £50,000 (default) — override with the CAPITAL_GBP env var / repo variable,
    so the app and the daily refresh both size against the same number without a code edit.
  - Risk per trade: 1.5% (~£750 max loss per trade)
  - Max concurrent positions: 8  (portfolio heat ~12%)
  - Benchmark: SPY
Phase 1 only uses the data/screen params; risk params are surfaced in the UI
and will drive position sizing in Phase 2.
"""
from __future__ import annotations

import os as _os
from dataclasses import dataclass


def _load_dotenv():
    """Load KEY=VALUE lines from a project-root .env into the environment (no dependency).
    Lets you keep ANTHROPIC_API_KEY (and SMTP_* email settings) in one file that both the
    pipeline and the dashboard pick up. Existing environment variables win."""
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    path = _os.path.join(root, ".env")
    if not _os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            _os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _env_float(key, default):
    """Read a float from the environment, falling back to `default` if it is unset, blank, or
    unparseable. Keeps a stray/empty CAPITAL_GBP repo variable from crashing the whole pipeline."""
    try:
        return float(_os.environ[key])
    except (KeyError, ValueError, TypeError):
        return float(default)


def _env_int(key, default):
    """Read an int from the environment, falling back to `default` if unset, blank, or unparseable
    (a stray MAX_POSITIONS repo variable shouldn't crash the pipeline). Floats are truncated."""
    try:
        return int(float(_os.environ[key]))
    except (KeyError, ValueError, TypeError):
        return int(default)


@dataclass
class Config:
    # --- Portfolio / risk (from user) ---
    # Trading capital. Defaults to £50,000; set CAPITAL_GBP (in .env for the app, or as a repo
    # variable wired into the daily Action) to change it in one place — sizing, the heat cap, and
    # the % deployed / cash-free tiles all recompute on the next refresh run.
    capital_gbp: float = _env_float("CAPITAL_GBP", 50_000.0)
    risk_per_trade: float = 0.015           # 1.5%
    # Max concurrent positions. Defaults to 8; set MAX_POSITIONS (in .env / a repo variable) to
    # change the automated run in one place. The dashboard also offers a session-only what-if.
    max_positions: int = _env_int("MAX_POSITIONS", 8)
    max_portfolio_heat: float = 0.12        # positions * risk/trade guardrail

    # --- Benchmark ---
    benchmark: str = "SPY"

    # --- Data ---
    universe_scope: str = "sp500"           # "sp500" or "sp1500" (adds S&P 400 mid + 600 small caps —
    #                                         more high-beta candidates, but a heavier daily run)
    history_period: str = "1y"              # yfinance lookback
    beta_window: int = 252                  # trading days used for beta

    # --- Tradability filters ---
    min_price: float = 5.0
    min_avg_dollar_volume: float = 20_000_000.0   # $20M average daily $ volume

    # --- High-beta shortlist ---
    min_beta: float = 1.3
    shortlist_size: int = 75
    beta_shrink: float = 0.67    # Blume shrinkage: blend raw beta toward the market (1.0)
    max_per_sector: int = 3      # concentration cap: at most N portfolio positions per sector
    stale_days: int = 5          # drop a ticker whose latest bar lags the market by > this

    # --- Signals & position sizing (Phase 2) ---
    atr_stop_mult: float = 2.0     # stop distance = 2 x ATR (wide, for high beta)
    reward_risk: float = 2.0       # target = entry + 2 x stop distance (2:1)
    max_chase_frac: float = 0.25   # 'don't chase': OK to buy up to entry + this x the stop distance
    #                                (≈ 0.5 x ATR). Above it, reward:risk degrades too far — skip/wait.
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_min_buy: float = 45.0      # below this, momentum too weak to enter
    # Entry-quality gates — make live BUYs match the backtested (validated) rules:
    min_conviction: int = 75            # only BUY when >= this % of independent checks agree
    require_rel_strength: bool = True   # only BUY names beating the S&P (3-month relative strength)
    require_volume_confirm: bool = True # only BUY on above-average volume (confirmation)
    # Exit model — shared by the live ledger AND the backtest: trailing stop + trend break
    trail_atr_mult: float = 2.5         # trail the stop this many ATRs below the run-up high
    trend_exit_sma: int = 20            # also exit if price closes below this SMA (trend break)
    # Portfolio construction
    max_position_correlation: float = 0.80  # don't add a pick this correlated with one already held
    corr_window: int = 60                    # trading days of returns used for the correlation cap
    # Edge-weighted sizing: scale size by confidence (a fractional-Kelly proxy), never above full risk
    edge_weighted_sizing: bool = True
    edge_size_floor: float = 0.5             # lowest-confidence BUY still gets this fraction of full size
    fx_pair: str = "GBPUSD=X"      # convert £ capital -> $ for US-stock sizing
    fx_fallback: float = 1.27      # used only if the live FX fetch fails

    # --- Macro layers (Phase 3: A = regime gate, B = event risk) ---
    macro_event_window: int = 10        # days ahead to surface macro events
    macro_event_sizedown_days: int = 2  # size down if a big macro event is this close
    earnings_block_days: int = 3        # no new BUY if earnings within this many days
    earnings_lookahead_days: int = 14   # window to flag upcoming earnings

    # --- News sentiment (Phase 3, Layer C) ---
    # Sentiment stack: StockTwits crowd tags for every name (free, no key, and context-independent
    # — it reads market mood, not headline snippets), then Alpha Vantage article-level news on the
    # day's actionable names to 'double down', with FinBERT -> keywords as the fallback.
    use_stocktwits: bool = True           # primary: StockTwits crowd bull/bear tags (free, no key)
    stocktwits_min_tags: int = 3          # need at least this many tagged posts to trust the crowd read
    use_alpha_vantage: bool = True        # article-level confirmation on actionable names (needs key)
    av_max_calls_per_run: int = 20        # cap AV calls/run — stays under the free 25/day (run 1x/day)
    use_claude_news: bool = False         # OFF: the configured key routes via a Salesforce-internal
    #                                       gateway that 401s from CI/Streamlit, so it silently fell
    #                                       back to keywords anyway. StockTwits/AV replace it.
    use_finbert: bool = True              # fallback: local FinBERT if installed (no key needed)
    claude_model: str = "claude-haiku-4-5"  # cheap headline classification within budget;
    #                                         swap to "claude-opus-5" for maximum quality
    news_headlines: int = 8               # headlines/news items pulled per ticker
    news_lookback_days: int = 4           # how far back to pull news
    news_avoid_downgrades_buy: bool = True  # a strongly negative news read turns BUY -> HOLD
    use_cnbc: bool = True                 # blend in CNBC headlines (site-restricted Google News RSS,
    #                                       no key) so that outlet is always represented per ticker
    cnbc_headlines: int = 3               # CNBC items to surface per ticker before the rest of the feed
    # News source: Finnhub company-news (headline + summary + source, reliable ticker tagging) when
    # FINNHUB_API_KEY is set; otherwise falls back to Google News RSS titles. When use_cnbc is on,
    # CNBC headlines are blended in on top. Claude also gets the stock's recent price move + the
    # market regime as context, so it reads the *reaction*.

    # --- Post-earnings drift (PEAD, Layer E: a momentum-aligned earnings tilt) ---
    # Once a stock HAS reported, a strong beat the market rewarded with a gap tends to keep
    # drifting the same way for weeks. Unlike the pre-earnings blackout (Layer B, which keeps us
    # OUT before the print), this rewards a move that already happened — so it can only nudge an
    # otherwise-valid setup, never override the technicals.
    pead_enabled: bool = True
    pead_drift_days: int = 14          # a past report still counts as 'fresh drift' within this many days
    pead_min_gap: float = 0.03         # the report reaction (2-session close-to-close) must exceed ±this
    pead_strong_gap: float = 0.07      # a reaction beyond this is 'strong' (a strong miss can veto a BUY)
    pead_conviction_bonus: int = 10    # a positive drift adds this to the confidence score
    pead_conviction_penalty: int = 15  # a negative drift subtracts this
    pead_avoid_downgrades_buy: bool = True  # a strong negative post-earnings gap turns a BUY -> HOLD

    # --- Options-implied vol + short-interest overlays (B2 / B3, best-effort via yfinance) ---
    fetch_market_extras: bool = True      # pull IV + short interest for the shortlist each run
    high_short_pct_float: float = 0.15    # flag when short interest >= this % of float (squeeze fuel)
    iv_rich_ratio: float = 1.5            # flag when ATM implied vol >= this x recent realized vol

    # --- Hourly news-shock alert ---
    shock_move_pct: float = 0.05          # a >=5% intraday move triggers a news look

    # --- Backtester + track-record ledger ---
    backtest_years: str = "10y"           # how much history to replay (incl. bear markets)
    backtest_universe_max: int = 120      # cap to the most liquid high-beta names for speed
    backtest_max_hold_days: int = 40      # backstop time exit (trailing/trend exits do the work)
    backtest_cost_pct: float = 0.001      # base round-trip slippage + commission per trade
    backtest_spread_atr_coef: float = 0.05  # realistic extra cost = this x ATR% (jumpy names cost
    #                                         more to trade — wider spreads/slippage). Applied in
    #                                         the backtest AND the live ledger for an honest R.
    # Improved exits (1): trailing stop + trend-break instead of a fixed time clock
    # (trailing-stop + trend-break exit params are shared with live signals — see trail_atr_mult
    #  / trend_exit_sma above)
    # Higher-quality entries (2) + regime gate (3)
    backtest_min_conviction: int = 75     # only take setups where >= this % of checks agree
    backtest_use_regime: bool = True      # only buy when the S&P 500 is in an uptrend
    backtest_use_rs: bool = True          # only buy if the stock is beating the S&P (rel. strength)
    backtest_use_volume: bool = True      # only buy on above-average volume (confirmation)
    backtest_mc_runs: int = 1000          # Monte-Carlo resamples for the robustness range (6)
    ledger_max_hold_days: int = 15        # close a logged trade after this many trading days
    daily_loss_limit_pct: float = 0.04    # circuit-breaker: warn if today's realized losses exceed this

    # --- Ops ---
    db_path: str = "data/stock_insights.db"
    batch_size: int = 50
    max_tickers: int | None = None          # cap universe for quick runs (None = all)


CONFIG = Config()
