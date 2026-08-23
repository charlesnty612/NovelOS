"""chapter_review 工作流（Sprint 4-A；revise 语义 Sprint 5 补全，闭环 PRD §59/§87）。

节点列表：
- ``basic_checks`` (Transform) —— 草稿存在性、字数偏离 target ±15% 记 warning 进
  ctx["review_report"]、禁用词扫描（默认 forbidden_words=[仿佛,如同,本章目标]）。
- ``author_review`` (Human) —— payload=review_report；human_input 三态决议：
  ``{"approved": true}`` 通过；``{"approved": false}`` 拒绝（run FAILED）；
  ``{"approved": false, "revise": true, "note": str?}`` 驳回并改稿（run FAILED、
  error='rejected-for-revision'，chapter 保持 DRAFTED，note 落 plan_json.revision_note）。
- ``mark_reviewed`` (State) —— chapters.status DRAFTED→REVIEWED（仅当 DRAFTED 状态可走）。
"""

from __future__ import annotations

import json
from typing import Any

from packages.core.db import get_connection
from packages.core.ids import now_iso
from packages.core.workflow_runtime.engine import PauseRequested, WorkflowNode

DEFAULT_FORBIDDEN_WORDS = ["仿佛", "如同", "本章目标"]


def _basic_checks_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    target = int(ctx.get("target_word_count", 2200))

    conn = get_connection(db_path)
    try:
        draft_row = conn.execute(
            """
            SELECT content FROM drafts WHERE chapter_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (chapter_id,),
        ).fetchone()
    finally:
        conn.close()

    if draft_row is None:
        # 直接抛错：basic_checks 失败会让 run FAILED
        raise ValueError(f"chapter {chapter_id!r} has no draft; run chapter-write first")

    prose = draft_row["content"] or ""
    word_count = len(prose)
    deviation = (word_count - target) / target if target > 0 else 0
    within_range = abs(deviation) <= 0.15

    forbidden_hits = [w for w in DEFAULT_FORBIDDEN_WORDS if w in prose]
    warnings: list[str] = []
    if not within_range:
        warnings.append(
            f"字数 {word_count} 偏离 target {target} 达 {deviation:.1%}（阈值 ±15%）"
        )
    if forbidden_hits:
        warnings.append(f"禁用词命中：{','.join(forbidden_hits)}")

    report = {
        "chapter_id": chapter_id,
        "word_count": word_count,
        "target_word_count": target,
        "within_range": within_range,
        "deviation": round(deviation, 4),
        "forbidden_word_hits": forbidden_hits,
        "warnings": warnings,
    }
    return {"review_report": report}


def _author_review_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """Human 节点：抛 PauseRequested 等 author 决议。
    human_input={"approved": true} → 通过；其他（"approved":false 或缺 approved）→ 拒绝。

    本 fn 总是抛 PauseRequested；resume 时会被重跑读 ctx["human_input"]["approved"]。
    """
    report = ctx.get("review_report") or {}
    payload = {
        "stage": "chapter-review",
        "message": "请审查章节草稿并批准或驳回",
        "review_report": report,
    }
    # 先把 human_input 已批准的标志 merge 进 ctx（提供给 mark_reviewed 用）
    hi = ctx.get("human_input") or {}
    ctx["_author_approved"] = bool(hi.get("approved")) if isinstance(hi, dict) else False
    if ctx["_author_approved"]:
        # 已通过审批：直接返回（不抛 PauseRequested）
        return {"author_decision": "approved", "human_input": hi}
    # 驳回并改稿（revise:true）时把 note 透传给 mark_reviewed，供 revision_note 落库
    ctx["_revision_note"] = hi.get("note") if isinstance(hi, dict) else None
    raise PauseRequested(payload)


class _RejectForRevision(Exception):
    """revise 分支专用异常：让 run 以 FAILED + error='rejected-for-revision' 收尾。

    引擎侧无 CANCELLED 触发路径（engine._run_nodes 只在节点抛异常时置 FAILED；
    节点内直接改 ctx 的 status 会被 _finalize_run 无条件覆盖为 COMPLETED），
    故沿用 FAILED 终态、以 error 字段区分，改动最小且不碰 engine/DDL。
    error 字段取 ``str(exc)``（engine._run_nodes），故 args[0] 直接写标识值。
    """

    def __init__(self) -> None:
        super().__init__("rejected-for-revision")


def _mark_reviewed_node(ctx: dict[str, Any]) -> dict[str, Any]:
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    # resume 场景：author_review 节点被 SKIPPED（不会再跑）；直接读 human_input
    hi = ctx.get("human_input") or {}
    approved = bool(hi.get("approved")) if isinstance(hi, dict) else bool(ctx.get("_author_approved"))
    if not approved:
        if isinstance(hi, dict) and hi.get("revise"):
            # 驳回并改稿：chapter 保持 DRAFTED，note 追加进 plan_json.revision_note，
            # 供下次 chapter-write 的 load_plan 参考；run 以 FAILED(rejected-for-revision) 收尾
            note = hi.get("note") if isinstance(hi.get("note"), str) else ""
            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,)
                ).fetchone()
                if row is None:
                    raise ValueError(f"chapter {chapter_id!r} not found")
                raw = row["plan_json"]
                plan = json.loads(raw) if raw else {}
                if not isinstance(plan, dict):
                    plan = {}
                if note:
                    plan["revision_note"] = note
                else:
                    plan.pop("revision_note", None)
                conn.execute(
                    "UPDATE chapters SET plan_json = ?, updated_at = ? WHERE chapter_id = ?",
                    (json.dumps(plan, ensure_ascii=False), now_iso(), chapter_id),
                )
                conn.commit()
            finally:
                conn.close()
            raise _RejectForRevision()
        raise ValueError("author_review 未通过，无法标记 REVIEWED")

    conn = get_connection(db_path)
    try:
        cur = conn.execute("SELECT status FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
        if cur is None:
            raise ValueError(f"chapter {chapter_id!r} not found")
        if cur["status"] != "DRAFTED":
            raise ValueError(
                f"chapter {chapter_id!r} status={cur['status']!r}，必须先 DRAFTED 才能 REVIEWED"
            )
        conn.execute(
            "UPDATE chapters SET status = 'REVIEWED', updated_at = ? WHERE chapter_id = ?",
            (now_iso(), chapter_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"status_after": "REVIEWED"}


def _build_nodes() -> list[WorkflowNode]:
    return [
        WorkflowNode("basic_checks", "Transform", _basic_checks_node),
        WorkflowNode("author_review", "Human", _author_review_node),
        WorkflowNode("mark_reviewed", "State", _mark_reviewed_node),
    ]


WORKFLOW = {
    "name": "chapter-review",
    "version": "v1",
    "description": "basic_checks → author_review(Human) → mark_reviewed (DRAFTED→REVIEWED)；revise 驳回改稿闭环",
    "nodes": _build_nodes(),
}


def register_workflow(workflow: dict[str, Any] = WORKFLOW) -> None:
    from packages.workflows import register_workflow as _register

    _register(workflow)


__all__ = ["WORKFLOW", "register_workflow"]
