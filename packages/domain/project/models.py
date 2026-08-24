"""Pydantic 模型：Project（Sprint 1 + Sprint 15/V1.3）。

对齐 ``database/migrations/0001_init.sql`` 中 ``projects`` 表结构（line 34-44）；
Sprint 15 通过 ``0008_author_style_samples_and_overdue.sql`` 增加可空列
``foreshadow_overdue_chapters INTEGER NOT NULL DEFAULT 30``（DDL 兜底，
DB 中永远非空；模型仍允许 None 以兜底「极老库在升级前快照」的瞬态场景）。

- ``project_id`` 形如 ``prj_<12hex>``，由 Service 层 ``new_id("prj")`` 生成。
- ``status`` 受 CHECK 约束，仅允许 ``ACTIVE / PAUSED / ARCHIVED``。
- ``foreshadow_overdue_chapters``：Context Engine 装配开放伏笔清单时
  判断 overdue 的项目级阈值（chapters），默认 30（Sprint 14 常量）。
- ``created_at`` / ``updated_at`` 为 ISO-8601 字符串，Service 层写入时使用 ``now_iso()``。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProjectStatus = Literal["ACTIVE", "PAUSED", "ARCHIVED"]
"""Projects 表 status 枚举（与 DDL CHECK 对齐）。"""

# 默认 overdue 阈值（与 0008 DDL DEFAULT 30 + builders._FORESHADOW_OVERDUE_CHAPTERS 对齐）。
_DEFAULT_FORESHADOW_OVERDUE_CHAPTERS = 30


class ProjectCreate(BaseModel):
    """创建项目请求体。

    ``name`` 必填；``premise / genre / target_words`` 可选。
    状态在创建时默认 ``ACTIVE``，不暴露给客户端指定。
    """

    name: str = Field(..., min_length=1, max_length=200)
    premise: str | None = None
    genre: str | None = Field(default=None, max_length=80)
    target_words: int | None = Field(default=None, ge=0)
    foreshadow_overdue_chapters: int | None = Field(
        default=None, ge=1,
        description="伏笔 overdue 阈值（章节数）；省略时使用 DDL 默认 30。",
    )


class ProjectUpdate(BaseModel):
    """部分更新请求体——所有字段均可选，未提供则不修改。

    不允许通过该接口修改 ``project_id / created_at``，这两个字段由系统维护。
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    premise: str | None = None
    genre: str | None = Field(default=None, max_length=80)
    target_words: int | None = Field(default=None, ge=0)
    status: ProjectStatus | None = None
    foreshadow_overdue_chapters: int | None = Field(default=None, ge=1)


class Project(BaseModel):
    """项目完整表示，对应数据库行。"""

    project_id: str
    name: str
    premise: str | None
    genre: str | None
    target_words: int | None
    status: ProjectStatus
    created_at: str
    updated_at: str
    foreshadow_overdue_chapters: int | None = Field(
        default=_DEFAULT_FORESHADOW_OVERDUE_CHAPTERS,
    )


__all__ = [
    "Project",
    "ProjectCreate",
    "ProjectUpdate",
    "ProjectStatus",
    "_DEFAULT_FORESHADOW_OVERDUE_CHAPTERS",
]
