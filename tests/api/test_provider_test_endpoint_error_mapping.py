"""`/model-profiles/{id}/test` 与 `/model-configs/{id}/test` 的错误映射（2026-10-15 批次六）。

修的缺陷
--------
两个 `/test` 端点原先写成::

    provider = ModelRouter(...).get_provider(config_row)   # ← 在 try 之外
    try:
        result = provider.health_check()
    except ProviderError as exc:
        raise HTTPException(502, ...)

而 ``get_provider`` 会抛 ``ValueError``（provider 配置不合法，例如
``openai_compatible`` 缺 ``base_url``——见 ``model_router/providers.py`` 的构造校验）。
那个 ``ValueError`` **没有被捕获** ⇒ FastAPI 回 **500，且 Content-Type 是
``text/plain``**，响应体是裸的 ``Internal Server Error``。

为什么这不是「小瑕疵」：前端 ``apps/web/src/api/client.ts`` 统一按 JSON 的
``detail`` 解析错误，非 JSON 会让它在**错误展示**这一步崩掉——即「配置写错了」
这条最容易自我修复的路径，反而最难让人看见。

⚠️ 与 AGENTS 坑区「夹具永远不触发闸门 ⇒ 空断言」同源：本组用例**不得**只断言
「不再 500」——那样把 ``except Exception: raise HTTPException(500)`` 也算过。
所以配**正反一对**：

* 正向：配置无效 ⇒ **422 + JSON**，且 detail 说清是哪个实体的哪项配置；
* 反向：上游真连不上 ⇒ 仍是 **502 + JSON**（没有被新 except 吃掉、也没被降级）。

第三条守「不要把 provider 配置问题误报成上游故障」这条语义边界。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings


def _app(tmp_path: Path):
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))


async def _req(app, method: str, path: str, **kw) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.request(method, path, **kw)


def _seed_profile_invalid(app) -> str:
    async def go() -> str:
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                r = await c.post(
                    "/api/model-profiles",
                    json={
                        "name": "缺 base_url",
                        "provider": "openai_compatible",
                        "model": "some-model",
                        "params_json": {},
                    },
                )
                assert r.status_code in (200, 201), r.text
                return r.json()["profile_id"]

    return asyncio.run(go())


def _seed_config_invalid(app) -> str:
    async def go() -> str:
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                r = await c.post(
                    "/api/model-configs",
                    json={
                        "name": "缺 base_url",
                        "provider": "openai_compatible",
                        "model": "some-model",
                        "capability": "creative_writing",
                        "params_json": {},
                    },
                )
                assert r.status_code in (200, 201), r.text
                return r.json()["config_id"]

    return asyncio.run(go())


def _assert_json_422(r: httpx.Response, what: str) -> None:
    assert r.status_code == 422, f"{what} 应回 422（配置无效），实回 {r.status_code}：{r.text[:200]}"
    assert "application/json" in r.headers.get("content-type", ""), (
        f"{what} 的响应必须是 JSON（前端按 detail 解析），"
        f"实得 Content-Type={r.headers.get('content-type')}"
    )
    detail = r.json()["detail"]
    assert isinstance(detail, str) and "配置无效" in detail, (
        f"{what} 的 detail 应当是讲清「provider 配置无效」的字符串，实得 {detail!r}"
    )


def test_profile_test_with_invalid_provider_config_returns_422_json(tmp_path: Path):
    """正向：`openai_compatible` 缺 base_url ⇒ 422 + JSON（不再是 500 纯文本）。"""
    app = _app(tmp_path)
    pid = _seed_profile_invalid(app)
    r = asyncio.run(_req(app, "POST", f"/api/model-profiles/{pid}/test"))
    _assert_json_422(r, "profile /test")
    assert pid in r.json()["detail"], "detail 应点名是哪个实体"


def test_config_test_with_invalid_provider_config_returns_422_json(tmp_path: Path):
    """正向：同款缺陷在 model_configs 侧，修了一样。"""
    app = _app(tmp_path)
    cid = _seed_config_invalid(app)
    r = asyncio.run(_req(app, "POST", f"/api/model-configs/{cid}/test"))
    _assert_json_422(r, "config /test")
    assert cid in r.json()["detail"], "detail 应点名是哪个实体"


def test_provider_test_still_reports_unreachable_upstream_as_data(tmp_path: Path):
    """反向：上游连不上 ⇒ **仍是 200 + `ok: false`**，没被新 except 改变。

    ⚠️ 我最初把这条写成「应回 502」——**实测证伪了我自己的前提**：
    ``health_check()`` 对网络异常是**返回** ``{ok: false, detail: "network error…"}``，
    不是抛 ``ProviderError``。所以「连不上」在既有契约里是**一次成功的健康检查**，
    端点回 200 并把失败写进 body。

    这条用例守的正是这个既有语义**没被我的修复改动**：新增的 ``except ValueError``
    绝不能把「连不上」也收进来变成 422/500——那是把「上游挂了」误报成
    「你的配置写错了」，作者会去改一个没问题的档案。
    """
    app = _app(tmp_path)

    async def go() -> str:
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                r = await c.post(
                    "/api/model-profiles",
                    json={
                        "name": "指向黑洞的 base_url",
                        "provider": "openai_compatible",
                        "model": "m",
                        # 配置本身合法 ⇒ 走得到 health_check，只是连不上
                        "params_json": {"base_url": "http://127.0.0.1:9/none"},
                    },
                )
                assert r.status_code in (200, 201), r.text
                return r.json()["profile_id"]

    pid = asyncio.run(go())
    r = asyncio.run(_req(app, "POST", f"/api/model-profiles/{pid}/test"))
    assert r.status_code == 200, (
        f"上游不可达在既有契约里是 200 + ok=false（health_check 返回而非抛错），"
        f"实回 {r.status_code}：{r.text[:200]}"
    )
    body = json.loads(r.text)
    assert body["ok"] is False, body
    assert "配置无效" not in (body.get("detail") or ""), (
        f"「连不上」不得被误报成「配置无效」：{body!r}"
    )


def test_invalid_config_detail_names_the_offending_field(tmp_path: Path):
    """语义边界：detail 应当说清是**配置**问题，而不是笼统的「失败」。

    这是第 4 条断言存在的原因——只断言 422 的话，一个
    ``raise HTTPException(422, "bad request")`` 也能过，
    而作者看到后仍然不知道该去改哪一行。
    """
    app = _app(tmp_path)
    pid = _seed_profile_invalid(app)
    r = asyncio.run(_req(app, "POST", f"/api/model-profiles/{pid}/test"))
    detail = r.json()["detail"]
    assert "base_url" in detail, f"detail 应点名缺失的字段，实得 {detail!r}"
