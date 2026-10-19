"""skill（内部消费外部 skill）Pydantic 模型。

职责与边界：
- 描述 ``skills`` 表行（:class:`Skill`）与其摘要投影（:class:`SkillSummary`）；
- 描述 payload 的**软件层消费子集**结构（:class:`SkillPayload` /
  :class:`SkillReference` / :class:`SkillCheck`）——只承载「可移植的通用手艺」，
  **不**承载项目专属设定、角色、剧情（那些属内容仓编辑面与项目级设定）；
- 定义 payload 的 schema 版本线常量（v1.0.0）与 schema 文件路径供校验器引用。

与题材包（``packages/core.genre.model``）的分工：
- 题材包是**题材公约**（规定性：爽点分类法 / 配比 / 节奏红线 / 核销口径）；
  skill 是**手艺说明**（外部可移植的「怎么做」）。两者不共用表、不共用 schema
  版本线、不共用注入段（skill 走**独立新段** ``payload["skills"]``，绝不塞进
  ``genre_pack`` / ``style_constraints`` 任何既有段）。
- 严格口径（键集合、长度上限、枚举取值）由 schema 文件负责；本模块的 pydantic
  模型是**宽松视图**（类型提示 + 取值抽取），**不做第二套强校验**——避免两套规则
  漂移后互相打脸（同 genre 的取舍）。

``extra="allow"``：读侧宽松（导入 / 历史数据的未知键原样保留）；**写侧的严格性
由 jsonschema 承担**（``additionalProperties: false``），不是这里。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "SKILL_APPLIES_TO_CONSUMERS",
    "SKILL_CHECK_ID_PATTERN",
    "SKILL_ID_PATTERN",
    "SKILL_SCHEMA_PATH",
    "SKILL_SCHEMA_VERSION",
    "Skill",
    "SkillCheck",
    "SkillCreate",
    "SkillPayload",
    "SkillReference",
    "SkillSummary",
    "SkillUpdate",
    "parse_payload",
]

# payload schema 版本线（与 docs/state-model/schemas/skill.schema.json 的
# ``schema_version.pattern`` 对齐：``^skill\.v1\.[0-9]+\.[0-9]+$``）。
SKILL_SCHEMA_VERSION = "skill.v1.0.0"

# schema 文件（jsonschema 校验权威）。路径以仓库根为基准，与
# packages/core/genre/model.py 同款口径。
SKILL_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "state-model"
    / "schemas"
    / "skill.schema.json"
)

#: 装配可注入的环节白名单（与 schema 的 ``applies_to`` 枚举逐字一致）。
#: ⚠️ 两侧必须同形：schema 放行而装配不认 ⇒ skill 静默不生效（死资源）。
SKILL_APPLIES_TO_CONSUMERS: tuple[str, ...] = (
    "director",
    "scene_planner",
    "writer",
    "critic",
    "deep_reviewer",
)

#: skill_id 允许的字符集（ASCII 字母数字与 ``. _ -``）。
#: ⚠️ **为什么限死**：缓存键指纹 ``skills_fp`` 用 ``skill_id:version`` 拼串
#: 再取分隔符切分（见 ``packages/core.context_engine.cache._fingerprint_skills``），
#: 字符集里出现分隔符会让切分产生歧义 ⇒ 主 JOIN 与降级路径可能算出**不同的**键
#: 形态 ⇒ 脏命中（硬规则 2）。限死字符集是让「键形态逐字同形」成立的前提。
SKILL_ID_PATTERN = r"^[A-Za-z0-9._-]{1,120}$"

#: check_id 允许的字符集（小写字母数字与 ``. _ -``）——与 schema 同款。
#: 命中时落库的 rule_id 恒为 ``SKILL-<check_id>``。
SKILL_CHECK_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"


class SkillReference(BaseModel):
    """payload.references[] 条目（参考材料）。

    ⚠️ **不是渐进披露**：本仓模型没有 tool use，模型无法按需读取 ⇒ 装配期全量注入。
    """

    model_config = ConfigDict(extra="allow")

    path: str = ""
    title: str = ""
    body: str = ""


class SkillCheck(BaseModel):
    """payload.checks[] 条目（确定性检查项）。

    ``keywords`` 缺席 = 不做确定性扫描（只作为声明随段注入给模型看）。
    """

    model_config = ConfigDict(extra="allow")

    check_id: str = ""
    description: str = ""
    keywords: list[str] = Field(default_factory=list)


class SkillPayload(BaseModel):
    """skill payload 的消费子集视图（除版本锚点外全可缺席）。

    - ``schema_version``：版本线锚点（缺失时按当前版本线补齐，便于读侧统一）；
    - ``instructions``：手艺本体（必填）；
    - ``applies_to``：适用环节白名单；**缺席 = 不限定范围 = 全部环节适用**；
    - ``references`` / ``checks``：可选段。
    """

    model_config = ConfigDict(extra="allow")

    schema_version: str = SKILL_SCHEMA_VERSION
    name: str = ""
    description: str = ""
    instructions: str = ""
    applies_to: list[str] = Field(default_factory=list)
    references: list[SkillReference] = Field(default_factory=list)
    checks: list[SkillCheck] = Field(default_factory=list)


class SkillCreate(BaseModel):
    """创建 skill 请求体（POST /api/skills）。

    ``skill_id`` 可显式指定（内容仓 slug）；省略时由 service 生成 ``skl_<12hex>``。
    ``payload`` 原样透传（jsonschema 校验权威）。
    """

    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    skill_id: str | None = Field(
        default=None,
        max_length=120,
        pattern=SKILL_ID_PATTERN,
        description="显式 skill_id（内容仓 slug）；省略则生成 skl_<12hex>。",
    )
    source_path: str | None = Field(
        default=None, max_length=500,
        description="内容仓来源路径（审计用；软件层不解析）。",
    )


class SkillUpdate(BaseModel):
    """更新 skill 请求体（PUT /api/skills/{skill_id}）；提供 payload 时 version 自增。"""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    payload: dict[str, Any] | None = None
    source_path: str | None = Field(default=None, max_length=500)


class SkillSummary(BaseModel):
    """skill 摘要投影（列表端点 / 绑定集合）。

    派生字段 ``applies_to`` 从 payload 解析；缺字段 / 解析失败 → 空列表（不炸）。
    """

    skill_id: str
    name: str
    description: str | None = None
    version: int
    source_path: str | None = None
    created_at: str
    updated_at: str
    applies_to: list[str] = Field(default_factory=list)
    reference_count: int = 0
    check_count: int = 0
    bound_project_count: int = 0


class Skill(BaseModel):
    """skill 完整表示（GET /api/skills/{skill_id}）。``payload`` 为解析后的 dict。

    ⚠️ **``payload.references` 不是渐进披露**：本仓模型**没有 tool use**，无法「按需
    读取」——references 在**装配期**随 instructions 一起注入（受段字符预算约束）。
    API 消费方**不应**假定「模型能自己取参考材料」，也不应假定未出现在 payload 里的
    内容对模型可见。
    """

    skill_id: str
    name: str
    description: str | None = None
    version: int
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "运行面 payload（schema：docs/state-model/schemas/skill.schema.json）。"
            "⚠️ references 不是渐进披露——模型无 tool use，装配期全量注入。"
        ),
    )
    source_path: str | None = None
    created_at: str
    updated_at: str
    bound_project_count: int = 0


def parse_payload(raw: Any) -> SkillPayload:
    """把 payload（dict / JSON 串 / 非法值）解析为 :class:`SkillPayload`。

    宽松语义：非 dict 输入 → 空 payload（各段缺席）；字段类型非法 → 该段退化为
    空 / None，不抛错。**这不是校验入口**——写路径的严格校验在
    :func:`packages.core.skill.service.validate_payload`（jsonschema 权威）。
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return SkillPayload()
    if not isinstance(raw, dict):
        return SkillPayload()

    try:
        return SkillPayload.model_validate(raw)
    except Exception:  # noqa: BLE001 —— 读侧容忍脏数据（不炸装配路径）
        return SkillPayload()
