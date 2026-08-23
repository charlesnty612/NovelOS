"""Chapters REST 路由（Sprint 1）。

挂在 ``/api`` 前缀下。对齐 ``chapters`` 表（``database/migrations/0001_init.sql`` line 240-254）。

状态机约束（任务书给死）：PLANNED→DRAFTED→REVIEWED→COMMITTED→RELEASED 顺序推进，
或回退到 PLANNED；非法跳变由 router 转 409。
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.logging_config import get_logger
from packages.domain.chapter.models import Chapter, ChapterCreate, ChapterUpdate
from packages.domain.chapter.service import (
    ChapterNumberConflict,
    ChapterService,
    ChapterTransitionError,
)

log = get_logger("novelos.routers.chapters")

router = APIRouter(tags=["chapters"])


def _service(request: Request) -> ChapterService:
    settings = request.app.state.settings
    return ChapterService(settings.db_path)


@router.post(
    "/projects/{project_id}/chapters",
    response_model=Chapter,
    status_code=status.HTTP_201_CREATED,
)
def create_chapter(project_id: str, payload: ChapterCreate, request: Request) -> dict:
    svc = _service(request)
    # project 不存在 → 404
    from packages.domain.project.service import ProjectService

    if ProjectService(request.app.state.settings.db_path).get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    try:
        return svc.create(project_id, payload)
    except ChapterNumberConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc


@router.get(
    "/projects/{project_id}/chapters",
    response_model=list[Chapter],
)
def list_chapters(project_id: str, request: Request) -> list[dict]:
    from packages.domain.project.service import ProjectService

    if ProjectService(request.app.state.settings.db_path).get(project_id) is None:
        raise HTTPException(status_code=404, detail=f"project {project_id!r} not found")
    return _service(request).list_by_project(project_id)


@router.get("/chapters/{chapter_id}", response_model=Chapter)
def get_chapter(chapter_id: str, request: Request) -> dict:
    row = _service(request).get(chapter_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"chapter {chapter_id!r} not found")
    return row


@router.patch("/chapters/{chapter_id}", response_model=Chapter)
def update_chapter(chapter_id: str, payload: ChapterUpdate, request: Request) -> dict:
    svc = _service(request)
    try:
        row = svc.update(chapter_id, payload)
    except ChapterTransitionError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"illegal chapter status transition: {exc.current!r} -> {exc.target!r}",
        ) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"chapter {chapter_id!r} not found")
    return row


@router.delete("/chapters/{chapter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chapter(chapter_id: str, request: Request) -> None:
    try:
        ok = _service(request).delete(chapter_id)
    except sqlite3.IntegrityError as exc:
        # scenes/drafts 等子记录 → 409
        raise HTTPException(
            status_code=409,
            detail=f"chapter {chapter_id!r} has dependent rows, cannot delete",
        ) from exc
    if not ok:
        raise HTTPException(status_code=404, detail=f"chapter {chapter_id!r} not found")
    return None
