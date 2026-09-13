"""题材包（genre_pack）Pydantic 模型（题材库 P1a）。

职责与边界：
- 描述 ``genre_packs`` 表行（:class:`GenrePack`）与其摘要投影
  （:class:`GenrePackSummary`）；
- 描述 payload 的**软件层消费子集**结构（:class:`GenrePackPayload` /
  :class:`GenrePayoffType`）——对齐内容仓 10 维中「机制可消费」的 7 段：
  ``payoff_types`` / ``structure_templates`` / ``pacing`` /
  ``ratio_declarations``（可选）/ ``style_constraints``（可选）/
  ``opening_rules``（可选，P2）/ ``critic_rubric``（可选，P2）；
- 定义 payload 的 schema 版本线常量（v1.1.0）与 schema 文件路径供校验器引用。

设计取舍（与 reference_canons 的差别）：
- canon 的 schema 是**唯一校验权威**（deconstruct 工作流产出后过 jsonschema，产不出即
  失败）；题材包同为 curated 资产，但人工策展的 payload 允许「只声明一部分维度」，
  故本模块的 pydantic 模型是**宽松视图**（类型提示 + 取值抽取），**不做第二套强校验**：
  严格校验交给 ``docs/state-model/schemas/genre-pack.schema.json``（jsonschema，
  Draft 2020-12），避免两套规则漂移后互相打脸。
- ``extra="allow"``：未知键原样保留（curation 会加 ``stale`` / ``source`` 等标注；
  消费侧只按需取用已知字段）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "GENRE_PACK_SCHEMA_PATH",
    "GENRE_PACK_SCHEMA_VERSION",
    "GenrePack",
    "GenrePackCreate",
    "GenrePackPayload",
    "GenrePackSummary",
    "GenrePackUpdate",
    "GenrePayoffType",
    "parse_payload",
]

# payload schema 版本线（与 docs/state-model/schemas/genre-pack.schema.json 的
# ``schema_version.pattern`` 对齐：``^genre-pack\\.v1\\.[0-9]+\\.[0-9]+$``）。
# v1.1.0（P2）：只加可选段（opening_rules / critic_rubric），旧 v1.0.0 payload 继续有效。
GENRE_PACK_SCHEMA_VERSION = "genre-pack.v1.1.0"

# schema 文件（jsonschema 校验权威）。路径以仓库根为基准，与
# packages/core/story_state/validator.py 同款口径。
GENRE_PACK_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "state-model"
    / "schemas"
    / "genre-pack.schema.json"
)


class GenrePayoffType(BaseModel):
    """爽点类型条目（payload.payoff_types[]）。

    严格口径（``type_id`` 的 snake_case 形态、``strength`` 三值枚举、字段长度上限）
    由 schema 文件负责；本模型只做类型提示与取值抽取，字段全可空。
    """

    model_config = ConfigDict(extra="allow")

    type_id: str
    name: str
    strength: str | None = Field(
        default=None, description="强度分级：S=卷级 / M=章级中型 / s=章级小型。",
    )
    description: str | None = None
    applicable: str | None = Field(default=None, description="适用段落。")
    density_cap: str | None = Field(
        default=None, description="密度上限原文文本（如「每卷 2~3 次」）。",
    )
    min_interval_chapters: int | None = Field(
        default=None, description="同型爽点最小间隔章数。",
    )
    fatigue_risk: str | None = Field(default=None, description="疲劳管理说明。")
    mapped_tropes: list[str] = Field(default_factory=list, description="桥段映射。")
    verify_hint: str | None = Field(default=None, description="核销判定提示。")
    source: str | None = Field(default=None, description="curated / deconstructed:<书名>。")
    stale: bool | None = Field(default=None, description="热度漂移后的过期标记。")


class GenrePackPayload(BaseModel):
    """题材包 payload 的消费子集视图（7 段，除版本锚点外全可缺席）。

    - ``schema_version``：版本线锚点（缺失时按当前版本线补齐，便于读侧统一）；
    - ``structure_templates`` / ``pacing`` / ``style_constraints`` / ``critic_rubric``：
      结构化 dict 原样透传（内部字段由 schema 校验类型，消费侧按需取用）；
    - ``ratio_declarations``：配比声明（键=维度，值=0~1 份额）；
    - ``opening_rules``：黄金三章题材特化规则（P2；signing_check 消费）。
    """

    model_config = ConfigDict(extra="allow")

    schema_version: str = GENRE_PACK_SCHEMA_VERSION
    payoff_types: list[GenrePayoffType] = Field(default_factory=list)
    structure_templates: dict[str, Any] | None = None
    pacing: dict[str, Any] | None = None
    ratio_declarations: dict[str, float] | None = None
    style_constraints: dict[str, Any] | None = None
    opening_rules: list[dict[str, Any]] = Field(
        default_factory=list, description="黄金三章题材特化规则（P2）。",
    )
    critic_rubric: dict[str, Any] | None = Field(
        default=None, description="critic / deep_review 的题材审查要点（P2）。",
    )


class GenrePackCreate(BaseModel):
    """创建题材包请求体（POST /projects/{pid}/genre-packs）。

    ``pack_id`` 可显式指定（内容仓 slug，如 ``genre-male-quicktrans-v1``）；
    省略时由 service 生成 ``gp_<12hex>``。``payload`` 原样透传（jsonschema 校验权威）。
    """

    name: str = Field(..., min_length=1, max_length=200)
    genre_tag: str = Field(..., min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)
    pack_id: str | None = Field(
        default=None, max_length=120,
        description="显式 pack_id（内容仓 slug）；省略则生成 gp_<12hex>。",
    )
    source_path: str | None = Field(
        default=None, max_length=500,
        description="内容仓来源路径（审计用；软件层不解析）。",
    )


class GenrePackUpdate(BaseModel):
    """更新题材包请求体（PUT /genre-packs/{pack_id}）；提供 payload 时 version 自增。"""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    genre_tag: str | None = Field(default=None, min_length=1, max_length=80)
    payload: dict[str, Any] | None = None
    source_path: str | None = Field(default=None, max_length=500)


class GenrePackSummary(BaseModel):
    """题材包摘要投影（列表端点 / 绑定响应 / 预览）。

    派生字段（``payoff_type_count`` / ``structure_model`` / ``chapter_words_target``）
    从 payload 解析；缺字段/解析失败 → 计数 0 / None（不炸）。
    """

    pack_id: str
    name: str
    genre_tag: str
    version: int
    source_path: str | None = None
    created_at: str
    updated_at: str
    payoff_type_count: int = 0
    structure_model: str | None = None
    chapter_words_target: int | None = None
    bound_project_count: int = 0


class GenrePack(BaseModel):
    """题材包完整表示（GET /genre-packs/{pack_id}）。``payload`` 为解析后的 dict。"""

    pack_id: str
    name: str
    genre_tag: str
    version: int
    payload: dict[str, Any]
    source_path: str | None
    created_at: str
    updated_at: str
    bound_project_count: int = 0


def parse_payload(raw: Any) -> GenrePackPayload:
    """把 payload（dict / JSON 串 / 非法值）解析为 :class:`GenrePackPayload`。

    宽松语义：非 dict 输入 → 空 payload（各段缺席）；字段类型非法（如
    ``payoff_types`` 不是数组）→ 该段退化为空/None，不抛错。**这不是校验入口**——
    写路径的严格校验在 :func:`packages.core.genre.service.GenrePackService.validate_payload`。
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return GenrePackPayload()
    if not isinstance(raw, dict):
        return GenrePackPayload()

    try:
        return GenrePackPayload.model_validate(raw)
    except Exception:  # noqa: BLE001 —— 读侧容忍脏数据（不炸装配路径）
        return GenrePackPayload()
