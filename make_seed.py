"""Rebuild the slim seed database that ships in the repo (data/seed.db).

The deployed app copies data/seed.db into place on first run, so this snapshot is what
viewers see. It's the working DB minus the heavy 10-year backtest price cache, so it stays
small (~9 MB) and git-friendly.

Update the shared baseline in two commands:
    python -m src.pipeline      # (or hit Refresh) to pull fresh data
    python make_seed.py         # rebuild the seed, then commit + push
"""
import os
import shutil
import sqlite3

from src.config import CONFIG

DST = "data/seed.db"
DROP_TABLES = ["backtest_prices", "backtest_meta"]  # huge raw price cache — regenerated on demand


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
