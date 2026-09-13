"""质量门禁与高危审批节点
（拆分自 chapter_commit/pipeline.py，2026-09-06 审查批次三）。

V3.9 批次 4.1（失败闭环）：enforce 阻断时除抛错外，把改稿出路落到 chapter：
- ``plan_json.revision_note``：由 ``revision_guidance`` 编译成的人类可读改稿意见
  （与 chapter-review「驳回并改稿」同款落点，单一属主语义）；
- ``plan_json.gate_blocked``：阻断标记 + ``rule_ids`` 摘要，供前端区分
  「review 驳回」与「gate 阻断」，并驱动「按门禁建议改稿」入口。
- 门禁通过时清除上一次残留的 ``gate_blocked``（防御历史数据 / 上一次阻断已解除）。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from packages.core.db import get_connection
from packages.core.ids import now_iso
from packages.core.quality.engine import QualityEngine
from packages.core.quality.issues import is_blocking_issue
from packages.core.quality.service import QualityService, build_quality_context, capture_reference_consumption
from packages.core.story_state.service import StoryStateService
from packages.core.workflow_runtime.engine import PauseRequested

from .pipeline_common import (
    _build_revision_guidance,
)

_log = logging.getLogger(__name__)

# plan_json 中的门禁标记键（唯一属主：本节点写/清，前端与 API 只读）。
_GATE_BLOCKED_KEY = "gate_blocked"


def _build_gate_revision_note(
    revision_guidance: list[dict[str, Any]],
    error_rule_ids: list[str],
) -> str:
    """把 ``revision_guidance`` 编译成写进 ``plan_json.revision_note`` 的改稿意见。

    纯文本、多行，供 chapter-write 的 revise 模式（``revision_note``）直接消费：
    首行给出阻断规则清单，其后每个 guidance 维度一行（含 rule_hint），
    再把该维度的 top_issues 逐条列出（最多 3 条，带 rule_id / message）。
    """
    lines: list[str] = [
        "【质量门禁阻断·改稿建议】quality_gate 检测到 error 级问题："
        + ("、".join(error_rule_ids) if error_rule_ids else "（未给出 rule_id）"),
    ]
    for entry in revision_guidance:
        if not isinstance(entry, dict):
            continue
        dim = entry.get("dimension") or "unknown"
        score = entry.get("score")
        threshold = entry.get("threshold")
        hint = str(entry.get("rule_hint") or "").strip()
        prefix = f"- {dim}"
        if isinstance(score, int) and isinstance(threshold, int):
            prefix += f"（分 {score}/阈值 {threshold}）"
        lines.append(f"{prefix}：{hint}" if hint else prefix)
        for issue in (entry.get("top_issues") or [])[:3]:
            if not isinstance(issue, dict):
                continue
            rule_id = issue.get("rule_id") or issue.get("category") or ""
            message = str(issue.get("message") or issue.get("suggestion") or "").strip()
            if not message:
                continue
            lines.append(f"  · {rule_id}: {message}")
    lines.append("（改稿后重新审校并提交，门禁通过后本提示自动解除。）")
    return "\n".join(lines)


def _read_plan_json(db_path: str, chapter_id: str) -> dict[str, Any] | None:
    """读 chapters.plan_json；chapter 不存在 → None；非法 JSON / 非 dict → {}。"""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    raw = row["plan_json"]
    if not raw:
        return {}
    try:
        plan = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return plan if isinstance(plan, dict) else {}


def _write_plan_json(db_path: str, chapter_id: str, plan: dict[str, Any]) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapters SET plan_json = ?, updated_at = ? WHERE chapter_id = ?",
            (json.dumps(plan, ensure_ascii=False), now_iso(), chapter_id),
        )
        conn.commit()
    finally:
        conn.close()


def _persist_gate_block(
    db_path: str,
    chapter_id: str,
    *,
    mode: str,
    rule_ids: list[str],
    note: str,
) -> None:
    """enforce 阻断落点：写 ``plan_json.revision_note`` + ``gate_blocked`` 标记。

    覆盖语义（重复阻断只重写，不追加）；写库失败只记日志——阻断本身（raise）
    才是主行为，不能因为落点失败改变 run 的失败语义。
    """
    try:
        plan = _read_plan_json(db_path, chapter_id)
        if plan is None:
            return
        plan["revision_note"] = note
        plan[_GATE_BLOCKED_KEY] = {
            "mode": mode,
            "rule_ids": list(rule_ids),
            "blocked_at": now_iso(),
        }
        _write_plan_json(db_path, chapter_id, plan)
    except Exception as exc:  # noqa: BLE001 —— 落点失败不改变阻断语义
        _log.warning(
            "quality_gate persist blocked note failed: chapter_id=%s err=%s",
            chapter_id, exc,
        )


def _clear_gate_block(db_path: str, chapter_id: str) -> None:
    """门禁通过时清除残留的 ``gate_blocked`` 标记（不改 revision_note——其属主是改稿流程）。"""
    try:
        plan = _read_plan_json(db_path, chapter_id)
        if plan is None or _GATE_BLOCKED_KEY not in plan:
            return
        plan.pop(_GATE_BLOCKED_KEY, None)
        _write_plan_json(db_path, chapter_id, plan)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "quality_gate clear blocked marker failed: chapter_id=%s err=%s",
            chapter_id, exc,
        )


def _quality_gate_mode(ctx: dict[str, Any]) -> str:
    """解析 quality_gate 阻断模式。

    优先级（与现有 NOVELOS_* 环境变量口径对齐）：
    - ``ctx["quality_gate_mode"]``（调用方 / 测试用例可显式注入）。
    - ``NOVELOS_QUALITY_GATE`` 环境变量（``"enforce"`` / ``"report"``，默认 ``"enforce"``）。
    """
    mode = ctx.get("quality_gate_mode") or os.environ.get("NOVELOS_QUALITY_GATE", "enforce")
    mode = str(mode).strip().lower()
    return mode if mode in {"enforce", "report"} else "enforce"


def _quality_gate_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """quality_gate 节点。

    - 现场组装 :class:`QualityContext`（复用 :func:`packages.core.quality.service.build_quality_context`）；
    - 调 :class:`QualityEngine.evaluate`；
    - 落 ``quality_reports`` 表（与 ``commit`` 不在同一事务——见 :mod:`packages.core.quality.service` 注释）；
    - **blocking** error issue（``issues.is_blocking_issue``：severity=='error' 且 rule_id
      在阻断白名单）+ ``enforce`` 模式 ⇒ 抛 :class:`ValueError` 阻断；
      informational error（如显式 strict 前的 Q8）只随报告落库、不阻断
      （与 ``aggregate.compute_overall`` 的 blocking/informational 分组同口径，V3.9 批次 3.1）。
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

    # Sprint V1.4：参照系消费可观测——quality_gate 现场采集本节点消费的 *.txt 清单
    # 写到节点返回 dict，供 checkpoint_json 暴露给前端；与 quality 评估同一时机，
    # 阻断时也会随 ctx 落盘（_update_run_checkpoint 在每节点完成后写一次）。
    reference_consumption = capture_reference_consumption(db_path, project_id)

    quality_ctx = build_quality_context(
        db_path,
        project_id=project_id,
        chapter_id=chapter_id,
        delta=delta,
        snapshot_pre=snapshot_pre,
        run_id=ctx.get("run_id"),
    )
    report = QualityEngine().evaluate(quality_ctx)
    # Sprint V1.4：把参照系消费清单持久化到 quality_reports._meta.reference_consumption，
    # 让 /api/chapters/{cid}/quality 端点直接返回，UI 不必再回查 runs.checkpoint_json。
    meta = dict(report.meta or {})
    meta["reference_consumption"] = reference_consumption
    report.meta = meta
    QualityService(db_path).save_report(
        report,
        project_id=project_id,
        chapter_id=chapter_id,
        run_id=ctx.get("run_id"),
    )

    mode = _quality_gate_mode(ctx)
    error_issues = [i for i in report.issues if i.severity == "error"]
    # V3.9 批次 3.1：只有 blocking error 触发 enforce 阻断；informational error 仅落库。
    blocking_issues = [i for i in error_issues if is_blocking_issue(i)]
    blocking_rule_ids = [i.rule_id for i in blocking_issues]

    # Sprint V1.4：enforce 模式阻断时，把每低分维度的可执行改稿建议结构化带上。
    # revision_guidance 写进 ctx['quality_gate']（checkpoint_json 会自动收录），
    # 同时把整段结构化 payload JSON 化追加到 ValueError 信息里——run FAILED 时
    # runs.error 已包含它，前端 QualityPanel 可直接从错误字符串里解析。
    # 注：guidance 覆盖全部 error（含 informational，仍有改稿价值），阻断只看 blocking。
    revision_guidance: list[dict[str, Any]] = []
    if error_issues:
        revision_guidance = _build_revision_guidance(report, report.issues)
    ctx["quality_gate"] = {
        "blocked": bool(blocking_issues) and mode == "enforce",
        "mode": mode,
        "reference_consumption": reference_consumption,
        "revision_guidance": revision_guidance,
    }

    if blocking_issues and mode == "enforce":
        # V3.9 批次 4.1：先把改稿出路写进 chapter（plan_json.revision_note +
        # gate_blocked 标记），再抛错阻断。作者可在章节详情页一键「按门禁建议改稿」
        # 复用既有写正文（revise）→ 审校回路，不必手工改稿。
        _persist_gate_block(
            db_path,
            chapter_id,
            mode=mode,
            rule_ids=blocking_rule_ids,
            note=_build_gate_revision_note(revision_guidance, blocking_rule_ids),
        )
        # 与现有 _commit_node 失败语义一致：抛 ValueError 让 run FAILED，
        # chapter 保持当前状态（当前章节 status=REVIEWED；error 阻断不会推到 COMMITTED）。
        # 在错误信息里把 revision_guidance 序列化为可解析段：
        #   "quality gate blocked: <rule_ids> | guidance=<json>"
        # 前端 / 测试可按 "| guidance=" 分隔；JSON 解析失败也不影响主信息。
        try:
            guidance_json = json.dumps(revision_guidance, ensure_ascii=False)
        except (TypeError, ValueError):
            guidance_json = "[]"
        raise ValueError(
            f"quality gate blocked: {blocking_rule_ids} | guidance={guidance_json}"
        )

    # 未阻断（report 模式或本次无 blocking error）：清除上一次残留的 gate_blocked 标记。
    # revision_note 不在此清除——其属主是改稿流程（review 驳回 / gate 阻断写入）。
    _clear_gate_block(db_path, chapter_id)

    return {
        "quality_report": report.model_dump(by_alias=True),
        "quality_gate_mode": mode,
        "quality_report_id": report.report_id,
        "quality_overall": int(report.overall),
        "quality_error_count": len(error_issues),
        "quality_blocking_count": len(blocking_issues),
        "reference_consumption": reference_consumption,
        "revision_guidance": revision_guidance,
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
