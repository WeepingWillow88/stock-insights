"""Build the tradable universe.

Phase 1 uses the S&P 500 as a reliable, no-API-key universe (fetched from
Wikipedia). It's liquid and contains plenty of high-beta names to prove the
engine. We can widen this to the Russell 1000-3000 in a later phase by swapping
in a constituents source.
"""
import io
import time

import pandas as pd
import requests

from . import db

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Fallback set of liquid, generally higher-beta US names, used only if the
# Wikipedia fetch fails (e.g. no network).
FALLBACK = [
    "AAPL", "MSFT", "NVDA", "AMD", "MU", "TSLA", "META", "AMZN", "GOOGL", "NFLX",
    "AVGO", "QCOM", "INTC", "CRM", "ORCL", "ADBE", "SHOP", "COIN", "PLTR", "SNOW",
    "UBER", "ABNB", "MRNA", "BA", "CAT", "GS", "JPM", "XOM", "CVX", "F",
]


def get_sp500_tickers():
    """Fetch S&P 500 constituents from Wikipedia. Returns a list of tickers."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    df = tables[0]
    # Yahoo uses '-' where S&P uses '.' (e.g. BRK.B -> BRK-B)
    tickers = df["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
    return tickers


def get_sectors():
    """Map ticker -> GICS sector from Wikipedia. Empty dict on failure (callers default
    to 'Unknown'). Used for the portfolio concentration cap."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        df = pd.read_html(io.StringIO(resp.text))[0]
        syms = df["Symbol"].astype(str).str.replace(".", "-", regex=False)
        return dict(zip(syms, df["GICS Sector"].astype(str)))
    except Exception as e:  # noqa: BLE001
        print(f"[universe] sector fetch failed ({e}); sectors will be 'Unknown'.")
        return {}


def get_names():
    """Map ticker -> company (security) name from Wikipedia. Empty dict on failure (the news
    relevance filter then falls back to matching the ticker symbol only). Used to keep off-topic,
    cross-tagged headlines (an ETF/sector piece that merely name-drops the stock) out of the
    per-ticker sentiment read."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        df = pd.read_html(io.StringIO(resp.text))[0]
        syms = df["Symbol"].astype(str).str.replace(".", "-", regex=False)
        return dict(zip(syms, df["Security"].astype(str)))
    except Exception as e:  # noqa: BLE001
        print(f"[universe] name fetch failed ({e}); news relevance will match tickers only.")
        return {}


_SP_PAGES = {
    400: "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",  # mid caps
    600: "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",  # small caps
}


def _sp_tickers(url):
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    for tbl in pd.read_html(io.StringIO(resp.text)):
        if "Symbol" in tbl.columns:
            return tbl["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
    return []


# Wikipedia asks automated clients to identify themselves with a way to reach the maintainer.
_WIKI_HEADERS = {"User-Agent": "stock-insights/1.0 (https://github.com/WeepingWillow88/stock-insights)"}
_WIKI_PAGE = "List of S&P 500 companies"


def _wiki_get(url, params, as_json=False, tries=6):
    for k in range(tries):
        try:
            r = requests.get(url, params=params, headers=_WIKI_HEADERS, timeout=30)
            if r.status_code == 200:
                return r.json() if as_json else r.text
        except (requests.RequestException, ValueError):
            pass
        time.sleep(3 * (k + 1))  # rate-limited or flaky — back off and retry
    raise RuntimeError(f"Wikipedia request failed after {tries} tries: {params}")


def _constituents_table(html):
    """The constituents table in one revision of the page, or None if that revision is broken
    (mid-edit, vandalised) — it must have a symbol column and roughly 500 rows."""
    for t in pd.read_html(io.StringIO(html)):
        sym = next((c for c in t.columns if "ymbol" in str(c)), None)  # 'Symbol' / 'Ticker symbol'
        if sym is not None and len(t) >= 400:
            return t, sym
    return None


def _snapshot(asof):
    """S&P 500 constituents as Wikipedia listed them on `asof` (the page's last good revision on or
    before that date) -> DataFrame [symbol, cik, sector]."""
    js = _wiki_get("https://en.wikipedia.org/w/api.php", {
        "action": "query", "prop": "revisions", "titles": _WIKI_PAGE, "rvlimit": 5,
        "rvstart": f"{asof}T00:00:00Z", "rvdir": "older", "rvprop": "ids", "format": "json"},
        as_json=True)
    for rev in list(js["query"]["pages"].values())[0]["revisions"]:   # newest first
        found = _constituents_table(_wiki_get("https://en.wikipedia.org/w/index.php",
                                              {"oldid": rev["revid"]}))
        if found:
            break
    else:
        raise RuntimeError(f"No readable S&P 500 table in the 5 revisions before {asof}")
    t, sym = found
    cik = next((c for c in t.columns if "CIK" in str(c)), None)
    sec = next((c for c in t.columns if "Sector" in str(c)), None)
    return pd.DataFrame({
        "symbol": t[sym].astype(str).str.replace(".", "-", regex=False),
        "cik": (t[cik].astype(str).str.replace(r"\.0$", "", regex=True).str.lstrip("0")
                if cik is not None else ""),
        "sector": t[sec].astype(str) if sec is not None else "Unknown",
    })


def sp500_history(db_path, start, end=None):
    """Point-in-time S&P 500 membership, rebuilt from monthly snapshots of Wikipedia's constituents
    table (via the page's revision history). A backtest on TODAY's members quietly assumes you knew
    which companies would survive — and which would be added *because* they had already soared —
    so the backtest uses this to trade a stock only while it was actually in the index.

    Snapshots are cached in the DB (`sp500_snapshots`): only months not fetched before hit
    Wikipedia. Returns [snap, ticker, sector] — `ticker` is the company's current symbol while it's
    still a member (renames resolved by SEC CIK, e.g. FB -> META), else its last listed symbol.
    Membership changes inside a month show up at the next month's snapshot."""
    end = end or pd.Timestamp.today().strftime("%Y-%m-%d")
    months = [d.strftime("%Y-%m-%d") for d in
              pd.date_range(pd.Timestamp(start).replace(day=1), end, freq="MS")]
    have = (db.read_df("SELECT * FROM sp500_snapshots", db_path)
            if db.table_exists("sp500_snapshots", db_path) else pd.DataFrame(columns=["snap"]))
    todo = [m for m in months if m not in set(have["snap"])]
    if todo:
        print(f"      fetching {len(todo)} monthly S&P 500 snapshot(s) from Wikipedia history...",
              flush=True)
        for n, m in enumerate(todo, 1):
            s = _snapshot(m)
            s["snap"] = m
            db.write_df(s, "sp500_snapshots", db_path, if_exists="append")  # resumable if interrupted
            have = pd.concat([have, s], ignore_index=True)
            if n % 12 == 0:
                print(f"        {n}/{len(todo)} (through {m})", flush=True)
            time.sleep(0.5)  # be polite to the API
    snaps = have[have["snap"].isin(months)].copy()
    snaps["cik"] = snaps["cik"].fillna("").astype(str)
    latest = snaps[snaps["snap"] == snaps["snap"].max()]
    current, cik_now = set(latest["symbol"]), dict(zip(latest["cik"], latest["symbol"]))
    snaps["ticker"] = [s if s in current else cik_now.get(c, s) if c else s
                       for s, c in zip(snaps["symbol"], snaps["cik"])]
    return snaps[["snap", "ticker", "sector"]].drop_duplicates(["snap", "ticker"]).reset_index(drop=True)


def get_universe(scope="sp500"):
    """Tradable universe. scope="sp500" (default) or "sp1500" (adds S&P 400 mid + 600 small caps —
    more high-beta candidates; the liquidity filters still prune the illiquid ones later)."""
    try:
        tickers = get_sp500_tickers()
        if scope == "sp1500":
            for sz, url in _SP_PAGES.items():
                try:
                    tickers += _sp_tickers(url)
                except Exception as e:  # noqa: BLE001 - keep S&P 500 if a widen page fails
                    print(f"[universe] S&P {sz} fetch failed ({e}); continuing without it.")
        if len(tickers) < 50:
            raise ValueError("suspiciously few tickers returned")
        return sorted(set(tickers))
    except Exception as e:  # noqa: BLE001 - want any failure to fall back gracefully
        print(f"[universe] Wikipedia fetch failed ({e}); using fallback list.")
        return sorted(set(FALLBACK))
