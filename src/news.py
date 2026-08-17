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


def fetch_headlines(ticker, limit=8):
    """Recent headlines for a ticker via Google News RSS (free, no key)."""
    url = (f"https://news.google.com/rss/search?q={ticker}+stock+when:3d"
           f"&hl=en-US&gl=US&ceid=US:en")
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        titles = [it.findtext("title", default="").strip() for it in root.iter("item")]
        return _dedupe([t for t in titles if t])[:limit]
    except Exception:
        return []


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


def score_with_claude(ticker, headlines, cfg):
    import json

    import anthropic

    client = anthropic.Anthropic()  # resolves ANTHROPIC_API_KEY or an `ant auth` profile
    joined = "\n".join(f"- {h}" for h in headlines)
    prompt = (
        f"You are a markets news analyst helping a short-term, high-beta swing trader. "
        f"Assess how these recent headlines about {ticker} are likely to affect the stock "
        f"over the next few trading days.\n\nHeadlines:\n{joined}\n\n"
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


def score(ticker, headlines, cfg):
    """Sentiment with graceful fallback: Claude -> FinBERT -> keywords. The 'source'
    field records which engine actually ran."""
    if not headlines:
        return {"sentiment": 0.0, "label": "neutral", "materiality": "low",
                "macro_driver": "none", "action_bias": "none",
                "rationale": "No recent headlines found.", "source": "none"}
    if cfg.use_claude_news:
        try:
            return score_with_claude(ticker, headlines, cfg)
        except Exception:  # noqa: BLE001 - no creds / old SDK / refusal -> next engine
            pass
    if cfg.use_finbert:
        try:
            return score_with_finbert(headlines)
        except Exception:  # noqa: BLE001 - transformers not installed -> keywords
            pass
    return score_with_keywords(headlines)


def build_news_map(tickers, cfg):
    """Return {ticker: {sentiment, label, ..., headlines}} for each ticker."""
    out = {}
    for t in tickers:
        heads = fetch_headlines(t, cfg.news_headlines)
        s = score(t, heads, cfg)
        s["headlines"] = heads
        out[t] = s
    return out
