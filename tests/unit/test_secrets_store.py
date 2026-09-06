"""``packages.core.secrets_store`` 单测（V3.8 — secrets.json）。

覆盖：
- 优先级（高 → 低）：file[profile_id] > file[provider] > file[env_name] > params > env；
- 文件缺失返回空 dict、坏 JSON 不抛、空 / 非 dict api_keys 优雅降级；
- mtime 变更后自动重读（缓存命中校验）；
- ``resolve_api_key`` 关键词 ``profile_id`` 透传到文件优先；
- ``NOVELOS_SECRETS_FILE`` 覆盖默认路径；
- ``security._mask_response`` 在 params 无 api_key 但文件命中时仍 ``has_api_key=True``。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from packages.core.model_router import security
from packages.core.model_router.providers import resolve_api_key
from packages.core.secrets_store import (
    _resolve_secrets_path,
    load_api_keys,
    reset_cache,
    resolve_profile_api_key,
)

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _write_secrets(path: Path, api_keys: dict[str, str]) -> None:
    """写入测试用 secrets.json；api_keys 不含明文敏感数据。"""
    path.write_text(
        json.dumps({"api_keys": api_keys}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """每个用例前后清缓存并屏蔽 NOVELOS_SECRETS_FILE，避免相互污染。"""
    monkeypatch.delenv("NOVELOS_SECRETS_FILE", raising=False)
    monkeypatch.delenv("NOVELOS_API_KEY_KIMI", raising=False)
    monkeypatch.delenv("NOVELOS_API_KEY_DEEPSEEK", raising=False)
    reset_cache()
    yield
    reset_cache()


# ---------------------------------------------------------------------------
# load_api_keys：基本 / 缺失 / 坏 JSON / 缓存
# ---------------------------------------------------------------------------


def test_load_api_keys_missing_returns_empty(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(tmp_path / "no-such.json"))
    assert load_api_keys() == {}


def test_load_api_keys_bad_json_does_not_raise(tmp_path: Path, monkeypatch):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(p))
    assert load_api_keys() == {}


def test_load_api_keys_invalid_root_or_apikeys_returns_empty(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(tmp_path / "a.json"))
    p = _resolve_secrets_path()
    assert p is not None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    assert load_api_keys() == {}
    p.write_text(json.dumps({"api_keys": "nope"}), encoding="utf-8")
    assert load_api_keys() == {}
    p.write_text(json.dumps({"api_keys": {"k": ""}}), encoding="utf-8")
    assert load_api_keys() == {}


def test_load_api_keys_mtime_cache_reload(tmp_path: Path, monkeypatch):
    p = tmp_path / "s.json"
    _write_secrets(p, {"mprof_x": "sk-test-xxx"})
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(p))
    reset_cache()
    assert load_api_keys() == {"mprof_x": "sk-test-xxx"}
    # mtime 不变 → 缓存命中（无法直接验对象一致，但 force_reload 应拿同样结果）
    assert load_api_keys(force_reload=False) == {"mprof_x": "sk-test-xxx"}
    # 修改文件并 sleep 确保 mtime 变化
    time.sleep(0.05)
    _write_secrets(p, {"mprof_x": "sk-test-yyy", "kimi": "sk-test-zzz"})
    assert load_api_keys() == {"mprof_x": "sk-test-yyy", "kimi": "sk-test-zzz"}


# ---------------------------------------------------------------------------
# 解析优先级
# ---------------------------------------------------------------------------


def test_priority_profile_id_over_provider_over_envname_over_params_over_env(
    tmp_path: Path, monkeypatch
):
    p = tmp_path / "s.json"
    _write_secrets(
        p,
        {
            "mprof_p": "sk-from-profile",
            "openai_compatible": "sk-from-provider",
            "NOVELOS_API_KEY_DEEPSEEK": "sk-from-envname",
        },
    )
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(p))
    monkeypatch.setenv("NOVELOS_API_KEY_DEEPSEEK", "sk-from-env")

    got = resolve_profile_api_key(
        profile_id="mprof_p",
        provider="openai_compatible",
        env_names=["NOVELOS_API_KEY_DEEPSEEK"],
        params_api_key="sk-from-params",
    )
    assert got == "sk-from-profile"

    got = resolve_profile_api_key(
        profile_id=None,
        provider="openai_compatible",
        env_names=["NOVELOS_API_KEY_DEEPSEEK"],
        params_api_key="sk-from-params",
    )
    assert got == "sk-from-provider"

    got = resolve_profile_api_key(
        profile_id=None,
        provider="deepseek",
        env_names=["NOVELOS_API_KEY_DEEPSEEK"],
        params_api_key="sk-from-params",
    )
    assert got == "sk-from-envname"

    got = resolve_profile_api_key(
        profile_id=None,
        provider="deepseek",
        env_names=["NOVELOS_API_KEY_DEEPSEEK"],
        params_api_key="sk-from-params",
    )
    assert got == "sk-from-envname"

    # 去掉 envname 项后，params 优先于 env
    p2 = tmp_path / "s2.json"
    _write_secrets(p2, {})
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(p2))
    monkeypatch.setenv("NOVELOS_API_KEY_DEEPSEEK", "sk-from-env")
    got = resolve_profile_api_key(
        profile_id=None,
        provider="deepseek",
        env_names=["NOVELOS_API_KEY_DEEPSEEK"],
        params_api_key="sk-from-params",
    )
    assert got == "sk-from-params"

    # 再去掉 params → env 兜底
    got = resolve_profile_api_key(
        profile_id=None,
        provider="deepseek",
        env_names=["NOVELOS_API_KEY_DEEPSEEK"],
        params_api_key=None,
    )
    assert got == "sk-from-env"


def test_priority_provider_lowercase_lookup(tmp_path: Path, monkeypatch):
    """provider 字段大小写在 file 中按小写命中（router 已经把 provider lower 化）。"""
    p = tmp_path / "s.json"
    _write_secrets(p, {"openai_compatible": "sk-from-provider-lower"})
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(p))
    got = resolve_profile_api_key(
        profile_id=None,
        provider="openai_compatible",
        env_names=[],
        params_api_key=None,
    )
    assert got == "sk-from-provider-lower"


def test_priority_returns_none_when_all_empty(tmp_path: Path, monkeypatch):
    p = tmp_path / "s.json"
    _write_secrets(p, {})
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(p))
    monkeypatch.delenv("NOVELOS_API_KEY_DEEPSEEK", raising=False)
    assert (
        resolve_profile_api_key(
            profile_id="mprof_x",
            provider="deepseek",
            env_names=["NOVELOS_API_KEY_DEEPSEEK"],
            params_api_key=None,
        )
        is None
    )


def test_params_api_key_empty_string_treated_as_missing(tmp_path: Path, monkeypatch):
    p = tmp_path / "s.json"
    _write_secrets(p, {})
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(p))
    monkeypatch.delenv("NOVELOS_API_KEY_DEEPSEEK", raising=False)
    got = resolve_profile_api_key(
        profile_id=None,
        provider="deepseek",
        env_names=["NOVELOS_API_KEY_DEEPSEEK"],
        params_api_key="",
    )
    assert got is None


# ---------------------------------------------------------------------------
# resolve_api_key：profile_id 透传到文件优先（与既有行为兼容）
# ---------------------------------------------------------------------------


def test_resolve_api_key_profile_id_wins_over_params_then_env(
    tmp_path: Path, monkeypatch
):
    p = tmp_path / "s.json"
    _write_secrets(p, {"mprof_p": "sk-from-profile"})
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(p))
    monkeypatch.setenv("NOVELOS_API_KEY_DEEPSEEK", "sk-from-env")
    # file[profile_id] 命中
    got = resolve_api_key(
        "deepseek",
        {"api_key": "sk-from-params"},
        profile_id="mprof_p",
    )
    assert got == "sk-from-profile"


def test_resolve_api_key_backward_compat_no_profile_id(monkeypatch):
    """保持旧调用方不传 profile_id 的行为：params → env。"""
    monkeypatch.setenv("NOVELOS_API_KEY_DEEPSEEK", "sk-from-env")
    assert resolve_api_key("deepseek", {"api_key": "sk-inline"}) == "sk-inline"
    assert resolve_api_key("deepseek", None) == "sk-from-env"
    monkeypatch.delenv("NOVELOS_API_KEY_DEEPSEEK")
    assert resolve_api_key("deepseek", None) is None


def test_resolve_api_key_provider_fallback_in_file(tmp_path: Path, monkeypatch):
    """profile_id 缺失但 provider 命中文件的情况。"""
    p = tmp_path / "s.json"
    _write_secrets(p, {"openai_compatible": "sk-from-provider"})
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(p))
    monkeypatch.delenv("NOVELOS_API_KEY_OPENAI_COMPATIBLE", raising=False)
    got = resolve_api_key("openai_compatible", None, profile_id=None)
    assert got == "sk-from-provider"


# ---------------------------------------------------------------------------
# security._mask_response：文件来源应让 has_api_key=True
# ---------------------------------------------------------------------------


def test_mask_response_has_api_key_true_from_file_only(tmp_path: Path, monkeypatch):
    p = tmp_path / "s.json"
    _write_secrets(p, {"mprof_p": "sk-test-from-file"})
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(p))
    # 模拟从 DB 取到的 row：params_json 里没有 api_key，但文件里有
    row = {
        "profile_id": "mprof_p",
        "provider": "openai_compatible",
        "params_json": json.dumps({"base_url": "https://example.com"}),
    }
    out = security._mask_response(row)
    assert out["has_api_key"] is True
    # params_json 仍脱敏（无 api_key 键，保持原样）
    assert "api_key" not in (out["params_json"] or {})


def test_mask_response_has_api_key_false_when_nothing(tmp_path: Path, monkeypatch):
    p = tmp_path / "s.json"
    _write_secrets(p, {})
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(p))
    row = {
        "config_id": "mcf_xxx",
        "provider": "deepseek",
        "params_json": json.dumps({}),
    }
    out = security._mask_response(row)
    assert out["has_api_key"] is False
