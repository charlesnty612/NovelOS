"""Quality REST 路由（Sprint 6 下半）。

端点（挂在 ``/api`` 前缀下）：
- ``GET  /chapters/{chapter_id}/quality``       —— 最新一份 report；不存在 → 404
- ``GET  /projects/{project_id}/quality``       —— 项目全部 report 列表（created_at DESC，limit 默认 50）
- ``POST /chapters/{chapter_id}/quality/evaluate`` —— 现场组装 ctx + 评估 + 落库 + 返回；201
- ``POST /chapters/{chapter_id}/quality/judge``  —— V3.1 P1-2：LLM judge 双轨评分落库
- ``GET  /projects/{project_id}/quality/q8-export`` —— 全章节人工加工占比 CSV（PRD §125 合规自证）

错误码映射：
- 404 — chapter / project 不存在；
- 422 — 评估过程中未捕获的 pydantic / schema 错误；
- 500 — DB 错误或意外异常。

设计要点：
- 复用 :class:`packages.core.quality.service.QualityService`（save_report /
  save_judge_score / latest / list）以及 :func:`packages.core.quality.service.build_quality_context`
  （与 ``packages/workflows/chapter_commit/pipeline.py`` 共用）；
- 评估不经过 chapter_commit pipeline；该端点对应「人工触发一次最新评估」的轻量入口，
  与门禁节点同语义但不对 chapter 状态 / commit 产生副作用（最多落一份新 report）。
- V3.1 P1-2：``/quality/judge`` 端点与七子分完全解耦，``judge`` 字段单独附在
  GET 响应顶层，无 judge 时为 ``null``。
- ``discover_routers`` 自动发现：模块顶层 ``router`` 即被 ``main.py`` 挂载。
- ``q8-export`` 端点：按章节实时调 :func:`packages.core.quality.service.compute_char_stats`
  计算 ai/human 字符数（与 :func:`guardrails.req_q8` 算法一致）；输出 csv（utf-8-sig）
  供番茄平台审核自证。
"""

from __future__ import annotations

import csv
import io
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from packages.core.db import get_connection
from packages.core.logging_config import get_logger
from packages.core.quality.engine import QualityEngine
from packages.core.quality.service import (
    QualityService,
    build_quality_context,
    capture_reference_consumption,
    compute_char_stats,
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


def _attach_judge(row: dict[str, Any] | None) -> dict[str, Any]:
    """V3.1 P1-2：从 ``row["judge_json"]`` 派生顶层 ``judge`` 字段（无则 ``None``）。

    - ``judge_json`` 已是 dict / None（service 层 ``_row_to_dict`` 解析过）；
    - 本函数不修改七子分与 overall；只是把 judge 的 JSON 内容挪到独立的 ``judge`` 键，
      便于前端一查就能识别「LLM judge 旁路数据」存在与否。
    """
    if row is None:
        return {}
    judge = row.pop("judge_json", None)
    # 兼容历史版本：旧库迁移前未跑 0012 时 ``judge_json`` 不在 row 中；一律视为 None
    row["judge"] = judge if isinstance(judge, (dict, type(None))) else None
    return row


@router.get("/chapters/{chapter_id}/quality")
def get_chapter_quality(chapter_id: str, request: Request) -> dict:
    """该 chapter 的最新一份 QualityReport；不存在 → 404。

    V3.1 P1-2：响应顶层附 ``judge`` 字段（来自 ``quality_reports.judge_json``，
    双轨并存；未评审或评审失败时为 ``null``）。七子分 ``scores_json`` / overall
    / issues_json / formula_hash 等字段不变；judge 不进入 overall 计算。
    """
    _ensure_chapter(request, chapter_id)
    row = _service(request).latest_report(chapter_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"chapter {chapter_id!r} has no quality report yet",
        )
    return _attach_judge(row)


@router.post(
    "/chapters/{chapter_id}/quality/judge",
    status_code=status.HTTP_200_OK,
)
def save_chapter_judge(chapter_id: str, payload: dict, request: Request) -> dict:
    """V3.1 P1-2：把 LLM judge 四维评分（pacing/style/logic/dialogue 0-100）+ 扩展
    字段（verdict / top_issues / score_avg / usage / dry_run）落库到该 chapter
    最新一份 report 的 ``judge_json``。

    - chapter 不存在 → 404；
    - payload 缺四维分键 → 422（``QualityService.save_judge_score`` 抛 ``ValueError``）；
    - 该 chapter 没有 quality_report → 422（先跑 evaluate 再落 judge）。
    - 返回 ``{"report_id", "chapter_id", "judge": {...}}``，与 POST evaluate 的
      风格一致（chapter_id 回显、报告标识）。
    """
    _ensure_chapter(request, chapter_id)
    try:
        report_id = _service(request).save_judge_score(chapter_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    row = _service(request).latest_report(chapter_id) or {}
    return {
        "report_id": report_id,
        "chapter_id": chapter_id,
        "judge": row.get("judge_json"),
    }


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
    rows = _service(request).list_reports(project_id, limit=int(limit))
    return [_attach_judge(r) for r in rows]


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

    # Sprint V1.4：参照系消费可观测——与 chapter_commit pipeline 同口径，把
    # 本次评估消费的 *.txt 清单写入 _meta.reference_consumption，便于前端复用。
    ref_consumption = capture_reference_consumption(db_path, project_id)
    meta = dict(report.meta or {})
    meta["reference_consumption"] = ref_consumption
    report.meta = meta

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

    return _attach_judge(_service(request).latest_report(chapter_id) or {})


@router.get("/projects/{project_id}/quality/q8-export")
def export_q8_csv(project_id: str, request: Request) -> Response:
    """导出全章节「人工加工占比」CSV（PRD §125 合规自证）。

    - 数据源：按章节实时调 :func:`compute_char_stats` 读 ``drafts`` 表重算 ai/human
      字符数（与 ``guardrails.req_q8`` 用同一函数，保证「导出数字 ≡ 评估口径」）。
      并对该章 ``quality_reports`` 取 ``MAX(created_at)`` 作为 ``evaluated_at``（未评估
      → 空串）。
    - 输出 CSV 列：``chapter_number, chapter_title, chapter_status, ai_chars,
      human_chars, human_ratio, evaluated_at``。按 chapter_number 升序。``human_ratio``
      是百分比（保留 1 位小数），total=0 时输出 ``0.0``。
    - 响应头 ``Content-Type: text/csv; charset=utf-8`` + ``Content-Disposition: attachment;
      filename="q8-report-<project_id>.csv"``；UTF-8 BOM 以兼容 Excel 中文显示。
    - project 不存在 → 404。
    """
    settings = request.app.state.settings
    db_path = _db_path(request)
    from packages.domain.project.service import ProjectService

    if ProjectService(settings.db_path).get(project_id) is None:
        raise HTTPException(
            status_code=404, detail=f"project {project_id!r} not found"
        )

    chapters = ChapterService(db_path).list_by_project(project_id)
    # 取每章最新一次评估的 created_at（evaluated_at 显示口径）。
    conn = get_connection(db_path)
    try:
        latest_per_chapter: dict[str, str] = {}
        for row in conn.execute(
            """
            SELECT chapter_id, MAX(created_at) AS evaluated_at
            FROM quality_reports
            WHERE project_id = ?
            GROUP BY chapter_id
            """,
            (project_id,),
        ).fetchall():
            latest_per_chapter[row["chapter_id"]] = row["evaluated_at"] or ""
    finally:
        conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "chapter_number",
            "chapter_title",
            "chapter_status",
            "ai_chars",
            "human_chars",
            "human_ratio",
            "evaluated_at",
        ]
    )
    for ch in chapters:
        ai, human = compute_char_stats(db_path, ch["chapter_id"])
        total = ai + human
        ratio_pct = (human / total * 100) if total > 0 else 0.0
        writer.writerow(
            [
                ch.get("number", 0),
                ch.get("title") or "",
                ch.get("status") or "",
                int(ai),
                int(human),
                f"{ratio_pct:.1f}",
                latest_per_chapter.get(ch["chapter_id"], ""),
            ]
        )

    body = ("\ufeff" + buf.getvalue()).encode("utf-8")  # UTF-8 BOM for Excel
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="q8-report-{project_id}.csv"'
            ),
        },
    )
