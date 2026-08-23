"""Story State REST 路由（Sprint 2 + Sprint 7）。

端点：
- POST /projects/{pid}/state/init               —— 创建 genesis（v1 快照）
- GET  /projects/{pid}/state                    —— 当前 Canonical State（main）；可选 ?branch_id=
- GET  /projects/{pid}/state/versions/{n}       —— 指定快照
- POST /projects/{pid}/deltas                   —— submit（201 通过；422 校验失败）
                                                   body 可选 branch_id
- POST /projects/{pid}/commits                  —— commit（201 通过；409 乐观锁/审批；404）
                                                   body 可选 branch_id
- POST /commits/{cid}/rollback                  —— 回滚（201）
- GET  /projects/{pid}/commits                  —— 列 commits
- GET  /projects/{pid}/chapters/{cid}/deltas    —— 列 deltas
- POST /projects/{pid}/branches                 —— 创建分支（Sprint 7）
- GET  /projects/{pid}/branches                 —— 列分支
- POST /projects/{pid}/branches/{bid}/promote   —— 分支 promote 到 main
- GET  /projects/{pid}/state/diff?a=&b=&branch_id=  —— 两版本 diff

错误码映射：
- 422：submit 校验失败（detail 含 errors 列表）。
- 404：delta / commit / chapter / project / branch 不存在。
- 409：StateConflictError（状态机非法）、OptimisticLockError（乐观锁）、
       ApprovalRequiredError（HIGH 风险无审批）、BranchClosed（分支 MERGED/DISCARDED）、
       branch_name_conflict（重名）、FK 冲突（commit 时 chapter/project 不存在）。
- 500：未捕获异常（detail 仅含类名 + 简短 message）。

设计要点：
- 复用 ``discover_routers`` 自动发现机制：模块顶层定义 ``router`` 即可被 main.py 挂载到 ``/api`` 前缀。
- 路由内部不开连接：所有 DB 操作委派 ``StoryStateService``（与 S1 services 一致）。
- 路径参数 ``pid`` / ``cid`` 仅作路由层校验：项目不存在时由 service 阶段抛 StateNotFoundError
  转 404；端点 /projects/{pid}/state/init 与 /projects/{pid}/state 在 project 缺失时返回 404，
  便于前端快速定位。
- Sprint 7 新增分支能力：body 中 ``branch_id`` 是路由层透传字段（不属于 delta schema），router
  取出后再传给 service；service 校验分支归属与 ACTIVE 状态；state_deltas 表本身无 branch_id 列
  （沿用 state-delta-v0.md §2.2），仅 commits.branch_id 区分 commit 归属。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.logging_config import get_logger
from packages.core.story_state import StoryStateService
from packages.core.story_state.exceptions import (
    ApprovalRequiredError,
    BranchClosed,
    BranchNotFound,
    OptimisticLockError,
    StateConflictError,
    StateNotFoundError,
)

log = get_logger("novelos.routers.story_state")

router = APIRouter(tags=["story_state"])


def _service(request: Request) -> StoryStateService:
    settings = request.app.state.settings
    return StoryStateService(settings.db_path)


def _ensure_project(request: Request, project_id: str) -> None:
    from packages.domain.project.service import ProjectService

    if ProjectService(request.app.state.settings.db_path).get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")


# ----------------------------------------------------------------- genesis & state


@router.post(
    "/projects/{project_id}/state/init",
    status_code=status.HTTP_201_CREATED,
)
def init_state(project_id: str, payload: dict, request: Request) -> dict:
    chapter_id = payload.get("chapter_id")
    if not isinstance(chapter_id, str) or not chapter_id:
        raise HTTPException(status_code=422, detail="chapter_id required (non-empty string)")
    _ensure_project(request, project_id)
    return _service(request).init_genesis(project_id, chapter_id)


@router.get("/projects/{project_id}/state")
def get_state(project_id: str, request: Request, branch_id: str | None = None) -> dict:
    _ensure_project(request, project_id)
    try:
        return _service(request).get_current_state(project_id, branch_id=branch_id)
    except BranchNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/projects/{project_id}/state/versions/{version}")
def get_state_version(project_id: str, version: int, request: Request) -> dict:
    _ensure_project(request, project_id)
    snap = _service(request).get_snapshot(project_id, version)
    if snap is None:
        raise HTTPException(
            status_code=404,
            detail=f"snapshot version {version} not found for project {project_id!r}",
        )
    return snap


# ----------------------------------------------------------------- deltas


@router.post(
    "/projects/{project_id}/deltas",
    status_code=status.HTTP_201_CREATED,
)
def submit_delta(project_id: str, payload: dict, request: Request) -> dict:
    _ensure_project(request, project_id)
    branch_id = (payload or {}).get("branch_id") if isinstance(payload, dict) else None
    service_payload = dict(payload or {})
    if branch_id is not None:
        service_payload.pop("branch_id", None)
    try:
        result = _service(request).submit_delta(service_payload, branch_id=branch_id)
    except BranchNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BranchClosed as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "branch_closed",
                "message": str(exc),
                "branch_id": exc.branch_id,
                "status": exc.status,
            },
        ) from exc
    if result["status"] == "rejected":
        raise HTTPException(status_code=422, detail={"errors": result["errors"], "delta_id": result["delta_id"]})
    return result


# ----------------------------------------------------------------- commits


@router.post(
    "/projects/{project_id}/commits",
    status_code=status.HTTP_201_CREATED,
)
def commit_delta(project_id: str, payload: dict, request: Request) -> dict:
    _ensure_project(request, project_id)
    delta_id = payload.get("delta_id")
    author_approval = payload.get("author_approval") or {}
    workflow_run_id = payload.get("workflow_run_id") or ""
    branch_id = payload.get("branch_id")
    if not isinstance(delta_id, str) or not delta_id:
        raise HTTPException(status_code=422, detail="delta_id required")
    if not isinstance(workflow_run_id, str) or not workflow_run_id:
        raise HTTPException(status_code=422, detail="workflow_run_id required")
    try:
        return _service(request).commit_delta(
            delta_id, author_approval, workflow_run_id, branch_id=branch_id,
        )
    except BranchNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BranchClosed as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "branch_closed",
                "message": str(exc),
                "branch_id": exc.branch_id,
                "status": exc.status,
            },
        ) from exc
    except StateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OptimisticLockError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "optimistic_lock",
                "message": str(exc),
                "expected_version": exc.expected_version,
                "actual_version": exc.actual_version,
            },
        ) from exc
    except ApprovalRequiredError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "approval_required",
                "message": str(exc),
                "high_risk_change_ids": exc.high_risk_change_ids,
            },
        ) from exc
    except StateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/commits/{commit_id}/rollback",
    status_code=status.HTTP_201_CREATED,
)
def rollback_commit(commit_id: str, payload: dict, request: Request) -> dict:
    author_approval = payload.get("author_approval") if isinstance(payload, dict) else {}
    try:
        return _service(request).rollback_commit(commit_id, author_approval or {})
    except StateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OptimisticLockError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "optimistic_lock",
                "message": str(exc),
                "expected_version": exc.expected_version,
                "actual_version": exc.actual_version,
            },
        ) from exc
    except ApprovalRequiredError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "approval_required",
                "message": str(exc),
                "high_risk_change_ids": exc.high_risk_change_ids,
            },
        ) from exc
    except StateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ----------------------------------------------------------------- lists


@router.get("/projects/{project_id}/commits")
def list_commits(project_id: str, request: Request) -> list[dict]:
    _ensure_project(request, project_id)
    return _service(request).list_commits(project_id)


@router.get("/projects/{project_id}/chapters/{chapter_id}/deltas")
def list_deltas(project_id: str, chapter_id: str, request: Request) -> list[dict]:
    _ensure_project(request, project_id)
    return _service(request).list_deltas(chapter_id)


# ----------------------------------------------------------------- branches (Sprint 7)


@router.post(
    "/projects/{project_id}/branches",
    status_code=status.HTTP_201_CREATED,
)
def create_branch(project_id: str, payload: dict, request: Request) -> dict:
    """创建分支（state-delta-v0.md §6.3）。

    body:
      - ``name``: str, 必填，同 project 唯一；禁止 'main'。
      - ``base_state_version``: int, 可选，缺省 = main 最新 version。
    """
    _ensure_project(request, project_id)
    name = (payload or {}).get("name") if isinstance(payload, dict) else None
    base = (payload or {}).get("base_state_version") if isinstance(payload, dict) else None
    if not isinstance(name, str) or not name:
        raise HTTPException(status_code=422, detail="name required (non-empty string)")
    try:
        return _service(request).create_branch(project_id, name, base_state_version=base)
    except StateConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "branch_name_conflict", "message": str(exc)},
        ) from exc


@router.get("/projects/{project_id}/branches")
def list_branches(project_id: str, request: Request) -> list[dict]:
    _ensure_project(request, project_id)
    return _service(request).list_branches(project_id)


@router.post(
    "/projects/{project_id}/branches/{branch_id}/promote",
    status_code=status.HTTP_201_CREATED,
)
def promote_branch(project_id: str, branch_id: str, payload: dict, request: Request) -> dict:
    """把分支全部 commits 合并为单个 delta，在 main 上提交（state-delta-v0.md §6.3）。"""
    _ensure_project(request, project_id)
    chapter_id = (payload or {}).get("chapter_id") if isinstance(payload, dict) else None
    try:
        return _service(request).promote_branch(project_id, branch_id, chapter_id=chapter_id)
    except BranchNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BranchClosed as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "branch_closed",
                "message": str(exc),
                "branch_id": exc.branch_id,
                "status": exc.status,
            },
        ) from exc
    except StateConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "promote_conflict", "message": str(exc)},
        ) from exc
    except ApprovalRequiredError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "approval_required",
                "message": str(exc),
                "high_risk_change_ids": exc.high_risk_change_ids,
            },
        ) from exc
    except OptimisticLockError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "optimistic_lock",
                "message": str(exc),
                "expected_version": exc.expected_version,
                "actual_version": exc.actual_version,
            },
        ) from exc


@router.get("/projects/{project_id}/state/diff")
def diff_versions(
    project_id: str,
    request: Request,
    a: int,
    b: int,
    branch_id: str | None = None,
) -> dict:
    """两个 state_version 之间的结构化 diff。"""
    _ensure_project(request, project_id)
    try:
        return _service(request).diff_versions(project_id, a, b, branch_id=branch_id)
    except StateNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StateConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "diff_conflict", "message": str(exc)},
        ) from exc
