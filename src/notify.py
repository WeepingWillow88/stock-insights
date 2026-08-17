"""Alert delivery: build a plain-English digest and email it (or save it locally).

Email uses SMTP settings from environment variables:
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASS, ALERT_FROM, ALERT_TO
If those aren't set, the digest is written to data/alerts/ and printed — so nothing
is lost while you wire up email.
"""
import os
import smtplib
from email.mime.text import MIMEText


def build_digest(kind, sig, regime_row, events, fx_rate, cfg):
    lines = [f"HIGH-BETA SIGNALS — {kind} digest", "=" * 40, ""]

    if regime_row is not None:
        lines.append(f"MARKET REGIME: {regime_row.get('label', '?')}  "
                     f"(VIX {regime_row.get('vix')}, S&P vs 50d {regime_row.get('spy_vs_50d')}%, "
                     f"US10Y {regime_row.get('us10y')}%)")
        gate = {"RISK-ON": "new buys at full size",
                "CAUTION": "new buys at reduced size",
                "RISK-OFF": "new buys paused"}.get(str(regime_row.get('label')), "")
        lines.append(f"  -> {gate}")
    if events:
        ev = ", ".join(f"{e['label']} in {e['days_until']}d" for e in events[:5])
        lines.append(f"UPCOMING EVENTS: {ev}")
    lines.append(f"GBP/USD: {fx_rate:.4f}")
    lines.append("")

    if sig is not None and not sig.empty:
        sel = sig[sig["selected"].astype(bool)] if "selected" in sig.columns else sig.iloc[0:0]
        lines.append(f"TODAY'S PORTFOLIO ({len(sel)} positions):")
        for _, r in sel.iterrows():
            note = f"  | {r.get('flags')}" if r.get("flags") else ""
            lines.append(f"  BUY {r['ticker']:6s} entry ${r.get('entry')}  stop ${r.get('stop')}  "
                         f"target ${r.get('target')}  x{int(r.get('shares') or 0)}  "
                         f"risk £{int(r.get('risk_gbp') or 0)}{note}")
        sells = sig[sig["signal"] == "SELL"].head(10)
        if not sells.empty:
            lines.append("")
            lines.append("SELL / AVOID (trend broken or weak):")
            for _, r in sells.iterrows():
                lines.append(f"  SELL {r['ticker']:6s} — {r.get('reason', '')[:70]}")
        newsy = sig[sig.get("news", "").astype(str).isin(["negative", "positive"])] \
            if "news" in sig.columns else sig.iloc[0:0]
        if not newsy.empty:
            lines.append("")
            lines.append("NOTABLE NEWS:")
            for _, r in newsy.head(10).iterrows():
                lines.append(f"  {r['ticker']:6s} [{r.get('news')}] {r.get('news_note', '')[:80]}")

    lines.append("")
    lines.append("Decision-support only. Not financial advice.")
    return "\n".join(lines)


def send_or_save(subject, body, cfg):
    host = os.environ.get("SMTP_HOST")
    to = os.environ.get("ALERT_TO")
    if host and to:
        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = os.environ.get("ALERT_FROM", os.environ.get("SMTP_USER", "alerts@localhost"))
            msg["To"] = to
            port = int(os.environ.get("SMTP_PORT", "587"))
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls()
                if os.environ.get("SMTP_USER"):
                    s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
                s.sendmail(msg["From"], [to], msg.as_string())
            print(f"[notify] emailed digest to {to}")
            return "emailed"
        except Exception as e:  # noqa: BLE001
            print(f"[notify] email failed ({e}); saving locally instead.")

    # Fallback: save to disk
    d = os.path.join(os.path.dirname(cfg.db_path) or ".", "alerts")
    os.makedirs(d, exist_ok=True)
    safe = subject.replace(" ", "_").replace("/", "-").replace("[", "").replace("]", "")
    path = os.path.join(d, f"{safe}.txt")
    with open(path, "w") as f:
        f.write(body)
    print(f"[notify] saved digest to {path}")
    return path
