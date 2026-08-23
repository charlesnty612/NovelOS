"""Quality REST 路由（Sprint 6 下半）。

端点（挂在 ``/api`` 前缀下）：
- ``GET  /chapters/{chapter_id}/quality``       —— 最新一份 report；不存在 → 404
- ``GET  /projects/{project_id}/quality``       —— 项目全部 report 列表（created_at DESC，limit 默认 50）
- ``POST /chapters/{chapter_id}/quality/evaluate`` —— 现场组装 ctx + 评估 + 落库 + 返回；201

错误码映射：
- 404 — chapter / project 不存在；
- 422 — 评估过程中未捕获的 pydantic / schema 错误；
- 500 — DB 错误或意外异常。

设计要点：
- 复用 :class:`packages.core.quality.service.QualityService`（save_report / latest /
  list）以及 :func:`packages.core.quality.service.build_quality_context`（与
  ``packages/workflows/chapter_commit/pipeline.py`` 共用）；
- 评估不经过 chapter_commit pipeline；该端点对应「人工触发一次最新评估」的轻量入口，
  与门禁节点同语义但不对 chapter 状态 / commit 产生副作用（最多落一份新 report）。
- ``discover_routers`` 自动发现：模块顶层 ``router`` 即被 ``main.py`` 挂载。
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.logging_config import get_logger
from packages.core.quality.engine import QualityEngine
from packages.core.quality.service import (
    QualityService,
    build_quality_context,
)
from packages.core.story_state.service import StoryStateService
from packages.domain.chapter.service import ChapterService

log = get_logger("novelos.routers.quality")

router = APIRouter(tags=["quality"])


def _service(request: Request) -> QualityService:
    settings = request.app.state.settings
    return QualityService(settings.db_path)


def _db_path(request: Request) -> str:
    return str(request.app.state.settings.db_path)


def _ensure_chapter(request: Request, chapter_id: str) -> None:
    """chapter 不存在时直接 404（与 chapters router 一致）。"""
    if ChapterService(_db_path(request)).get(chapter_id) is None:
        raise HTTPException(
            status_code=404, detail=f"chapter {chapter_id!r} not found"
        )


@router.get("/chapters/{chapter_id}/quality")
def get_chapter_quality(chapter_id: str, request: Request) -> dict:
    """该 chapter 的最新一份 QualityReport；不存在 → 404。"""
    _ensure_chapter(request, chapter_id)
    row = _service(request).latest_report(chapter_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"chapter {chapter_id!r} has no quality report yet",
        )
    return row


@router.get("/projects/{project_id}/quality")
def list_project_quality(
    project_id: str,
    request: Request,
    limit: int = 50,
) -> list[dict]:
    """列出该项目全部 quality report；按 ``created_at`` 降序。"""
    settings = request.app.state.settings
    from packages.domain.project.service import ProjectService

    if ProjectService(settings.db_path).get(project_id) is None:
        raise HTTPException(
            status_code=404, detail=f"project {project_id!r} not found"
        )
    return _service(request).list_reports(project_id, limit=int(limit))


@router.post(
    "/chapters/{chapter_id}/quality/evaluate",
    status_code=status.HTTP_201_CREATED,
)
def evaluate_chapter_quality(chapter_id: str, request: Request) -> dict:
    """现场对 chapter 触发一次 Quality 评估并落库，返回 report。

    - chapter 不存在 → 404；
    - 调用 :class:`StoryStateService.get_current_state` 拿 snapshot_pre（与 chapter_commit
      pipeline 的 quality_gate 节点保持同一口径）；
    - 复用 :func:`build_quality_context` + :class:`QualityEngine`；
    - 落库后返回 report dict（与 GET 端点同 schema）。
    """
    settings = request.app.state.settings
    db_path = _db_path(request)

    chapter = ChapterService(db_path).get(chapter_id)
    if chapter is None:
        raise HTTPException(
            status_code=404, detail=f"chapter {chapter_id!r} not found"
        )
    project_id = chapter["project_id"]
    run_id = request.headers.get("X-Workflow-Run-Id") or None
    try:
        snapshot_pre = StoryStateService(db_path).get_current_state(project_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("snapshot_pre unavailable: %s", exc)
        snapshot_pre = {}

    quality_ctx = build_quality_context(
        db_path,
        project_id=project_id,
        chapter_id=chapter_id,
        delta={},
        snapshot_pre=snapshot_pre,
        run_id=run_id,
    )
    try:
        report = QualityEngine().evaluate(quality_ctx)
    except ValueError as exc:
        # 理论 compute_overall 内部就会抛错；这里兜底让 router 转 422。
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        QualityService(db_path).save_report(
            report,
            project_id=project_id,
            chapter_id=chapter_id,
            run_id=run_id,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=422, detail=f"integrity error: {exc}"
        ) from exc

    return _service(request).latest_report(chapter_id) or {}
