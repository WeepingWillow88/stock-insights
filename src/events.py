"""Layer B: event-risk awareness.

Two kinds of scheduled 'landmines' that whip high-beta stocks around:
  1. Macro events (CPI, FOMC rate decisions, jobs reports) — seeded calendar below.
  2. Per-stock earnings dates — fetched best-effort from yfinance.

NOTE: the macro calendar is SEEDED (approximate) and should be verified against an
official source (BLS / Federal Reserve). Free real-time econ-calendar APIs are flaky,
so this is the pragmatic Phase-3 approach; easy to swap for an API later.
"""
import datetime as dt
import os

# Keywords for the macro events that actually whip high-beta stocks around.
_MAJOR = ("cpi", "consumer price", "fomc", "federal funds", "interest rate",
          "nonfarm", "payroll", "unemployment", "jobs")

# (ISO date, label) — US macro events, H2 2026 onward. Curated FALLBACK used when no live
# calendar API key is configured. VERIFY before relying on exact dates.
MACRO_EVENTS = [
    ("2026-09-04", "Jobs report (Aug NFP)"),
    ("2026-09-11", "CPI inflation (Aug)"),
    ("2026-09-16", "FOMC rate decision"),
    ("2026-10-02", "Jobs report (Sep NFP)"),
    ("2026-10-13", "CPI inflation (Sep)"),
    ("2026-10-28", "FOMC rate decision"),
    ("2026-11-06", "Jobs report (Oct NFP)"),
    ("2026-11-12", "CPI inflation (Oct)"),
    ("2026-12-04", "Jobs report (Nov NFP)"),
    ("2026-12-10", "CPI inflation (Nov)"),
    ("2026-12-16", "FOMC rate decision"),
]


def _api_macro_events(today, within_days):
    """Real US macro calendar from Financial Modeling Prep when FMP_API_KEY is set. Best-effort:
    returns None on any failure so callers fall back to the curated MACRO_EVENTS list."""
    key = os.environ.get("FMP_API_KEY")
    if not key:
        return None
    try:
        import requests
        end = today + dt.timedelta(days=within_days)
        url = ("https://financialmodelingprep.com/api/v3/economic_calendar"
               f"?from={today.isoformat()}&to={end.isoformat()}&apikey={key}")
        rows = requests.get(url, timeout=15).json()
        out, seen = [], set()
        for r in rows:
            if str(r.get("country")) not in ("US", "United States"):
                continue
            label = str(r.get("event", "")).strip()
            if not any(k in label.lower() for k in _MAJOR):
                continue
            d = str(r.get("date", ""))[:10]
            try:
                days = (dt.date.fromisoformat(d) - today).days
            except ValueError:
                continue
            if 0 <= days <= within_days and (d, label) not in seen:
                seen.add((d, label))
                out.append({"date": d, "label": label, "days_until": days})
        return sorted(out, key=lambda x: x["days_until"]) if out else None
    except Exception:  # noqa: BLE001
        return None


def upcoming_macro_events(today=None, within_days=10):
    today = today or dt.date.today()
    live = _api_macro_events(today, within_days)
    if live is not None:
        return live
    out = []
    for d, label in MACRO_EVENTS:
        ed = dt.date.fromisoformat(d)
        days = (ed - today).days
        if 0 <= days <= within_days:
            out.append({"date": d, "label": label, "days_until": days})
    return sorted(out, key=lambda x: x["days_until"])


def earnings_dates(tickers, within_days=14, today=None):
    """Best-effort next earnings date per ticker. Returns {ticker: {date, days_until}}.
    Silently skips tickers that error out (yfinance calendar is not 100% reliable)."""
    import yfinance as yf

    today = today or dt.date.today()
    out = {}
    for t in tickers:
        try:
            cal = yf.Ticker(t).calendar
            ed = None
            if isinstance(cal, dict):
                v = cal.get("Earnings Date")
                ed = (v[0] if isinstance(v, list) and v else v)
            if ed is None:
                continue
            edd = ed if isinstance(ed, dt.date) else dt.date.fromisoformat(str(ed)[:10])
            days = (edd - today).days
            if 0 <= days <= within_days:
                out[t] = {"date": edd.isoformat(), "days_until": days}
        except Exception:
            continue
    return out
