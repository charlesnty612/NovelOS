"""Reference Canon REST 路由（Sprint 11 上半）。

挂在 ``/api`` 前缀下（discover_routers 自动发现）。

端点：
- ``POST /projects/{project_id}/deconstruct``  —— 启动 deconstruct-book 工作流；201
- ``GET  /projects/{project_id}/canons``       —— 列出项目下全部 active canon；按 created_at DESC
- ``GET  /canons/{canon_id}``                  —— 单一 canon 全文（canon_json + report_md）
- ``DELETE /canons/{canon_id}``                —— 级联删除 canon + 关联 extracts；204

错误码映射：
- 404 — project / canon 不存在；
- 422 — 请求体字段非法或 workflow 注册缺失；
- 500 — workflow run 中未捕获异常。

设计要点：
- POST deconstruct：复用 :class:`WorkflowEngine.start_with_nodes` 模式（与 workflows.py 对齐），
  通过 ``packages.workflows.get_workflow`` 取节点；run 同步执行到底（deconstruct 不挂 Human 节点）；
  失败 → run FAILED，路由仍返回 201（含 status 字段便于前端诊断）。
- list 摘要：从 canon_json 解析 logline + spine 长度 + rhythm 章节数；canon_json 完整 JSON 不展开。
- DELETE：单事务级联删 canon_extracts + reference_canons；FK ON DELETE CASCADE 启用则更稳，
  本实现显式事务删除便于审计。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.db import get_connection
from packages.core.logging_config import get_logger
from packages.core.workflow_runtime.engine import WorkflowEngine
from packages.core.workflow_runtime.runs import get_run
from packages.domain.project.service import ProjectService
from packages.workflows import get_workflow

log = get_logger("novelos.routers.reference")

router = APIRouter(tags=["reference"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db_path(request: Request) -> str:
    return str(request.app.state.settings.db_path)


def _engine(request: Request) -> WorkflowEngine:
    return WorkflowEngine(_db_path(request))


def _ensure_project(request: Request, project_id: str) -> None:
    if ProjectService(_db_path(request)).get(project_id) is None:
        raise HTTPException(
            status_code=404, detail=f"project {project_id!r} not found"
        )


def _summary_of(canon_row: sqlite3.Row) -> dict[str, Any]:
    """从 canon_json 解析摘要字段；解析失败 → 返回空摘要但保留 row 字段。"""
    canon_id = canon_row["canon_id"]
    project_id = canon_row["project_id"]
    title = canon_row["title"]
    reader_profile = canon_row["reader_profile"]
    status_val = canon_row["status"]
    created_at = canon_row["created_at"]
    raw_canon_json = canon_row["canon_json"] or "{}"

    summary: dict[str, Any] = {
        "canon_id": canon_id,
        "project_id": project_id,
        "title": title,
        "reader_profile": reader_profile,
        "status": status_val,
        "created_at": created_at,
        "logline": "",
        "spine_count": 0,
        "rhythm_chapter_count": 0,
    }
    try:
        cj = json.loads(raw_canon_json)
        if isinstance(cj, dict):
            logline = cj.get("logline")
            if isinstance(logline, str):
                summary["logline"] = logline
            spine = cj.get("spine")
            if isinstance(spine, list):
                summary["spine_count"] = len(spine)
            emo = cj.get("emotion_curve")
            if isinstance(emo, list):
                summary["rhythm_chapter_count"] = len(emo)
    except (TypeError, ValueError):
        pass
    return summary


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/deconstruct
# ---------------------------------------------------------------------------


class DeconstructRequest:
    """请求体：``book_title`` / ``text`` / ``reader_profile``（可选）/ ``mock_providers``（可选）。"""

    def __init__(self, **data: Any) -> None:
        self.book_title = data["book_title"]
        self.text = data["text"]
        self.reader_profile = data.get("reader_profile") or "male_fantasy"
        self.mock_providers = data.get("mock_providers") or None


@router.post(
    "/projects/{project_id}/deconstruct",
    status_code=status.HTTP_201_CREATED,
)
def start_deconstruct(
    project_id: str,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    """启动 deconstruct-book workflow。"""
    _ensure_project(request, project_id)

    book_title = payload.get("book_title")
    text = payload.get("text")
    if not isinstance(book_title, str) or not book_title.strip():
        raise HTTPException(
            status_code=422,
            detail="book_title must be a non-empty string",
        )
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(
            status_code=422,
            detail="text must be a non-empty string",
        )
    reader_profile = payload.get("reader_profile") or "male_fantasy"
    mock_providers = payload.get("mock_providers") or None

    workflow = get_workflow("deconstruct-book")
    if workflow is None:
        raise HTTPException(
            status_code=500,
            detail="workflow 'deconstruct-book' not registered",
        )

    initial_ctx: dict[str, Any] = {
        "db_path": _db_path(request),
        "project_id": project_id,
        "book_title": book_title,
        "text": text,
        "reader_profile": reader_profile,
    }
    if mock_providers:
        initial_ctx["mock_providers"] = mock_providers

    try:
        run_id = _engine(request).start_with_nodes(
            "deconstruct-book",
            workflow["nodes"],
            chapter_id=None,
            initial_ctx=initial_ctx,
            mock_providers=mock_providers,
            checkpoint_exclude=["text"],
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run = get_run(_db_path(request), run_id)
    if run is None:
        raise HTTPException(status_code=500, detail="run disappeared after start")

    out: dict[str, Any] = {
        "run_id": run_id,
        "status": run["status"],
        "current_node": run.get("current_node"),
        "project_id": project_id,
        "book_title": book_title,
    }
    # 如 COMPLETED，把 canon_id + extracts_count 提取出来便于前端
    if run["status"] == "COMPLETED":
        ckpt = run.get("checkpoint_json") or {}
        persist = ckpt.get("T4_persist") if isinstance(ckpt, dict) else None
        if isinstance(persist, dict):
            if "canon_id" in persist:
                out["canon_id"] = persist["canon_id"]
            if "extracts_count" in persist:
                out["extracts_count"] = persist["extracts_count"]
    if run["status"] == "FAILED":
        out["error"] = run.get("error")
    return out


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/canons
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/canons")
def list_project_canons(project_id: str, request: Request) -> list[dict[str, Any]]:
    """列出项目下全部 active canon（按 created_at DESC）。"""
    _ensure_project(request, project_id)
    conn = get_connection(_db_path(request))
    try:
        rows = conn.execute(
            """
            SELECT canon_id, project_id, title, reader_profile, status,
                   canon_json, created_at
            FROM reference_canons
            WHERE project_id = ? AND status = 'active'
            ORDER BY created_at DESC, canon_id DESC
            """,
            (project_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_summary_of(r) for r in rows]


# ---------------------------------------------------------------------------
# GET /canons/{canon_id}
# ---------------------------------------------------------------------------


@router.get("/canons/{canon_id}")
def get_canon(canon_id: str, request: Request) -> dict[str, Any]:
    """单一 canon 全文（canon_json + report_md + extracts 列表）。"""
    conn = get_connection(_db_path(request))
    try:
        row = conn.execute(
            """
            SELECT canon_id, project_id, title, reader_profile, status,
                   canon_json, report_md, created_at
            FROM reference_canons WHERE canon_id = ?
            """,
            (canon_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"canon {canon_id!r} not found"
            )
        extracts = conn.execute(
            """
            SELECT extract_id, chapter_index, extract_json, created_at
            FROM canon_extracts WHERE canon_id = ?
            ORDER BY chapter_index ASC
            """,
            (canon_id,),
        ).fetchall()
    finally:
        conn.close()

    canon_json = row["canon_json"]
    try:
        canon_json_obj = json.loads(canon_json) if canon_json else {}
    except (TypeError, ValueError):
        canon_json_obj = {}

    out = {
        "canon_id": row["canon_id"],
        "project_id": row["project_id"],
        "title": row["title"],
        "reader_profile": row["reader_profile"],
        "status": row["status"],
        "canon_json": canon_json_obj,
        "report_md": row["report_md"],
        "created_at": row["created_at"],
        "extracts": [
            {
                "extract_id": e["extract_id"],
                "chapter_index": e["chapter_index"],
                "extract_json": _safe_parse(e["extract_json"]),
                "created_at": e["created_at"],
            }
            for e in extracts
        ],
    }
    return out


def _safe_parse(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


# ---------------------------------------------------------------------------
# DELETE /canons/{canon_id}
# ---------------------------------------------------------------------------


@router.delete("/canons/{canon_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_canon(canon_id: str, request: Request) -> None:
    """级联删除 canon + 关联 extracts（事务）。"""
    conn = get_connection(_db_path(request))
    try:
        cur = conn.execute(
            "SELECT canon_id FROM reference_canons WHERE canon_id = ?", (canon_id,)
        ).fetchone()
        if cur is None:
            raise HTTPException(
                status_code=404, detail=f"canon {canon_id!r} not found"
            )
        # 显式事务删除（MVP：FK 兜底；事务便于审计）
        conn.execute("DELETE FROM canon_extracts WHERE canon_id = ?", (canon_id,))
        conn.execute("DELETE FROM reference_canons WHERE canon_id = ?", (canon_id,))
        conn.commit()
    finally:
        conn.close()
    return None


__all__ = ["router"]