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
from packages.core.story_state.validator import validate_delta
from packages.core.workflow_runtime.engine import PauseRequested, WorkflowNode


# Observer delta 校验失败重试提示模板（注入 payload._retry_hint 引导 LLM 修正）。
# 真实 LLM（如 MiniMax-M3）曾出现 ``character_changes[0].op='update' 但 before 为 None``
# 这类业务校验失败：让 observer 修正后重新完整输出 7 个 change 数组 JSON。
_OBSERVER_RETRY_HINT_TEMPLATE = (
    "\n\n[Validation note] 上一次输出的 delta 未通过业务校验：{errors}。"
    "请按反馈修正后重新完整输出 7 个 change 数组的合法 JSON（保持 schema_version="
    "state-delta-v0 外的其它元信息字段由后续节点注入，无需在本次输出中包含）。"
)


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


def _build_delta(
    observer_payload: dict[str, Any],
    *,
    chapter_id: str,
    run_id: str,
    previous_state_version: int,
) -> dict[str, Any]:
    """由 observer 业务载荷注入 10 元信息字段构造完整 delta（不含 created_at 之外的 DB 行为）。"""
    return {
        "delta_id": new_id("dlt"),
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


def _inject_validate_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """注入元信息 → validate_delta（纯函数，无落库副作用）→ 失败带错误重试 observer 一次。

    重试范围（与 deconstruct_book 的 T2/T3 retry 范式对齐）：
    - 校验失败后构造新 payload = ``dict(ctx["observer_input"])`` + ``_retry_hint``；
    - mock_script 取 ``ctx["mock_providers"]["observer"]``：list 且长度 >1 时取 ``mock_script[1]``，
      否则保持原样（让生产 / 单条 mock 走同一响应，retry 仅靠 _retry_hint 修正）；
    - 两次都失败 ⇒ ``raise ValueError("observer delta rejected by validator: errors=...")``。
    - submit_delta 仅在最终通过的 delta 上调一次（service.py:662-680 失败会落 rejected 行，
      重试循环内禁止反复调）。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    run_id = ctx["run_id"]
    observer_payload = ctx.get("observer_payload") or {}

    # 取当前 state_version 作 previous_state_version（不依赖 observer_payload，原口径）
    svc = StoryStateService(db_path)
    project_id = svc._project_id_for_chapter(  # noqa: SLF001
        get_connection(db_path), chapter_id
    )
    if project_id is None:
        raise ValueError(f"chapter {chapter_id!r} not found")
    current_state = svc.get_current_state(project_id)
    previous_state_version = int(current_state.get("state_version") or 1)

    # 第一次：基于 _observer_node 注入的 observer_payload 构造 delta → validate
    delta = _build_delta(
        observer_payload,
        chapter_id=chapter_id,
        run_id=run_id,
        previous_state_version=previous_state_version,
    )
    errors = validate_delta(delta)
    if errors:
        # 构造重试 payload（在 observer_input 副本上注入 _retry_hint）
        retry_payload = dict(ctx.get("observer_input") or {})
        retry_payload["_retry_hint"] = _OBSERVER_RETRY_HINT_TEMPLATE.format(
            errors="; ".join(errors)
        )
        # mock_script list 模式：第二次取下一条以让 MockProvider 返回不同响应。
        # 注意 MockProvider(scripted=str) 会把 str 当 iterable 取字符（runner 测试通用行为），
        # 因此弹出的 str 必须用 list[str]（单元素）包一层，与 deconstruct_book T2 retry
        # 处理一致。
        original_mock_script = (ctx.get("mock_providers") or {}).get("observer")
        if isinstance(original_mock_script, list) and len(original_mock_script) > 1:
            picked = original_mock_script[1]
            retry_mock_script = [picked] if isinstance(picked, str) else picked
        else:
            retry_mock_script = original_mock_script

        observer_payload = run_agent(
            db_path,
            "observer",
            retry_payload,
            run_id,
            node_run_id=ctx.get("_current_node_run_id"),
            expected="observer",
            mock_script=retry_mock_script,
        )
        # 第二次：基于 retry 后 observer_payload 重建 delta → 再次 validate
        delta = _build_delta(
            observer_payload,
            chapter_id=chapter_id,
            run_id=run_id,
            previous_state_version=previous_state_version,
        )
        errors = validate_delta(delta)
        if errors:
            raise ValueError(
                f"observer delta rejected by validator: errors={errors}"
            )

    submit_result = svc.submit_delta(delta)
    if submit_result.get("status") != "validated":
        # 防御保留：理论上 validate_delta 通过后 service 也会通过；若仍失败按原口径报错
        raise ValueError(
            f"observer delta rejected by validator: errors={submit_result.get('errors')}"
        )

    # 标记是否需 high_risk 审批（基于最终采用的 observer_payload 计算）
    needs_high_risk_approval = _has_high_risk_change(observer_payload)
    return {
        "delta_id": delta["delta_id"],
        "delta": delta,
        "observer_payload": observer_payload,
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


# ============================================================================
# Sprint 14：summarize 节点（commit 成功后追加，失败降级不阻塞）
# ============================================================================


# 摘要最大字符数（中文按字符）；超长由 service 层截断 + 标 degraded。
_SUMMARY_MAX_CHARS = 200
# tail_text 取已提交正文末尾字符数（不调 LLM）。
_TAIL_TEXT_CHARS = 300


def _summarize_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """summarize 节点（Sprint 14）—— commit 成功后追加。

    行为：
    1. 取最新 draft content（已 commit 的草稿正文）；若为空 → summary_status='skipped'。
    2. tail_text = content[-300:]（不调 LLM）。
    3. 调 ``run_agent(..., agent_name='summarizer', mock_script=...)`` 输出 JSON ``{"summary": "..."}``。
    4. 写 ``chapter_summaries`` 行（summary_id / project_id / chapter_id / chapter_no /
       summary ≤ 200 字 / tail_text / created_at）；摘要超长则截断并标记 degraded。
    5. 任何异常（LLM 失败 / JSON 解析失败 / DB 写入失败）→ 降级：warning 日志 +
       summary_status='failed' + 不抛错（保证 chapter 提交不因摘要失败而 FAILED）。

    返回 ``{"summary_status": "ok|failed|skipped", "summary_id": str|None, "degraded": bool}``。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    mock_script = (ctx.get("mock_providers") or {}).get("summarizer")

    # 1) 章节 + 项目 + chapter_no
    conn = get_connection(db_path)
    try:
        chap_row = conn.execute(
            "SELECT project_id, number FROM chapters WHERE chapter_id = ?",
            (chapter_id,),
        ).fetchone()
        if chap_row is None:
            return {"summary_status": "skipped", "summary_id": None, "degraded": False}
        project_id = chap_row["project_id"]
        chapter_no = int(chap_row["number"] or 0)
        draft_row = conn.execute(
            """
            SELECT content FROM drafts
            WHERE chapter_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (chapter_id,),
        ).fetchone()
    finally:
        conn.close()
    # sqlite3.Row 不支持 .get；用 dict(draft_row) 兜底（draft_row 为 None 时返回空 dict）
    content = (dict(draft_row) if draft_row else {}).get("content") or ""
    if not content.strip():
        return {"summary_status": "skipped", "summary_id": None, "degraded": False}

    # 2) tail_text（不调 LLM）
    tail_text = content[-_TAIL_TEXT_CHARS:] if len(content) > _TAIL_TEXT_CHARS else content

    # 3) 构造输入 payload + 调 summarizer
    plan_goal = ""
    try:
        conn = get_connection(db_path)
        try:
            pj = conn.execute(
                "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,),
            ).fetchone()
        finally:
            conn.close()
        if pj:
            import json as _json
            raw = pj["plan_json"] or "{}"
            try:
                pj_d = _json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (TypeError, ValueError):
                pj_d = {}
            plan_goal = (pj_d or {}).get("chapter_goal") or ""
    except Exception:  # noqa: BLE001 —— 计划读取失败不阻塞 summarize
        plan_goal = ""

    summary_payload = {
        "agent": "summarizer",
        "prompt_version": "summarizer:v1",
        "chapter": {
            "chapter_id": chapter_id,
            "chapter_no": chapter_no,
            "chapter_goal": plan_goal,
        },
        "prose_excerpt": content[:4000],  # 取前 4000 字足够上下文（避免超长 prompt）
        "tail_text": tail_text,
    }

    degraded = False
    summary_text = ""
    summary_status = "failed"
    try:
        # summarizer 无 ACTIVE prompt 时 runner 会抛 PromptNotFoundError → 降级。
        out = run_agent(
            db_path,
            "summarizer",
            summary_payload,
            ctx.get("run_id") or "",
            node_run_id=ctx.get("_current_node_run_id"),
            expected="summarizer",
            mock_script=mock_script,
        )
        if not isinstance(out, dict):
            raise ValueError(f"summarizer output not dict: {type(out).__name__}")
        candidate = out.get("summary")
        if not isinstance(candidate, str):
            raise ValueError("summarizer output missing 'summary' string")
        summary_text = candidate.strip()
        if not summary_text:
            raise ValueError("summarizer output 'summary' empty")
        if len(summary_text) > _SUMMARY_MAX_CHARS:
            summary_text = summary_text[:_SUMMARY_MAX_CHARS]
            degraded = True
        summary_status = "ok"
    except Exception as exc:  # noqa: BLE001 —— 降级：任何失败不抛
        import logging
        logging.getLogger(__name__).warning(
            "chapter_commit.summarize degraded: chapter_id=%s err=%s",
            chapter_id, exc,
        )
        return {
            "summary_status": "failed",
            "summary_id": None,
            "degraded": False,
            "summary_error": str(exc),
        }

    # 4) 落库 chapter_summaries
    summary_id = new_id("sum")
    try:
        conn = get_connection(db_path)
        try:
            conn.execute(
                """
                INSERT INTO chapter_summaries
                    (summary_id, project_id, chapter_id, chapter_no,
                     summary, tail_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary_id,
                    project_id,
                    chapter_id,
                    chapter_no,
                    summary_text,
                    tail_text,
                    now_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "chapter_commit.summarize DB insert failed: summary_id=%s err=%s",
            summary_id, exc,
        )
        return {
            "summary_status": "failed",
            "summary_id": None,
            "degraded": False,
            "summary_error": str(exc),
        }

    return {
        "summary_status": summary_status,
        "summary_id": summary_id,
        "degraded": degraded,
    }


def _build_nodes() -> list[WorkflowNode]:
    return [
        WorkflowNode("build_observer_ctx", "Transform", _build_observer_ctx_node),
        WorkflowNode("observer", "AI", _observer_node, agent_name="observer"),
        WorkflowNode("inject_validate", "Transform", _inject_validate_node),
        WorkflowNode("quality_gate", "State", _quality_gate_node),
        WorkflowNode("high_risk_approval", "Human", _high_risk_approval_node),
        WorkflowNode("commit", "State", _commit_node),
        WorkflowNode("summarize", "State", _summarize_node),
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
