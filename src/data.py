"""Price-history ingestion via yfinance (free, no API key).

Returns long-format OHLCV so we can compute beta, momentum, ATR and $-volume.
Batched to stay polite to Yahoo's endpoints.
"""
import pandas as pd
import yfinance as yf

COLUMNS = ["date", "ticker", "open", "high", "low", "close", "adj_close", "volume"]


def download_prices(tickers, period="1y", batch_size=50):
    frames = []
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        raw = yf.download(
            batch,
            period=period,
            interval="1d",
            auto_adjust=False,       # keep both Close and Adj Close
            group_by="ticker",
            progress=False,
            threads=True,
        )
        frames.extend(_reshape(raw, batch))
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    return out.dropna(subset=["adj_close"])


def _reshape(raw, batch):
    frames = []
    if raw is None or len(raw) == 0:
        return frames
    if len(batch) == 1:
        df = _finalize(raw.copy(), batch[0])
        if df is not None:
            frames.append(df)
        return frames
    level0 = raw.columns.get_level_values(0)
    for t in batch:
        if t not in level0:
            continue
        df = _finalize(raw[t].copy(), t)
        if df is not None:
            frames.append(df)
    return frames


def get_fx_rate(pair="GBPUSD=X", fallback=1.27):
    """Latest FX rate (e.g. GBP->USD). Falls back to a static value if offline."""
    try:
        h = yf.Ticker(pair).history(period="5d")
        rate = float(h["Close"].dropna().iloc[-1])
        if rate > 0:
            return rate
    except Exception as e:  # noqa: BLE001
        print(f"[fx] live rate fetch failed ({e}); using fallback {fallback}.")
    return fallback


def _finalize(df, ticker):
    if df is None or df.empty:
        return None
    df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]
    df = df.reset_index()
    df = df.rename(columns={"index": "date", "Date": "date", "date": "date"})
    if "date" not in df.columns:
        df = df.rename(columns={df.columns[0]: "date"})
    df["ticker"] = ticker
    for col in ["open", "high", "low", "close", "adj_close", "volume"]:
        if col not in df.columns:
            df[col] = pd.NA
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df[COLUMNS]
