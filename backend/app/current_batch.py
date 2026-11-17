"""现行批判定的同源 facade。

「现行」全局只有一份口径（见 :mod:`app.repositories.closes`）：任一营业日
至多一个现行 sealed 批（id 最大者，即最近一次成功封账）；superseded 旧批
只是只读审计——既不构成现行，也不构成日锁，更不计入已封计数。
"""
from __future__ import annotations

from typing import Any

from app.repositories import closes as close_repo


def list_current(conn) -> list[dict[str, Any]]:
    """全部营业日的现行批（每日至多一行）；superseded 与双封残留行永不混入。"""
    return close_repo.list_batches(conn, current_only=True)


def is_day_sealed(conn, business_day: str) -> bool:
    """日锁只认现行 sealed 批；superseded 旧批不驱动拒写。"""
    return close_repo.is_sealed(conn, business_day)
