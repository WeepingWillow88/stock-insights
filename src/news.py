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
import html
import os
import re
import xml.etree.ElementTree as ET

import requests

from . import universe

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

_NEG = ["war", "strike", "inflation", "recession", "downgrade", "lawsuit", "probe",
        "investigation", "miss", "misses", "cut", "cuts", "plunge", "slump", "fraud",
        "recall", "ban", "tariff", "sanction", "layoff", "layoffs", "halt", "warning",
        "weak", "fear", "selloff", "sell-off", "crash", "drop", "falls", "fell", "sink"]
_POS = ["beat", "beats", "record", "surge", "surges", "upgrade", "raise", "raises", "soar",
        "jump", "jumps", "expand", "approval", "approved", "wins", "strong", "rally",
        "breakthrough", "partnership", "gains", "rises", "rose", "outperform", "tops"]

# Finnhub's free company-news feed (and Google News' loose "TICKER stock" match) tag a lot of
# generic, market-wide screener/aggregator content to individual symbols (a stock merely appears
# in a "most active S&P 500" listicle, or an ETF/sector round-up name-drops it). That's noise for a
# per-stock sentiment read, so we drop it and keep the company-specific items.
_NOISE_SOURCES = ("chartmill", "valueengine", "marketbeat", "simply wall")
_NOISE_HEADLINE = ("s&p500", "s&p 500", "most active", "top movers", "gap up", "gap down",
                   "trading volume", "today's session", "market summary", "stocks to watch",
                   "biggest movers", "biggest gainers", "biggest losers", "premarket",
                   "market wrap", "week in review", "whale activity", "unusual options",
                   "options activity", "lightning round", "stocks to buy", "stocks to watch now")
# Listicle pattern: "10 Information Technology Stocks ...", "7 Stocks To Buy ..." — a numbered
# round-up where the ticker is one of many, never a company-specific story.
_LISTICLE_RE = re.compile(r"^\s*\d+\s+[\w\s.&/'-]*\bstocks?\b", re.I)
# Corporate suffixes / filler dropped when picking a company's distinctive "brand" word.
_NAME_STOP = {"the", "inc", "corp", "corporation", "co", "company", "companies", "ltd", "plc",
              "group", "holdings", "holding", "technologies", "technology", "systems",
              "international", "industries", "enterprises", "and", "class"}


def _normalize(s):
    """Straighten curly quotes/apostrophes so phrase filters match reliably ("today's" vs "today's")."""
    return (s.replace("’", "'").replace("‘", "'")
            .replace("“", '"').replace("”", '"'))


def _clean_text(s):
    """Un-escape HTML entities and repair the common UTF-8-read-as-Latin-1 mojibake (e.g. an em
    dash showing as 'â€"'). Only attempts the byte round-trip when the tell-tale bytes are present,
    and keeps the original on failure — so clean text is never corrupted."""
    if not s:
        return s
    s = html.unescape(s)
    # The classic "â€"/"Ã"/"Â" mojibake is UTF-8 bytes misdecoded as Windows-1252 (cp1252, not
    # Latin-1 — the tell-tale "€"/curly-quote bytes only exist in cp1252). Reverse it by encoding
    # back to cp1252 then decoding as UTF-8. Only when the signature is present, and keep the
    # original on failure, so clean text is never corrupted.
    if "Ã" in s or "â€" in s or "Â" in s:
        try:
            s = s.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return s.strip()


def _is_market_noise(headline, source):
    h, s = _normalize(headline.lower()), source.lower()
    if any(n in s for n in _NOISE_SOURCES):
        return True
    if _LISTICLE_RE.search(_normalize(headline)):
        return True
    return any(n in h for n in _NOISE_HEADLINE)


def _brand_word(name):
    """The distinctive first word of a company name (e.g. 'Palantir' from 'Palantir Technologies
    Inc.'), skipping a leading 'The' and corporate filler. None if nothing usable."""
    if not name:
        return None
    for w in re.sub(r"[.,]", " ", str(name)).split():
        wl = w.lower()
        if len(wl) >= 3 and wl not in _NAME_STOP:
            return wl
    return None


def _relevance_tokens(ticker, name):
    """Lower-cased tokens a headline must contain to count as on-topic: the ticker (if it's long
    enough to match safely) and the company's brand word. Empty => can't match safely, so relevance
    filtering is skipped for this name rather than over-dropping."""
    toks = set()
    if ticker and len(ticker) >= 3:
        toks.add(ticker.lower())
    brand = _brand_word(name)
    if brand:
        toks.add(brand)
    return toks


def _is_relevant(text, tokens):
    """True if the item is about this company: the ticker or brand word appears as a whole word.
    True when tokens is empty (we can't verify safely — don't over-filter)."""
    if not tokens:
        return True
    t = text.lower()
    return any(re.search(rf"\b{re.escape(tok)}\b", t) for tok in tokens)


_NAME_MAP = None


def _company_name(ticker):
    """Company name for a ticker (cached per process); '' if unknown."""
    global _NAME_MAP
    if _NAME_MAP is None:
        try:
            _NAME_MAP = universe.get_names()
        except Exception:  # noqa: BLE001
            _NAME_MAP = {}
    return _NAME_MAP.get(ticker, "")


def _google_rss(ticker, limit, days, site=None):
    """Recent headline TITLES for a ticker via Google News RSS (free, no key). Titles only.
    Pass `site` (e.g. "cnbc.com") to restrict the query to a single outlet. Titles are cleaned
    (entities/mojibake); relevance + noise filtering happen in fetch_headlines."""
    q = f"{ticker}+stock+when:{days}d"
    if site:
        q += f"+site:{site}"
    url = (f"https://news.google.com/rss/search?q={q}"
           f"&hl=en-US&gl=US&ceid=US:en")
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        titles = [_clean_text(it.findtext("title", default="")) for it in root.iter("item")]
        return _dedupe([t for t in titles if t])[:limit]
    except Exception:  # noqa: BLE001
        return []


def _finnhub_news(ticker, cfg, limit, tokens):
    """Recent company news (headline + summary + source) from Finnhub when FINNHUB_API_KEY is set.
    Free-tier tagging is loose, so we filter across ALL rows for market-wide screener noise AND
    for relevance (the ticker or company brand must appear in the headline+summary) before capping
    to `limit` — otherwise off-topic, cross-tagged stories eat the slots. Returns richer item
    strings, or None on any failure so callers fall back to the RSS titles."""
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
        for r in rows:
            head = _clean_text(str(r.get("headline", "")))
            if not head:
                continue
            src = _clean_text(str(r.get("source", "")))
            summ = _clean_text(str(r.get("summary", "")))
            if _is_market_noise(head, src) or not _is_relevant(f"{head} {summ}", tokens):
                continue
            txt = head + (f" — {summ}" if summ else "") + (f" [{src}]" if src else "")
            items.append(txt[:400])  # cap so one long story can't dominate the prompt
            if len(items) >= limit:
                break
        return items or None
    except Exception:  # noqa: BLE001
        return None


def _cnbc_rss(ticker, limit, days):
    """Recent CNBC headlines for a ticker (free, no key) via a site-restricted Google News RSS
    query. CNBC has no per-symbol RSS/API of its own, so we filter Google News to cnbc.com."""
    return _google_rss(ticker, limit, days, site="cnbc.com")


def fetch_headlines(ticker, cfg):
    """Recent, on-topic news items for a ticker. Finnhub (headline + summary + source) is the
    trusted per-symbol source; Google News RSS is a **last resort**, used only when Finnhub yields
    nothing on-topic — its loose "TICKER stock" query cross-tags unrelated ETF/sector stories.
    Every item is stripped of market-wide screener noise AND checked for relevance (the ticker or
    the company's brand name must appear), so an off-topic headline can't reach the scorer. When
    cfg.use_cnbc is on, on-topic CNBC headlines are blended in (surfaced first) so that outlet is
    always represented. Returns a list of strings, deduped and capped at cfg.news_headlines."""
    tokens = _relevance_tokens(ticker, _company_name(ticker))

    def _keep_rss(titles):
        return [t for t in titles
                if not _is_market_noise(t, "") and _is_relevant(t, tokens)]

    items = _finnhub_news(ticker, cfg, cfg.news_headlines, tokens) or []
    if not items:  # Finnhub gave nothing on-topic -> fall back to (filtered) RSS titles
        items = _keep_rss(_google_rss(ticker, cfg.news_headlines, cfg.news_lookback_days))
    if getattr(cfg, "use_cnbc", False):
        cnbc = _keep_rss(_cnbc_rss(ticker, cfg.cnbc_headlines, cfg.news_lookback_days))
        if cnbc:
            items = _dedupe(cnbc + list(items))
    return list(items)[:cfg.news_headlines]


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
