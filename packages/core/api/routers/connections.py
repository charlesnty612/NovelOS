"""Model Connections REST 路由（2026-10-21 批次十五「连接层」）。

挂在 ``/api`` 前缀下。对齐 ``model_connections`` 表
（``database/migrations/0033_model_connections.sql``）。

端点：
- ``POST /model-connections`` ——创建一条连接（201）。
- ``GET /model-connections`` ——列出所有连接。
- ``GET /model-connections/{id}`` ——按主键取单条（404 不存在）。
- ``PATCH /model-connections/{id}`` ——部分更新。
- ``DELETE /model-connections/{id}`` ——删除（204）；若被任何档案引用 → 409。
- ``POST /model-connections/{id}/test`` ——探测可达性。

**为什么要有连接层**（用户裁决 2026-10-21）：
「同一厂商，想分配不同模型承担不同职责，需要要重新完全新建。」
⇒ 同一厂商的多个档案**共享一条连接**：改一次 key / base_url / headers 全局生效。

设计要点：
- 与 :mod:`packages.core.api.routers.model_profiles` 同构：所有 CRUD 调用
  :class:`packages.core.model_router.connections.ConnectionService`，
  路由只做参数校验 + 错误映射。
- **连接级只允许三个参数**：``api_key`` / ``headers`` / ``models_url``。
  其它键 → **422**（不静默忽略——「收下即忘」与「表里有值」是同一级缺陷）。
  ⚠️ ``timeout_s`` 归**档案**级（不同模型响应速度不同，允许不同 timeout）。
- **协议地址池**（2026-10-24，协议下放模型档案）：可选 ``protocol_base_urls``
  （协议 → 该网关此协议的 base_url）。键必须是 :data:`PROTOCOL_IDS` 四协议之一、
  值非空 URL，违规 **422**；显式 ``null`` = 清空（全部走 base_url）。
  连接的 ``provider`` 列是**主协议**（给无覆盖场景与旧档案回退用），
  不再向档案同步协议（原 ``sync_provider_to_profiles`` 已随本裁决删除）。
- **鉴权头撞车 → 422**：连接既有 ``api_key`` 又在 ``headers`` 里写鉴权头
  （authorization / x-api-key / …）时二选一——否则自定义头会**静默架空**密钥通道，
  而 ``has_api_key`` 仍显示 true（显示与实际失真）。
- ``headers`` 的形状在**写时**校验（合法 HTTP token 名、值不含控制字符、
  占位符在封闭集合内）——httpx 遇非法头值会抛难懂的 TypeError，
  ``\\r\\n`` 更是请求拆分风险。
- 读路径脱敏复用 :mod:`packages.core.model_router.security`：
  ``api_key`` → ``***``，``headers`` 里的**敏感头逐键**脱敏
  （``x-opencode-session`` 这类非凭据头**不掩**——用户要能看到模板对不对）。
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status

from packages.core.logging_config import get_logger
from packages.core.model_router.connections import ConnectionService
from packages.core.model_router.effective import (
    CONNECTION_ONLY_PARAMS,
    PROTOCOL_IDS,
    SENSITIVE_HEADER_NAMES,
    parse_params,
    validate_headers,
)
from packages.core.model_router.models import (
    ConnectionPresetOut,
    ConnectionTestResult,
    ModelConnectionOut,
)
from packages.core.model_router.presets import CONNECTION_PRESETS
from packages.core.model_router.security import (
    _mask_params,
    _normalize_params,
    _prepare_patch_params,
    _prepare_post_params,
)

log = get_logger("novelos.routers.connections")

router = APIRouter()

# 探测模型列表端点的总超时（秒）
_PROBE_TIMEOUT_S = 10.0


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _mask_connection(row: dict[str, Any]) -> dict[str, Any]:
    """连接行的读路径出口：``api_key`` → ``***``、``headers`` 敏感头逐键脱敏、
    ``protocol_base_urls`` JSON 字符串反序列化为对象。

    与档案侧的 :func:`packages.core.model_router.security._mask_response` 同构，
    但 ``has_api_key`` 的判定**按 connection_id 查 secrets.json**
    （档案侧按 profile_id 查 —— 两者的身份不同，不能混用）。
    """
    params = parse_params(row.get("params_json"))
    masked, has_key = _mask_params(params)
    if not has_key:
        has_key = _file_has_key_for_connection(row)
    out = dict(row)
    out["params_json"] = masked
    out["has_api_key"] = has_key
    # 协议地址池：DB 里是 JSON 字符串（或 NULL）⇒ 出参统一为 dict | null
    # （脏 JSON 按 parse_params 容错为 {} ⇒ 出参 null，与「无覆盖」同语义）
    out["protocol_base_urls"] = parse_params(row.get("protocol_base_urls")) or None
    return out


def _file_has_key_for_connection(row: dict[str, Any]) -> bool:
    """``secrets.json`` 里是否有该连接（或其 provider）的 key。"""
    try:
        from packages.core.secrets_store import load_api_keys
    except Exception:  # pragma: no cover - 极端 import 失败
        return False
    secrets = load_api_keys()
    if not secrets:
        return False
    connection_id = row.get("connection_id")
    if isinstance(connection_id, str) and secrets.get(connection_id):
        return True
    provider = row.get("provider")
    if isinstance(provider, str) and provider:
        if secrets.get(provider) or secrets.get(provider.lower()):
            return True
    return False


def _validate_connection_params(params: dict[str, Any]) -> None:
    """写时校验连接级 ``params``；不合规抛 422（由调用方转 HTTPException）。

    三条：
    1. **键白名单** —— 只允许 ``api_key`` / ``headers`` / ``models_url``；
    2. **headers 形状** —— 见 :func:`validate_headers`；
    3. **鉴权头撞车** —— 既有 ``api_key`` 又在 headers 里写鉴权头 ⇒ 二选一。
    """
    extra = sorted(set(params) - CONNECTION_ONLY_PARAMS)
    if extra:
        raise HTTPException(
            status_code=422,
            detail=(
                f"连接级 params 不支持这些键：{extra}；"
                "只允许 api_key / headers / models_url"
                "（timeout_s 等调参归档案级）"
            ),
        )
    header_errors = validate_headers(params.get("headers"))
    if header_errors:
        raise HTTPException(
            status_code=422, detail="；".join(header_errors)
        )
    api_key = params.get("api_key")
    if isinstance(api_key, str) and api_key and api_key != "***":
        headers = params.get("headers")
        if isinstance(headers, dict):
            clash = sorted(
                k
                for k in headers
                if isinstance(k, str) and k.strip().lower() in SENSITIVE_HEADER_NAMES
            )
            if clash:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"鉴权头 {clash} 与连接级 api_key 冲突，二选一即可"
                        "（同时配会让自定义头静默架空密钥通道，而「已配置密钥」仍显示为真）"
                    ),
                )


def _validate_protocol_base_urls(value: Any) -> dict[str, str]:
    """校验 ``protocol_base_urls``（协议地址池）形状；不合规抛 422。

    三条：
    1. 必须是对象（协议 → base_url）；
    2. 键必须是 :data:`PROTOCOL_IDS` 四协议之一（与 router 工厂四路 dispatch 对齐；
       未知协议的地址永远不会被解析消费——「收下即忘」同级缺陷，写时拦）；
    3. 值必须是非空字符串（strip 后归一化；空串 = 没配，不允许混进表里）。
    """
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=422,
            detail="protocol_base_urls 必须是对象（协议 → base_url）",
        )
    cleaned: dict[str, str] = {}
    for k, v in value.items():
        if not isinstance(k, str) or k not in PROTOCOL_IDS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"protocol_base_urls 的键必须是协议名"
                    f"（{sorted(PROTOCOL_IDS)}），收到 {k!r}"
                ),
            )
        if not isinstance(v, str) or not v.strip():
            raise HTTPException(
                status_code=422,
                detail=f"protocol_base_urls[{k!r}] 必须是非空 URL 字符串",
            )
        cleaned[k] = v.strip()
    return cleaned


# ---------------------------------------------------------------------------
# 预设清单
# ---------------------------------------------------------------------------


@router.get("/model-connections/presets", response_model=list[ConnectionPresetOut])
def list_connection_presets():
    """列出连接预设（**一键填表**：provider / base_url / protocol_base_urls /
    headers / models_url）。

    ⚠️ **必须声明在 ``/model-connections/{connection_id}`` 之前**——否则
    FastAPI 会把 ``presets`` 当成 ``connection_id`` 匹配掉（路由按声明顺序）。

    权威清单在 :mod:`packages.core.model_router.presets`（**只此一处**）⇒
    前端只渲染、不硬编码任何 URL（避免同一组地址在前后端各写一份）。
    """
    return [
        {
            "preset_id": p["preset_id"],
            "label": p["label"],
            "provider": p["provider"],
            "base_url": p["base_url"],
            "protocol_base_urls": p.get("protocol_base_urls"),
            "params": p["params"],
            "note": p.get("note"),
        }
        for p in CONNECTION_PRESETS
    ]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/model-connections",
    status_code=status.HTTP_201_CREATED,
    response_model=ModelConnectionOut,
)
def create_model_connection(payload: dict, request: Request) -> dict:
    """创建一条连接。必填：``name / provider``。

    ``openai_compatible`` 的 ``base_url`` 必填（与档案同口径）——
    该 provider 没有可用的默认端点。
    可选 ``protocol_base_urls``：协议地址池（协议 → 该网关此协议的 base_url）；
    多协议网关（如 opencode Go）用它让同一条连接服务不同协议的模型档案。
    """
    name = payload.get("name")
    provider = payload.get("provider")
    if not (isinstance(name, str) and name.strip()):
        raise HTTPException(status_code=422, detail="name required")
    if not (isinstance(provider, str) and provider.strip()):
        raise HTTPException(status_code=422, detail="provider required")
    provider = provider.strip()

    base_url = payload.get("base_url")
    if base_url is not None:
        if not isinstance(base_url, str):
            raise HTTPException(status_code=422, detail="base_url must be a string")
        base_url = base_url.strip() or None
    if provider not in ("anthropic", "ollama", "mock") and not base_url:
        raise HTTPException(
            status_code=422,
            detail=f"provider {provider!r} requires base_url（无默认端点）",
        )

    protocol_base_urls = payload.get("protocol_base_urls")
    if protocol_base_urls is not None:
        protocol_base_urls = _validate_protocol_base_urls(protocol_base_urls)

    try:
        raw_params = _normalize_params(payload.get("params_json", payload.get("params")))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _validate_connection_params(raw_params)
    params_dict = _prepare_post_params(raw_params)

    enabled = payload.get("enabled", 1)
    if enabled not in (0, 1, True, False):
        raise HTTPException(status_code=422, detail="enabled must be 0 or 1")
    enabled_int = 1 if enabled in (1, True) else 0

    settings = request.app.state.settings
    svc = ConnectionService(settings.db_path)
    row = svc.create(
        name=name.strip(),
        provider=provider,
        base_url=base_url,
        params=params_dict,
        enabled=enabled_int,
        protocol_base_urls=protocol_base_urls,
    )
    return _mask_connection(row)


@router.get("/model-connections", response_model=list[ModelConnectionOut])
def list_model_connections(request: Request, include_enabled_only: bool = False):
    """列出所有连接（``include_enabled_only`` 可选过滤）。"""
    settings = request.app.state.settings
    svc = ConnectionService(settings.db_path)
    rows = svc.list(include_enabled_only=include_enabled_only)
    return [_mask_connection(r) for r in rows]


@router.get("/model-connections/{connection_id}", response_model=ModelConnectionOut)
def get_model_connection(connection_id: str, request: Request):
    """按主键取单条；不存在 → 404。"""
    settings = request.app.state.settings
    row = ConnectionService(settings.db_path).get(connection_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"model_connection {connection_id!r} not found"
        )
    return _mask_connection(row)


@router.patch("/model-connections/{connection_id}", response_model=ModelConnectionOut)
def update_model_connection(connection_id: str, payload: dict, request: Request) -> dict:
    """部分更新（name / provider / base_url / protocol_base_urls / params / enabled）。

    ``params`` 走 PATCH 语义：**缺键 = 保留原值**、显式 ``null`` = 删除该键；
    ``headers`` 是 dict ⇒ **逐键**语义（``***`` = 该键不改）。
    ``protocol_base_urls``：dict = 整表替换、显式 ``null`` = 清空（全部走 base_url）、
    缺键 = 不动。

    ⚠️ 连接改 ``provider``（主协议）**不再**同步到引用它的档案——协议是模型级
    属性，由档案自持（2026-10-24 裁决；原 ``sync_provider_to_profiles`` 已删）。
    """
    settings = request.app.state.settings
    svc = ConnectionService(settings.db_path)
    existing = svc.get(connection_id)
    if existing is None:
        raise HTTPException(
            status_code=404, detail=f"model_connection {connection_id!r} not found"
        )

    fields: dict[str, Any] = {}
    if "name" in payload:
        name = payload["name"]
        if not (isinstance(name, str) and name.strip()):
            raise HTTPException(status_code=422, detail="name must be a non-empty string")
        fields["name"] = name.strip()
    if "provider" in payload:
        provider = payload["provider"]
        if not (isinstance(provider, str) and provider.strip()):
            raise HTTPException(status_code=422, detail="provider must be a non-empty string")
        fields["provider"] = provider.strip()
    if "base_url" in payload:
        base_url = payload["base_url"]
        if base_url is None:
            fields["base_url"] = None
        elif isinstance(base_url, str):
            fields["base_url"] = base_url.strip() or None
        else:
            raise HTTPException(status_code=422, detail="base_url must be a string or null")
    if "protocol_base_urls" in payload:
        pool = payload["protocol_base_urls"]
        if pool is None:
            fields["protocol_base_urls"] = None
        else:
            cleaned = _validate_protocol_base_urls(pool)
            fields["protocol_base_urls"] = (
                _dump(cleaned) if cleaned else None
            )
    if "enabled" in payload:
        enabled = payload["enabled"]
        if enabled not in (0, 1, True, False):
            raise HTTPException(status_code=422, detail="enabled must be 0 or 1")
        fields["enabled"] = 1 if enabled in (1, True) else 0
    if "params_json" in payload or "params" in payload:
        try:
            raw_params = _normalize_params(
                payload["params_json"] if "params_json" in payload else payload["params"]
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # 合并后的结果也要过白名单与撞车校验
        merged = _prepare_patch_params(raw_params, existing["params_json"])
        _validate_connection_params(merged)
        fields["params_json"] = _dump(merged)

    row = svc.update_partial(connection_id, fields)
    assert row is not None  # 上方已确认存在
    return _mask_connection(row)


def _dump(params: dict) -> str:
    import json

    return json.dumps(params, ensure_ascii=False)


@router.delete("/model-connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_connection(connection_id: str, request: Request):
    """删除（204）。

    ⚠️ **被任何档案引用 → 409**（照既有「删档案被 binding 引用就 409」的范式）：
    **不做静默解绑**——那会把档案变成「悬空引用」这种第三态，
    且用户下一次改 key 时会踩空。``detail.referenced_by`` 列出引用它的档案。
    """
    settings = request.app.state.settings
    svc = ConnectionService(settings.db_path)
    refs = svc.list_profiles_referencing(connection_id)
    if refs:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "connection_in_use",
                "message": f"connection {connection_id!r} 被 {len(refs)} 个档案引用",
                "referenced_by": refs,
            },
        )
    ok = svc.delete(connection_id)
    if not ok:
        raise HTTPException(
            status_code=404, detail=f"model_connection {connection_id!r} not found"
        )
    return None


@router.post(
    "/model-connections/{connection_id}/test",
    status_code=status.HTTP_200_OK,
    response_model=ConnectionTestResult,
    response_model_exclude_unset=True,
)
def test_model_connection(connection_id: str, request: Request) -> dict:
    """探测连接的可达性。

    打 ``GET {models_url}``（缺省 ``{base_url}/models``）。
    ⚠️ **不降级到 chat 探测**（连接层没有 model 概念）⇒ 端点 404/405/501 时
    如实报「该端点不提供模型列表」并给出可执行指引，**不假成功**。
    （档案侧的 ``/test`` 有 model，可以降级到极小 chat 请求探测。）
    """
    settings = request.app.state.settings
    svc = ConnectionService(settings.db_path)
    row = svc.get(connection_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"model_connection {connection_id!r} not found"
        )
    if row.get("enabled") == 0:
        raise HTTPException(
            status_code=422,
            detail=(
                f"model_connection {connection_id!r} is disabled (enabled=0); cannot /test"
            ),
        )

    params = parse_params(row.get("params_json"))
    base_url = (row.get("base_url") or "").rstrip("/")
    models_url = params.get("models_url") or (f"{base_url}/models" if base_url else "")
    if not models_url:
        raise HTTPException(
            status_code=422,
            detail="连接未配置 base_url，且未配置 models_url —— 无法探测",
        )

    headers = {"Content-Type": "application/json"}
    api_key = params.get("api_key")
    if isinstance(api_key, str) and api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    raw_headers = params.get("headers")
    if isinstance(raw_headers, dict):
        headers.update({k: v for k, v in raw_headers.items() if isinstance(v, str)})

    import time

    start = time.monotonic()
    try:
        with httpx.Client() as client:
            resp = client.get(models_url, headers=headers, timeout=_PROBE_TIMEOUT_S)
    except httpx.HTTPError as exc:
        latency = int((time.monotonic() - start) * 1000)
        return {
            "connection_id": connection_id,
            "ok": False,
            "latency_ms": latency,
            "detail": f"network error: {exc}",
            "status_code": None,
        }
    latency = int((time.monotonic() - start) * 1000)
    if resp.status_code < 400:
        return {
            "connection_id": connection_id,
            "ok": True,
            "latency_ms": latency,
            "detail": "reachable",
            "status_code": resp.status_code,
        }
    snippet = (resp.text or "")[:200]
    detail = f"HTTP {resp.status_code}: {snippet}"
    if resp.status_code in (404, 405, 501):
        detail = (
            f"模型列表端点不可用（HTTP {resp.status_code}）：{models_url} —— "
            "该服务可能不提供模型列表，请在连接里填「模型列表地址」，或直接手工填写模型名"
        )
    return {
        "connection_id": connection_id,
        "ok": False,
        "latency_ms": latency,
        "detail": detail,
        "status_code": resp.status_code,
    }
