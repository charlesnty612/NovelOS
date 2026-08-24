"""Pydantic 模型：Ledger（Sprint 9）。

对齐 ``database/migrations/0001_init.sql`` 中 ``hooks`` 表结构（line 197-214）与
``narrative_debts`` 表结构（line 219-235）。

字段口径
--------
- ``hooks`` 表枚举：
  - status: ``OPEN / ACTIVE / ESCALATED / RESOLVED / ABANDONED``（PRD §21 五态）
  - importance: REAL 0.0-1.0（DDL CHECK 约束）
- ``narrative_debts`` 表枚举：
  - status: ``open / acknowledged / paid / forgiven``（PRD §22 + v1.1 扩展）
  - severity: REAL 0.0-1.0（DDL CHECK 约束）
- 三个可选章节外键（hook: introduced_chapter_id / expected_payoff_chapter_id /
  payoff_chapter_id；debt: created_chapter_id / deadline_chapter_id）均允许 NULL；
  Service 层提供存在性校验。
- ``who_knows`` 为 JSON 字符串数组（NULL 沿用默认；``'[]'`` 显式置空，对齐
  knowledge-permission-v0.md §3.1）。
- ``visibility`` 默认 RESTRICTED（与 plot_events 一致；台账属于「写作工艺」层）。

状态机
------
任务书给死（PRD §21 / §22）：

Hook 五态机（OPEN → ACTIVE → ESCALATED → RESOLVED + 任意 → ABANDONED）::

    OPEN       → {OPEN, ACTIVE, ABANDONED}
    ACTIVE     → {ACTIVE, ESCALATED, RESOLVED, ABANDONED}
    ESCALATED  → {ESCALATED, RESOLVED, ABANDONED}
    RESOLVED   → {RESOLVED, ABANDONED}    # 兜底：已 RESOLVED 后仅可放弃（业务罕见但合规）
    ABANDONED  → {ABANDONED}              # 终态，不可回退

注：任务书原文是「前进制 + 任意→ABANDONED」；RESOLVED 的后继集合严格按
任务书给死。若需 RESOLVED→ACTIVE 复活，由后续 Sprint 重新拍板。

Debt 四态机（open → acknowledged → paid / forgiven 前进制）::

    open         → {open, acknowledged, paid, forgiven}
    acknowledged → {acknowledged, paid, forgiven}
    paid         → {paid}
    forgiven     → {forgiven}

付费 / 免除为终态，不可回退。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

HookStatus = Literal["OPEN", "ACTIVE", "ESCALATED", "RESOLVED", "ABANDONED"]
"""Hook 状态枚举（与 DDL CHECK 对齐，PRD §21）。"""

# Hook 状态机迁移白名单（任务书给死）。
HOOK_ALLOWED_NEXT: dict[str, set[str]] = {
    "OPEN": {"OPEN", "ACTIVE", "ABANDONED"},
    "ACTIVE": {"ACTIVE", "ESCALATED", "RESOLVED", "ABANDONED"},
    "ESCALATED": {"ESCALATED", "RESOLVED", "ABANDONED"},
    "RESOLVED": {"RESOLVED", "ABANDONED"},
    "ABANDONED": {"ABANDONED"},
}


class HookCreate(BaseModel):
    """创建 Hook 请求体。"""

    name: str = Field(..., min_length=1, max_length=200)
    introduced_chapter_id: str | None = None
    expected_payoff_chapter_id: str | None = None
    payoff_chapter_id: str | None = None
    status: HookStatus | None = None
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    visibility: str | None = None
    who_knows: list[str] | None = None


class HookUpdate(BaseModel):
    """部分更新 Hook 请求体——所有字段均可选。"""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    introduced_chapter_id: str | None = None
    expected_payoff_chapter_id: str | None = None
    payoff_chapter_id: str | None = None
    status: HookStatus | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    visibility: str | None = None
    who_knows: list[str] | None = None


class Hook(BaseModel):
    """Hook 完整表示，对应数据库行。"""

    hook_id: str
    project_id: str
    name: str
    introduced_chapter_id: str | None
    status: HookStatus
    importance: float
    expected_payoff_chapter_id: str | None
    payoff_chapter_id: str | None
    visibility: str
    who_knows: list[str] | None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Narrative Debts
# ---------------------------------------------------------------------------

DebtStatus = Literal["open", "acknowledged", "paid", "forgiven"]
"""Debt 状态枚举（与 DDL CHECK 对齐，PRD §22 v1.1 扩展）。"""

# Debt 状态机迁移白名单（任务书给死）。
DEBT_ALLOWED_NEXT: dict[str, set[str]] = {
    "open": {"open", "acknowledged", "paid", "forgiven"},
    "acknowledged": {"acknowledged", "paid", "forgiven"},
    "paid": {"paid"},
    "forgiven": {"forgiven"},
}


class DebtCreate(BaseModel):
    """创建 Debt 请求体。"""

    description: str = Field(..., min_length=1)
    created_chapter_id: str | None = None
    deadline_chapter_id: str | None = None
    status: DebtStatus | None = None
    severity: float = Field(default=0.5, ge=0.0, le=1.0)
    visibility: str | None = None
    who_knows: list[str] | None = None


class DebtUpdate(BaseModel):
    """部分更新 Debt 请求体——所有字段均可选。"""

    description: str | None = Field(default=None, min_length=1)
    created_chapter_id: str | None = None
    deadline_chapter_id: str | None = None
    status: DebtStatus | None = None
    severity: float | None = Field(default=None, ge=0.0, le=1.0)
    visibility: str | None = None
    who_knows: list[str] | None = None


class Debt(BaseModel):
    """Debt 完整表示，对应数据库行。"""

    debt_id: str
    project_id: str
    description: str
    created_chapter_id: str | None
    severity: float
    deadline_chapter_id: str | None
    status: DebtStatus
    visibility: str
    who_knows: list[str] | None
    created_at: str
    updated_at: str


# 公开状态枚举值集合（前端 / 测试用）
HOOK_STATUS_VALUES: tuple[str, ...] = ("OPEN", "ACTIVE", "ESCALATED", "RESOLVED", "ABANDONED")
DEBT_STATUS_VALUES: tuple[str, ...] = ("open", "acknowledged", "paid", "forgiven")
