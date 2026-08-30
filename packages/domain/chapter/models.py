"""Pydantic 模型：Chapter（Sprint 1）。

对齐 ``database/migrations/0001_init.sql`` 中 ``chapters`` 表结构（line 240-254）：

- ``chapter_id`` 形如 ``ch_<12hex>``，由 Service 层 ``new_id("ch")`` 生成。
- ``number`` 同 project 内唯一（UNIQUE 约束由 Service 层显式捕获，DB 层无 UNIQUE 索引）。
- ``plan_json`` 默认 ``{}``，由 Service 层 ``json.dumps`` 写入；pydantic 侧为 dict。
- ``status`` 受 CHECK 约束，仅允许 ``PLANNED / DRAFTED / REVIEWED / COMMITTED / RELEASED``。
- 状态机（任务书给死）：PLANNED→DRAFTED→REVIEWED→COMMITTED→RELEASED 顺序，
  或任意阶段回退到 PLANNED；非法跳变返回 409。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ChapterStatus = Literal["PLANNED", "DRAFTED", "REVIEWED", "COMMITTED", "RELEASED"]
"""Chapters 表 status 枚举（与 DDL CHECK 对齐）。"""

# 状态机迁移白名单：合法后继集合（任务书给死：顺序推进 + 回退到 PLANNED）。
# REVIEWED→DRAFTED：批准后改稿（新草稿使旧批准失效，强制重审）——
# 与 chapter_write save_draft 守卫口径一致。
ALLOWED_NEXT: dict[str, set[str]] = {
    "PLANNED": {"PLANNED", "DRAFTED"},
    "DRAFTED": {"PLANNED", "DRAFTED", "REVIEWED"},
    "REVIEWED": {"PLANNED", "DRAFTED", "REVIEWED", "COMMITTED"},
    "COMMITTED": {"PLANNED", "COMMITTED", "RELEASED"},
    "RELEASED": {"PLANNED", "RELEASED"},
}


class ChapterCreate(BaseModel):
    """创建章节请求体。"""

    number: int = Field(..., ge=1)
    title: str | None = Field(default=None, max_length=200)
    plan_json: dict | None = None  # None 视为 {}；Service 层兜底


class ChapterUpdate(BaseModel):
    """部分更新请求体——所有字段均可选。

    不允许通过该接口修改 ``chapter_id / project_id / created_at``。
    """

    title: str | None = Field(default=None, max_length=200)
    plan_json: dict | None = None
    status: ChapterStatus | None = None


class Chapter(BaseModel):
    """章节完整表示，对应数据库行。"""

    chapter_id: str
    project_id: str
    number: int
    title: str | None
    plan_json: dict
    status: ChapterStatus
    visibility: str
    who_knows: list[str] | None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Sprint 5：drafts 表（人工改稿能力，对齐 ``database/migrations/0001_init.sql``
# line 270-280）。
# ---------------------------------------------------------------------------


class DraftCreate(BaseModel):
    """创建草稿请求体（人工改稿入口）。

    - ``content`` 必填且长度 ≥ 1（pydantic 校验）；上限 500_000 字符（约 50 万字，
      远大于单章实际目标 ~2200 字；用于防止超大 body DoS）。
    - 不暴露 ``version / created_by / created_at``：version 由 Service 按
      ``max(version)+1`` 计算；created_by 在人工改稿场景下固定为 ``"human"``；
      created_at 由 ``now_iso()`` 写入。
    """

    content: str = Field(..., min_length=1, max_length=500_000)


class Draft(BaseModel):
    """草稿完整表示，对应 ``drafts`` 表行。"""

    draft_id: str
    chapter_id: str
    version: int
    content: str
    created_by: str
    prompt_version: str | None
    model_id: str | None
    created_at: str
