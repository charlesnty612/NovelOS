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

出参契约（G2，2026-10-15 批次（六））
------------------------------------
7 个**读**端点已挂 ``response_model``：

===========  =======================================  ====================
端点          模型                                    形状来源
===========  =======================================  ====================
单体 3+1     ``PlotEventOut`` / ``TimelineEventOut``  ``to_dict()`` 逐字镜像
列表 3       ``list[PlotEventOut]`` /                同上（**实测**，不是
             ``list[TimelineEventOut]`` /            「应该和单体一样」）
             ``list[RelationshipOut]``
===========  =======================================  ====================

⚠️ 三个 **DELETE** 端点仍返回 204（无 body，本就无契约可挂）。

⚠️ 端点上**一律不加** ``response_model_exclude_none``：这些实体有 5~6 个可空列，
实测它们是「键恒在、值为 null」；加了那个开关会把键整个抹掉，把契约改成另一种形状
（与 ``simulation`` 的 ``diff`` 恰好相反——那边要的是「空则省略」，这里要的是「空也在」）。

⚠️ 列表行的形状**单独量过**（2026-10-15 实测、2026-10-16 复测，临时库 + 合成夹具，
见 ``tests/api/test_ledger_plot_list_response_contract.py``）：
``events`` 13 键 / ``timeline`` 8 键，与单体端点逐字相同；``relationships`` **9 键**
≡ 它的表列集 9 列（⚠️ 键名两处改名/解码：``relationship_id`` → ``id``、
``state_json`` → ``state``，故「9≡9」是**键数相等**不是列名逐字相等）。

⚠️ ``relationships`` 原先只有 **7 键**：``visibility`` / ``who_knows``（迁移 0014 加、
0022 重建表时逐字保留）被 ``PlotService._row_to_relationship`` 丢弃。2026-10-16 已修，
详见 ``packages/domain/plot/models.py::RelationshipOut``。
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
from packages.domain.plot.models import PlotEventOut, RelationshipOut, TimelineEventOut

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


@router.post(
    "/projects/{pid}/events",
    status_code=status.HTTP_201_CREATED,
    response_model=PlotEventOut,
)
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
            description=payload.get("description"),
        )
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle(exc) from exc
    return ev.to_dict()


@router.get("/projects/{pid}/events", response_model=list[PlotEventOut])
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


@router.get("/events/{event_id}", response_model=PlotEventOut)
def get_event(event_id: str, request: Request) -> dict[str, Any]:
    svc = _service(request)
    ev = svc.get_event(event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail=f"event#{event_id} 不存在")
    return ev.to_dict()


@router.patch("/events/{event_id}", response_model=PlotEventOut)
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
            description=payload.get("description"),
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


@router.get("/projects/{pid}/timeline", response_model=list[TimelineEventOut])
def list_timeline(pid: str, request: Request) -> list[dict[str, Any]]:
    svc = _service(request)
    return [t.to_dict() for t in svc.list_timeline_events(pid)]


@router.post(
    "/projects/{pid}/timeline",
    status_code=status.HTTP_201_CREATED,
    response_model=TimelineEventOut,
)
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
            # V3.1 P1-1.2：透传 visibility/who_knows（缺省 → 'PUBLIC' / NULL，
            # 向后兼容旧调用方）。
            visibility=payload.get("visibility"),
            who_knows=payload.get("who_knows"),
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


@router.get("/projects/{pid}/relationships", response_model=list[RelationshipOut])
def list_relationships(pid: str, request: Request) -> list[dict[str, Any]]:
    svc = _service(request)
    return [r.to_dict() for r in svc.list_relationships(pid)]
