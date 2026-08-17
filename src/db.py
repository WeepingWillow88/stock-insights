"""Tiny SQLite helper layer. Phase 1 stores everything in one local .db file."""
import os
import sqlite3

import pandas as pd


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
