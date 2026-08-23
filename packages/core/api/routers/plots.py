"""Plot REST API 路由（Sprint 1）。

自动发现：本模块顶层定义名为 ``router`` 的 ``APIRouter``，
由 ``packages.core.api.routers.discover_routers`` 自动挂载到 FastAPI 应用。

端点清单（对齐任务书 §3）：
- POST /api/projects/{pid}/events        创建 plot_event（含时间线自动同步）
- GET  /api/projects/{pid}/events        列表，支持 ?type=&status= 过滤
- GET  /api/events/{id}                  获取单条
- PATCH /api/events/{id}                 部分更新
- DELETE /api/events/{id}                删除
- GET  /api/projects/{pid}/timeline      时间线（按 day_index 排序）
- POST /api/projects/{pid}/timeline      手动追加时间线条目
- DELETE /api/timeline/{id}              删除时间线条目
- GET  /api/projects/{pid}/relationships 关系列表（S1 只读）
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from packages.domain.plot import (
    NotFoundError,
    PlotService,
    ReferencedError,
    ValidationError,
)

router = APIRouter(tags=["plot"])


def _service(request: Request) -> PlotService:
    """每请求新建 ``PlotService``；连接在其内部按方法生命周期管理。"""
    settings = request.app.state.settings
    return PlotService(settings.db_path)


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ReferencedError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# plot_events
# ---------------------------------------------------------------------------


@router.post("/projects/{pid}/events", status_code=status.HTTP_201_CREATED)
def create_event(
    pid: str, payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    svc = _service(request)
    try:
        ev = svc.create_event(
            project_id=pid,
            type=payload.get("type", ""),
            cause=payload.get("cause"),
            effects=payload.get("effects"),
            participants=payload.get("participants"),
            location_id=payload.get("location_id"),
            time=payload.get("time"),
            status=payload.get("status"),
            introduced_chapter_id=payload.get("introduced_chapter_id"),
            visibility=payload.get("visibility"),
            who_knows=payload.get("who_knows"),
        )
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle(exc) from exc
    return ev.to_dict()


@router.get("/projects/{pid}/events")
def list_events(
    pid: str,
    request: Request,
    type: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    svc = _service(request)
    try:
        events = svc.list_events(pid, type=type, status=status)
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle(exc) from exc
    return [e.to_dict() for e in events]


@router.get("/events/{event_id}")
def get_event(event_id: str, request: Request) -> dict[str, Any]:
    svc = _service(request)
    ev = svc.get_event(event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail=f"event#{event_id} 不存在")
    return ev.to_dict()


@router.patch("/events/{event_id}")
def update_event(
    event_id: str, payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    svc = _service(request)
    try:
        ev = svc.update_event(
            event_id,
            type=payload.get("type"),
            cause=payload.get("cause"),
            effects=payload.get("effects"),
            participants=payload.get("participants"),
            location_id=payload.get("location_id"),
            time=payload.get("time"),
            status=payload.get("status"),
            introduced_chapter_id=payload.get("introduced_chapter_id"),
            visibility=payload.get("visibility"),
            who_knows=payload.get("who_knows"),
        )
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle(exc) from exc
    return ev.to_dict()


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(event_id: str, request: Request) -> Response:
    svc = _service(request)
    try:
        svc.delete_event(event_id)
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# timeline_events
# ---------------------------------------------------------------------------


@router.get("/projects/{pid}/timeline")
def list_timeline(pid: str, request: Request) -> list[dict[str, Any]]:
    svc = _service(request)
    return [t.to_dict() for t in svc.list_timeline_events(pid)]


@router.post("/projects/{pid}/timeline", status_code=status.HTTP_201_CREATED)
def create_timeline(
    pid: str, payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    svc = _service(request)
    try:
        te = svc.create_timeline_event(
            project_id=pid,
            event_id=payload.get("event_id", ""),
            day_index=payload.get("day_index", 0),
            time_ref=payload.get("time_ref"),
            description=payload.get("description"),
        )
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle(exc) from exc
    return te.to_dict()


@router.delete("/timeline/{timeline_event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_timeline(timeline_event_id: str, request: Request) -> Response:
    svc = _service(request)
    try:
        svc.delete_timeline_event(timeline_event_id)
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# relationships (S1 只读)
# ---------------------------------------------------------------------------


@router.get("/projects/{pid}/relationships")
def list_relationships(pid: str, request: Request) -> list[dict[str, Any]]:
    svc = _service(request)
    return [r.to_dict() for r in svc.list_relationships(pid)]
