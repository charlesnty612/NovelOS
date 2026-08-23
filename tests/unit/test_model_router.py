"""Model Router 单测（Sprint 3）。

覆盖：
- MockProvider：无脚本回显 / 列表耗尽重复末条 / callable 按 i 返回。
- OpenAICompatibleProvider：httpx.MockTransport 断言请求体与响应解析；HTTP 错误 → ProviderError。
- ModelRouter.resolve：命中 / 缺失异常（ModelNotConfiguredError）/ capability 空。
- ModelRouter.get_provider：mock 分发 / openai_compatible 分发（含 base_url 缺失报错 / api_key 解析）。
- capability_for 映射。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.model_router import (
    AGENT_CAPABILITY,
    MockProvider,
    ModelNotConfiguredError,
    ModelRouter,
    OpenAICompatibleProvider,
    capability_for,
    resolve_api_key,
)
from packages.core.model_router.exceptions import ProviderError

# ---------------------------------------------------------------------------
# MockProvider
# ---------------------------------------------------------------------------


def test_mock_provider_no_script_echoes_empty_json():
    p = MockProvider()
    out = p.complete([{"role": "user", "content": "hi"}])
    assert out["text"] == "{}"
    assert out["usage"] == {"prompt": 0, "completion": 0, "total": 0}


def test_mock_provider_list_returns_then_repeats_last():
    p = MockProvider(scripted=["first", "second"])
    assert p.complete([])["text"] == "first"
    assert p.complete([])["text"] == "second"
    assert p.complete([])["text"] == "second"
    assert p.complete([])["text"] == "second"


def test_mock_provider_callable_invocation():
    p = MockProvider(scripted=lambda i: f"reply {i}")
    assert p.complete([])["text"] == "reply 0"
    assert p.complete([])["text"] == "reply 1"
    assert p.complete([])["text"] == "reply 2"


def test_mock_provider_empty_list_repeats_last_which_is_empty():
    # 空列表视为无脚本等同路径 → 回显 "{}"（与无脚本一致）
    p = MockProvider(scripted=[])
    assert p.complete([])["text"] == "{}"


# ---------------------------------------------------------------------------
# OpenAICompatibleProvider
# ---------------------------------------------------------------------------


def _make_handler(payload: dict, status_code: int = 200):
    """构造 httpx.MockTransport handler：断言请求体，返回指定响应。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(status_code, json=payload)

    return captured, handler


def test_openai_provider_uses_params_timeout_and_omits_it_from_body():
    payload = {"choices": [{"message": {"content": "hello"}}]}
    captured = {}
    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=payload)
    p = OpenAICompatibleProvider(
        base_url="https://api.example.com", api_key=None, model="m",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p.complete([], params={"timeout_s": 321.5})
    assert captured["body"] == {"model": "m", "messages": []}


def test_openai_provider_sends_correct_request_and_parses_response():
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "hello"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
    }
    captured, handler = _make_handler(payload)
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    p = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="gpt-4o",
        client=client,
    )
    result = p.complete(
        [{"role": "user", "content": "hi"}],
        params={"temperature": 0.5, "top_p": 0.9},
    )
    assert result["text"] == "hello"
    assert result["usage"] == {"prompt": 5, "completion": 7, "total": 12}
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/chat/completions")
    assert captured["body"]["model"] == "gpt-4o"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["body"]["temperature"] == 0.5
    assert captured["body"]["top_p"] == 0.9
    assert captured["headers"]["authorization"] == "Bearer sk-test"


def test_openai_provider_http_error_raises_provider_error():
    transport = httpx.MockTransport(lambda req: httpx.Response(500, text="server boom"))
    p = OpenAICompatibleProvider(
        base_url="https://api.example.com",
        api_key=None,
        model="m",
        client=httpx.Client(transport=transport),
    )
    with pytest.raises(ProviderError) as exc:
        p.complete([])
    assert exc.value.provider == "openai_compatible"
    assert exc.value.status_code == 500


def test_openai_provider_network_error_raises_provider_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    p = OpenAICompatibleProvider(
        base_url="https://api.example.com",
        api_key=None,
        model="m",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ProviderError):
        p.complete([])


def test_openai_provider_malformed_response_raises_provider_error():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"choices": []}))
    p = OpenAICompatibleProvider(
        base_url="https://api.example.com",
        api_key=None,
        model="m",
        client=httpx.Client(transport=transport),
    )
    with pytest.raises(ProviderError):
        p.complete([])


def test_openai_provider_construct_requires_base_url_and_model():
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(base_url="", api_key=None, model="m")
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(base_url="https://x", api_key=None, model="")


def test_resolve_api_key_prefers_params_then_env(monkeypatch):
    monkeypatch.setenv("NOVELOS_API_KEY_DEEPSEEK", "env-key")
    assert resolve_api_key("deepseek", {"api_key": "inline"}) == "inline"
    assert resolve_api_key("deepseek", None) == "env-key"
    monkeypatch.delenv("NOVELOS_API_KEY_DEEPSEEK")
    assert resolve_api_key("deepseek", None) is None


# ---------------------------------------------------------------------------
# ModelRouter.resolve / get_provider
# ---------------------------------------------------------------------------


def _insert_config(
    db_path: Path,
    capability: str,
    provider: str,
    model: str,
    params_json: str = "{}",
    enabled: int = 1,
) -> str:
    from packages.core.ids import new_id

    cid = new_id("mcf")
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO model_configs (config_id, capability, provider, model, params_json, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cid, capability, provider, model, params_json, enabled),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def test_capability_for_known_agents():
    assert capability_for("director") == "reasoning"
    assert capability_for("observer") == "reasoning"
    assert capability_for("writer") == "creative_writing"
    assert AGENT_CAPABILITY["director"] == "reasoning"


def test_capability_for_unknown_defaults_reasoning():
    assert capability_for("unknown_agent") == "reasoning"


def test_agent_capability_matches_agent_runtime_prompts():
    """P2-8：model_router.AGENT_CAPABILITY 与 agent_runtime.prompts.AGENT_TO_CAPABILITY
    必须严格相等（两边独立维护；新增 agent 时必须同步）。"""
    from packages.core.agent_runtime.prompts import (
        AGENT_TO_CAPABILITY as PROMPTS_AGENT_TO_CAPABILITY,
    )

    assert AGENT_CAPABILITY == PROMPTS_AGENT_TO_CAPABILITY, (
        f"AGENT_CAPABILITY drift detected.\n"
        f"  model_router.AGENT_CAPABILITY: {AGENT_CAPABILITY}\n"
        f"  agent_runtime.prompts.AGENT_TO_CAPABILITY: {PROMPTS_AGENT_TO_CAPABILITY}"
    )


def test_capability_for_known_agents_via_prompts_module():
    """P2-8：agent_runtime.prompts.capability_for 与 model_router.capability_for 行为一致。"""
    from packages.core.agent_runtime.prompts import (
        capability_for as prompts_capability_for,
    )

    for name in ("director", "observer", "writer", "arbiter",
                 "deconstructor_chapter", "deconstructor_aggregate"):
        assert capability_for(name) == prompts_capability_for(name), (
            f"capability_for({name!r}) mismatch: "
            f"router={capability_for(name)!r}, prompts={prompts_capability_for(name)!r}"
        )


def test_router_resolve_returns_first_enabled(tmp_path: Path):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    # 插入两条 enabled=1，rowid 后插的应排第二
    _insert_config(db_path, "reasoning", "openai", "gpt-4o", enabled=1)
    cid2 = _insert_config(db_path, "reasoning", "deepseek", "deepseek-chat", enabled=1)

    row = ModelRouter(db_path).resolve("reasoning")
    assert row["model"] == "gpt-4o"
    assert row["config_id"] != cid2


def test_router_resolve_skips_disabled(tmp_path: Path):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    _insert_config(db_path, "reasoning", "openai", "gpt-4o", enabled=0)
    cid2 = _insert_config(db_path, "reasoning", "deepseek", "deepseek-chat", enabled=1)
    row = ModelRouter(db_path).resolve("reasoning")
    assert row["config_id"] == cid2
    assert row["model"] == "deepseek-chat"


def test_router_resolve_missing_raises(tmp_path: Path):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    with pytest.raises(ModelNotConfiguredError) as exc:
        ModelRouter(db_path).resolve("reasoning")
    assert exc.value.capability == "reasoning"


def test_router_resolve_empty_capability_raises(tmp_path: Path):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    with pytest.raises(ModelNotConfiguredError):
        ModelRouter(db_path).resolve("")


def test_router_get_provider_mock_returns_mock(tmp_path: Path):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    row = {"provider": "mock", "model": "x", "params_json": "{}"}
    provider = ModelRouter(db_path).get_provider(row)
    assert isinstance(provider, MockProvider)


def test_router_get_provider_mock_with_scripted(tmp_path: Path):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    row = {"provider": "mock", "model": "x", "params_json": "{}"}
    provider = ModelRouter(db_path).get_provider(row, scripted=["ok"])
    assert provider.complete([])["text"] == "ok"


def test_router_get_provider_openai_compatible(tmp_path: Path, monkeypatch):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("NOVELOS_API_KEY_OPENAI", "sk-env")
    row = {
        "provider": "openai",
        "model": "gpt-4o",
        "params_json": json.dumps({"base_url": "https://api.openai.com/v1"}),
    }
    provider = ModelRouter(db_path).get_provider(row)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.api_key == "sk-env"
    assert provider.base_url == "https://api.openai.com/v1"


def test_router_get_provider_openai_compatible_missing_base_url(tmp_path: Path):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    row = {"provider": "openai", "model": "gpt-4o", "params_json": "{}"}
    with pytest.raises(ValueError) as exc:
        ModelRouter(db_path).get_provider(row)
    assert "base_url" in str(exc.value)


def test_router_get_provider_deepseek_uses_deepseek_env(monkeypatch):
    monkeypatch.setenv("NOVELOS_API_KEY_DEEPSEEK", "sk-ds")
    row = {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "params_json": json.dumps({"base_url": "https://api.deepseek.com/v1"}),
    }
    provider = ModelRouter("dummy.db").get_provider(row)
    assert provider.api_key == "sk-ds"


def test_router_get_provider_handles_dict_params_json(tmp_path: Path):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    row = {
        "provider": "openai",
        "model": "gpt-4o",
        "params_json": {"base_url": "https://x.com/v1"},  # dict 而非 str
    }
    provider = ModelRouter(db_path).get_provider(row)
    assert provider.base_url == "https://x.com/v1"
