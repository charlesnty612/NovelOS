"""Model Router 共享安全工具（V3.7「模型档案 + 环节绑定」集中复用）。

职责：
- 集中 ``params_json`` 字段的 ``api_key`` 掩码 / 空值特殊语义处理；
- 给 :mod:`packages.core.api.routers.model_configs` / ``model_profiles`` /
  ``capability_bindings`` 三个路由共同 import 使用，避免在多处重复粘贴。

设计要点：
- ``_MASK = "***"`` 既作响应出口脱敏占位符，也作 PATCH 入参「保留 DB 原值」哨兵；
- 写路径的 ``api_key`` 语义：
  - ``"***"`` / 空字符串 / ``None`` → 视为「未设置 / 清空 / 保留」；
  - 其它字符串 → 视为新值（POST 落库；PATCH 覆盖）。
- 读路径一律过 :func:`_mask_response`：``api_key`` 替换为 ``_MASK``，响应顶层
  追加 ``has_api_key`` 布尔字段供前端判断是否已配置密钥。
"""

from __future__ import annotations

import json
from typing import Any

from .effective import SENSITIVE_HEADER_NAMES

_MASK = "***"
"""读路径脱敏占位符；写路径「保留 DB 原值」哨兵。"""


def _mask_headers(headers: dict[str, Any]) -> dict[str, Any]:
    """把**敏感头名**的值替换为 :data:`_MASK`（头名小写比较）。

    ⚠️ 2026-10-21 批次十五：``_mask_params`` 原本只掩 ``params["api_key"]`` 这一层
    ⇒ ``params_json.headers`` 里的 ``authorization`` **不会被掩** ⇒ GET 会把明文
    鉴权头回吐给前端。**这是新增的泄露面**，必须递归一层。

    ⚠️ ``x-opencode-session`` **不掩**：它是每 run 的路由 / 缓存 id，不是凭据——
    掩掉用户就没法核对模板对不对（见 :data:`SENSITIVE_HEADER_NAMES`）。
    """
    out = dict(headers)
    for k in list(out.keys()):
        if isinstance(k, str) and k.strip().lower() in SENSITIVE_HEADER_NAMES:
            out[k] = _MASK
    return out


def _parse_params_json(params_json: str | None) -> dict:
    """把 DB 中的 params_json JSON 字符串解析为 dict；解析失败 / 非 dict → 空 dict。"""
    if not params_json:
        return {}
    try:
        v = json.loads(params_json)
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def _dump_params_json(params: dict) -> str:
    return json.dumps(params, ensure_ascii=False)


def _mask_params(params: dict) -> tuple[dict, bool]:
    """浅拷贝 ``params``：若存在非空 ``api_key`` → 替换为 :data:`_MASK`；
    ``headers`` 里的**敏感头**逐键替换（见 :func:`_mask_headers`）。

    返回 ``(masked_params, has_api_key)``，供响应组装。
    ``has_api_key`` 仅反映 ``params.api_key`` 是否非空；文件来源的 key
    由 :func:`_mask_response` 在外层叠加（避免重复 JSON 解析与缓存失效）。
    """
    out = dict(params)
    key = out.get("api_key")
    has_key = isinstance(key, str) and key != ""
    if has_key:
        out["api_key"] = _MASK
    if isinstance(out.get("headers"), dict):
        out["headers"] = _mask_headers(out["headers"])
    return out, has_key


def _file_has_key(row: dict) -> bool:
    """``secrets.json`` 文件里是否有该档案 / provider 的 key。

    通过 ``profile_id``（model_profiles 行）或 ``config_id``（model_configs 行）
    查 ;provider 字段同时作为兜底。延迟导入以避免循环。
    """
    try:
        from packages.core.secrets_store import load_api_keys
    except Exception:  # pragma: no cover - 极端 import 失败
        return False
    secrets = load_api_keys()
    if not secrets:
        return False
    profile_id = row.get("profile_id") or row.get("config_id")
    if isinstance(profile_id, str) and secrets.get(profile_id):
        return True
    provider = row.get("provider")
    if isinstance(provider, str) and provider:
        if secrets.get(provider) or secrets.get(provider.lower()):
            return True
    return False


def _connection_has_key(connection_row: dict) -> bool:
    """连接的生效密钥是否存在（``params_json.api_key`` 或 ``secrets.json``）。

    绑定连接的档案，其 ``has_api_key`` 必须由**连接**派生 ⇒ 否则
    「连接上清空了 key」时前端仍显示「已配置密钥」（显示与实际失真）。
    """
    params = _parse_params_json(connection_row.get("params_json"))
    key = params.get("api_key")
    if isinstance(key, str) and key:
        return True
    try:
        from packages.core.secrets_store import load_api_keys
    except Exception:  # pragma: no cover - 极端 import 失败
        return False
    secrets = load_api_keys()
    if not secrets:
        return False
    connection_id = connection_row.get("connection_id")
    if isinstance(connection_id, str) and secrets.get(connection_id):
        return True
    provider = connection_row.get("provider")
    if isinstance(provider, str) and provider:
        return bool(secrets.get(provider) or secrets.get(provider.lower()))
    return False


def _mask_response(row: dict, *, connection_row: dict | None = None) -> dict:
    """读路径出口统一过此函数：把 ``row`` 转成对外响应（mask api_key + 附 has_api_key）。

    ``params_json`` 在 DB 是 JSON 字符串；此处解析后脱敏再以 dict 形态返回，
    便于前端直接渲染。``has_api_key`` 顶层字段供前端判断密钥是否已配置。
    V3.8 扩展：``has_api_key`` 现在同时涵盖 ``secrets.json`` 文件来源（profile_id /
    provider 命中），保证迁移到文件后前端仍能正确展示「已配置」。

    ⚠️ 2026-10-21 批次十五（连接层）：**绑定连接的档案，其 ``has_api_key`` 由连接派生**
    （``connection_row`` 传入时）——档案自己的 params 里那份 key 在绑定后**不生效**
    （运行时解析链不再查档案级 secrets），拿它判定会让「连接上清空了 key」
    仍显示「已配置密钥」⇒ **显示与实际失真**。
    """
    params = _parse_params_json(row.get("params_json"))
    masked, has_key = _mask_params(params)
    if connection_row is not None:
        # 绑定态 ⇒ 真相在连接上（档案自己的那份只是陈旧副本）
        has_key = _connection_has_key(connection_row)
    elif not has_key:
        has_key = _file_has_key(row)
    out = dict(row)
    out["params_json"] = masked
    out["has_api_key"] = has_key
    return out


def _normalize_params(params_json: Any) -> dict:
    """统一 ``params_json`` 入参为 dict（接受 dict / str / None）。

    返回 dict 而不是 JSON 字符串，便于在写路径上做 ``api_key`` 的特殊值处理。
    """
    if params_json is None:
        return {}
    if isinstance(params_json, str):
        try:
            v = json.loads(params_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"params_json must be valid JSON: {exc}") from exc
        if not isinstance(v, dict):
            raise ValueError("params_json must decode to an object")
        return v
    if isinstance(params_json, dict):
        return params_json
    raise ValueError("params_json must be a string or object")


def _is_mask_or_empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and (v == "" or v == _MASK))


def _prepare_post_headers(headers: dict[str, Any]) -> dict[str, Any]:
    """POST 侧的 headers 处理：值为掩码 / 空串 / None 的键**剔除**（视为未设置）。

    与标量 ``api_key`` 的 POST 语义同款（:func:`_is_mask_or_empty`）。
    """
    return {
        k: v
        for k, v in headers.items()
        if not (v is None or (isinstance(v, str) and v in ("", _MASK)))
    }


def _prepare_patch_headers(payload_headers: dict[str, Any], existing_headers: dict[str, Any]) -> dict[str, Any]:
    """headers 的 PATCH 语义（**逐键**，镜像标量 ``api_key`` 的规则）。

    - 键不在 payload        → 保留 existing；
    - 值 == ``"***"``       → 保留 existing[键]（掩码哨兵 = 不改）；
    - 值 == ``""`` / None   → 删除该键；
    - 其它字符串            → 覆盖。

    ⚠️ **不能复用标量语义**：``api_key`` 是**单个键**（``***`` / 空 / 覆盖 三态），
    而 headers 是「一组键值对」且**只有部分头名敏感** ⇒ 必须逐键判定。
    漏掉「逐键保留」的后果：前端把读回来的 ``***`` 原样提交 ⇒ **真值被写成
    ``***``** ⇒ 密钥静默失效。
    """
    out = dict(existing_headers)
    for k, v in payload_headers.items():
        if v is None or (isinstance(v, str) and v == ""):
            out.pop(k, None)
        elif isinstance(v, str) and v == _MASK:
            # 掩码哨兵 = 「不改这个键」；原值不存在时该键视为未设置，跳过
            continue
        else:
            out[k] = v
    return out


def _apply_headers(
    out: dict[str, Any],
    *,
    has_headers: bool,
    raw_headers: Any,
    existing: dict[str, Any],
) -> dict[str, Any]:
    """把 headers 的 dict 语义套到已合并好的 ``out`` 上（PATCH 路径专用）。"""
    if not has_headers:
        return out
    if raw_headers is None:
        out.pop("headers", None)
        return out
    if not isinstance(raw_headers, dict):
        raise ValueError("params.headers must be an object (mapping of header names to values)")
    existing_headers = existing.get("headers")
    if not isinstance(existing_headers, dict):
        existing_headers = {}
    out["headers"] = _prepare_patch_headers(raw_headers, existing_headers)
    return out


def _prepare_post_params(params: dict) -> dict:
    """POST 写路径：剥离 mask/空 api_key（视为未设置），其余保留明文。"""
    out = dict(params)
    if "api_key" in out and _is_mask_or_empty(out["api_key"]):
        out.pop("api_key")
    if isinstance(out.get("headers"), dict):
        out["headers"] = _prepare_post_headers(out["headers"])
    return out


def _prepare_patch_params(payload_params: dict, existing_params_json: str) -> dict:
    """PATCH 写路径：合并入参与 DB 原值。

    - 入参 ``api_key`` 是 :data:`_MASK`（"***"）→ 沿用 DB 原值；
    - 入参 ``api_key`` 是空字符串 / ``None`` → 清空（从结果中删除键）；
    - 入参 ``api_key`` 是其他字符串 → 用入参值覆盖；
    - 入参无 ``api_key`` 字段 → DB 原值保留；
    - 其它字段以入参为准（PATCH 部分更新语义）；
    - 入参某键显式为 ``None``（除 ``api_key`` 外）→ 视为「删除该键」语义，
      用于思考档位回默认等需要从 DB 删除字段的场景。缺键仍走「保留 DB 原值」
      的部分更新语义。

    ⚠️ 2026-10-21 批次十五：**``headers`` 走单独的 dict 逐键语义**
    （:func:`_prepare_patch_headers`），不适用上面的标量规则
    （「``headers: null``」= 删除整个 headers 键，与「某键值为 None」不同层）。
    """
    existing = _parse_params_json(existing_params_json)
    payload = dict(payload_params)

    # headers 走 dict 逐键语义 ⇒ 先摘出来，最后单独套用
    has_headers = "headers" in payload
    raw_headers = payload.pop("headers", None)

    def _strip_nulls(merged: dict) -> dict:
        # 显式 None = 删除该键（api_key 的 None 已在上面专门分支处理过）
        for k, v in payload.items():
            if k == "api_key":
                continue
            if v is None:
                merged.pop(k, None)
        return merged

    def _finish(merged: dict) -> dict:
        return _apply_headers(
            merged, has_headers=has_headers, raw_headers=raw_headers, existing=existing
        )

    if "api_key" in payload:
        v = payload["api_key"]
        if isinstance(v, str) and v == _MASK:
            payload_without = dict(payload)
            if "api_key" in existing:
                payload_without["api_key"] = existing["api_key"]
            else:
                payload_without.pop("api_key", None)
            out = dict(existing)
            out.update(payload_without)
            return _finish(_strip_nulls(out))
        if v is None or (isinstance(v, str) and v == ""):
            out = dict(existing)
            out.pop("api_key", None)
            for k, val in payload.items():
                if k == "api_key":
                    continue
                if val is None:
                    out.pop(k, None)
                else:
                    out[k] = val
            return _finish(out)
    out = dict(existing)
    out.update(payload)
    return _finish(_strip_nulls(out))


__all__ = [
    "_MASK",
    "_parse_params_json",
    "_dump_params_json",
    "_mask_params",
    "_mask_headers",
    "_mask_response",
    "_normalize_params",
    "_is_mask_or_empty",
    "_prepare_post_params",
    "_prepare_post_headers",
    "_prepare_patch_headers",
    "_prepare_patch_params",
]
