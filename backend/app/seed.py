from __future__ import annotations
import sqlite3
from app import reseal_policy
from app.db import connect

RESEAL_SETTING_KEY = reseal_policy.SETTING_KEY


SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tills (
  id INTEGER PRIMARY KEY,
  till_code TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cash_lines (
  id INTEGER PRIMARY KEY,
  line_no TEXT NOT NULL UNIQUE,
  till_code TEXT NOT NULL,
  business_day TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  amount REAL NOT NULL,
  pay_method TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS close_batches (
  id INTEGER PRIMARY KEY,
  batch_no TEXT NOT NULL UNIQUE,
  business_day TEXT NOT NULL,
  system_total REAL NOT NULL,
  declared_cash REAL NOT NULL,
  variance REAL NOT NULL,
  line_count INTEGER NOT NULL,
  status TEXT NOT NULL,
  sealed_at TEXT NOT NULL,
  note TEXT,
  superseded_by_batch_id INTEGER,
  superseded_at TEXT,
  superseded_reason TEXT
);
CREATE TABLE IF NOT EXISTS variance_rows (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL,
  business_day TEXT NOT NULL,
  variance REAL NOT NULL,
  disposition TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS variance_holds (
  id INTEGER PRIMARY KEY,
  business_day TEXT NOT NULL,
  reason TEXT
);
CREATE TABLE IF NOT EXISTS reopen_requests (
  id INTEGER PRIMARY KEY,
  business_day TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'none'
);
'''

# 索引在列补齐之后创建：旧库直接建局部索引会因缺列失败。
INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_cash_lines_day ON cash_lines(business_day)",
    "CREATE INDEX IF NOT EXISTS idx_close_batches_day ON close_batches(business_day)",
    # 业务不变量：任一营业日最多一个现行 sealed 批。
    # 迁移先收敛历史脏数据，再建索引；极端情况下建不上也不阻断启动
    # （BEGIN IMMEDIATE 串行化仍保证运行期唯一）。
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_close_batches_current_day "
    "ON close_batches(business_day) WHERE status='sealed'",
]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _collapse_legacy_duplicate_sealed(conn: sqlite3.Connection) -> None:
    """旧库可能因历史 bug 一天留下多个 sealed 批。

    不能静默删数：保留 id 最大（最近封账）的批为唯一现行，其余显式标
    ``superseded``（数字原样保留，只读审计），并指回新现行批。
    """
    rows = conn.execute(
        '''SELECT business_day FROM close_batches
           WHERE status='sealed'
           GROUP BY business_day HAVING COUNT(*) > 1'''
    ).fetchall()
    for row in rows:
        day = row["business_day"]
        keep = conn.execute(
            "SELECT id, sealed_at FROM close_batches "
            "WHERE business_day=? AND status='sealed' ORDER BY id DESC LIMIT 1",
            (day,),
        ).fetchone()
        if keep is None:
            continue
        conn.execute(
            '''UPDATE close_batches
               SET status='superseded', superseded_by_batch_id=?,
                   superseded_reason=COALESCE(superseded_reason, 'migration: duplicate sealed batch')
               WHERE business_day=? AND status='sealed' AND id<>?''',
            (keep["id"], day, keep["id"]),
        )


def migrate(conn: sqlite3.Connection) -> None:
    """对既有数据库做幂等升级；新库由 SCHEMA 直接建出目标形状。"""
    _add_column(conn, "close_batches", "superseded_by_batch_id", "INTEGER")
    _add_column(conn, "close_batches", "superseded_at", "TEXT")
    _add_column(conn, "close_batches", "superseded_reason", "TEXT")
    _collapse_legacy_duplicate_sealed(conn)
    # draft 只在覆盖重算的单事务内短暂存在；进程崩溃后残留的 draft
    # 不应被当作历史，启动时清理（回滚语义下的孤儿行）。
    conn.execute("DELETE FROM close_batches WHERE status='draft'")
    # 旧库没有重封策略配置时补一条显式配置；已有（含历史别名）时保持原值。
    has_policy = conn.execute(
        "SELECT 1 FROM settings WHERE key IN ({}) LIMIT 1".format(
            ",".join("?" for _ in reseal_policy.LEGACY_SETTING_KEYS)
        ),
        reseal_policy.LEGACY_SETTING_KEYS,
    ).fetchone()
    if has_policy is None:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?)",
            (RESEAL_SETTING_KEY, reseal_policy.env_default()),
        )
    for ddl in INDEX_DDL:
        try:
            conn.execute(ddl)
        except sqlite3.IntegrityError:
            # 历史重复数据在极端场景下仍存在（例如并发写库的外部副本）：
            # 不阻断启动，运行期由 BEGIN IMMEDIATE + 应用层校验兜底。
            pass


def seed_if_empty() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        migrate(conn)
        n = conn.execute("SELECT COUNT(*) c FROM tills").fetchone()["c"]
        if n:
            conn.commit()
            return
        conn.execute("INSERT INTO settings(key, value) VALUES ('business_day', ?)", ("2026-11-16",))
        tills = [
            ("TILL-A", "一号收银"),
            ("TILL-B", "二号收银"),
            ("TILL-C", "三号收银"),
        ]
        for code, title in tills:
            conn.execute("INSERT INTO tills(till_code, title) VALUES (?,?)", (code, title))
        lines = [
            ("CL-0001", "TILL-A", "2026-11-16", "2026-11-16T09:12:00Z", 35.5, "现金", "早市"),
            ("CL-0002", "TILL-A", "2026-11-16", "2026-11-16T10:05:00Z", 128.0, "微信", None),
            ("CL-0003", "TILL-B", "2026-11-16", "2026-11-16T11:20:00Z", 66.0, "现金", None),
            ("CL-0004", "TILL-B", "2026-11-16", "2026-11-16T14:02:00Z", 210.0, "支付宝", None),
            ("CL-0005", "TILL-C", "2026-11-16", "2026-11-16T16:40:00Z", 49.9, "现金", None),
            ("CL-0006", "TILL-A", "2026-11-15", "2026-11-15T18:10:00Z", 88.0, "现金", "昨日"),
        ]
        for row in lines:
            conn.execute(
                '''INSERT INTO cash_lines(line_no, till_code, business_day, occurred_at, amount, pay_method, note, created_at)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (*row, row[3]),
            )
        conn.commit()
    finally:
        conn.close()
