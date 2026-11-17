# Dayseal（日封台）

收银流水按营业日归集，日结封账后锁定；长短款可查。

- 前端 http://127.0.0.1:5700
- API http://127.0.0.1:10700

## 重封权威（全局唯一）

同一营业日**已存在现行 `status=sealed` 批**时再次发起封账，只按全局唯一策略裁决
（`app/reseal_policy.py`，配置项 `settings.reseal_policy`）。两种策略互斥，系统同一时刻
只贯彻一种，不按营业日混用：

### 禁止重封（`forbid`，默认）

- 再次封账**一律 409 拒绝**，`detail.current_batch` 只读返回原批；不新建批次、不写长短款行。
- 该营业日写锁保持不变；流水集合与合计以原封为准。
- 失败后再封同一请求仍得到同一原批，数字逐字段不变。

### 重算覆盖（`supersede`）

- 以**当前有效流水**重算系统合计与长短款，写入新批；新批成为**唯一**现行 sealed 批。
- 旧批只打 `status='superseded'`、`superseded_by_batch_id`、`superseded_at`、
  `superseded_reason`；**合计/实盘/差额/笔数/封账时间一律原样保留**，仅供只读审计。
- 旧批降级与新批落库在**同一 `BEGIN IMMEDIATE` 事务**内同成同败：不存在「旧批已降级、
  新批未成」的中间态；新批先以 `draft` 落库、旧批降级后再置 `sealed`，DB 唯一索引
  `ux_close_batches_current_day` 兜底「一天一个现行批」。
- 旧批的长短款行保留，但 `is_current=false`；现行行只来自新批。

### 同源读口径

下列四处**只认 `current_batch`（status=sealed）**，superseded 旧批永不参与：

| 读路径 | 字段 / 行为 |
| --- | --- |
| 总览已封标记 | `GET /api/dashboard` → `sealed_today`、`current_batch_no`、`close_batch_count`（只数现行） |
| 批次列表现行过滤 | `GET /api/close/batches?current_only=true`；默认列表每行带 `is_current` |
| 长短款现行行 | `GET /api/close/variances?current_only=true`；默认列表带 `is_current` |
| 写入口闸 | 流水新增/修改/删除一律以现行 sealed 批判定 409 |

策略切换（`PUT /api/settings/reseal-policy` 或 `PUT /api/close/policy`，body
`{"policy": "forbid|supersede"}`）只影响「下一次重封」的裁决，**不改写任何已封批次或历史数字**。
旧库若因历史 bug 存在同日多个 sealed 批，启动迁移保留最近一封为唯一现行，其余显式标
`superseded` 并指回新批（不删数、不静默改写）。部署如需另一种默认策略，可用环境变量
`DAYSEAL_RESEAL_POLICY=supersede` 一次性选定；运行期仍只贯彻一种。

### 相关 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/close/seal` | 唯一封账入口；禁止策略下重封 409 并只读返回原批，覆盖策略下重算并降级旧批 |
| GET | `/api/close/current` | 指定营业日唯一现行批（无则 `null`） |
| GET | `/api/close/preview` | 含 `sealed`、`current_batch`、`reseal_policy`、`can_seal`、当日批次历史 |
| GET | `/api/close/batches` | 全部批次（含 superseded 只读审计），每行带 `is_current`；`?current_only=true` 只返回现行 |
| GET | `/api/close/variances` | 长短款行，每行带 `is_current`；`?current_only=true` 只返回现行批的行 |
| GET/PUT | `/api/close/policy` | 重封策略读写 |
| GET | `/api/close/integrity` | 同源自检：双现行、现行批缺长短款行、superseded 链断链 |

自检：`GET /api/close/integrity` 暴露双现行、现行批缺长短款行、superseded 链断链等问题。

## 启动

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 10700 --app-dir .
```

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5700
```

## 测试

```powershell
cd backend
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest tests -q
```
