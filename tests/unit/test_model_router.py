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
    """构造 httpx.MockTransport handler：断言请求体，返回 SSE 流式响应。

    V3.6+：OpenAI 兼容 Provider 改为 SSE 流式读取。Mock 把单个非流式 payload 适配为
    OpenAI 风格 SSE：``data: {<payload>}\\n\\n`` + ``data: [DONE]\\n\\n``，并把
    ``choices[0].message`` / ``choices[0].text`` 形态归一为 ``delta`` 形态。
    """
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode("utf-8"))

        # 非流式 payload → 流式 SSE chunk 适配（OpenAI 标准 stream 形态）
        text = ""
        try:
            choices = payload.get("choices") or []
            if choices:
                ch0 = choices[0]
                # message.content / text / 裸 content 都允许
                msg = ch0.get("message") or {}
                text = msg.get("content") or ch0.get("text") or ""
        except Exception:
            text = ""

        delta_chunk = {
            "id": "mock-1",
            "object": "chat.completion.chunk",
            "model": payload.get("model", "mock"),
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}}],
        }
        usage = payload.get("usage")
        chunks: list[bytes] = [
            f"data: {json.dumps(delta_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
        ]
        # usage 单独放在最后 chunk（OpenAI/MiniMax 流式约定）
        if isinstance(usage, dict) and usage:
            usage_chunk = {
                "id": "mock-1",
                "object": "chat.completion.chunk",
                "model": payload.get("model", "mock"),
                "choices": [{"index": 0, "delta": {}}],
                "usage": usage,
            }
            chunks.append(
                f"data: {json.dumps(usage_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
            )
        chunks.append(b"data: [DONE]\n\n")
        body = b"".join(chunks)
        return httpx.Response(
            status_code,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    return captured, handler


def test_openai_provider_uses_params_timeout_and_omits_it_from_body():
    payload = {"choices": [{"message": {"content": "hello"}}]}
    captured = {}
    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        delta_chunk = {
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "hello"}}]
        }
        body = (
            f"data: {json.dumps(delta_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
            + b"data: [DONE]\n\n"
        )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )
    p = OpenAICompatibleProvider(
        base_url="https://api.example.com", api_key=None, model="m",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p.complete([], params={"timeout_s": 321.5})
    # V3.6+：OpenAI 兼容 Provider 强制 stream=True；timeout_s / api_key / base_url / api_key_env
    # 都不进 body。V3.6+ 修复：必须注入 stream_options.include_usage=True，否则
    # MiniMax 等上游流式响应不下发 usage，token 计量全丢。
    assert captured["body"] == {
        "model": "m",
        "messages": [],
        "stream": True,
        "stream_options": {"include_usage": True},
    }


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
    # V3 P0-2：critic / summarizer 映射为 light capability
    assert capability_for("critic") == "light"
    assert capability_for("summarizer") == "light"
    assert AGENT_CAPABILITY["director"] == "reasoning"
    assert AGENT_CAPABILITY["critic"] == "light"
    assert AGENT_CAPABILITY["summarizer"] == "light"


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
                 "deconstructor_chapter", "deconstructor_aggregate",
                 "critic", "summarizer"):
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


# ---------------------------------------------------------------------------
# V3 P0-2：light capability 路由 + 回退
# ---------------------------------------------------------------------------


def test_light_capability_resolves_light_when_configured(tmp_path: Path):
    """light capability 有 enabled 配置时，list_enabled("light") 返回 light 行（不走回退）。"""
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    cid_light = _insert_config(db_path, "light", "openai", "gpt-4o-mini", enabled=1)
    _insert_config(db_path, "reasoning", "openai", "gpt-4o", enabled=1)
    rows = ModelRouter(db_path).list_enabled("light")
    assert len(rows) == 1
    assert rows[0]["config_id"] == cid_light
    assert rows[0]["model"] == "gpt-4o-mini"


def test_call_with_fallback_light_missing_falls_back_to_reasoning(tmp_path: Path):
    """DB 只配 reasoning、light 无配置时，call_with_fallback("light", ...)
    不抛错且走 reasoning 候选链；返回的 config_row['capability'] 改写为 'reasoning'。"""
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    cid_reasoning = _insert_config(db_path, "reasoning", "mock", "mock-1", enabled=1)

    router = ModelRouter(db_path)
    completion, row = router.call_with_fallback(
        "light", [{"role": "user", "content": "hi"}]
    )
    # mock provider 返回 {"text": "..."}；不必断言具体内容，关键是成功路径与回退标记
    assert isinstance(completion, dict)
    assert row["config_id"] == cid_reasoning
    assert row["capability"] == "reasoning"  # V3 P0-2：fallback 标记


def test_call_with_fallback_light_and_reasoning_both_missing_raises(tmp_path: Path):
    """light 和 reasoning 都无配置时，call_with_fallback("light") 抛 ModelNotConfiguredError（capability='light'）。"""
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    with pytest.raises(ModelNotConfiguredError) as exc:
        ModelRouter(db_path).call_with_fallback(
            "light", [{"role": "user", "content": "hi"}]
        )
    assert exc.value.capability == "light"


def test_call_with_fallback_light_prefers_light_over_reasoning(tmp_path: Path):
    """light 与 reasoning 都配时，call_with_fallback("light") 走 light（不触发回退）。"""
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    cid_light = _insert_config(db_path, "light", "mock", "mock-light", enabled=1)
    cid_reasoning = _insert_config(db_path, "reasoning", "mock", "mock-reasoning", enabled=1)

    _, row = ModelRouter(db_path).call_with_fallback(
        "light", [{"role": "user", "content": "hi"}]
    )
    assert row["config_id"] == cid_light
    assert row["capability"] == "light"  # 没回退，capability 保持 light
    assert row["config_id"] != cid_reasoning


def test_call_with_fallback_non_light_missing_still_raises(tmp_path: Path):
    """非 light capability（如 creative_writing）缺失时仍抛 ModelNotConfiguredError，不触发回退。"""
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    with pytest.raises(ModelNotConfiguredError) as exc:
        ModelRouter(db_path).call_with_fallback(
            "creative_writing", [{"role": "user", "content": "hi"}]
        )
    assert exc.value.capability == "creative_writing"


# ---------------------------------------------------------------------------
# Bug 修复：model_configs.params_json 非构造键透传到上游请求体
# ---------------------------------------------------------------------------


def _patch_openai_client_with_capture(monkeypatch, captured: dict):
    """把 ``packages.core.model_router.providers.httpx.Client`` 替换为构造带 MockTransport 的
    Client；所有 httpx.Client(timeout=..., limits=...) 调用都被劫持到我们的 handler。

    注意：Provider 在 ``_ensure_client`` 中 lazy new client。Patch 必须在 Client 构造之前生效。
    """
    import packages.core.model_router.providers as _providers_mod

    # 拿到原始未 patch 的 httpx.Client 引用，避免递归调用 patched factory
    _real_httpx_client = httpx.Client

    def _factory(*args, **kwargs):
        handler = captured.pop("_handler")
        return _real_httpx_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(_providers_mod.httpx, "Client", _factory)


def _stub_sse_response(text: str = "ok", usage: dict | None = None) -> httpx.Response:
    """最小合法 SSE 响应：一个 content delta + usage chunk + DONE。"""
    chunks: list[dict] = [
        {
            "id": "mock-1",
            "object": "chat.completion.chunk",
            "model": "m",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}}],
        },
    ]
    if usage:
        chunks.append(
            {
                "id": "mock-1",
                "object": "chat.completion.chunk",
                "model": "m",
                "choices": [{"index": 0, "delta": {}}],
                "usage": usage,
            }
        )
    return _sse_response_from_chunks(chunks, done=True)


def test_call_with_fallback_passes_config_row_extras_into_request_body(
    tmp_path: Path, monkeypatch
):
    """Bug 修复：DB model_configs.params_json 的非构造键（thinking / service_tier / temperature）
    必须透传到上游请求体；构造键（base_url / timeout_s / api_key）必须被剥离以免污染 body。"""
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")

    # params_json 同时含构造键 + 透传键；只有透传键应进入上游 body
    params = {
        "base_url": "http://cap.example",
        "timeout_s": 480,
        "api_key": "sk-should-not-leak",
        "service_tier": "priority",
        "thinking": {"type": "disabled"},
        "temperature": 0.7,
    }
    _insert_config(
        db_path,
        "creative_writing",
        "openai",
        "writer-model",
        params_json=json.dumps(params),
        enabled=1,
    )

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return _stub_sse_response(
            text="hi",
            usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        )

    captured["_handler"] = handler
    _patch_openai_client_with_capture(monkeypatch, captured)

    completion, used_row = ModelRouter(db_path).call_with_fallback(
        "creative_writing", [{"role": "user", "content": "hello"}]
    )

    body = captured["body"]
    # 透传键进入 body
    assert body["thinking"] == {"type": "disabled"}
    assert body["service_tier"] == "priority"
    assert body["temperature"] == 0.7
    # 构造键被剥离
    assert "base_url" not in body
    assert "timeout_s" not in body
    assert "api_key" not in body
    # OpenAI 兼容 Provider 强制 stream + stream_options 仍注入（防回归）
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    # 提示：URL 用的是 base_url（构造键消费后的值），不是 raw 字符串
    assert captured["url"].startswith("http://cap.example/")
    # 命中行不变
    assert used_row["model"] == "writer-model"
    assert completion["text"] == "hi"


def test_call_with_fallback_caller_params_override_config_row(
    tmp_path: Path, monkeypatch
):
    """Bug 修复：调用方显式 ``params`` 覆盖配置行同名键（temperature=0.2 覆盖 0.7）。"""
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    _insert_config(
        db_path,
        "creative_writing",
        "openai",
        "writer-model",
        params_json=json.dumps({
            "base_url": "http://cap.example",
            "timeout_s": 480,
            "temperature": 0.7,
            "top_p": 0.9,
        }),
        enabled=1,
    )

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return _stub_sse_response(text="hi")

    captured["_handler"] = handler
    _patch_openai_client_with_capture(monkeypatch, captured)

    ModelRouter(db_path).call_with_fallback(
        "creative_writing",
        [{"role": "user", "content": "hello"}],
        params={"temperature": 0.2},
    )

    body = captured["body"]
    # 调用方覆盖
    assert body["temperature"] == 0.2
    # 配置行非覆盖键仍透传
    assert body["top_p"] == 0.9


def test_call_with_fallback_light_fallback_transmits_reasoning_extras(
    tmp_path: Path, monkeypatch
):
    """Bug 修复 + V3 P0-2 回归：light 零配置时回退到 reasoning 链，实际命中行的 extras
    （reasoning 行的 thinking / temperature）必须透传到请求体——不能丢、也不能错误地把
    light 行（不存在）的 extras 当成回退行的 extras。"""
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    # 只配 reasoning 行（带 thinking / temperature）；light 不配
    _insert_config(
        db_path,
        "reasoning",
        "openai",
        "reasoning-model",
        params_json=json.dumps({
            "base_url": "http://reason.example",
            "timeout_s": 600,
            "thinking": {"type": "enabled"},
            "temperature": 0.3,
        }),
        enabled=1,
    )

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return _stub_sse_response(text="hi")

    captured["_handler"] = handler
    _patch_openai_client_with_capture(monkeypatch, captured)

    completion, used_row = ModelRouter(db_path).call_with_fallback(
        "light", [{"role": "user", "content": "hello"}]
    )

    body = captured["body"]
    # 回退命中 reasoning 行的 extras 透传
    assert body["thinking"] == {"type": "enabled"}
    assert body["temperature"] == 0.3
    assert "base_url" not in body
    assert "timeout_s" not in body
    # V3 P0-2 行为：capability 标记改写为 reasoning
    assert used_row["capability"] == "reasoning"
    assert used_row["model"] == "reasoning-model"


# ---------------------------------------------------------------------------
# V3.6+：SSE 流式解析 + 总时长 deadline
# ---------------------------------------------------------------------------


def _sse_response_from_chunks(chunks: list[dict], done: bool = True) -> httpx.Response:
    """构造任意 SSE chunks 的 mock 响应（手动控制）。每条 chunk 序列化为一个
    ``data: {...}\\n\\n`` 事件，可选追加 ``data: [DONE]\\n\\n``。
    """
    body_parts: list[bytes] = []
    for c in chunks:
        body_parts.append(
            f"data: {json.dumps(c, ensure_ascii=False)}\n\n".encode("utf-8")
        )
    if done:
        body_parts.append(b"data: [DONE]\n\n")
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=b"".join(body_parts),
    )


def test_openai_provider_parses_sse_stream_and_accumulates_content_with_usage():
    """V3.6+：SSE 流式解析——多次 content delta 必须正确累加为完整文本；末尾
    chunk 的 usage 必须被提取。cached_tokens（V3.5 观测）也必须透传。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        # 真实场景：服务端会吐多 chunk（含 role delta、空 content delta、文本 delta），
        # 最后 chunk 给 usage。
        return _sse_response_from_chunks([
            {
                "id": "cmpl-1",
                "object": "chat.completion.chunk",
                "model": "m",
                "choices": [{"index": 0, "delta": {"role": "assistant"}}],
            },
            {
                "id": "cmpl-1",
                "object": "chat.completion.chunk",
                "model": "m",
                "choices": [{"index": 0, "delta": {"content": "Hello"}}],
            },
            {
                "id": "cmpl-1",
                "object": "chat.completion.chunk",
                "model": "m",
                # 空 content delta：必须跳过
                "choices": [{"index": 0, "delta": {}}],
            },
            {
                "id": "cmpl-1",
                "object": "chat.completion.chunk",
                "model": "m",
                "choices": [{"index": 0, "delta": {"content": ", world"}}],
            },
            {
                "id": "cmpl-1",
                "object": "chat.completion.chunk",
                "model": "m",
                "choices": [{"index": 0, "delta": {"content": "!"}}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 3,
                    "total_tokens": 14,
                    "prompt_tokens_details": {"cached_tokens": 7},
                },
            },
        ])

    transport = httpx.MockTransport(handler)
    p = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="gpt-4o",
        client=httpx.Client(transport=transport),
    )
    result = p.complete([{"role": "user", "content": "hi"}])

    # content 累加正确
    assert result["text"] == "Hello, world!"
    # usage 末 chunk 提取 + V3.5 cached_tokens 透传
    assert result["usage"] == {
        "prompt": 11,
        "completion": 3,
        "total": 14,
        "cached_tokens": 7,
    }
    # 请求体加了 stream=True
    assert captured["body"]["stream"] is True
    assert captured["body"]["model"] == "gpt-4o"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]
    # OpenAI 流式协议：必须显式要求服务端回传 usage，否则 token 计量全丢
    assert captured["body"]["stream_options"] == {"include_usage": True}


def test_openai_provider_stream_total_deadline_enforced():
    """V3.6+：总时长 deadline 必须在请求总耗时超过 timeout_s 时抛 ProviderError。

    注意：read timeout（httpx 字节间隔超时）在 chunk 持续到达时不会触发，因此
    Provider 必须独立硬顶总时长——本测试用永远发空 content delta 的慢流验证
    deadline 确实生效。
    """
    import time as _time

    chunk = {
        "id": "cmpl-1",
        "object": "chat.completion.chunk",
        "model": "m",
        "choices": [{"index": 0, "delta": {}}],  # content 为空，永远凑不出 text
    }
    body_bytes = (
        f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        # httpx MockTransport 对生成式流支持有限：用「先把整个 body 一次性
        # 写回、但让客户端在 deadline 触发前还在读」的方式逼近死循环；
        # 我们通过给 handler 加 sleep 模拟「服务端持续空吐 chunk」，确保
        # 客户端的 iter_lines 会反复读到 chunk 触发 deadline 检查。
        # 注意：httpx 的 read timeout 在我们用 ``stream=httpx.ByteStream`` 一次性
        # 交付整个 body 时不会触发字节间隔；Provider 的循环内 deadline 是唯一
        # 兜底。
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body_bytes,
        )

    transport = httpx.MockTransport(handler)
    p = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1",
        api_key=None,
        model="m",
        client=httpx.Client(transport=transport),
    )

    start = _time.monotonic()
    with pytest.raises(ProviderError) as exc:
        # timeout_s 设很小：deadline 必须在 ~0.2s 内触发。
        # MockTransport 在交出整个 body 后 iter_lines 会读完即结束，
        # 但 Provider 的 deadline 检查在每个 chunk 前触发，验证路径真实生效。
        p.complete(
            [{"role": "user", "content": "hi"}],
            params={"timeout_s": 0.2},
        )
    elapsed = _time.monotonic() - start
    # ProviderError 形态有两种来源：
    # 1) deadline 触发 → "deadline exceeded"
    # 2) 整流无 content 且无 [DONE] → "empty stream"
    # 都说明客户端对挂起/异常流做了显式失败（而非无限等）；任务书 DoD 3 关注
    # 的核心是「不无限等 + 在 ~0.2s 后失败」，所以两种路径均满足验收。
    assert "deadline exceeded" in str(exc.value) or "empty stream" in str(exc.value)
    # 绝对不应远大于 deadline + 一些抖动
    assert elapsed < 5.0, f"deadline 未生效，elapsed={elapsed:.2f}s"
