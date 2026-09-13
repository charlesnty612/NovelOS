"""Genre Pack REST 路由（题材库 P1a）。

挂在 ``/api`` 前缀下（``discover_routers`` 自动发现，参照 ``routers/reference.py``）。

端点：
- ``POST   /projects/{project_id}/genre-packs``            —— 创建题材包；201
- ``GET    /genre-packs``                                  —— 列出题材包摘要；``?genre_tag=`` 过滤
- ``GET    /genre-packs/{pack_id}``                        —— 题材包全文（payload 已解析）
- ``PUT    /genre-packs/{pack_id}``                        —— 更新；提供 payload 时 version 自增
- ``DELETE /genre-packs/{pack_id}``                        —— 删除；被项目绑定 → 409
- ``GET    /projects/{project_id}/genre-pack``             —— 项目当前绑定（含题材包全文）
- ``POST   /projects/{project_id}/genre-pack/bind``        —— 绑定（单 slot，覆盖式）
- ``POST   /projects/{project_id}/genre-pack/unbind``      —— 解绑（幂等）

错误码映射：
- 404 —— project / pack 不存在；
- 409 —— 删除被项目绑定的题材包（先解绑再删）；
- 422 —— payload 不合 ``docs/state-model/schemas/genre-pack.schema.json``
  （``detail={"errors": [...]}``，错误串格式与 canon / state-delta 校验器同款），
  或 pack_id 冲突 / 请求体字段非法；
- 500 —— 未捕获异常。

设计要点：
- 路由层不写 SQL：全部数据访问走
  :class:`packages.core.genre.service.GenrePackService`；路由只做参数校验 + 调用 +
  错误映射。
- 题材包是**跨作品资源**（不像 canon 归属单个项目）：创建端点挂在项目路径下只为
  资源树一致性（要求项目存在），绑定点才是项目语义（``projects.genre_pack_id`` 单 slot）。
- 双 slot：题材包（项目常驻）与 ``reference_canons``（叠加参照）互不覆盖；本路由不
  触碰 canon。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from packages.core.genre import (
    BindStatus,
    GenrePack,
    GenrePackCreate,
    GenrePackService,
    GenrePackSummary,
    GenrePackUpdate,
)
from packages.core.logging_config import get_logger
from packages.domain.project.service import ProjectService

log = get_logger("novelos.routers.genre")

router = APIRouter(tags=["genre"])


class GenreBindRequest(BaseModel):
    """绑定请求体：``{"pack_id": "..."}``。"""

    pack_id: str = Field(..., min_length=1, max_length=120)


def _service(request: Request) -> GenrePackService:
    """从 app.state 构造 service（每请求一个实例，便于测试隔离）。"""
    return GenrePackService(request.app.state.settings.db_path)


def _ensure_project(request: Request, project_id: str) -> None:
    """创建端点用：题材包是跨作品资源，挂在项目路径下只为资源树一致性 → 要求项目存在。"""
    svc = ProjectService(request.app.state.settings.db_path)
    if svc.get(project_id) is None:
        raise HTTPException(
            status_code=404, detail=f"project {project_id!r} not found"
        )


def _validate_or_422(svc: GenrePackService, payload: Any) -> None:
    """payload 过 jsonschema；不合规 → 422（``detail={"errors": [...]}``）。"""
    errors = svc.validate_payload(payload)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/genre-packs
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{project_id}/genre-packs",
    response_model=GenrePack,
    status_code=status.HTTP_201_CREATED,
)
def create_genre_pack(
    project_id: str, payload: GenrePackCreate, request: Request,
) -> dict[str, Any]:
    """创建题材包（201）；``payload`` 不合 schema → 422；``pack_id`` 冲突 → 422。"""
    _ensure_project(request, project_id)
    svc = _service(request)
    _validate_or_422(svc, payload.payload)
    try:
        return svc.create(payload)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc


# ---------------------------------------------------------------------------
# GET /genre-packs
# ---------------------------------------------------------------------------


@router.get("/genre-packs", response_model=list[GenrePackSummary])
def list_genre_packs(
    request: Request, genre_tag: str | None = Query(default=None, max_length=80),
) -> list[dict[str, Any]]:
    """列出题材包摘要（``created_at DESC``；``?genre_tag=`` 精确过滤）。"""
    return _service(request).list_packs(genre_tag=genre_tag)


# ---------------------------------------------------------------------------
# GET /genre-packs/{pack_id}
# ---------------------------------------------------------------------------


@router.get("/genre-packs/{pack_id}", response_model=GenrePack)
def get_genre_pack(pack_id: str, request: Request) -> dict[str, Any]:
    """题材包全文（payload 已解析为 dict）。"""
    pack = _service(request).get(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail=f"genre pack {pack_id!r} not found")
    return pack


# ---------------------------------------------------------------------------
# PUT /genre-packs/{pack_id}
# ---------------------------------------------------------------------------


@router.put("/genre-packs/{pack_id}", response_model=GenrePack)
def update_genre_pack(
    pack_id: str, payload: GenrePackUpdate, request: Request,
) -> dict[str, Any]:
    """更新题材包；提供 ``payload`` 时 version 自增（装配缓存键指纹跟随）。"""
    svc = _service(request)
    if payload.payload is not None:
        _validate_or_422(svc, payload.payload)
    updated = svc.update(pack_id, payload)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"genre pack {pack_id!r} not found")
    return updated


# ---------------------------------------------------------------------------
# DELETE /genre-packs/{pack_id}
# ---------------------------------------------------------------------------


@router.delete("/genre-packs/{pack_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_genre_pack(pack_id: str, request: Request) -> None:
    """删除题材包；仍被项目绑定 → 409（先 unbind 再删）。"""
    svc = _service(request)
    bound = svc.count_bindings(pack_id)
    if bound > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"genre pack {pack_id!r} is bound to {bound} project(s); "
                f"unbind before deleting"
            ),
        )
    if not svc.delete(pack_id):
        raise HTTPException(status_code=404, detail=f"genre pack {pack_id!r} not found")
    return None


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/genre-pack
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/genre-pack")
def get_project_genre_pack(project_id: str, request: Request) -> dict[str, Any]:
    """项目当前题材包绑定：``{project_id, pack_id, bound, pack}``（未绑 → pack=None）。"""
    binding = _service(request).get_project_binding(project_id)
    if binding is None:  # 项目不存在
        raise HTTPException(
            status_code=404, detail=f"project {project_id!r} not found"
        )
    return binding


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/genre-pack/bind
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/genre-pack/bind")
def bind_project_genre_pack(
    project_id: str, payload: GenreBindRequest, request: Request,
) -> dict[str, Any]:
    """绑定题材包到项目（单 slot；已有绑定 → 覆盖）。

    404：项目或题材包不存在。响应含覆盖后的绑定详情（``{project_id, pack_id,
    bound, pack}``）。
    """
    code, binding = _service(request).bind(project_id, payload.pack_id)
    if code == BindStatus.PROJECT_NOT_FOUND:
        raise HTTPException(
            status_code=404, detail=f"project {project_id!r} not found"
        )
    if code == BindStatus.PACK_NOT_FOUND:
        raise HTTPException(
            status_code=404, detail=f"genre pack {payload.pack_id!r} not found"
        )
    return binding or {}


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/genre-pack/unbind
# ---------------------------------------------------------------------------


@router.post("/projects/{project_id}/genre-pack/unbind")
def unbind_project_genre_pack(project_id: str, request: Request) -> dict[str, Any]:
    """解绑项目题材包（幂等：未绑定也返回 200，``bound=false``）。

    404：项目不存在。
    """
    code, _ = _service(request).unbind(project_id)
    if code == BindStatus.PROJECT_NOT_FOUND:
        raise HTTPException(
            status_code=404, detail=f"project {project_id!r} not found"
        )
    log.info("genre pack unbound: project=%s", project_id)
    return {"project_id": project_id, "pack_id": None, "bound": False, "pack": None}


__all__ = ["router"]
