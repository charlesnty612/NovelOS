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

出参契约（G2，2026-10-15 批次（六））
----------------------------------
``Hook`` / ``Debt`` 同时是 8 个 ledger 端点的 **``response_model``**：单体 6 个
（POST/GET/PATCH ×2）+ 列表 2 个（``GET /projects/{pid}/hooks`` → ``list[Hook]``、
``GET /projects/{pid}/debts`` → ``list[Debt]``，2026-10-15 L1 组）。

字段形状**不是照源码读出来的，是量出来的**
--------------------------------------------
``LedgerService._row_to_hook`` / ``_row_to_debt`` 走的是 ``SELECT *`` → ``dict(row)``，
所以**响应键集 ≡ 表列集**。``response_model`` 会**过滤**未声明字段：少声明一个，响应
就静默少一个，而功能测试常常照旧全绿。故建模前用临时库 + 合成夹具跑通端点 dump 实际
键集（方法见 ``tests/api/test_ledger_plot_response_contract.py`` 与
``tests/api/test_ledger_plot_list_response_contract.py`` 的 docstring）。
实测结论（2026-10-15）：

- ``hooks`` **12 键恒在**、``narrative_debts`` **11 键恒在**，与 0001 DDL 逐字一致
  （此后无任何迁移 ALTER 过这两张表）；
- ⚠️ **列表端点的行键集单独量过**（每端点造 2 行、含「全空」与「全字段带值」两种，
  并各跑一次 ``?status=`` 过滤）：**12 / 11 键，与单体端点逐字相同**。这不是从
  「两者共用同一个 Service 方法」推出来的——Lead 手上就有反例（``GET /commits``
  列表行 13 键而 ``POST /commits`` 响应只有 4 键），所以两条路径都实测过；
- 空值一律是 **``null`` 而非省略** ⇒ 所有可空列声明为 ``X | None`` 且**不给默认值**
  （给了默认值就变成「可省略」，前端得写 ``??`` 兜底，那正是 G10「欠定义类型」的病）；
- 端点上**一律不加** ``response_model_exclude_none``：加了就会把上面那批 null 键抹掉，
  把契约从「键恒在」改成「键可能缺席」。

``extra="allow"`` 的理由
------------------------
读路径是 ``SELECT *``：将来某个迁移给 ``hooks`` 加一列，响应就会多一个键。若模型
``extra="ignore"``（Pydantic 默认），``response_model`` 会**静默把新列裁掉**——那正是本
批次要消灭的「后端有、前端拿不到」缺口（见 G1~G10）。故声明已知的键、不宣称封闭。
代价是 OpenAPI 里 ``additionalProperties: true``，新增列不出现在 schema 中——
契约测试因此另断言 schema 的 ``properties`` / ``required``（防 ``extra`` 掩盖声明丢失）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
    """Hook 完整表示，对应数据库行。

    ⚠️ **响应键集 ≡ ``hooks`` 表列集**（读路径是 ``SELECT *``）：表加列 ⇒ 响应加键。
    本类声明 12 个已知键并 ``extra="allow"``，详见模块 docstring「出参契约」段。
    """

    model_config = ConfigDict(extra="allow")

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
    """Debt 完整表示，对应数据库行。

    ⚠️ **响应键集 ≡ ``narrative_debts`` 表列集**（读路径是 ``SELECT *``）。同 ``Hook``：
    声明已知的 11 个键 + ``extra="allow"``，不给可空字段设默认值（空值是 null 而非省略）。
    """

    model_config = ConfigDict(extra="allow")

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
