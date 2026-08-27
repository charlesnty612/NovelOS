"""Capability Bindings REST 路由（V3.7「模型档案 + 环节绑定」）。

挂在 ``/api`` 前缀下。对齐 ``capability_bindings`` 表（``database/migrations/0016_model_profiles.sql``）。

端点：
- ``GET /capability-bindings`` ——列出全部 binding（含未绑定环节），每项含 ``label`` /
  ``agents`` / ``profile_ids`` / ``profiles`` / ``legacy_available`` / ``updated_at``。
- ``PUT /capability-bindings/{capability}`` ——upsert：body ``{profile_ids: [...]}``，
  至少 1 个、须都存在且 enabled=1；未知 capability → 404；profile 不存在或禁用 → 422。
- ``DELETE /capability-bindings/{capability}`` ——解除 binding（回落旧 model_configs 行为）。

设计要点：
- :class:`packages.core.model_router.BindingService` 负责 DB CRUD + 校验；
  路由仅做 HTTP 错误映射。
- 响应不暴露 api_key（profiles 详情里只取 ``profile_id`` / ``name`` / ``model`` / ``provider``，
  不附带 ``params_json``；前端如需密钥状态走 ``/model-profiles/{id}`` 读路径走脱敏）。
- 旧 /model-configs 端点保留不动（兼容期）；``legacy_available`` 字段让前端引导用户迁移。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from packages.core.logging_config import get_logger
from packages.core.model_router import BindingService
from packages.core.model_router.bindings import (
    InvalidBindingError,
    UnknownCapabilityError,
)

log = get_logger("novelos.routers.capability_bindings")

router = APIRouter(tags=["capability_bindings"])


@router.get("/capability-bindings")
def list_capability_bindings(request: Request) -> list[dict]:
    settings = request.app.state.settings
    svc = BindingService(settings.db_path)
    return svc.list_with_profiles()


@router.put("/capability-bindings/{capability}")
def upsert_capability_binding(capability: str, payload: dict, request: Request) -> dict:
    """upsert 一个 binding。body: ``{profile_ids: [...]}``。

    - ``profile_ids`` 非空；
    - 所有 profile_id 必须存在且 ``enabled=1``（失效的 profile 不允许出现在 binding 里——
      否则 candidate 链会跳过它，违反「binding 显式声明」的语义）；
    - 未知 capability → 404；
    - 校验失败 → 422（detail 含具体原因）。
    """
    profile_ids = payload.get("profile_ids")
    if not isinstance(profile_ids, list) or not profile_ids:
        raise HTTPException(status_code=422, detail="profile_ids must be a non-empty list")
    if not all(isinstance(x, str) and x for x in profile_ids):
        raise HTTPException(status_code=422, detail="profile_ids must be list of strings")
    # 去重保持顺序
    seen: set[str] = set()
    deduped: list[str] = []
    for pid in profile_ids:
        if pid not in seen:
            seen.add(pid)
            deduped.append(pid)

    settings = request.app.state.settings
    svc = BindingService(settings.db_path)
    try:
        binding = svc.upsert(capability, deduped)
    except UnknownCapabilityError as exc:
        raise HTTPException(
            status_code=404, detail=f"capability {exc.args[0]!r} not found"
        ) from exc
    except InvalidBindingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # 响应格式与 GET 单项一致（capability/label/agents/profile_ids/profiles/legacy_available）
    listed = svc.list_with_profiles()
    for item in listed:
        if item["capability"] == capability:
            return item
    raise HTTPException(status_code=500, detail="binding upserted but not found in list")


@router.delete("/capability-bindings/{capability}")
def delete_capability_binding(capability: str, request: Request) -> dict:
    """解除 binding；不存在 → 404。"""
    settings = request.app.state.settings
    svc = BindingService(settings.db_path)
    ok = svc.delete(capability)
    if not ok:
        raise HTTPException(
            status_code=404, detail=f"capability_binding {capability!r} not found"
        )
    return {"capability": capability, "deleted": True}


__all__ = ["router"]
