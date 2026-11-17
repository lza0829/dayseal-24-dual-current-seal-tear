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
    """该营业日唯一的现行 sealed 批；superseded 旧批永远不返回。

    并发双封 / 旧 bug 残留可能让同一天存在多行 sealed：始终只钉 id 最大
    （最近一次成功封账）的那一行，与 :func:`current_batch_ids` 同源。
    """
    row = conn.execute(
        f"SELECT {_BATCH_COLUMNS} FROM close_batches "
        "WHERE business_day=? AND status=? ORDER BY id DESC LIMIT 1",
        (business_day, SEALED),
    ).fetchone()
    return dict(row) if row else None


def current_batch_ids(conn: sqlite3.Connection) -> set[int]:
    """收敛后的现行批 id 集合：每个营业日只钉 id 最大的 sealed 行。

    这是「现行」的唯一判定口径：批次列表的 is_current、current_only 过滤、
    已封计数、长短款现行行全部从这里取，保证同时至多一个现行，且与
    :func:`current_batch` 单批读取同源。双封残留里非最大的 sealed 行只是
    待愈合的异常（integrity 会报、重封会降级），绝不冒充现行。
    """
    rows = conn.execute(
        "SELECT MAX(id) id FROM close_batches WHERE status=? GROUP BY business_day",
        (SEALED,),
    ).fetchall()
    return {int(r["id"]) for r in rows}


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


def _with_current_flag(batch: dict[str, Any], current_ids: set[int]) -> dict[str, Any]:
    # 现行标记只认收敛集合：status='sealed' 的残留行（非当日最大 id）不算现行。
    batch["is_current"] = int(batch["id"]) in current_ids
    return batch


def list_batches(
    conn: sqlite3.Connection, current_only: bool = False
) -> list[dict[str, Any]]:
    """批次列表：默认含历史（superseded 只读审计），每行带 is_current。

    ``current_only`` 只返回收敛后的现行批——任一营业日至多一行，并发双封
    残留也不会带出第二行。唯一索引 + BEGIN IMMEDIATE 保证运行期不再产生
    残留，这里的收敛口径是残留数据的同源读法，不是把旧批行藏起来。
    """
    current_ids = current_batch_ids(conn)
    sql = f"SELECT {_BATCH_COLUMNS} FROM close_batches"
    args: tuple[Any, ...] = ()
    if current_only:
        if not current_ids:
            return []
        sql += f" WHERE id IN ({','.join('?' for _ in current_ids)})"
        args = tuple(sorted(current_ids))
    sql += " ORDER BY id DESC"
    return [
        _with_current_flag(dict(r), current_ids)
        for r in conn.execute(sql, args).fetchall()
    ]


def list_batches_for_day(conn: sqlite3.Connection, business_day: str) -> list[dict[str, Any]]:
    current_ids = current_batch_ids(conn)
    rows = conn.execute(
        f"SELECT {_BATCH_COLUMNS} FROM close_batches WHERE business_day=? ORDER BY id DESC",
        (business_day,),
    ).fetchall()
    return [_with_current_flag(dict(r), current_ids) for r in rows]


#: 收敛后的现行批 id 子查询（与 current_batch_ids 同一口径），供 SQL 内联使用。
_CURRENT_IDS_SQL = (
    "SELECT MAX(id) FROM close_batches WHERE status=? GROUP BY business_day"
)


def list_variances(
    conn: sqlite3.Connection, current_only: bool = False
) -> list[dict[str, Any]]:
    """长短款行：只有收敛后现行批的行是现行行；其余（含双封残留批的行）都是历史。

    现行判定挂在「批次是否现行」上，与批次列表、总览、写闸同源——
    重封后旧批的差额行绝不再以现行身份出现。
    """
    sql = f'''SELECT v.*, b.batch_no, b.status AS batch_status,
                    CASE WHEN v.batch_id IN ({_CURRENT_IDS_SQL}) THEN 1 ELSE 0 END AS is_current
             FROM variance_rows v
             LEFT JOIN close_batches b ON b.id = v.batch_id'''
    args: list[Any] = [SEALED]
    if current_only:
        sql += f" WHERE v.batch_id IN ({_CURRENT_IDS_SQL})"
        args.append(SEALED)
    sql += " ORDER BY v.id DESC"
    out = []
    for r in conn.execute(sql, tuple(args)).fetchall():
        item = dict(r)
        item["is_current"] = bool(item["is_current"])
        out.append(item)
    return out


def count_current_batches(conn: sqlite3.Connection) -> int:
    """现行批计数：按收敛口径，每个营业日至多计 1。"""
    return int(
        conn.execute(
            f"SELECT COUNT(*) c FROM ({_CURRENT_IDS_SQL})",
            (SEALED,),
        ).fetchone()["c"]
    )


def count_superseded_batches(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) c FROM close_batches WHERE status=?", (SUPERSEDED,)
        ).fetchone()["c"]
    )


def count_open_variances_current(conn: sqlite3.Connection) -> int:
    """只统计收敛后现行批的未处置长短款；superseded 旧批与双封残留批的差额都属于历史。"""
    return int(
        conn.execute(
            f'''SELECT COUNT(*) c FROM variance_rows v
               WHERE v.disposition='pending' AND v.batch_id IN ({_CURRENT_IDS_SQL})''',
            (SEALED,),
        ).fetchone()["c"]
    )
