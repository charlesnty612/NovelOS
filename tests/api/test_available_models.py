"""V3.8 模型档案「拉取模型」端点测试。

覆盖：
1. ``mock`` provider 固定返回 ``mock-model``，不发出网。
2. ``openai_compatible`` + 假 key：monkeypatch httpx 返回假列表 → 200 且 models 含期望 id。
3. ``anthropic`` 无 key → 400；带 key 走 monkeypatch 路径成功。
4. ``openai_compatible`` 缺 base_url → 400。
5. 上游 500 → 502（不透传密文）。
6. 上游 502 等「上游报错」信息不得携带 api_key 明文。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations

_SECRET = "sk-THIS-IS-SECRET-KEY-12345"


def _make_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


# ---------------------------------------------------------------------------
# mock —— 直接返回假列表
# ---------------------------------------------------------------------------


def test_mock_provider_returns_fixed_list_without_network(tmp_path: Path):
    """``provider='mock'`` → 立即返回 ``["mock-model"]``，不发任何 HTTP。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-profiles/available-models",
                json={"provider": "mock"},
            )
            assert r.status_code == 200, r.text
            assert r.json() == {"models": ["mock-model"]}

    asyncio.run(run())


# ---------------------------------------------------------------------------
# openai_compatible —— monkeypatch httpx
# ---------------------------------------------------------------------------


def test_openai_compatible_with_inline_api_key_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``openai_compatible`` + 入参 api_key：拦截 httpx.get 返回假列表。"""
    app = _create_app(tmp_path)
    fake_response = httpx.Response(
        200,
        json={"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]},
    )

    def fake_get(url, **kwargs):  # noqa: ANN001
        # 校验 Authorization 头被正确设置（不回显密文到日志）
        headers = kwargs.get("headers") or {}
        assert "Bearer" in headers.get("Authorization", "")
        return fake_response

    import packages.core.api.routers.model_profiles as mp_mod

    monkeypatch.setattr(mp_mod.httpx, "get", fake_get)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-profiles/available-models",
                json={
                    "provider": "openai_compatible",
                    "base_url": "https://api.example.com/v1",
                    "api_key": _SECRET,
                },
            )
            assert r.status_code == 200, r.text
            data = r.json()
            # 排序去重
            assert sorted(data["models"]) == ["gpt-4o", "gpt-4o-mini"]

    asyncio.run(run())


def test_openai_compatible_missing_base_url_returns_400(tmp_path: Path):
    """``openai_compatible`` 缺 base_url → 400。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-profiles/available-models",
                json={"provider": "openai_compatible", "api_key": _SECRET},
            )
            assert r.status_code == 400, r.text
            assert "base_url" in r.text

    asyncio.run(run())


def test_openai_compatible_upstream_500_returns_502_without_key_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """上游 500 → 后端 502；错误信息不得包含 api_key 明文。"""
    app = _create_app(tmp_path)

    def fake_get(url, **kwargs):  # noqa: ANN001
        return httpx.Response(500, text="upstream boom")

    import packages.core.api.routers.model_profiles as mp_mod

    monkeypatch.setattr(mp_mod.httpx, "get", fake_get)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-profiles/available-models",
                json={
                    "provider": "openai_compatible",
                    "base_url": "https://api.example.com/v1",
                    "api_key": _SECRET,
                },
            )
            assert r.status_code == 502, r.text
            # 错误信息不含密文
            assert _SECRET not in r.text

    asyncio.run(run())


# ---------------------------------------------------------------------------
# anthropic —— 必须 key
# ---------------------------------------------------------------------------


def test_anthropic_missing_api_key_returns_400(tmp_path: Path):
    """Anthropic 无 key（入参 / env / secrets 都未配置）→ 400。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-profiles/available-models",
                json={"provider": "anthropic"},
            )
            assert r.status_code == 400, r.text
            assert "密钥" in r.text or "key" in r.text.lower()

    asyncio.run(run())


def test_anthropic_with_key_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Anthropic + 入参 key + base_url：走 monkeypatch 拿 x-api-key + 解析 data[].id。"""
    app = _create_app(tmp_path)
    fake_response = httpx.Response(
        200,
        json={"data": [{"id": "claude-3-5-sonnet-20241022"}, {"id": "claude-3-haiku"}]},
    )

    def fake_get(url, **kwargs):  # noqa: ANN001
        headers = kwargs.get("headers") or {}
        assert "x-api-key" in headers
        assert "anthropic-version" in headers
        return fake_response

    import packages.core.api.routers.model_profiles as mp_mod

    monkeypatch.setattr(mp_mod.httpx, "get", fake_get)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-profiles/available-models",
                json={
                    "provider": "anthropic",
                    "api_key": _SECRET,
                    "base_url": "https://api.anthropic.com",
                },
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert sorted(data["models"]) == [
                "claude-3-5-sonnet-20241022",
                "claude-3-haiku",
            ]

    asyncio.run(run())


# ---------------------------------------------------------------------------
# ollama —— 无需 key
# ---------------------------------------------------------------------------


def test_ollama_default_base_url_no_key_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``provider='ollama'`` 不传 base_url 用本地默认；解析 ``models[].name``。"""
    app = _create_app(tmp_path)
    fake_response = httpx.Response(
        200,
        json={"models": [{"name": "llama3.1:8b"}, {"name": "qwen2.5:7b"}]},
    )

    def fake_get(url, **kwargs):  # noqa: ANN001
        # 默认 base_url 应指向 127.0.0.1:11434
        assert "127.0.0.1:11434" in url
        return fake_response

    import packages.core.api.routers.model_profiles as mp_mod

    monkeypatch.setattr(mp_mod.httpx, "get", fake_get)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-profiles/available-models",
                json={"provider": "ollama"},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert sorted(data["models"]) == ["llama3.1:8b", "qwen2.5:7b"]

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 缺 provider → 422
# ---------------------------------------------------------------------------


def test_missing_provider_returns_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-profiles/available-models",
                json={},
            )
            assert r.status_code == 422, r.text

    asyncio.run(run())
