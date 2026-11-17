from __future__ import annotations
import sqlite3
from app.config import DATA_DIR, DB_PATH


def connect(*, immediate: bool = False) -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # timeout / busy_timeout：并发封账时第二个写事务会等待而不是立刻抛
    # "database is locked"；写互斥仍由 BEGIN IMMEDIATE 保证。
    conn = sqlite3.connect(DB_PATH, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    if immediate:
        conn.execute("BEGIN IMMEDIATE")
    return conn
