"""chapter_plan 工作流（Sprint 4-A）。

节点列表：
- ``build_ctx`` (Transform) —— 调 :func:`packages.core.context_engine.build_director_input`
  组装 director input。
- ``director`` (AI) —— 调 :func:`packages.core.agent_runtime.runner.run_agent` 跑 director。
  mock_providers 透传自 ctx。
- ``save_plan`` (State) —— 写 chapters.plan_json（director 输出）；chapters.status 不变（保持 PLANNED）。

返回 ``run_id``（由 WorkflowEngine.start_with_nodes 给出）。
"""

from __future__ import annotations

import json
from typing import Any

from packages.core.agent_runtime.runner import run_agent
from packages.core.context_engine import build_director_input
from packages.core.db import get_connection
from packages.core.workflow_runtime.engine import WorkflowNode


def _build_ctx_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """Transform：组装 director input（不调 LLM）。"""
    db_path = ctx["db_path"]
    project_id = ctx["project_id"]
    chapter_id = ctx["chapter_id"]
    intent = ctx.get("author_intent") or ""
    payload = build_director_input(
        db_path,
        project_id,
        chapter_id,
        intent,
        target_word_count=ctx.get("target_word_count", 2200),
    )
    payload["chapter"]["expected_role"] = ctx.get("expected_role", "setup")
    return {"director_input": payload}


def _director_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """AI：调 director agent。"""
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    payload = ctx["director_input"]
    mock_script = (ctx.get("mock_providers") or {}).get("director")
    out = run_agent(
        db_path,
        "director",
        payload,
        run_id,
        node_run_id=ctx.get("_current_node_run_id"),
        expected="director",
        mock_script=mock_script,
    )
    return {"director_output": out}


def _save_plan_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """State：把 director 输出写入 chapters.plan_json（status 不动）。"""
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    director_output = ctx.get("director_output") or {}
    plan_payload: dict[str, Any] = {
        "chapter_goal": director_output.get("chapter_goal"),
        "core_conflict": director_output.get("core_conflict"),
        "turning_point": director_output.get("turning_point"),
        "expected_role": director_output.get("expected_role"),
        "key_beats": director_output.get("key_beats", []),
        "character_changes_planned": director_output.get("character_changes_planned", []),
        "information_releases": director_output.get("information_releases", []),
        "hook_handling": director_output.get("hook_handling", []),
        "debt_handling": director_output.get("debt_handling", []),
        "proposed_new_entities": director_output.get("proposed_new_entities", []),
        "deviations": director_output.get("deviations", []),
        "knowledge_leakage_check": director_output.get("knowledge_leakage_check", {}),
        "open_questions": director_output.get("open_questions", []),
        "notes_for_planner": director_output.get("notes_for_planner"),
        "schema_version": director_output.get("schema_version"),
        "prompt_version": director_output.get("prompt_version"),
    }

    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapters SET plan_json = ?, updated_at = ? WHERE chapter_id = ?",
            (json.dumps(plan_payload, ensure_ascii=False), _now(), chapter_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"plan_saved": True, "plan_payload": plan_payload}


def _now() -> str:
    from packages.core.ids import now_iso

    return now_iso()


def _build_nodes() -> list[WorkflowNode]:
    """构造节点列表；AI 节点的 fn 接受 ctx 但需读取 ``_current_node_run_id``——
    WorkflowEngine 在调用 fn 时把 node_run_id 写不进 ctx（因 fn 返回后才写）；
    所以我们在 fn 内部通过 SQL 查询最近一行的 node_run_id。

    MVP 简化：依赖 workflow_run_nodes 表按 started_at DESC 取最新行。
    """
    return [
        WorkflowNode("build_ctx", "Transform", _build_ctx_node),
        WorkflowNode("director", "AI", _director_node, agent_name="director"),
        WorkflowNode("save_plan", "State", _save_plan_node),
    ]


WORKFLOW = {
    "name": "chapter-plan",
    "version": "v1",
    "description": "Director Plan → chapters.plan_json (status stays PLANNED)",
    "nodes": _build_nodes(),
}


def register_workflow(workflow: dict[str, Any] = WORKFLOW) -> None:
    """由 :mod:`packages.workflows.__init__` 调用；注册到全局中心。"""
    from packages.workflows import register_workflow as _register

    _register(workflow)


# 默认 import 时自动注册（包 __init__ 已触发；此处不重复注册）
_ = register_workflow  # 保留函数供 API / 测试使用


__all__ = ["WORKFLOW", "register_workflow"]
