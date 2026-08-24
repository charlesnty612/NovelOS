"""AI 调用日志查询路由（Sprint 13 下半）。

挂在 ``/api`` 前缀下（discover_routers 自动发现）。

端点：
- ``GET /ai-call-logs``            —— 分页列表（按 created_at DESC），可选按 project_id / node 过滤
- ``GET /ai-call-logs/{log_id}``   —— 单条详情（含 input_context_ids + output_json）

设计要点：
- 仅读 ai_call_logs（PRD §93 落地表，database/migrations/0001_init.sql L424-442）。
- **不暴露任何 API key** —— 表 schema 本来就不含 api_key 字段（model_id 仅是
  ``provider/model`` 字符串），响应 Pydantic 化时也明确不引入 key 字段。
- list 默认 limit=50、上限 200；offset 必须 >= 0。
- project_id 过滤通过 ``JOIN workflow_runs ON run_id`` 实现（ai_call_logs
  本身不含 project_id）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from packages.core.db import get_connection
from packages.core.logging_config import get_logger

log = get_logger("novelos.routers.ai_call_logs")

router = APIRouter(tags=["ai-call-logs"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

# 安全白名单：list 返回的字段。绝不引入任何 key/api_key/secret 字段。
_SUMMARY_FIELDS = (
    "call_id",
    "run_id",
    "node_run_id",
    "agent_id",
    "model_id",
    "prompt_version",
    "latency_ms",
    "retry_count",
    "error",
    "token_usage_json",
    "cost",
    "created_at",
)

# 敏感字段黑名单（即使将来 schema 变更也不允许出现在响应里）。
_FORBIDDEN_FIELD_KEYS = {"api_key", "key", "secret", "api_secret", "access_token"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_json_loads(raw: str | None) -> Any:
    """解析 JSON 列；解析失败或为 None 返回 None。"""
    if not raw:
        return None
    import json

    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    """列表摘要：序列化 JSON 列（token_usage_json），不含完整 prompt/response。

    敏感字段黑名单（api_key / key / secret / api_secret / access_token）一律
    剥离 —— 当前 schema 本就无这些字段，但作为防御层保留。
    """
    out: dict[str, Any] = {}
    for k in _SUMMARY_FIELDS:
        v = row.get(k)
        if k == "token_usage_json":
            out["token_usage"] = _safe_json_loads(v)
        elif k in _FORBIDDEN_FIELD_KEYS:
            # 防御层：若 DB 误增敏感列，强制剥离。
            continue
        else:
            out[k] = v
    # 再次扫描输出 dict，防止意外键泄漏
    return {k: v for k, v in out.items() if k not in _FORBIDDEN_FIELD_KEYS}


def _detail(row: dict[str, Any]) -> dict[str, Any]:
    """详情：在 summary 基础上加 input_context_ids + output（解析后的 JSON）。

    input_context_ids_json 是纯 id 列表（runner.py L52-69 实现：仅收集 *_id
    键值，**不存原文**），详情端点把它解析成 list 返回。
    """
    base = _summary(row)
    base["input_context_ids"] = _safe_json_loads(row.get("input_context_ids_json")) or []
    out_json = row.get("output_json")
    base["output"] = _safe_json_loads(out_json)
    return {k: v for k, v in base.items() if k not in _FORBIDDEN_FIELD_KEYS}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/ai-call-logs")
def list_ai_call_logs(
    request: Request,
    project_id: str | None = Query(default=None, description="按项目过滤（JOIN workflow_runs）"),
    node: str | None = Query(default=None, description="按 node_run_id 过滤"),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """分页列出 ai_call_logs（按 created_at DESC）。

    查询参数：
    - ``project_id`` —— 可选；按项目过滤（JOIN workflow_runs）。
    - ``node`` —— 可选；按 node_run_id 精确过滤。
    - ``limit`` —— 默认 50，上限 200。
    - ``offset`` —— 默认 0。
    """
    db = str(request.app.state.settings.db_path)
    conn = get_connection(db)
    try:
        cols = ", ".join(f"l.{c}" for c in _SUMMARY_FIELDS) + ", l.input_context_ids_json, l.output_json"
        sql = f"SELECT {cols} FROM ai_call_logs l"
        params: list[Any] = []
        where: list[str] = []
        if project_id:
            # workflow_runs 没有 project_id 列：通过 chapter_id 间接拿到
            # (ai_call_logs → workflow_runs → chapters → projects)。
            sql += (
                " JOIN workflow_runs r ON r.run_id = l.run_id"
                " JOIN chapters ch ON ch.chapter_id = r.chapter_id"
            )
            where.append("ch.project_id = ?")
            params.append(project_id)
        if node:
            where.append("l.node_run_id = ?")
            params.append(node)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY l.created_at DESC, l.call_id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(sql, params).fetchall()
        return [_summary(dict(r)) for r in rows]
    finally:
        conn.close()


@router.get("/ai-call-logs/{log_id}")
def get_ai_call_log(log_id: str, request: Request) -> dict[str, Any]:
    """单条详情：含 input_context_ids + output（解析后）。"""
    db = str(request.app.state.settings.db_path)
    conn = get_connection(db)
    try:
        row = conn.execute(
            "SELECT * FROM ai_call_logs WHERE call_id = ?",
            (log_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"ai_call_log {log_id!r} not found"
        )
    return _detail(dict(row))


__all__ = ["list_ai_call_logs", "get_ai_call_log"]