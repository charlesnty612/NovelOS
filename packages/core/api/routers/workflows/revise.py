"""workflows 路由：gate-revise 接力与 auto_revise 自动改稿回路（V3.9 批次 4.1 / 4.2）。

V4.0 模块化重构 V2：自 ``packages/core/api/routers/workflows.py`` 按端点域拆出，
纯搬家零逻辑变更。内容：

- ``_read_pending_gate_block``：读 ``chapters.plan_json.gate_blocked``（quality_gate
  enforce 阻断落点）；
- ``_chain_review_after_write``：gate-revise 的 daemon 接力线程（write → review）；
- ``_auto_revise_loop``：resume 触发的自动改稿回路（每轮 write → review，直到
  approved / PAUSED / 达上限 / 被取消）；
- ``POST /projects/{project_id}/chapters/{chapter_id}/gate-revise`` 端点。

回路级取消注册表与子 run 启动口径在 ``common``；cancel 端点在 ``control``。
"""

from __future__ import annotations

import json
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.db import get_connection
from packages.core.ids import new_id
from packages.core.workflow_runtime.engine import WorkflowEngine
from packages.core.workflow_runtime.runs import get_run

from .common import (
    _RUN_WAIT_DEADLINE_SECONDS,
    GateReviseRequest,
    StartWorkflowRequest,
    _auto_revise_loop_cancelled,
    _check_chapter,
    _engine,
    _finish_auto_revise_loop,
    _record_auto_revise_child,
    _register_auto_revise_loop,
    _run_workflow_return_payload,
    log,
)
from .control import _start_workflow

router = APIRouter(tags=["workflows"])


# ---------------------------------------------------------------------------
# Helpers（gate-revise 落点读取与接力线程）
# ---------------------------------------------------------------------------


def _read_pending_gate_block(db_path: str, chapter_id: str) -> dict[str, Any] | None:
    """读 ``chapters.plan_json.gate_blocked``（quality_gate enforce 阻断落点）。

    返回 dict 形态的标记（含 mode / rule_ids / blocked_at）；无标记、
    chapter 不存在、plan_json 非法 → None。
    """
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["plan_json"]:
        return None
    try:
        plan = json.loads(row["plan_json"])
    except (TypeError, ValueError):
        return None
    if not isinstance(plan, dict):
        return None
    blocked = plan.get("gate_blocked")
    return blocked if isinstance(blocked, dict) else None


def _chain_review_after_write(
    engine: WorkflowEngine,
    db_path: str,
    project_id: str,
    chapter_id: str,
    write_run_id: str,
    mock_providers: dict[str, list[str]] | None,
    initial_ctx_extra: dict[str, Any] | None,
) -> None:
    """daemon 线程体：等 write 子 run 终态 → COMPLETED 则接力 chapter-review。

    V3.9 批次 4.1「按门禁建议改稿」的第二段：write（revise 模式，消费
    ``plan_json.revision_note``）完成后自动启动审校；审校会停在 author_review
    等作者决议，批准后章节回到 REVIEWED、可重新提交。

    write 未 COMPLETED（FAILED / CANCELLED / 超时仍在跑）→ 不接力审校，
    交由前端轮询 run 列表处理。复用 :func:`_run_workflow_return_payload`
    （与 auto_revise 回路启动子 run 同一口径）。异常只记日志，不外抛（HTTP 已返回）。
    """
    import time as _t

    try:
        deadline = _t.monotonic() + _RUN_WAIT_DEADLINE_SECONDS
        cur: dict[str, Any] | None = None
        while _t.monotonic() < deadline:
            cur = get_run(db_path, write_run_id)
            if cur is not None and cur["status"] in (
                "COMPLETED", "FAILED", "CANCELLED",
            ):
                break
            _t.sleep(0.5)
        if cur is None or cur["status"] != "COMPLETED":
            log.info(
                "gate-revise chain: write run %s status=%s，不接力审校",
                write_run_id, cur["status"] if cur else None,
            )
            return
        _run_workflow_return_payload(
            engine, db_path, "chapter-review", project_id, chapter_id,
            mock_providers, initial_ctx_extra=initial_ctx_extra,
        )
        log.info(
            "gate-revise chain: chapter_id=%s 已接力 chapter-review（write_run=%s）",
            chapter_id, write_run_id,
        )
    except Exception as exc:  # noqa: BLE001 —— daemon 线程，异常不外抛
        log.exception(
            "gate-revise chain crashed: chapter_id=%s write_run=%s err=%s",
            chapter_id, write_run_id, exc,
        )


def _auto_revise_loop(
    engine: WorkflowEngine,
    db_path: str,
    project_id: str,
    chapter_id: str,
    mock_providers: dict[str, list[str]] | None,
    max_iter: int,
    model_overrides: dict[str, str] | None = None,
    *,
    parent_run_id: str | None = None,
) -> dict[str, Any]:
    """P0 自动改稿回路：重跑 chapter-write → chapter-review 直到 approved 或达上限。

    返回最终 run 的标准 payload。write 失败或 review 非 revise 失败时直接返回。
    review 达到 PAUSED（待人工审批）时直接返回 PAUSED。
    review 继续 revise 失败时进入下一轮，最多 ``max_iter`` 轮。

    缺陷 1（P0 高）修复：当 write 或 review 子 run 等待超时（payload 含 ``timeout=True``，
    即 run 仍 RUNNING）时，不继续下一轮——后台线程会自行到终态，前端通过 GET /runs 轮询。

    V3.9 批次 4.2：回路在进程内注册表登记（``loop_id``），每轮启动子 run **前**检查
    回路是否已被取消（cancel 端点取消任一子 run → 注册表标记；或已记录子 run 的 DB 状态
    为 CANCELLED）。命中即终止回路，不再启动下一轮 write/review，返回 CANCELLED payload。

    ``model_overrides``：触发回路的 resume 请求的 model_overrides（已按「请求体 >
    原 review run ctx」优先级解析）。非 None 时透传到回路内每轮 write / review 子 run 的
    ctx，保持与首轮 run 一致的模型档案覆盖；None 时不写入 ctx（与既有「缺省不出现键」
    行为一致，避免下游误读为「空覆盖」）。

    ``parent_run_id``：触发本回路的父 review run（仅用于注册表可读性与日志）。
    """
    final_payload: dict[str, Any] | None = None
    # 仅当非 None 时构造 initial_ctx_extra，避免 None 覆盖行为（缺省不出现键）。
    # 与 _start_workflow 的处理口径保持一致。
    initial_ctx_extra: dict[str, Any] | None = (
        {"model_overrides": model_overrides} if model_overrides is not None else None
    )
    loop_id = new_id("arloop")
    _register_auto_revise_loop(
        loop_id=loop_id,
        parent_run_id=parent_run_id,
        chapter_id=chapter_id,
        project_id=project_id,
        max_iter=max_iter,
    )
    def on_run_started(child_run_id: str) -> None:
        _record_auto_revise_child(loop_id, child_run_id)

    try:
        for iteration in range(1, max_iter + 1):
            # 0) 回路级取消：启动任何子 run 之前先查「父级意图」——
            #    注册表 cancelled 标记（cancel 端点取消任一子 run 时置位）
            #    或已记录子 run 在 DB 中已是 CANCELLED。
            cancelled, reason = _auto_revise_loop_cancelled(loop_id, db_path)
            if cancelled:
                log.info(
                    "auto_revise loop %s 已被取消（%s）：第 %d/%d 轮不再启动子 run",
                    loop_id, reason, iteration, max_iter,
                )
                return {
                    "run_id": parent_run_id or "",
                    "status": "CANCELLED",
                    "current_node": None,
                }
            log.info(
                "auto_revise loop iteration %d/%d for chapter %s",
                iteration, max_iter, chapter_id,
            )
            # 1) 重跑 chapter-write：revision_note 已在 plan_json 中由上一轮 load_plan 带上
            write_payload = _run_workflow_return_payload(
                engine, db_path, "chapter-write", project_id, chapter_id, mock_providers,
                initial_ctx_extra=initial_ctx_extra,
                on_run_started=on_run_started,
            )
            # 缺陷 1（P0 高）：子 run 超时（仍在后台执行），不触发下一轮 review，
            # 直接返回该 payload；后台 run 列表可见，前端轮询。
            if write_payload.get("timeout") or write_payload["status"] == "RUNNING":
                log.warning(
                    "auto_revise_loop 短路: write 超时（run_id=%s），不再触发 review",
                    write_payload.get("run_id"),
                )
                return write_payload
            if write_payload["status"] != "COMPLETED":
                return write_payload

            # 2) 重跑 chapter-review
            review_payload = _run_workflow_return_payload(
                engine, db_path, "chapter-review", project_id, chapter_id, mock_providers,
                initial_ctx_extra=initial_ctx_extra,
                on_run_started=on_run_started,
            )
            # 缺陷 1（P0 高）：同上，review 超时短路
            if review_payload.get("timeout") or review_payload["status"] == "RUNNING":
                log.warning(
                    "auto_revise_loop 短路: review 超时（run_id=%s），不再触发下一轮 write",
                    review_payload.get("run_id"),
                )
                return review_payload
            if review_payload["status"] == "COMPLETED":
                return review_payload
            if review_payload["status"] == "PAUSED":
                return review_payload

            # FAILED：检查是否为 rejected-for-revision，是则继续下一轮
            run = get_run(db_path, review_payload["run_id"])
            error = (run or {}).get("error") or ""
            if "rejected-for-revision" not in str(error):
                return review_payload
            final_payload = review_payload
    finally:
        _finish_auto_revise_loop(loop_id)

    # 达到上限仍未 approved：返回最后一轮 review 的 FAILED payload
    return final_payload or {
        "run_id": "",
        "status": "FAILED",
        "current_node": None,
    }


# ---------------------------------------------------------------------------
# Endpoints（gate-revise）
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/gate-revise",
    status_code=status.HTTP_201_CREATED,
)
def start_gate_revise(
    project_id: str,
    chapter_id: str,
    body: GateReviseRequest,
    request: Request,
) -> dict[str, Any]:
    """按门禁建议改稿（V3.9 批次 4.1）：一键把 quality_gate 阻断变成改稿闭环。

    语义：
    - 前置：chapter 的 ``plan_json.gate_blocked`` 非空（quality_gate enforce 阻断时写入的
      标记；改稿意见 ``plan_json.revision_note`` 同批写入）→ 无标记 → 409；
    - 启动 chapter-write：``revision_note`` 存在 ⇒ write 节点走 revise 模式（定向改稿）；
    - write COMPLETED 后 daemon 线程（``gate-revise-{run_id}``）接力 chapter-review，
      审校停在 author_review 等作者决议；作者批准后章节回到 REVIEWED，可重新提交；
    - 复用 :func:`_start_workflow`（chapter 校验 / 活跃 run 并发防护 / 409 语义）与
      :func:`_run_workflow_return_payload`（同 auto_revise 回路的子 run 口径），
      不新建并行机制。

    返回：与其他 start 端点同形（``{run_id, status, current_node}``，201）。
    """
    _check_chapter(request, project_id, chapter_id)
    settings = request.app.state.settings
    db_path = str(settings.db_path)

    if _read_pending_gate_block(db_path, chapter_id) is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"chapter {chapter_id!r} has no pending quality-gate block; "
                f"gate-revise 仅在 quality_gate enforce 阻断后可用"
            ),
        )

    payload = _start_workflow(
        workflow_name="chapter-write",
        request=request,
        project_id=project_id,
        chapter_id=chapter_id,
        body=StartWorkflowRequest(
            mock_providers=body.mock_providers,
            model_overrides=body.model_overrides,
            critic_mode=body.critic_mode,
            deep_review=body.deep_review,
        ),
    )

    # 接力审校的 ctx 透传：与 _start_workflow 同口径（仅非 None / 显式 True 才写入键）。
    review_ctx_extra: dict[str, Any] = {}
    if body.model_overrides is not None:
        review_ctx_extra["model_overrides"] = body.model_overrides
    if body.critic_mode is not None:
        review_ctx_extra["critic_mode"] = body.critic_mode
    if body.deep_review is True:
        review_ctx_extra["deep_review"] = True

    engine = _engine(request)
    t = threading.Thread(
        target=_chain_review_after_write,
        args=(
            engine,
            db_path,
            project_id,
            chapter_id,
            payload["run_id"],
            body.mock_providers,
            review_ctx_extra or None,
        ),
        name=f"gate-revise-{payload['run_id']}",
        daemon=True,
    )
    t.start()
    log.info(
        "gate-revise chain started: chapter_id=%s write_run=%s thread=%s",
        chapter_id, payload["run_id"], t.name,
    )
    return payload
