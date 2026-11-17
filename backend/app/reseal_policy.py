"""重封权威策略（reseal / re-seal authority）的唯一事实来源。

同一营业日已经存在一个 ``status='sealed'`` 的现行批次时，再次发起封账
只允许一种全局行为；系统在同一时刻只生效一种策略，不允许按营业日或
按调用方混用：

``forbid``（禁止重封）
    已存在现行 sealed 批时，再次封账一律拒绝（HTTP 409），只读返回原批；
    营业日写锁保持不变；流水集合以原封为准。

``supersede``（重算覆盖）
    已存在现行 sealed 批时，以当前有效流水重算系统合计与长短款，创建新批
    并成为唯一现行 sealed 批；旧批只打 ``status='superseded'`` 与
    ``superseded_by_batch_id`` 标记，数字原样保留，仅供只读审计。整个覆盖
    （旧批降级 + 新批落库 + 长短款行）在同一事务内同成同败。

所有读写路径（总览已封标记、批次列表现行过滤、长短款现行行、流水写入口闸）
都必须通过 :func:`normalize` 读取同一份配置，并只认 ``current_batch``。
"""
from __future__ import annotations

import os
from typing import Any

# ---------------------------------------------------------------- 策略选择

POLICY_FORBID = "forbid"
POLICY_SUPERSEDE = "supersede"

#: 新库 / 未配置时的默认策略。禁止重封不会改写任何既有数字，作为默认最稳妥。
#: 部署可一次性用环境变量 ``DAYSEAL_RESEAL_POLICY`` 选定另一种，运行期仍只生效一种。
DEFAULT_POLICY = POLICY_FORBID


def env_default() -> str:
    raw = os.environ.get("DAYSEAL_RESEAL_POLICY") or os.environ.get("RESEAL_POLICY")
    if not raw:
        return DEFAULT_POLICY
    return _ALIASES.get(str(raw).strip().lower(), DEFAULT_POLICY)

POLICIES: tuple[str, ...] = (POLICY_FORBID, POLICY_SUPERSEDE)

POLICY_LABELS: dict[str, str] = {
    POLICY_FORBID: "禁止重封",
    POLICY_SUPERSEDE: "重算覆盖（supersede）",
}

POLICY_DESCRIPTIONS: dict[str, str] = {
    POLICY_FORBID: (
        "营业日已存在现行 sealed 批时，再次封账一律拒绝并只读返回原批；"
        "写锁保持，流水集合与合计以原封为准。"
    ),
    POLICY_SUPERSEDE: (
        "营业日已存在现行 sealed 批时，允许以当前有效流水重算合计与长短款；"
        "旧批标记 superseded（数字只读保留），新批成为唯一现行 sealed 批，"
        "覆盖在同一事务内同成同败。"
    ),
}

#: 设置表中保存策略的键（规范键）；读取时兼容历史/别名键。
SETTING_KEY = "reseal_policy"
LEGACY_SETTING_KEYS: tuple[str, ...] = (
    "reseal_policy",
    "reclose_policy",
    "seal_rerun_policy",
    "reseal_authority",
)

#: 宽松接受调用方的中英文写法，落库只存规范值。
_ALIASES: dict[str, str] = {
    POLICY_FORBID: POLICY_FORBID,
    "reject": POLICY_FORBID,
    "deny": POLICY_FORBID,
    "block": POLICY_FORBID,
    "no_reseal": POLICY_FORBID,
    "no-reseal": POLICY_FORBID,
    "禁止": POLICY_FORBID,
    "禁止重封": POLICY_FORBID,
    POLICY_SUPERSEDE: POLICY_SUPERSEDE,
    "overwrite": POLICY_SUPERSEDE,
    "recompute": POLICY_SUPERSEDE,
    "replace": POLICY_SUPERSEDE,
    "cover": POLICY_SUPERSEDE,
    "覆盖": POLICY_SUPERSEDE,
    "重算": POLICY_SUPERSEDE,
    "覆盖重算": POLICY_SUPERSEDE,
}


def normalize(policy: str | None) -> str:
    """把库中 / 请求中的策略归一；无法识别时回落到默认策略。"""
    if policy is None:
        return DEFAULT_POLICY
    return _ALIASES.get(str(policy).strip().lower(), DEFAULT_POLICY)


def parse(policy: str | None) -> str:
    """严格解析外部输入；非法值抛 ``ValueError``（由 API 转成 400）。"""
    key = str(policy or "").strip().lower()
    if key not in _ALIASES:
        raise ValueError(
            f"未知重封策略：{policy!r}；只允许 {', '.join(POLICIES)}"
        )
    return _ALIASES[key]


def label(policy: str) -> str:
    return POLICY_LABELS[normalize(policy)]


def options() -> list[dict[str, Any]]:
    return [
        {
            "value": policy,
            "label": POLICY_LABELS[policy],
            "description": POLICY_DESCRIPTIONS[policy],
        }
        for policy in POLICIES
    ]


def as_dict(policy: str) -> dict[str, Any]:
    """给 API / 总览复用的策略快照。"""
    current = normalize(policy)
    return {
        "policy": current,
        "reclose_policy": current,
        "seal_rerun_policy": current,
        "label": POLICY_LABELS[current],
        "description": POLICY_DESCRIPTIONS[current],
        "default": DEFAULT_POLICY,
        "forbid_reseal": current == POLICY_FORBID,
        "supersede_enabled": current == POLICY_SUPERSEDE,
        "options": options(),
    }
