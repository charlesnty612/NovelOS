"""domain.world 数据模型（Sprint 1 + V2.0 Wave B 任务二）。

WorldService 三类实体（locations / factions / world_rules）共用同一 dataclass 形态；
差异在表名、ID 前缀、默认 visibility 与 DDL 列名（PK 列名 ``location_id`` /
``faction_id`` / ``world_rule_id``）。详见 ``service.py``。

V2.0 Wave B 任务二：locations / factions 加 ``aliases`` / ``inject_mode`` 两列（0010 迁移）。
world_rules 不加（PRD 视其为硬设定，常驻不参与触发）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
