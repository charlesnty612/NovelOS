"""chapter_commit 工作流（Sprint 4-A + Sprint 6 下半 quality_gate）。

节点列表：
- ``build_observer_ctx`` (Transform) —— 调 :func:`build_observer_input` 组装 observer 输入。
- ``observer`` (AI) —— 调 observer agent 输出 7 个 change 数组（业务载荷）。
- ``inject_validate`` (Transform) —— 注入 10 元信息字段（delta_id=新 ID, schema_version="state-delta-v0",
  workflow_run_id=run_id, created_by="observer:v1", created_at=now_iso 等）；
  调 :meth:`StoryStateService.submit_delta` 校验；校验失败 → run FAILED。
- ``quality_gate`` (State) —— **Sprint 6 新增**：现场组装 :class:`QualityContext`，
  调 :class:`QualityEngine` 评估并落 ``quality_reports``；
  **模式**由环境变量 ``NOVELOS_QUALITY_GATE``（或 ``ctx["quality_gate_mode"]``）控制：
  - ``"enforce"``（默认）—— 任一 ``severity == 'error'`` ⇒ 抛
    ``ValueError("quality gate blocked: ...")`；run 收尾 FAILED，chapter 保持 REVIEWED。
  - ``"report"`` —— error 只落库不阻断；run 收尾 COMPLETED（评审展示用，便于 ``evals/runner`` 通过）。
  与 high_risk_approval / commit 是顺序节点；eval golden / 测试默认走
  ``report`` 避免 REQ-Q8 / H-3 等 MVP 阻断规则误伤（任务书拍板）。
- ``high_risk_approval`` (Human) —— **仅当 payload 含 HIGH/definition/rule change 时暂停**，
  payload=change 清单；human_input={"approved": true}。
- ``commit`` (State) —— 调 :meth:`StoryStateService.commit_delta`；
  chapters.status REVIEWED→COMMITTED；若当前 DRAFTED（未过 review）则 run FAILED 提示先跑 review。
"""

from __future__ import annotations

import os
from typing import Any

from packages.core.agent_runtime.runner import run_agent
from packages.core.context_engine import build_observer_input
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.engine import QualityEngine
from packages.core.quality.service import QualityService, build_quality_context
from packages.core.story_state.service import StoryStateService
from packages.core.workflow_runtime.engine import PauseRequested, WorkflowNode


def _build_observer_ctx_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    payload = build_observer_input(db_path, chapter_id)
    return {"observer_input": payload}


def _observer_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    payload = ctx["observer_input"]
    mock_script = (ctx.get("mock_providers") or {}).get("observer")
    out = run_agent(
        db_path,
        "observer",
        payload,
        run_id,
        node_run_id=ctx.get("_current_node_run_id"),
        expected="observer",
        mock_script=mock_script,
    )
    # out 含 7 个 change 数组（无元信息）
    return {"observer_payload": out}


def _has_high_risk_change(observer_payload: dict[str, Any]) -> bool:
    """扫描 7 数组，risk_level=HIGH / character facet=definition / world_kind=rule 任一命中即 True。"""
    for ch in observer_payload.get("character_changes") or []:
        if isinstance(ch, dict) and (
            ch.get("risk_level") == "HIGH" or ch.get("facet") == "definition"
        ):
            return True
    for w in observer_payload.get("world_changes") or []:
        if isinstance(w, dict) and (
            w.get("risk_level") == "HIGH" or w.get("world_kind") == "rule"
        ):
            return True
    for key in ("relationship_changes", "new_events", "resolved_hooks", "new_hooks", "debt_changes"):
        for item in observer_payload.get(key) or []:
            if isinstance(item, dict) and item.get("risk_level") == "HIGH":
                return True
    return False


def _inject_validate_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    run_id = ctx["run_id"]
    observer_payload = ctx.get("observer_payload") or {}

    # 取当前 state_version 作 previous_state_version
    svc = StoryStateService(db_path)
    project_id = svc._project_id_for_chapter(  # noqa: SLF001
        get_connection(db_path), chapter_id
    )
    if project_id is None:
        raise ValueError(f"chapter {chapter_id!r} not found")
    current_state = svc.get_current_state(project_id)
    previous_state_version = int(current_state.get("state_version") or 1)

    delta_id = new_id("dlt")
    delta = {
        "delta_id": delta_id,
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": run_id,
        "previous_state_version": previous_state_version,
        "created_by": "observer:v1",
        "created_at": now_iso(),
        "supersedes": None,
        "notes": None,
        # 7 数组
        "character_changes": observer_payload.get("character_changes", []),
        "world_changes": observer_payload.get("world_changes", []),
        "relationship_changes": observer_payload.get("relationship_changes", []),
        "new_events": observer_payload.get("new_events", []),
        "resolved_hooks": observer_payload.get("resolved_hooks", []),
        "new_hooks": observer_payload.get("new_hooks", []),
        "debt_changes": observer_payload.get("debt_changes", []),
    }
    submit_result = svc.submit_delta(delta)
    if submit_result.get("status") != "validated":
        raise ValueError(
            f"observer delta rejected by validator: errors={submit_result.get('errors')}"
        )

    # 标记是否需 high_risk 审批（供下一节点判断）
    needs_high_risk_approval = _has_high_risk_change(observer_payload)
    return {
        "delta_id": delta_id,
        "delta": delta,
        "snapshot_pre": current_state,
        "project_id": project_id,
        "needs_high_risk_approval": needs_high_risk_approval,
        "submit_result": submit_result,
    }


# ============================================================================
# Sprint 6 下半：quality_gate 节点
# ============================================================================


def _quality_gate_mode(ctx: dict[str, Any]) -> str:
    """解析 quality_gate 阻断模式。

    优先级（与现有 NOVELOS_* 环境变量口径对齐）：
    - ``ctx["quality_gate_mode"]``（调用方 / 测试用例可显式注入）。
    - ``NOVELOS_QUALITY_GATE`` 环境变量（``"enforce"`` / ``"report"``，默认 ``"report"``）。
    """
    mode = ctx.get("quality_gate_mode") or os.environ.get("NOVELOS_QUALITY_GATE", "report")
    mode = str(mode).strip().lower()
    return mode if mode in {"enforce", "report"} else "report"


def _quality_gate_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """quality_gate 节点。

    - 现场组装 :class:`QualityContext`（复用 :func:`packages.core.quality.service.build_quality_context`）；
    - 调 :class:`QualityEngine.evaluate`；
    - 落 ``quality_reports`` 表（与 ``commit`` 不在同一事务——见 :mod:`packages.core.quality.service` 注释）；
    - 任一 ``severity == 'error'`` + ``enforce`` 模式 ⇒ 抛 :class:`ValueError` 阻断。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    project_id = ctx.get("project_id")
    if not project_id:
        svc = StoryStateService(db_path)
        project_id = svc._project_id_for_chapter(  # noqa: SLF001
            get_connection(db_path), chapter_id
        )
    if not project_id:
        raise ValueError(f"chapter {chapter_id!r} not found")

    delta = ctx.get("delta") or {}
    snapshot_pre = ctx.get("snapshot_pre") or {}

    quality_ctx = build_quality_context(
        db_path,
        project_id=project_id,
        chapter_id=chapter_id,
        delta=delta,
        snapshot_pre=snapshot_pre,
        run_id=ctx.get("run_id"),
    )
    report = QualityEngine().evaluate(quality_ctx)
    QualityService(db_path).save_report(
        report,
        project_id=project_id,
        chapter_id=chapter_id,
        run_id=ctx.get("run_id"),
    )

    mode = _quality_gate_mode(ctx)
    error_issues = [i for i in report.issues if i.severity == "error"]
    error_rule_ids = [i.rule_id for i in error_issues]
    if error_issues and mode == "enforce":
        # 与现有 _commit_node 失败语义一致：抛 ValueError 让 run FAILED，
        # chapter 保持当前状态（当前章节 status=REVIEWED；error 阻断不会推到 COMMITTED）。
        raise ValueError(
            f"quality gate blocked: {error_rule_ids}"
        )

    return {
        "quality_report": report.model_dump(by_alias=True),
        "quality_gate_mode": mode,
        "quality_report_id": report.report_id,
        "quality_overall": int(report.overall),
        "quality_error_count": len(error_issues),
    }


def _high_risk_approval_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """Human 节点：仅在 observer_payload 含 HIGH / definition / rule change 时需要人工审批。
    human_input={"approved": true} → 通过。
    """
    needs = bool(ctx.get("needs_high_risk_approval"))
    hi = ctx.get("human_input") or {}
    approved = bool(hi.get("approved")) if isinstance(hi, dict) else False
    ctx["_high_risk_approved"] = approved

    if not needs:
        # 不需要审批：直接通过
        return {"high_risk_required": False, "human_input": hi}

    if approved:
        return {"high_risk_required": True, "human_input": hi}

    payload = {
        "stage": "chapter-commit.high_risk_approval",
        "message": "Observer 检测到 HIGH 风险 / definition / world_kind=rule change，请人工审批",
        "delta_id": ctx.get("delta_id"),
        "changes": {
            "character_changes": ctx.get("observer_payload", {}).get("character_changes", []),
            "world_changes": ctx.get("observer_payload", {}).get("world_changes", []),
        },
    }
    raise PauseRequested(payload)


def _commit_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    run_id = ctx["run_id"]
    delta_id = ctx.get("delta_id")
    if not delta_id:
        raise ValueError("inject_validate 节点未产出 delta_id")

    needs_high_risk = bool(ctx.get("needs_high_risk_approval"))
    # resume 场景：human_input 是权威源；_high_risk_approved 仅作为 first-pass 标记
    if needs_high_risk:
        hi = ctx.get("human_input") or {}
        approved = bool(hi.get("approved")) if isinstance(hi, dict) else bool(ctx.get("_high_risk_approved"))
    else:
        approved = True
    if not approved:
        raise ValueError("HIGH 风险变更未通过 author approval，无法 commit")

    conn = get_connection(db_path)
    try:
        cur = conn.execute("SELECT status FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
    finally:
        conn.close()
    if cur is None:
        raise ValueError(f"chapter {chapter_id!r} not found")
    # 状态机：必须先 REVIEWED 才能 COMMITTED；DRAFTED 直接 commit 拒绝
    if cur["status"] != "REVIEWED":
        raise ValueError(
            f"chapter {chapter_id!r} status={cur['status']!r}；请先跑 chapter-review 把它推到 REVIEWED"
        )

    svc = StoryStateService(db_path)
    author_approval = {
        "approved": True,
        "approver": "human" if needs_high_risk else "system:workflow",
        "notes": "chapter-commit workflow auto-approved" if not needs_high_risk else "human approved HIGH risk",
    }
    commit_result = svc.commit_delta(delta_id, author_approval, run_id)

    # chapters.status REVIEWED→COMMITTED
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapters SET status = 'COMMITTED', updated_at = ? WHERE chapter_id = ?",
            (now_iso(), chapter_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"commit_result": commit_result, "status_after": "COMMITTED"}


def _build_nodes() -> list[WorkflowNode]:
    return [
        WorkflowNode("build_observer_ctx", "Transform", _build_observer_ctx_node),
        WorkflowNode("observer", "AI", _observer_node, agent_name="observer"),
        WorkflowNode("inject_validate", "Transform", _inject_validate_node),
        WorkflowNode("quality_gate", "State", _quality_gate_node),
        WorkflowNode("high_risk_approval", "Human", _high_risk_approval_node),
        WorkflowNode("commit", "State", _commit_node),
    ]


WORKFLOW = {
    "name": "chapter-commit",
    "version": "v1",
    "description": (
        "Observer → inject metadata → submit_delta → quality_gate → "
        "(HIGH) Human Approval → commit_delta; status REVIEWED→COMMITTED"
    ),
    "nodes": _build_nodes(),
}


def register_workflow(workflow: dict[str, Any] = WORKFLOW) -> None:
    from packages.workflows import register_workflow as _register

    _register(workflow)


__all__ = ["WORKFLOW", "register_workflow"]
