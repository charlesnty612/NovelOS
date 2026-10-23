"""contract 测试族的共享**夹具原语**（2026-10-23 task-9 · C-1 模板族收敛）。

为什么有它
----------
十几个 contract 文件各自复制了一份「建项目 / 建章 / 建角色 / 建地点」的 HTTP 播种
代码（请求体逐字相同，只有**名字**不同）。本模块把这些请求收成**带名字参数的
原语**：各文件传自己的字面量 ⇒ **发出的请求逐字不变**（不引入新行为）。

纪律
----
* **只搬「请求」不搬「断言」**：断言一律留在各文件（期望值必须与被测对象同文件
  可读，见 ``_contract_runner.py`` 的第一铁律）。
* **只搬真正通用的**：各文件**特有**的播种（state_list 的 commit/delta/branch/run
  直插、workflow_run 的 mock 脚本、genre_quality 的 canon/报告直插）**留在原文件**
  —— 那些不是「富夹具」，是各域自己的数据形状。
* 本模块**零真实 LLM 调用**：只发管理类端点（项目 / 章 / 角色 / 地点）。
"""
from __future__ import annotations

from typing import Any

import httpx

from packages.core.ids import new_id, now_iso

from ._shared import _evidence

__all__ = [
    "build_state_delta",
    "seed_character",
    "seed_chapter",
    "seed_core_ids",
    "seed_location",
    "seed_project",
]


async def seed_project(c: httpx.AsyncClient, name: str) -> str:
    """建项目，返回 ``project_id``。"""
    r = await c.post("/api/projects", json={"name": name})
    r.raise_for_status()
    return r.json()["project_id"]


async def seed_chapter(
    c: httpx.AsyncClient, project_id: str, *, number: int = 1, title: str = "第一章"
) -> str:
    """建章，返回 ``chapter_id``。"""
    r = await c.post(
        f"/api/projects/{project_id}/chapters", json={"number": number, "title": title}
    )
    r.raise_for_status()
    return r.json()["chapter_id"]


async def seed_character(c: httpx.AsyncClient, project_id: str, name: str) -> str:
    """建角色，返回 ``character_id``。"""
    r = await c.post(f"/api/projects/{project_id}/characters", json={"name": name})
    r.raise_for_status()
    return r.json()["character_id"]


async def seed_location(c: httpx.AsyncClient, project_id: str, name: str) -> str:
    """建地点，返回地点 id（世界三实体的出参键是 ``id``，不是 ``location_id``）。"""
    r = await c.post(f"/api/projects/{project_id}/locations", json={"name": name})
    r.raise_for_status()
    return r.json()["id"]


async def seed_core_ids(
    c: httpx.AsyncClient,
    *,
    project_name: str,
    chapter_title: str = "第一章",
    character_name: str = "林昭",
    location_name: str = "客栈",
) -> dict[str, Any]:
    """「项目 + 章 + 角色 + 地点」四连播种，返回 id 包。

    各 contract 文件几乎都有这一段（字面量不同）⇒ 收成一处。
    """
    pid = await seed_project(c, project_name)
    cid = await seed_chapter(c, pid, number=1, title=chapter_title)
    char_id = await seed_character(c, pid, character_name)
    loc_id = await seed_location(c, pid, location_name)
    return {"pid": pid, "cid": cid, "char_id": char_id, "loc_id": loc_id}


# ---------------------------------------------------------------------------
# state-delta 构造器（两个 contract 文件逐字同形 ⇒ 收成一处）
# ---------------------------------------------------------------------------
# ⚠️ **产出必须与各文件原来的本地构造器逐字相同**（含**键序**）：这份 dict 是
# **HTTP 请求体**（``POST /api/projects/{pid}/deltas``）⇒ 键序变了就是请求变了。
# 两个调用方的差异只有两处，都用参数表达：
#   * ``created_by``：调用方各自的字面量；
#   * ``with_events``：写面要 ``new_events`` 非空（点亮 events_changed 段），
#     列表面要 ``new_events`` 为空（genesis 的 7 数组全空）。


def build_state_delta(
    chapter_id: str,
    character_id: str,
    prev_version: int,
    location: str,
    *,
    created_by: str,
    with_events: bool = False,
) -> dict:
    """一条能过 ``validate_delta`` 的完整 state-delta（7 数组齐备）。"""
    new_events: list[dict[str, Any]] = []
    if with_events:
        new_events = [
            {
                "change_id": new_id("ne"),
                "op": "add",
                "target_id": new_id("et"),
                "event_id": new_id("evt"),
                "type": "other",
                "participants": [character_id],
                "time": {"timeline_day": 1, "in_story_date": "辰时"},
                "confidence": 0.8,
                "evidence": _evidence(chapter_id),
                "risk_level": "LOW",
            }
        ]
    return {
        "delta_id": new_id("dl"),
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": new_id("wfr"),
        "previous_state_version": prev_version,
        "created_by": created_by,
        "created_at": now_iso(),
        "supersedes": None,
        "notes": None,
        "character_changes": [
            {
                "change_id": new_id("cc"),
                "op": "update",
                "target_id": character_id,
                "character_id": character_id,
                "facet": "state",
                "field": "state.location",
                "before": "Unknown",
                "after": location,
                "confidence": 0.9,
                "evidence": _evidence(chapter_id),
                "risk_level": "LOW",
            }
        ],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": new_events,
        "resolved_hooks": [],
        "new_hooks": [
            {
                "change_id": new_id("nh"),
                "op": "add",
                "target_id": new_id("ht"),
                "hook_id": new_id("hk"),
                "name": "三年之约",
                "importance": 0.8,
                "description": "埋线",
                "confidence": 0.8,
                "evidence": _evidence(chapter_id),
                "risk_level": "LOW",
            }
        ],
        "debt_changes": [
            {
                "change_id": new_id("dc"),
                "op": "add",
                "target_id": new_id("dt"),
                "debt_id": new_id("db"),
                "description": "欠他一条命",
                "status_after": "open",
                "severity_after": 0.8,
                "confidence": 0.8,
                "evidence": _evidence(chapter_id),
                "risk_level": "LOW",
            }
        ],
    }
