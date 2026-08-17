"""Hourly news-shock check.

Between the twice-daily runs, this scans the shortlist for a big intraday move and,
when one is found, pulls fresh news and scores it — then alerts. This is the piece
that catches the "Micron dips on an Iran/inflation headline" moment intraday.

Run hourly during market hours (cron):
    python -m src.shock
"""
import yfinance as yf

from .config import CONFIG
from . import db, news, notify


def _intraday_move(ticker):
    """Return (pct_move, last_price) for today, or (None, None)."""
    try:
        t = yf.Ticker(ticker)
        fi = t.fast_info
        last = float(fi.get("last_price") or fi.get("lastPrice"))
        prev = float(fi.get("previous_close") or fi.get("previousClose"))
        if prev:
            return (last / prev - 1.0, last)
    except Exception:
        pass
    return (None, None)


def run_shock_check(cfg=CONFIG):
    shortlist = db.read_df("SELECT ticker FROM screen_results ORDER BY rank", cfg.db_path)
    if shortlist.empty:
        print("[shock] no shortlist; run the pipeline first.")
        return []

    alerts = []
    for t in shortlist["ticker"].tolist():
        move, last = _intraday_move(t)
        if move is None or abs(move) < cfg.shock_move_pct:
            continue
        heads = news.fetch_headlines(t, cfg.news_headlines)
        s = news.score(t, heads, cfg)
        alerts.append({
            "ticker": t, "move_pct": round(move * 100, 1), "last": round(last, 2),
            "news_label": s["label"], "rationale": s["rationale"],
            "headline": heads[0] if heads else "(no headline)",
        })

    if alerts:
        lines = ["INTRADAY SHOCK ALERTS", "=" * 30, ""]
        for a in sorted(alerts, key=lambda x: abs(x["move_pct"]), reverse=True):
            arrow = "▲" if a["move_pct"] > 0 else "▼"
            lines.append(f"{arrow} {a['ticker']:6s} {a['move_pct']:+.1f}%  ${a['last']}  "
                         f"[news: {a['news_label']}]")
            lines.append(f"     {a['headline']}")
            lines.append(f"     {a['rationale'][:100]}")
            lines.append("")
        notify.send_or_save("Intraday shock alert", "\n".join(lines), cfg)
        print(f"[shock] {len(alerts)} alert(s) sent.")
    else:
        print("[shock] no shocks detected.")
    return alerts


if __name__ == "__main__":
    run_shock_check()
