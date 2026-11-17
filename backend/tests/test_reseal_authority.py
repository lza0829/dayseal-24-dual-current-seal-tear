"""重封权威（reseal authority）回归测试。

覆盖：全局唯一策略、禁止重封只读、supersede 覆盖同成同败、并发二次封无
双现行、四处只认一个现行批（总览/批次/长短款/写入口闸）。
"""
from __future__ import annotations

import importlib
import sys
import threading

import pytest
from fastapi.testclient import TestClient

SEED_DAY = "2026-11-16"
SEED_TOTAL = 489.4  # 5 笔
YESTERDAY = "2026-11-15"
YESTERDAY_TOTAL = 88.0


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYSEAL_DATA_DIR", str(tmp_path))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    main = importlib.import_module("app.main")
    import app.config as config

    assert config.DB_PATH == tmp_path / "dayseal.db"
    with TestClient(main.app) as c:
        yield c


def _set_policy(client, policy):
    return client.put("/api/settings/reseal-policy", json={"policy": policy})


def _seal(client, declared, day=SEED_DAY, **extra):
    body = {"business_day": day, "declared_cash": declared, **extra}
    return client.post("/api/close/seal", json=body)


def _line(client, day, amount=10.0):
    return client.post(
        "/api/lines",
        json={"till_code": "TILL-A", "amount": amount, "pay_method": "现金", "business_day": day},
    )


def _batches(client, current_only=False):
    q = "?current_only=true" if current_only else ""
    return client.get("/api/close/batches" + q).json()


def _variances(client, current_only=False):
    q = "?current_only=true" if current_only else ""
    return client.get("/api/close/variances" + q).json()


def _preview(client, day=SEED_DAY):
    return client.get(f"/api/close/preview?business_day={day}").json()


# ------------------------------------------------------------------ 策略唯一性

def test_default_policy_is_forbid_and_global(client):
    p = client.get("/api/close/policy").json()
    assert p["policy"] == "forbid"
    assert p["forbid_reseal"] is True
    assert p["supersede_enabled"] is False
    assert {o["value"] for o in p["options"]} == {"forbid", "supersede"}
    # 总览与设置同源
    assert client.get("/api/dashboard").json()["reseal_policy"] == "forbid"
    assert client.get("/api/settings/reseal-policy").json()["policy"] == "forbid"


def test_policy_rejects_unknown_value(client):
    assert _set_policy(client, "overwrite-everything").status_code == 400
    assert client.get("/api/close/policy").json()["policy"] == "forbid"


# ------------------------------------------------------------------ 禁止重封

def test_forbid_policy_rejects_second_seal_and_keeps_original(client):
    first = _seal(client, SEED_TOTAL)
    assert first.status_code == 200, first.text
    original = first.json()

    before = _batches(client)
    second = _seal(client, 111.0)
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["code"] == "reseal_forbidden"
    assert detail["current_batch"]["batch_no"] == original["batch_no"]

    # 原批逐字段不变，且没有新增批次 / 长短款行
    after = _batches(client)
    assert after == before
    assert len(after) == 1
    assert after[0]["status"] == "sealed"
    assert after[0]["declared_cash"] == SEED_TOTAL
    assert after[0]["system_total"] == SEED_TOTAL
    assert len(_variances(client)) == 1

    # 写锁保持、总览仍已封
    assert _line(client, SEED_DAY).status_code == 409
    dash = client.get("/api/dashboard").json()
    assert dash["sealed_today"] is True
    assert dash["current_batch_no"] == original["batch_no"]


def test_forbid_policy_preview_blocks_seal_action(client):
    _seal(client, SEED_TOTAL)
    p = _preview(client)
    assert p["sealed"] is True
    assert p["can_seal"] is False
    assert p["current_batch"]["batch_no"] == _batches(client)[0]["batch_no"]


# ------------------------------------------------------------------ supersede 覆盖

def test_supersede_recomputes_and_links_batches(client):
    assert _set_policy(client, "supersede").status_code == 200
    first = _seal(client, SEED_TOTAL).json()
    first_total = first["system_total"]
    before = _batches(client)[0]

    # 解封前提：supersede 策略允许在仍锁定时覆盖，先补一笔真实流水需要先解封；
    # 这里不改流水，直接以不同实盘重封，验证合计来自现行流水、长短款按新批重算。
    second = _seal(client, SEED_TOTAL - 5.0, supersede_reason="重新点数")
    assert second.status_code == 200, second.text
    new = second.json()
    assert new["superseded"] is True
    assert new["superseded_batch_ids"] == [first["batch_id"]]
    assert new["batch_id"] != first["batch_id"]
    assert new["system_total"] == first_total
    assert new["variance"] == -5.0

    batches = _batches(client)
    assert len(batches) == 2
    old = next(b for b in batches if b["id"] == first["batch_id"])
    cur = next(b for b in batches if b["id"] == new["batch_id"])
    assert old["status"] == "superseded"
    assert old["superseded_by_batch_id"] == new["batch_id"]
    assert old["superseded_reason"] == "重新点数"
    # 旧批数字原样保留（只读审计）：除 status/superseded_* 外逐字段与封账当时一致
    for key in ("batch_no", "business_day", "system_total", "declared_cash",
                "variance", "line_count", "sealed_at", "note"):
        assert old[key] == before[key], key
    assert cur["status"] == "sealed"
    # 现行批自己的 superseded_* 必须为空（不得静默给现行批打旧标记）
    for key in ("superseded_by_batch_id", "superseded_at", "superseded_reason"):
        assert cur[key] is None, key

    # 只有一个现行批
    current = _batches(client, current_only=True)
    assert [b["id"] for b in current] == [new["batch_id"]]

    # 长短款：旧行非现行，新行现行且金额为新差额的处置
    rows = _variances(client)
    assert len(rows) == 2
    old_row = next(r for r in rows if r["batch_id"] == first["batch_id"])
    new_row = next(r for r in rows if r["batch_id"] == new["batch_id"])
    assert old_row["is_current"] is False
    assert new_row["is_current"] is True
    assert new_row["variance"] == -5.0
    assert [r["batch_id"] for r in _variances(client, current_only=True)] == [new["batch_id"]]

    # 总览/预览/写闸都认新批
    dash = client.get("/api/dashboard").json()
    assert dash["current_batch_no"] == new["batch_no"]
    assert dash["current_close_batch_count"] == 1
    assert dash["superseded_batch_count"] == 1
    assert dash["close_batch_count"] == 2
    # 总览的合计/长短款必须来自现行批，不能是「总览已封、长短款另一套」
    assert dash["current_system_total"] == new["system_total"]
    assert dash["current_declared_cash"] == new["declared_cash"]
    assert dash["current_variance"] == new["variance"]
    assert dash["current_variance"] != old["variance"]
    assert dash["open_variance_count"] == 1  # 只数现行批的未处置差额
    assert _preview(client)["current_batch"]["id"] == new["batch_id"]
    assert _line(client, SEED_DAY).status_code == 409

    # 独立现行读数端点与批次/长短款过滤同源
    assert client.get("/api/close/current").json()["id"] == new["batch_id"]
    assert [b["id"] for b in client.get("/api/close/batches/current").json()] == [new["batch_id"]]
    assert [r["batch_id"] for r in client.get("/api/close/variances/current").json()] == [new["batch_id"]]

    # 自检无违规
    integrity = client.get("/api/close/integrity").json()
    assert integrity["ok"] is True, integrity


def test_supersede_uses_current_lines_after_unseal_workflow_is_not_implied(client):
    """覆盖重算只读现行流水：锁定期内流水集合不变，合计不会漂移。"""
    _set_policy(client, "supersede")
    first = _seal(client, SEED_TOTAL).json()
    assert _line(client, SEED_DAY).status_code == 409  # 仍锁定，不能补流水
    second = _seal(client, SEED_TOTAL + 3.0).json()
    assert second["system_total"] == first["system_total"]
    assert second["line_count"] == first["line_count"]


def test_supersede_chain_keeps_single_current(client):
    _set_policy(client, "supersede")
    ids = [_seal(client, SEED_TOTAL).json()["batch_id"]]
    ids.append(_seal(client, SEED_TOTAL + 1).json()["batch_id"])
    ids.append(_seal(client, SEED_TOTAL + 2).json()["batch_id"])

    current = _batches(client, current_only=True)
    assert len(current) == 1
    assert current[0]["id"] == ids[-1]

    all_batches = _batches(client)
    assert len(all_batches) == 3
    # superseded 指针是「被谁取代」的时间链：每次覆盖只改当时的那一个现行批。
    assert next(b for b in all_batches if b["id"] == ids[0])["superseded_by_batch_id"] == ids[1]
    assert next(b for b in all_batches if b["id"] == ids[1])["superseded_by_batch_id"] == ids[2]
    for b in all_batches:
        if b["id"] == ids[-1]:
            assert b["status"] == "sealed"
            assert b["superseded_by_batch_id"] is None
        else:
            assert b["status"] == "superseded"

    assert client.get("/api/close/integrity").json()["ok"] is True


def test_switch_policy_after_seal_does_not_rewrite_numbers(client):
    first = _seal(client, SEED_TOTAL).json()
    assert _set_policy(client, "supersede").status_code == 200
    b = _batches(client)[0]
    assert b["id"] == first["batch_id"]
    assert b["status"] == "sealed"
    assert b["declared_cash"] == SEED_TOTAL
    # 再切回禁止：已有 sealed 批不受影响
    assert _set_policy(client, "forbid").status_code == 200
    assert _seal(client, 1.0).status_code == 409
    assert _batches(client)[0]["declared_cash"] == SEED_TOTAL


def test_malformed_declared_cash_rejected_without_trace(client):
    _set_policy(client, "supersede")
    assert _seal(client, SEED_TOTAL).status_code == 200
    before = _batches(client)
    bad = _seal(client, "not-a-number")
    assert bad.status_code == 400
    missing = client.post("/api/close/seal", json={"business_day": SEED_DAY})
    assert missing.status_code == 400
    # 新旧批都未被改动，也没有多出 draft 行
    assert _batches(client) == before
    assert client.get("/api/close/integrity").json()["ok"] is True


# ------------------------------------------------------------------ 并发

def test_concurrent_first_seal_yields_single_current(tmp_path, monkeypatch):
    """两路并发「首次封账」：只能一成一败，绝不出现双份 sealed 现行。"""
    monkeypatch.setenv("DAYSEAL_DATA_DIR", str(tmp_path))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    importlib.import_module("app.main")
    from app.services import close_service as svc

    barrier = threading.Barrier(2)
    results: list[object] = []

    def worker(declared):
        try:
            barrier.wait(timeout=10)
            results.append(svc.seal({"business_day": SEED_DAY, "declared_cash": declared}))
        except Exception as exc:  # noqa: BLE001
            results.append(exc)

    threads = [threading.Thread(target=worker, args=(d,)) for d in (SEED_TOTAL, SEED_TOTAL + 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    ok = [r for r in results if isinstance(r, dict)]
    assert len(ok) == 1
    assert len([r for r in results if not isinstance(r, dict)]) == 1

    batches = svc.list_batches()
    assert len(batches) == 1
    assert batches[0]["status"] == "sealed"
    assert batches[0]["id"] == ok[0]["batch_id"]


def test_concurrent_second_seal_never_yields_two_current_batches(tmp_path, monkeypatch):
    """两路并发封账：结果只能是「一成一败」或「一成一覆盖」，绝不能双现行。"""
    monkeypatch.setenv("DAYSEAL_DATA_DIR", str(tmp_path))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    importlib.import_module("app.main")
    from app.services import close_service as svc

    # 先成功封一次，建立现行批
    first = svc.seal({"business_day": SEED_DAY, "declared_cash": SEED_TOTAL})
    assert first["status"] == "sealed"

    results: list[object] = []
    barrier = threading.Barrier(2)

    def worker(declared):
        try:
            barrier.wait(timeout=10)
            results.append(svc.seal({"business_day": SEED_DAY, "declared_cash": declared}))
        except Exception as exc:  # noqa: BLE001
            results.append(exc)

    threads = [threading.Thread(target=worker, args=(d,)) for d in (SEED_TOTAL + 1, SEED_TOTAL + 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    ok = [r for r in results if isinstance(r, dict)]
    failed = [r for r in results if not isinstance(r, dict)]
    # forbid 默认策略下并发二次封：两路都应被拒，现行批仍是首封
    assert not ok
    assert len(failed) == 2

    from app.db import connect

    conn = connect()
    try:
        rows = conn.execute(
            "SELECT business_day, COUNT(*) c FROM close_batches "
            "WHERE status='sealed' GROUP BY business_day"
        ).fetchall()
    finally:
        conn.close()
    assert all(r["c"] == 1 for r in rows)


def test_concurrent_supersede_keeps_one_current(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYSEAL_DATA_DIR", str(tmp_path))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    importlib.import_module("app.main")
    from app.db import connect
    from app.repositories import settings as settings_repo
    from app.services import close_service as svc

    svc.seal({"business_day": SEED_DAY, "declared_cash": SEED_TOTAL})
    conn = connect(immediate=True)
    try:
        settings_repo.set_value(conn, "reseal_policy", "supersede")
        conn.commit()
    finally:
        conn.close()

    barrier = threading.Barrier(4)
    errors: list[Exception] = []

    def worker(declared):
        try:
            barrier.wait(timeout=10)
            svc.seal({"business_day": SEED_DAY, "declared_cash": declared})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(SEED_TOTAL + i,)) for i in range(1, 5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    conn = connect()
    try:
        sealed = conn.execute(
            "SELECT COUNT(*) c FROM close_batches WHERE business_day=? AND status='sealed'",
            (SEED_DAY,),
        ).fetchone()["c"]
        total = conn.execute("SELECT COUNT(*) c FROM close_batches").fetchone()["c"]
        superseded = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM close_batches WHERE status='superseded'"
            ).fetchall()
        ]
        by_id = {
            r["id"]: dict(r)
            for r in conn.execute("SELECT * FROM close_batches").fetchall()
        }
    finally:
        conn.close()
    assert sealed == 1
    assert total == 5  # 首封 + 4 次覆盖，全部留痕
    # 每条 superseded 链都必须终止于唯一现行批，且没有环
    current_id = next(i for i, b in by_id.items() if b["status"] == "sealed")
    for b in superseded:
        seen = set()
        nid = b["superseded_by_batch_id"]
        while nid is not None and by_id[nid]["status"] == "superseded":
            assert nid not in seen
            seen.add(nid)
            nid = by_id[nid]["superseded_by_batch_id"]
        assert nid == current_id


def test_failed_supersede_rolls_back_both_sides(tmp_path, monkeypatch):
    """覆盖须同成同败：新批写入失败时旧批不得被单独降级。"""
    monkeypatch.setenv("DAYSEAL_DATA_DIR", str(tmp_path))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    importlib.import_module("app.main")
    from app.db import connect
    from app.services import close_service as svc

    first = svc.seal({"business_day": SEED_DAY, "declared_cash": SEED_TOTAL})
    conn = connect(immediate=True)
    try:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('reseal_policy','supersede') "
            "ON CONFLICT(key) DO UPDATE SET value='supersede'"
        )
        conn.commit()
    finally:
        conn.close()

    # declared_cash 无法转成 float → 在任何写入之前就失败
    with pytest.raises(Exception):
        svc.seal({"business_day": SEED_DAY, "declared_cash": "not-a-number"})

    batches = svc.list_batches()
    assert len(batches) == 1
    assert batches[0]["id"] == first["batch_id"]
    assert batches[0]["status"] == "sealed"
    assert batches[0]["superseded_by_batch_id"] is None
    assert len(svc.list_variances()) == 1


def test_supersede_failure_after_demotion_rolls_back_atomically(tmp_path, monkeypatch):
    """同成同败：旧批已降级、新批已落库后若最后一步失败，整个覆盖必须回滚。"""
    monkeypatch.setenv("DAYSEAL_DATA_DIR", str(tmp_path))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    importlib.import_module("app.main")
    from app.db import connect
    from app.repositories import closes as close_repo
    from app.services import close_service as svc

    first = svc.seal({"business_day": SEED_DAY, "declared_cash": SEED_TOTAL})
    conn = connect(immediate=True)
    try:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('reseal_policy','supersede') "
            "ON CONFLICT(key) DO UPDATE SET value='supersede'"
        )
        conn.commit()
    finally:
        conn.close()

    def boom(*args, **kwargs):
        raise RuntimeError("injected failure promoting new batch")

    monkeypatch.setattr(close_repo, "mark_sealed", boom)
    with pytest.raises(RuntimeError):
        svc.seal({"business_day": SEED_DAY, "declared_cash": SEED_TOTAL - 5})
    monkeypatch.undo()

    batches = svc.list_batches()
    # 没有残留 draft / 半成品；旧批仍是唯一现行，且未被降级
    assert len(batches) == 1, batches
    assert batches[0]["id"] == first["batch_id"]
    assert batches[0]["status"] == "sealed"
    assert batches[0]["declared_cash"] == SEED_TOTAL
    assert batches[0]["superseded_by_batch_id"] is None
    assert len(svc.list_variances()) == 1
    assert svc.integrity()["ok"] is True


# ------------------------------------------------------------------ 历史脏数据迁移

def test_legacy_duplicate_sealed_is_collapsed_on_migrate(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYSEAL_DATA_DIR", str(tmp_path))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    importlib.import_module("app.main")

    from app.db import connect

    conn = connect(immediate=True)
    try:
        # 模拟旧 bug 遗留：同一天两个 sealed 批（绕过唯一索引需要先删索引）
        conn.execute("DROP INDEX IF EXISTS ux_close_batches_current_day")
        conn.execute(
            '''INSERT INTO close_batches(batch_no, business_day, system_total, declared_cash,
               variance, line_count, status, sealed_at)
               VALUES ('CB-9001', ?, 1.0, 1.0, 0.0, 1, 'sealed', '2026-11-16T01:00:00Z')''',
            (YESTERDAY,),
        )
        conn.execute(
            '''INSERT INTO close_batches(batch_no, business_day, system_total, declared_cash,
               variance, line_count, status, sealed_at)
               VALUES ('CB-9002', ?, 2.0, 2.0, 0.0, 1, 'sealed', '2026-11-16T02:00:00Z')''',
            (YESTERDAY,),
        )
        conn.commit()
    finally:
        conn.close()

    from app.seed import migrate

    conn = connect(immediate=True)
    try:
        migrate(conn)
        conn.commit()
        sealed = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM close_batches WHERE business_day=? AND status='sealed'",
                (YESTERDAY,),
            ).fetchall()
        ]
        superseded = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM close_batches WHERE business_day=? AND status='superseded'",
                (YESTERDAY,),
            ).fetchall()
        ]
    finally:
        conn.close()

    assert len(sealed) == 1
    assert sealed[0]["batch_no"] == "CB-9002"  # 最近封账者成为唯一现行
    assert len(superseded) == 1
    assert superseded[0]["batch_no"] == "CB-9001"
    assert superseded[0]["superseded_by_batch_id"] == sealed[0]["id"]
    # 数字未被改写
    assert superseded[0]["system_total"] == 1.0
    assert superseded[0]["declared_cash"] == 1.0


# ------------------------------------------------------------------ 现行钉扎唯一性互证
# 「双 sealed」「superseded 仍拒写」「长短款挂旧批」三条路径交叉验证：
# 同时至多一个 sealed 现行；总览已封、写闸、长短款现行必须同源。

def _inject_second_sealed(day=SEED_DAY, batch_no="CB-9001", declared=500.0):
    """模拟并发双封 / 旧 bug 残留：同日第二行 sealed（绕过唯一索引直写）。"""
    from app.db import connect

    conn = connect(immediate=True)
    try:
        conn.execute("DROP INDEX IF EXISTS ux_close_batches_current_day")
        variance = round(declared - SEED_TOTAL, 2)
        conn.execute(
            '''INSERT INTO close_batches(batch_no, business_day, system_total, declared_cash,
               variance, line_count, status, sealed_at)
               VALUES (?, ?, ?, ?, ?, 5, 'sealed', '2026-11-16T23:59:59Z')''',
            (batch_no, day, SEED_TOTAL, declared, variance),
        )
        bid = conn.execute(
            "SELECT id FROM close_batches WHERE batch_no=?", (batch_no,)
        ).fetchone()["id"]
        conn.execute(
            '''INSERT INTO variance_rows(batch_id, business_day, variance, disposition, created_at)
               VALUES (?,?,?,?,?)''',
            (bid, day, variance, "pending", "2026-11-16T23:59:59Z"),
        )
        conn.commit()
        return bid
    finally:
        conn.close()


def test_double_sealed_residue_converges_to_single_current(client):
    """双 sealed 残留：所有读路径收敛到「最近成功封账批」这一个现行。"""
    first = _seal(client, 480.0).json()  # 长短款 -9.4，pending
    residue_id = _inject_second_sealed()  # id 更大 = 更晚封账 = 收敛后的现行

    # 批次现行标记：current_only 恰好一行，且是最近封账批
    current = _batches(client, current_only=True)
    assert [b["batch_no"] for b in current] == ["CB-9001"]
    allb = _batches(client)
    assert [b["batch_no"] for b in allb if b["is_current"]] == ["CB-9001"]

    # 总览已封计数与现行读数同源：计数为 1，数字全部来自最近封账批
    dash = client.get("/api/dashboard").json()
    assert dash["sealed_today"] is True
    assert dash["current_batch_no"] == "CB-9001"
    assert dash["current_close_batch_count"] == 1
    assert dash["current_system_total"] == SEED_TOTAL
    assert dash["current_declared_cash"] == 500.0
    assert dash["current_variance"] == 10.6
    # 未处置长短款只数现行批（残留批的 pending 不重复计数）
    assert dash["open_variance_count"] == 1

    # 长短款现行只挂最近封账批；残留批的差额行不再冒充现行
    cur_var = _variances(client, current_only=True)
    assert [v["batch_no"] for v in cur_var] == ["CB-9001"]
    flags = {v["batch_no"]: v["is_current"] for v in _variances(client)}
    assert flags == {"CB-9001": True, "CB-0001": False}

    # 扩展总览与主总览同源，不把残留双计
    ext = client.get("/api/dashboard/extended").json()
    assert ext["close_batch_count"] == dash["close_batch_count"]
    assert ext["sealed_days"] == [SEED_DAY]

    # 写闸 / 日锁同源：现行批存在即锁
    assert _line(client, SEED_DAY).status_code == 409
    assert client.get("/api/close/current").json()["batch_no"] == "CB-9001"

    # 自检仍能发现残留（检测与读路径收敛是两回事）
    integ = client.get("/api/close/integrity").json()
    assert integ["ok"] is False
    assert any(v["code"] == "multiple_current_batches" for v in integ["violations"])

    # 重封（supersede）把残留收口到唯一现行：两行旧 sealed 一起降级
    assert _set_policy(client, "supersede").status_code == 200
    reseal = _seal(client, SEED_TOTAL)
    assert reseal.status_code == 200, reseal.text
    assert set(reseal.json()["superseded_batch_ids"]) == {first["batch_id"], residue_id}
    assert [b["batch_no"] for b in _batches(client, current_only=True)] == [
        reseal.json()["batch_no"]
    ]
    assert client.get("/api/close/integrity").json()["ok"] is True


def test_superseded_batch_drives_no_lock_and_no_sealed_count(client):
    """superseded 旧批：不构成日锁、不计入已封、永不带现行标记。"""
    _seal(client, SEED_TOTAL)  # 现行批在 2026-11-16
    # 构造「只剩 superseded 批」的营业日（取代链断裂的极端残留）
    from app.db import connect

    conn = connect(immediate=True)
    try:
        conn.execute(
            '''INSERT INTO close_batches(batch_no, business_day, system_total, declared_cash,
               variance, line_count, status, sealed_at, superseded_by_batch_id)
               VALUES ('CB-8001', '2026-11-14', 10.0, 10.0, 0.0, 1, 'superseded',
                       '2026-11-14T10:00:00Z', NULL)'''
        )
        conn.commit()
    finally:
        conn.close()

    # 写闸：superseded 不构成锁 → 该日可写；现行 sealed 日仍锁
    assert _line(client, "2026-11-14").status_code == 200
    assert _line(client, SEED_DAY).status_code == 409

    # 模块级同源判定：日锁与现行列表都只认 sealed 现行
    from app import current_batch

    conn = connect()
    try:
        assert current_batch.is_day_sealed(conn, "2026-11-14") is False
        assert current_batch.is_day_sealed(conn, SEED_DAY) is True
        current = current_batch.list_current(conn)
        assert [b["business_day"] for b in current] == [SEED_DAY]
        assert all(b["status"] == "sealed" for b in current)
    finally:
        conn.close()

    # 已封计数：superseded 不计入现行；扩展总览与主总览同源
    dash = client.get("/api/dashboard").json()
    assert dash["current_close_batch_count"] == 1
    assert dash["superseded_batch_count"] == 1
    ext = client.get("/api/dashboard/extended").json()
    assert ext["sealed_days"] == [SEED_DAY]
    assert ext["close_batch_count"] == dash["close_batch_count"]

    # current_only 批次 / 长短款都不含 superseded
    assert [b["business_day"] for b in _batches(client, current_only=True)] == [SEED_DAY]
    row = next(b for b in _batches(client) if b["batch_no"] == "CB-8001")
    assert row["status"] == "superseded" and row["is_current"] is False


def test_reseal_receipt_variance_and_overview_point_at_same_new_batch(client):
    """重封回执、长短款现行、总览现行、独立现行端点必须同批（互证不撕裂）。"""
    assert _set_policy(client, "supersede").status_code == 200
    first = _seal(client, SEED_TOTAL).json()
    second = _seal(client, SEED_TOTAL - 7.5).json()
    assert second["current_batch_id"] == second["batch_id"] != first["batch_id"]

    # 长短款现行挂新批，金额是新差额
    cur = _variances(client, current_only=True)
    assert [v["batch_id"] for v in cur] == [second["batch_id"]]
    assert cur[0]["variance"] == second["variance"] == -7.5
    # 旧批差额行只是历史
    old = next(v for v in _variances(client) if v["batch_id"] == first["batch_id"])
    assert old["is_current"] is False

    # 总览现行与回执同批同数
    dash = client.get("/api/dashboard").json()
    assert dash["current_batch_id"] == second["batch_id"]
    assert dash["current_variance"] == second["variance"]
    assert dash["open_variance_count"] == 1

    # 独立现行端点同批
    assert client.get("/api/close/current").json()["id"] == second["batch_id"]
    assert [b["id"] for b in client.get("/api/close/batches/current").json()] == [
        second["batch_id"]
    ]
    assert [v["batch_id"] for v in client.get("/api/close/variances/current").json()] == [
        second["batch_id"]
    ]
