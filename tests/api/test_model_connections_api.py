"""Model Connections 路由测试（2026-10-21 批次十五「连接层」）。

覆盖：
1. CRUD 路径 + 读路径脱敏（``api_key`` → ``***``；``headers`` 里的**敏感头逐键**脱敏；
   ``x-opencode-session`` 这类非凭据头**不掩**）。
2. 写时校验：参数白名单 / headers 形状 / **鉴权头与 api_key 撞车** /
   **协议地址池形状** ⇒ 422。
3. DELETE 被档案引用 → 409（``detail.referenced_by``）。
4. 档案绑定连接：**协议下放档案**（2026-10-24）——档案传与连接主协议**不同**的
   provider ⇒ **201 成功**（原 422「不一致」校验已删）；**首次绑定**不传 provider ⇒
   回退连接主协议（存量兼容），**改绑**不传 provider ⇒ 档案协议保持原值（2026-10-24
   审查修复 C1）；``connection_id: null`` ⇒ 解绑。
5. **备份红线**：``model_connections`` **不在**导出白名单里（ADR-0002 §4）。
6. 归组：分组预览 + 应用（生效密钥不一致 ⇒ 422，归组**不改密钥**）。
7. 协议地址池（``protocol_base_urls``）：合法落库往返 / 非法键 422 / PATCH 清空；
   预设 opencode_go 自带双协议地址池（逐字断言）。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.backup.schema import EXPORTED_TABLES
from packages.core.db import get_connection

from ._shared import _create_app, _request

_SECRET = "sk-connection-secret-12345"






def _post_connection(app, **overrides):
    body = {
        "name": "opencode",
        "provider": "openai_compatible",
        "base_url": "https://opencode.ai/inference/go/openai/v1",
        "params": {"api_key": _SECRET},
    }
    body.update(overrides)
    return _post_connection_sync(app, body)


def _post_connection_sync(app, body):
    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            return await _request(app, "POST", "/api/model-connections", json=body)

    return asyncio.run(run())


# ---------------------------------------------------------------------------
# 1. CRUD + 脱敏
# ---------------------------------------------------------------------------


def test_create_connection_masks_api_key(tmp_path: Path):
    """POST 含明文 api_key → 响应 params_json.api_key 是 *** 且 has_api_key=True。"""
    app = _create_app(tmp_path)
    r = _post_connection(app)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["params_json"]["api_key"] == "***"
    assert body["has_api_key"] is True
    assert body["provider"] == "openai_compatible"
    assert body["base_url"] == "https://opencode.ai/inference/go/openai/v1"
    assert body["enabled"] == 1


def test_headers_sensitive_values_masked_but_session_header_visible(tmp_path: Path):
    """★headers 里的敏感头逐键脱敏；``x-opencode-session`` **不掩**（用户要能核对模板）。

    ⚠️ 本用例**不配** api_key —— 既配 key 又写鉴权头会被撞车校验拒掉（422，另有用例）。
    """
    app = _create_app(tmp_path)
    r = _post_connection(
        app,
        params={
            "headers": {
                "authorization": "Bearer leak-me",
                "x-api-key": "leak-me-too",
                "x-opencode-session": "{{session_id}}",
            },
        },
    )
    assert r.status_code == 201, r.text
    headers = r.json()["params_json"]["headers"]
    assert headers["authorization"] == "***"
    assert headers["x-api-key"] == "***"
    assert headers["x-opencode-session"] == "{{session_id}}", "非凭据头不该被掩"


def test_list_and_get_connection(tmp_path: Path):
    app = _create_app(tmp_path)
    created = _post_connection(app).json()
    r = _post_connection_sync(app, {"name": "x"}) if False else None  # noqa: F841
    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            lst = await _request(app, "GET", "/api/model-connections")
            one = await _request(
                app, "GET", f"/api/model-connections/{created['connection_id']}"
            )
            missing = await _request(app, "GET", "/api/model-connections/mconn_nope")
        return lst, one, missing

    lst, one, missing = asyncio.run(run())
    assert lst.status_code == 200
    assert len(lst.json()) == 1
    assert one.status_code == 200
    assert one.json()["connection_id"] == created["connection_id"]
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# 2. 写时校验
# ---------------------------------------------------------------------------


def test_rejects_params_outside_whitelist(tmp_path: Path):
    """连接级 params 只允许 api_key / headers / models_url ⇒ 其它键 422。"""
    app = _create_app(tmp_path)
    r = _post_connection(app, params={"api_key": "k", "temperature": 0.5})
    assert r.status_code == 422
    assert "temperature" in r.text


def test_rejects_header_injection_and_unknown_placeholder(tmp_path: Path):
    """header 注入与未知占位符 ⇒ 422（写时拦，不静默发出去）。"""
    app = _create_app(tmp_path)
    r1 = _post_connection(app, params={"headers": {"x-a": "a\r\nX-B: b"}})
    assert r1.status_code == 422
    r2 = _post_connection(app, params={"headers": {"x-a": "{{foo}}"}})
    assert r2.status_code == 422


def test_rejects_auth_header_clashing_with_api_key(tmp_path: Path):
    """★既有 api_key 又写鉴权头 ⇒ 422（二选一）。

    否则自定义头会静默架空密钥通道，而 has_api_key 仍显示 true。
    """
    app = _create_app(tmp_path)
    r = _post_connection(
        app, params={"api_key": "sk-1", "headers": {"authorization": "Bearer other"}}
    )
    assert r.status_code == 422
    assert "冲突" in r.text or "二选一" in r.text


def test_openai_compatible_requires_base_url(tmp_path: Path):
    app = _create_app(tmp_path)
    r = _post_connection(app, base_url=None)
    assert r.status_code == 422
    assert "base_url" in r.text


# ---------------------------------------------------------------------------
# 2b. 协议地址池（protocol_base_urls，2026-10-24）
# ---------------------------------------------------------------------------


_POOL = {
    "openai_compatible": "https://opencode.ai/inference/go/openai/v1",
    "anthropic": "https://opencode.ai/inference/go/anthropic",
}


def test_protocol_pool_roundtrips(tmp_path: Path):
    """★合法协议池：POST 落库 → GET 读回**逐字**往返（出参是对象不是 JSON 串）。"""
    app = _create_app(tmp_path)
    created = _post_connection(app, protocol_base_urls=dict(_POOL)).json()
    assert created["protocol_base_urls"] == _POOL
    assert created["protocol_base_urls"]["anthropic"] == (
        "https://opencode.ai/inference/go/anthropic"
    )

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            return await _request(
                app, "GET", f"/api/model-connections/{created['connection_id']}"
            )

    r = asyncio.run(run())
    assert r.status_code == 200
    assert r.json()["protocol_base_urls"] == _POOL


def test_protocol_pool_rejects_unknown_key_and_empty_value(tmp_path: Path):
    """★非法键（非四协议）/ 空串值 ⇒ 422（写时拦，不收下即忘）。"""
    app = _create_app(tmp_path)
    r1 = _post_connection(app, protocol_base_urls={"responses": "https://x.test/responses"})
    assert r1.status_code == 422, r1.text
    assert "协议" in r1.text
    r2 = _post_connection(app, protocol_base_urls={"anthropic": "   "})
    assert r2.status_code == 422, r2.text
    # 非对象形状同样 422
    r3 = _post_connection(app, protocol_base_urls="https://not-an-object")
    assert r3.status_code == 422, r3.text


def test_protocol_pool_patch_replace_and_clear(tmp_path: Path):
    """★PATCH：dict = 整表替换；显式 null = 清空（落库 NULL，出参 null）。"""
    app = _create_app(tmp_path)
    created = _post_connection(app, protocol_base_urls=dict(_POOL)).json()
    cid = created["connection_id"]

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            replaced = await _request(
                app,
                "PATCH",
                f"/api/model-connections/{cid}",
                json={"protocol_base_urls": {"anthropic": "https://other.test/anthropic"}},
            )
            cleared = await _request(
                app, "PATCH", f"/api/model-connections/{cid}",
                json={"protocol_base_urls": None},
            )
            reread = await _request(app, "GET", f"/api/model-connections/{cid}")
        return replaced, cleared, reread

    replaced, cleared, reread = asyncio.run(run())
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["protocol_base_urls"] == {
        "anthropic": "https://other.test/anthropic"
    }, "PATCH dict 必须整表替换（不是逐键合并）"
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["protocol_base_urls"] is None
    assert reread.json()["protocol_base_urls"] is None
    # DB 实际列值：清空后必须为 NULL
    conn = get_connection(app.state.settings.db_path)
    try:
        row = conn.execute(
            "SELECT protocol_base_urls FROM model_connections WHERE connection_id = ?",
            (cid,),
        ).fetchone()
    finally:
        conn.close()
    assert row["protocol_base_urls"] is None


def test_connection_without_pool_returns_null(tmp_path: Path):
    """不传协议池 ⇒ 出参恒为 null（不出 JSON 串 / 不出空 dict）。"""
    app = _create_app(tmp_path)
    created = _post_connection(app).json()
    assert created["protocol_base_urls"] is None


# ---------------------------------------------------------------------------
# 3. 删除守卫
# ---------------------------------------------------------------------------


def test_delete_connection_referenced_by_profiles_returns_409(tmp_path: Path):
    """被档案引用 ⇒ 409 + referenced_by（不做静默解绑）。"""
    app = _create_app(tmp_path)
    created = _post_connection(app).json()
    conn_id = created["connection_id"]

    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute(
            """
            INSERT INTO model_profiles
                (profile_id, name, provider, model, params_json, enabled,
                 created_at, updated_at, connection_id)
            VALUES ('mprof_x', 'x', 'openai_compatible', 'm', '{}', 1, 't', 't', ?)
            """,
            (conn_id,),
        )
        conn.commit()
    finally:
        conn.close()

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            return await _request(app, "DELETE", f"/api/model-connections/{conn_id}")

    r = asyncio.run(run())
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "connection_in_use"
    assert "mprof_x" in detail["referenced_by"]


# ---------------------------------------------------------------------------
# 4. 档案绑定连接
# ---------------------------------------------------------------------------


def test_profile_binding_accepts_provider_different_from_connection(tmp_path: Path):
    """★协议下放档案（2026-10-24）：绑定连接时档案传**不同于**连接主协议的
    provider ⇒ **201 成功**（原 422「provider 与所选连接不一致」校验已删）。

    判别：同厂商网关下不同模型声明不同协议（opencode Go：GLM 走 openai 平面、
    Claude 系走 anthropic 平面）——旧校验把这种合法配置挡在 422。
    落库值必须是请求体的 provider（不是连接的主协议）。
    """
    app = _create_app(tmp_path)
    created = _post_connection(app).json()
    conn_id = created["connection_id"]

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            return await _request(
                app,
                "POST",
                "/api/model-profiles",
                json={
                    "name": "p",
                    "provider": "anthropic",
                    "model": "claude-opus",
                    "connection_id": conn_id,
                },
            )

    r = asyncio.run(run())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["provider"] == "anthropic", (
        "档案 provider 必须原样落库（协议是模型级属性，不得被连接覆盖）"
    )
    assert body["connection_id"] == conn_id
    # DB 实际列值（断言打在真实值上，不是只看响应）
    conn = get_connection(app.state.settings.db_path)
    try:
        row = conn.execute(
            "SELECT provider FROM model_profiles WHERE profile_id = ?",
            (body["profile_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["provider"] == "anthropic"


def test_profile_binding_without_provider_falls_back_to_connection(tmp_path: Path):
    """★**首次绑定**连接时不传 provider ⇒ 回退连接主协议（兼容旧前端/脚本）。

    2026-10-24 审查修复 C1 后的语义界定：回退**只对未绑定档案**（此前
    ``connection_id IS NULL``）生效；已绑定档案改绑不传 provider ⇒ 不动协议
    （见 :func:`test_rebinding_keeps_profile_provider_when_provider_not_given`）。
    """
    app = _create_app(tmp_path)
    created = _post_connection(app).json()
    conn_id = created["connection_id"]

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            # PATCH 绑定一条先建的独立档案，请求体不带 provider
            made = await _request(
                app,
                "POST",
                "/api/model-profiles",
                json={"name": "p", "provider": "mock", "model": "m1"},
            )
            pid = made.json()["profile_id"]
            return await _request(
                app,
                "PATCH",
                f"/api/model-profiles/{pid}",
                json={"connection_id": conn_id},
            )

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    assert r.json()["provider"] == "openai_compatible", (
        "绑定请求体没给 provider ⇒ 回退为连接主协议"
    )


def test_rebinding_keeps_profile_provider_when_provider_not_given(tmp_path: Path):
    """★改绑不动协议（2026-10-24 审查修复 C1，判别用例）。

    已绑定档案 PATCH 改绑另一条连接且请求**不带** provider ⇒ 档案 provider
    **保持原值**（协议是档案自持属性）。旧行为无条件回退连接主协议，实测：
    anthropic 档案改绑到 openai 主协议连接上，一次改绑就被静默改写成
    ``openai_compatible``。

    反例（未绑定档案首次绑定不带 provider ⇒ 回退，存量兼容语义保留）由
    :func:`test_profile_binding_without_provider_falls_back_to_connection` 钉住。
    """
    app = _create_app(tmp_path)
    conn_a = _post_connection(app, name="conn-openai-1").json()
    conn_b = _post_connection(
        app,
        name="conn-openai-2",
        base_url="https://opencode.ai/inference/go/openai/v2",
    ).json()

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            # 首建即绑定（POST 带 connection_id）：provider=anthropic 是档案自持协议
            made = await _request(
                app,
                "POST",
                "/api/model-profiles",
                json={
                    "name": "claude",
                    "provider": "anthropic",
                    "model": "claude-opus",
                    "connection_id": conn_a["connection_id"],
                },
            )
            pid = made.json()["profile_id"]
            # 改绑到另一条连接，请求体不带 provider
            return await _request(
                app,
                "PATCH",
                f"/api/model-profiles/{pid}",
                json={"connection_id": conn_b["connection_id"]},
            )

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    conn = get_connection(app.state.settings.db_path)
    try:
        row = conn.execute(
            "SELECT provider, connection_id FROM model_profiles WHERE profile_id = ?",
            (r.json()["profile_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["connection_id"] == conn_b["connection_id"], "改绑应落到新连接"
    assert row["provider"] == "anthropic", (
        "改绑不带 provider ⇒ 档案协议必须保持 anthropic（不得回退连接主协议）"
    )


def test_profile_binding_and_unbinding(tmp_path: Path):
    """绑定 ⇒ 档案带 connection_id；PATCH 传 null ⇒ 解绑。"""
    app = _create_app(tmp_path)
    created = _post_connection(app).json()
    conn_id = created["connection_id"]

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            made = await _request(
                app,
                "POST",
                "/api/model-profiles",
                json={
                    "name": "p",
                    "provider": "openai_compatible",
                    "model": "gpt-5.5",
                    "connection_id": conn_id,
                },
            )
            pid = made.json()["profile_id"]
            unbound = await _request(
                app,
                "PATCH",
                f"/api/model-profiles/{pid}",
                json={"connection_id": None},
            )
        return made, unbound

    made, unbound = asyncio.run(run())
    assert made.status_code == 201, made.text
    assert made.json()["connection_id"] == conn_id
    assert unbound.status_code == 200
    assert unbound.json()["connection_id"] is None


def test_profile_has_api_key_follows_the_connection_not_stale_copy(tmp_path: Path):
    """★绑定档案的 ``has_api_key`` 由**连接**派生。

    绑定后档案自己 params 里那份 key **不生效** ⇒ 若仍拿它判定，
    「连接上清空了 key」时前端仍显示「已配置密钥」（显示与实际失真）。
    """
    app = _create_app(tmp_path)
    created = _post_connection(app).json()
    conn_id = created["connection_id"]

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            made = await _request(
                app,
                "POST",
                "/api/model-profiles",
                json={
                    "name": "p",
                    "provider": "openai_compatible",
                    "model": "m",
                    # 档案自带一份陈旧的 key（存量形态）
                    "params": {"api_key": "sk-stale"},
                    "connection_id": conn_id,
                },
            )
            pid = made.json()["profile_id"]
            before = await _request(app, "GET", f"/api/model-profiles/{pid}")
            # 把连接上的 key 清空
            await _request(
                app,
                "PATCH",
                f"/api/model-connections/{conn_id}",
                json={"params": {"api_key": ""}},
            )
            after = await _request(app, "GET", f"/api/model-profiles/{pid}")
        return made, before, after

    made, before, after = asyncio.run(run())
    assert made.status_code == 201, made.text
    assert before.json()["has_api_key"] is True
    assert after.json()["has_api_key"] is False, (
        "连接上已清空密钥，档案却仍显示「已配置密钥」⇒ 显示与实际失真"
    )


def test_profile_test_endpoint_resolves_connection(tmp_path: Path):
    """★绑定连接的档案，``/test`` 端点要能正常构造 provider。

    ``/test`` 手工构造 config_row ⇒ **必须带上 connection_id**，
    否则绑定态档案（自身 params 里没有 base_url）会抛「provider 配置无效」422。
    """
    app = _create_app(tmp_path)
    created = _post_connection(app).json()
    conn_id = created["connection_id"]

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            made = await _request(
                app,
                "POST",
                "/api/model-profiles",
                json={
                    "name": "p",
                    "provider": "openai_compatible",
                    "model": "gpt-5.5",
                    "connection_id": conn_id,
                },
            )
            pid = made.json()["profile_id"]
            tested = await _request(app, "POST", f"/api/model-profiles/{pid}/test")
        return made, tested

    made, tested = asyncio.run(run())
    assert made.status_code == 201, made.text
    # 探测会真的发一次请求（上游不可达 ⇒ 200 + ok=false），但**不能**是
    # 422「provider 配置无效」——那说明连接没被解析。
    assert tested.status_code == 200, tested.text
    body = tested.json()
    assert "ok" in body
    assert "provider 配置无效" not in (body.get("detail") or "")


# ---------------------------------------------------------------------------
# 4.5 厂商预设
# ---------------------------------------------------------------------------


def test_presets_endpoint_is_not_swallowed_by_path_param(tmp_path: Path):
    """★``/model-connections/presets`` 必须声明在 ``/{connection_id}`` **之前**。

    否则 FastAPI 按声明顺序匹配 ⇒ ``presets`` 被当成 ``connection_id`` ⇒ 404。
    """
    app = _create_app(tmp_path)

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            return await _request(app, "GET", "/api/model-connections/presets")

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list) and body


def test_opencode_go_preset_carries_its_conventions(tmp_path: Path):
    """★opencode Go 预设必须自带「请求头 + 模型列表地址 + 双协议地址池」——
    用户不该自己管这些。"""
    app = _create_app(tmp_path)

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            return await _request(app, "GET", "/api/model-connections/presets")

    r = asyncio.run(run())
    presets = {p["preset_id"]: p for p in r.json()}
    assert "opencode_go" in presets
    p = presets["opencode_go"]
    assert p["provider"] == "openai_compatible"
    assert p["base_url"] == "https://opencode.ai/inference/go/openai/v1"
    # ★协议地址池逐字断言（2026-10-24：协议下放模型档案——同一网关双协议路径）
    assert p["protocol_base_urls"] == {
        "openai_compatible": "https://opencode.ai/inference/go/openai/v1",
        "anthropic": "https://opencode.ai/inference/go/anthropic",
    }, "opencode Go 的协议地址池必须与官方两协议路径逐字一致"
    assert p["params"]["headers"]["x-opencode-session"] == "{{session_id}}"
    assert p["params"]["models_url"] == "https://opencode.ai/inference/go/v1/models"
    # 预设**不含**密钥
    assert "api_key" not in p["params"]


def test_expected_presets_are_present(tmp_path: Path):
    """★用户点名的预设必须都在（防止以后有人悄悄删掉一条）。

    清单：国内 minimax / kimi code；国外 command code goat / opencode go / opendesign go。
    """
    app = _create_app(tmp_path)

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            return await _request(app, "GET", "/api/model-connections/presets")

    r = asyncio.run(run())
    ids = {p["preset_id"] for p in r.json()}
    for expected in (
        "minimax_cn",
        "kimi_code",
        "commandcode_goat",
        "opencode_go",
        "opencode_go_anthropic",
        "opendesign_go",
    ):
        assert expected in ids, f"预设 {expected} 不见了（现有：{sorted(ids)}）"


def test_opencode_go_anthropic_variant_uses_official_path(tmp_path: Path):
    """★opencode 官方：基础 URL ``/inference/go`` + ``/anthropic/v1/messages``。

    NovelOS 的 AnthropicProvider 拼 ``/v1/messages`` ⇒ base_url 到 ``/anthropic`` 为止。
    """
    app = _create_app(tmp_path)

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            return await _request(app, "GET", "/api/model-connections/presets")

    r = asyncio.run(run())
    presets = {p["preset_id"]: p for p in r.json()}
    p = presets["opencode_go_anthropic"]
    assert p["provider"] == "anthropic"
    assert p["base_url"] == "https://opencode.ai/inference/go/anthropic"
    assert p["params"]["headers"]["x-opencode-session"] == "{{session_id}}"


def test_commandcode_goat_carries_its_required_header(tmp_path: Path):
    """★Command Code GOAT 官方要求每个请求带 ``x-cmd-zdr: 1`` ⇒ 预设必须带上。

    （用户最初以为「只有 opencode 有请求头」，但 GOAT 套餐官方明确要求这个头，
    缺了会被拒 ⇒ 按官方要求内置，并在预设说明里标出。）
    """
    app = _create_app(tmp_path)

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            return await _request(app, "GET", "/api/model-connections/presets")

    r = asyncio.run(run())
    presets = {p["preset_id"]: p for p in r.json()}
    p = presets["commandcode_goat"]
    assert p["base_url"] == "https://api.commandcode.ai/provider/v1"
    assert p["params"]["headers"]["x-cmd-zdr"] == "1"
    assert p["params"]["models_url"] == "https://api.commandcode.ai/provider/v1/models"


def test_opendesign_go_leaves_base_url_blank_on_purpose(tmp_path: Path):
    """★OpenDesign 的 API 端点官方未公开固定值 ⇒ 预设**留空**（不编造地址）。"""
    app = _create_app(tmp_path)

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            return await _request(app, "GET", "/api/model-connections/presets")

    r = asyncio.run(run())
    presets = {p["preset_id"]: p for p in r.json()}
    p = presets["opendesign_go"]
    assert p["base_url"] is None
    assert p["note"], "留空地址必须给出说明（让用户知道去哪取）"


def test_presets_never_contain_api_key(tmp_path: Path):
    """全部预设都不得带 api_key（密钥只能由用户填 / 走 secrets.json）。"""
    app = _create_app(tmp_path)

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            return await _request(app, "GET", "/api/model-connections/presets")

    r = asyncio.run(run())
    for p in r.json():
        assert "api_key" not in p["params"], f"{p['preset_id']} 预设里混进了 api_key"
        assert set(p["params"]) <= {"headers", "models_url"}


# ---------------------------------------------------------------------------
# 5. 备份红线
# ---------------------------------------------------------------------------


def test_model_connections_not_in_export_whitelist():
    """★ADR-0002 §4 红线：含密钥的表不进导出包。

    ``model_connections.params_json.api_key`` 与 ``model_profiles`` 一样是明文存 DB，
    ⇒ **必须**不在导出白名单里（不能靠「白名单默认不包含」这个推理，要写成断言）。
    """
    assert "model_connections" not in EXPORTED_TABLES
    assert "model_profiles" not in EXPORTED_TABLES
    assert "model_configs" not in EXPORTED_TABLES


# ---------------------------------------------------------------------------
# 6. 归组
# ---------------------------------------------------------------------------


def test_group_preview_and_apply_keeps_effective_key(tmp_path: Path):
    """归组：分组预览按**生效 key**；应用时密钥不一致 ⇒ 422（不改密钥）。"""
    app = _create_app(tmp_path)

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            made_ids = []
            for key in ("sk-same", "sk-same"):
                made = await _request(
                    app,
                    "POST",
                    "/api/model-profiles",
                    json={
                        "name": "p",
                        "provider": "openai_compatible",
                        "model": "m",
                        "params": {
                            "base_url": "https://x.test/v1",
                            "api_key": key,
                        },
                    },
                )
                made_ids.append(made.json()["profile_id"])
            preview = await _request(app, "POST", "/api/model-profiles/group-by-connection")
            # 建一条 key 不同的连接 ⇒ 归组必须被拒
            other = await _request(
                app,
                "POST",
                "/api/model-connections",
                json={
                    "name": "c",
                    "provider": "openai_compatible",
                    "base_url": "https://x.test/v1",
                    "params": {"api_key": "sk-different"},
                },
            )
            conn_id = other.json()["connection_id"]
            apply_bad = await _request(
                app,
                "POST",
                "/api/model-profiles/apply-grouping",
                json={
                    "assignments": [
                        {"connection_id": conn_id, "profile_ids": [made_ids[0]]}
                    ]
                },
            )
            # 建一条 key 相同的连接 ⇒ 归组成功
            same = await _request(
                app,
                "POST",
                "/api/model-connections",
                json={
                    "name": "c2",
                    "provider": "openai_compatible",
                    "base_url": "https://x.test/v1",
                    "params": {"api_key": "sk-same"},
                },
            )
            apply_ok = await _request(
                app,
                "POST",
                "/api/model-profiles/apply-grouping",
                json={
                    "assignments": [
                        {
                            "connection_id": same.json()["connection_id"],
                            "profile_ids": made_ids,
                        }
                    ]
                },
            )
        return preview, apply_bad, apply_ok, made_ids

    preview, apply_bad, apply_ok, made_ids = asyncio.run(run())
    assert preview.status_code == 200
    groups = preview.json()["groups"]
    assert len(groups) == 1, f"生效 key 相同的两个档案应归到一组：{groups}"
    assert set(groups[0]["profile_ids"]) == set(made_ids)
    assert groups[0]["key_consistent"] is True

    assert apply_bad.status_code == 422
    assert "密钥" in apply_bad.text

    assert apply_ok.status_code == 200
    assert set(apply_ok.json()["applied"]) == set(made_ids)


def test_apply_grouping_keeps_profile_provider(tmp_path: Path, monkeypatch):
    """★归组不改协议（2026-10-24 审查修复 C2，判别用例）。

    anthropic 档案归到 openai 主协议连接（base_url 与生效 key 一致 ⇒ 逐条校验
    通过）⇒ 200 且档案 provider **保持 anthropic**。旧行为把 provider 一并写成
    连接主协议——base_url 一致性校验只拦「地址不同」，拦不住「地址相同、协议
    不同」的静默改写 ⇒ 修复直接消灭归组写 provider 这一分支。

    ``load_api_keys`` 打空：量具不得依赖本机 secrets.json 的 provider 级条目
    （若存在 ``anthropic`` 条目，档案侧生效 key 会命中它而与连接侧不一致 ⇒ 422，
    红的原因变成夹具而非被测机制）。
    """
    monkeypatch.setattr("packages.core.secrets_store.load_api_keys", lambda: {})
    app = _create_app(tmp_path)
    created = _post_connection(
        app,
        name="gateway",
        base_url="https://gateway.test/v1",
    ).json()
    conn_id = created["connection_id"]

    import asyncio

    async def run():
        async with app.router.lifespan_context(app):
            made = await _request(
                app,
                "POST",
                "/api/model-profiles",
                json={
                    "name": "claude",
                    "provider": "anthropic",
                    "model": "claude-opus",
                    "params": {
                        "base_url": "https://gateway.test/v1",
                        "api_key": _SECRET,
                    },
                },
            )
            pid = made.json()["profile_id"]
            apply_res = await _request(
                app,
                "POST",
                "/api/model-profiles/apply-grouping",
                json={
                    "assignments": [
                        {"connection_id": conn_id, "profile_ids": [pid]}
                    ]
                },
            )
            after = await _request(app, "GET", f"/api/model-profiles/{pid}")
        return apply_res, after, pid

    apply_res, after, pid = asyncio.run(run())
    assert apply_res.status_code == 200, apply_res.text
    assert after.status_code == 200
    assert after.json()["connection_id"] == conn_id, "归组应写 connection_id"
    assert after.json()["provider"] == "anthropic", (
        "归组只做物理归并 ⇒ 档案协议必须保持 anthropic（不得改成连接主协议）"
    )
    # DB 实际列值（断言打在真实值上，不是只看响应）
    conn = get_connection(app.state.settings.db_path)
    try:
        row = conn.execute(
            "SELECT provider, connection_id FROM model_profiles WHERE profile_id = ?",
            (pid,),
        ).fetchone()
    finally:
        conn.close()
    assert row["connection_id"] == conn_id
    assert row["provider"] == "anthropic"
