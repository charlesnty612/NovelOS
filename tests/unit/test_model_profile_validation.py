"""model-profile 写入侧形状校验 + unknown-model 提示（task-28）。

背景：该 400 的定性是**运行期配置事故**（远端模型上下线 / 账号无权访问），
不是数据填错。本批在两处补结构性防复发：

1. **写入侧**——``model`` 原判断是 ``isinstance(v, str) and v``，而纯空白
   （``"   "``）是 truthy 字符串，会被直接放行落库；到调用时远端才以
   unknown model 400 收场。现改为 strip 后判空，含前后空白则归一化落库 +
   ``log.warning`` 留痕（原值被静默改写，排查需要能看出是谁写的）。
2. **读出侧**——``/test`` 的 400 若错误文本指向模型名，附中文提示（新增可选
   字段 ``hint``）；不命中时响应体与本批之前**逐字一致**（正常错误继续透传）。

形状校验**只管「非空且非纯空白」**：不建本地模型名表——可用模型随部署 / 账号 /
上下线变化，硬编码白名单必然过期（AGENTS.md「可调项不得硬编码」）。名字对错
一律由远端响应判定。

全部用 tmp_path 临时库 + ASGI in-process 调用：零网络、零真实 key、零 LLM。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        return await client.request(method, path, **kwargs)


def _create_profile(app, *, model: str, provider: str = "mock", **extra) -> httpx.Response:
    async def run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            return await _request(
                app, "POST", "/api/model-profiles",
                json={"name": "alpha", "provider": provider, "model": model, **extra},
            )

    return asyncio.run(run())


class _FakeProvider:
    """替身 provider：只回放构造好的 health_check 结果，绝不发外网。"""

    def __init__(self, result: dict) -> None:
        self._result = result

    def health_check(self, **kwargs) -> dict:
        return self._result


def _patch_provider(monkeypatch, result: dict) -> None:
    from packages.core.model_router import ModelRouter

    monkeypatch.setattr(
        ModelRouter, "get_provider", lambda self, config_row: _FakeProvider(result)
    )


# ---------------------------------------------------------------------------
# 1. 写入侧：strip 归一化
# ---------------------------------------------------------------------------


def test_model_field_is_stripped_on_create(tmp_path: Path):
    """model 含前后空白 → 落库为归一化后的值（不再把原样空白写进档案）。"""
    app = _create_app(tmp_path)

    r = _create_profile(app, model="  gpt-4o  ")
    assert r.status_code == 201, r.text
    pid = r.json()["profile_id"]
    assert r.json()["model"] == "gpt-4o", "响应即归一化后的值"

    async def read_back() -> httpx.Response:
        async with app.router.lifespan_context(app):
            return await _request(app, "GET", f"/api/model-profiles/{pid}")

    got = asyncio.run(read_back())
    assert got.status_code == 200, got.text
    assert got.json()["model"] == "gpt-4o", "落库值必须已 strip（读回确认，非仅响应）"


def test_model_field_is_stripped_on_patch(tmp_path: Path):
    """PATCH 同样 strip——两个写入口口径必须一致，否则同一字段两套规则。"""
    app = _create_app(tmp_path)
    pid = _create_profile(app, model="gpt-4o").json()["profile_id"]

    async def run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            return await _request(
                app, "PATCH", f"/api/model-profiles/{pid}", json={"model": "\tgpt-4o-mini\n"}
            )

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# 2. 写入侧：拒绝纯空白（原判断的漏网形态）
# ---------------------------------------------------------------------------


def test_create_rejects_pure_whitespace_model(tmp_path: Path):
    """纯空白 model → 422。原判断 ``and v`` 对 truthy 的 ``"   "`` 放行。"""
    app = _create_app(tmp_path)
    r = _create_profile(app, model="   ")
    assert r.status_code == 422, f"纯空白必须拒收，实得 {r.status_code}: {r.text}"
    assert "纯空白" in r.text


def test_create_rejects_tab_newline_only_model(tmp_path: Path):
    """制表符 / 换行组成的「视觉为空」同样拒收——strip 口径覆盖所有空白字符。"""
    app = _create_app(tmp_path)
    r = _create_profile(app, model="\t\n  \n")
    assert r.status_code == 422, r.text


def test_patch_rejects_pure_whitespace_model(tmp_path: Path):
    app = _create_app(tmp_path)
    pid = _create_profile(app, model="gpt-4o").json()["profile_id"]

    async def run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            return await _request(
                app, "PATCH", f"/api/model-profiles/{pid}", json={"model": "  "}
            )

    r = asyncio.run(run())
    assert r.status_code == 422, r.text


def test_rejected_pure_whitespace_writes_nothing(tmp_path: Path):
    """拒收必须**真的没写库**——否则 422 之后档案已被污染成空模型名。"""
    app = _create_app(tmp_path)
    assert _create_profile(app, model="   ").status_code == 422

    async def run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            return await _request(app, "GET", "/api/model-profiles")

    listed = asyncio.run(run())
    assert listed.status_code == 200, listed.text
    assert listed.json() == [], f"拒收后不得留下任何档案，实得 {listed.json()!r}"


# ---------------------------------------------------------------------------
# 3. 读出侧：unknown-model 提示（400 专属）
# ---------------------------------------------------------------------------


def test_unknown_model_400_gets_chinese_hint(tmp_path: Path, monkeypatch):
    """远端 400 且错误文本指向模型名 → 响应附中文 hint。"""
    app = _create_app(tmp_path)
    pid = _create_profile(app, model="gpt-nope", provider="openai").json()["profile_id"]
    _patch_provider(monkeypatch, {
        "ok": False,
        "status_code": 400,
        "latency_ms": 42,
        "detail": 'HTTP 400: {"error":{"message":"The model `gpt-nope` does not exist"}}',
    })

    async def run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            return await _request(app, "POST", f"/api/model-profiles/{pid}/test")

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is False and data["status_code"] == 400
    assert "hint" in data, "unknown model 必须给出明确提示"
    assert "模型名" in data["hint"], f"提示须为中文说明模型名问题，实得 {data['hint']!r}"
    # 既有 5 字段逐字不变（提示是新增，不是把原文改写）
    assert "does not exist" in data["detail"], "远端原文必须原样透传"


def test_non_400_errors_get_no_hint(tmp_path: Path, monkeypatch):
    """401 / 5xx **不加** hint——把它们归成「模型名」是误诊，属提示噪声。"""
    app = _create_app(tmp_path)
    pid = _create_profile(app, model="gpt-4o", provider="openai").json()["profile_id"]
    _patch_provider(monkeypatch, {
        "ok": False,
        "status_code": 401,
        "latency_ms": 7,
        "detail": "HTTP 401: invalid api key",
    })

    async def run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            return await _request(app, "POST", f"/api/model-profiles/{pid}/test")

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    data = r.json()
    assert "hint" not in data, f"401 属鉴权问题，不得挂模型名提示：{data!r}"


def test_non_400_with_marker_text_still_gets_no_hint(tmp_path: Path, monkeypatch):
    """**可判别**用例：非 400，且错误文本**确实含**模型名 marker → 仍不得挂 hint。

    与上一条 `test_non_400_errors_get_no_hint` 的分工：本条的 detail 含 marker，
    所以它真正判别的是 `_unknown_model_hint` 外层那道 `status_code == 400` 闸门
    （`model_profiles.py:375`）——闸门一拆，hint 必挂，本条必红。

    上一条不是无用：它守住「401 原文不含 marker 时不挂噪声提示」。但它**单独不足以**
    守住那道闸门——它的 detail（`HTTP 401: invalid api key`）不含任何 marker，
    `_unknown_model_hint` 在进闸门判断前就提前返回 None 了，于是把闸门整个拆掉它
    照样绿（2026-10-13 突变验证实测：改 `if status_code == 400:` → `if True:` 时
    13 条全绿，突变存活）。**这是「断言了结果 ≠ 判别了机制」的典型形状**，
    登记在此以防后人误以为上一条已覆盖闸门。
    """
    app = _create_app(tmp_path)
    pid = _create_profile(app, model="gpt-4o", provider="openai").json()["profile_id"]
    _patch_provider(monkeypatch, {
        "ok": False,
        "status_code": 401,
        "latency_ms": 7,
        "detail": "HTTP 401: model does not exist for this api key",
    })

    async def run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            return await _request(app, "POST", f"/api/model-profiles/{pid}/test")

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    data = r.json()
    assert "does not exist" in data["detail"], "前置：远端原文必须原样透传"
    assert "hint" not in data, (
        f"401 属鉴权问题，即便文本含模型名也不得挂提示（闸门在 status_code 判断处）：{data!r}"
    )


def test_unrelated_400_gets_no_hint(tmp_path: Path, monkeypatch):
    """400 但错误文本与模型名无关（如参数 schema 错）→ 不加 hint，保持原样。"""
    app = _create_app(tmp_path)
    pid = _create_profile(app, model="gpt-4o", provider="openai").json()["profile_id"]
    _patch_provider(monkeypatch, {
        "ok": False,
        "status_code": 400,
        "latency_ms": 9,
        "detail": "HTTP 400: {\"error\":{\"message\":\"messages: field required\"}}",
    })

    async def run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            return await _request(app, "POST", f"/api/model-profiles/{pid}/test")

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    assert "hint" not in r.json(), f"无关 400 不得被误标为模型名问题：{r.json()!r}"


# ---------------------------------------------------------------------------
# 4. /model-configs 对齐（task-33）
#
# 两个写入口曾**同形**漏校验（`isinstance(v, str) and v` 放行纯空白）。task-28 只修
# 了 /model-profiles 一边，反而制造了口径分叉——本节钉住对齐后的共同口径。
# ---------------------------------------------------------------------------


def _create_config(app, *, model: str, capability: str = "reasoning") -> httpx.Response:
    async def run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            return await _request(
                app, "POST", "/api/model-configs",
                json={"capability": capability, "provider": "mock", "model": model},
            )

    return asyncio.run(run())


def test_model_configs_rejects_pure_whitespace_model(tmp_path: Path):
    """/model-configs 与 /model-profiles 同口径：纯空白 model → 422。"""
    app = _create_app(tmp_path)
    r = _create_config(app, model="   ")
    assert r.status_code == 422, f"纯空白必须拒收，实得 {r.status_code}: {r.text}"
    assert "纯空白" in r.text


def test_model_configs_strips_model_on_create(tmp_path: Path):
    """/model-configs 同样 strip 归一化（两个写入口口径一致）。

    与 profile 侧 `test_model_field_is_stripped_on_create` 对齐守卫强度：
    **读回确认落库值**，不只看响应体——「响应归一化」与「落库归一化」是两件事
    （AGENTS 铁律 9 的形状：表里有值 ≠ 值已进该去的地方）。
    """
    app = _create_app(tmp_path)
    r = _create_config(app, model="  gpt-4o  ")
    assert r.status_code == 201, r.text
    assert r.json()["model"] == "gpt-4o", f"响应应归一化，实得 {r.json()['model']!r}"
    config_id = r.json()["config_id"]

    async def read_back() -> httpx.Response:
        async with app.router.lifespan_context(app):
            return await _request(app, "GET", f"/api/model-configs/{config_id}")

    got = asyncio.run(read_back())
    assert got.status_code == 200, got.text
    assert got.json()["model"] == "gpt-4o", "落库值必须已 strip（读回确认，非仅响应）"


def test_model_configs_patch_rejects_pure_whitespace_model(tmp_path: Path):
    app = _create_app(tmp_path)
    cfg_id = _create_config(app, model="gpt-4o").json()["config_id"]

    async def run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            return await _request(
                app, "PATCH", f"/api/model-configs/{cfg_id}", json={"model": " \t "}
            )

    r = asyncio.run(run())
    assert r.status_code == 422, r.text


def test_both_write_endpoints_agree_on_whitespace(tmp_path: Path):
    """跨端点一致性看守：同一输入两个写入口必须给同一结论（防再次分叉）。

    这是本节的核心断言——前面几条各自钉一个端点，本条钉的是「两者相同」。
    """
    app = _create_app(tmp_path)
    profiles_resp = _create_profile(app, model="  ")
    configs_resp = _create_config(app, model="  ")
    assert profiles_resp.status_code == configs_resp.status_code, (
        f"两写入口对纯空白给出不同结论："
        f"profiles={profiles_resp.status_code} configs={configs_resp.status_code}"
    )
    assert profiles_resp.status_code == 422, (
        f"共同结论应为 422（拒收），实得 {profiles_resp.status_code}"
    )
