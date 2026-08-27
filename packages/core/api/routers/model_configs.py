"""Model Configs REST 路由（Sprint 3 + V1.5 架构整理）。

挂在 ``/api`` 前缀下。对齐 ``model_configs`` 表（``database/migrations/0001_init.sql`` line 464-471）。

端点：
- ``POST /model-configs`` ——创建一条 model config（201）。
- ``GET /model-configs`` ——列出所有 configs（可按 ``capability`` / ``provider`` 过滤）。
- ``GET /model-configs/{id}`` ——按主键取单条（404 不存在）。
- ``PATCH /model-configs/{id}`` ——部分更新（capability / provider / model / params / enabled）。
- ``DELETE /model-configs/{id}`` ——删除（204）。
- ``POST /model-configs/{id}/test`` ——ping：发「回复 ok」，返回延迟（ms）与首 100 字。

设计要点：
- V1.5 起，路由层不再直接写 SQL——所有 CRUD 调用 ``ModelConfigService``（位于
  :mod:`packages.core.model_router.configs`），路由只做参数校验 + 错误映射。
- ``params_json`` 字段接受 JSON 字符串或 dict（前端友好）；写入时统一 ``json.dumps``。
- ``/test`` 端点直接构造对应 Provider，发一条最小消息；真实外网调用不要测（任务书边界）。
  失败抛 ``ProviderError``（→ 502）——与 ``agents.py`` 一致。
- enabled=0 的行不返回 / 不能用于路由（与 :class:`ModelRouter.resolve` 语义一致）。

P1-1 密钥脱敏（Sprint 12）：
- 读路径（GET 列表 / GET 详情 / POST 创建 / PATCH 更新 的响应）一律过
  :func:`_mask_response`：把 ``params_json.api_key`` 替换为 ``_MASK``，并在响应顶层追加
  ``has_api_key`` 布尔字段，供前端判断「已配置 / 未配置」而不再读取明文。
- 写路径（POST / PATCH 的入参）处理 ``api_key`` 的两种特殊值：
  - ``"***"``（即 :data:`_MASK`）→ 视为「未改动」，PATCH 时复用 DB 原值；POST 视为未设置。
  - 空字符串 ``""`` → POST 视为未设置；PATCH 视为清空。
- 明文 api_key 仅在 POST 创建 / PATCH 显式新值时短暂出现在请求体与进程内存里，
  不会通过任何 GET 响应回写到前端。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.logging_config import get_logger
from packages.core.model_router import ModelConfigService, ModelRouter, ProviderError
from packages.core.model_router.security import (
    _MASK,
    _dump_params_json,
    _mask_response,
    _normalize_params,
    _prepare_patch_params,
    _prepare_post_params,
)

log = get_logger("novelos.routers.model_configs")

router = APIRouter(tags=["model_configs"])


@router.post("/model-configs", status_code=status.HTTP_201_CREATED)
def create_model_config(payload: dict, request: Request) -> dict:
    """创建一条 model config。必填字段：``capability / provider / model``。

    入参 ``params_json.api_key`` 的特殊值：
    - ``""`` 或 ``"***"``（:data:`_MASK`）→ 视为未设置，落库时不写入 api_key。
    - 其他字符串 → 原样落库（明文）。
    """
    capability = payload.get("capability")
    provider = payload.get("provider")
    model = payload.get("model")
    if not (isinstance(capability, str) and capability):
        raise HTTPException(status_code=422, detail="capability required")
    if not (isinstance(provider, str) and provider):
        raise HTTPException(status_code=422, detail="provider required")
    if not (isinstance(model, str) and model):
        raise HTTPException(status_code=422, detail="model required")
    try:
        raw_params = _normalize_params(payload.get("params_json"))
    except ValueError as exc:
        # security._normalize_params 在非法 JSON / 非对象时抛 ValueError；
        # 转 422 与 model_profiles.py 对齐（详见 E3 review 修复）。
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    params_dict = _prepare_post_params(raw_params)
    enabled = payload.get("enabled", 1)
    if enabled not in (0, 1, True, False):
        raise HTTPException(status_code=422, detail="enabled must be 0 or 1")
    enabled_int = 1 if enabled in (1, True) else 0

    settings = request.app.state.settings
    svc = ModelConfigService(settings.db_path)
    try:
        row = svc.create(
            capability=capability,
            provider=provider,
            model=model,
            params=params_dict,
            enabled=enabled_int,
        )
    except Exception as exc:
        # FK / 唯一性约束等：转 422（与既有路由契约一致）
        from sqlite3 import IntegrityError

        if isinstance(exc, IntegrityError):
            raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc
        raise
    return _mask_response(row)


@router.get("/model-configs")
def list_model_configs(
    request: Request,
    capability: str | None = None,
    provider: str | None = None,
) -> list[dict]:
    settings = request.app.state.settings
    svc = ModelConfigService(settings.db_path)
    rows = svc.list(capability=capability, provider=provider)
    return [_mask_response(r) for r in rows]


@router.get("/model-configs/{config_id}")
def get_model_config(config_id: str, request: Request) -> dict:
    settings = request.app.state.settings
    svc = ModelConfigService(settings.db_path)
    row = svc.get(config_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"model_config {config_id!r} not found")
    return _mask_response(row)


@router.patch("/model-configs/{config_id}")
def patch_model_config(config_id: str, payload: dict, request: Request) -> dict:
    """部分更新。

    入参 ``params_json.api_key`` 的特殊值：
    - ``"***"``（:data:`_MASK`）→ 视为「保留 DB 原值」，不动 api_key；
    - 空字符串 → 视为「清空 api_key」（删除该键）；
    - 其他 → 用入参值覆盖。

    其余字段（capability / provider / model / enabled）沿用既有非空校验。
    """
    settings = request.app.state.settings
    svc = ModelConfigService(settings.db_path)
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
        # 合并入参与 DB 原值，按 api_key 特殊语义处理
        existing = svc.get(config_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"model_config {config_id!r} not found")
        try:
            raw_params = _normalize_params(payload["params_json"])
        except ValueError as exc:
            # security._normalize_params 在非法 JSON / 非对象时抛 ValueError；
            # 转 422 与 model_profiles.py 对齐（详见 E3 review 修复）。
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        merged = _prepare_patch_params(raw_params, existing.get("params_json") or "{}")
        fields["params_json"] = _dump_params_json(merged)
    if "enabled" in payload:
        v = payload["enabled"]
        if v not in (0, 1, True, False):
            raise HTTPException(status_code=422, detail="enabled must be 0 or 1")
        fields["enabled"] = 1 if v in (1, True) else 0

    if not fields:
        # 无字段 → 直接返回当前行
        row = svc.get(config_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"model_config {config_id!r} not found")
        return _mask_response(row)

    row = svc.update_partial(config_id, fields)
    if row is None:
        raise HTTPException(status_code=404, detail=f"model_config {config_id!r} not found")
    return _mask_response(row)


@router.delete("/model-configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_config(config_id: str, request: Request):
    settings = request.app.state.settings
    svc = ModelConfigService(settings.db_path)
    ok = svc.delete(config_id)
    if not ok:
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
    svc = ModelConfigService(settings.db_path)
    config_row = svc.get(config_id)
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
