"""连接层（model_connections）单元测试 —— 2026-10-21 批次十五。

覆盖：
1. **生效配置解析**（:func:`resolve_effective`）：
   - 未绑定连接的档案 ⇒ 生效配置与改造前**逐字相同**（存量零行为变化）；
   - 已绑定 ⇒ provider / base_url / api_key / headers / models_url **全部取自连接**，
     档案 params 里的同名键**被忽略**（防「一列两用」）；
   - 档案的**非**保留键（temperature / thinking）**仍然生效**；
   - **连接的 provider 覆盖档案列** —— 直接 UPDATE 档案的 provider 列**不改变**生效
     provider（★核心不变量：档案的 provider 列只是派生镜像）；
   - **悬空 connection_id ⇒ 抛配置错误**，不静默回落到档案自己的 base_url。
2. **请求头**：
   - 合并顺序：provider 默认 < 连接级 < 运行级；
   - **headers 绝不出现在上游请求体里**（4 个 provider 各一条 —— 漏一处就 400）；
   - ``{{run_id}}`` / ``{{session_id}}`` 渲染；``{{session_id}}`` 稳定且前缀 ``ses_``；
   - **无 run 上下文时不是 "unknown"**（探针 session 由 connection_id 派生）。
3. **密钥链**：``secrets[connection_id]`` 优先；绑定态**不查** ``secrets[profile_id]``。
4. **归组**：分组键用**生效 key**（两个档案 params 里同一个 key、生效 key 不同 ⇒ 分属两组）。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.model_router.effective import (
    RESERVED_PARAM_KEYS,
    HeaderContext,
    derive_probe_session_id,
    derive_session_id,
    group_profiles_by_connection,
    parse_params,
    render_headers,
    resolve_effective,
    validate_headers,
)
from packages.core.model_router.providers import (
    AnthropicProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)

_BASE = "https://api.example.test/v1"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    p = tmp_path / "t.db"
    apply_migrations(p)
    return str(p)


def _insert_profile(
    db_path: str,
    profile_id: str = "mprof_a",
    *,
    provider: str = "openai_compatible",
    model: str = "gpt-5.5",
    params: dict | None = None,
    connection_id: str | None = None,
    enabled: int = 1,
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO model_profiles
                (profile_id, name, provider, model, params_json, enabled,
                 created_at, updated_at, connection_id)
            VALUES (?, ?, ?, ?, ?, ?, 't', 't', ?)
            """,
            (
                profile_id,
                profile_id,
                provider,
                model,
                json.dumps(params or {}),
                enabled,
                connection_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_connection(
    db_path: str,
    connection_id: str = "mconn_x",
    *,
    name: str = "测试连接",
    provider: str = "openai_compatible",
    base_url: str | None = _BASE,
    params: dict | None = None,
    enabled: int = 1,
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO model_connections
                (connection_id, name, provider, base_url, params_json, enabled,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 't', 't')
            """,
            (connection_id, name, provider, base_url, json.dumps(params or {}), enabled),
        )
        conn.commit()
    finally:
        conn.close()


def _profile_row(db_path: str, profile_id: str = "mprof_a") -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM model_profiles WHERE profile_id = ?", (profile_id,)
        ).fetchone()
    finally:
        conn.close()
    return {k: row[k] for k in row.keys()}


def _connection_row(db_path: str, connection_id: str = "mconn_x") -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM model_connections WHERE connection_id = ?", (connection_id,)
        ).fetchone()
    finally:
        conn.close()
    return {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------------------
# 1. 生效配置解析
# ---------------------------------------------------------------------------


def test_unbound_profile_keeps_legacy_behaviour(db_path: str):
    """未绑定连接的档案 ⇒ 生效配置与改造前逐字相同（存量零行为变化）。"""
    _insert_profile(
        db_path,
        params={"base_url": _BASE, "api_key": "sk-plain", "timeout_s": 42, "temperature": 0.3},
    )
    row = _profile_row(db_path)
    eff = resolve_effective(row, None)
    assert eff.bound is False
    assert eff.provider == "openai_compatible"
    assert eff.base_url == _BASE
    assert eff.api_key == "sk-plain"
    assert eff.timeout_s == 42
    assert eff.upstream_params == {"temperature": 0.3}
    assert eff.headers == {}


def test_bound_profile_takes_everything_from_connection(db_path: str):
    """绑定连接 ⇒ base_url / api_key / headers / models_url 全部取自连接。"""
    _insert_connection(
        db_path,
        params={
            "api_key": "sk-conn",
            "headers": {"x-opencode-session": "{{session_id}}"},
            "models_url": "https://api.example.test/v1/models",
        },
    )
    _insert_profile(
        db_path,
        params={"base_url": "https://stale.example.test/v1", "api_key": "sk-stale"},
        connection_id="mconn_x",
    )
    row = _profile_row(db_path)
    eff = resolve_effective(row, _connection_row(db_path), run_id="run_1")
    assert eff.bound is True
    assert eff.base_url == _BASE
    assert eff.api_key == "sk-conn"
    assert eff.headers["x-opencode-session"] == derive_session_id("run_1")
    assert eff.models_url == "https://api.example.test/v1/models"


def test_bound_profile_keeps_non_reserved_params(db_path: str):
    """档案的**非**保留键（temperature / thinking）绑定连接后仍生效。"""
    _insert_connection(db_path, params={"api_key": "sk-conn"})
    _insert_profile(
        db_path,
        params={"base_url": _BASE, "temperature": 0.7, "thinking": {"type": "enabled"}},
        connection_id="mconn_x",
    )
    eff = resolve_effective(_profile_row(db_path), _connection_row(db_path))
    assert eff.upstream_params == {"temperature": 0.7, "thinking": {"type": "enabled"}}


def test_connection_provider_overrides_profile_column(db_path: str):
    """★核心不变量：连接的 provider 是唯一权威。

    档案的 provider 列只是派生镜像 ⇒ 直接 UPDATE 那一列**不改变**生效 provider。
    """
    _insert_connection(db_path, provider="anthropic", base_url=None)
    _insert_profile(db_path, provider="mock", connection_id="mconn_x")
    # 手工把档案的 provider 列改掉（模拟数据分叉）
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE model_profiles SET provider = 'mock' WHERE profile_id = 'mprof_a'"
        )
        conn.commit()
    finally:
        conn.close()
    eff = resolve_effective(_profile_row(db_path), _connection_row(db_path))
    assert eff.provider == "anthropic", "档案的 provider 列不得覆盖连接的 provider"


def test_dangling_connection_id_raises_instead_of_silent_fallback(db_path: str):
    """悬空 connection_id ⇒ 抛配置错误（**不静默回落**到档案自己的 base_url）。"""
    _insert_profile(db_path, params={"base_url": _BASE}, connection_id="mconn_missing")
    with pytest.raises(ValueError, match="不存在的连接"):
        resolve_effective(_profile_row(db_path), None)


def test_reserved_param_keys_include_headers():
    """headers 必须在保留键集合里 —— 漏了它就会进上游请求体 ⇒ 400。"""
    assert "headers" in RESERVED_PARAM_KEYS
    assert "models_url" in RESERVED_PARAM_KEYS


# ---------------------------------------------------------------------------
# 2. 请求头
# ---------------------------------------------------------------------------


def test_header_merge_order_default_connection_runtime(db_path: str):
    """合并顺序：provider 默认 < 连接级 < 运行级。"""
    _insert_connection(db_path, params={"headers": {"x-b": "conn", "x-c": "conn"}})
    _insert_profile(db_path, connection_id="mconn_x")
    eff = resolve_effective(_profile_row(db_path), _connection_row(db_path))
    assert eff.headers == {"x-b": "conn", "x-c": "conn"}


def test_session_id_is_stable_and_prefixed():
    """{{session_id}} 稳定、前缀 ses_、不同 run 不同。"""
    a = derive_session_id("run_1")
    b = derive_session_id("run_1")
    c = derive_session_id("run_2")
    assert a == b
    assert a.startswith("ses_")
    assert a != c


def test_probe_session_id_is_not_unknown(db_path: str):
    """★无 run 上下文时，{{session_id}} 渲染成**按连接派生的探针 id**，不是 "unknown"。"""
    out = render_headers(
        {"x-opencode-session": "{{session_id}}"},
        HeaderContext(run_id=None, connection_id="mconn_x"),
    )
    value = out["x-opencode-session"]
    assert value != "ses_unknown"
    assert value == derive_probe_session_id("mconn_x")
    assert value.startswith("ses_probe_")


def test_validate_headers_rejects_unknown_placeholder_and_injection():
    """未知占位符与控制字符都要被拒（写时 422）。"""
    assert validate_headers({"x-a": "{{foo}}"}) != []
    assert validate_headers({"x-a": "a\r\nX-B: b"}) != []
    assert validate_headers({"x-a": "{{session_id}}"}) == []
    assert validate_headers({"bad name": "v"}) != []


@pytest.mark.parametrize(
    "provider_cls,kwargs",
    [
        (
            OpenAICompatibleProvider,
            {"base_url": _BASE, "api_key": "sk", "model": "m"},
        ),
        (AnthropicProvider, {"api_key": "sk", "model": "m"}),
        (OllamaProvider, {"model": "m"}),
    ],
)
def test_headers_never_leak_into_request_body(provider_cls, kwargs):
    """★headers 绝不出现在上游请求体里（漏一处就 400）。

    三个真实 provider 各一条 —— Anthropic / Ollama 此前**根本不剔保留键**
    （它们今天靠 router 层兜底，直调时会漏）。
    """
    captured: dict[str, object] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured["content"] = req.content
        captured["headers"] = dict(req.headers)
        if provider_cls is OllamaProvider:
            return httpx.Response(200, json={"message": {"content": "ok"}})
        if provider_cls is AnthropicProvider:
            return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})
        return httpx.Response(200, text="data: {\"choices\":[{\"delta\":{\"content\":\"ok\"}}]}\n\ndata: [DONE]\n\n")

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    provider = provider_cls(**kwargs, client=client, extra_headers={"x-opencode-session": "ses_1"})
    provider.complete([{"role": "user", "content": "hi"}], params={"headers": {"x-a": "b"}})
    body = captured["content"]
    assert b"headers" not in body, f"{provider_cls.__name__} 把 headers 塞进了请求体"
    assert b"base_url" not in body, f"{provider_cls.__name__} 把 base_url 塞进了请求体"
    # 自定义头确实发出去了
    assert captured["headers"].get("x-opencode-session") == "ses_1"


def test_openai_provider_health_check_falls_back_to_chat_probe():
    """模型列表端点 404 ⇒ 退化为极小 chat 探测（不假失败）。"""
    seen: list[str] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.path.endswith("/models"):
            return httpx.Response(404, text="no models here")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    provider = OpenAICompatibleProvider(
        base_url=_BASE, api_key="sk", model="gpt-5.5", client=client
    )
    result = provider.health_check()
    assert result["ok"] is True
    assert result["probe"] == "chat"
    assert seen[-1].endswith("/chat/completions")
    assert "模型列表端点不可用" in result["detail"]


def test_openai_provider_health_check_uses_models_url_override():
    """models_url 覆盖 ⇒ 探测打的是那个 URL（opencode 的模型列表不在 {base}/models）。"""
    seen: list[str] = []

    def _handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    provider = OpenAICompatibleProvider(
        base_url=_BASE,
        api_key="sk",
        model="gpt-5.5",
        client=client,
        models_url="https://api.example.test/inference/go/v1/models",
    )
    result = provider.health_check()
    assert result["ok"] is True
    assert result["probe"] == "models"
    assert seen == ["https://api.example.test/inference/go/v1/models"]


# ---------------------------------------------------------------------------
# 3. 密钥链
# ---------------------------------------------------------------------------


def test_connection_id_outranks_profile_id_in_secrets(db_path: str, monkeypatch):
    """secrets[connection_id] 优先于 secrets[profile_id]。"""
    from packages.core import secrets_store

    monkeypatch.setattr(
        secrets_store,
        "load_api_keys",
        lambda: {"mconn_x": "sk-from-conn", "mprof_a": "sk-from-profile"},
    )
    _insert_connection(db_path, params={})
    _insert_profile(db_path, connection_id="mconn_x")
    eff = resolve_effective(_profile_row(db_path), _connection_row(db_path))
    assert eff.api_key == "sk-from-conn"


def test_bound_profile_does_not_read_profile_level_secret(db_path: str, monkeypatch):
    """★绑定态**不查** secrets[profile_id]（档案不再拥有 key）。"""
    from packages.core import secrets_store

    monkeypatch.setattr(
        secrets_store, "load_api_keys", lambda: {"mprof_a": "sk-from-profile"}
    )
    _insert_connection(db_path, params={"api_key": "sk-from-conn"})
    _insert_profile(db_path, connection_id="mconn_x")
    eff = resolve_effective(_profile_row(db_path), _connection_row(db_path))
    assert eff.api_key == "sk-from-conn", "绑定态仍在读档案级 secrets ⇒ 改连接上的 key 不生效"


# ---------------------------------------------------------------------------
# 4. 归组（分组键必须用生效 key）
# ---------------------------------------------------------------------------


def test_grouping_uses_effective_key_not_raw_param(db_path: str, monkeypatch):
    """★两个档案 params 里同一个 key、但生效 key 不同 ⇒ **必须分属两组**。

    （按 params 里的 key 分组会把它们合并 ⇒ 凭据静默串用。）
    """
    from packages.core import secrets_store

    monkeypatch.setattr(
        secrets_store, "load_api_keys", lambda: {"mprof_b": "sk-overridden"}
    )
    _insert_profile(
        db_path, "mprof_a", params={"base_url": _BASE, "api_key": "sk-shared"}
    )
    _insert_profile(
        db_path, "mprof_b", params={"base_url": _BASE, "api_key": "sk-shared"}
    )
    groups = group_profiles_by_connection(
        [
            _profile_row(db_path, "mprof_a"),
            _profile_row(db_path, "mprof_b"),
        ]
    )
    assert len(groups) == 2, f"生效 key 不同的两个档案被合并了：{groups}"
    for g in groups:
        assert len(g["profile_ids"]) == 1


def test_grouping_merges_identical_effective_keys(db_path: str):
    """生效 key 相同的两个档案 ⇒ 归到同一组（这正是「消除重复填」的收益点）。"""
    _insert_profile(
        db_path, "mprof_a", params={"base_url": _BASE, "api_key": "sk-shared"}
    )
    _insert_profile(
        db_path, "mprof_b", params={"base_url": _BASE, "api_key": "sk-shared"}
    )
    groups = group_profiles_by_connection(
        [
            _profile_row(db_path, "mprof_a"),
            _profile_row(db_path, "mprof_b"),
        ]
    )
    assert len(groups) == 1
    assert set(groups[0]["profile_ids"]) == {"mprof_a", "mprof_b"}


def test_parse_params_tolerates_dirty_json():
    """脏 JSON ⇒ {}（不抛）—— 与既有读路径口径一致。"""
    assert parse_params("{not json") == {}
    assert parse_params(None) == {}
    assert parse_params('{"a":1}') == {"a": 1}
    assert parse_params({"a": 1}) == {"a": 1}
