"""Projects REST 路由（Sprint 1 + V3.7 字数带覆盖）。

挂在 ``/api`` 前缀下，由 ``packages/core/api/main.py`` 的 ``discover_routers()`` 自动发现。
对齐 DDL：``projects`` 表（``database/migrations/0001_init.sql`` line 34-44）。

V3.7：PATCH /projects/{project_id} 支持 ``word_band`` 字段——dict 落 ``word_band_json``
列、null 清除覆盖、缺省保留。校验在 router 层走
:func:`packages.core.quality.wordcount.resolve_band_config`，非法值 → 422。
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.logging_config import get_logger
from packages.core.quality.wordcount import resolve_band_config
from packages.domain.project.models import Project, ProjectCreate, ProjectUpdate
from packages.domain.project.service import ProjectService

log = get_logger("novelos.routers.projects")

router = APIRouter(tags=["projects"])


def _service(request: Request) -> ProjectService:
    """从 app.state 构造领域 Service（每请求一个实例，便于测试隔离）。"""
    settings = request.app.state.settings
    return ProjectService(settings.db_path)


@router.post("/projects", response_model=Project, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, request: Request) -> dict:
    # 与 PATCH 同口径：word_band 显式提供（非 None）时先过 resolve_band_config 校验。
    if payload.word_band is not None:
        try:
            resolve_band_config(payload.word_band)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"word_band 非法: {exc}",
            ) from exc
    try:
        return _service(request).create(payload)
    except sqlite3.IntegrityError as exc:
        # CHECK 违反或 FK 违反 → 422
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc


@router.get("/projects", response_model=list[Project])
def list_projects(
    request: Request,
    include_archived: bool = False,
) -> list[dict]:
    """默认排除归档项目；``?include_archived=true`` 时全量返回。"""
    return _service(request).list(include_archived=include_archived)


@router.get("/projects/{project_id}", response_model=Project)
def get_project(project_id: str, request: Request) -> dict:
    row = _service(request).get(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return row


@router.patch("/projects/{project_id}", response_model=Project)
def update_project(project_id: str, payload: ProjectUpdate, request: Request) -> dict:
    # V3.7：word_band 显式提供时校验；非法 → 422（与 Pydantic 校验错同等待遇）。
    # model_fields_set 区分「未提供」与「显式 null」——后者不校验（语义：清除覆盖）。
    if "word_band" in payload.model_fields_set and payload.word_band is not None:
        try:
            resolve_band_config(payload.word_band)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"word_band 非法: {exc}",
            ) from exc
    try:
        row = _service(request).update(project_id, payload)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return row


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, request: Request) -> None:
    svc = _service(request)
    try:
        ok = svc.delete(project_id)
    except sqlite3.IntegrityError as exc:
        # 存在子记录（characters / chapters 等）→ 409
        # 提供 has_children 检出的可读 detail，便于前端展示
        detail = (
            f"project {project_id!r} has dependent rows "
            f"(characters/chapters), cannot delete"
        )
        log.info("delete project conflict: %s", detail)
        raise HTTPException(status_code=409, detail=detail) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return None
