"""Central configuration for the high-beta screener.

All portfolio/risk parameters were set with the user:
  - Capital: £50,000
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


@dataclass
class Config:
    # --- Portfolio / risk (from user) ---
    capital_gbp: float = 50_000.0
    risk_per_trade: float = 0.015           # 1.5%
    max_positions: int = 8
    max_portfolio_heat: float = 0.12        # positions * risk/trade guardrail

    # --- Benchmark ---
    benchmark: str = "SPY"

    # --- Data ---
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
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_min_buy: float = 45.0      # below this, momentum too weak to enter
    fx_pair: str = "GBPUSD=X"      # convert £ capital -> $ for US-stock sizing
    fx_fallback: float = 1.27      # used only if the live FX fetch fails

    # --- Macro layers (Phase 3: A = regime gate, B = event risk) ---
    macro_event_window: int = 10        # days ahead to surface macro events
    macro_event_sizedown_days: int = 2  # size down if a big macro event is this close
    earnings_block_days: int = 3        # no new BUY if earnings within this many days
    earnings_lookahead_days: int = 14   # window to flag upcoming earnings

    # --- News sentiment (Phase 3, Layer C) ---
    use_claude_news: bool = True          # use Claude API; falls back to FinBERT then keywords
    use_finbert: bool = True              # use local FinBERT if installed (no key needed)
    claude_model: str = "claude-haiku-4-5"  # cheap headline classification within budget;
    #                                         swap to "claude-opus-5" for maximum quality
    news_headlines: int = 8               # headlines pulled per ticker
    news_avoid_downgrades_buy: bool = True  # a strongly negative news read turns BUY -> HOLD

    # --- Hourly news-shock alert ---
    shock_move_pct: float = 0.05          # a >=5% intraday move triggers a news look

    # --- Backtester + track-record ledger ---
    backtest_years: str = "10y"           # how much history to replay (incl. bear markets)
    backtest_universe_max: int = 120      # cap to the most liquid high-beta names for speed
    backtest_max_hold_days: int = 40      # backstop time exit (trailing/trend exits do the work)
    backtest_cost_pct: float = 0.001      # 0.1% round-trip slippage + commission per trade
    # Improved exits (1): trailing stop + trend-break instead of a fixed time clock
    backtest_trail_atr_mult: float = 2.5  # trail the stop this many ATRs below the run-up high
    backtest_trend_exit_sma: int = 20     # also exit if price closes below its 20-day average
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
