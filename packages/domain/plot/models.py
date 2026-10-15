"""domain.plot 数据模型（Sprint 1）。

plot_events / timeline_events / relationships 三类实体的 dataclass 表示。

出参契约（G2，2026-10-15 批次（六））
----------------------------------
``PlotEventOut`` / ``TimelineEventOut`` / ``RelationshipOut`` 是 7 个 plot 端点的
``response_model``：``POST|GET|PATCH /events/{id}``、``POST /projects/{pid}/timeline``
（单体 4 个）＋ ``GET /projects/{pid}/events``、``GET /projects/{pid}/timeline``、
``GET /projects/{pid}/relationships``（列表 3 个，2026-10-15 批次（六）L1 组）。

字段形状**不是照源码读出来的，是量出来的**
--------------------------------------------
``response_model`` 会**过滤**未声明字段：少声明一个，响应就静默少一个，而功能测试常常
照旧全绿。故建模前用临时库 + 合成夹具跑通端点 dump 实际键集（方法见
``tests/api/test_ledger_plot_response_contract.py`` 与
``tests/api/test_ledger_plot_list_response_contract.py`` 的 docstring）。实测（2026-10-15）：

- ``PlotEventOut`` **13 键恒在**、``TimelineEventOut`` **8 键恒在**、
  ``RelationshipOut`` **9 键恒在**（2026-10-16 起；此前 7 键——见下一条的修复注记）；
- ⚠️ 三者都是 ``to_dict()`` 的**逐字镜像**，而**不是表列集的镜像**：DB 的
  ``plot_events`` 有 13 列，但列名是 ``cause_json / effects_json / participants_json /
  time_json``、主键是 ``event_id``；响应把它们解码成 ``cause / effects / participants /
  time`` 并把主键改名 ``id``。照表建模会写出四个永不命中的字段；
- ⚠️ ``relationships`` **2026-10-16 已修**：原先是「表 9 列、响应 7 键」的反向形态
  （``_row_to_relationship`` 只映射 7 个 ⇒ ``visibility`` / ``who_knows`` 被服务层丢弃），
  本文件曾照**实测的 7 键**建模并挂了一条会红的漂移告警。现在服务层补上映射，
  三者重新对齐为 9；漂移告警已**反转**成新事实，仍以 ``PRAGMA table_info`` 为基准
  （``test_relationships_list_covers_every_column``）——不把「谁都不能再悄悄少一列」
  这条守望一起删掉；
- 空值一律是 **``null`` 而非省略**（``location_id`` / ``introduced_chapter_id`` /
  ``who_knows`` / ``description`` / ``time_ref``）⇒ 声明为 ``X | None`` 且**不给默认值**；
- 端点上**一律不加** ``response_model_exclude_none``（加了会把那批 null 键抹掉）。

``type`` / ``status`` / ``visibility`` 建模为裸 ``str`` 而非 ``Literal``
----------------------------------------------------------------------
写路径（``PlotService``）与 DDL CHECK 都已保证枚举合法，但读路径还会撞上 observer
写透、备份导入、旧 seed 行。若这里用 ``Literal``，一条脏数据会**把 GET 变成 500**——
即「一条坏行让整个列表端点不可用」。透出字符串并让契约测试守枚举集合，是更稳的形状
（同 ``packages/core/arc/models.py::ArcVolumeRow.status`` 的取舍）。

⚠️ 这条取舍在 **2026-10-15 L1 组接列表端点后变得更贵**（留给 Lead 裁决，不自行扩大范围）：
``list[PlotEventOut]`` 是一条脏行就让**整个列表** 500 的形状，而单体端点只是那一条。
本仓的 ledger 侧（``Hook.status`` / ``Debt.status``）用的仍是 ``Literal``，同一形状的
风险已在那边存在（见交付汇报的「存疑点」）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

# plot_events.type 枚举（DDL CHECK 约束）
PLOT_EVENT_TYPES: tuple[str, ...] = (
    "revelation",
    "conflict",
    "decision",
    "encounter",
    "transition",
    "other",
)

# plot_events.status 枚举（DDL CHECK 约束）
PLOT_EVENT_STATUSES: tuple[str, ...] = (
    "planned",
    "recorded",
    "resolved",
    "abandoned",
)

# 复用 WorldService 同款 visibility 枚举（DDL CHECK 同结构）
VISIBILITY_VALUES: tuple[str, ...] = ("PUBLIC", "VISIBLE", "RESTRICTED", "HIDDEN")


@dataclass
class PlotEvent:
    """plot_events 表对应实体。"""

    id: str
    project_id: str
    type: str
    cause: list[str] = field(default_factory=list)        # 引用 event_id
    effects: list[str] = field(default_factory=list)      # 引用 event_id
    participants: list[str] = field(default_factory=list) # 引用 character_id
    location_id: str | None = None
    # 默认 time：与 PlotService._TIME_DEFAULT 保持一致
    time: dict[str, Any] = field(
        default_factory=lambda: {"timeline_day": 1, "in_story_date": None}
    )
    status: str = "planned"
    introduced_chapter_id: str | None = None
    visibility: str = "RESTRICTED"
    who_knows: list[str] | None = None
    # V3.1 P1-1.1：迁移 0013 在 DB 加 ``description TEXT`` 列，observer 写透路径
    # 已落该列内容，但领域模型/API 响应此前未暴露 → 修复 B1：补齐字段透出。
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "type": self.type,
            "cause": self.cause,
            "effects": self.effects,
            "participants": self.participants,
            "location_id": self.location_id,
            "time": self.time,
            "status": self.status,
            "introduced_chapter_id": self.introduced_chapter_id,
            "visibility": self.visibility,
            "who_knows": self.who_knows,
            "description": self.description,
        }


@dataclass
class TimelineEvent:
    """timeline_events 表对应实体（按 day_index 排序的时间线索引）。"""

    id: str
    project_id: str
    event_id: str
    day_index: int
    time_ref: str | None = None
    description: str | None = None
    # V3.1 P1-1.2：补 visibility/who_knows 伴随列；与 plot_events 同源（迁移 0019
    # 已对齐 DDL + 回填）。默认与 DDL DEFAULT 一致：'PUBLIC' / None。
    visibility: str = "PUBLIC"
    who_knows: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "event_id": self.event_id,
            "day_index": self.day_index,
            "time_ref": self.time_ref,
            "description": self.description,
            "visibility": self.visibility,
            "who_knows": self.who_knows,
        }


@dataclass
class Relationship:
    """relationships 表对应实体（Sprint 1 只读端点，写入由 S2 State Delta 驱动）。"""

    id: str
    project_id: str
    from_character_id: str
    to_character_id: str
    relation_type: str
    state: dict[str, Any] = field(default_factory=dict)
    last_state_version: int = 0
    # 迁移 0014 补的伴随列，0022 重建表时逐字保留。**2026-10-16 修复**：此前本类只
    # 声明 7 个字段且 ``to_dict`` 是 7 键硬编码白名单 ⇒ 这两列被服务层静默丢弃
    # （同文件的 TimelineEvent / PlotEvent 早在 V3.1 P1-1.2 就补齐了，唯独本类漏了）。
    # 默认值逐字对齐 DDL：0014 与 0022 都写 ``DEFAULT 'PUBLIC'``（**不是** plot_events
    # 的 'RESTRICTED'——两表不同，别互相"对齐"）。
    # ⚠️ 刻意**不**复用 ``PlotService._validate_visibility``：那校验的是**写**路径，
    # 而 relationships 是 S1 只读端点（写入在 story_state/write_through.py）。把写校验
    # 塞进读路径 = 一行脏 visibility 让整个列表 500，正是本文件 docstring 警告的形状。
    visibility: str = "PUBLIC"
    who_knows: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "from_character_id": self.from_character_id,
            "to_character_id": self.to_character_id,
            "relation_type": self.relation_type,
            "state": self.state,
            "last_state_version": self.last_state_version,
            "visibility": self.visibility,
            "who_knows": self.who_knows,
        }


# ---------------------------------------------------------------------------
# REST 出参模型（G2）
#
# 刻意与上面的 dataclass **分开**成两个类：dataclass 是**领域内部**表示（字段名带
# ``_json`` 后缀的解码在这里已经做完，但仍按领域语义组织），出参模型是**对外契约**
# （键名逐字等于响应 JSON）。合成一个类会让「领域字段改名」直接变成 API 破坏性变更。
# ---------------------------------------------------------------------------


class PlotEventOut(BaseModel):
    """``POST|GET|PATCH /api/events/{id}`` 的响应。

    ``extra="allow"``：``PlotEvent.to_dict`` 是手写白名单，将来加了键而忘了在本类
    声明时，``extra="ignore"`` 会让 ``response_model`` 把它静默裁掉——那正是本批次要
    消灭的「后端有、前端拿不到」缺口。代价见模块 docstring。
    """

    model_config = ConfigDict(extra="allow")

    # ⚠️ 键名是 ``id`` 不是 ``event_id``（DB 主键列名是 event_id，to_dict 改过名）。
    # 照 DDL 建模会写出一个永不命中的字段、并让真正的 ``id`` 静默缺席。
    id: str
    project_id: str
    # 裸 str 而非 Literal：一条脏数据不该让 GET 变 500（见模块 docstring）。
    type: str
    cause: list[str] = Field(description="引用的上游 event_id 列表（已从 cause_json 解码）")
    effects: list[str] = Field(description="影响的下游 event_id 列表（已从 effects_json 解码）")
    participants: list[str] = Field(description="参与角色 character_id 列表（已从 participants_json 解码）")
    location_id: Optional[str]
    time: dict[str, Any] = Field(description="已从 time_json 解码，形如 {timeline_day, in_story_date}")
    status: str
    introduced_chapter_id: Optional[str]
    visibility: str
    who_knows: Optional[list[str]] = Field(description="知情者 character_id 列表；NULL=沿用默认，[]=显式置空")
    description: Optional[str] = Field(description="迁移 0013 的列；本字段曾因「模型里没有」而对 API 隐形")


class TimelineEventOut(BaseModel):
    """``POST /api/projects/{pid}/timeline`` 的响应（``GET .../timeline`` 同款）。

    ⚠️ 主键键名同样是 ``id``（DB 列名 ``timeline_event_id``）。
    """

    model_config = ConfigDict(extra="allow")

    id: str
    project_id: str
    event_id: str
    day_index: int = Field(description="故事内第几天；DDL NOT NULL，恒在")
    time_ref: Optional[str] = Field(description="可选 HH:MM 等")
    description: Optional[str]
    visibility: str = Field(description="迁移 0014 伴随列，默认 'PUBLIC'（与 plot_events 的 'RESTRICTED' 不同）")
    who_knows: Optional[list[str]]


class RelationshipOut(BaseModel):
    """``GET /api/projects/{pid}/relationships`` 的响应行。

    **表 9 列 ≡ 响应 9 键 ≡ 模型 9 字段**（2026-10-16 修复后对齐）。

    ⚠️ **2026-10-16：这里原先是 7 字段**。``visibility`` / ``who_knows``（迁移 0014 加、
    0022 重建表时逐字保留）在服务层被丢弃，本模型照**实测的 7 键**建模，把缺口登记成
    会红的漂移告警。修复后三者重新对齐，「改名/解码」之外**不再有被丢弃的列**——
    漂移告警已反转成新事实（``test_relationships_list_covers_every_column``：
    以 ``PRAGMA table_info`` 为基准，9 列 − 2 个改名 = 7 个直呼键，
    再断言**响应里没有一列缺席**）。

    ⚠️ 遗留的「改名/解码」形态**没有**消失，两处照旧：
    ``relationship_id`` → ``id``、``state_json`` → ``state``（已解码）。
    所以「9 列 ≡ 9 键」是**键数相等**，不是**列名逐字相等**。
    """

    model_config = ConfigDict(extra="allow")

    # ⚠️ 键名是 ``id``（DB 主键列名 ``relationship_id``），同 PlotEventOut / TimelineEventOut。
    id: str
    project_id: str
    # 0022 之后端点**可以**是 ``faction_id``（不再有 characters 外键），故是裸 str。
    from_character_id: str
    to_character_id: str
    relation_type: str
    # 由 ``state_json`` 解码而来；DDL DEFAULT '{}' ⇒ 恒是对象，不会是 null。
    state: dict[str, Any] = Field(description="已从 state_json 解码，形如 {intensity, since_chapter}")
    last_state_version: int = Field(description="DDL NOT NULL；快照版本号")
    # ⚠️ 两字段都**不给默认值**（本文件惯例：可空列必填可空 ⇒ 进 schema 的 required）。
    # ⚠️ `visibility` 声明成裸 ``str`` 而非 ``Literal``：一条脏数据不该让整个列表 500
    # （见模块 docstring 同款取舍）。
    visibility: str = Field(description="迁移 0014 伴随列，默认 'PUBLIC'（与 plot_events 的 'RESTRICTED' 不同）")
    who_knows: Optional[list[str]] = Field(description="知情者 character_id 列表；NULL=沿用默认，[]=显式置空")


__all__ = [
    "PLOT_EVENT_STATUSES",
    "PLOT_EVENT_TYPES",
    "VISIBILITY_VALUES",
    "PlotEvent",
    "PlotEventOut",
    "Relationship",
    "RelationshipOut",
    "TimelineEvent",
    "TimelineEventOut",
]

