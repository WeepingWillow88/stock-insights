"""Tiny SQLite helper layer. Phase 1 stores everything in one local .db file."""
import os
import shutil
import sqlite3

import pandas as pd


def _market_through(path):
    """Latest market date recorded in a DB's meta table, or None if unavailable."""
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("SELECT market_through FROM meta LIMIT 1").fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None


def bootstrap_working_db(db_path, seed_path="data/seed.db", refresh_if_newer=False):
    """Put the shipped snapshot in place as the working DB so accumulated state (esp. the
    track-record ledger) and prices carry over.

    - Always copies when the working DB is ABSENT — this is the key fix for CI / the daily
      GitHub Action, where only seed.db is checked out: without it the pipeline would start
      from an empty ledger every run and never accumulate closed trades.
    - With refresh_if_newer=True, also re-copies when the seed covers a LATER market date than
      the working copy (used by the Cloud app, whose working DB is ephemeral and can go stale
      across redeploys). Compares market_through, not file mtime, so a richer local working DB
      (e.g. one holding the backtest price cache) isn't clobbered when data is already current.

    Returns True if a copy happened.
    """
    if not os.path.exists(seed_path):
        return False
    if not os.path.exists(db_path):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        shutil.copy(seed_path, db_path)
        return True
    if refresh_if_newer:
        st, wt = _market_through(seed_path), _market_through(db_path)
        if st and wt and str(st) > str(wt):
            shutil.copy(seed_path, db_path)
            return True
    return False


def get_connection(db_path):
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return sqlite3.connect(db_path)


def write_df(df, table, db_path, if_exists="replace"):
    conn = get_connection(db_path)
    try:
        df.to_sql(table, conn, if_exists=if_exists, index=False)
    finally:
        conn.close()


def read_df(query, db_path, params=None):
    conn = get_connection(db_path)
    try:
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


def table_exists(table, db_path):
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        return cur.fetchone() is not None
    finally:
        conn.close()
