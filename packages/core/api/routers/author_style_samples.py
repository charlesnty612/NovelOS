"""Author style samples REST 路由（Sprint 15 / V1.3）。

挂在 ``/api`` 前缀下，由 ``packages/core/api/main.py`` 的 ``discover_routers()``
自动发现。

端点清单
========
- GET    /api/projects/{pid}/style-samples                列表（created_at DESC）
- POST   /api/projects/{pid}/style-samples                新增（201）
- DELETE /api/projects/{pid}/style-samples/{sample_id}    删除（204）

约束（与任务书 V1.3 §A 一致）：
- 单篇 ``content`` 上限 5000 字（超限 → 422）。
- 单项目上限 10 篇（超限 → 422）。
- 不暴露 content 之外的截断与拼装策略；context_engine.builders 负责注入 writer 输入。

错误码
------
- 201 / 200 / 204：成功
- 404：project 不存在 / sample_id 不属于该项目
- 422：参数校验失败 / content 超过 5000 字 / 已达 10 篇上限
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso

router = APIRouter(tags=["author-style-samples"])

# ---------------------------------------------------------------------------
# 常量与限制（与任务书 V1.3 §A 对齐）
# ---------------------------------------------------------------------------

_MAX_CONTENT_CHARS = 5000      # 单篇 content 上限
_MAX_SAMPLES_PER_PROJECT = 10  # 单项目上限


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _conn(request: Request) -> sqlite3.Connection:
    return get_connection(request.app.state.settings.db_path)


def _project_exists(pid: str, request: Request) -> bool:
    conn = _conn(request)
    try:
        row = conn.execute(
            "SELECT 1 FROM projects WHERE project_id = ?", (pid,),
        ).fetchone()
    finally:
        conn.close()
    return row is not None


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class StyleSampleCreate(BaseModel):
    """POST 请求体。"""

    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/projects/{pid}/style-samples")
def list_style_samples(pid: str, request: Request) -> list[dict]:
    """列出该项目的全部文风样例（created_at DESC）。"""
    if not _project_exists(pid, request):
        raise HTTPException(status_code=404, detail=f"project {pid!r} not found")
    conn = _conn(request)
    try:
        rows = conn.execute(
            "SELECT sample_id, project_id, title, content, created_at, updated_at "
            "FROM author_style_samples WHERE project_id = ? "
            "ORDER BY created_at DESC, sample_id DESC",
            (pid,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@router.post(
    "/projects/{pid}/style-samples",
    status_code=status.HTTP_201_CREATED,
)
def create_style_sample(
    pid: str, payload: StyleSampleCreate, request: Request,
) -> dict:
    """新增一条文风样例；content 超 5000 字或已满 10 篇 → 422。"""
    if not _project_exists(pid, request):
        raise HTTPException(status_code=404, detail=f"project {pid!r} not found")
    if len(payload.content) > _MAX_CONTENT_CHARS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"content too long: {len(payload.content)} chars "
                f"> limit {_MAX_CONTENT_CHARS}"
            ),
        )
    conn = _conn(request)
    try:
        count_row = conn.execute(
            "SELECT COUNT(*) AS n FROM author_style_samples WHERE project_id = ?",
            (pid,),
        ).fetchone()
        if int(dict(count_row)["n"]) >= _MAX_SAMPLES_PER_PROJECT:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"project {pid!r} already has "
                    f"{_MAX_SAMPLES_PER_PROJECT} style samples (limit reached)"
                ),
            )
        sample_id = new_id("asty")
        now = now_iso()
        conn.execute(
            """
            INSERT INTO author_style_samples
                (sample_id, project_id, title, content, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (sample_id, pid, payload.title, payload.content, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "sample_id": sample_id,
        "project_id": pid,
        "title": payload.title,
        "content": payload.content,
        "created_at": now,
        "updated_at": now,
    }


@router.delete(
    "/projects/{pid}/style-samples/{sample_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_style_sample(pid: str, sample_id: str, request: Request) -> None:
    """删除一条文风样例；不存在 / 不属于该项目 → 404。"""
    conn = _conn(request)
    try:
        cur = conn.execute(
            "DELETE FROM author_style_samples "
            "WHERE sample_id = ? AND project_id = ?",
            (sample_id, pid),
        )
        conn.commit()
    finally:
        conn.close()
    if cur.rowcount == 0:
        raise HTTPException(
            status_code=404,
            detail=f"style sample {sample_id!r} not found in project {pid!r}",
        )
    return None
