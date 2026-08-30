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

_MASK = "***"
"""读路径脱敏占位符；写路径「保留 DB 原值」哨兵。"""


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
    """浅拷贝 ``params``：若存在非空 ``api_key`` → 替换为 :data:`_MASK`。

    返回 ``(masked_params, has_api_key)``，供响应组装。
    ``has_api_key`` 仅反映 ``params.api_key`` 是否非空；文件来源的 key
    由 :func:`_mask_response` 在外层叠加（避免重复 JSON 解析与缓存失效）。
    """
    out = dict(params)
    key = out.get("api_key")
    has_key = isinstance(key, str) and key != ""
    if has_key:
        out["api_key"] = _MASK
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


def _mask_response(row: dict) -> dict:
    """读路径出口统一过此函数：把 ``row`` 转成对外响应（mask api_key + 附 has_api_key）。

    ``params_json`` 在 DB 是 JSON 字符串；此处解析后脱敏再以 dict 形态返回，
    便于前端直接渲染。``has_api_key`` 顶层字段供前端判断密钥是否已配置。
    V3.8 扩展：``has_api_key`` 现在同时涵盖 ``secrets.json`` 文件来源（profile_id /
    provider 命中），保证迁移到文件后前端仍能正确展示「已配置」。
    """
    params = _parse_params_json(row.get("params_json"))
    masked, has_key = _mask_params(params)
    if not has_key:
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


def _prepare_post_params(params: dict) -> dict:
    """POST 写路径：剥离 mask/空 api_key（视为未设置），其余保留明文。"""
    out = dict(params)
    if "api_key" in out and _is_mask_or_empty(out["api_key"]):
        out.pop("api_key")
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
    """
    existing = _parse_params_json(existing_params_json)

    def _strip_nulls(merged: dict) -> dict:
        # 显式 None = 删除该键（api_key 的 None 已在上面专门分支处理过）
        for k, v in payload_params.items():
            if k == "api_key":
                continue
            if v is None:
                merged.pop(k, None)
        return merged

    if "api_key" in payload_params:
        v = payload_params["api_key"]
        if isinstance(v, str) and v == _MASK:
            payload_without = dict(payload_params)
            if "api_key" in existing:
                payload_without["api_key"] = existing["api_key"]
            else:
                payload_without.pop("api_key", None)
            out = dict(existing)
            out.update(payload_without)
            return _strip_nulls(out)
        if v is None or (isinstance(v, str) and v == ""):
            out = dict(existing)
            out.pop("api_key", None)
            for k, val in payload_params.items():
                if k == "api_key":
                    continue
                if val is None:
                    out.pop(k, None)
                else:
                    out[k] = val
            return out
    out = dict(existing)
    out.update(payload_params)
    return _strip_nulls(out)


__all__ = [
    "_MASK",
    "_parse_params_json",
    "_dump_params_json",
    "_mask_params",
    "_mask_response",
    "_normalize_params",
    "_is_mask_or_empty",
    "_prepare_post_params",
    "_prepare_patch_params",
]
