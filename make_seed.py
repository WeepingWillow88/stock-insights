"""Rebuild the slim seed database that ships in the repo (data/seed.db).

The deployed app copies data/seed.db into place on first run, so this snapshot is what
viewers see. It's the working DB minus the heavy backtest caches (10-year prices + the S&P 500
membership history), so it stays small (~9 MB) and git-friendly. The backtest *results* ship.

Update the shared baseline in two commands:
    python -m src.pipeline      # (or hit Refresh) to pull fresh data
    python make_seed.py         # rebuild the seed, then commit + push
"""
import os
import shutil
import sqlite3

from src.config import CONFIG

DST = "data/seed.db"
# Caches regenerated on demand by `python -m src.backtest` (raw prices, membership snapshots).
DROP_TABLES = ["backtest_prices", "backtest_meta", "sp500_snapshots"]


def main():
    if not os.path.exists(CONFIG.db_path):
        raise SystemExit(f"No working DB at {CONFIG.db_path} — run `python -m src.pipeline` first.")
    shutil.copy(CONFIG.db_path, DST)
    conn = sqlite3.connect(DST)
    for t in DROP_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    mb = os.path.getsize(DST) / 1e6
    print(f"Rebuilt {DST} ({mb:.1f} MB) from {CONFIG.db_path} — dropped: {', '.join(DROP_TABLES)}")


if __name__ == "__main__":
    main()
