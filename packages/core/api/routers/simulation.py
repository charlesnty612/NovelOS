"""Simulation REST 路由（Sprint 10）。

端点：
- POST /projects/{pid}/simulate                —— What-if 推演；body: {deltas, name?}
- GET  /projects/{pid}/simulations            —— 列历史推演（name LIKE 'sim-%'）
- GET  /projects/{pid}/simulations/{sid}      —— 重放某次推演

错误码映射：
- 404：project 不存在；simulation_id 找不到。
- 409：branch 名冲突（SimulationError reason='branch_create_failed'）。
- 422：body 缺 deltas / deltas 为空 / 单条 delta 校验失败（detail 含 issues 列表）。
- 500：未捕获异常。

设计要点：
- 复用 ``discover_routers`` 自动发现机制：模块顶层定义 ``router`` 即被 main.py 挂载到 ``/api``。
- 路由不开 DB 连接：所有 DB 操作委派 :class:`packages.core.simulation.SimulationService`。
- ``deltas`` 入参形态与 ``/deltas`` 一致；每项 delta 必须含 ``chapter_id``（与 production 一致）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.logging_config import get_logger
from packages.core.simulation import SimulationError, SimulationService

log = get_logger("novelos.routers.simulation")

router = APIRouter(tags=["simulation"])


def _service(request: Request) -> SimulationService:
    settings = request.app.state.settings
    return SimulationService(settings.db_path)


def _ensure_project(request: Request, project_id: str) -> None:
    from packages.domain.project.service import ProjectService

    if ProjectService(request.app.state.settings.db_path).get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")


# ----------------------------------------------------------------- simulate


@router.post(
    "/projects/{project_id}/simulate",
    status_code=status.HTTP_201_CREATED,
)
def simulate(project_id: str, payload: dict, request: Request) -> dict:
    """What-if 推演：在临时分支上应用假设 delta，返回与 main 的结构化 diff，归档分支。

    body:
      - ``deltas``: list[dict], 必填；每项为完整 delta dict（含 10 元信息字段 + 7 数组）。
      - ``name``: str?, 可选；缺省 ``sim-<ts>``。

    返回：``SimulationResult`` dict 形态（含 simulation_id / branch_id / diff / applied / issues 等）。
    """
    _ensure_project(request, project_id)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    deltas = payload.get("deltas")
    name = payload.get("name") if isinstance(payload, dict) else None
    if not isinstance(deltas, list) or not deltas:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "empty_deltas",
                "message": "deltas must be a non-empty list",
            },
        )
    if name is not None and not isinstance(name, str):
        raise HTTPException(status_code=422, detail="name must be a string if provided")
    try:
        result = _service(request).simulate(project_id, deltas, name=name)
    except SimulationError as exc:
        if exc.reason == "validation_failed":
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "validation_failed",
                    "message": str(exc),
                    "issues": exc.issues,
                    "applied": exc.applied,
                    "branch_id": exc.branch_id,
                    "simulation_id": exc.simulation_id,
                },
            ) from exc
        if exc.reason == "branch_create_failed":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "branch_name_conflict",
                    "message": str(exc),
                    "branch_id": exc.branch_id,
                },
            ) from exc
        if exc.reason == "commit_failed":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "simulation_commit_failed",
                    "message": str(exc),
                    "applied": exc.applied,
                    "branch_id": exc.branch_id,
                    "simulation_id": exc.simulation_id,
                },
            ) from exc
        if exc.reason == "approval_leak":
            # 防御性：_skip_approval=True 不应到这里。返 500 让审计可见。
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "approval_leak",
                    "message": str(exc),
                    "branch_id": exc.branch_id,
                },
            ) from exc
        # empty_deltas / project_not_found / 其他：500
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return result.to_dict()


# ----------------------------------------------------------------- list / get


@router.get("/projects/{project_id}/simulations")
def list_simulations(project_id: str, request: Request) -> list[dict]:
    """列历史推演（按 created_at DESC）。"""
    _ensure_project(request, project_id)
    return _service(request).list_simulations(project_id)


@router.get("/projects/{project_id}/simulations/{simulation_id}")
def get_simulation(project_id: str, simulation_id: str, request: Request) -> dict:
    """重放某次推演。找不到 → 404。"""
    _ensure_project(request, project_id)
    result = _service(request).get_simulation(project_id, simulation_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"simulation {simulation_id!r} not found for project {project_id!r}",
        )
    return result.to_dict()
