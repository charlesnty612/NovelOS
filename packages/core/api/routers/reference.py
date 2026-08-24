"""Reference Canon REST 路由（Sprint 11 上半 + V1.5 架构整理）。

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
- V1.5 起，路由层不再直接写 SQL——所有引用数据访问走
  :class:`packages.domain.reference.ReferenceService`；路由仅做参数校验 + 调用 +
  错误映射。
- POST deconstruct：复用 :class:`WorkflowEngine.start_with_nodes` 模式（与 workflows.py 对齐），
  通过 :func:`packages.core.workflow_registry.get_workflow` 取节点；run 同步执行到底
  （deconstruct 不挂 Human 节点）；失败 → run FAILED，路由仍返回 201（含 status 字段便于前端诊断）。
- list 摘要：从 canon_json 解析 logline + spine 长度 + rhythm 章节数；canon_json 完整 JSON 不展开。
- DELETE：单事务级联删 canon_extracts + reference_canons；FK ON DELETE CASCADE 启用则更稳，
  本实现显式事务删除便于审计。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.logging_config import get_logger
from packages.core.model_router import ModelNotConfiguredError, ModelRouter
from packages.core.workflow_registry import get_workflow
from packages.core.workflow_runtime.engine import WorkflowEngine
from packages.core.workflow_runtime.runs import get_run
from packages.domain.project.service import ProjectService
from packages.domain.reference import ReferenceService

log = get_logger("novelos.routers.reference")

router = APIRouter(tags=["reference"])

# 拆书文本硬上限：5 MB 字符。超过则 422 拒绝（防 DoS 与内存放大）。
_MAX_DECONSTRUCT_TEXT_LEN = 5_000_000

# 拆书工作流涉及的 capability 集合（deconstructor_chapter / deconstructor_aggregate
# 在 packages.core.model_router.AGENT_CAPABILITY 均映射到 "reasoning"）。
_DECONSTRUCT_REQUIRED_CAPABILITIES: tuple[str, ...] = ("reasoning",)


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
    # P1-2：文本硬上限（5 MB 字符），防止超大 body / 内存放大 / DoS。
    if len(text) > _MAX_DECONSTRUCT_TEXT_LEN:
        raise HTTPException(
            status_code=422,
            detail=(
                f"text exceeds {_MAX_DECONSTRUCT_TEXT_LEN} chars (got {len(text)}); "
                f"split into smaller chunks"
            ),
        )
    reader_profile = payload.get("reader_profile") or "male_fantasy"
    mock_providers = payload.get("mock_providers") or None

    workflow = get_workflow("deconstruct-book")
    if workflow is None:
        raise HTTPException(
            status_code=500,
            detail="workflow 'deconstruct-book' not registered",
        )

    # P1-4：拆书工作流启动前预检查 capability 是否有可用模型。
    # mock 路径（请求体带 ``mock_providers``）不依赖 model_config，跳过此检查；
    # 生产路径（无 mock_providers）必须预检查 capability 是否可用，
    # 在启动 run 之前就 422 拒绝，避免启动一个注定 FAILED 的 run + 兜底 ai_call_logs。
    if not mock_providers:
        router = ModelRouter(_db_path(request))
        for cap in _DECONSTRUCT_REQUIRED_CAPABILITIES:
            if not router.list_enabled(cap):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"no enabled model configured for capability={cap!r}; "
                        f"请先在 AI 设置里启用模型"
                    ),
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
    # P1-4：未配置可用模型时（deconstructor_* 走 ModelRouter.resolve），从 500 兜成 422
    # 让前端明确「未配置模型」而非不可恢复的服务错误。
    except ModelNotConfiguredError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

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
    svc = ReferenceService(_db_path(request))
    return svc.list_active_summaries(project_id)


# ---------------------------------------------------------------------------
# GET /canons/{canon_id}
# ---------------------------------------------------------------------------


@router.get("/canons/{canon_id}")
def get_canon(canon_id: str, request: Request) -> dict[str, Any]:
    """单一 canon 全文（canon_json + report_md + extracts 列表）。"""
    svc = ReferenceService(_db_path(request))
    detail = svc.get_canon_detail(canon_id)
    if detail is None:
        raise HTTPException(
            status_code=404, detail=f"canon {canon_id!r} not found"
        )
    return detail


# ---------------------------------------------------------------------------
# DELETE /canons/{canon_id}
# ---------------------------------------------------------------------------


@router.delete("/canons/{canon_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_canon(canon_id: str, request: Request) -> None:
    """级联删除 canon + 关联 extracts（事务）。"""
    svc = ReferenceService(_db_path(request))
    ok = svc.delete_canon_cascade(canon_id)
    if not ok:
        raise HTTPException(
            status_code=404, detail=f"canon {canon_id!r} not found"
        )
    return None


__all__ = ["router"]