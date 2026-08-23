"""domain.plot 数据模型（Sprint 1）。

plot_events / timeline_events / relationships 三类实体的 dataclass 表示。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "event_id": self.event_id,
            "day_index": self.day_index,
            "time_ref": self.time_ref,
            "description": self.description,
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "from_character_id": self.from_character_id,
            "to_character_id": self.to_character_id,
            "relation_type": self.relation_type,
            "state": self.state,
            "last_state_version": self.last_state_version,
        }
