"""workflows 路由：gate-revise 接力与 auto_revise 自动改稿回路（V3.9 批次 4.1 / 4.2）。

V4.0 模块化重构 V2：自 ``packages/core/api/routers/workflows.py`` 按端点域拆出，
纯搬家零逻辑变更。内容：

- ``_read_pending_gate_block``：读 ``chapters.plan_json.gate_blocked``（quality_gate
  enforce 阻断落点）；
- ``_chain_review_after_write``：gate-revise 的 daemon 接力线程（write → review）；
- ``_auto_revise_loop``：resume 触发的自动改稿回路（每轮 write → review，直到
  approved / PAUSED / 硬停 / 达上限 / 被取消）；
- ``_read_pending_review_report``：读 pending review 的 ``review_report``（策略的输入）；
- ``POST /projects/{project_id}/chapters/{chapter_id}/gate-revise`` 端点。

回路级取消注册表与子 run 启动口径在 ``common``；cancel 端点在 ``control``；
**失败形状 → 修复动作的策略表**（纯函数）在 ``repair_policy``。
"""

from __future__ import annotations

import json
import sqlite3
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
    _extract_pause_payload,
    _finish_auto_revise_loop,
    _record_auto_revise_child,
    _register_auto_revise_loop,
    _run_workflow_return_payload,
    log,
)
from .control import _start_workflow
from .repair_policy import RepairDecision, decide_repair, describe_exhaustion

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


def _inherit_chapter_ctx_scalar(
    db_path: str, chapter_id: str, key: str,
) -> Any | None:
    """从该章最近一次 run 的 ctx（checkpoint_json 顶层）继承一个标量（m6）。

    用途：``gate-revise`` 在作者没显式传 ``author_intent`` / ``target_word_count``
    时，从**同一章**最近的 run 里取上一次生效值——否则这两个键在改稿链上静默丢失
    （作者按 ``--target-word-count 300`` 起稿后被门禁拦下，点「按门禁建议改稿」
    时 target 退回服务端默认 3000，改稿在错误口径上进行）。与 ``control.py`` 的
    resume 回路继承同语义，但那边只看**父 run**，这里要看「该章最近一次 run」。

    只认非空 str / 正整数（bool 不算）；取不到 → None（调用方据此不写 ctx 键，
    保持「缺省不出现键」纪律）。
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT checkpoint_json FROM workflow_runs
            WHERE chapter_id = ?
            ORDER BY started_at DESC
            LIMIT 5
            """,
            (chapter_id,),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    for row in rows:
        raw = row["checkpoint_json"]
        if not raw:
            continue
        try:
            ckpt = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(ckpt, dict):
            continue
        val = ckpt.get(key)
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, int) and not isinstance(val, bool) and val > 0:
            return val
    return None


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


# ---------------------------------------------------------------------------
# Helpers（读 pending 报告 + 失败形状 → 修复动作）
# ---------------------------------------------------------------------------
#
# 历史（两次线上事故，本回路的来龙去脉）：
#
# ① 2026-09-18 W-LEN 死循环（chapter ch_92bac068ff0d，target=2500 / 带 2125~2875）：
#    writer 首稿 932 字，改稿回路四轮 revise 后 1044 → 1242 → 1300 字（单轮 +4.7%），
#    每轮 review 报同一个 error「W-LEN-DEVIATION … deviation=-65.8%」，回路必然以
#    「轮次耗尽」收尾。两层原因相抵：``docs/agents/prompts/writer-v3.md`` 规则 20
#    规定 revise 净增 ≤ +5%，§6.1 又要求「未提及的部分逐字保留」，而回路把每一轮
#    修复都按 revise 驱动 ⇒ 2 轮 × +5% ≈ +10% 去追 -65%，数学上追不回。
#
# ② 2026-09-18 同日事故 prj_2567bb8de642：给 ① 打补丁引入了 ``fresh_write`` 逃逸，
#    逃逸确实补上了字数，但一次改稿产出 **9 段逐字重复**（章内重复 20.3%）——
#    「修长度的工具」和「造重复的工具」是同一把（revise）。
#
# 结论：单一动作路线（一律 revise，再补一个 fresh_write 特例）到此为止。
# 现在每一轮的动作由 ``repair_policy.decide_repair``（纯函数 + 规则表）按**失败形状**
# 判定，闭集三选一：regenerate / revise / stop。长度大缺口只是表里的一行
# （``length_shortfall_beyond_revise_cap`` ⇒ regenerate），不再是特例分支。


def _read_pending_review_report(db_path: str, run_id: str | None) -> dict[str, Any] | None:
    """读某个 review run 落盘的 ``review_report``（author_review 的 pause payload 内）。

    复用 :func:`_extract_pause_payload` 的 checkpoint 落点解析（与 resume / 前端
    reviewer UI 同一口径），不新开查询。review 在 author_review 挂起时把整份
    review_report 放进 ``__pause_payload__``；resume→驳回（FAILED
    ``rejected-for-revision``）后该镜像键仍留在 checkpoint 里（2026-09-18 实测
    wfr_8f623af4556d 可读到 errors[0].rule_id=W-LEN-DEVIATION）。

    run 不存在 / 无 pause payload / 无 report → None——**不等于「没问题」**：调用方
    （``decide_repair``）把它判成 ``no_review_report`` 硬停，而不是按 revise 蒙一把。
    """
    if not run_id:
        return None
    run = get_run(db_path, run_id)
    if run is None:
        return None
    payload = _extract_pause_payload(run)
    report = (payload or {}).get("review_report")
    return report if isinstance(report, dict) else None


def _annotate_run_error(db_path: str, run_id: str | None, note: str) -> None:
    """把回路结论**追加**到 run 的 ``error`` 字段（幂等），供作者/驱动在 run 列表里读到。

    为什么要落库：回路「停在人工」或「轮次耗尽」时，光有日志的话作者看不到原因——
    review run 只有 ``rejected-for-revision`` 一个通用失败串。追加式写入保留原始子串
    （resume / 驱动都按 ``"rejected-for-revision" in error`` 判别），只在其后接
    `` | auto_revise: …``。异常不外抛：留痕失败不该拖垮回路。
    """
    if not run_id:
        return
    marker = f" | auto_revise: {note}"
    try:
        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT error FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return
            current = row["error"] or ""
            if marker in current:
                return
            conn.execute(
                "UPDATE workflow_runs SET error = ? WHERE run_id = ?",
                (f"{current}{marker}", run_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 —— 留痕失败不影响回路结论
        log.warning("auto_revise annotate run error failed: run_id=%s err=%s", run_id, exc)


def _auto_revise_loop(
    engine: WorkflowEngine,
    db_path: str,
    project_id: str,
    chapter_id: str,
    mock_providers: dict[str, list[str]] | None,
    max_iter: int,
    model_overrides: dict[str, str] | None = None,
    author_intent: str | None = None,
    target_word_count: int | None = None,
    *,
    parent_run_id: str | None = None,
) -> dict[str, Any]:
    """自动改稿回路：每轮「按失败形状选一个动作」→ write → review，直到终态。

    返回最终 run 的标准 payload。write 失败或 review 非 revise 失败时直接返回。
    review 达到 PAUSED（待人工审批）时直接返回 PAUSED。
    review 继续 revise 失败时进入下一轮，最多 ``max_iter`` 轮。

    缺陷 1（P0 高）修复：当 write 或 review 子 run 等待超时（payload 含 ``timeout=True``，
    即 run 仍 RUNNING）时，不继续下一轮——后台线程会自行到终态，前端通过 GET /runs 轮询。

    V3.9 批次 4.2：回路在进程内注册表登记（``loop_id``），每轮启动子 run **前**检查
    回路是否已被取消（cancel 端点取消任一子 run → 注册表标记；或已记录子 run 的 DB 状态
    为 CANCELLED）。命中即终止回路，不再启动下一轮 write/review，返回 CANCELLED payload。

    2026-09-18（本批）：**单一动作路线改为策略表**。每轮在启动任何子 run 之前，先读
    pending review 的 ``review_report``，交给 :func:`repair_policy.decide_repair`
    （纯函数 + 规则表）判一个动作：

    - ``regenerate`` ⇒ 本轮 write 带 ``fresh_write``（writer 回 mode='write'，忽略旧稿与
      ``revision_note``）：章内重复、字数带下限大缺口；
    - ``revise`` ⇒ 与既有行为一致（按 ``plan_json.revision_note`` 走 capped revise）：
      局部形态类规则、压缩、小缺口长度、作者主观驳回；
    - ``stop`` ⇒ **不启动任何子 run**，直接返回 FAILED payload（带 ``repair_decision`` /
      ``detail``）并把结论追加到该 run 的 ``error``（:func:`_annotate_run_error`）：
      未知 / 缺 rule_id、连续性 / 逻辑 / 设定类规则、读不到报告——不猜、不自动修。

    每轮的决策（含依据）都记日志并进返回 payload；轮次耗尽时返回的 payload 里
    ``detail`` 由 :func:`repair_policy.describe_exhaustion` **点名**修不动的形状。

    ``model_overrides``：触发回路的 resume 请求的 model_overrides（已按「请求体 >
    原 review run ctx」优先级解析）。非 None 时透传到回路内每轮 write / review 子 run 的
    ctx，保持与首轮 run 一致的模型档案覆盖；None 时不写入 ctx（与既有「缺省不出现键」
    行为一致，避免下游误读为「空覆盖」）。

    ``author_intent``：触发回路的 resume 请求的 author_intent（同样按「请求体 > 原 review
    run ctx」优先级解析）。非 None 时透传到回路内每轮 write / review 子 run 的 ctx，与
    model_overrides 并列存在、互不覆盖；None 时不写入 ctx。
    2026-09-16 补（F-11 实证）：回路原先只透传 model_overrides，作者侧硬性要求（本书铁律
    等）在改稿轮全部丢失——新书 01 的 ch2 经一轮改稿后正文冒出内部字段名
    ``recalled_passages``（v1 干净、v2 带毒），因为改稿轮是在无任何作者约束的状态下重写的。

    ``target_word_count``：触发回路的 resume 请求的目标字数（同样按「请求体 > 原 review
    run ctx」优先级解析）。非 None 时透传到每轮 write / review 子 run 的 ctx——
    2026-09-18 实证（另一路端到端）：作者按 ``--target-word-count 300`` 起稿，
    首轮 review 判「288/300 字」，改稿回路的 review 子 run 却判「288/3000 字」——
    子 run 拿不到 target 就退回服务端默认 3000，**回路据此在错误的口径上判定成败**，
    修的是「离 3000 差太远」而不是作者要的 300。

    三个键都为空时 ``initial_ctx_extra`` 保持 None，不得退化成空 dict。

    ``parent_run_id``：触发本回路的父 review run（注册表可读性与日志；同时是第 1 轮
    动作判定的 review_report 来源）。
    """
    final_payload: dict[str, Any] | None = None
    # 仅当至少一个键非 None 时才构造 initial_ctx_extra，避免 None 覆盖行为（缺省不出现键）。
    # 与 _start_workflow 的处理口径保持一致；三个键可同时存在（互不覆盖）。
    _ctx_extra: dict[str, Any] = {}
    if model_overrides is not None:
        _ctx_extra["model_overrides"] = model_overrides
    if author_intent is not None:
        _ctx_extra["author_intent"] = author_intent
    if target_word_count is not None:
        _ctx_extra["target_word_count"] = int(target_word_count)
    initial_ctx_extra: dict[str, Any] | None = _ctx_extra or None
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

    # 动作判定的报告来源：第 1 轮用触发回路的父 review run（驳回改稿时它的 checkpoint
    # 里留着 review_report），之后每轮用上一轮 review 子 run 的报告（新稿会被重新审）。
    pending_review_run_id = parent_run_id
    attempted_reasons: list[str] = []
    last_decision: RepairDecision | None = None
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
            # 1) 失败形状 → 修复动作（策略表，纯函数；决策依据全进日志与 payload）
            remaining_rounds = max_iter - iteration + 1
            report = _read_pending_review_report(db_path, pending_review_run_id)
            decision = decide_repair(report, remaining_rounds=remaining_rounds)
            last_decision = decision
            log.info(
                "auto_revise loop %s 第 %d/%d 轮：action=%s reason=%s rule_ids=%s — %s",
                loop_id, iteration, max_iter, decision.action, decision.reason,
                list(decision.rule_ids), decision.detail,
            )
            if decision.action == "stop":
                # 硬停：不认识 / 读不到的形状一律交给作者，不启动任何子 run（不猜、不自动修）。
                log.warning(
                    "auto_revise loop %s 停在人工：%s（%s）",
                    loop_id, decision.reason, decision.detail,
                )
                _annotate_run_error(
                    db_path, pending_review_run_id,
                    f"stopped at iteration {iteration}/{max_iter}: "
                    f"{decision.reason} — {decision.detail}",
                )
                return {
                    "run_id": parent_run_id or "",
                    "status": "FAILED",
                    "current_node": None,
                    "repair_decision": decision.as_dict(),
                    "detail": decision.detail,
                }
            attempted_reasons.append(decision.reason)

            # 2) 重跑 chapter-write：regenerate 时带 fresh_write（忽略旧稿与 revision_note）；
            # 其余动作与既有行为一致（revision_note 已在 plan_json 中由上一轮 load_plan 带上）。
            write_ctx_extra = initial_ctx_extra
            if decision.regenerate:
                write_ctx_extra = {**(initial_ctx_extra or {}), "fresh_write": True}
                log.info(
                    "auto_revise loop %s 第 %d/%d 轮：%s → 本轮 write 走全新重写（fresh_write）",
                    loop_id, iteration, max_iter, decision.reason,
                )
            write_payload = _run_workflow_return_payload(
                engine, db_path, "chapter-write", project_id, chapter_id, mock_providers,
                initial_ctx_extra=write_ctx_extra,
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

            # 3) 重跑 chapter-review（target_word_count 随 ctx 透传：
            #    子 run 必须按与父 run 同一个目标字数判字数带）
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
            # 下一轮的动作判定改用本轮 review 的报告（新稿已被重新审）。
            pending_review_run_id = review_payload.get("run_id")
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

    # 达到上限仍未 approved：返回最后一轮 review 的 FAILED payload，并**点名**修不动的形状。
    exhausted = dict(
        final_payload or {"run_id": "", "status": "FAILED", "current_node": None}
    )
    exhausted["detail"] = describe_exhaustion(attempted_reasons, max_iter)
    if last_decision is not None:
        exhausted["repair_decision"] = last_decision.as_dict()
    _annotate_run_error(db_path, pending_review_run_id, exhausted["detail"])
    log.warning("auto_revise loop %s 轮次耗尽：%s", loop_id, exhausted["detail"])
    return exhausted


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

    m6（2026-09-21 检修）：``target_word_count`` / ``author_intent`` 随改稿链透传
    （请求体显式给出 > 该章最近一次 run 的 ctx 继承）。此前这两个键在此链上整体缺失，
    作者按非默认字数起稿后被门禁拦下，一键改稿会退回服务端默认 3000 去判字数带。

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
            # m6（2026-09-21）：目标字数与作者意图必须跟着改稿链走。作者没显式传时
            # 从该章最近一次 run 的 ctx 继承（与 resume 回路同语义），取不到才不写键。
            target_word_count=(
                body.target_word_count
                if body.target_word_count is not None
                else _inherit_chapter_ctx_scalar(db_path, chapter_id, "target_word_count")
            ),
            author_intent=(
                body.author_intent
                if body.author_intent is not None
                else _inherit_chapter_ctx_scalar(db_path, chapter_id, "author_intent")
            ),
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
    # m6：接力审校必须与本次 write 用同一个 target——否则 review 子 run 退回服务端
    # 默认 3000 判字数带，回路在错误口径上判定成败（2026-09-18 同形实证）。
    effective_target = (
        body.target_word_count
        if body.target_word_count is not None
        else _inherit_chapter_ctx_scalar(db_path, chapter_id, "target_word_count")
    )
    if effective_target is not None:
        review_ctx_extra["target_word_count"] = int(effective_target)
    effective_intent = (
        body.author_intent
        if body.author_intent is not None
        else _inherit_chapter_ctx_scalar(db_path, chapter_id, "author_intent")
    )
    if effective_intent is not None:
        review_ctx_extra["author_intent"] = effective_intent

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
