from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from fastapi import HTTPException
from app import reseal_policy
from app.db import connect
from app.repositories import closes as close_repo
from app.repositories import lines as line_repo
from app.repositories import settings as settings_repo
from app import align_24 as align_mod

_ZERO = 1e-9


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_declared(raw: Any) -> float:
    """实盘金额解析：缺失 / 非法一律 400，且在任何写入之前失败。"""
    if raw is None:
        raise HTTPException(400, "缺少实盘现金 declared_cash")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"实盘现金必须是数字：{raw!r}") from exc
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        raise HTTPException(400, f"实盘现金必须是有限数字：{raw!r}")
    return round(value, 2)


def read_policy(conn) -> str:
    """全局唯一重封策略：所有读写路径共用此入口，只认一份配置。

    规范键 ``reseal_policy`` 优先；若只存在历史别名键（同一策略的旧命名），
    归一后仍作为同一份配置读取，避免「两个键各说各话」。
    """
    for key in reseal_policy.LEGACY_SETTING_KEYS:
        raw = settings_repo.get(conn, key)
        if raw is not None and str(raw).strip():
            return reseal_policy.normalize(raw)
    return reseal_policy.env_default()


def _day(conn, business_day: str | None) -> str:
    day = (business_day or settings_repo.get(conn, "business_day") or "").strip()
    if not day:
        raise HTTPException(400, "未指定营业日")
    return day


def preview(business_day: str | None = None) -> dict[str, Any]:
    conn = connect()
    try:
        day = (business_day or settings_repo.get(conn, "business_day") or "").strip()
        total, count = line_repo.day_total(conn, day) if day else (0.0, 0.0)
        current = close_repo.current_batch(conn, day) if day else None
        policy = read_policy(conn)
        return {
            "business_day": day,
            "system_total": round(float(total), 2),
            "line_count": int(count),
            "sealed": current is not None,
            "current_batch": current,
            "reseal_policy": policy,
            "can_seal": current is None or policy == reseal_policy.POLICY_SUPERSEDE,
            "history": close_repo.list_batches_for_day(conn, day) if day else [],
            "lines": line_repo.list_lines(conn, day) if day else [],
        }
    finally:
        conn.close()


def seal(body: dict[str, Any]) -> dict[str, Any]:
    """封账唯一入口：现行批存在时由全局策略决定「拒绝」还是「覆盖重算」。

    整个判定 + 写入在同一 BEGIN IMMEDIATE 事务内完成，并发二次封账被串行化，
    第二个事务必然看到第一个事务提交的 sealed 批，因此不会出现双现行。
    """
    conn = connect(immediate=True)
    try:
        day = _day(conn, body.get("business_day"))
        policy = read_policy(conn)
        current = close_repo.current_batch(conn, day)

        if current is not None and policy == reseal_policy.POLICY_FORBID:
            # 禁止重封：不改写任何字段、不释放写锁，只读返回原批。
            # 请求体里若夹带策略字段也不生效——全局只贯彻一种策略。
            raise HTTPException(
                409,
                detail={
                    "code": "reseal_forbidden",
                    "message": f"营业日 {day} 已封账，禁止重封；以现行批 {current['batch_no']} 为准",
                    "business_day": day,
                    "policy": policy,
                    # 原批同时以两种常见形态暴露，便于只读回显 / 审计。
                    "current_batch": current,
                    "batch": current,
                    "batch_id": current["id"],
                    "batch_no": current["batch_no"],
                    "system_total": current["system_total"],
                    "declared_cash": current["declared_cash"],
                    "variance": current["variance"],
                    "line_count": current["line_count"],
                    "status": current["status"],
                },
            )

        declared = _parse_declared(body.get("declared_cash"))
        total, count = line_repo.day_total(conn, day)
        total = round(float(total), 2)
        variance = round(declared - total, 2)
        sealed_at = _now()
        batch_no = close_repo.next_batch_no(conn)

        batch_id = close_repo.create_batch(
            conn,
            batch_no=batch_no,
            business_day=day,
            # 覆盖重算时先落 draft，旧批降级后再置 sealed；首次封账同样走
            # draft -> sealed，保证全天只有唯一现行批的索引不被中途撞破。
            status=close_repo.DRAFT,
            system_total=total,
            declared_cash=declared,
            variance=variance,
            line_count=int(count),
            sealed_at=sealed_at,
            note=body.get("note"),
        )
        disposition = "pending" if abs(variance) > _ZERO else "balanced"
        close_repo.add_variance(
            conn,
            batch_id=batch_id,
            business_day=day,
            variance=variance,
            disposition=disposition,
            created_at=sealed_at,
        )

        superseded_batch_ids: list[int] = []
        if current is not None:
            # 覆盖策略：旧现行批显式降级为 superseded；合计/实盘/差额/笔数原样不动。
            # 与新建批次同事务，失败则一起回滚，不会出现「旧批已降级、新批未成」。
            reason = (body.get("supersede_reason") or "").strip() or None
            for old in close_repo.sealed_batches(conn, day):
                if old["id"] == batch_id:
                    continue
                close_repo.mark_superseded(
                    conn,
                    old["id"],
                    batch_id,
                    superseded_at=sealed_at,
                    reason=reason,
                )
                superseded_batch_ids.append(old["id"])

        close_repo.mark_sealed(conn, batch_id)
        conn.commit()
        return {
            "batch_id": batch_id,
            "batch_no": batch_no,
            "business_day": day,
            "system_total": total,
            "declared_cash": declared,
            "variance": variance,
            "line_count": int(count),
            "status": close_repo.SEALED,
            "disposition": disposition,
            "policy": policy,
            "superseded": bool(superseded_batch_ids),
            "superseded_batch_ids": superseded_batch_ids,
            # 现行权威 = 本次新批；旧批数字仅在 /batches 历史行里只读可见。
            "current_batch_id": batch_id,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def current(business_day: str | None = None) -> dict[str, Any] | None:
    """唯一现行批读取入口；无现行批返回 null，superseded 永不冒充现行。"""
    conn = connect()
    try:
        day = (business_day or settings_repo.get(conn, "business_day") or "").strip()
        return close_repo.current_batch(conn, day) if day else None
    finally:
        conn.close()


def list_batches(current_only: bool = False) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return close_repo.list_batches(conn, current_only=current_only)
    finally:
        conn.close()


def list_variances(current_only: bool = False) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return close_repo.list_variances(conn, current_only=current_only)
    finally:
        conn.close()


def policy_snapshot() -> dict[str, Any]:
    conn = connect()
    try:
        return reseal_policy.as_dict(read_policy(conn))
    finally:
        conn.close()


def get_policy() -> dict[str, Any]:
    return policy_snapshot()


def set_policy(body: dict[str, Any]) -> dict[str, Any]:
    """切换重封策略。

    历史批次数字永不因策略切换而改写：切换只影响「下一次重封」的裁决。
    """
    try:
        target = reseal_policy.parse(
            body.get("policy")
            or body.get("reseal_policy")
            or body.get("reclose_policy")
            or body.get("seal_rerun_policy")
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    conn = connect(immediate=True)
    try:
        previous = read_policy(conn)
        settings_repo.set_value(conn, reseal_policy.SETTING_KEY, target)
        conn.commit()
        return {
            **reseal_policy.as_dict(target),
            "previous": previous,
            "changed": previous != target,
            "sealed_batch_count": close_repo.count_current_batches(conn),
            "superseded_batch_count": close_repo.count_superseded_batches(conn),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _follow_superseded_chain(conn, batch_id: int | None) -> dict[str, Any] | None:
    """沿 superseded_by_batch_id 走到链尾；遇到断链 / 环返回 None。"""
    seen: set[int] = set()
    current_id = batch_id
    node: dict[str, Any] | None = None
    while current_id is not None:
        if current_id in seen:
            return None
        seen.add(current_id)
        node = close_repo.get_batch(conn, int(current_id))
        if node is None:
            return None
        if node["status"] != close_repo.SUPERSEDED:
            return node
        current_id = node["superseded_by_batch_id"]
    return node


def integrity() -> dict[str, Any]:
    """自检同源约束：双现行 / 旧批被静默改写 / 现行批缺长短款行。"""
    conn = connect()
    try:
        violations: list[dict[str, Any]] = []

        for row in conn.execute(
            '''SELECT business_day, COUNT(*) c FROM close_batches
               WHERE status='sealed' GROUP BY business_day HAVING COUNT(*) > 1'''
        ).fetchall():
            violations.append(
                {
                    "code": "multiple_current_batches",
                    "business_day": row["business_day"],
                    "detail": f"营业日存在 {row['c']} 个现行 sealed 批",
                }
            )

        for row in conn.execute(
            '''SELECT b.id, b.batch_no, b.business_day FROM close_batches b
               WHERE b.status='sealed' AND b.superseded_by_batch_id IS NOT NULL'''
        ).fetchall():
            violations.append(
                {
                    "code": "current_batch_marked_superseded",
                    "business_day": row["business_day"],
                    "batch_id": row["id"],
                    "detail": f"{row['batch_no']} 仍是 sealed 却已带 superseded 指针",
                }
            )

        for row in conn.execute(
            '''SELECT b.id, b.batch_no, b.business_day, b.superseded_by_batch_id
               FROM close_batches b WHERE b.status='superseded' '''
        ).fetchall():
            # superseded 指针是「被谁取代」的时间链：旧批指向紧随其后的一次重封，
            # 不是永远指向今天的最新批（审计指针一经写入不再改写）。
            # 不变量只是：沿链走必须在有限步内落到唯一现行 sealed 批。
            terminal = _follow_superseded_chain(conn, row["superseded_by_batch_id"])
            if terminal is None or terminal["status"] != close_repo.SEALED:
                violations.append(
                    {
                        "code": "superseded_without_current_successor",
                        "business_day": row["business_day"],
                        "batch_id": row["id"],
                        "detail": (
                            f"{row['batch_no']} 的取代链未终止于现行 sealed 批"
                            f"（superseded_by_batch_id={row['superseded_by_batch_id']}）"
                        ),
                    }
                )

        for row in conn.execute(
            '''SELECT b.id, b.batch_no, b.business_day FROM close_batches b
               WHERE b.status='sealed'
                 AND NOT EXISTS (SELECT 1 FROM variance_rows v WHERE v.batch_id=b.id)'''
        ).fetchall():
            violations.append(
                {
                    "code": "current_batch_without_variance",
                    "business_day": row["business_day"],
                    "batch_id": row["id"],
                    "detail": f"{row['batch_no']} 是现行批却没有长短款行",
                }
            )

        return {
            "ok": not violations,
            "policy": read_policy(conn),
            "violations": violations,
        }
    finally:
        conn.close()

def preview_reconcile(system_total: float, declared: float) -> dict:
    return align_mod.reconcile_hint(system_total, declared)

