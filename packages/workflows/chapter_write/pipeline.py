"""chapter_write 工作流（Sprint 4-A）。

节点列表：
- ``load_plan`` (Transform) —— 读 chapters.plan_json 准备 director_plan 输入。
- ``scene_planner_stub`` (Transform) —— MVP 占位：把 director.key_beats 逐个映射为 scene,
  每 scene slots=[{slot_id, type}] 机械生成。**V1 由 Planner Agent 替代**。
- ``writer`` (AI) —— 调 writer agent 生成本章 prose。
- ``save_draft`` (State) —— 写 drafts 表 + chapters.status PLANNED→DRAFTED。
"""

from __future__ import annotations

import json
from typing import Any

from packages.core.agent_runtime.runner import run_agent
from packages.core.context_engine import build_writer_input
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.workflow_runtime.engine import WorkflowNode


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


def _scene_planner_stub(ctx: dict[str, Any]) -> dict[str, Any]:
    """MVP 占位：把 director.key_beats 逐个映射为 scene；每 scene slots=[1 个 action]。

    **V1 由 Planner Agent 替代**（参见 ``docs/agents/agent-contracts-v0.md`` Open Question §1）。
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
                "purpose": "本章内容（V1 由 Planner Agent 替代）",
                "characters": [],
                "location": None,
                "conflict": "",
                "turn": "",
                "time_in_story": "",
                "pov": "third_person_limited",
                "pov_character_id": None,
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


def _writer_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    chapter_id = ctx["chapter_id"]
    scene_plan = ctx["scene_plan"]
    payload = build_writer_input(
        db_path,
        chapter_id,
        scene_plan,
        target_word_count=ctx.get("target_word_count", 2200),
    )
    mock_script = (ctx.get("mock_providers") or {}).get("writer")
    out = run_agent(
        db_path,
        "writer",
        payload,
        run_id,
        node_run_id=ctx.get("_current_node_run_id"),
        expected="writer",
        mock_script=mock_script,
    )
    return {"writer_output": out}


def _save_draft_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    run_id = ctx["run_id"]
    writer_output = ctx.get("writer_output") or {}
    prose = writer_output.get("prose") or ""
    self_report = writer_output.get("self_report") or {}
    word_count = int(self_report.get("word_count") or len(prose))
    prompt_version = writer_output.get("prompt_version") or "writer:v1"

    draft_id = new_id("dr")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        # 先看是否有 draft 行（按 chapter + version 累计）
        max_row = conn.execute(
            "SELECT MAX(version) AS v FROM drafts WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        next_v = (max_row["v"] or 0) + 1
        conn.execute(
            """
            INSERT INTO drafts (draft_id, chapter_id, version, content, created_by,
                                prompt_version, model_id, created_at)
            VALUES (?, ?, ?, ?, 'writer:v1', ?, 'mock/mock', ?)
            """,
            (draft_id, chapter_id, next_v, prose, prompt_version, now),
        )
        # chapters.status PLANNED→DRAFTED（走白名单）
        cur = conn.execute("SELECT status FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
        if cur is None:
            raise ValueError(f"chapter {chapter_id!r} not found")
        status = cur["status"]
        if status == "PLANNED":
            conn.execute(
                "UPDATE chapters SET status = 'DRAFTED', updated_at = ? WHERE chapter_id = ?",
                (now, chapter_id),
            )
        elif status != "DRAFTED":
            # 与 chapter_commit 硬校验口径一致：仅 PLANNED / DRAFTED 允许 write；
            # DRAFTED 重跑允许追加新 draft 版本（支撑人工改稿循环）。
            raise ValueError(
                f"chapter {chapter_id} status={status} 不允许 write，仅 PLANNED/DRAFTED 可写"
            )
        conn.commit()
    finally:
        conn.close()
    return {
        "draft_id": draft_id,
        "draft_version": next_v,
        "word_count": word_count,
        "prompt_version": prompt_version,
        "chapter_id": chapter_id,
        "workflow_run_id": run_id,
    }


def _build_nodes() -> list[WorkflowNode]:
    return [
        WorkflowNode("load_plan", "Transform", _load_plan_node),
        WorkflowNode("scene_planner_stub", "Transform", _scene_planner_stub),
        WorkflowNode("writer", "AI", _writer_node, agent_name="writer"),
        WorkflowNode("save_draft", "State", _save_draft_node),
    ]


WORKFLOW = {
    "name": "chapter-write",
    "version": "v1",
    "description": "Director Plan + Scene Planner stub → Writer prose → drafts table; chapter status PLANNED→DRAFTED",
    "nodes": _build_nodes(),
}


def register_workflow(workflow: dict[str, Any] = WORKFLOW) -> None:
    from packages.workflows import register_workflow as _register

    _register(workflow)


__all__ = ["WORKFLOW", "register_workflow"]
