from __future__ import annotations
import sqlite3
from typing import Any

#: 现行批（唯一权威）：营业日写锁、总览已封标记、短长款现行行的唯一来源。
SEALED = "sealed"
#: 被后一次封账覆盖的旧批：数字原样保留，只读审计，不再当现行。
SUPERSEDED = "superseded"
#: 覆盖重算的中间态：新批先以 draft 落库，旧批降级后再置 sealed，
#: 同一事务内完成，外部永远看不到 draft（唯一索引也只约束 sealed）。
DRAFT = "draft"

_BATCH_COLUMNS = (
    "id, batch_no, business_day, system_total, declared_cash, variance, "
    "line_count, status, sealed_at, note, superseded_by_batch_id, "
    "superseded_at, superseded_reason"
)


def current_batch(conn: sqlite3.Connection, business_day: str) -> dict[str, Any] | None:
    """该营业日唯一的现行 sealed 批；superseded 旧批永远不返回。"""
    row = conn.execute(
        f"SELECT {_BATCH_COLUMNS} FROM close_batches "
        "WHERE business_day=? AND status=? ORDER BY id DESC LIMIT 1",
        (business_day, SEALED),
    ).fetchone()
    return dict(row) if row else None


def is_sealed(conn: sqlite3.Connection, business_day: str) -> bool:
    """日锁定状态：唯一来源是现行 sealed 批是否存在。"""
    return current_batch(conn, business_day) is not None


def sealed_batches(conn: sqlite3.Connection, business_day: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_BATCH_COLUMNS} FROM close_batches "
        "WHERE business_day=? AND status=? ORDER BY id DESC",
        (business_day, SEALED),
    ).fetchall()
    return [dict(r) for r in rows]


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT {_BATCH_COLUMNS} FROM close_batches WHERE id=?", (batch_id,)
    ).fetchone()
    return dict(row) if row else None


def next_batch_no(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(batch_no, 4) AS INTEGER)) n "
        "FROM close_batches WHERE batch_no LIKE 'CB-%'"
    ).fetchone()
    n = int(row["n"] or 0) + 1
    return f"CB-{n:04d}"


def create_batch(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        '''INSERT INTO close_batches(
             batch_no, business_day, system_total, declared_cash, variance,
             line_count, status, sealed_at, note
           ) VALUES (?,?,?,?,?,?,?,?,?)''',
        (
            fields["batch_no"],
            fields["business_day"],
            fields["system_total"],
            fields["declared_cash"],
            fields["variance"],
            fields["line_count"],
            fields["status"],
            fields["sealed_at"],
            fields.get("note"),
        ),
    )
    return int(cur.lastrowid)


def mark_sealed(conn: sqlite3.Connection, batch_id: int) -> None:
    """draft -> sealed；唯一索引在此刻兜底「一天只有一个现行批」。"""
    conn.execute(
        "UPDATE close_batches SET status=? WHERE id=? AND status=?",
        (SEALED, batch_id, DRAFT),
    )


def mark_superseded(
    conn: sqlite3.Connection,
    batch_id: int,
    new_batch_id: int,
    *,
    superseded_at: str,
    reason: str | None = None,
) -> None:
    """把旧现行批显式降级为 superseded，指向取代它的新批。

    合计、实盘、差额、笔数、sealed_at 一律不改写——旧批数字只能只读审计。
    """
    conn.execute(
        '''UPDATE close_batches
           SET status=?, superseded_by_batch_id=?, superseded_at=?, superseded_reason=?
           WHERE id=? AND status=?''',
        (SUPERSEDED, new_batch_id, superseded_at, reason, batch_id, SEALED),
    )


def add_variance(conn: sqlite3.Connection, **fields: Any) -> None:
    conn.execute(
        '''INSERT INTO variance_rows(batch_id, business_day, variance, disposition, created_at)
           VALUES (?,?,?,?,?)''',
        (
            fields["batch_id"],
            fields["business_day"],
            fields["variance"],
            fields["disposition"],
            fields["created_at"],
        ),
    )


def variance_for_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM variance_rows WHERE batch_id=? ORDER BY id DESC LIMIT 1",
        (batch_id,),
    ).fetchone()
    return dict(row) if row else None


def _with_current_flag(batch: dict[str, Any]) -> dict[str, Any]:
    batch["is_current"] = batch.get("status") == SEALED
    return batch


def list_batches(
    conn: sqlite3.Connection, current_only: bool = False
) -> list[dict[str, Any]]:
    """批次列表：默认含历史（superseded 只读审计），每行带 is_current。

    ``current_only`` 只返回现行 sealed 批；任一营业日至多一行的约束由
    唯一索引 + BEGIN IMMEDIATE 保证。
    """
    sql = f"SELECT {_BATCH_COLUMNS} FROM close_batches"
    args: tuple[Any, ...] = ()
    if current_only:
        sql += " WHERE status=?"
        args = (SEALED,)
    sql += " ORDER BY id DESC"
    return [_with_current_flag(dict(r)) for r in conn.execute(sql, args).fetchall()]


def list_batches_for_day(conn: sqlite3.Connection, business_day: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_BATCH_COLUMNS} FROM close_batches WHERE business_day=? ORDER BY id DESC",
        (business_day,),
    ).fetchall()
    return [_with_current_flag(dict(r)) for r in rows]


def list_variances(
    conn: sqlite3.Connection, current_only: bool = False
) -> list[dict[str, Any]]:
    """长短款行：只有现行 sealed 批的行是现行行；旧批行保留只读审计。"""
    sql = '''SELECT v.*, b.batch_no, b.status AS batch_status,
                    CASE WHEN b.status=? THEN 1 ELSE 0 END AS is_current
             FROM variance_rows v
             LEFT JOIN close_batches b ON b.id = v.batch_id'''
    args: list[Any] = [SEALED]
    if current_only:
        sql += " WHERE b.status=?"
        args.append(SEALED)
    sql += " ORDER BY v.id DESC"
    out = []
    for r in conn.execute(sql, tuple(args)).fetchall():
        item = dict(r)
        item["is_current"] = bool(item["is_current"])
        out.append(item)
    return out


def count_current_batches(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) c FROM close_batches WHERE status=?", (SEALED,)
        ).fetchone()["c"]
    )


def count_superseded_batches(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) c FROM close_batches WHERE status=?", (SUPERSEDED,)
        ).fetchone()["c"]
    )


def count_open_variances_current(conn: sqlite3.Connection) -> int:
    """只统计现行批的未处置长短款；superseded 旧批的差额属于历史。"""
    return int(
        conn.execute(
            '''SELECT COUNT(*) c FROM variance_rows v
               JOIN close_batches b ON b.id = v.batch_id
               WHERE b.status=? AND v.disposition='pending' ''',
            (SEALED,),
        ).fetchone()["c"]
    )
