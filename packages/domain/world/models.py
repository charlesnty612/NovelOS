"""domain.world 数据模型（Sprint 1 + V2.0 Wave B 任务二）。

WorldService 三类实体（locations / factions / world_rules）共用同一 dataclass 形态；
差异在表名、ID 前缀、默认 visibility 与 DDL 列名（PK 列名 ``location_id`` /
``faction_id`` / ``world_rule_id``）。详见 ``service.py``。

V2.0 Wave B 任务二：locations / factions 加 ``aliases`` / ``inject_mode`` 两列（0010 迁移）。
world_rules 不加（PRD 视其为硬设定，常驻不参与触发）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# visibility 枚举（DDL CHECK 约束）
VISIBILITY_VALUES: tuple[str, ...] = ("PUBLIC", "VISIBLE", "RESTRICTED", "HIDDEN")
# V2.0 Wave B 任务二：注入模式（与 0010 DDL CHECK 对齐）
INJECT_MODES: tuple[str, ...] = ("auto", "always", "never")


@dataclass
class WorldEntity:
    """三类世界实体的统一表示。

    字段含义：
    - ``id_field``: DDL 中的主键列名（``location_id`` / ``faction_id`` / ``world_rule_id``）
    - ``id_prefix``: ID 前缀（``loc`` / ``fac`` / ``wrule``）
    - ``default_visibility``: DDL 默认值（locations/world_rules 为 PUBLIC，factions 为 VISIBLE）
    - ``name``: 实体名
    - ``statement``: 一句话陈述
    - ``data``: ``data_json`` 列对应的 Python 对象（dict/list 等可序列化值）
    - ``visibility``: 权限级别
    - ``who_knows``: 可见者 ID 列表；None 表示沿用默认
    - ``aliases`` / ``inject_mode``: V2.0 Wave B 任务二（仅 locations/factions 使用）；
      world_rules 始终走默认值。
    - ``created_at`` / ``updated_at``: UTC ISO-8601
    """

    id: str
    id_field: str
    id_prefix: str
    project_id: str
    name: str
    statement: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    visibility: str = "PUBLIC"
    who_knows: list[str] | None = None
    aliases: list[str] = field(default_factory=list)
    inject_mode: str = "auto"
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """序列化为 API 友好字典（``*_json`` 列对应字段保持 JSON 字符串风格不展开）。"""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "statement": self.statement,
            "data": self.data,
            "visibility": self.visibility,
            "who_knows": self.who_knows,
            # V2.0 Wave B 任务二：触发键字段
            "aliases": list(self.aliases),
            "inject_mode": self.inject_mode,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# Pydantic 视图（2026-10-15 批次（六）补契约）
# ---------------------------------------------------------------------------
# 上面那个 ``WorldEntity`` 是 **dataclass**（service 层在用），router 拿它当
# ``response_model`` 是用不了的。这里补一个 Pydantic 模型专供 API 契约，
# **不替换** dataclass：换掉会动到 service 层与 ``WorldService`` 的全部调用点，
# 收益不抵风险（同 ``packages/domain/knowledge/models.py::RevealPolicyOut`` 的先例）。
#
# 字段形状**不是照 dataclass 抄的，是量出来的**
# ------------------------------------------------
# ``response_model`` 会**过滤**未声明字段：少声明一个，响应就静默少一个，而
# 功能测试常常照旧全绿。所以建模前先用临时库 + 合成夹具跑通 9 个端点、把实际
# 返回的键集 dump 出来。实测结论（2026-10-15，``/locations`` ``/factions``
# ``/world-rules`` 各 3 个端点，POST / GET / PATCH 全覆盖）：
#
# - **11 个键恒在，9 个端点 + 3 个列表端点逐字相同**，一个都不省略 ⇒
#   **不需要** ``response_model_exclude_none``（加了反而会吃掉 ``who_knows``）。
# - ``who_knows`` 无数据时是 **null 而非省略** ⇒ 必填可空且**不给默认值**
#   （给默认值会生成成 ``who_knows?:``，即「可省略」，与实测不符，
#   前端就得写 ``?? []`` 兜底——那正是 G10「欠定义类型」的病）。
# - ``data`` 实测两种形态都出现：默认 ``{}``，写入数组时是 ``[1, "x"]``
#   ⇒ 声明为 ``dict[str, Any] | list[Any]``。
# - ``aliases`` / ``statement`` / ``data`` **恒非 null**（分别至少 ``[]`` /
#   ``""`` / ``{}``），别按可空建模。
#
# ⚠️ 三类实体**共用一个模型**是刻意的：service 层本来就把它们统一成同一个
# dataclass 形态（见模块 docstring），实测键集也逐字相同。拆成三个同形模型
# 只会制造三份可各自漂移的副本。
#
# ⚠️ **world_rules 的 ``aliases`` / ``inject_mode`` 恒为 ``[]`` / ``"auto"``**：
# 实测 POST 与 PATCH 带上这两个键都会被静默吞掉（service 对 world_rules 不写
# 这两列）。这是**既有行为**，不是本次引入；测试里以「实测形态」钉住，
# 是否该改成 422 拒绝见 ``tests/api/test_world_response_contract.py`` 的同名用例。

# visibility 枚举（0010 DDL CHECK 对齐，与 VISIBILITY_VALUES 同源）
WorldVisibilityLiteral = Literal["PUBLIC", "VISIBLE", "RESTRICTED", "HIDDEN"]
# 注入模式（0010 DDL CHECK 对齐，与 INJECT_MODES 同源）
WorldInjectModeLiteral = Literal["auto", "always", "never"]


class WorldEntityOut(BaseModel):
    """``locations`` / ``factions`` / ``world_rules`` 三类实体的**出参**形态。

    = ``WorldEntity.to_dict()`` 的 11 个键（实测，非照抄）。
    """

    model_config = ConfigDict(extra="allow")

    id: str
    project_id: str
    name: str
    statement: str = Field(description="一句话陈述；未填时为空串（非 null）")
    data: dict[str, Any] | list[Any] = Field(
        description="data_json 列的反序列化结果；未填时为 {}，也可能是数组",
    )
    visibility: WorldVisibilityLiteral = Field(description="权限级别")
    # ⚠️ 这 11 个键**恒在**（实测），所以一律不给 default。可空用 `X | None` 表达，
    # 「可省略」才是 `= None` —— 后者会把契约说成比实际更松。
    who_knows: list[str] | None = Field(description="可见者 ID 列表；null = 全员可见")
    aliases: list[str] = Field(
        description="别名列表；恒为数组（world_rules 恒为 []，见模块注释）",
    )
    inject_mode: WorldInjectModeLiteral = Field(
        description="触发注入模式（world_rules 恒为 'auto'，见模块注释）",
    )
    created_at: str
    updated_at: str
