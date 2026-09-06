"""质量门禁与高危审批节点
（拆分自 chapter_commit/pipeline.py，2026-09-06 审查批次三）。"""

from __future__ import annotations

import os
from typing import Any

from packages.core.db import get_connection
from packages.core.quality.engine import QualityEngine
from packages.core.quality.service import QualityService, build_quality_context, capture_reference_consumption
from packages.core.story_state.service import StoryStateService
from packages.core.workflow_runtime.engine import PauseRequested

from .pipeline_common import (
    _build_revision_guidance,
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
    error_rule_ids = [i.rule_id for i in error_issues]

    # Sprint V1.4：enforce 模式阻断时，把每低分维度的可执行改稿建议结构化带上。
    # revision_guidance 写进 ctx['quality_gate']（checkpoint_json 会自动收录），
    # 同时把整段结构化 payload JSON 化追加到 ValueError 信息里——run FAILED 时
    # runs.error 已包含它，前端 QualityPanel 可直接从错误字符串里解析。
    revision_guidance: list[dict[str, Any]] = []
    if error_issues:
        revision_guidance = _build_revision_guidance(report, report.issues)
    ctx["quality_gate"] = {
        "blocked": bool(error_issues) and mode == "enforce",
        "mode": mode,
        "reference_consumption": reference_consumption,
        "revision_guidance": revision_guidance,
    }

    if error_issues and mode == "enforce":
        # 与现有 _commit_node 失败语义一致：抛 ValueError 让 run FAILED，
        # chapter 保持当前状态（当前章节 status=REVIEWED；error 阻断不会推到 COMMITTED）。
        # 在错误信息里把 revision_guidance 序列化为可解析段：
        #   "quality gate blocked: <rule_ids> | guidance=<json>"
        # 前端 / 测试可按 "| guidance=" 分隔；JSON 解析失败也不影响主信息。
        import json as _json
        try:
            guidance_json = _json.dumps(revision_guidance, ensure_ascii=False)
        except (TypeError, ValueError):
            guidance_json = "[]"
        raise ValueError(
            f"quality gate blocked: {error_rule_ids} | guidance={guidance_json}"
        )

    return {
        "quality_report": report.model_dump(by_alias=True),
        "quality_gate_mode": mode,
        "quality_report_id": report.report_id,
        "quality_overall": int(report.overall),
        "quality_error_count": len(error_issues),
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
