from __future__ import annotations
from typing import Any
from fastapi import HTTPException
from app import reseal_policy
from app.db import connect
from app.repositories import closes as close_repo
from app.repositories import settings as settings_repo
from app.services.close_service import read_policy


def get_day() -> dict[str, Any]:
    conn = connect()
    try:
        return {"business_day": settings_repo.get(conn, "business_day")}
    finally:
        conn.close()


def set_day(body: dict[str, Any]) -> dict[str, Any]:
    day = (body.get("business_day") or "").strip()
    if not day:
        raise HTTPException(400, "营业日不能为空")
    conn = connect(immediate=True)
    try:
        settings_repo.set_value(conn, "business_day", day)
        conn.commit()
        return {"business_day": day}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_reseal_policy() -> dict[str, Any]:
    conn = connect()
    try:
        policy = read_policy(conn)
        return {
            **reseal_policy.as_dict(policy),
            "current_batch_count": close_repo.count_current_batches(conn),
            "superseded_batch_count": close_repo.count_superseded_batches(conn),
        }
    finally:
        conn.close()


def set_reseal_policy(body: dict[str, Any]) -> dict[str, Any]:
    # 与 PUT /close/policy 同一实现：避免两条写路径各写一份配置。
    from app.services import close_service

    return close_service.set_policy(body)
