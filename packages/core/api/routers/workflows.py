"""Workflow REST 路由（Sprint 4-A）。

端点（挂在 ``/api`` 前缀下）：
- ``POST /projects/{project_id}/chapters/{chapter_id}/plan``    — 启动 chapter-plan
- ``POST /projects/{project_id}/chapters/{chapter_id}/write``   — 启动 chapter-write
- ``POST /projects/{project_id}/chapters/{chapter_id}/review``  — 启动 chapter-review
- ``POST /projects/{project_id}/chapters/{chapter_id}/commit``  — 启动 chapter-commit
- ``GET  /runs/{run_id}``                                          — run + 节点明细
- ``POST /runs/{run_id}/resume``                                   — 恢复 PAUSED run
- ``GET  /projects/{project_id}/runs``                             — list runs

请求体：
- ``{author_intent?: str, mock_providers?: {agent_name: [str, ...]}}`` — start
- ``{human_input?: dict}`` — resume

返回：
- 201（start）→ ``{run_id, status, ...}``；若 PAUSED 则附加 ``pause_payload``
- 200（get/list/resume）→ run dict（含 nodes 数组）或 list
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from packages.core.db import get_connection
from packages.core.logging_config import get_logger
from packages.core.workflow_runtime.engine import WorkflowEngine
from packages.core.workflow_runtime.runs import get_run, list_runs
from packages.workflows import get_workflow

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


class ResumeRequest(BaseModel):
    human_input: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engine(request: Request) -> WorkflowEngine:
    settings = request.app.state.settings
    return WorkflowEngine(settings.db_path)


def _check_chapter(request: Request, project_id: str, chapter_id: str) -> tuple[str, str]:
    """校验 chapter 属于该 project；返回 (project_id, chapter_id)。"""
    settings = request.app.state.settings
    conn = get_connection(settings.db_path)
    try:
        row = conn.execute(
            "SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"chapter {chapter_id!r} not found")
    if row["project_id"] != project_id:
        raise HTTPException(
            status_code=400,
            detail=f"chapter {chapter_id!r} does not belong to project {project_id!r}",
        )
    return row["project_id"], chapter_id


def _init_genesis_if_needed(db_path: str, project_id: str, chapter_id: str) -> None:
    """如无快照则调 init_genesis 创建 v1 快照；供 chapter-write / commit 等依赖 state 的节点使用。"""
    from packages.core.story_state.service import StoryStateService

    svc = StoryStateService(db_path)
    state_version = int((svc.get_current_state(project_id) or {}).get("state_version") or 0)
    if state_version < 1:
        svc.init_genesis(project_id, chapter_id)


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

    engine = _engine(request)
    try:
        run_id = engine.start_with_nodes(
            workflow_name,
            workflow["nodes"],
            chapter_id=chapter_id,
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
    payload: dict[str, Any] = {
        "run_id": run_id,
        "status": run["status"],
        "current_node": run.get("current_node"),
    }
    if run["status"] == "PAUSED":
        # 找出 PENDING 节点的 __pause_payload__（ctx 中以 node_id 为 key）
        pause_payload = None
        for node_id, val in (run.get("checkpoint_json") or {}).items():
            if isinstance(val, dict) and "__pause_payload__" in val:
                pause_payload = val["__pause_payload__"]
                break
        payload["pause_payload"] = pause_payload
    return payload


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


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

    workflow_name = run.get("workflow_name")  # 见 get_run 输出（无 workflow_name，需查表）
    # 从 workflows 表反查 workflow 名（通过 run.workflow_id）
    conn = get_connection(db_path)
    try:
        wf_row = conn.execute(
            """
            SELECT w.name FROM workflow_runs wr
            JOIN workflows w ON w.workflow_id = wr.workflow_id
            WHERE wr.run_id = ?
            """,
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    if wf_row is None:
        raise HTTPException(status_code=500, detail="workflow not found for run")
    workflow_name = wf_row["name"]
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
    out: dict[str, Any] = {
        "run_id": run_id,
        "status": final["status"],
        "current_node": final.get("current_node"),
    }
    if final["status"] == "PAUSED":
        pause_payload = None
        for node_id, val in (final.get("checkpoint_json") or {}).items():
            if isinstance(val, dict) and "__pause_payload__" in val:
                pause_payload = val["__pause_payload__"]
                break
        out["pause_payload"] = pause_payload
    return out


@router.get("/projects/{project_id}/runs")
def list_runs_endpoint(project_id: str, request: Request) -> list[dict[str, Any]]:
    settings = request.app.state.settings
    runs = list_runs(settings.db_path, project_id)
    return runs
