"""Model Configs REST 路由（Sprint 3）。

挂在 ``/api`` 前缀下。对齐 ``model_configs`` 表（``database/migrations/0001_init.sql`` line 464-471）。

端点：
- ``POST /model-configs`` ——创建一条 model config（201）。
- ``GET /model-configs`` ——列出所有 configs（可按 ``capability`` / ``provider`` 过滤）。
- ``GET /model-configs/{id}`` ——按主键取单条（404 不存在）。
- ``PATCH /model-configs/{id}`` ——部分更新（capability / provider / model / params / enabled）。
- ``DELETE /model-configs/{id}`` ——删除（204）。
- ``POST /model-configs/{id}/test`` ——ping：发「回复 ok」，返回延迟（ms）与首 100 字。

设计要点：
- ``params_json`` 字段接受 JSON 字符串或 dict（前端友好）；写入时统一 ``json.dumps``。
- ``/test`` 端点直接构造对应 Provider，发一条最小消息；真实外网调用不要测（任务书边界）。
  失败抛 ``ProviderError``（→ 502）——与 ``agents.py`` 一致。
- enabled=0 的行不返回 / 不能用于路由（与 :class:`ModelRouter.resolve` 语义一致）。
"""

from __future__ import annotations

import json
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.db import get_connection
from packages.core.ids import new_id
from packages.core.logging_config import get_logger
from packages.core.model_router import ModelRouter, ProviderError

log = get_logger("novelos.routers.model_configs")

router = APIRouter(tags=["model_configs"])


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def _get_config(db_path: str, config_id: str) -> dict | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM model_configs WHERE config_id = ?", (config_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row else None


def _normalize_params(params_json) -> str:
    """统一 ``params_json`` 入参为 JSON 字符串（接受 dict 或 str）。"""
    if params_json is None:
        return "{}"
    if isinstance(params_json, str):
        # 校验可解析
        try:
            json.loads(params_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"params_json must be valid JSON: {exc}")
        return params_json
    if isinstance(params_json, dict):
        return json.dumps(params_json, ensure_ascii=False)
    raise HTTPException(status_code=422, detail="params_json must be a string or object")


@router.post("/model-configs", status_code=status.HTTP_201_CREATED)
def create_model_config(payload: dict, request: Request) -> dict:
    """创建一条 model config。必填字段：``capability / provider / model``。"""
    capability = payload.get("capability")
    provider = payload.get("provider")
    model = payload.get("model")
    if not (isinstance(capability, str) and capability):
        raise HTTPException(status_code=422, detail="capability required")
    if not (isinstance(provider, str) and provider):
        raise HTTPException(status_code=422, detail="provider required")
    if not (isinstance(model, str) and model):
        raise HTTPException(status_code=422, detail="model required")
    params_str = _normalize_params(payload.get("params_json"))
    enabled = payload.get("enabled", 1)
    if enabled not in (0, 1, True, False):
        raise HTTPException(status_code=422, detail="enabled must be 0 or 1")
    enabled_int = 1 if enabled in (1, True) else 0

    config_id = new_id("mcf")
    settings = request.app.state.settings
    conn = get_connection(settings.db_path)
    try:
        try:
            conn.execute(
                """
                INSERT INTO model_configs (config_id, capability, provider, model, params_json, enabled)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (config_id, capability, provider, model, params_str, enabled_int),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc
    finally:
        conn.close()
    row = _get_config(settings.db_path, config_id)
    assert row is not None
    return row


@router.get("/model-configs")
def list_model_configs(
    request: Request,
    capability: str | None = None,
    provider: str | None = None,
) -> list[dict]:
    settings = request.app.state.settings
    clauses: list[str] = []
    params: list = []
    if capability:
        clauses.append("capability = ?")
        params.append(capability)
    if provider:
        clauses.append("provider = ?")
        params.append(provider)
    sql = "SELECT * FROM model_configs"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY rowid ASC"
    conn = get_connection(settings.db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


@router.get("/model-configs/{config_id}")
def get_model_config(config_id: str, request: Request) -> dict:
    settings = request.app.state.settings
    row = _get_config(settings.db_path, config_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"model_config {config_id!r} not found")
    return row


@router.patch("/model-configs/{config_id}")
def patch_model_config(config_id: str, payload: dict, request: Request) -> dict:
    settings = request.app.state.settings
    fields: dict = {}
    if "capability" in payload:
        v = payload["capability"]
        if not (isinstance(v, str) and v):
            raise HTTPException(status_code=422, detail="capability must be non-empty string")
        fields["capability"] = v
    if "provider" in payload:
        v = payload["provider"]
        if not (isinstance(v, str) and v):
            raise HTTPException(status_code=422, detail="provider must be non-empty string")
        fields["provider"] = v
    if "model" in payload:
        v = payload["model"]
        if not (isinstance(v, str) and v):
            raise HTTPException(status_code=422, detail="model must be non-empty string")
        fields["model"] = v
    if "params_json" in payload:
        fields["params_json"] = _normalize_params(payload["params_json"])
    if "enabled" in payload:
        v = payload["enabled"]
        if v not in (0, 1, True, False):
            raise HTTPException(status_code=422, detail="enabled must be 0 or 1")
        fields["enabled"] = 1 if v in (1, True) else 0

    if not fields:
        # 无字段 → 直接返回当前行
        row = _get_config(settings.db_path, config_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"model_config {config_id!r} not found")
        return row

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [config_id]
    conn = get_connection(settings.db_path)
    try:
        cur = conn.execute(
            f"UPDATE model_configs SET {set_clause} WHERE config_id = ?", values
        )
        if cur.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail=f"model_config {config_id!r} not found")
        conn.commit()
    finally:
        conn.close()
    row = _get_config(settings.db_path, config_id)
    assert row is not None
    return row


@router.delete("/model-configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_config(config_id: str, request: Request):
    settings = request.app.state.settings
    conn = get_connection(settings.db_path)
    try:
        cur = conn.execute("DELETE FROM model_configs WHERE config_id = ?", (config_id,))
        conn.commit()
    finally:
        conn.close()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"model_config {config_id!r} not found")
    return None


@router.post("/model-configs/{config_id}/test", status_code=status.HTTP_200_OK)
def test_model_config(config_id: str, request: Request) -> dict:
    """ping：用该配置做一次轻量级健康检查，返回 ``{ok, latency_ms, detail, status_code?}``。

    行为契约（Sprint 8）：
    - ``mock`` provider → 直接 ``ok=True``（不发起真实调用，与既有 mock 语义一致）。
    - 其它 provider → 调 :meth:`Provider.health_check`：OpenAI 兼容 ``GET /models``、
      Ollama ``GET /api/tags``、Anthropic 极小 ``POST /v1/messages``（401/200 都算通）。
    - 健康检查失败 → 200 但 ``ok=False``（前端可区分「端点不可达」与「鉴权失败」）；
      仅 ProviderError 透传到 502 兜底保留。
    - ``enabled=0`` 仍返回 422（P2-5 业务规则）。
    """
    settings = request.app.state.settings
    config_row = _get_config(settings.db_path, config_id)
    if config_row is None:
        raise HTTPException(status_code=404, detail=f"model_config {config_id!r} not found")
    if config_row.get("enabled") == 0:
        raise HTTPException(
            status_code=422,
            detail=f"model_config {config_id!r} is disabled (enabled=0); cannot /test",
        )

    provider = ModelRouter(settings.db_path).get_provider(config_row)
    # mock provider 走 health_check（恒 ok），与既有 mock 语义一致；
    # 其它 provider 也优先走 health_check，避免向真模型发完整 prompt。
    try:
        result = provider.health_check()
    except ProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "provider_error", "message": str(exc), "status_code": exc.status_code},
        ) from exc
    return {
        "config_id": config_id,
        "ok": bool(result.get("ok")),
        "latency_ms": int(result.get("latency_ms") or 0),
        "detail": result.get("detail") or "",
        "status_code": result.get("status_code"),
    }


__all__ = ["router"]
