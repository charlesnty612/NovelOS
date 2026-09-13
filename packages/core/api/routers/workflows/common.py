"""workflows 路由包共享层：请求模型 / 依赖 helper / auto_revise 回路注册表 / 子 run 启动口径。

V4.0 模块化重构 V2：自 ``packages/core/api/routers/workflows.py``（单文件 1675 行）
按端点域拆包，本模块承载被 ``control`` / ``runs`` / ``revise`` 共用的部分——
纯搬家零逻辑变更。

依赖方向：``common ← control / runs / revise``；本模块不得 import 同包兄弟模块。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from packages.core.logging_config import get_logger
from packages.core.workflow_registry import get_workflow
from packages.core.workflow_runtime.engine import WorkflowEngine
from packages.core.workflow_runtime.runs import get_run
from packages.domain.chapter.service import ChapterService

log = get_logger("novelos.routers.workflows")


# 缺陷 1（P0 高）：_run_workflow_return_payload 等待 run 终态的 deadline。
# 提为模块级常量便于测试注入（短超时复现），生产仍是 600s 覆盖真实 write 几分钟量级。
_RUN_WAIT_DEADLINE_SECONDS: float = 600.0


# ---------------------------------------------------------------------------
# Schemas（请求模型；同包多个子模块共用）
# ---------------------------------------------------------------------------


class StartWorkflowRequest(BaseModel):
    author_intent: str | None = None
    expected_role: str | None = None
    target_word_count: int | None = Field(default=None, ge=100, le=100_000)
    mock_providers: dict[str, list[str]] | None = None
    # Sprint 6 下半：chapter_commit pipeline 的 quality_gate 节点拦截模式
    # （"enforce" 阻断 / "report" 不阻断）；任务书拍板默认 report；
    # 显式注入便于测试覆盖两种模式。
    quality_gate_mode: str | None = None
    # V3 P0-1：chapter-review critic 采样模式（off / sample / always）。
    # 仅 chapter-review 节点读取；其他 workflow 忽略。优先级高于 NOVELOS_CRITIC_MODE 环境变量。
    critic_mode: str | None = Field(default=None, pattern="^(off|sample|always)$")
    # 单次 run 级别模型档案覆盖：key=capability 名（如 creative_writing/reasoning/light），
    # value=model_profiles.profile_id。本次 run 对应 capability 的 agent 调用强制使用该档案，
    # 不影响全局 capability_bindings。None 或缺省 → 不覆盖（走既有 capability_bindings / model_configs）。
    model_overrides: dict[str, str] | None = None
    # chapter-write 全章重写：True 时忽略 plan_json.revision_note 与最新 draft，
    # 强制走 write 模式（用于跨模型文风对比）。None/False → 维持既有 revise 判定。
    fresh_write: bool | None = None
    # chapter-review 指定审校稿版本：用户对比多模型多版本草稿时，可指定审 v7/v8 等
    # 特定版本（不再强制只审最新稿）。None → 维持既有"取最新 draft"语义。
    draft_version: int | None = Field(default=None, ge=1)
    # V1.3：deep_review 二审 AI 节点开关。仅 chapter-review 节点读取；其他 workflow 忽略。
    # None/False → 跳过 deep_reviewer（不调 AI）；True → 调 deep_reviewer 按三层清单核销。
    # verdict=revise 不驳回 run（advisory），与 critic 默认 always 形成差异化——critic 写法层
    # 每章评，deep_reviewer 事实层按需启用。
    deep_review: bool | None = None


class ProjectInitRequest(BaseModel):
    """project-init 触发请求体。

    - ``brief`` 必填：genre / logline / platform / target_words / title / author_notes。
    - ``project_id`` 可选：传入则挂载到已有项目并更新；不传则创建新项目。
    - ``chapter_seed_count`` 可选：默认 10 章。
    - ``mock_providers`` 可选：测试用脚本化 LLM 输出。
    - ``selected_stages`` 可选：仅跑白名单内的环节；未选环节从落库数据重建为
      下游 AI 输入，不调 AI、不抛 PauseRequested。省略时全选（与既有行为一致）。
    - ``model_profile_id`` 可选：本次初始化全部 4 个 AI 节点（premise_designer /
      world_builder / character_designer / volume_outliner）统一使用该 model_profiles
      档案调用 LLM；不影响全局 capability_bindings。None/缺省 → 走全局
      capability_bindings / model_configs。
    """

    brief: dict[str, Any]
    project_id: str | None = None
    # 上限 500：与 pipeline 推导 clamp（[10,500]）对齐，支持百万字 ÷ 单章 3000 ≈ 333 章的推导结果
    chapter_seed_count: int | None = Field(default=None, ge=1, le=500)
    mock_providers: dict[str, list[str]] | None = None
    # 分段审阅：True 时按 4 关卡（题材定位 → 世界观 → 核心角色 → 卷纲与章节种子）
    # 暂停等待人工审阅修订，通过 resume 端点 human_input.revisions 回灌并生效；
    # 省略/False 时保持一次性跑完（与既有行为一致）。
    step_mode: bool | None = None
    # 环节可选复用：仅跑白名单内的环节；未选环节从落库数据重建为下游 AI 输入。
    # 省略或 null 时等价于 ["premise", "world", "character", "outline"]。
    # 非法值（不在白名单内）→ 422。
    selected_stages: list[str] | None = None
    # 单次 run 级模型档案覆盖：本次初始化全部 AI 节点统一使用该档案；None → 走
    # 全局 capability_bindings。后端会校验该 profile_id 存在（model_profiles 表），
    # 不存在 → 400。
    model_profile_id: str | None = None


class ResumeRequest(BaseModel):
    human_input: dict[str, Any] | None = None
    # P0 自动改稿回路：resume chapter-review 被 revise 驳回时，可自动重跑 write→review。
    # 默认 2；0 表示禁用（保持原有 FAILED 终态）。
    auto_revise_max: int | None = Field(default=None, ge=0, le=10)
    # 自动回路中新的 write / review run 需要 mock 脚本时传入；不传则从原 run checkpoint 继承。
    mock_providers: dict[str, list[str]] | None = None
    # 重生成：true 时重跑当前挂起节点自身（而非下一节点）；可与 human_input.regenerate_note
    # 配合传一条重生成意见，pipeline 会注入到对应 AI 节点的 payload。
    regenerate: bool | None = None
    # 单次 run 级模型档案覆盖：与 StartWorkflowRequest.model_overrides 同语义。
    # 自动改稿回路（auto_revise）触发时，回路内重跑的 write / review 子 run 必须把同一份
    # model_overrides 透传到各自 ctx，避免首轮指定的档案在改稿回路里丢失。
    # None → 不覆盖（保持现状）；缺省从原 review run 的 ctx 中继承（如能取到）。
    model_overrides: dict[str, str] | None = None


class GateReviseRequest(BaseModel):
    """「按门禁建议改稿」请求体（V3.9 批次 4.1）。

    只收「本次改稿 + 接力审校」用得到的透传字段：
    - ``mock_providers``：mock 脚本（write 与接力 review 共用同一份）；
    - ``model_overrides``：与其它 start 端点同语义的单次 run 级模型档案覆盖；
    - ``critic_mode`` / ``deep_review``：接力审校时会读取的开关。
    改稿意见本身不在这里传——它在 quality_gate 阻断时已写入 ``plan_json.revision_note``。
    """

    mock_providers: dict[str, list[str]] | None = None
    model_overrides: dict[str, str] | None = None
    critic_mode: str | None = Field(default=None, pattern="^(off|sample|always)$")
    deep_review: bool | None = None


# ---------------------------------------------------------------------------
# Helpers（共享依赖与派生）
# ---------------------------------------------------------------------------


def _engine(request: Request) -> WorkflowEngine:
    settings = request.app.state.settings
    return WorkflowEngine(settings.db_path)


def _check_chapter(request: Request, project_id: str, chapter_id: str) -> tuple[str, str]:
    """校验 chapter 属于该 project；返回 (project_id, chapter_id)。

    V1.5 起：通过 :class:`ChapterService.get_project_id` 取 project_id；
    chapter 不存在 → 404；chapter 不属于该 project → 400。
    """
    settings = request.app.state.settings
    chapter_pid = ChapterService(settings.db_path).get_project_id(chapter_id)
    if chapter_pid is None:
        raise HTTPException(status_code=404, detail=f"chapter {chapter_id!r} not found")
    if chapter_pid != project_id:
        raise HTTPException(
            status_code=400,
            detail=f"chapter {chapter_id!r} does not belong to project {project_id!r}",
        )
    return chapter_pid, chapter_id


def _init_genesis_if_needed(db_path: str, project_id: str, chapter_id: str) -> None:
    """如无快照则调 init_genesis 创建 v1 快照；供 chapter-write / commit 等依赖 state 的节点使用。"""
    from packages.core.story_state.service import StoryStateService

    svc = StoryStateService(db_path)
    state_version = int((svc.get_current_state(project_id) or {}).get("state_version") or 0)
    if state_version < 1:
        svc.init_genesis(project_id, chapter_id)


def _extract_pause_payload(run: dict[str, Any]) -> dict[str, Any] | None:
    """从 run.checkpoint_json 中抽取 PENDING 节点的 __pause_payload__。

    优先取与 ``current_node`` 对应节点的 payload；当前挂起节点不存在时回退到
    checkpoint 中任意 PENDING 节点的 payload（兼容极端场景）。
    """
    checkpoint = run.get("checkpoint_json") or {}
    current = run.get("current_node")
    if current and isinstance(checkpoint.get(current), dict) and "__pause_payload__" in checkpoint[current]:
        return checkpoint[current]["__pause_payload__"]
    for node_id, val in checkpoint.items():
        if isinstance(val, dict) and "__pause_payload__" in val:
            return val["__pause_payload__"]
    return None


# ---------------------------------------------------------------------------
# V3.9 批次 4.2：auto_revise 回路级取消（进程内注册表）
# ---------------------------------------------------------------------------
#
# 设计取舍：回路只存活在本进程的 daemon 线程里（进程重启即消失），因此回路状态也放在
# 进程内注册表，不落库——落库会产生「重启后 active 标记永远挂着」的僵尸状态，还会扩大
# 存储面（任务书要求不扩大存储面）。记录形态：
#   {loop_id: {"chapter_id", "project_id", "parent_run_id", "max_iter",
#              "child_run_ids": [...], "cancelled": bool,
#              "cancelled_reason": str | None, "started_at": float}}
# 取消入口沿用既有 ``POST /runs/{run_id}/cancel``：取消任一属于本回路的子 run 时，
# 注册表把该回路标记 cancelled；``_auto_revise_loop`` 每轮启动子 run 前同时检查
#   1) 注册表 cancelled 标记（覆盖「回路正在两轮之间、当前没有 RUNNING 子 run」的窗口），
#   2) 已记录子 run 在本进程 DB 里的状态是否 CANCELLED（兜底注册表缺失/竞态）。
# 命中即终止回路，不再启动下一轮 write / review。
_AUTO_REVISE_LOOPS: dict[str, dict[str, Any]] = {}
_AUTO_REVISE_LOOPS_LOCK = threading.Lock()


def _register_auto_revise_loop(
    *,
    loop_id: str,
    parent_run_id: str | None,
    chapter_id: str,
    project_id: str,
    max_iter: int,
) -> None:
    with _AUTO_REVISE_LOOPS_LOCK:
        _AUTO_REVISE_LOOPS[loop_id] = {
            "chapter_id": chapter_id,
            "project_id": project_id,
            "parent_run_id": parent_run_id,
            "max_iter": int(max_iter),
            "child_run_ids": [],
            "cancelled": False,
            "cancelled_reason": None,
            "started_at": time.monotonic(),
        }


def _finish_auto_revise_loop(loop_id: str) -> None:
    with _AUTO_REVISE_LOOPS_LOCK:
        _AUTO_REVISE_LOOPS.pop(loop_id, None)


def _record_auto_revise_child(loop_id: str, run_id: str) -> None:
    if not run_id:
        return
    with _AUTO_REVISE_LOOPS_LOCK:
        rec = _AUTO_REVISE_LOOPS.get(loop_id)
        if rec is None or run_id in rec["child_run_ids"]:
            return
        rec["child_run_ids"].append(run_id)


def _mark_auto_revise_loops_cancelled_for_run(
    run_id: str, *, reason: str = "child-run-cancelled",
) -> list[str]:
    """把「子 run ``run_id`` 所属」的活跃回路标记为 cancelled；返回命中的 loop_id 列表。"""
    hit: list[str] = []
    with _AUTO_REVISE_LOOPS_LOCK:
        for loop_id, rec in _AUTO_REVISE_LOOPS.items():
            if run_id not in rec["child_run_ids"]:
                continue
            if not rec["cancelled"]:
                rec["cancelled"] = True
                rec["cancelled_reason"] = reason
            hit.append(loop_id)
    return hit


def _auto_revise_loop_cancelled(loop_id: str, db_path: str) -> tuple[bool, str]:
    """回路是否应终止；返回 ``(cancelled, reason)``。"""
    with _AUTO_REVISE_LOOPS_LOCK:
        rec = _AUTO_REVISE_LOOPS.get(loop_id)
        snapshot = dict(rec) if rec is not None else None
    if snapshot is None:
        return False, ""
    if snapshot["cancelled"]:
        return True, str(snapshot.get("cancelled_reason") or "cancelled")
    for child_run_id in snapshot["child_run_ids"]:
        child = get_run(db_path, child_run_id)
        if child is not None and child.get("status") == "CANCELLED":
            with _AUTO_REVISE_LOOPS_LOCK:
                cur = _AUTO_REVISE_LOOPS.get(loop_id)
                if cur is not None and not cur["cancelled"]:
                    cur["cancelled"] = True
                    cur["cancelled_reason"] = f"child-run-{child_run_id}-cancelled"
            return True, f"child-run-{child_run_id}-cancelled"
    return False, ""


def _run_workflow_return_payload(
    engine: WorkflowEngine,
    db_path: str,
    workflow_name: str,
    project_id: str,
    chapter_id: str,
    mock_providers: dict[str, list[str]] | None,
    initial_ctx_extra: dict[str, Any] | None = None,
    *,
    wait_deadline_seconds: float | None = None,
    on_run_started: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """启动指定 workflow 并返回标准响应 payload（run_id / status / current_node / pause_payload）。

    缺陷 1（P0 高）修复：等待 deadline（默认 ``_RUN_WAIT_DEADLINE_SECONDS`` = 600s）到了之后，
    若 run 仍未到终态（仍 RUNNING/PENDING），payload 显式标记 ``timeout=True`` 并附加人类可读
    ``detail``，明确告知调用方后台 run 仍在执行；不杀后台线程（它会自行到终态）。
    ``wait_deadline_seconds`` 仅用于测试注入，生产调用方不传。

    ``on_run_started``：子 run 落库（status=RUNNING）后的即时回调，供 auto_revise 回路的
    注册表在其「在途」阶段就登记该子 run（否则用户在子 run RUNNING 期间取消时，
    回路线程还可能再启动下一轮）。None → 不回调（既有调用方零影响）。
    """
    workflow = get_workflow(workflow_name)
    if workflow is None:
        raise HTTPException(status_code=500, detail=f"workflow {workflow_name!r} not registered")

    if workflow_name in ("chapter-write", "chapter-commit", "chapter-review"):
        _init_genesis_if_needed(db_path, project_id, chapter_id)

    initial_ctx: dict[str, Any] = {
        "db_path": str(db_path),
        "project_id": project_id,
        "chapter_id": chapter_id,
    }
    if initial_ctx_extra:
        initial_ctx.update(initial_ctx_extra)

    run_id = engine.start_with_nodes_async(
        workflow_name,
        workflow["nodes"],
        chapter_id=chapter_id,
        initial_ctx=initial_ctx,
        mock_providers=mock_providers,
        checkpoint_exclude=workflow.get("checkpoint_exclude"),
    )
    if on_run_started is not None:
        # 回调只在注册表登记（O(1)、无 IO）；异常不外抛，避免影响子 run 等待逻辑。
        try:
            on_run_started(run_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("on_run_started callback failed: run_id=%s err=%s", run_id, exc)

    # 异步启动后本函数仅被 auto_revise 回路内部使用：调用方依赖返回的
    # status 判断本轮 write/review 是否 COMPLETED/PAUSED/FAILED，因此必须
    # 同步等到终态（引擎已在后台线程推进，这里只轮询 run 行）。上限默认 600s
    # 覆盖真实 write（几分钟量级）；测试可通过 wait_deadline_seconds 注入短超时。
    import time as _wait_t

    deadline_seconds = (
        wait_deadline_seconds
        if wait_deadline_seconds is not None
        else _RUN_WAIT_DEADLINE_SECONDS
    )
    deadline = _wait_t.monotonic() + deadline_seconds
    run: dict[str, Any] | None = None
    while _wait_t.monotonic() < deadline:
        run = get_run(db_path, run_id)
        if run is not None and run["status"] in ("COMPLETED", "PAUSED", "FAILED", "CANCELLED"):
            break
        _wait_t.sleep(0.5)
    if run is None:
        raise HTTPException(status_code=500, detail="run disappeared after start")
    payload: dict[str, Any] = {
        "run_id": run_id,
        "status": run["status"],
        "current_node": run.get("current_node"),
    }
    if run["status"] == "PAUSED":
        payload["pause_payload"] = _extract_pause_payload(run)
    # 缺陷 1（P0 高）：超时分支显式标记。RUNNING 保留供调用方识别当前实际状态；
    # timeout=True + detail 是「后台仍在跑」的明确信号；不杀后台线程。
    if run["status"] not in ("COMPLETED", "PAUSED", "FAILED", "CANCELLED"):
        payload["timeout"] = True
        payload["detail"] = (
            f"等待 run 终态超时（{int(deadline_seconds)}s），"
            f"run 仍在后台执行；请稍后在 run 列表查看结果"
        )
        log.warning(
            "_run_workflow_return_payload 超时: run_id=%s workflow=%s deadline=%ss",
            run_id, workflow_name, int(deadline_seconds),
        )
    return payload
