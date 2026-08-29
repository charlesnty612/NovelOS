"""Model Profiles REST 路由（V3.7「模型档案 + 环节绑定」）。

挂在 ``/api`` 前缀下。对齐 ``model_profiles`` 表（``database/migrations/0016_model_profiles.sql``）。

端点：
- ``POST /model-profiles`` ——创建一条档案（201）。
- ``GET /model-profiles`` ——列出所有档案（``include_enabled_only`` 可选过滤）。
- ``GET /model-profiles/{id}`` ——按主键取单条（404 不存在）。
- ``PATCH /model-profiles/{id}`` ——部分更新（name / provider / model / params / enabled）。
- ``DELETE /model-profiles/{id}`` ——删除（204）；若被任何 binding 引用 → 409。
- ``POST /model-profiles/{id}/test`` ——ping：发「回复 ok」，返回延迟（ms）与首 100 字。

设计要点：
- 与 :mod:`packages.core.api.routers.model_configs` 同构：所有 CRUD 调用
  :class:`packages.core.model_router.ProfileService`，路由只做参数校验 + 错误映射。
- ``params_json`` 字段接受 JSON 字符串或 dict（前端友好）；写入时统一 ``json.dumps``。
- 读路径脱敏 / 写路径 ``api_key`` 特殊值（mask / 空字符串 / 新值）一律复用
  :mod:`packages.core.model_router.security`，与 ``/model-configs`` 行为一致。
- ``DELETE`` 前的 binding 引用检查由 :meth:`ProfileService.list_bindings_referencing` 提供：
  有引用 → 409，``detail`` 列出引用 capability 清单，便于前端展示与一键解绑。
- ``/test`` 端点直接构造对应 Provider，发一条最小消息；mock 直接 ok；其它走
  ``Provider.health_check``（与 model_configs 同语义）。
- enabled=0 的行仍可返回（与 binding 配合：disabled 的 profile 自动从 binding 候选链跳过）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from packages.core.logging_config import get_logger
from packages.core.model_router import ModelRouter, ProfileService, ProviderError
from packages.core.model_router.security import (
    _dump_params_json,
    _mask_response,
    _normalize_params,
    _prepare_patch_params,
    _prepare_post_params,
)

log = get_logger("novelos.routers.model_profiles")

router = APIRouter(tags=["model_profiles"])


@router.post("/model-profiles", status_code=status.HTTP_201_CREATED)
def create_model_profile(payload: dict, request: Request) -> dict:
    """创建一条档案。必填字段：``name / provider / model``。

    入参 ``params_json.api_key`` 的特殊值与 ``/model-configs`` 同语义：
    - ``""`` 或 ``"***"`` → 视为未设置，落库时不写 api_key。
    - 其他字符串 → 原样落库（明文）。
    """
    name = payload.get("name")
    provider = payload.get("provider")
    model = payload.get("model")
    if not (isinstance(name, str) and name):
        raise HTTPException(status_code=422, detail="name required")
    if not (isinstance(provider, str) and provider):
        raise HTTPException(status_code=422, detail="provider required")
    if not (isinstance(model, str) and model):
        raise HTTPException(status_code=422, detail="model required")
    try:
        # 契约兼容：params_json（旧）优先；缺失则读 params（新契约，前端按约定发）。
        # 两者都无 → None → _normalize_params 返回空 dict（保留原行为）。
        raw_params = _normalize_params(
            payload.get("params_json", payload.get("params"))
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    params_dict = _prepare_post_params(raw_params)
    enabled = payload.get("enabled", 1)
    if enabled not in (0, 1, True, False):
        raise HTTPException(status_code=422, detail="enabled must be 0 or 1")
    enabled_int = 1 if enabled in (1, True) else 0

    settings = request.app.state.settings
    svc = ProfileService(settings.db_path)
    try:
        row = svc.create(
            name=name,
            provider=provider,
            model=model,
            params=params_dict,
            enabled=enabled_int,
        )
    except Exception as exc:
        from sqlite3 import IntegrityError

        if isinstance(exc, IntegrityError):
            raise HTTPException(status_code=422, detail=f"integrity error: {exc}") from exc
        raise
    return _mask_response(row)


@router.get("/model-profiles")
def list_model_profiles(
    request: Request,
    include_enabled_only: bool = False,
) -> list[dict]:
    settings = request.app.state.settings
    svc = ProfileService(settings.db_path)
    rows = svc.list(include_enabled_only=include_enabled_only)
    return [_mask_response(r) for r in rows]


@router.get("/model-profiles/{profile_id}")
def get_model_profile(profile_id: str, request: Request) -> dict:
    settings = request.app.state.settings
    svc = ProfileService(settings.db_path)
    row = svc.get(profile_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"model_profile {profile_id!r} not found")
    return _mask_response(row)


@router.patch("/model-profiles/{profile_id}")
def patch_model_profile(profile_id: str, payload: dict, request: Request) -> dict:
    """部分更新。

    入参 ``params_json.api_key`` 的特殊值与 ``/model-configs`` 同语义：
    - ``"***"`` → 保留 DB 原值；
    - 空字符串 → 清空；
    - 其它 → 用入参值覆盖。

    其余字段（name / provider / model / enabled）沿用既有非空校验。
    """
    settings = request.app.state.settings
    svc = ProfileService(settings.db_path)
    fields: dict = {}
    if "name" in payload:
        v = payload["name"]
        if not (isinstance(v, str) and v):
            raise HTTPException(status_code=422, detail="name must be non-empty string")
        fields["name"] = v
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
    if "params_json" in payload or "params" in payload:
        existing = svc.get(profile_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"model_profile {profile_id!r} not found")
        try:
            # 契约兼容：params_json 优先；缺失则读 params（新契约）。
            # 与 POST 对齐：两个键都视作「更新 params」的入口。
            raw_params = _normalize_params(
                payload.get("params_json", payload.get("params"))
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        merged = _prepare_patch_params(raw_params, existing.get("params_json") or "{}")
        fields["params_json"] = _dump_params_json(merged)
    if "enabled" in payload:
        v = payload["enabled"]
        if v not in (0, 1, True, False):
            raise HTTPException(status_code=422, detail="enabled must be 0 or 1")
        fields["enabled"] = 1 if v in (1, True) else 0

    if not fields:
        row = svc.get(profile_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"model_profile {profile_id!r} not found")
        return _mask_response(row)

    row = svc.update_partial(profile_id, fields)
    if row is None:
        raise HTTPException(status_code=404, detail=f"model_profile {profile_id!r} not found")
    return _mask_response(row)


@router.delete("/model-profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_profile(profile_id: str, request: Request):
    settings = request.app.state.settings
    svc = ProfileService(settings.db_path)
    refs = svc.list_bindings_referencing(profile_id)
    if refs:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "profile_in_use",
                "message": f"profile {profile_id!r} 被 {len(refs)} 个 binding 引用",
                "referenced_by": refs,
            },
        )
    ok = svc.delete(profile_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"model_profile {profile_id!r} not found")
    return None


@router.post("/model-profiles/{profile_id}/test", status_code=status.HTTP_200_OK)
def test_model_profile(profile_id: str, request: Request) -> dict:
    """ping：用该档案做一次轻量级健康检查。

    行为契约与 ``/model-configs/{id}/test`` 一致：
    - ``mock`` provider → 直接 ``ok=True``；
    - 其它 provider → 调 :meth:`Provider.health_check`；
    - 健康检查失败 → 200 + ``ok=False``（前端可区分「端点不可达」与「鉴权失败」）；
    - 仅 ProviderError 透传到 502。
    - ``enabled=0`` → 422。
    """
    settings = request.app.state.settings
    svc = ProfileService(settings.db_path)
    profile_row = svc.get(profile_id)
    if profile_row is None:
        raise HTTPException(status_code=404, detail=f"model_profile {profile_id!r} not found")
    if profile_row.get("enabled") == 0:
        raise HTTPException(
            status_code=422,
            detail=f"model_profile {profile_id!r} is disabled (enabled=0); cannot /test",
        )

    # 构造与 model_configs 行同键名的 dict，复用 ModelRouter.get_provider。
    config_row = {
        "config_id": profile_row["profile_id"],
        "capability": "creative_writing",  # 仅用于 provider 构造，无业务影响
        "provider": profile_row["provider"],
        "model": profile_row["model"],
        "params_json": profile_row["params_json"],
        "enabled": profile_row["enabled"],
    }
    provider = ModelRouter(settings.db_path).get_provider(config_row)
    try:
        result = provider.health_check()
    except ProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": "provider_error", "message": str(exc), "status_code": exc.status_code},
        ) from exc
    return {
        "profile_id": profile_id,
        "ok": bool(result.get("ok")),
        "latency_ms": int(result.get("latency_ms") or 0),
        "detail": result.get("detail") or "",
        "status_code": result.get("status_code"),
    }


__all__ = ["router"]
