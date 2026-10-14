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

import json
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status

from packages.core.logging_config import get_logger
from packages.core.model_router import ModelRouter, ProfileService, ProviderError
from packages.core.model_router.providers import (
    AnthropicProvider,
    OllamaProvider,
    resolve_api_key,
)
from packages.core.model_router.security import (
    _dump_params_json,
    _mask_response,
    _normalize_params,
    _prepare_patch_params,
    _prepare_post_params,
)

log = get_logger("novelos.routers.model_profiles")

# ---------------------------------------------------------------------------
# V3.8（拉取可用模型）内部常量
# ---------------------------------------------------------------------------

# 拉取模型列表总超时（秒）。各家列表接口通常很快，但需兜底防挂起。
_FETCH_LIST_TIMEOUT_S = 10.0


def _extract_ids_openai_compatible(data: object) -> list[str]:
    """OpenAI 兼容 /models 响应：``{"data": [{"id": "..."}, ...]}``。"""
    if not isinstance(data, dict):
        return []
    items = data.get("data")
    if not isinstance(items, list):
        return []
    ids: list[str] = []
    for it in items:
        if isinstance(it, dict):
            v = it.get("id")
            if isinstance(v, str) and v:
                ids.append(v)
    return ids


def _extract_ids_anthropic(data: object) -> list[str]:
    """Anthropic /v1/models 响应：``{"data": [{"id": "..."}, ...]}``。"""
    return _extract_ids_openai_compatible(data)


def _extract_ids_ollama(data: object) -> list[str]:
    """Ollama /api/tags 响应：``{"models": [{"name": "..."}, ...]}``。"""
    if not isinstance(data, dict):
        return []
    items = data.get("models")
    if not isinstance(items, list):
        return []
    ids: list[str] = []
    for it in items:
        if isinstance(it, dict):
            v = it.get("name")
            if isinstance(v, str) and v:
                ids.append(v)
    return ids


def _dedup_sorted(ids: list[str]) -> list[str]:
    """排序去重（保持稳定插入序以稳定输出，但用户体验上看到有序列表更直接）。"""
    return sorted({i for i in ids if isinstance(i, str) and i})


router = APIRouter(tags=["model_profiles"])


# ---------------------------------------------------------------------------
# 写入侧形状校验 + unknown-model 提示（task-28）
# ---------------------------------------------------------------------------


def _clean_model_field(raw: Any, *, field: str, logger: Any = None) -> str:
    """``model`` 字段：strip 后非空校验，**拒绝纯空白**。

    原判断是 ``isinstance(model, str) and model``——纯空白（``"   "``）是 truthy
    字符串，会被直接放行落库，到调用时远端才以 unknown model 400 收场，而那个
    400 在本文件被泛化成了「provider_error」。写入侧先把形状问题拦下。

    形状校验**只管「非空且非纯空白」**：不校验该模型名是否真实存在——可用模型随
    部署 / 账号 / 上下线状态变化，本地没有权威名表，硬编码白名单必然过期
    （AGENTS.md「可调项不得硬编码」）。名字对错由 ``/test`` 的远端响应判定。

    含前后空白 → 归一化落库并 ``log.warning`` 留痕：原值被静默改写，排查时需要
    能看出是谁写了带空格的模型名。

    ``logger``：``/model-configs`` 复用本函数但有自己的 logger（两个 router 各带
    自己的 ``novelos.routers.*`` 命名空间），由调用方传入以免 warning 打到邻居。
    共享 helper 的最终归属应是 :mod:`packages.core.model_router.security`（两个
    router 已共用其中的 ``_normalize_params`` 等），本批未扩白名单故先跨 router 引用。
    """
    warn = logger if logger is not None else log
    if not isinstance(raw, str):
        raise HTTPException(status_code=422, detail=f"{field} must be a non-empty string")
    stripped = raw.strip()
    if not stripped:
        raise HTTPException(
            status_code=422,
            detail=f"{field} 不能为纯空白（strip 后为空）；请填写真实的模型名",
        )
    if stripped != raw:
        warn.warning("%s 含前后空白，已 strip 归一化：%r -> %r", field, raw, stripped)
    return stripped


# 远端 400 错误文本里「模型名问题」的公共形态（各 provider 措辞不同，取交集式关键词）。
# 这是**错误语义**匹配，不是模型名清单——可用模型随部署变化，本地不建名表。
_UNKNOWN_MODEL_MARKERS = (
    "unknown model",
    "model_not_found",
    "model not found",
    "invalid model",
    "no such model",
    "unsupported model",
    "model_not_supported",
    "does not exist",
)


def _unknown_model_hint(detail: str) -> str | None:
    """远端 400 命中「模型名问题」时给中文提示；不命中返回 ``None``（正常透传）。

    仅在 400 上生效：400 是参数类错误，401/403/5xx 各自含义不同（鉴权 / 限流 /
    服务端故障），把它们一并归到「模型名」是误诊——那才是真正的提示噪声。
    """
    low = (detail or "").lower()
    if not any(marker in low for marker in _UNKNOWN_MODEL_MARKERS):
        return None
    return (
        "模型名问题：provider 不认识这个模型名（拼写错误 / 已下线 / 当前 API key "
        "无权访问）。请核对 model 字段，或先用 available-models 拉取该 key "
        "实际可用的模型列表。"
    )


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
    model = _clean_model_field(model, field="model")
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
        fields["model"] = _clean_model_field(payload["model"], field="model")
    if "params_json" in payload or "params" in payload:
        if "params_json" in payload and "params" in payload:
            raise HTTPException(
                status_code=422,
                detail="params 与 params_json 只能传其一",
            )
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
    status_code = result.get("status_code")
    detail = result.get("detail") or ""
    payload = {
        "profile_id": profile_id,
        "ok": bool(result.get("ok")),
        "latency_ms": int(result.get("latency_ms") or 0),
        "detail": detail,
        "status_code": status_code,
    }
    # 400 且错误文本指向模型名 → 追加中文提示。这是**新增可选字段**：既有 5 个字段
    # 逐字不变，不命中时响应体与本批之前完全一致（正常错误继续原样透传）。
    if status_code == 400:
        hint = _unknown_model_hint(detail)
        if hint is not None:
            payload["hint"] = hint
    return payload


# ---------------------------------------------------------------------------
# V3.8 拉取可选模型列表（POST /model-profiles/available-models）
# ---------------------------------------------------------------------------


@router.post("/model-profiles/available-models")
def list_available_models(payload: dict) -> dict:
    """按 ``provider / base_url / api_key / profile_id`` 调用各家模型列表接口。

    用途：模型档案编辑表单「拉取模型」按钮的支撑端点。后端代理请求以
    避免浏览器跨域、CORS 与密文外带；前端只看到列表不接触 key。

    请求体（所有键均可选除 ``provider``）：
    - ``provider`` (str, 必填) —— ``mock / openai_compatible / anthropic / ollama`` 等。
    - ``base_url`` (str, 可选) —— 若空，按 provider 选用官方默认；OpenAI 兼容 / 自建
      端点必须提供。
    - ``api_key`` (str, 可选) —— 明文直接用；缺省走 ``resolve_api_key``：先查档案
      ``profile_id``、再 ``provider`` 默认 env（``NOVELOS_API_KEY_<PROVIDER>``）。
    - ``profile_id`` (str, 可选) —— 已有档案 ID；用于上面提到的解析优先级。

    错误处理：
    - ``400`` —— 必填缺失 / 该 provider 必须有 key 但解析不到；detail 仅文案不含 key。
    - ``502`` —— 拉取失败（超时、非 2xx、解析坏 JSON）。detail 仅文案不含 key。

    响应：
    - ``{"models": ["id1", "id2", ...]}`` —— 排序去重后的字符串数组。
    """
    provider = payload.get("provider")
    if not (isinstance(provider, str) and provider):
        raise HTTPException(status_code=422, detail="provider required")

    raw_base_url = payload.get("base_url")
    base_url: str | None = None
    if isinstance(raw_base_url, str) and raw_base_url.strip():
        base_url = raw_base_url.strip()

    raw_api_key = payload.get("api_key")
    inline_api_key: str | None = None
    if isinstance(raw_api_key, str) and raw_api_key.strip():
        inline_api_key = raw_api_key.strip()
        # 安全：不接受脱敏占位符作为「真密钥」。
        if inline_api_key == "***":
            inline_api_key = None

    raw_profile_id = payload.get("profile_id")
    profile_id: str | None = None
    if isinstance(raw_profile_id, str) and raw_profile_id.strip():
        profile_id = raw_profile_id.strip()

    # ---- mock：固定假列表，无需任何外部调用 ----
    if provider == "mock":
        return {"models": ["mock-model"]}

    # ---- ollama：无需 key；缺 base_url 用本地默认 ----
    if provider == "ollama":
        target_base = base_url or OllamaProvider.DEFAULT_BASE_URL
        url = f"{target_base.rstrip('/')}/api/tags"
        try:
            resp = httpx.get(url, timeout=_FETCH_LIST_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"拉取模型列表失败：无法连接 Ollama（{type(exc).__name__}）",
            ) from exc
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"拉取模型列表失败：上游 HTTP {resp.status_code}",
            )
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"拉取模型列表失败：响应非 JSON（{type(exc).__name__}）",
            ) from exc
        return {"models": _dedup_sorted(_extract_ids_ollama(data))}

    # ---- 解析 API key（优先入参，再走 secrets / env） ----
    api_key: str | None = inline_api_key
    if api_key is None:
        api_key = resolve_api_key(
            provider,
            None,
            profile_id=profile_id,
        )

    # ---- anthropic：必须 key（/v1/models 走鉴权） ----
    if provider == "anthropic":
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="拉取模型列表失败：需先配置 Anthropic 密钥",
            )
        target_base = base_url or AnthropicProvider.DEFAULT_BASE_URL
        url = f"{target_base.rstrip('/')}/v1/models"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": AnthropicProvider.ANTHROPIC_VERSION,
        }
        try:
            resp = httpx.get(url, headers=headers, timeout=_FETCH_LIST_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"拉取模型列表失败：无法连接 Anthropic（{type(exc).__name__}）",
            ) from exc
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"拉取模型列表失败：上游 HTTP {resp.status_code}",
            )
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"拉取模型列表失败：响应非 JSON（{type(exc).__name__}）",
            ) from exc
        return {"models": _dedup_sorted(_extract_ids_anthropic(data))}

    # ---- openai_compatible / 其他：必须 base_url；key 可选（本地 / Ollama 等） ----
    if not base_url:
        raise HTTPException(
            status_code=400,
            detail=f"拉取模型列表失败：{provider!r} 需提供 base_url",
        )
    url = f"{base_url.rstrip('/')}/models"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = httpx.get(url, headers=headers, timeout=_FETCH_LIST_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"拉取模型列表失败：无法连接（{type(exc).__name__}）",
        ) from exc
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"拉取模型列表失败：上游 HTTP {resp.status_code}",
        )
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"拉取模型列表失败：响应非 JSON（{type(exc).__name__}）",
        ) from exc
    return {"models": _dedup_sorted(_extract_ids_openai_compatible(data))}


__all__ = ["router"]
