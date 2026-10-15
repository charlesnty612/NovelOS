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


class StyleSample(BaseModel):
    """文风样例行（``author_style_samples`` 表全列，**恒 6 键、无条件键**）。

    实测：POST 返回体与 GET 列表行**同形**（router 两条路都是整行投影），
    故共用一个模型；``created_at`` / ``updated_at`` 由 router 写 ``now_iso()``，
    创建时二者必然相等，之后只有 UPDATE 才会拉开。

    2026-10-15 批次（六）L4 起，``GET`` 列表端点也声明了本模型 ⇒ 这条「同形」
    从「散文里的观察」变成**被测试钉住的契约**：任一端点多/少一个键，
    ``tests/api/test_quality_reference_list_response_contract.py`` 必红。
    """

    sample_id: str
    project_id: str
    title: str
    content: str
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/projects/{pid}/style-samples", response_model=list[StyleSample])
def list_style_samples(pid: str, request: Request) -> list[dict]:
    """列出该项目的全部文风样例（created_at DESC）。

    **复用** POST 用的同一个 :class:`StyleSample`（2026-10-15 批次（六）L4 实测）：
    两条路都是「整行投影」⇒ 恒 6 键、无条件键。

    ⚠️ **不设 ``extra="allow"``**：本端点的 SQL 是**显式 6 列枚举**（不是
    ``SELECT *``）⇒ 表将来加列**不会**出现在响应里，多出来的键只可能是 bug
    （与 ``QualityReportResponse`` 必须开 ``extra="allow"`` 的理由相反——
    那边是 ``SELECT *``）。判别方式见
    ``tests/api/test_quality_reference_list_response_contract.py``。
    """
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
    response_model=StyleSample,
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
