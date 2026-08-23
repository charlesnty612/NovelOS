"""Pydantic 模型：Character（Sprint 1）。

对齐 ``database/migrations/0001_init.sql`` 中两张表：

- ``characters``（line 51-64）：定义侧。``role`` 枚举
  ``protagonist / antagonist / supporting / mentor / love_interest / narrator / other``；
  ``visibility`` 枚举 ``PUBLIC / VISIBLE / RESTRICTED / HIDDEN``。
- ``character_states``（line 70-80）：状态侧，追加式快照。
  ``PRIMARY KEY (character_id, state_version)``。
  对齐 PRD §17：Definition 与 State 分离——性格/价值观/背景/核心创伤/基本能力 长期不变
  写在 ``core_json``；当前地点/情绪/目标/认知/关系/伤势/资源 写在 ``state_json`` 快照。

PR #§16 原则：所有角色对象必须能挂「权限字段」（visibility + who_knows）；Sprint 1
由 Service 层在 JSON 内部做字段级过滤（属后续 context_engine），不在本 Sprint 范围。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CharacterRole = Literal[
    "protagonist", "antagonist", "supporting", "mentor", "love_interest", "narrator", "other"
]
"""Characters 表 role 枚举（与 DDL CHECK 对齐）。"""

VisibilityLevel = Literal["PUBLIC", "VISIBLE", "RESTRICTED", "HIDDEN"]
"""Characters / character_states 共享 visibility 枚举（与 DDL CHECK 对齐）。"""


class CharacterCreate(BaseModel):
    """创建角色请求体。

    - ``name`` 必填。
    - ``role`` 默认 ``supporting``。
    - ``core_json`` 默认 ``{}``（性格/价值观/背景/核心创伤/基本能力）。
    - ``visibility`` 默认 ``PUBLIC``（与 DB DEFAULT 一致）。
    - ``who_knows`` 默认 ``None``（沿用默认）。

    创建时同时插入 ``character_states`` 首行（v1，state_json={}，visibility=VISIBLE），
    由 Service 同一事务完成——对齐 PRD §17 Definition/State 分离。
    """

    name: str = Field(..., min_length=1, max_length=200)
    role: CharacterRole | None = None  # None → Service 兜底 supporting
    core_json: dict | None = None  # None → {}
    visibility: VisibilityLevel | None = None  # None → PUBLIC
    who_knows: list[str] | None = None  # JSON 数组；None → NULL


class CharacterUpdate(BaseModel):
    """部分更新请求体——所有字段均可选。

    只允许修改定义侧字段：``name / role / core_json / visibility / who_knows``。
    不允许通过本接口修改 ``character_id / project_id / created_at``。

    注：``core_json`` 的更新在概念上属于「Definition 变更」；Sprint 2 起应走 State Delta
    通道（PRD §17），Sprint 1 先以直接覆盖方式实现，方法注释保留说明。
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    role: CharacterRole | None = None
    core_json: dict | None = None
    visibility: VisibilityLevel | None = None
    who_knows: list[str] | None = None


class Character(BaseModel):
    """角色完整表示。

    ``latest_state_json`` 取自最大 ``state_version`` 行的 ``state_json``；不存在时为 ``{}``。
    """

    character_id: str
    project_id: str
    name: str
    role: CharacterRole
    core_json: dict
    visibility: VisibilityLevel
    who_knows: list[str] | None
    created_at: str
    updated_at: str
    latest_state_version: int  # 0 表示尚无 state 行（理论上不应发生：create 时已插 v1）
    latest_state_json: dict


class CharacterState(BaseModel):
    """角色状态快照行（character_states 表）。"""

    character_id: str
    state_version: int
    state_json: dict
    visibility: VisibilityLevel
    who_knows: list[str] | None
    created_at: str
