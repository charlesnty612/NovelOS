"""domain.knowledge 数据模型（V3.3 P0-2 知识权限补全）。

对齐 ``database/migrations/0014_knowledge_reveal.sql`` 中 ``reveal_policies`` 表结构。

设计要点：
- ``target_kind`` 枚举 8 种实体（character / location / faction / world_rule /
  event / hook / debt / relationship）；与 0014 DDL CHECK 对齐。
- ``status`` 三态（planned / revealed / cancelled）；与 0014 DDL CHECK 对齐。
- ``audience`` 默认 'reader'，可写 'reader' / 'character:<id>' / 逗号分隔的混合
  形态（语义与 0014 DDL default 一致；扩展语义由 service 层解释）。
- ``RevealPolicy`` dataclass + ``to_dict()`` 与现有 domain 服务风格一致（参
  ``packages/domain/plot/models.py`` ``Relationship`` / ``TimelineEvent``）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# reveal_policies.target_kind 枚举（0014 DDL CHECK 对齐）
REVEAL_POLICY_TARGET_KINDS: tuple[str, ...] = (
    "character",
    "location",
    "faction",
    "world_rule",
    "event",
    "hook",
    "debt",
    "relationship",
)

# reveal_policies.status 枚举（0014 DDL CHECK 对齐）
REVEAL_POLICY_STATUSES: tuple[str, ...] = (
    "planned",
    "revealed",
    "cancelled",
)

# 默认 audience（0014 DDL DEFAULT 'reader'）
_DEFAULT_AUDIENCE = "reader"

# 默认 status（0014 DDL DEFAULT 'planned'）
_DEFAULT_STATUS = "planned"


@dataclass
class RevealPolicy:
    """reveal_policies 表对应实体（V3.3 schema）。"""

    policy_id: str
    project_id: str
    target_kind: str
    target_id: str
    reveal_by_chapter: int | None = None
    audience: str = _DEFAULT_AUDIENCE
    status: str = _DEFAULT_STATUS
    revealed_chapter: int | None = None
    notes: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "project_id": self.project_id,
            "target_kind": self.target_kind,
            "target_id": self.target_id,
            "reveal_by_chapter": self.reveal_by_chapter,
            "audience": self.audience,
            "status": self.status,
            "revealed_chapter": self.revealed_chapter,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# Pydantic 视图（2026-10-15 批次（五）补契约）
# ---------------------------------------------------------------------------
# 上面那个 `RevealPolicy` 是 **dataclass**（service 层在用），router 无法拿它当
# `response_model`。这里补一组 Pydantic 模型专供 API 契约，**不替换** dataclass：
# 换掉会动到 service 层与 `KnowledgeService` 的所有调用点，收益不抵风险。
#
# ⚠️ 两份模型必须保持同形，否则会出现「入参校验用一套、出参序列化用另一套」的
# 静默分叉。`tests/api/test_reveal_and_judge_response_contract.py` 有专门用例把
# `RevealPolicyOut` 的字段集与 `RevealPolicy.to_dict()` 的键集**逐字比对**。

RevealTargetKindLiteral = Literal[
    "character",
    "location",
    "faction",
    "world_rule",
    "event",
    "hook",
    "debt",
    "relationship",
]
RevealStatusLiteral = Literal["planned", "revealed", "cancelled"]


class RevealPolicyOut(BaseModel):
    """``reveal_policies`` 行的**出参**形态（= `RevealPolicy.to_dict()` 的 11 个键）。

    字段与类型经实测（临时库跑通端点后 dump 键集）确认，不是照 dataclass 抄的。
    """

    model_config = ConfigDict(extra="allow")

    policy_id: str
    project_id: str
    target_kind: RevealTargetKindLiteral
    target_id: str
    # ⚠️ 这 11 个键**恒在**（实测：新建后立即 list，每个键都在），
    # 所以一律不给 default——给了就会生成成可选（`status?:`），
    # 而后端从不省略它 ⇒ 前端被迫写 `?? ''` 兜底，那正是「欠定义类型」的病。
    # 「可空」用 `X | None` 表达，「可省略」才是 `= None`。
    reveal_by_chapter: int | None = Field(description="计划在第几章前揭开；null = 未定")
    audience: str = Field(
        description="`reader` / `character:<id>` / 逗号混写；DDL default 为 'reader'",
    )
    status: RevealStatusLiteral
    revealed_chapter: int | None
    notes: str | None
    created_at: str
    updated_at: str


class RevealPolicyCreateIn(BaseModel):
    """``POST /projects/{pid}/reveal-policies`` 的入参。

    ⚠️ **服务端仍会再校验一遍**（`KnowledgeService.create` 的枚举 / 范围 / target
    存在性检查），本模型只为让 OpenAPI 有 schema、把 422 的原因提前说清楚。

    ⚠️ ``reveal_by_chapter`` / ``revealed_chapter`` 必须 ≥1（DDL 与 service 双检）。
    """

    model_config = ConfigDict(extra="forbid")

    target_kind: RevealTargetKindLiteral
    target_id: str = Field(min_length=1)
    reveal_by_chapter: int | None = Field(default=None, ge=1)
    audience: str = Field(default="reader", min_length=1)
    status: RevealStatusLiteral = "planned"
    revealed_chapter: int | None = Field(default=None, ge=1)
    notes: str | None = None


class RevealPolicyUpdateIn(BaseModel):
    """``PATCH /projects/{pid}/reveal-policies/{policy_id}`` 的入参。

    ⚠️ **只有这三个字段被后端读取**，其余键静默忽略——所以这里用
    ``extra="forbid"`` 把这件事**显式化**：以前传 ``reveal_by_chapter`` 会被
    悄悄吞掉（返回 200 却什么都没改），现在直接 422。

    ⚠️ ``revealed_chapter`` **无法清空**：service 把 ``None`` 读作「不提供」，
    发 ``null`` 也改不掉（DDL 与 service 均无「清空」通道）。这是既有缺口，
    UI 侧已按复算口径放行并在 `RevealTab` 里写明。
    """

    model_config = ConfigDict(extra="forbid")

    status: RevealStatusLiteral | None = None
    revealed_chapter: int | None = Field(default=None, ge=1)
    notes: str | None = None


__all__ = [
    "RevealPolicy",
    "RevealPolicyCreateIn",
    "RevealPolicyOut",
    "RevealPolicyUpdateIn",
    "RevealStatusLiteral",
    "RevealTargetKindLiteral",
    "REVEAL_POLICY_STATUSES",
    "REVEAL_POLICY_TARGET_KINDS",
]
