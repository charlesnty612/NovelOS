"""Pydantic 模型：Volume（V3.4 多卷与规模）。

对齐 ``database/migrations/0015_volumes.sql`` 中 ``volumes`` 表结构：

- ``volume_id`` 形如 ``vol_<12hex>``，由 Service 层 ``new_id("vol")`` 生成。
- ``number`` 同 project 内唯一（UNIQUE(project_id, number)），由 Service 层
  显式捕获 IntegrityError 转 VolumeConflictError，DB 层 UNIQUE 由迁移保障。
- ``status`` 受 CHECK 约束，仅允许 ``active / sealed``；状态机由 Service 层
  ``seal()`` 方法显式控制（PATCH 不允许 sealed → active 反向跳变）。
- ``terminal_snapshot_json`` 仅在 ``status='sealed'`` 时由 Service 写入
  story_states 最新快照 JSON；active 时保持 NULL。
- ``created_at / updated_at`` 为 ISO-8601 字符串，Service 层写入时使用
  ``now_iso()``。

Pydantic 形态：
- 域内用 ``BaseModel`` 而非 dataclass，对齐 ``packages/domain/chapter/models.py``
  风格；路由 response_model 复用同名 BaseModel。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

VolumeStatus = Literal["active", "sealed"]
"""volumes 表 status 枚举（与 DDL CHECK 对齐）。"""


class VolumeCreate(BaseModel):
    """创建卷请求体。

    ``number`` 必填且 ≥1；``title`` 可选。状态在创建时固定为 ``active``，
    不暴露给客户端指定（封存走显式 ``POST /seal`` 端点）。
    """

    number: int = Field(..., ge=1)
    title: str | None = Field(default=None, max_length=200)


class VolumeUpdate(BaseModel):
    """部分更新请求体——所有字段均可选，未提供则不修改。

    ``status`` 允许 ``active`` 修正（仅限 PATCH 调用场景下确实需要改 title
    时顺带改 status）；``sealed`` → ``active`` 的反向跳变由 Service 拒绝。
    """

    title: str | None = Field(default=None, max_length=200)
    status: VolumeStatus | None = None


class VolumeAssignRequest(BaseModel):
    """挂章请求体。"""

    chapter_id: str = Field(..., min_length=1)


class Volume(BaseModel):
    """卷完整表示，对应数据库行。

    ``terminal_snapshot_json`` 在 status='sealed' 时为 dict（解析后）；
    status='active' 时为 None。
    """

    volume_id: str
    project_id: str
    number: int
    title: str | None
    status: VolumeStatus
    terminal_snapshot_json: dict | None
    created_at: str
    updated_at: str


class VolumeListItem(BaseModel):
    """卷列表项——在 ``Volume`` 基础上加 ``chapter_count`` 聚合字段。"""

    volume_id: str
    project_id: str
    number: int
    title: str | None
    status: VolumeStatus
    terminal_snapshot_json: dict | None
    chapter_count: int
    created_at: str
    updated_at: str


__all__ = [
    "VolumeStatus",
    "VolumeCreate",
    "VolumeUpdate",
    "VolumeAssignRequest",
    "Volume",
    "VolumeListItem",
]
