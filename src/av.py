"""Alpha Vantage NEWS_SENTIMENT — article-level, per-ticker sentiment (the 'double down' layer).

Unlike headline scoring, Alpha Vantage computes sentiment over the *full article* server-side and
returns a per-ticker score + label + a relevance weight, so a stock merely *mentioned* in a story
doesn't skew its read. The free tier is ~25 calls/day, so this is used ONLY on the day's actionable
names (BUY candidates + current holdings) and once per day — see `build_confirmation_map`'s budget.

Activate by setting ALPHAVANTAGE_API_KEY (a free key). Without it every call returns None and the
StockTwits/FinBERT sentiment carries the app on its own. Multiple free keys can be supplied as a
comma-separated list — they're rotated when one hits its 25/day cap, so N keys ~= N*25 calls/day.
"""
import os
import time

import requests

_URL = "https://www.alphavantage.co/query"
_MIN_INTERVAL = 1.2  # AV free tier throttles bursts to ~1 req/sec — space calls out

_KEY_IDX = 0  # module-level cursor into the key list; advances when a key hits its daily cap


def _keys():
    """All configured AV keys (comma-separated in ALPHAVANTAGE_API_KEY), in order."""
    raw = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def _key():
    """The current key (None if none configured)."""
    keys = _keys()
    if not keys:
        return None
    return keys[_KEY_IDX % len(keys)]


def _label(score):
    """Alpha Vantage's own sentiment bands (Bearish … Bullish) applied to a [-1, 1] score."""
    if score <= -0.35:
        return "Bearish"
    if score <= -0.15:
        return "Somewhat-Bearish"
    if score < 0.15:
        return "Neutral"
    if score < 0.35:
        return "Somewhat-Bullish"
    return "Bullish"


def _parse_feed(feed, ticker):
    """Relevance-weighted average of this ticker's sentiment across the returned articles."""
    num = den = 0.0
    n = 0
    for art in feed:
        for ts in art.get("ticker_sentiment", []):
            if ts.get("ticker") != ticker:
                continue
            try:
                rel = float(ts.get("relevance_score", 0) or 0)
                sc = float(ts.get("ticker_sentiment_score", 0) or 0)
            except (TypeError, ValueError):
                continue
            num += rel * sc
            den += rel
            n += 1
    if den == 0:
        return None
    score = round(num / den, 3)
    return {"av_score": score, "av_label": _label(score), "av_articles": n}


def ticker_sentiment(ticker, limit=50):
    """Relevance-weighted per-ticker sentiment across recent articles. On a rate-limit response
    (no `feed`, just an "Information"/"Note"), rotate to the next configured key and retry — so a
    key that's hit its 25/day cap hands off to a fresh one. Returns the dict or None."""
    global _KEY_IDX
    keys = _keys()
    if not keys:
        return None
    for _ in range(len(keys)):  # try each key at most once
        key = _key()
        try:
            params = {"function": "NEWS_SENTIMENT", "tickers": ticker,
                      "sort": "LATEST", "limit": limit, "apikey": key}
            d = requests.get(_URL, params=params, timeout=20).json()
        except Exception:  # noqa: BLE001 - network / JSON -> caller falls back
            return None
        feed = d.get("feed")
        if isinstance(feed, list) and feed:
            return _parse_feed(feed, ticker)
        # No feed: a throttle/daily-cap note ("Information"/"Note") or genuinely no coverage.
        if d.get("Information") or d.get("Note"):
            _KEY_IDX += 1  # this key is capped/throttled — advance and try the next one
            if len(keys) > 1:
                time.sleep(_MIN_INTERVAL)
            continue
        return None  # valid response, just no coverage for this ticker
    return None


def build_confirmation_map(tickers, cfg):
    """AV sentiment for up to cfg.av_max_calls_per_run names (stays under the free 25/day limit
    when run once daily). Returns {ticker: {av_score, av_label, av_articles}}; logs any it skipped
    so a truncated run never reads as full coverage. No key / empty list -> {}."""
    if not _key() or not tickers:
        return {}
    tickers = list(tickers)
    budget = cfg.av_max_calls_per_run
    out = {}
    for i, t in enumerate(tickers[:budget]):
        if i:
            time.sleep(_MIN_INTERVAL)  # respect the free-tier ~1 req/sec burst limit
        r = ticker_sentiment(t)
        if r:
            out[t] = r
    if len(tickers) > budget:
        print(f"      [AV] daily budget {budget} reached — skipped {len(tickers) - budget} "
              f"name(s): {tickers[budget:]}")
    return out
