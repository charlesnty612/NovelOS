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
from packages.core.model_router.router import capability_for
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
        target_word_count=ctx.get("target_word_count", 3000),
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
        profile_id=(ctx.get("model_overrides") or {}).get(
            capability_for("director")
        ),
    )
    return {"director_output": out}


def _save_plan_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """State：把 director 输出写入 chapters.plan_json（status 不动）。

    保字数契约：若章节当前 plan_json 已含 ``expected_word_count``（>0），
    重生成时继承该值，避免误点「生成计划」覆盖掉已规划的单章字数。
    默认 3000（对齐 NovelOS 单章字数标准，与 project-init 的 DEFAULT_CHAPTER_WORD_COUNT 一致）。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    director_output = ctx.get("director_output") or {}

    # 读取原 plan_json.expected_word_count（如有）——保字数用
    inherit_expected: int | None = None
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        if row is not None:
            existing_plan = _safe_load_json(row["plan_json"])
            if isinstance(existing_plan, dict):
                raw = existing_plan.get("expected_word_count")
                if isinstance(raw, (int, float)) and raw > 0:
                    inherit_expected = int(raw)
    finally:
        conn.close()

    director_word_count = director_output.get("expected_word_count")
    if isinstance(director_word_count, (int, float)) and director_word_count > 0:
        expected_word_count: int = int(director_word_count)
    elif inherit_expected is not None:
        expected_word_count = inherit_expected
    else:
        expected_word_count = 2200

    plan_payload: dict[str, Any] = {
        "chapter_goal": director_output.get("chapter_goal"),
        "core_conflict": director_output.get("core_conflict"),
        "turning_point": director_output.get("turning_point"),
        "expected_role": director_output.get("expected_role"),
        "expected_word_count": expected_word_count,
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


def _safe_load_json(raw: Any) -> Any:
    """chapters.plan_json 列存的是 JSON 字符串；读时安全反序列化（失败返回 None）。"""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except (ValueError, TypeError):
            return None
    return None


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


# 注意（Sprint V1.5）：注册动作统一在 :mod:`packages.workflows.chapter_plan.__init__`
# 调用 :func:`packages.core.workflow_registry.register_workflow`；本模块不再暴露
# ``register_workflow`` 函数（业务 pipeline 不再依赖 packages.workflows 中心）。


__all__ = ["WORKFLOW"]
