"""domain.knowledge 公共导出（V3.3 P0-2 知识权限补全）。

承载 reveal_policies 表的领域模型与 Service；与 visibility/who_knows 字段正交。
"""
from __future__ import annotations

from .models import (
    REVEAL_POLICY_STATUSES,
    REVEAL_POLICY_TARGET_KINDS,
    RevealPolicy,
)
from .service import (
    KnowledgeServiceError,
    NotFoundError,
    RevealPolicyService,
    ValidationError,
)

__all__ = [
    "REVEAL_POLICY_TARGET_KINDS",
    "REVEAL_POLICY_STATUSES",
    "RevealPolicy",
    "RevealPolicyService",
    "KnowledgeServiceError",
    "NotFoundError",
    "ValidationError",
]
