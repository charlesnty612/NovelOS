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
- 两条端点的出参**已 Pydantic 化**且形状**不同**：列表行 = :class:`AiCallLogSummary`
  的 12 键摘要，详情行 = :class:`AiCallLogDetail` 的 14 键（多 ``input_context_ids``
  与 ``output``）。键集是 2026-10-15 用临时库跑通端点 dump 实测的（跑一次 mock
  agent 落一行日志再 GET 列表），方法记录见
  ``tests/api/test_config_list_response_contract.py``。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from packages.core.db import get_connection
from packages.core.logging_config import get_logger

log = get_logger("novelos.routers.ai_call_logs")

router = APIRouter(tags=["ai-call-logs"])


class AiCallLogDetail(BaseModel):
    """``GET /ai-call-logs/{log_id}`` 的响应（:func:`_detail` 的产物）。

    恒 14 键、无条件键：``_summary`` 的 12 个摘要位（其中 ``token_usage_json``
    列被换名成解析后的 ``token_usage``）＋ 详情端点特有的 ``input_context_ids`` /
    ``output``。列表端点的 12 键摘要**不含**后两个，故两者不是同一形状。

    不可为空 ≠ 恒有值：除 ``call_id`` / ``run_id`` / ``retry_count`` /
    ``created_at`` / ``input_context_ids`` 外，实测均可为 null（空 error 列、
    未绑 agent 的历史行、解析失败的 JSON 列 → ``_safe_json_loads`` 返回 None）。
    声明成 ``| None`` 是照实测，**不是**为了松校验。

    安全红线：表 schema 本就不含 api_key 列，本模型**不得**新增任何 key/secret
    字段（``_FORBIDDEN_FIELD_KEYS`` 是 router 侧的防御层）。
    """

    call_id: str
    run_id: str
    node_run_id: str | None = None
    agent_id: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    latency_ms: int | None = None
    retry_count: int
    error: str | None = None
    token_usage: dict[str, Any] | None = None
    cost: float | None = None
    created_at: str
    # runner.py L52-69：只存 *_id 键值，**原文不落 DB**
    input_context_ids: list[str]
    # output_json 解析结果；解析失败/为 NULL → None（不做二次兜底类型）
    output: Any = None


class AiCallLogSummary(BaseModel):
    """``GET /ai-call-logs`` 的一行：:func:`_summary` 的 12 键摘要。

    恒 12 键、无条件键（:data:`_SUMMARY_FIELDS` 是**定长元组**、逐项写进 out，
    且 ``token_usage_json`` 被换名成解析后的 ``token_usage``）——与
    :class:`AiCallLogDetail` 的 14 键**不是同一形状**：列表行不含
    ``input_context_ids`` / ``output``（那两项要读全文，只给详情端点）。

    ⚠️ 八个可空键**无默认值**（``X | None`` 而非 ``X | None = None``）⇒ 进
    OpenAPI ``required``。这是刻意的：键**恒在**（无数据时值为 ``null``），
    给了默认值就变成「可省略」，前端据此会写出错误的类型（沿用
    :class:`ProfileTestResult` 的同一判定，判别用例见
    ``tests/api/test_config_list_response_contract.py``）。

    本模型**刻意不加** ``extra="allow"``（与 :class:`AgentListRowOut` 相反的口径，
    理由如下）：摘要是由**白名单元组** :data:`_SUMMARY_FIELDS` 逐项组装的，键集在
    构造上封闭，多出来的键只可能来自那个元组被改动——而那正是需要被看见的变更。
    安全面上它把 :data:`_FORBIDDEN_FIELD_KEYS` 从「防御层」升级成「**出口保证**」：
    pydantic v2 默认 ``extra="ignore"``，未声明的键被**丢弃**（响应里根本不出现），
    故即便将来有人往 ``_SUMMARY_FIELDS`` 里塞了一个 ``api_key`` 列，明文也**到不了
    客户端**。⚠️ 代价要说准：那是**静默丢弃**、**不是** 500——「加了一列却被模型
    吞掉」正是 ``extra="allow"`` 要防的反面，本组选择在这里把它记为**已知取舍**
    （宁可丢也不泄），由 ``_SUMMARY_FIELDS`` 的白名单纪律 + 契约测试兜住。

    安全红线：与 :class:`AiCallLogDetail` 同——表 schema 本就不含 key 列，
    本模型不得新增任何 key/secret 字段。
    """

    call_id: str
    run_id: str
    node_run_id: str | None
    agent_id: str | None
    model_id: str | None = Field(description="'provider/model' 字符串（mock 路径下是 'mock/mock'）")
    prompt_version: str | None = Field(description="形如 'writer:v3'（含 agent 前缀，与 prompts.version 不同形态）")
    latency_ms: int | None
    retry_count: int = Field(description="只计「输出不合契约」的重试（0/1），不是引擎的 run 级重试")
    error: str | None
    token_usage: dict[str, Any] | None
    cost: float | None
    created_at: str

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


@router.get("/ai-call-logs", response_model=list[AiCallLogSummary])
def list_ai_call_logs(
    request: Request,
    project_id: str | None = Query(default=None, description="按项目过滤（JOIN workflow_runs）"),
    node: str | None = Query(default=None, description="按 node_run_id 过滤"),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """分页列出 ai_call_logs（按 created_at DESC）。

    形状 = :class:`AiCallLogSummary`（12 键摘要，**不是** 14 键的详情形状）。
    严格模型 ⇒ 「少声明一个字段」在响应侧立刻可见（少一个键），
    而 ``extra="allow"`` 的行模型（档案/配置/绑定/agent）只能从 OpenAPI 侧抓。

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


@router.get("/ai-call-logs/{log_id}", response_model=AiCallLogDetail)
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


__all__ = ["list_ai_call_logs", "get_ai_call_log", "AiCallLogDetail", "AiCallLogSummary"]
