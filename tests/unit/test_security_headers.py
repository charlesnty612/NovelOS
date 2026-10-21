"""``security.py`` 的 headers 语义测试（2026-10-21 批次十五）。

覆盖：
1. **读路径脱敏**：``headers`` 里的**敏感头逐键**替换为 ``***``（大小写不敏感）；
   ``x-opencode-session`` 这类非凭据头**不掩**（用户要能核对模板对不对）。
2. **写路径 dict 语义**（``headers`` 是「一组键值对」，不能套用 api_key 的单键三态）：
   - 缺键 → 保留原值；
   - 值 ``"***"`` → **逐键**保留原值（漏这条 ⇒ 前端把掩码原样提交 ⇒ 真值被写成 ``***``）；
   - 值 ``""`` / ``None`` → 删除该键；
   - 其它字符串 → 覆盖；
   - ``headers: null`` → 删除整个 headers 键。
3. **标量语义不被污染**：``_prepare_patch_params`` 对**非** headers 的键行为逐字不变。
4. 不落 ``None`` 值（``{"headers": {"x": null}}`` 会让 httpx 抛难懂的 TypeError）。
"""

from __future__ import annotations

import json

from packages.core.model_router.security import (
    _MASK,
    _mask_params,
    _prepare_patch_params,
    _prepare_post_params,
)

# ---------------------------------------------------------------------------
# 1. 读路径脱敏
# ---------------------------------------------------------------------------


def test_mask_params_masks_sensitive_headers_case_insensitively():
    """敏感头逐键掩码（头名大小写不敏感）。"""
    params = {
        "api_key": "sk-plain",
        "headers": {
            "Authorization": "Bearer leak",
            "X-API-Key": "leak2",
            "api-key": "leak3",
            "Cookie": "leak4",
            "Proxy-Authorization": "leak5",
            "x-opencode-session": "{{session_id}}",
            "x-custom": "visible",
        },
    }
    masked, has_key = _mask_params(params)
    assert has_key is True
    assert masked["api_key"] == _MASK
    h = masked["headers"]
    for name in ("Authorization", "X-API-Key", "api-key", "Cookie", "Proxy-Authorization"):
        assert h[name] == _MASK, f"{name} 未被掩"
    assert h["x-opencode-session"] == "{{session_id}}", "非凭据头不该被掩"
    assert h["x-custom"] == "visible"


def test_mask_params_leaves_non_dict_headers_alone():
    """headers 不是 dict 时不炸（脏数据不放大成 500）。"""
    masked, _ = _mask_params({"headers": "not-a-dict"})
    assert masked["headers"] == "not-a-dict"


# ---------------------------------------------------------------------------
# 2. 写路径 dict 语义
# ---------------------------------------------------------------------------


def test_patch_headers_keeps_missing_keys():
    """缺键 → 保留 existing。"""
    existing = json.dumps({"headers": {"x-a": "1", "x-b": "2"}})
    out = _prepare_patch_params({"headers": {"x-a": "9"}}, existing)
    assert out["headers"] == {"x-a": "9", "x-b": "2"}


def test_patch_headers_masked_value_keeps_original():
    """★值 == "***" → 保留该键原值（逐键，不是整体）。

    漏这条的后果：前端把读回来的 ``***`` 原样提交 ⇒ 真值被写成 ``***`` ⇒ 密钥静默失效。
    """
    existing = json.dumps({"headers": {"authorization": "Bearer real", "x-a": "1"}})
    out = _prepare_patch_params({"headers": {"authorization": _MASK}}, existing)
    assert out["headers"]["authorization"] == "Bearer real"
    assert out["headers"]["x-a"] == "1"


def test_patch_headers_empty_or_none_deletes_key():
    """值 == "" 或 None → 删除该键。"""
    existing = json.dumps({"headers": {"x-a": "1", "x-b": "2"}})
    out = _prepare_patch_params({"headers": {"x-a": ""}}, existing)
    assert out["headers"] == {"x-b": "2"}
    out2 = _prepare_patch_params({"headers": {"x-b": None}}, existing)
    assert out2["headers"] == {"x-a": "1"}


def test_patch_headers_null_removes_whole_key():
    """headers: null → 删除整个 headers 键。"""
    existing = json.dumps({"headers": {"x-a": "1"}})
    out = _prepare_patch_params({"headers": None}, existing)
    assert "headers" not in out


def test_patch_headers_missing_key_keeps_whole_dict():
    """入参无 headers 键 → 整个 headers 原样保留。"""
    existing = json.dumps({"headers": {"x-a": "1"}})
    out = _prepare_patch_params({"temperature": 0.5}, existing)
    assert out["headers"] == {"x-a": "1"}
    assert out["temperature"] == 0.5


def test_post_params_drops_masked_and_empty_header_values():
    """POST：值为掩码 / 空串 / None 的头键被剔除（视为未设置）。"""
    out = _prepare_post_params(
        {"headers": {"x-a": _MASK, "x-b": "", "x-c": None, "x-d": "keep"}}
    )
    assert out["headers"] == {"x-d": "keep"}


def test_patch_params_does_not_store_none_header_values():
    """★落库结果不含 None 值（httpx 遇 None 头值会抛 TypeError）。"""
    existing = json.dumps({"headers": {"x-a": "1"}})
    out = _prepare_patch_params({"headers": {"x-b": None}}, existing)
    assert None not in out["headers"].values()


# ---------------------------------------------------------------------------
# 3. 标量语义不被污染
# ---------------------------------------------------------------------------


def test_api_key_scalar_semantics_unchanged():
    """★api_key 的三态语义逐字不变（headers 的新逻辑不得污染它）。"""
    existing = json.dumps({"api_key": "sk-old", "timeout_s": 30})

    masked = _prepare_patch_params({"api_key": _MASK}, existing)
    assert masked["api_key"] == "sk-old"

    cleared = _prepare_patch_params({"api_key": ""}, existing)
    assert "api_key" not in cleared

    replaced = _prepare_patch_params({"api_key": "sk-new"}, existing)
    assert replaced["api_key"] == "sk-new"

    kept = _prepare_patch_params({"timeout_s": 60}, existing)
    assert kept["api_key"] == "sk-old"
    assert kept["timeout_s"] == 60

    # 显式 None = 删除该键（思考档位回默认的既有语义）
    dropped = _prepare_patch_params({"timeout_s": None}, existing)
    assert "timeout_s" not in dropped


def test_null_value_keys_still_deleted_at_top_level():
    """顶层键显式 None ⇒ 删除（既有语义，不受 headers 分支影响）。"""
    existing = json.dumps({"thinking": {"type": "enabled"}})
    out = _prepare_patch_params({"thinking": None}, existing)
    assert "thinking" not in out
