"""Pydantic 模型：Project（Sprint 1 + Sprint 15/V1.3 + V3.7 字数带覆盖）。

对齐 ``database/migrations/0001_init.sql`` 中 ``projects`` 表结构（line 34-44）；
Sprint 15 通过 ``0008_author_style_samples_and_overdue.sql`` 增加可空列
``foreshadow_overdue_chapters INTEGER NOT NULL DEFAULT 30``（DDL 兜底，
DB 中永远非空；模型仍允许 None 以兜底「极老库在升级前快照」的瞬态场景）。

V3.7 通过 ``0023_project_word_band.sql`` 新增可空列 ``word_band_json TEXT``——
存储 JSON 字符串（dict 序列化），供 writer / chapter_review 装配时按项目覆盖
``wordcount.word_band`` / ``classify_prose_length`` 的 ``low_ratio / high_ratio /
floor`` 三个可选键。无覆盖（NULL）= 模块默认（0.85 / 1.15 / 1200）。

- ``project_id`` 形如 ``prj_<12hex>``，由 Service 层 ``new_id("prj")`` 生成。
- ``status`` 受 CHECK 约束，仅允许 ``ACTIVE / PAUSED / ARCHIVED``。
- ``foreshadow_overdue_chapters``：Context Engine 装配开放伏笔清单时
  判断 overdue 的项目级阈值（chapters），默认 30（Sprint 14 常量）。
- ``word_band``：项目级字数带覆盖 dict；可空键；非法值由 router 调
  :func:`packages.core.quality.wordcount.resolve_band_config` 校验，非法 → 422。
  ``None`` 与「未提供」严格区分——前者表示清除覆盖（写 NULL），
  后者表示保留原值。
- ``created_at`` / ``updated_at`` 为 ISO-8601 字符串，Service 层写入时使用 ``now_iso()``。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ProjectStatus = Literal["ACTIVE", "PAUSED", "ARCHIVED"]
"""Projects 表 status 枚举（与 DDL CHECK 对齐）。"""

# 默认 overdue 阈值（与 0008 DDL DEFAULT 30 + builders._FORESHADOW_OVERDUE_CHAPTERS 对齐）。
_DEFAULT_FORESHADOW_OVERDUE_CHAPTERS = 30

# word_band 覆盖 dict 允许的可选键（与 wordcount._OVERRIDE_KEYS 对齐）；
# 此处再列一份仅用于 Pydantic 字段文档，不作严格白名单（resolve_band_config 是校验权威）。
_WORD_BAND_KEYS = ("low_ratio", "high_ratio", "floor")


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
    word_band: dict[str, Any] | None = Field(
        default=None,
        description=(
            "字数带覆盖（low_ratio / high_ratio / floor 三键可任选）；"
            "省略/None=无覆盖。校验在 router 层走 resolve_band_config。"
        ),
    )


class ProjectUpdate(BaseModel):
    """部分更新请求体——所有字段均可选，未提供则不修改。

    不允许通过该接口修改 ``project_id / created_at``，这两个字段由系统维护。

    ``word_band`` 与「未提供」区分：未提供 → 保留原值；显式 ``null`` →
 清除覆盖（落 DB NULL）；显式 dict → 校验后写 ``word_band_json``。
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    premise: str | None = None
    genre: str | None = Field(default=None, max_length=80)
    target_words: int | None = Field(default=None, ge=0)
    status: ProjectStatus | None = None
    foreshadow_overdue_chapters: int | None = Field(default=None, ge=1)
    word_band: dict[str, Any] | None = Field(
        default=None,
        description=(
            "字数带覆盖（low_ratio / high_ratio / floor 三键可任选）；"
            "None=清除覆盖；省略=保留原值。校验在 router 层走 resolve_band_config。"
        ),
    )


class Project(BaseModel):
    """项目完整表示，对应数据库行。

    ``word_band`` 字段：读路径把 ``projects.word_band_json`` 解析成 dict
    （非法 JSON 视为 None，不炸）；写路径由 ProjectUpdate 入口，service 序列化。
    """

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
    word_band: dict[str, Any] | None = Field(
        default=None,
        description=(
            "字数带覆盖（low_ratio / high_ratio / floor）；None = 项目无覆盖，"
            "消费点走模块默认 0.85 / 1.15 / 1200。"
        ),
    )


__all__ = [
    "Project",
    "ProjectCreate",
    "ProjectUpdate",
    "ProjectStatus",
    "_DEFAULT_FORESHADOW_OVERDUE_CHAPTERS",
    "_WORD_BAND_KEYS",
]
