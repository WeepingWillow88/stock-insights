"""Build the tradable universe.

Phase 1 uses the S&P 500 as a reliable, no-API-key universe (fetched from
Wikipedia). It's liquid and contains plenty of high-beta names to prove the
engine. We can widen this to the Russell 1000-3000 in a later phase by swapping
in a constituents source.
"""
import io

import pandas as pd
import requests

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
