"""Workflow REST 路由（Sprint 4-A + V1.5 架构整理 + P0 自动改稿回路）。

端点（挂在 ``/api`` 前缀下）：
- ``POST /projects/{project_id}/chapters/{chapter_id}/plan``    — 启动 chapter-plan
- ``POST /projects/{project_id}/chapters/{chapter_id}/write``   — 启动 chapter-write
- ``POST /projects/{project_id}/chapters/{chapter_id}/review``  — 启动 chapter-review
- ``POST /projects/{project_id}/chapters/{chapter_id}/commit``  — 启动 chapter-commit
- ``GET  /runs/{run_id}``                                          — run + 节点明细
- ``POST /runs/{run_id}/resume``                                   — 恢复 PAUSED run（P0 支持 auto_revise 自动改稿回路）
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
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from packages.core.logging_config import get_logger
from packages.core.workflow_registry import get_workflow
from packages.core.workflow_runtime.engine import WorkflowEngine
from packages.core.workflow_runtime.runs import (
    get_run,
    get_workflow_name_for_run,
    list_runs,
)
from packages.domain.chapter.service import ChapterService

log = get_logger("novelos.routers.workflows")

router = APIRouter(tags=["workflows"])


# ---------------------------------------------------------------------------
# Schemas
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


class ProjectInitRequest(BaseModel):
    """project-init 触发请求体。

    - ``brief`` 必填：genre / logline / platform / target_words / title / author_notes。
    - ``project_id`` 可选：传入则挂载到已有项目并更新；不传则创建新项目。
    - ``chapter_seed_count`` 可选：默认 10 章。
    - ``mock_providers`` 可选：测试用脚本化 LLM 输出。
    """

    brief: dict[str, Any]
    project_id: str | None = None
    chapter_seed_count: int | None = Field(default=None, ge=1, le=100)
    mock_providers: dict[str, list[str]] | None = None
    # 分段审阅：True 时按 4 关卡（题材定位 → 世界观 → 核心角色 → 卷纲与章节种子）
    # 暂停等待人工审阅修订，通过 resume 端点 human_input.revisions 回灌并生效；
    # 省略/False 时保持一次性跑完（与既有行为一致）。
    step_mode: bool | None = None


class ResumeRequest(BaseModel):
    human_input: dict[str, Any] | None = None
    # P0 自动改稿回路：resume chapter-review 被 revise 驳回时，可自动重跑 write→review。
    # 默认 2；0 表示禁用（保持原有 FAILED 终态）。
    auto_revise_max: int | None = Field(default=None, ge=0, le=10)
    # 自动回路中新的 write / review run 需要 mock 脚本时传入；不传则从原 run checkpoint 继承。
    mock_providers: dict[str, list[str]] | None = None


# ---------------------------------------------------------------------------
# Helpers
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


def _resolve_auto_revise_max(request_max: int | None) -> int:
    """解析自动改稿上限。

    优先级：请求体 ``auto_revise_max`` > 环境变量 ``NOVELOS_AUTO_REVISE_MAX`` > 默认 ``2``。
    ``0`` 表示禁用（保持原有 FAILED 终态）。
    """
    if request_max is not None:
        return int(request_max)
    env = os.environ.get("NOVELOS_AUTO_REVISE_MAX", "2").strip()
    try:
        return int(env)
    except (TypeError, ValueError):
        return 2


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


def _run_workflow_return_payload(
    engine: WorkflowEngine,
    db_path: str,
    workflow_name: str,
    project_id: str,
    chapter_id: str,
    mock_providers: dict[str, list[str]] | None,
    initial_ctx_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """启动指定 workflow 并返回标准响应 payload（run_id / status / current_node / pause_payload）。"""
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

    run_id = engine.start_with_nodes(
        workflow_name,
        workflow["nodes"],
        chapter_id=chapter_id,
        initial_ctx=initial_ctx,
        mock_providers=mock_providers,
        checkpoint_exclude=workflow.get("checkpoint_exclude"),
    )
    run = get_run(db_path, run_id)
    if run is None:
        raise HTTPException(status_code=500, detail="run disappeared after start")
    payload: dict[str, Any] = {
        "run_id": run_id,
        "status": run["status"],
        "current_node": run.get("current_node"),
    }
    if run["status"] == "PAUSED":
        payload["pause_payload"] = _extract_pause_payload(run)
    return payload


def _auto_revise_loop(
    engine: WorkflowEngine,
    db_path: str,
    project_id: str,
    chapter_id: str,
    mock_providers: dict[str, list[str]] | None,
    max_iter: int,
) -> dict[str, Any]:
    """P0 自动改稿回路：重跑 chapter-write → chapter-review 直到 approved 或达上限。

    返回最终 run 的标准 payload。write 失败或 review 非 revise 失败时直接返回。
    review 达到 PAUSED（待人工审批）时直接返回 PAUSED。
    review 继续 revise 失败时进入下一轮，最多 ``max_iter`` 轮。
    """
    final_payload: dict[str, Any] | None = None
    for iteration in range(1, max_iter + 1):
        log.info(
            "auto_revise loop iteration %d/%d for chapter %s",
            iteration, max_iter, chapter_id,
        )
        # 1) 重跑 chapter-write：revision_note 已在 plan_json 中由上一轮 load_plan 带上
        write_payload = _run_workflow_return_payload(
            engine, db_path, "chapter-write", project_id, chapter_id, mock_providers
        )
        if write_payload["status"] != "COMPLETED":
            return write_payload

        # 2) 重跑 chapter-review
        review_payload = _run_workflow_return_payload(
            engine, db_path, "chapter-review", project_id, chapter_id, mock_providers
        )
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

    # 达到上限仍未 approved：返回最后一轮 review 的 FAILED payload
    return final_payload or {
        "run_id": "",
        "status": "FAILED",
        "current_node": None,
    }


def _start_workflow(
    *,
    workflow_name: str,
    request: Request,
    project_id: str,
    chapter_id: str,
    body: StartWorkflowRequest,
) -> dict[str, Any]:
    _check_chapter(request, project_id, chapter_id)
    settings = request.app.state.settings
    db_path = settings.db_path

    # plan 不依赖 story_state；write/commit/review 需要 init_genesis（如尚未）
    if workflow_name in ("chapter-write", "chapter-commit", "chapter-review"):
        _init_genesis_if_needed(db_path, project_id, chapter_id)

    workflow = get_workflow(workflow_name)
    if workflow is None:
        raise HTTPException(status_code=500, detail=f"workflow {workflow_name!r} not registered")

    initial_ctx: dict[str, Any] = {
        "db_path": str(db_path),
        "project_id": project_id,
        "chapter_id": chapter_id,
    }
    if body.author_intent is not None:
        initial_ctx["author_intent"] = body.author_intent
    if body.expected_role is not None:
        initial_ctx["expected_role"] = body.expected_role
    if body.target_word_count is not None:
        initial_ctx["target_word_count"] = body.target_word_count
    if body.mock_providers:
        initial_ctx["mock_providers"] = body.mock_providers
    if body.quality_gate_mode is not None:
        initial_ctx["quality_gate_mode"] = body.quality_gate_mode
    # V3 P0-1：仅 chapter-review 节点读取；其他 workflow 收到此字段会被 pipeline 忽略。
    if body.critic_mode is not None:
        initial_ctx["critic_mode"] = body.critic_mode

    engine = _engine(request)
    try:
        run_id = engine.start_with_nodes(
            workflow_name,
            workflow["nodes"],
            chapter_id=chapter_id,
            initial_ctx=initial_ctx,
            mock_providers=body.mock_providers,
            checkpoint_exclude=workflow.get("checkpoint_exclude"),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run = get_run(db_path, run_id)
    if run is None:
        raise HTTPException(status_code=500, detail="run disappeared after start")
    payload: dict[str, Any] = {
        "run_id": run_id,
        "status": run["status"],
        "current_node": run.get("current_node"),
    }
    if run["status"] == "PAUSED":
        payload["pause_payload"] = _extract_pause_payload(run)
    return payload


def _start_project_init(
    *,
    request: Request,
    body: ProjectInitRequest,
) -> dict[str, Any]:
    """启动 project-init workflow。

    若 body.project_id 存在则校验并挂载；否则由 persist_all 节点创建新项目。
    """
    settings = request.app.state.settings
    db_path = settings.db_path

    workflow = get_workflow("project-init")
    if workflow is None:
        raise HTTPException(status_code=500, detail="workflow 'project-init' not registered")

    # 校验现有项目存在性
    if body.project_id is not None:
        from packages.domain.project.service import ProjectService

        if ProjectService(db_path).get(body.project_id) is None:
            raise HTTPException(
                status_code=404, detail=f"project {body.project_id!r} not found"
            )

    initial_ctx: dict[str, Any] = {
        "db_path": str(db_path),
        "brief": body.brief,
    }
    if body.project_id is not None:
        initial_ctx["project_id"] = body.project_id
    if body.chapter_seed_count is not None:
        initial_ctx["chapter_seed_count"] = body.chapter_seed_count
    if body.mock_providers:
        initial_ctx["mock_providers"] = body.mock_providers
    if body.step_mode is not None:
        initial_ctx["step_mode"] = body.step_mode

    engine = _engine(request)
    try:
        run_id = engine.start_with_nodes(
            "project-init",
            workflow["nodes"],
            initial_ctx=initial_ctx,
            mock_providers=body.mock_providers,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run = get_run(db_path, run_id)
    if run is None:
        raise HTTPException(status_code=500, detail="run disappeared after start")
    out: dict[str, Any] = {
        "run_id": run_id,
        "status": run["status"],
        "current_node": run.get("current_node"),
        "project_id": (run.get("checkpoint_json") or {}).get("project_id"),
    }
    if run["status"] == "PAUSED":
        out["pause_payload"] = _extract_pause_payload(run)
    return out


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/projects/init",
    status_code=status.HTTP_201_CREATED,
)
def start_project_init(
    body: ProjectInitRequest,
    request: Request,
) -> dict[str, Any]:
    """project-init：从题材 brief 生成 Story Bible 并落库。

    ``brief`` 字段示例::

        {
          "genre": "玄幻",
          "logline": "少年得古籍，逆天改命",
          "platform": "起点",
          "target_words": 300000,
          "title": "九天星辰诀",
          "author_notes": "快节奏，爽文"
        }
    """
    return _start_project_init(request=request, body=body)


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/plan",
    status_code=status.HTTP_201_CREATED,
)
def start_plan(
    project_id: str,
    chapter_id: str,
    body: StartWorkflowRequest,
    request: Request,
) -> dict[str, Any]:
    return _start_workflow(
        workflow_name="chapter-plan",
        request=request,
        project_id=project_id,
        chapter_id=chapter_id,
        body=body,
    )


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/write",
    status_code=status.HTTP_201_CREATED,
)
def start_write(
    project_id: str,
    chapter_id: str,
    body: StartWorkflowRequest,
    request: Request,
) -> dict[str, Any]:
    return _start_workflow(
        workflow_name="chapter-write",
        request=request,
        project_id=project_id,
        chapter_id=chapter_id,
        body=body,
    )


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/review",
    status_code=status.HTTP_201_CREATED,
)
def start_review(
    project_id: str,
    chapter_id: str,
    body: StartWorkflowRequest,
    request: Request,
) -> dict[str, Any]:
    return _start_workflow(
        workflow_name="chapter-review",
        request=request,
        project_id=project_id,
        chapter_id=chapter_id,
        body=body,
    )


@router.post(
    "/projects/{project_id}/chapters/{chapter_id}/commit",
    status_code=status.HTTP_201_CREATED,
)
def start_commit(
    project_id: str,
    chapter_id: str,
    body: StartWorkflowRequest,
    request: Request,
) -> dict[str, Any]:
    return _start_workflow(
        workflow_name="chapter-commit",
        request=request,
        project_id=project_id,
        chapter_id=chapter_id,
        body=body,
    )


@router.get("/runs/{run_id}")
def get_run_endpoint(run_id: str, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    run = get_run(settings.db_path, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return run


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: str, body: ResumeRequest, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    db_path = settings.db_path

    run = get_run(db_path, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    if run["status"] != "PAUSED":
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id!r} status={run['status']!r}，must be PAUSED to resume",
        )

    # V1.5 越层整改：通过 service 反查 workflow_name（替换原 JOIN SQL）
    workflow_name = get_workflow_name_for_run(db_path, run_id)
    if workflow_name is None:
        raise HTTPException(status_code=500, detail="workflow not found for run")
    workflow = get_workflow(workflow_name)
    if workflow is None:
        raise HTTPException(status_code=500, detail=f"workflow {workflow_name!r} not registered")

    engine = _engine(request)
    try:
        engine.resume(
            run_id,
            workflow["nodes"],
            human_input=body.human_input,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    final = get_run(db_path, run_id)
    if final is None:
        raise HTTPException(status_code=500, detail="run disappeared after resume")

    # P0 自动改稿回路：仅对 chapter-review 且以 rejected-for-revision 失败时触发
    auto_revise_max = _resolve_auto_revise_max(body.auto_revise_max)
    if (
        workflow_name == "chapter-review"
        and final["status"] == "FAILED"
        and "rejected-for-revision" in str(final.get("error") or "")
        and auto_revise_max > 0
    ):
        chapter_id = run["chapter_id"]
        if chapter_id:
            project_id = ChapterService(db_path).get_project_id(chapter_id)
            if project_id is not None:
                # mock_providers：优先用请求体传入；否则从原 run checkpoint 继承
                mock_providers = body.mock_providers
                if mock_providers is None:
                    checkpoint = run.get("checkpoint_json") or {}
                    mp = checkpoint.get("mock_providers")
                    if isinstance(mp, dict):
                        mock_providers = mp
                return _auto_revise_loop(
                    engine,
                    db_path,
                    project_id,
                    chapter_id,
                    mock_providers,
                    auto_revise_max,
                )

    out: dict[str, Any] = {
        "run_id": run_id,
        "status": final["status"],
        "current_node": final.get("current_node"),
    }
    if final["status"] == "PAUSED":
        out["pause_payload"] = _extract_pause_payload(final)
    elif workflow_name == "project-init" and final["status"] == "COMPLETED":
        out["project_id"] = (final.get("checkpoint_json") or {}).get("project_id")
    return out


@router.get("/projects/{project_id}/runs")
def list_runs_endpoint(project_id: str, request: Request) -> list[dict[str, Any]]:
    settings = request.app.state.settings
    runs = list_runs(settings.db_path, project_id)
    return runs


# ---------------------------------------------------------------------------
# Sprint 13 下半：context-preview（dry-run；只读、不调 LLM、不写库）。
# ---------------------------------------------------------------------------


@router.get("/chapters/{chapter_id}/context-preview")
def get_chapter_context_preview(chapter_id: str, request: Request) -> dict[str, Any]:
    """dry-run：返回 chapter 关联的 LLM context 装配预览。

    按 L0/L1/L2 分层，每层包含 ``token_estimate`` + ``items`` 条目清单 +
    ``total_tokens`` + ``token_budget``。**只读**，不调 LLM，不写 ai_call_logs。
    """
    from packages.core.context_engine import preview_context

    settings = request.app.state.settings
    db = settings.db_path

    # V1.5 越层整改：通过 ChapterService 取 chapter.project_id + 404 校验
    project_id = ChapterService(db).get_project_id(chapter_id)
    if project_id is None:
        raise HTTPException(
            status_code=404, detail=f"chapter {chapter_id!r} not found"
        )

    try:
        return preview_context(db, project_id, chapter_id)
    except ValueError as exc:
        # builder 抛的 project/chapter 不存在 → 404
        raise HTTPException(status_code=404, detail=str(exc)) from exc
