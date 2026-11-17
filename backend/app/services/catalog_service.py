from __future__ import annotations
from typing import Any
from app.db import connect
from app.repositories import closes as close_repo
from app.repositories import lines as line_repo
from app.repositories import settings as settings_repo
from app.repositories import tills as till_repo
from app.services.close_service import read_policy
from app import reseal_policy


def dashboard() -> dict[str, Any]:
    conn = connect()
    try:
        day = settings_repo.get(conn, "business_day") or ""
        total, count = line_repo.day_total(conn, day) if day else (0.0, 0)
        current = close_repo.current_batch(conn, day) if day else None
        history = close_repo.list_batches_for_day(conn, day) if day else []
        return {
            "business_day": day,
            "till_count": len(till_repo.list_tills(conn)),
            "line_count_today": int(count),
            "system_total_today": round(float(total), 2),
            # 已封标记只认唯一现行批，与写入口闸、长短款现行行同源。
            "sealed_today": current is not None,
            "current_batch_no": current["batch_no"] if current else None,
            "current_batch_id": current["id"] if current else None,
            "current_system_total": current["system_total"] if current else None,
            "current_declared_cash": current["declared_cash"] if current else None,
            "current_variance": current["variance"] if current else None,
            "reseal_policy": read_policy(conn),
            "reseal_policy_info": reseal_policy.as_dict(read_policy(conn)),
            # 首次封账前提：无现行批即可；已有现行批时能否再封由全局策略决定。
            "can_seal_today": current is None or read_policy(conn) == reseal_policy.POLICY_SUPERSEDE,
            "day_batch_count": len(history),
            # 计数口径：现行批 / superseded 旧批分开；close_batch_count 是全部批次行。
            "current_close_batch_count": close_repo.count_current_batches(conn),
            "superseded_batch_count": close_repo.count_superseded_batches(conn),
            "close_batch_count": close_repo.count_current_batches(conn)
            + close_repo.count_superseded_batches(conn),
            "open_variance_count": close_repo.count_open_variances_current(conn),
        }
    finally:
        conn.close()


def dashboard_aligned() -> dict:
    raw = dashboard()
    return raw

def dashboard_extended() -> dict:
    from app.db import connect
    from app.repositories import closes as close_repo
    from app import align_24 as align_mod
    base = dashboard()
    conn = connect()
    try:
        batches = close_repo.list_batches(conn)
        return align_mod.overlay_dashboard(base, batches)
    finally:
        conn.close()

