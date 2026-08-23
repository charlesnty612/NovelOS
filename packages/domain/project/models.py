"""Pydantic 模型：Project（Sprint 1）。

对齐 ``database/migrations/0001_init.sql`` 中 ``projects`` 表结构（line 34-44）：

- ``project_id`` 形如 ``prj_<12hex>``，由 Service 层 ``new_id("prj")`` 生成。
- ``status`` 受 CHECK 约束，仅允许 ``ACTIVE / PAUSED / ARCHIVED``。
- ``created_at`` / ``updated_at`` 为 ISO-8601 字符串，Service 层写入时使用 ``now_iso()``。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProjectStatus = Literal["ACTIVE", "PAUSED", "ARCHIVED"]
"""Projects 表 status 枚举（与 DDL CHECK 对齐）。"""


class ProjectCreate(BaseModel):
    """创建项目请求体。

    ``name`` 必填；``premise / genre / target_words`` 可选。
    状态在创建时默认 ``ACTIVE``，不暴露给客户端指定。
    """

    name: str = Field(..., min_length=1, max_length=200)
    premise: str | None = None
    genre: str | None = Field(default=None, max_length=80)
    target_words: int | None = Field(default=None, ge=0)


class ProjectUpdate(BaseModel):
    """部分更新请求体——所有字段均可选，未提供则不修改。

    不允许通过该接口修改 ``project_id / created_at``，这两个字段由系统维护。
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    premise: str | None = None
    genre: str | None = Field(default=None, max_length=80)
    target_words: int | None = Field(default=None, ge=0)
    status: ProjectStatus | None = None


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
