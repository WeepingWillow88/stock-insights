"""Tiny SQLite helper layer. Phase 1 stores everything in one local .db file."""
import os
import shutil
import sqlite3

import pandas as pd


def _freshness_key(path):
    """(market_through, last_updated) from a DB's meta table, or None if unavailable. Compared as
    a tuple: a later market date wins, and on the same date the later refresh wins."""
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("SELECT market_through, last_updated FROM meta LIMIT 1").fetchone()
            return (str(row[0]), str(row[1] or "")) if row and row[0] else None
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
    - With refresh_if_newer=True, also re-copies when the seed holds NEWER data than the working
      copy (used by the Cloud app, whose container keeps its working DB between pushes): a later
      market date, or the same date refreshed later. Comparing the date alone missed every
      same-day refresh — the post-close run after a morning run, or a scheduled run after an
      in-app 'Pull fresh prices' — leaving the app on the day's first refresh until the next day.
      Uses the meta timestamps, not file mtime, so a richer local working DB (e.g. one holding the
      backtest price cache) isn't clobbered when its data is already as fresh.

    Returns True if a copy happened.
    """
    if not os.path.exists(seed_path):
        return False
    if not os.path.exists(db_path):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        shutil.copy(seed_path, db_path)
        return True
    if refresh_if_newer:
        st, wt = _freshness_key(seed_path), _freshness_key(db_path)
        if st and wt and st > wt:
            shutil.copy(seed_path, db_path)
            return True
    return False


def sync_backtest_from_seed(db_path, seed_path="data/seed.db"):
    """Copy the backtest_* result tables from the shipped snapshot when its backtest is newer.

    The backtest runs offline and ships inside the seed, but a long-running Cloud container keeps
    its own working DB and only re-seeds it when the seed has newer *market* data — so a freshly
    shipped backtest would otherwise wait for the next price refresh. This moves just those tables,
    leaving prices, signals and the ledger alone. Returns True if anything was copied."""
    if not os.path.exists(seed_path) or not os.path.exists(db_path) \
            or os.path.abspath(seed_path) == os.path.abspath(db_path):
        return False

    def generated(path):
        try:
            conn = sqlite3.connect(path)
            try:
                row = conn.execute("SELECT generated FROM backtest_summary LIMIT 1").fetchone()
                return str(row[0]) if row and row[0] else ""
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - no table / old schema
            return ""

    if not generated(seed_path) or generated(seed_path) <= generated(db_path):
        return False
    src = sqlite3.connect(seed_path)
    try:
        tables = [r[0] for r in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'backtest_%'")]
        frames = {t: pd.read_sql_query(f"SELECT * FROM {t}", src) for t in tables}
    finally:
        src.close()
    for t, df in frames.items():
        write_df(df, t, db_path)
    return True


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
