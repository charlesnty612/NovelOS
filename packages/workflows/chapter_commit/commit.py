"""delta 构建、校验注入与 commit 节点
（拆分自 chapter_commit/pipeline.py，2026-09-06 审查批次三）。"""

from __future__ import annotations

import logging
from typing import Any

from packages.core.agent_runtime.runner import run_agent
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.story_state.delta_repair import repair_delta
from packages.core.story_state.service import StoryStateService
from packages.core.story_state.validator import validate_delta

from .observer import (
    _OBSERVER_RETRY_HINT_TEMPLATE,
    _extract_leg_payload,
    _merge_observer_legs,
    _pick_retry_mock,
    _trim_observer_input_for_leg,
)
from .pipeline_common import (
    _classify_validator_errors_to_legs,
    _has_high_risk_change,
    _inject_resolvable_ids_into_config,
)


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
    """注入元信息 → validate_delta（纯函数，无落库副作用）→ 失败按 leg 重试。

    重试范围（V3.1.1 O-2 拆分后）：
    - 校验失败后解析 errors 中出现的数组名，把仅与某 leg 关联的 errors 路由到对应腿：
      - character_changes / relationship_changes / world_changes → leg_a 重试
      - new_events / new_hooks / resolved_hooks / debt_changes → leg_b 重试
    - errors 不可归类（不含数组名）→ 双腿都重试（兜底，保持向后兼容）。
    - 重试 payload（V3.1.1 O-3）：``_trim_observer_input_for_leg(base, leg)`` 后的
      leg 专用 payload + ``_retry_hint`` + ``extraction_scope``——按腿裁剪 previous_state
      与首次调用口径一致。
    - mock_script：首次按 leg 过滤；重试时取 ``mock_script[idx+1]`` 对应 leg 的元素。
    - 重试预算：每个 leg 最多 1 次（与单次路径的 1 次重试预算对齐——双腿都重试场景下
      总调用次数上限 = 2 首次 + 2 重试 = 4 次 ai_call_logs 行）。
    - 重试后再次 merge → validate；仍失败 ⇒ ``raise ValueError(...)``。
    - submit_delta 仅在最终通过的 delta 上调一次（service.py:662-680 失败会落 rejected 行，
      重试循环内禁止反复调）。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    run_id = ctx["run_id"]
    node_run_id = ctx.get("_current_node_run_id")
    split_meta = ctx.get("observer_split_meta") or {}
    split_enabled = bool(split_meta.get("enabled"))

    # 取当前 state_version 作 previous_state_version（不依赖 observer_payload，原口径）
    svc = StoryStateService(db_path)
    project_id = svc._project_id_for_chapter(  # noqa: SLF001
        get_connection(db_path), chapter_id
    )
    if project_id is None:
        raise ValueError(f"chapter {chapter_id!r} not found")
    current_state = svc.get_current_state(project_id)
    previous_state_version = int(current_state.get("state_version") or 1)

    # 快照：observer_input.previous_state（trimmed 模式）+ 引用存在性校验
    snapshot_for_validate = (ctx.get("observer_input") or {}).get("previous_state")

    # 当前累积的 observer_payload（首次 = _observer_node 输出；后续 = merge 结果）
    observer_payload = ctx.get("observer_payload") or {}
    # leg 维度的输出缓存（按 leg 存最近一次响应，便于只重跑出错 leg）
    leg_outputs: dict[str, dict[str, Any]] = {}
    if split_enabled:
        leg_outputs["entities"] = _extract_leg_payload(observer_payload, "entities")
        leg_outputs["narrative"] = _extract_leg_payload(observer_payload, "narrative")
    else:
        # off 路径：把 observer_payload 视为「完整 7 数组」（与单次大调用一致）；
        # 重试时整次重跑。
        leg_outputs["all"] = dict(observer_payload) if isinstance(observer_payload, dict) else {}

    # 首次校验（先确定性自动修复，再 validate；修不了的留给重试）
    delta = _build_delta(
        observer_payload,
        chapter_id=chapter_id,
        run_id=run_id,
        previous_state_version=previous_state_version,
    )
    delta, repairs = repair_delta(delta, snapshot=snapshot_for_validate, db_path=db_path)
    if repairs:
        ctx["delta_repairs"] = repairs
        logging.info("observer delta repaired before first validation: %s", repairs)
    errors = validate_delta(delta, snapshot=snapshot_for_validate)

    # 重试预算：每腿 1 次
    if errors:
        # mock_script 原始形态（list / str / callable / None）
        original_mock_script = (ctx.get("mock_providers") or {}).get("observer")
        if split_enabled:
            need_a, need_b = _classify_validator_errors_to_legs(errors)
            legs_to_retry: list[str] = []
            if need_a:
                legs_to_retry.append("entities")
            if need_b:
                legs_to_retry.append("narrative")
            for leg in legs_to_retry:
                # V3.1.1 O-3：retry 也按 leg 裁剪 previous_state；与首次调用口径一致。
                base_retry_payload = dict(ctx.get("observer_input") or {})
                retry_payload, _ = _trim_observer_input_for_leg(
                    base_retry_payload, leg,
                )
                retry_payload["_retry_hint"] = _OBSERVER_RETRY_HINT_TEMPLATE.format(
                    errors="; ".join(errors)
                )
                retry_payload["extraction_scope"] = (
                    "entities" if leg == "entities" else "narrative"
                )
                # V3.10 O-4：narrative 腿 retry payload 重新注入可核销白名单
                # （_trim_observer_input_for_leg 不动 config 子树，retry 时需要补一次）。
                if leg == "narrative":
                    snapshot_for_resolvable = (
                        (ctx.get("observer_input") or {}).get("previous_state")
                    )
                    retry_payload = _inject_resolvable_ids_into_config(
                        retry_payload, snapshot_for_resolvable or {},
                    )
                # mock_script：取下一条响应（list 模式弹 idx+1），单条/字符串保持原样
                retry_mock = _pick_retry_mock(original_mock_script, leg)
                retry_out = run_agent(
                    db_path,
                    "observer",
                    retry_payload,
                    run_id,
                    node_run_id=node_run_id,
                    expected="observer",
                    mock_script=retry_mock,
                    capability_override="observer",  # V3.9.3：observer 拆为独立环节（不再走 light）
                    # V3.9.4：observer 单次 run 级覆盖透传（与首次调用口径一致）
                    profile_id=(ctx.get("model_overrides") or {}).get("observer"),
                )
                leg_outputs[leg] = retry_out if isinstance(retry_out, dict) else {}
            # 重新合并双腿
            observer_payload = _merge_observer_legs(
                leg_outputs.get("entities", {}),
                leg_outputs.get("narrative", {}),
            )
        else:
            # off 路径：整次重跑（与单次大调用一致）
            retry_payload = dict(ctx.get("observer_input") or {})
            retry_payload["_retry_hint"] = _OBSERVER_RETRY_HINT_TEMPLATE.format(
                errors="; ".join(errors)
            )
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
                node_run_id=node_run_id,
                expected="observer",
                mock_script=retry_mock_script,
                # V3.9.4：observer 单次 run 级覆盖透传（off 路径未显式 capability_override，
                # 沿用既有 capability_for('observer') 解析行为，不改变业务逻辑）。
                profile_id=(ctx.get("model_overrides") or {}).get("observer"),
            )
            leg_outputs["all"] = (
                dict(observer_payload) if isinstance(observer_payload, dict) else {}
            )

        # 二次校验（重试后）：同样先 repair 再 validate
        delta = _build_delta(
            observer_payload,
            chapter_id=chapter_id,
            run_id=run_id,
            previous_state_version=previous_state_version,
        )
        delta, repairs = repair_delta(delta, snapshot=snapshot_for_validate, db_path=db_path)
        if repairs:
            ctx["delta_repairs"] = repairs
            logging.info("observer delta repaired after retry: %s", repairs)
        errors = validate_delta(delta, snapshot=snapshot_for_validate)
        if errors:
            raise ValueError(
                f"observer delta failed validation: errors={errors}"
            )

    submit_result = svc.submit_delta(delta)
    if submit_result.get("status") != "validated":
        # 防御保留：理论上 validate_delta 通过后 service 也会通过；若仍失败按原口径报错
        raise ValueError(
            f"observer delta failed validation: errors={submit_result.get('errors')}"
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
        if cur is None:
            raise ValueError(f"chapter {chapter_id!r} not found")
        # 状态机：必须先 REVIEWED 才能 COMMITTED；DRAFTED 直接 commit 拒绝
        if cur["status"] != "REVIEWED":
            raise ValueError(
                f"chapter {chapter_id!r} status={cur['status']!r}；请先跑 chapter-review 把它推到 REVIEWED"
            )

        # 时序守卫：最近一次 COMPLETED chapter-review 之后若又产生了更新草稿，commit 必须拒收。
        # 与 API 层 start_commit 的 _guard_commit_draft_freshness 同语义——API 层堵端点入口，
        # 此处堵绕过端点的 generic 启动路径（如 engine 直接 start_with_nodes 或未来新增的入口）。
        # 阈值/查询逻辑不复用 router 层（避免 workflow → API 层反向依赖），本文件内私有实现。
        review_end_row = conn.execute(
            """
            SELECT wr.ended_at FROM workflow_runs wr
            JOIN workflows wf ON wf.workflow_id = wr.workflow_id
            WHERE wr.chapter_id = ?
              AND wf.name = 'chapter-review'
              AND wr.status = 'COMPLETED'
            ORDER BY wr.ended_at DESC
            LIMIT 1
            """,
            (chapter_id,),
        ).fetchone()
        if review_end_row is not None and review_end_row["ended_at"] is not None:
            review_end = review_end_row["ended_at"]
            latest_draft = conn.execute(
                "SELECT version, created_at FROM drafts "
                "WHERE chapter_id = ? AND created_at > ? "
                "ORDER BY created_at DESC LIMIT 1",
                (chapter_id, review_end),
            ).fetchone()
            if latest_draft is not None:
                raise ValueError(
                    f"最新草稿 v{latest_draft['version']} 晚于最近一次审校完成时间"
                    f"（{review_end}），存在未审改动，请先重新审校再提交"
                )
    finally:
        conn.close()

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

    # V2.0 Wave C P1-1：commit 成功后显式失效本章装配缓存（兜底）。
    # 正常情况下 state_version 已推进，缓存键自然失效；此处对"plan_json
    # 被 UPDATE 但 state_version 未变"的边界场景提供最后一道防线。
    # 任何异常均吞掉——失效失败绝不能阻断 commit。
    try:
        from packages.core.context_engine.builders import (
            _invalidate_cache_for_chapter,
            _peek_project_id_from_chapter,
        )
        _proj_id = _peek_project_id_from_chapter(db_path, chapter_id)
        _chap_row = get_connection(db_path).execute(
            "SELECT number FROM chapters WHERE chapter_id = ?", (chapter_id,),
        ).fetchone()
        if _proj_id is not None and _chap_row is not None:
            _invalidate_cache_for_chapter(_proj_id, int(_chap_row["number"] or 0))
    except Exception:  # noqa: BLE001 —— 失效失败不阻断 commit
        pass

    # V2.0 Wave C 任务一：commit 成功后 upsert 本章正文进 chapter_fts（FTS5 全文检索虚表）。
    # 失败按 summarize 节点相同语义降级：log warning，不阻断 commit。
    # 兜底触发是因为 0011_fts_index.sql 的 trigger 在某些边界场景下可能被 SQLite
    # 跳过（例如 EXTERNAL content 模式 + UPDATE 时 old.content 为 NULL）；显式
    # upsert_chapter 保证下次召回能拿到最新章节。
    fts_upsert_ok: bool = True
    fts_upsert_error: str | None = None
    try:
        from packages.core.retrieval import upsert_chapter
        ok = upsert_chapter(db_path, chapter_id)
        if not ok:
            fts_upsert_ok = False
            fts_upsert_error = "upsert_chapter returned False"
    except Exception as exc:  # noqa: BLE001 —— 降级：失败不抛
        fts_upsert_ok = False
        fts_upsert_error = str(exc)
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "chapter_commit.fts_upsert degraded: chapter_id=%s err=%s",
            chapter_id, exc,
        )

    return {
        "commit_result": commit_result,
        "status_after": "COMMITTED",
        "fts_upsert_ok": fts_upsert_ok,
        "fts_upsert_error": fts_upsert_error,
    }
