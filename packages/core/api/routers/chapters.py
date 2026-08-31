"""Chapters REST 路由（Sprint 1 + Sprint 5 drafts 扩展）。

挂在 ``/api`` 前缀下。对齐 ``chapters`` 表（``database/migrations/0001_init.sql`` line 240-254）
与 ``drafts`` 表（line 270-280）。

状态机约束（任务书给死）：PLANNED→DRAFTED→REVIEWED→COMMITTED→RELEASED 顺序推进，
或回退到 PLANNED；非法跳变由 router 转 409。
Sprint 5 drafts：仅 chapter.status ∈ {DRAFTED, REVIEWED} 允许新增 draft，其余状态 409。
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from packages.core.logging_config import get_logger
from packages.domain.chapter.models import (
    Chapter,
    ChapterCreate,
    ChapterUpdate,
    Draft,
    DraftCreate,
)
from packages.domain.chapter.service import (
    ChapterNumberConflict,
    ChapterService,
    ChapterTransitionError,
    DraftStatusNotAllowed,
    DraftVersionConflict,
    RevisionNoteStatusNotAllowed,
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


# =============================================================================
# Sprint 5：drafts（人工改稿能力）。
# 契约见 packages/domain/chapter/service.py 中的 list_drafts / create_draft。
# =============================================================================


@router.get(
    "/chapters/{chapter_id}/drafts",
    response_model=list[Draft],
)
def list_chapter_drafts(chapter_id: str, request: Request) -> list[dict]:
    rows = _service(request).list_drafts(chapter_id)
    if rows is None:
        raise HTTPException(status_code=404, detail=f"chapter {chapter_id!r} not found")
    return rows


@router.post(
    "/chapters/{chapter_id}/drafts",
    response_model=Draft,
    status_code=status.HTTP_201_CREATED,
)
def create_chapter_draft(
    chapter_id: str,
    payload: DraftCreate,
    request: Request,
) -> dict:
    try:
        row = _service(request).create_draft(chapter_id, payload.content)
    except DraftStatusNotAllowed as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                f"chapter {chapter_id!r} status is {exc.current!r}; "
                f"draft creation only allowed when status is 'DRAFTED' or 'REVIEWED'"
            ),
        ) from exc
    except DraftVersionConflict as exc:
        # Sprint 5 review F2：idx_drafts_chapter_version 唯一约束触发。
        raise HTTPException(
            status_code=409,
            detail=f"draft version {exc.version} already exists for chapter {chapter_id!r}",
        ) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"chapter {chapter_id!r} not found")
    return row


# =============================================================================
# Sprint 6：revision_note（改稿意见专用端点，task R1）。
# 契约见 packages/domain/chapter/service.py 中的 update_revision_note。
# 仅 chapter.status ∈ {PLANNED, DRAFTED, REVIEWED} 允许写入/清除；
# COMMITTED/RELEASED 视为正文已锁定 → 409。
# =============================================================================


class RevisionNoteUpdate(BaseModel):
    """写入或清除改稿意见的请求体。

    - ``note`` 必填字符串；非空白字符串写入 ``plan_json.revision_note``；
      空串 / 全空白 → 删除该键（语义=清除改稿意见）。
    - 不设 ``max_length``：note 是人工意见，无明确字节上限；底层走 plan_json TEXT。
    """

    note: str = Field(..., min_length=0)


@router.patch("/chapters/{chapter_id}/revision-note", response_model=Chapter)
def update_chapter_revision_note(
    chapter_id: str,
    payload: RevisionNoteUpdate,
    request: Request,
) -> dict:
    try:
        row = _service(request).update_revision_note(chapter_id, payload.note)
    except RevisionNoteStatusNotAllowed as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                f"chapter {chapter_id!r} status is {exc.current!r}; "
                f"revision_note update only allowed when status is 'PLANNED', "
                f"'DRAFTED' or 'REVIEWED'"
            ),
        ) from exc
    if row is None:
        raise HTTPException(status_code=404, detail=f"chapter {chapter_id!r} not found")
    return row
