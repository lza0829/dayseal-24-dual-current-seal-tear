from __future__ import annotations

from typing import Any


def _current_sealed_by_day(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """现行钉扎：每个营业日只认 id 最大的 sealed 行（最近一次成功封账）。

    并发双封 / 旧 bug 残留可能在同一天留下多行 sealed；汇总口径与
    ``close_repo.current_batch_ids`` 同源，同时至多一个现行。
    """
    best: dict[str, dict[str, Any]] = {}
    for r in rows:
        if str(r.get("status") or "") != "sealed":
            continue
        day = str(r.get("business_day") or "")
        rid = int(r.get("id") or 0)
        cur = best.get(day)
        if cur is None or rid > int(cur.get("id") or 0):
            best[day] = r
    return best


def summarize_batches(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """批次汇总：已封只数收敛后的现行批；superseded 是历史，不计入已封。"""
    current = _current_sealed_by_day(rows)
    superseded = [r for r in rows if str(r.get("status") or "") == "superseded"]
    pendingish = [r for r in rows if str(r.get("status") or "") in {"draft", "failed"}]
    return {
        "sealed_count": len(current),
        "superseded_count": len(superseded),
        "draft_count": len(pendingish),
        "ids": [r.get("id") for r in current.values()],
        "days": sorted(current.keys()),
    }


def overlay_dashboard(payload: dict[str, Any], batches: list[dict[str, Any]]) -> dict[str, Any]:
    out = dict(payload)
    summary = summarize_batches(batches)
    # 与主总览同源：close_batch_count = 现行 + superseded；superseded 不算已封，
    # 双封残留不重复计数。
    out["close_batch_count"] = summary["sealed_count"] + summary["superseded_count"]
    out["sealed_days"] = summary["days"]
    out["alignment_token"] = "|".join(summary["days"]) if summary["days"] else ""
    return out


def filter_lines_for_export(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        item = dict(r)
        amt = float(item.get("amount") or 0)
        if abs(amt) < 1e-12:
            continue
        item["export_amount"] = amt
        out.append(item)
    return out


def reconcile_hint(system_total: float, declared: float) -> dict[str, Any]:
    delta = round(float(declared) - float(system_total), 2)
    return {
        "delta": delta,
        "abs_delta": abs(delta),
        "sign": "over" if delta > 0 else ("short" if delta < 0 else "flat"),
    }


def gate_matrix(day_sealed: bool, pending: int, latch_open: bool) -> dict[str, bool]:
    """写闸矩阵：日锁生效时所有写入口一致关闭，与 _require_writable 同源。"""
    locked = bool(day_sealed) and not latch_open
    return {
        "allow_create": not locked,
        "allow_patch": not locked,
        "allow_import": not locked,
        "allow_reverse": not locked,
        "allow_pickup": not locked,
        "pending_ok": pending == 0,
    }


def stamp_note(note: str | None, token: str) -> str:
    base = str(note or "").strip()
    if token in base:
        return base
    return (base + "|" + token).strip("|")
