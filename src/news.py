"""Layer C: news ingestion + sentiment.

Pulls recent headlines per ticker from free RSS (Google News) and scores them for
a short-term high-beta trader. Uses the Claude API when credentials are available
(ANTHROPIC_API_KEY or an `ant auth login` profile) and falls back to a transparent
keyword scorer otherwise — so the app always runs.

Output per ticker:
  sentiment    float  -1 (very negative) .. +1 (very positive)
  label        negative | neutral | positive
  materiality  low | medium | high
  macro_driver inflation | rates | geopolitical | earnings | company_specific | none
  action_bias  none | caution_hold | avoid   (avoid can turn a BUY into a HOLD)
  rationale    one-line plain-English explanation
  source       claude | keywords
  headlines    the raw headlines (so the reader can dig in)
"""
import datetime as dt
import os
import xml.etree.ElementTree as ET

import requests

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

_NEG = ["war", "strike", "inflation", "recession", "downgrade", "lawsuit", "probe",
        "investigation", "miss", "misses", "cut", "cuts", "plunge", "slump", "fraud",
        "recall", "ban", "tariff", "sanction", "layoff", "layoffs", "halt", "warning",
        "weak", "fear", "selloff", "sell-off", "crash", "drop", "falls", "fell", "sink"]
_POS = ["beat", "beats", "record", "surge", "surges", "upgrade", "raise", "raises", "soar",
        "jump", "jumps", "expand", "approval", "approved", "wins", "strong", "rally",
        "breakthrough", "partnership", "gains", "rises", "rose", "outperform", "tops"]


def _google_rss(ticker, limit, days):
    """Recent headline TITLES for a ticker via Google News RSS (free, no key). Titles only."""
    url = (f"https://news.google.com/rss/search?q={ticker}+stock+when:{days}d"
           f"&hl=en-US&gl=US&ceid=US:en")
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        titles = [it.findtext("title", default="").strip() for it in root.iter("item")]
        return _dedupe([t for t in titles if t])[:limit]
    except Exception:  # noqa: BLE001
        return []


def _finnhub_news(ticker, cfg, limit):
    """Recent company news (headline + summary + source, reliably tagged to the ticker) from
    Finnhub when FINNHUB_API_KEY is set. Returns richer item strings, or None on any failure so
    callers fall back to the RSS titles. Free tier covers /company-news."""
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        return None
    try:
        today = dt.date.today()
        frm = today - dt.timedelta(days=cfg.news_lookback_days)
        url = ("https://finnhub.io/api/v1/company-news"
               f"?symbol={ticker}&from={frm.isoformat()}&to={today.isoformat()}&token={key}")
        rows = requests.get(url, timeout=15).json()
        if not isinstance(rows, list) or not rows:
            return None
        rows = sorted(rows, key=lambda r: r.get("datetime", 0), reverse=True)
        items = []
        for r in rows[:limit]:
            head = str(r.get("headline", "")).strip()
            if not head:
                continue
            summ = str(r.get("summary", "")).strip()
            src = str(r.get("source", "")).strip()
            txt = head + (f" — {summ}" if summ else "") + (f" [{src}]" if src else "")
            items.append(txt[:400])  # cap so one long story can't dominate the prompt
        return items or None
    except Exception:  # noqa: BLE001
        return None


def fetch_headlines(ticker, cfg):
    """Recent news items for a ticker: Finnhub (headline + summary + source) if a key is set,
    else Google News RSS titles. Returns a list of strings."""
    items = _finnhub_news(ticker, cfg, cfg.news_headlines)
    if items is not None:
        return items
    return _google_rss(ticker, cfg.news_headlines, cfg.news_lookback_days)


def _dedupe(titles):
    """Drop near-duplicate headlines (Google News repeats the same story across outlets).
    Key on the headline minus its trailing ' - Source' and lowercased first 60 chars."""
    seen, out = set(), []
    for t in titles:
        core = t.rsplit(" - ", 1)[0].lower().strip()[:60]
        if core in seen:
            continue
        seen.add(core)
        out.append(t)
    return out


def score_with_keywords(headlines):
    blob = " ".join(headlines).lower()
    neg = sum(blob.count(w) for w in _NEG)
    pos = sum(blob.count(w) for w in _POS)
    total = neg + pos
    s = 0.0 if total == 0 else round((pos - neg) / total, 2)
    label = "neutral" if abs(s) < 0.2 else ("positive" if s > 0 else "negative")
    materiality = "high" if total >= 4 else ("medium" if total >= 2 else "low")
    if s <= -0.5 and total >= 3:
        bias = "avoid"
    elif s < 0:
        bias = "caution_hold"
    else:
        bias = "none"
    return {
        "sentiment": s, "label": label, "materiality": materiality,
        "macro_driver": "none", "action_bias": bias,
        "rationale": f"Keyword scan of {len(headlines)} headlines: {pos} positive vs {neg} negative signal words.",
        "source": "keywords",
    }


_SCHEMA = {
    "type": "object",
    "properties": {
        "sentiment": {"type": "number"},
        "label": {"type": "string", "enum": ["negative", "neutral", "positive"]},
        "materiality": {"type": "string", "enum": ["low", "medium", "high"]},
        "macro_driver": {"type": "string",
                         "enum": ["inflation", "rates", "geopolitical", "earnings",
                                  "company_specific", "none"]},
        "action_bias": {"type": "string", "enum": ["none", "caution_hold", "avoid"]},
        "rationale": {"type": "string"},
    },
    "required": ["sentiment", "label", "materiality", "macro_driver", "action_bias", "rationale"],
    "additionalProperties": False,
}


def score_with_claude(ticker, headlines, cfg, context=""):
    import json

    import anthropic

    client = anthropic.Anthropic()  # resolves ANTHROPIC_API_KEY or an `ant auth` profile
    joined = "\n".join(f"- {h}" for h in headlines)
    ctx = f"\n\nMarket context: {context}." if context else ""
    prompt = (
        f"You are a markets news analyst helping a short-term, high-beta swing trader. "
        f"Assess how this recent news about {ticker} is likely to affect the stock over the next "
        f"few trading days — weigh the likely *reaction* given the context, not just the tone."
        f"{ctx}\n\nNews items:\n{joined}\n\n"
        "Return: sentiment (-1 very negative to +1 very positive), a label, materiality, "
        "the dominant macro driver if any (inflation, rates, geopolitical, earnings, "
        "company_specific, or none), and an action_bias — 'avoid' if fresh material negative "
        "news argues against opening a new long, 'caution_hold' if mixed or risky, else 'none'. "
        "Keep the rationale to one plain-English sentence a non-expert can understand."
    )
    resp = client.messages.create(
        model=cfg.claude_model,
        max_tokens=1024,
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model refused")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    out = json.loads(text)
    out["source"] = "claude"
    return out


_FINBERT = None


def _finbert_pipe():
    global _FINBERT
    if _FINBERT is None:
        from transformers import pipeline  # heavy, optional dependency
        _FINBERT = pipeline("text-classification", model="ProsusAI/finbert", top_k=None)
    return _FINBERT


def score_with_finbert(headlines):
    """Local FinBERT — a model fine-tuned on financial news. Free, offline, no API key.
    Enable with:  pip install -r requirements-finbert.txt"""
    results = _finbert_pipe()(headlines)
    pos = neg = 0.0
    for r in results:
        d = {x["label"].lower(): x["score"] for x in r}
        pos += d.get("positive", 0.0)
        neg += d.get("negative", 0.0)
    n = len(headlines)
    s = round((pos - neg) / n, 2)
    label = "neutral" if abs(s) < 0.15 else ("positive" if s > 0 else "negative")
    materiality = "high" if abs(s) >= 0.4 else ("medium" if abs(s) >= 0.2 else "low")
    bias = "avoid" if s <= -0.4 else ("caution_hold" if s < -0.1 else "none")
    return {"sentiment": s, "label": label, "materiality": materiality,
            "macro_driver": "none", "action_bias": bias,
            "rationale": f"FinBERT (finance-trained model) read {n} headlines; net tone {s:+.2f}.",
            "source": "finbert"}


def score(ticker, headlines, cfg, context=""):
    """Sentiment with graceful fallback: Claude -> FinBERT -> keywords. The 'source'
    field records which engine actually ran. `context` (recent move + regime) is used by Claude."""
    if not headlines:
        return {"sentiment": 0.0, "label": "neutral", "materiality": "low",
                "macro_driver": "none", "action_bias": "none",
                "rationale": "No recent headlines found.", "source": "none"}
    if cfg.use_claude_news:
        try:
            return score_with_claude(ticker, headlines, cfg, context)
        except Exception:  # noqa: BLE001 - no creds / old SDK / refusal -> next engine
            pass
    if cfg.use_finbert:
        try:
            return score_with_finbert(headlines)
        except Exception:  # noqa: BLE001 - transformers not installed -> keywords
            pass
    return score_with_keywords(headlines)


def _price_context(prices, ticker, regime_label, window=5):
    """A short 'recent move + market regime' string to ground Claude's read (empty if unavailable)."""
    parts = []
    if prices is not None:
        try:
            p = prices[prices["ticker"] == ticker].sort_values("date")["adj_close"].astype(float)
            if len(p) > window:
                mv = (p.iloc[-1] / p.iloc[-1 - window] - 1) * 100
                parts.append(f"the stock is {mv:+.1f}% over the last {window} sessions")
        except Exception:  # noqa: BLE001
            pass
    if regime_label:
        parts.append(f"the broad market regime is {regime_label}")
    return "; ".join(parts)


def build_news_map(tickers, cfg, prices=None, regime_label=None):
    """Return {ticker: {sentiment, label, ..., headlines}} for each ticker. When prices/regime are
    supplied, Claude also gets each stock's recent move + the regime as context."""
    out = {}
    for t in tickers:
        heads = fetch_headlines(t, cfg)
        s = score(t, heads, cfg, _price_context(prices, t, regime_label))
        s["headlines"] = heads
        out[t] = s
    return out
