"""Canonical State 快照的**对外契约**（Pydantic）。

为什么要有这个模块
------------------
2026-10-15 之前，`GET /projects/{pid}/state` 的返回类型是裸 ``dict``——**没有**
``response_model``，于是 OpenAPI 里根本没有它的 schema。前端拿不到契约，只能在
``apps/web/src/api/types.ts`` 里手写一个欠定义类型：

    export interface SnapshotResponse {
      version?: number;          // ← 后端从不返回这个键
      state_version?: number;    // ← 后端真正返回的键
      characters?: unknown;
      ...
      [key: string]: unknown;
    }

两个互斥的版本命名、六个全 optional 的字段、一个吞掉一切类型错误的索引签名——
这不是契约，是「不知道后端返回什么」的兜底。**代价已经付出**（见
``apps/web/src/pages/ProjectOverviewPage.tsx``）：兜底类型逼出一层手写归一化，
而归一化把 ``characters``（后端是 list）用 ``normObj`` 拍成 ``{}``、把 ``events``
（后端是 dict）用 ``normArr`` 拍成 ``[]``，于是概览页「角色」与「事件·累计」
**两个统计项恒为 0**，且全程无任何告警。

字段形状由 ``snapshot.build_initial_state`` 与 ``snapshot.materialize_snapshot``
共同保证，并由 ``commits.rebuild_snapshot_collections_from_db`` 从领域表重放。
实测 133 条存量快照形状零违例（2026-10-15，只读查询），故此处用**严格类型**
而不是宽松 ``Any``——宽松类型只会把同一个坑重新挖一遍。

``extra="allow"``：故事状态是开放的 JSON 文档，delta 可以带额外集合键；契约描述
**已知的六个**，不宣称封闭（封闭的话每次加集合都要改契约 + regen 前端类型）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SnapshotResponse(BaseModel):
    """``GET /projects/{pid}/state`` 与 ``GET /projects/{pid}/state/versions/{n}`` 的响应。

    ``state_version`` 是**唯一**的版本字段名。后端 ``queries.get_current_state``
    与 ``queries.get_snapshot`` 都写 ``state_version``；前端历史上还并列过一个
    ``version``，是欠定义类型自己臆造的别名，已删除。
    """

    model_config = ConfigDict(extra="allow")

    state_version: int = Field(
        description="快照版本号；无快照时后端返回 0（genesis 未初始化前的形态）",
    )
    characters: list[dict[str, Any]] = Field(
        default_factory=list,
        description="角色列表（按 character_id ASC）",
    )
    world: dict[str, Any] = Field(
        default_factory=dict,
        description="世界观：{locations: dict, factions: dict, world_rules: list}",
    )
    hooks: list[dict[str, Any]] = Field(
        default_factory=list,
        description="伏笔列表（按 hook_id ASC）",
    )
    debts: list[dict[str, Any]] = Field(
        default_factory=list,
        description="叙事债务列表（按 debt_id ASC）",
    )
    recent_events: list[Any] = Field(
        default_factory=list,
        description="最近事件（时间线视角）",
    )
    events: dict[str, Any] = Field(
        default_factory=dict,
        description="剧情事件，**按 event_id 键的字典**（不是列表）",
    )


__all__ = ["SnapshotResponse"]
