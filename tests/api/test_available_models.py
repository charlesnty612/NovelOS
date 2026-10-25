"""V3.8 模型档案「拉取模型」端点测试。

覆盖：
1. ``mock`` provider 固定返回 ``mock-model``，不发出网。
2. ``openai_compatible`` + 假 key：monkeypatch httpx 返回假列表 → 200 且 models 含期望 id。
3. ``anthropic`` 无 key → 400；带 key 走 monkeypatch 路径成功。
4. ``openai_compatible`` 缺 base_url → 400。
5. 上游 500 → 502（不透传密文）。
6. 上游 502 等「上游报错」信息不得携带 api_key 明文。
7. **连接路径按档案协议取址**（2026-10-24 协议下放，M3）：档案声明的协议与连接
   主协议不同时，拉模型打到**该协议在协议地址池里的地址**（不是连接主地址）；
   不带协议 ⇒ 连接主协议的平面地址；池里没有该协议 ⇒ 回落连接主地址；
   未知协议 / ``protocol`` 与 ``provider`` 冲突 ⇒ 422。
8. **连接路径的密钥解析链**（2026-10-24 审查修复 M3，零覆盖分支补测试）：
   连接 params **不含 api_key** 时不 500（旧实现 UnboundLocalError），
   凭据走 ``resolve_api_key(connection_id=...)`` 解析链（secrets 连接级条目命中）；
   断言只打**布尔**（请求是否带 Authorization 头），**不断言任何凭据值**。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from ._shared import _create_app

_SECRET = "sk-THIS-IS-SECRET-KEY-12345"

# 协议地址池（连接级）：anthropic 平面与连接主地址**故意不同** ⇒
# 「打到池里 anthropic 的地址」与「打到连接主地址」是可区分的两件事。
_POOL_ADDRS = {
    "openai_compatible": "https://gw.test/openai/v1",
    "anthropic": "https://gw.test/anthropic",
}
_CONN_BODY = {
    "name": "gateway",
    "provider": "openai_compatible",
    "base_url": "https://gw.test/main",
    "protocol_base_urls": _POOL_ADDRS,
    "params": {"api_key": _SECRET},
}


def _make_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )




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


# ---------------------------------------------------------------------------
# 连接路径（2026-10-24 协议下放 · M3）：按**档案协议**从协议地址池取址
# ---------------------------------------------------------------------------


def _create_pool_connection(app, **overrides) -> str:
    """建一条带协议地址池的连接，返回 ``connection_id``。"""
    body = dict(_CONN_BODY)
    body.update(overrides)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "POST", "/api/model-connections", json=body)
            assert r.status_code == 201, r.text
            return r.json()["connection_id"]

    return asyncio.run(run())


def _record_probed_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """替换 ``httpx.get`` 并记录**真实被请求的 URL**（断言打在请求 URL 上）。"""
    calls: list[str] = []

    def fake_get(url, **kwargs):  # noqa: ANN001
        calls.append(url)
        return httpx.Response(200, json={"data": [{"id": "m-1"}]})

    import packages.core.api.routers.model_profiles as mp_mod

    monkeypatch.setattr(mp_mod.httpx, "get", fake_get)
    return calls


def _record_probed_requests(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """替换 ``httpx.get`` 并记录**请求 URL + 请求头键**。

    ⚠️ 只记录**头是否存在**（``Authorization`` 布尔），**不记录任何头的值**
    ⇒ 断言里不可能出现真实凭据明文。
    """
    calls: list[dict] = []

    def fake_get(url, **kwargs):  # noqa: ANN001
        headers = kwargs.get("headers") or {}
        calls.append(
            {
                "url": url,
                "has_authorization": "Authorization" in headers,
            }
        )
        return httpx.Response(200, json={"data": [{"id": "m-1"}]})

    import packages.core.api.routers.model_profiles as mp_mod

    monkeypatch.setattr(mp_mod.httpx, "get", fake_get)
    return calls


def _fetch_available_models(app, body: dict) -> httpx.Response:
    async def run():
        async with app.router.lifespan_context(app):
            return await _request(
                app, "POST", "/api/model-profiles/available-models", json=body
            )

    return asyncio.run(run())


def test_connection_path_uses_protocol_pool_address(tmp_path: Path, monkeypatch):
    """★连接路径 + 档案协议 ``anthropic`` ⇒ 打到**池里 anthropic 的平面地址**。

    这是 M3 的判别点：改前永远按连接主协议/主地址拉（此处 anthropic 档案会打到
    ``https://gw.test/main`` 或主协议的地址），改后必须命中池里的 anthropic 地址。
    """
    app = _create_app(tmp_path)
    cid = _create_pool_connection(app)
    calls = _record_probed_urls(monkeypatch)

    r = _fetch_available_models(app, {"connection_id": cid, "provider": "anthropic"})
    assert r.status_code == 200, r.text
    assert r.json() == {"models": ["m-1"]}
    # anthropic 平面的模型列表端点 = {池内 anthropic 地址}/v1/models
    assert calls == ["https://gw.test/anthropic/v1/models"]
    assert "gw.test/main" not in calls[0]


def test_connection_path_protocol_alias_uses_pool_address(tmp_path: Path, monkeypatch):
    """``protocol`` 是 ``provider`` 的显式别名（同义）⇒ 同样命中池内地址。"""
    app = _create_app(tmp_path)
    cid = _create_pool_connection(app)
    calls = _record_probed_urls(monkeypatch)

    r = _fetch_available_models(app, {"connection_id": cid, "protocol": "anthropic"})
    assert r.status_code == 200, r.text
    assert calls == ["https://gw.test/anthropic/v1/models"]


def test_connection_path_without_protocol_uses_main_plane_address(
    tmp_path: Path, monkeypatch
):
    """★不带协议 ⇒ 连接**主协议**对应的平面地址（池里主协议条目）。

    存量行为不回归：调用方没声明协议时，仍按连接的主协议拉模型。
    """
    app = _create_app(tmp_path)
    cid = _create_pool_connection(app)
    calls = _record_probed_urls(monkeypatch)

    r = _fetch_available_models(app, {"connection_id": cid})
    assert r.status_code == 200, r.text
    assert calls == ["https://gw.test/openai/v1/models"]


def test_connection_path_pool_miss_falls_back_to_connection_base_url(
    tmp_path: Path, monkeypatch
):
    """池里没有该协议 ⇒ 回落**连接主地址**（与 ``resolve_effective`` 同口径）。"""
    app = _create_app(tmp_path)
    cid = _create_pool_connection(
        app, protocol_base_urls={"anthropic": "https://gw.test/anthropic"}
    )
    calls = _record_probed_urls(monkeypatch)

    r = _fetch_available_models(app, {"connection_id": cid})
    assert r.status_code == 200, r.text
    assert calls == ["https://gw.test/main/models"]


def test_connection_path_unknown_protocol_returns_422(tmp_path: Path):
    """未知协议 ⇒ 422（不静默回落主协议：那是打到**错误协议面**的错址）。"""
    app = _create_app(tmp_path)
    cid = _create_pool_connection(app)

    r = _fetch_available_models(app, {"connection_id": cid, "provider": "responses"})
    assert r.status_code == 422, r.text
    assert "协议" in r.text


def test_connection_path_conflicting_protocol_and_provider_returns_422(tmp_path: Path):
    """``protocol`` 与 ``provider`` 同传且不一致 ⇒ 422（不静默择一）。"""
    app = _create_app(tmp_path)
    cid = _create_pool_connection(app)

    r = _fetch_available_models(
        app, {"connection_id": cid, "provider": "anthropic", "protocol": "ollama"}
    )
    assert r.status_code == 422, r.text


def test_protocol_without_connection_id_returns_422(tmp_path: Path):
    """``protocol`` 只在连接路径下有意义 ⇒ inline 路径显式拒绝（不静默忽略）。"""
    app = _create_app(tmp_path)

    r = _fetch_available_models(app, {"provider": "mock", "protocol": "mock"})
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 连接路径的**密钥解析链**（2026-10-24 审查修复 M3：零覆盖分支补测试）
# ---------------------------------------------------------------------------


def test_connection_path_without_api_key_resolves_without_500(tmp_path: Path, monkeypatch):
    """★连接 params **不含 api_key** ⇒ 该分支曾一次没跑过（旧实现 ⇒ 500）。

    旧实现里 ``profile_id`` 只在 inline 分支初始化，而密钥解析两条路径共用它
    ⇒ 连接路径在「连接 params 没有 api_key」时抛 UnboundLocalError ⇒ **500**。
    现有夹具的连接 params 全塞了 ``api_key`` ⇒ 该分支从未被执行。

    修复后：走 ``resolve_api_key(..., connection_id=...)`` 解析链。
    本用例把解析链的环境 / secrets 两侧都置空 ⇒ 解析结果为 None
    ⇒ 请求**不带** Authorization 头（布尔口径；**不断言任何凭据值**）。
    """
    app = _create_app(tmp_path)
    cid = _create_pool_connection(app, params={})
    calls = _record_probed_requests(monkeypatch)
    # 解析链两侧置空 ⇒ 「无 Authorization 头」是确定结论（不依赖环境偶然状态）
    monkeypatch.delenv("NOVELOS_API_KEY_OPENAI_COMPATIBLE", raising=False)
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(tmp_path / "absent_secrets.json"))

    r = _fetch_available_models(app, {"connection_id": cid})
    assert r.status_code == 200, r.text  # 旧实现在这里 500（UnboundLocalError）
    assert r.json() == {"models": ["m-1"]}
    assert len(calls) == 1
    assert calls[0]["has_authorization"] is False
    # 取址口径没被这次修复带偏：仍走协议池里的**主协议**平面
    assert calls[0]["url"] == "https://gw.test/openai/v1/models"


def test_connection_path_resolves_api_key_from_secrets_by_connection_id(
    tmp_path: Path, monkeypatch
):
    """★凭据**只存在 secrets.json**（连接 params 无 key）⇒ 按 connection_id 命中。

    钉住 ``resolve_api_key(..., connection_id=conn_id)`` 这个入参：绑定态
    **不查档案级 secrets 条目**、只查连接级 ⇒ 命中即带 Authorization 头。
    ⚠️ 断言只打**布尔**（是否带该头），**不断言密钥值**；secrets 文件里放的是
    假占位符（仓外临时目录，非真凭据）。
    """
    app = _create_app(tmp_path)
    cid = _create_pool_connection(app, params={})
    secrets_path = tmp_path / "secrets_probe.json"
    secrets_path.write_text(
        json.dumps({"api_keys": {cid: "{{API_KEY_probe_placeholder}}"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("NOVELOS_SECRETS_FILE", str(secrets_path))
    monkeypatch.delenv("NOVELOS_API_KEY_OPENAI_COMPATIBLE", raising=False)
    from packages.core import secrets_store

    secrets_store.reset_cache()
    calls = _record_probed_requests(monkeypatch)

    r = _fetch_available_models(app, {"connection_id": cid})
    assert r.status_code == 200, r.text
    assert calls[0]["has_authorization"] is True


def test_connection_path_with_api_key_sends_authorization_header(
    tmp_path: Path, monkeypatch
):
    """对照组：连接 params **有** api_key ⇒ 请求带 Authorization 头（布尔口径）。

    与上一条构成正反一对 ⇒ 「带不带该头」由**解析结果**决定，不是恒定行为。
    """
    app = _create_app(tmp_path)
    cid = _create_pool_connection(app)  # 默认 params 带 api_key
    calls = _record_probed_requests(monkeypatch)

    r = _fetch_available_models(app, {"connection_id": cid})
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    assert calls[0]["has_authorization"] is True
