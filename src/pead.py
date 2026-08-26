"""Layer E: post-earnings-announcement drift (PEAD) — a momentum-aligned earnings tilt.

The engine's earnings *blackout* (Layer B) keeps us OUT of a stock right before it reports,
because the print is a coin-flip. This layer handles the OTHER side of the event: once a stock
HAS reported, a strong beat that the market rewarded with a gap-up tends to keep drifting up for
weeks (and a big miss keeps bleeding). That drift is one of the most durable, well-documented
anomalies in finance — and, unlike a pre-earnings consensus preview, it's *momentum-compatible*:
it rewards a move that has already happened rather than betting on an unknown result.

We read the drift from PRICE, not just the reported EPS surprise. The 2-session close-to-close
reaction around the report embeds the whole market verdict — beat/miss *and* guidance *and* how
much was already priced in — which is a better short-horizon signal than the raw EPS number.
The reported EPS surprise (from yfinance, best-effort) is kept for context/labelling only.

Everything here is best-effort: yfinance's earnings-dates endpoint is flaky, so any failure just
means that ticker has no PEAD signal (never raises), exactly like the earnings/news/extras layers.

NOTE: this live tilt is NOT in the backtest (which uses technicals only, like the news layer) —
point-in-time historical earnings surprises aren't reliably available on the free feed. So treat
PEAD as a live nudge, not part of the validated backtested edge.
"""
import datetime as dt

import numpy as np
import pandas as pd


def _reaction_return(close_hist, ed):
    """2-session close-to-close reaction around earnings date `ed`.

    `close_hist` = a ticker's price rows (needs `date`, `close`), any order. We take the last
    close STRICTLY BEFORE the report as the pre-earnings anchor, then the close two sessions later.
    Two sessions captures both a before-open report (reaction lands same day) and an after-close
    report (reaction lands the next day), while the extra day is itself early drift. None if the
    price history doesn't straddle the date. """
    df = close_hist.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    if df.empty:
        return None
    pre = df[df["date"] < ed]
    if pre.empty:
        return None
    i0 = pre.index[-1]                      # last bar before the report
    i1 = min(i0 + 2, len(df) - 1)           # two sessions later (clamped to what we have)
    if i1 <= i0:
        return None
    c0 = float(df.loc[i0, "close"])
    c1 = float(df.loc[i1, "close"])
    if c0 <= 0:
        return None
    return c1 / c0 - 1.0


def _most_recent_report(edf, today, drift_days):
    """From a yfinance earnings_dates frame, return (date, surprise_pct) for the most recent
    report that actually happened within `drift_days` calendar days of `today`, else None.
    `surprise_pct` is a fraction (0.05 = +5% beat) or None if the feed didn't give one."""
    if edf is None or not isinstance(edf, pd.DataFrame) or edf.empty:
        return None
    df = edf.copy()
    # Index is a (often tz-aware) Timestamp; normalise to plain dates.
    try:
        idx = pd.to_datetime(df.index, utc=True).tz_convert(None)
    except (TypeError, ValueError):
        try:
            idx = pd.to_datetime(df.index)
        except (TypeError, ValueError):
            return None
    df = df.assign(_d=[d.date() if hasattr(d, "date") else d for d in idx])

    rep_col = next((c for c in df.columns if str(c).lower().startswith("reported")), None)
    est_col = next((c for c in df.columns if str(c).lower().startswith("eps estimate")), None)
    sur_col = next((c for c in df.columns if str(c).lower().startswith("surprise")), None)

    best = None
    for _, r in df.iterrows():
        d = r["_d"]
        if not isinstance(d, dt.date):
            continue
        days = (today - d).days
        if not (0 <= days <= drift_days):
            continue                        # future or too stale to still be drifting
        # Must be a report that has actually happened (a reported figure present).
        reported = r.get(rep_col) if rep_col else None
        if reported is None or (isinstance(reported, float) and np.isnan(reported)):
            continue
        surprise = None
        if sur_col is not None and pd.notna(r.get(sur_col)):
            surprise = float(r[sur_col]) / 100.0      # yfinance gives Surprise(%) as a percent
        elif est_col is not None and pd.notna(r.get(est_col)):
            est = float(r[est_col])
            if est != 0:
                surprise = float(reported) / abs(est) - 1.0
        if best is None or d > best[0]:
            best = (d, surprise)
    return best


def evaluate_pead(edf, close_hist, today, cfg):
    """Pure PEAD evaluation (no I/O) — testable. Returns a record dict when a fresh, material
    post-earnings drift is detected, else None.

    record = {date, days_since, surprise_pct, gap_ret, label('positive'|'negative'), strong, flag}
    """
    rep = _most_recent_report(edf, today, cfg.pead_drift_days)
    if rep is None:
        return None
    ed, surprise = rep
    gap = _reaction_return(close_hist, ed)
    if gap is None or abs(gap) < cfg.pead_min_gap:
        return None                          # no report reaction big enough to be a surprise event
    label = "positive" if gap > 0 else "negative"
    strong = abs(gap) >= cfg.pead_strong_gap
    days_since = (today - ed).days

    arrow = "↑" if label == "positive" else "↓"
    # Describe the report by its EPS surprise, but stay honest when the price reaction disagrees
    # (a stock can beat EPS yet sell off on guidance, or miss yet rally). The GAP drives the label.
    lead = ""
    if surprise is not None and surprise != 0:
        beat = surprise > 0
        agrees = (beat and label == "positive") or (not beat and label == "negative")
        if agrees:
            lead = "beat, " if beat else "miss, "
        else:
            lead = "beat but sold off, " if beat else "miss but rallied, "
    flag = f"post-earnings drift {arrow} ({lead}{gap:+.0%} gap {days_since}d ago)"
    return {
        "date": ed.isoformat(),
        "days_since": days_since,
        "surprise_pct": round(surprise, 4) if surprise is not None else None,
        "gap_ret": round(gap, 4),
        "label": label,
        "strong": strong,
        "flag": flag,
    }


def pead_for(ticker, prices, cfg, today=None, yft=None):
    """Best-effort PEAD record for one ticker (None if no fresh drift / data unavailable)."""
    import yfinance as yf

    close_hist = prices[prices["ticker"] == ticker][["date", "close"]].copy()
    if close_hist.empty:
        return None
    close_hist["date"] = pd.to_datetime(close_hist["date"]).dt.date
    if today is None:
        today = close_hist["date"].max()     # anchor to the latest bar we actually have
    try:
        yft = yft or yf.Ticker(ticker)
        edf = yft.get_earnings_dates(limit=12)
    except Exception:  # noqa: BLE001
        return None
    try:
        return evaluate_pead(edf, close_hist, today, cfg)
    except Exception:  # noqa: BLE001
        return None


def build_pead_map(tickers, prices, cfg):
    """{ticker: pead_record} for shortlist names with a fresh, material post-earnings drift.
    Names with no recent report / no material reaction are omitted (like the earnings map)."""
    if prices.empty:
        return {}
    today = pd.to_datetime(prices["date"]).dt.date.max()
    out = {}
    for t in tickers:
        rec = pead_for(t, prices, cfg, today=today)
        if rec is not None:
            out[t] = rec
    return out
