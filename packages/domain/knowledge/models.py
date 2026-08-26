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
from typing import Any

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
