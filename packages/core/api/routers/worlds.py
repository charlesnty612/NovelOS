"""World REST API 路由（Sprint 1）。

自动发现：本模块顶层定义名为 ``router`` 的 ``APIRouter``，
由 ``packages.core.api.routers.discover_routers`` 自动挂载到 FastAPI 应用。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from packages.domain.world import (
    NotFoundError,
    ReferencedError,
    ValidationError,
    WorldService,
)

router = APIRouter(tags=["world"])


# ---------------------------------------------------------------------------
# 依赖：每请求传 db_path，Service 内部按方法开/关连接
# ---------------------------------------------------------------------------


def _service(request: Request) -> WorldService:
    """每请求新建 ``WorldService``；连接在其内部按方法生命周期管理。"""
    settings = request.app.state.settings
    return WorldService(settings.db_path)


# ---------------------------------------------------------------------------
# 通用异常处理
# ---------------------------------------------------------------------------


def _handle_world_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ReferencedError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# locations
# ---------------------------------------------------------------------------


@router.post("/projects/{pid}/locations", status_code=status.HTTP_201_CREATED)
def create_location(pid: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    svc = _service(request)
    try:
        ent = svc.create_location(
            project_id=pid,
            name=payload.get("name", ""),
            statement=payload.get("statement", ""),
            data=payload.get("data"),
            visibility=payload.get("visibility"),
            who_knows=payload.get("who_knows"),
        )
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle_world_error(exc) from exc
    return ent.to_dict()


@router.get("/projects/{pid}/locations")
def list_locations(pid: str, request: Request) -> list[dict[str, Any]]:
    svc = _service(request)
    return [e.to_dict() for e in svc.list_locations(pid)]


@router.get("/locations/{location_id}")
def get_location(location_id: str, request: Request) -> dict[str, Any]:
    svc = _service(request)
    ent = svc.get_location(location_id)
    if ent is None:
        raise HTTPException(status_code=404, detail=f"location#{location_id} 不存在")
    return ent.to_dict()


@router.patch("/locations/{location_id}")
def update_location(
    location_id: str, payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    svc = _service(request)
    try:
        ent = svc.update_location(
            location_id,
            name=payload.get("name"),
            statement=payload.get("statement"),
            data=payload.get("data"),
            visibility=payload.get("visibility"),
            who_knows=payload.get("who_knows"),
        )
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle_world_error(exc) from exc
    return ent.to_dict()


@router.delete("/locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_location(location_id: str, request: Request) -> Response:
    svc = _service(request)
    try:
        svc.delete_location(location_id)
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle_world_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# factions
# ---------------------------------------------------------------------------


@router.post("/projects/{pid}/factions", status_code=status.HTTP_201_CREATED)
def create_faction(pid: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    svc = _service(request)
    try:
        ent = svc.create_faction(
            project_id=pid,
            name=payload.get("name", ""),
            statement=payload.get("statement", ""),
            data=payload.get("data"),
            visibility=payload.get("visibility"),
            who_knows=payload.get("who_knows"),
        )
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle_world_error(exc) from exc
    return ent.to_dict()


@router.get("/projects/{pid}/factions")
def list_factions(pid: str, request: Request) -> list[dict[str, Any]]:
    svc = _service(request)
    return [e.to_dict() for e in svc.list_factions(pid)]


@router.get("/factions/{faction_id}")
def get_faction(faction_id: str, request: Request) -> dict[str, Any]:
    svc = _service(request)
    ent = svc.get_faction(faction_id)
    if ent is None:
        raise HTTPException(status_code=404, detail=f"faction#{faction_id} 不存在")
    return ent.to_dict()


@router.patch("/factions/{faction_id}")
def update_faction(
    faction_id: str, payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    svc = _service(request)
    try:
        ent = svc.update_faction(
            faction_id,
            name=payload.get("name"),
            statement=payload.get("statement"),
            data=payload.get("data"),
            visibility=payload.get("visibility"),
            who_knows=payload.get("who_knows"),
        )
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle_world_error(exc) from exc
    return ent.to_dict()


@router.delete("/factions/{faction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_faction(faction_id: str, request: Request) -> Response:
    svc = _service(request)
    try:
        svc.delete_faction(faction_id)
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle_world_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# world_rules
# ---------------------------------------------------------------------------


@router.post("/projects/{pid}/world-rules", status_code=status.HTTP_201_CREATED)
def create_world_rule(pid: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    svc = _service(request)
    try:
        ent = svc.create_world_rule(
            project_id=pid,
            name=payload.get("name", ""),
            statement=payload.get("statement", ""),
            data=payload.get("data"),
            visibility=payload.get("visibility"),
            who_knows=payload.get("who_knows"),
        )
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle_world_error(exc) from exc
    return ent.to_dict()


@router.get("/projects/{pid}/world-rules")
def list_world_rules(pid: str, request: Request) -> list[dict[str, Any]]:
    svc = _service(request)
    return [e.to_dict() for e in svc.list_world_rules(pid)]


@router.get("/world-rules/{world_rule_id}")
def get_world_rule(world_rule_id: str, request: Request) -> dict[str, Any]:
    svc = _service(request)
    ent = svc.get_world_rule(world_rule_id)
    if ent is None:
        raise HTTPException(status_code=404, detail=f"world_rule#{world_rule_id} 不存在")
    return ent.to_dict()


@router.patch("/world-rules/{world_rule_id}")
def update_world_rule(
    world_rule_id: str, payload: dict[str, Any], request: Request
) -> dict[str, Any]:
    svc = _service(request)
    try:
        ent = svc.update_world_rule(
            world_rule_id,
            name=payload.get("name"),
            statement=payload.get("statement"),
            data=payload.get("data"),
            visibility=payload.get("visibility"),
            who_knows=payload.get("who_knows"),
        )
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle_world_error(exc) from exc
    return ent.to_dict()


@router.delete("/world-rules/{world_rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_world_rule(world_rule_id: str, request: Request) -> Response:
    svc = _service(request)
    try:
        svc.delete_world_rule(world_rule_id)
    except (NotFoundError, ReferencedError, ValidationError) as exc:
        raise _handle_world_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
