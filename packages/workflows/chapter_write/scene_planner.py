"""chapter_write · scene_planner 段（2026-09-27 B-1 拆分自 pipeline.py）。

含 load_plan / scene_planner 的辅助：_load_plan_node / _scene_planner_fallback /
_collect_scene_planner_inputs。同包内搬移，逻辑逐字不变。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from packages.core.context_engine.builders import (
    _REFERENCE_CANON_CONSUMER_PLANNER,
    _reference_canon_excerpt,
)
from packages.core.context_engine.builders_common import (
    _GENRE_PACK_CONSUMER_PLANNER,
    _genre_pack_excerpt,
)
from packages.core.db import get_connection
from packages.core.genre.target_words import resolve_target_word_count
from packages.workflows.chapter_plan.planner_input import collect_planner_context

_log = logging.getLogger(__name__)
def _load_plan_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"chapter {chapter_id!r} not found")
    plan_json_raw = row["plan_json"]
    plan_json = json.loads(plan_json_raw) if plan_json_raw else {}
    if not plan_json.get("chapter_goal"):
        raise ValueError(
            f"chapter {chapter_id!r} has empty plan_json; run chapter-plan first"
        )
    return {"loaded_plan": plan_json}


_SCENE_PLANNER_PROMPT_VERSION = "scene_planner:v1"


def _scene_planner_fallback(ctx: dict[str, Any]) -> dict[str, Any]:
    """降级路径：把 director.key_beats 逐个映射为 scene；每 scene slots=[1 个 action]。

    字段与 AI scene_planner 输出保持一致（scene_id / purpose / characters / location /
    conflict / turn / time_in_story / pov / pov_character_id / slots），下游
    build_writer_input 无需改动。
    """
    plan = ctx.get("loaded_plan") or {}
    beats = plan.get("key_beats") or []
    scenes: list[dict[str, Any]] = []
    for i, beat in enumerate(beats, start=1):
        if not isinstance(beat, dict):
            continue
        scene_id = f"scene_{i:03d}"
        slot_id = f"slot_{i:03d}"
        scenes.append(
            {
                "scene_id": scene_id,
                "purpose": beat.get("purpose") or "",
                "characters": beat.get("involved_characters", []),
                "location": (beat.get("involved_locations") or [None])[0],
                "conflict": "",
                "turn": "",
                "time_in_story": "",
                "pov": "third_person_limited",
                "pov_character_id": None,
                "information_boundary": [],
                "ending_hook": None,
                "slots": [
                    {
                        "slot_id": slot_id,
                        "type": "action",
                        "purpose": beat.get("purpose") or "",
                        "characters": beat.get("involved_characters", []),
                        "target_mood": None,
                        "constraints": [],
                    }
                ],
            }
        )
    if not scenes:
        # 兜底：若 key_beats 为空则建 1 个空 scene
        scenes = [
            {
                "scene_id": "scene_001",
                "purpose": "本章内容（Director plan 未提供 beats，兜底规划）",
                "characters": [],
                "location": None,
                "conflict": "",
                "turn": None,
                "time_in_story": "",
                "pov": "third_person_limited",
                "pov_character_id": None,
                "information_boundary": [],
                "ending_hook": None,
                "slots": [
                    {
                        "slot_id": "slot_001",
                        "type": "action",
                        "purpose": "本章主要内容",
                        "characters": [],
                        "target_mood": None,
                        "constraints": [],
                    }
                ],
            }
        ]
    return {"scene_plan": {"scenes": scenes}}


def _collect_scene_planner_inputs(
    db_path: str, chapter_id: str, plan: dict[str, Any]
) -> dict[str, Any]:
    """组装 scene_planner agent 输入（最小可运行口径）。

    - chapter 元信息、director_plan 来自 load_plan。
    - available_characters / available_locations / style_constraints / recent_prose 经
      :func:`...chapter_plan.planner_input.collect_planner_context` 取（P1 规划合并后与
      chapter-plan 的 director_planner 装配**同源同口径**——两条链的角色 / 地点 / 文风
      清单不得漂移）。
    - 题材库 P1b：项目绑定题材包时注入 ``genre_pack`` 段（爽点类型摘要 + 配比声明 +
      配比指令，见 :func:`...builders_common._genre_pack_excerpt` 的 scene_planner
      consumer 口径）+ ``_genre_pack_consumed`` 溯源审计；未绑定 → 两键都不出现。
    """
    context = collect_planner_context(db_path, chapter_id)
    project_id = context["project_id"]

    # Reference Canon 注入：scene_planner 消费 emotion_curve（张力曲线）+ payoff_list
    # （Scene 级爽点排布）。与 director / writer 共用同一 excerpt 函数（consumer=planner
    # 走 emotion_curve + payoff_list 分支）。无 active canon → 不注入 reference_canon 键，
    # 与 director 缺席语义保持一致。
    reference_canon_inject: dict | None = None
    reference_canon_audit: dict | None = None
    if project_id:
        conn3 = get_connection(db_path)
        try:
            reference_canon_inject, reference_canon_audit = _reference_canon_excerpt(
                conn3, project_id, consumer=_REFERENCE_CANON_CONSUMER_PLANNER,
            )
        except sqlite3.OperationalError:
            # 老库 / 缺表 → 视为无 canon，不阻断 Scene Planner 装配。
            reference_canon_inject = None
            reference_canon_audit = None
        finally:
            conn3.close()

    # 题材库 P1b：scene_planner 消费题材包（规划期硬约束）——
    # payoff_types（爽点类型摘要 + 密度约束文本化，≤1500 字符预算，
    # 超限打 ``__genre_pack_truncated__``）+ ratio_declarations（配比声明）+
    # 由其派生的 ``ratio_instruction``（逐 scene 标注 scene_type + 字数分摊遵守配比）。
    # 未绑定 / 题材包未声明相应段 → 不注入该键（与 director 缺席语义一致）。
    genre_pack_inject: dict | None = None
    genre_pack_audit: dict | None = None
    if project_id:
        conn_g = get_connection(db_path)
        try:
            genre_pack_inject, genre_pack_audit = _genre_pack_excerpt(
                conn_g, project_id, consumer=_GENRE_PACK_CONSUMER_PLANNER,
            )
        finally:
            conn_g.close()
        # ⚠️ 2026-10-16 批次九：此处的 ``except sqlite3.OperationalError`` 已删——
        # 降级语义由 ``_genre_pack_excerpt`` 内部唯一承载（缺表/缺列 → 未绑定），
        # 外层再吞一次会把内层刚收窄的范围放大（锁 / I/O 故障变静默丢题材包）。

    # world_state_excerpts：复用既有字段结构（P2-补洞），让 Scene Planner 能看到
    # world_rules_relevant（项目级硬设定，如原著要素锁），保证"沿传 Director 约束"
    # 不会因本节点缺 world_rules 而退化为凭印象。L0 world_rules 全量常驻，与
    # director/writer 装配口径保持一致。无项目 / 异常 → 返回最小结构，不阻断。
    world_state_excerpts: dict[str, Any] = {
        "current_time_in_story": None,
        "current_location": None,
        "locations": [],
        "active_factions": [],
        "world_rules_relevant": [],
        "sensory_anchors": [],
    }
    if project_id:
        conn2 = get_connection(db_path)
        try:
            rule_rows = conn2.execute(
                "SELECT world_rule_id, name, statement FROM world_rules "
                "WHERE project_id = ? ORDER BY world_rule_id ASC",
                (project_id,),
            ).fetchall()
            world_state_excerpts["world_rules_relevant"] = [
                {
                    "world_rule_id": r["world_rule_id"],
                    "name": r["name"],
                    "statement": r["statement"],
                }
                for r in rule_rows
            ]
        except sqlite3.OperationalError:
            # 老库 / 缺表 → 保留空列表，不阻断 Scene Planner 装配。
            pass
        finally:
            conn2.close()

    payload: dict[str, Any] = {
        "agent": "scene_planner",
        "prompt_version": _SCENE_PLANNER_PROMPT_VERSION,
        "chapter": {
            "chapter_id": chapter_id,
            "title": context["chapter_title"],
            "number": context["chapter_number"],
            # plan_json 存的是 expected_word_count；target_word_count 为兼容旧字段。
            # 兜底走共享解析（题材包 pacing.chapter_words.target → 全仓默认），
            # 与 writer / length_check / review 三处同链同值（target 题材包化）。
            "target_word_count": resolve_target_word_count(
                db_path, chapter_id, plan
            ),
            "expected_role": plan.get("expected_role"),
        },
        "director_plan": {
            "chapter_goal": plan.get("chapter_goal"),
            "core_conflict": plan.get("core_conflict"),
            "turning_point": plan.get("turning_point"),
            "key_beats": plan.get("key_beats") or [],
            "notes_for_planner": plan.get("notes_for_planner"),
            "revision_note": plan.get("revision_note"),
        },
        "available_characters": context["available_characters"],
        "available_locations": context["available_locations"],
        "world_state_excerpts": world_state_excerpts,
        "style_constraints": context["style_constraints"],
        "recent_prose": context["recent_prose"],
    }

    # Reference Canon（emotion_curve + payoff_list）。无 canon 时不注入该键——
    # 与 director 缺席语义保持一致（scene_planner 不得索要 canon）。
    if reference_canon_inject is not None:
        payload["reference_canon"] = reference_canon_inject
    if reference_canon_audit is not None:
        payload["_reference_canon_consumed"] = reference_canon_audit
    # 题材库 P1b：题材包注入段（爽点摘要 + 配比声明 + 配比指令）+ 溯源审计
    # （与 reference_canon 双 slot 并存互不覆盖；无题材包时两键都不出现）。
    if genre_pack_inject is not None:
        payload["genre_pack"] = genre_pack_inject
    if genre_pack_audit is not None:
        payload["_genre_pack_consumed"] = genre_pack_audit
    return payload
