"""Sprint 8：Anthropic / Ollama Provider、健康检查、失败转移链。

覆盖：
- AnthropicProvider：请求 URL / headers / body（含 system 字段拆出）/ 响应解析 / 401 错误映射 / 健康检查。
- OllamaProvider：请求 URL / headers / body（options）/ 响应解析 / 无 key / 健康检查（GET /api/tags）。
- OpenAICompatibleProvider / MockProvider.health_check：mock 恒 ok / 200 / 网络异常三态。
- ModelRouter.call_with_fallback：第一个失败→第二个成功的链路 / 全失败抛 AggregateProviderError /
  无候选抛 ModelNotConfiguredError。
- /model-configs/{id}/test 端点：mock provider 返回 ok=true / latency_ms / detail；enabled=0 仍 422。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.model_router import (
    AggregateProviderError,
    AnthropicProvider,
    ModelNotConfiguredError,
    ModelRouter,
    OllamaProvider,
)
from packages.core.model_router.exceptions import ProviderError

# ---------------------------------------------------------------------------
# helper：MockTransport 捕获
# ---------------------------------------------------------------------------


def _capture_handler(payload: dict, status_code: int = 200):
    """构造 httpx.MockTransport handler：断言请求体，返回指定响应。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        try:
            captured["body"] = json.loads(request.content.decode("utf-8"))
        except Exception:
            captured["body"] = None
        return httpx.Response(status_code, json=payload)

    return captured, handler


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


# ===========================================================================
# 1. AnthropicProvider
# ===========================================================================


def test_anthropic_provider_sends_correct_request_and_parses_response():
    payload = {
        "content": [{"type": "text", "text": "hi from claude"}],
        "usage": {"input_tokens": 9, "output_tokens": 4},
    }
    captured, handler = _capture_handler(payload)
    transport = httpx.MockTransport(handler)
    p = AnthropicProvider(
        base_url="https://api.example.com",
        api_key="sk-ant-test",
        model="claude-3-5-sonnet-20241022",
        client=httpx.Client(transport=transport),
    )
    result = p.complete(
        [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "hi"},
        ],
        params={"temperature": 0.3, "max_tokens": 256},
    )
    assert result["text"] == "hi from claude"
    assert result["usage"] == {"prompt": 9, "completion": 4, "total": 13}
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/v1/messages")
    # system 提到顶层、messages 只剩 user
    assert captured["body"]["system"] == "你是助手"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["body"]["model"] == "claude-3-5-sonnet-20241022"
    assert captured["body"]["max_tokens"] == 256  # params 覆盖默认 4096
    assert captured["body"]["temperature"] == 0.3
    # headers：x-api-key + anthropic-version
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"


def test_anthropic_provider_default_base_url():
    captured, handler = _capture_handler({"content": [{"text": "ok"}], "usage": {}})
    p = AnthropicProvider(
        api_key="sk-ant",
        model="claude-3-5-sonnet-20241022",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p.complete([{"role": "user", "content": "hi"}])
    assert captured["url"].startswith("https://api.anthropic.com/v1/messages")


def test_anthropic_provider_default_max_tokens_when_no_params():
    captured, handler = _capture_handler({"content": [{"text": "ok"}], "usage": {}})
    p = AnthropicProvider(
        api_key="sk-ant",
        model="claude-3-5-sonnet-20241022",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p.complete([{"role": "user", "content": "hi"}])
    assert captured["body"]["max_tokens"] == 4096


def test_anthropic_provider_no_system_message():
    captured, handler = _capture_handler({"content": [{"text": "ok"}], "usage": {}})
    p = AnthropicProvider(
        api_key="sk",
        model="claude-3-5-sonnet-20241022",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p.complete([{"role": "user", "content": "hi"}])
    # 无 system 时不写顶层字段
    assert "system" not in captured["body"]


def test_anthropic_provider_401_raises_provider_error():
    transport = httpx.MockTransport(lambda req: httpx.Response(401, text="bad key"))
    p = AnthropicProvider(
        api_key="sk-wrong",
        model="claude-3-5-sonnet-20241022",
        client=httpx.Client(transport=transport),
    )
    with pytest.raises(ProviderError) as exc:
        p.complete([{"role": "user", "content": "hi"}])
    assert exc.value.provider == "anthropic"
    assert exc.value.status_code == 401


def test_anthropic_provider_malformed_response_raises_provider_error():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"content": []}))
    p = AnthropicProvider(
        api_key="sk", model="claude-3-5-sonnet-20241022",
        client=httpx.Client(transport=transport),
    )
    with pytest.raises(ProviderError):
        p.complete([{"role": "user", "content": "hi"}])


def test_anthropic_provider_network_error_raises_provider_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    p = AnthropicProvider(
        api_key="sk", model="claude-3-5-sonnet-20241022",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ProviderError):
        p.complete([{"role": "user", "content": "hi"}])


def test_anthropic_provider_requires_model():
    with pytest.raises(ValueError):
        AnthropicProvider(api_key="sk", model="")


def test_anthropic_health_check_ok_on_200():
    captured, handler = _capture_handler(
        {"content": [{"text": "ok"}], "usage": {}}, status_code=200
    )
    p = AnthropicProvider(
        api_key="sk", model="claude-3-5-sonnet-20241022",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    r = p.health_check()
    assert r["ok"] is True
    assert r["status_code"] == 200
    assert r["latency_ms"] >= 0
    assert "max_tokens" in captured["body"] and captured["body"]["max_tokens"] == 1


def test_anthropic_health_check_ok_on_401():
    """Anthropic 无公开 ping：401（鉴权失败但 endpoint 通）→ ok=True。"""
    transport = httpx.MockTransport(lambda req: httpx.Response(401, text="bad key"))
    p = AnthropicProvider(
        api_key="sk", model="claude-3-5-sonnet-20241022",
        client=httpx.Client(transport=transport),
    )
    r = p.health_check()
    assert r["ok"] is True
    assert r["status_code"] == 401
    assert "401" in r["detail"]


def test_anthropic_health_check_fail_on_500():
    transport = httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))
    p = AnthropicProvider(
        api_key="sk", model="claude-3-5-sonnet-20241022",
        client=httpx.Client(transport=transport),
    )
    r = p.health_check()
    assert r["ok"] is False
    assert r["status_code"] == 500


def test_anthropic_health_check_fail_on_network_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    p = AnthropicProvider(
        api_key="sk", model="claude-3-5-sonnet-20241022",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    r = p.health_check()
    assert r["ok"] is False
    assert r["status_code"] is None


# ===========================================================================
# 2. OllamaProvider
# ===========================================================================


def test_ollama_provider_sends_correct_request_and_parses_response():
    payload = {
        "model": "llama3",
        "message": {"role": "assistant", "content": "hello ollama"},
        "prompt_eval_count": 3,
        "eval_count": 5,
        "done": True,
    }
    captured, handler = _capture_handler(payload)
    transport = httpx.MockTransport(handler)
    p = OllamaProvider(
        base_url="http://127.0.0.1:11434",
        model="llama3",
        client=httpx.Client(transport=transport),
    )
    result = p.complete(
        [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hi"},
        ],
        params={"temperature": 0.7, "top_p": 0.9},
    )
    assert result["text"] == "hello ollama"
    assert result["usage"] == {"prompt": 3, "completion": 5, "total": 8}
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/chat")
    assert captured["body"]["model"] == "llama3"
    assert captured["body"]["stream"] is False
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "hi"},
    ]
    # sampling 参数放进 options
    assert captured["body"]["options"] == {"temperature": 0.7, "top_p": 0.9}
    # 无 api_key → 不带 Authorization
    assert "authorization" not in {k.lower() for k in captured["headers"].keys()}


def test_ollama_provider_default_base_url():
    captured, handler = _capture_handler(
        {"message": {"content": "ok"}, "prompt_eval_count": 0, "eval_count": 0}
    )
    p = OllamaProvider(
        model="llama3",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p.complete([{"role": "user", "content": "hi"}])
    assert captured["url"].startswith("http://127.0.0.1:11434/api/chat")


def test_ollama_provider_404_raises_provider_error():
    transport = httpx.MockTransport(lambda req: httpx.Response(404, text="not found"))
    p = OllamaProvider(
        base_url="http://127.0.0.1:11434", model="missing",
        client=httpx.Client(transport=transport),
    )
    with pytest.raises(ProviderError) as exc:
        p.complete([{"role": "user", "content": "hi"}])
    assert exc.value.provider == "ollama"
    assert exc.value.status_code == 404


def test_ollama_provider_malformed_response_raises_provider_error():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"foo": "bar"}))
    p = OllamaProvider(
        base_url="http://127.0.0.1:11434", model="llama3",
        client=httpx.Client(transport=transport),
    )
    with pytest.raises(ProviderError):
        p.complete([{"role": "user", "content": "hi"}])


def test_ollama_provider_requires_model():
    with pytest.raises(ValueError):
        OllamaProvider(model="")


def test_ollama_health_check_ok_on_200():
    captured, handler = _capture_handler({"models": []}, status_code=200)
    p = OllamaProvider(
        base_url="http://127.0.0.1:11434", model="llama3",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    r = p.health_check()
    assert r["ok"] is True
    assert r["status_code"] == 200
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/api/tags")


def test_ollama_health_check_fail_on_500():
    transport = httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))
    p = OllamaProvider(
        base_url="http://127.0.0.1:11434", model="llama3",
        client=httpx.Client(transport=transport),
    )
    r = p.health_check()
    assert r["ok"] is False
    assert r["status_code"] == 500


def test_ollama_health_check_fail_on_network_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    p = OllamaProvider(
        base_url="http://127.0.0.1:11434", model="llama3",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    r = p.health_check()
    assert r["ok"] is False
    assert r["status_code"] is None


# ===========================================================================
# 3. OpenAI / Mock health_check
# ===========================================================================


def test_openai_health_check_ok_on_200():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"data": []}))
    p = _openai_with_transport(transport)
    r = p.health_check()
    assert r["ok"] is True
    assert r["status_code"] == 200


def test_openai_health_check_fail_on_401():
    transport = httpx.MockTransport(lambda req: httpx.Response(401, text="bad key"))
    p = _openai_with_transport(transport)
    r = p.health_check()
    assert r["ok"] is False
    assert r["status_code"] == 401


def test_openai_health_check_fail_on_network_error():
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    from packages.core.model_router import OpenAICompatibleProvider

    p = OpenAICompatibleProvider(
        base_url="https://api.example.com", api_key=None, model="x",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    r = p.health_check()
    assert r["ok"] is False
    assert r["status_code"] is None


def _openai_with_transport(transport):
    from packages.core.model_router import OpenAICompatibleProvider

    return OpenAICompatibleProvider(
        base_url="https://api.example.com/v1",
        api_key="sk",
        model="x",
        client=httpx.Client(transport=transport),
    )


def test_mock_health_check_always_ok():
    from packages.core.model_router import MockProvider

    p = MockProvider(scripted=["ok"])
    r = p.health_check()
    assert r["ok"] is True
    assert r["status_code"] == 200
    assert r["latency_ms"] == 0


# ===========================================================================
# 4. ModelRouter.get_provider 分发 + call_with_fallback
# ===========================================================================


def test_router_get_provider_anthropic_uses_env_key(tmp_path: Path, monkeypatch):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("NOVELOS_API_KEY_ANTHROPIC", "sk-ant-env")
    row = {
        "provider": "anthropic",
        "model": "claude-3-5-sonnet-20241022",
        "params_json": "{}",  # base_url 缺省 → 走默认官方
    }
    provider = ModelRouter(db_path).get_provider(row)
    assert isinstance(provider, AnthropicProvider)
    assert provider.api_key == "sk-ant-env"
    assert provider.base_url == "https://api.anthropic.com"


def test_router_get_provider_ollama_no_api_key(tmp_path: Path):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    row = {
        "provider": "ollama",
        "model": "llama3",
        "params_json": "{}",  # base_url 缺省 → 走默认本地
    }
    provider = ModelRouter(db_path).get_provider(row)
    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == "http://127.0.0.1:11434"
    assert provider.model == "llama3"


def test_router_get_provider_anthropic_inline_key(tmp_path: Path):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    row = {
        "provider": "anthropic",
        "model": "claude-3-5-sonnet-20241022",
        "params_json": json.dumps({"api_key": "sk-ant-inline", "base_url": "https://proxy.example.com"}),
    }
    provider = ModelRouter(db_path).get_provider(row)
    assert isinstance(provider, AnthropicProvider)
    assert provider.api_key == "sk-ant-inline"
    assert provider.base_url == "https://proxy.example.com"


def test_router_list_enabled_returns_all_enabled_ordered(tmp_path: Path):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    _insert_config(db_path, "reasoning", "anthropic", "claude-3-5-sonnet", enabled=0)
    cid_a = _insert_config(db_path, "reasoning", "openai", "gpt-4o", enabled=1)
    cid_b = _insert_config(db_path, "reasoning", "ollama", "llama3", enabled=1)
    cid_c = _insert_config(db_path, "creative_writing", "openai", "gpt-4o", enabled=1)
    rows = ModelRouter(db_path).list_enabled("reasoning")
    cids = [r["config_id"] for r in rows]
    assert cids == [cid_a, cid_b]
    assert ModelRouter(db_path).list_enabled("creative_writing")[0]["config_id"] == cid_c
    # 不存在 capability 返回空
    assert ModelRouter(db_path).list_enabled("nonexistent") == []


def test_router_call_with_fallback_first_succeeds(tmp_path: Path):
    """单配置时与 resolve+get_provider 等价：直接成功，返回 used_config_row。"""
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    _insert_config(db_path, "reasoning", "openai", "gpt-4o")

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
    )
    # 注入 client 让 transport 生效：ModelRouter 不直接接受 client，这里走 Provider 内部注入
    from packages.core.model_router.providers import OpenAICompatibleProvider

    # 拦截 get_provider：替换为返回我们注入 transport 的 Provider
    router = ModelRouter(db_path)

    def patched_get(row, *, scripted=None):
        return OpenAICompatibleProvider(
            base_url="https://x",
            api_key=None,
            model=row["model"],
            client=httpx.Client(transport=transport),
        )

    router.get_provider = patched_get  # type: ignore[assignment]

    completion, used_row = router.call_with_fallback(
        "reasoning", [{"role": "user", "content": "hi"}]
    )
    assert completion["text"] == "hi"
    assert used_row["model"] == "gpt-4o"


def test_router_call_with_fallback_skips_failed_first_uses_second(tmp_path: Path):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    cid_fail = _insert_config(db_path, "reasoning", "openai", "gpt-4o-broken")
    cid_ok = _insert_config(db_path, "reasoning", "openai", "gpt-4o")

    router = ModelRouter(db_path)
    from packages.core.model_router.providers import OpenAICompatibleProvider

    def patched_get(row, *, scripted=None):
        if row["config_id"] == cid_fail:
            # 每次都返 500
            transport = httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))
        else:
            transport = httpx.MockTransport(lambda req: httpx.Response(200, json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }))
        return OpenAICompatibleProvider(
            base_url="https://x", api_key=None, model=row["model"],
            client=httpx.Client(transport=transport),
        )

    router.get_provider = patched_get  # type: ignore[assignment]

    completion, used_row = router.call_with_fallback(
        "reasoning", [{"role": "user", "content": "hi"}]
    )
    assert completion["text"] == "ok"
    assert used_row["config_id"] == cid_ok
    assert used_row["model"] == "gpt-4o"
    # 第一个被尝试过
    assert cid_fail != used_row["config_id"]


def test_router_call_with_fallback_all_fail_raises_aggregate(tmp_path: Path):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    cid_a = _insert_config(db_path, "reasoning", "openai", "gpt-a")
    cid_b = _insert_config(db_path, "reasoning", "openai", "gpt-b")

    router = ModelRouter(db_path)
    from packages.core.model_router.providers import OpenAICompatibleProvider

    transport = httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))

    def patched_get(row, *, scripted=None):
        return OpenAICompatibleProvider(
            base_url="https://x", api_key=None, model=row["model"],
            client=httpx.Client(transport=transport),
        )

    router.get_provider = patched_get  # type: ignore[assignment]

    with pytest.raises(AggregateProviderError) as exc:
        router.call_with_fallback("reasoning", [{"role": "user", "content": "hi"}])
    assert exc.value.capability == "reasoning"
    # attempts 包含两个 config_id（顺序与 list_enabled 一致：rowid ASC）
    cids = [cid for cid, _ in exc.value.attempts]
    assert cids == [cid_a, cid_b]


def test_router_call_with_fallback_no_candidate_raises_not_configured(tmp_path: Path):
    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    router = ModelRouter(db_path)
    with pytest.raises(ModelNotConfiguredError):
        router.call_with_fallback("reasoning", [{"role": "user", "content": "hi"}])


# ===========================================================================
# 5. /model-configs/{id}/test 端点（mock provider 走 health_check 恒 ok）
# ===========================================================================


def test_model_configs_test_endpoint_mock_returns_ok(tmp_path: Path):
    """mock provider /test 返回 ok=true / latency_ms / detail。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from packages.core.api.routers.model_configs import router as mc_router
    from packages.core.config import Settings

    apply_migrations(tmp_path / "test.db")
    db_path = str(tmp_path / "test.db")
    cid = _insert_config(db_path, "reasoning", "mock", "mock-1")

    app = FastAPI()
    app.include_router(mc_router, prefix="/api")
    app.state.settings = Settings(db_path=db_path)
    client = TestClient(app)
    r = client.post(f"/api/model-configs/{cid}/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config_id"] == cid
    assert body["ok"] is True
    assert "latency_ms" in body
    assert "detail" in body
    # 与旧字段对比：不再有 preview / usage；Sprint 8 改成 {ok, latency_ms, detail, status_code?}
    assert "preview" not in body
    assert "usage" not in body
