"""Workflow REST 路由（Sprint 4-A + V1.5 架构整理 + P0 自动改稿回路）。

端点（挂在 ``/api`` 前缀下）：
- ``POST /projects/{project_id}/chapters/{chapter_id}/plan``    — 启动 chapter-plan
- ``POST /projects/{project_id}/chapters/{chapter_id}/write``   — 启动 chapter-write
- ``POST /projects/{project_id}/chapters/{chapter_id}/review``  — 启动 chapter-review
- ``POST /projects/{project_id}/chapters/{chapter_id}/commit``  — 启动 chapter-commit
- ``POST /projects/{project_id}/chapters/{chapter_id}/gate-revise`` — 按门禁建议改稿（V3.9 4.1）
- ``GET  /runs/{run_id}``                                          — run + 节点明细
- ``POST /runs/{run_id}/resume``                                   — 恢复 PAUSED run（P0 支持 auto_revise 自动改稿回路）
- ``POST /runs/{run_id}/cancel``                                   — 协作式取消 RUNNING run
- ``GET  /projects/{project_id}/runs``                             — list runs
- ``GET  /chapters/{chapter_id}/context-preview``                  — Sprint 13 下半 dry-run

请求体：
- ``{author_intent?: str, mock_providers?: {agent_name: [str, ...]}}`` — start
- ``{human_input?: dict, auto_revise_max?: int, mock_providers?: {...}}`` — resume

返回：
- 201（start）→ ``{run_id, status, ...}``；若 PAUSED 则附加 ``pause_payload``
- 200（get/list/resume）→ run dict（含 nodes 数组）或 list

V1.5 越层整改：
- 路由不再直接写 SQL——chapter 校验走 :class:`ChapterService.get_project_id`，
  resume 的 workflow_name 反查走 :func:`packages.core.workflow_runtime.runs.get_workflow_name_for_run`。

P0 自动改稿回路：
- ``resume`` chapter-review 时若 human_input 决议为 revise，且 ``auto_revise_max > 0``，
  则自动依次重跑 chapter-write → chapter-review，直到 approved（COMPLETED）或达到上限。
- 上限由请求体 ``auto_revise_max`` 或环境变量 ``NOVELOS_AUTO_REVISE_MAX`` 决定，默认 ``2``，
  ``0`` 表示禁用（保持现状）。
- **异步化（P0 续）**：resume 启动 ``resume_async`` 后立即返回 RUNNING；
  auto_revise 改稿回路在 daemon 线程（``auto-revise-{run_id}``）内执行，HTTP 不再阻塞
  等回路结束（最长可能几十分钟）。前端通过 ``GET /runs/{id}`` 或 list 端点轮询拿
  回路产生的子 run 状态。

V3.9 批次 4.1（失败闭环）：
- ``gate-revise`` 端点复用既有 write（revise 模式，消费 ``plan_json.revision_note``）
  → review 的链路，把「quality_gate enforce 阻断 → 只能手工改稿」补成「一键按门禁建议改稿」。
- 阻断落点（``plan_json.revision_note`` + ``gate_blocked``）由 chapter_commit 的
  quality_gate 节点写入；本模块只提供触发路径与接力。

V3.9 批次 4.2（回路治理）：
- 回路级取消：auto_revise 回路登记在进程内注册表；``POST /runs/{id}/cancel`` 取消任一
  回路子 run 时把回路标记为 cancelled，回路线程每轮启动子 run 前检查标记 + 已记录子 run
  的 DB 状态，命中即终止（详见 ``_AUTO_REVISE_LOOPS`` 注释）。

V4.0 模块化重构 V2（单文件 1675 行 → 按端点域拆包，纯搬家零逻辑变更）布局：
- ``runs``    run 查询 / 详情 / 列表与只读派生（GET 系列）
- ``control`` 启动（plan / write / review / commit / project-init）/ resume / cancel
- ``revise``  gate-revise 端点与 auto_revise 自动改稿回路（daemon 线程）
- ``common``  共享请求模型 / 依赖 helper / 回路注册表 / 子 run 启动口径

本文件为门面：组装 ``router``（``discover_routers`` 自动发现入口）并保留拆分前模块
全部顶层名的再导出——``from packages.core.api.routers.workflows import X``（含私有名）
与 ``workflows.X`` 属性访问行为不变；新代码请直接从子模块导入。
"""

from __future__ import annotations

from fastapi import APIRouter

from . import control, revise, runs
from .common import (
    _AUTO_REVISE_LOOPS,
    _AUTO_REVISE_LOOPS_LOCK,
    _RUN_WAIT_DEADLINE_SECONDS,
    GateReviseRequest,
    ProjectInitRequest,
    ResumeRequest,
    StartWorkflowRequest,
    _auto_revise_loop_cancelled,
    _check_chapter,
    _engine,
    _extract_pause_payload,
    _finish_auto_revise_loop,
    _init_genesis_if_needed,
    _mark_auto_revise_loops_cancelled_for_run,
    _record_auto_revise_child,
    _register_auto_revise_loop,
    _run_workflow_return_payload,
    log,
)
from .control import (
    PROJECT_INIT_STAGES,
    _check_active_run_for_chapter,
    _guard_commit_draft_freshness,
    _latest_completed_review_end,
    _latest_draft_after,
    _resolve_auto_revise_max,
    _start_project_init,
    _start_workflow,
    _validate_selected_stages,
    cancel_run,
    resume_run,
    start_commit,
    start_plan,
    start_project_init,
    start_review,
    start_write,
)
from .revise import (
    _auto_revise_loop,
    _chain_review_after_write,
    _read_pending_gate_block,
    start_gate_revise,
)
from .runs import (
    _WORKFLOW_ZH_LABELS,
    _collect_stage_models,
    _compute_run_label,
    _init_status_for_project,
    get_chapter_context_preview,
    get_init_status,
    get_run_endpoint,
    list_runs_endpoint,
)

router = APIRouter()
# 子 router 自带 tags=["workflows"]（本层不再叠加，避免 tag 重复）。注册顺序即路由
# 注册顺序（OpenAPI paths 键序随之变化）；路径集合 / 方法 / 响应模型 / 端点函数名
# 与拆分前完全一致。
router.include_router(control.router)
router.include_router(runs.router)
router.include_router(revise.router)

__all__ = [
    "PROJECT_INIT_STAGES",
    "GateReviseRequest",
    "ProjectInitRequest",
    "ResumeRequest",
    "StartWorkflowRequest",
    "_AUTO_REVISE_LOOPS",
    "_AUTO_REVISE_LOOPS_LOCK",
    "_RUN_WAIT_DEADLINE_SECONDS",
    "_WORKFLOW_ZH_LABELS",
    "_auto_revise_loop",
    "_auto_revise_loop_cancelled",
    "_chain_review_after_write",
    "_check_active_run_for_chapter",
    "_check_chapter",
    "_collect_stage_models",
    "_compute_run_label",
    "_engine",
    "_extract_pause_payload",
    "_finish_auto_revise_loop",
    "_guard_commit_draft_freshness",
    "_init_genesis_if_needed",
    "_init_status_for_project",
    "_latest_completed_review_end",
    "_latest_draft_after",
    "_mark_auto_revise_loops_cancelled_for_run",
    "_read_pending_gate_block",
    "_record_auto_revise_child",
    "_register_auto_revise_loop",
    "_resolve_auto_revise_max",
    "_run_workflow_return_payload",
    "_start_project_init",
    "_start_workflow",
    "_validate_selected_stages",
    "cancel_run",
    "get_chapter_context_preview",
    "get_init_status",
    "get_run_endpoint",
    "list_runs_endpoint",
    "log",
    "resume_run",
    "router",
    "start_commit",
    "start_gate_revise",
    "start_plan",
    "start_project_init",
    "start_review",
    "start_write",
]
