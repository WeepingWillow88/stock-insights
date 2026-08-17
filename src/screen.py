"""Turn per-ticker metrics into a ranked high-beta shortlist.

Ranking philosophy (matches the user's 'high beta AND trending up' brief):
  score = 0.6 * z(beta) + 0.4 * z(3-month momentum) + 0.5 bonus if uptrending
Filtered first for tradability (price, $-volume) and min beta.
"""
import numpy as np


def build_screen(metrics, cfg):
    df = metrics.copy()

    # Tradability filters
    df = df[df["price"] >= cfg.min_price]
    df = df[df["avg_dollar_vol"] >= cfg.min_avg_dollar_volume]
    df = df[df["beta"].notna()]

    df["high_beta"] = df["beta"] >= cfg.min_beta

    df["beta_z"] = _z(df["beta"])
    df["mom_z"] = _z(df["mom_3m"].fillna(0))
    df["score"] = df["beta_z"] * 0.6 + df["mom_z"] * 0.4
    df.loc[df["uptrend"], "score"] += 0.5

    shortlist = (
        df[df["high_beta"]]
        .sort_values("score", ascending=False)
        .head(cfg.shortlist_size)
        .reset_index(drop=True)
    )
    shortlist.insert(0, "rank", shortlist.index + 1)
    return shortlist


def _z(s):
    s = s.astype(float)
    mu, sd = s.mean(), s.std()
    if not sd or np.isnan(sd):
        return s * 0
    return (s - mu) / sd
