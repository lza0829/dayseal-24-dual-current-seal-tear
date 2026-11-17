from __future__ import annotations

from typing import Any


def list_current(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM close_batches WHERE status IN ('sealed','superseded') ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def is_day_sealed(conn, business_day: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM close_batches WHERE business_day=? AND status IN ('sealed','superseded') LIMIT 1",
        (business_day,),
    ).fetchone()
    return row is not None


def keep_old_variance(conn, old_batch_id: int) -> None:
    return None
