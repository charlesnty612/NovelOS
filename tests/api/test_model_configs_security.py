"""Model Configs 读路径脱敏 + 零外呼锁定 + 拆书入参护栏（Sprint 12 P1-1 / P1-4）。

覆盖：
1. POST / GET / PATCH 读路径响应一律脱敏 api_key，附 has_api_key 顶层字段；
   明文 api_key 仅在请求体内短暂出现，绝不通过响应回写。
2. PATCH 写语义：
   - ``api_key == "***"`` 视为「保留 DB 原值」；
   - ``api_key == ""`` 视为「清空」；
   - 其它字符串按入参覆盖。
3. POST 写语义：mask / 空字符串 → 落库时不写 api_key。
4. ModelRouter 在空库下抛 :class:`ModelNotConfiguredError`，且不写 ai_call_logs。
5. reference deconstruct 在无 model_config 时 422（非 500）。
6. reference deconstruct text > 5_000_000 字符 → 422。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.model_router import ModelNotConfiguredError, ModelRouter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


_SECRET = "sk-this-is-a-secret-key-12345"


# ---------------------------------------------------------------------------
# 1. 读路径脱敏（POST / GET / PATCH 响应）
# ---------------------------------------------------------------------------


def test_create_response_masks_api_key_and_attaches_has_api_key(tmp_path: Path):
    """POST 提交含明文 api_key → 响应 params_json.api_key 是 "***" 且 has_api_key=True。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app,
                "POST",
                "/api/model-configs",
                json={
                    "capability": "reasoning",
                    "provider": "openai",
                    "model": "gpt-4o",
                    "params_json": {"base_url": "https://api.openai.com/v1", "api_key": _SECRET},
                    "enabled": 1,
                },
            )
            assert r.status_code == 201, r.text
            cfg = r.json()
            assert cfg["params_json"]["api_key"] == "***"
            assert _SECRET not in r.text
            assert cfg["has_api_key"] is True

    asyncio.run(run())


def test_list_and_get_mask_api_key(tmp_path: Path):
    """GET 列表 / GET 详情读路径同样脱敏。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app,
                "POST",
                "/api/model-configs",
                json={
                    "capability": "reasoning",
                    "provider": "anthropic",
                    "model": "claude-3-5-sonnet",
                    "params_json": {"api_key": _SECRET},
                    "enabled": 1,
                },
            )
            assert r.status_code == 201, r.text
            cid = r.json()["config_id"]

            # GET list
            r = await _request(app, "GET", "/api/model-configs")
            assert r.status_code == 200
            listed = next(c for c in r.json() if c["config_id"] == cid)
            assert listed["params_json"]["api_key"] == "***"
            assert _SECRET not in r.text
            assert listed["has_api_key"] is True

            # GET one
            r = await _request(app, "GET", f"/api/model-configs/{cid}")
            assert r.status_code == 200
            detail = r.json()
            assert detail["params_json"]["api_key"] == "***"
            assert _SECRET not in r.text
            assert detail["has_api_key"] is True

    asyncio.run(run())


def test_patch_response_masks_api_key(tmp_path: Path):
    """PATCH 响应也走脱敏路径。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app,
                "POST",
                "/api/model-configs",
                json={
                    "capability": "reasoning",
                    "provider": "openai",
                    "model": "gpt-4o",
                    "params_json": {"api_key": _SECRET},
                },
            )
            cid = r.json()["config_id"]

            r = await _request(
                app,
                "PATCH",
                f"/api/model-configs/{cid}",
                json={"enabled": 1},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["params_json"]["api_key"] == "***"
            assert _SECRET not in r.text
            assert body["has_api_key"] is True

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 2. PATCH 写语义：mask / 空 / 新值
# ---------------------------------------------------------------------------


def test_patch_mask_keeps_existing_key(tmp_path: Path):
    """PATCH 提交 api_key='***' → DB 原值保留，has_api_key 仍 True。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app,
                "POST",
                "/api/model-configs",
                json={
                    "capability": "reasoning",
                    "provider": "openai",
                    "model": "gpt-4o",
                    "params_json": {"api_key": _SECRET, "base_url": "https://api.openai.com/v1"},
                },
            )
            cid = r.json()["config_id"]

            # PATCH 整对象，api_key 显式传 "***"
            r = await _request(
                app,
                "PATCH",
                f"/api/model-configs/{cid}",
                json={"params_json": {"base_url": "https://api.openai.com/v1", "api_key": "***"}},
            )
            assert r.status_code == 200, r.text
            assert r.json()["has_api_key"] is True
            assert r.json()["params_json"]["api_key"] == "***"

            # 校验 DB 原值确实没被清掉
            settings = app.state.settings
            conn = get_connection(settings.db_path)
            try:
                row = conn.execute(
                    "SELECT params_json FROM model_configs WHERE config_id = ?", (cid,)
                ).fetchone()
            finally:
                conn.close()
            stored = json.loads(row["params_json"])
            assert stored["api_key"] == _SECRET

    asyncio.run(run())


def test_patch_empty_string_clears_key(tmp_path: Path):
    """PATCH 提交 api_key='' → DB 中 api_key 键被删除（视为清空）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app,
                "POST",
                "/api/model-configs",
                json={
                    "capability": "reasoning",
                    "provider": "openai",
                    "model": "gpt-4o",
                    "params_json": {"api_key": _SECRET, "base_url": "https://api.openai.com/v1"},
                },
            )
            cid = r.json()["config_id"]

            r = await _request(
                app,
                "PATCH",
                f"/api/model-configs/{cid}",
                json={"params_json": {"base_url": "https://api.openai.com/v1", "api_key": ""}},
            )
            assert r.status_code == 200, r.text
            assert r.json()["has_api_key"] is False
            assert "api_key" not in r.json()["params_json"]

            # DB 中确实没有 api_key
            settings = app.state.settings
            conn = get_connection(settings.db_path)
            try:
                row = conn.execute(
                    "SELECT params_json FROM model_configs WHERE config_id = ?", (cid,)
                ).fetchone()
            finally:
                conn.close()
            stored = json.loads(row["params_json"])
            assert "api_key" not in stored

    asyncio.run(run())


def test_patch_new_value_overwrites_key(tmp_path: Path):
    """PATCH 提交 api_key='new-key' → DB 中覆盖。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app,
                "POST",
                "/api/model-configs",
                json={
                    "capability": "reasoning",
                    "provider": "openai",
                    "model": "gpt-4o",
                    "params_json": {"api_key": _SECRET, "base_url": "https://api.openai.com/v1"},
                },
            )
            cid = r.json()["config_id"]

            r = await _request(
                app,
                "PATCH",
                f"/api/model-configs/{cid}",
                json={"params_json": {"api_key": "new-key", "base_url": "https://api.openai.com/v1"}},
            )
            assert r.status_code == 200, r.text

            settings = app.state.settings
            conn = get_connection(settings.db_path)
            try:
                row = conn.execute(
                    "SELECT params_json FROM model_configs WHERE config_id = ?", (cid,)
                ).fetchone()
            finally:
                conn.close()
            assert json.loads(row["params_json"])["api_key"] == "new-key"

    asyncio.run(run())


def test_post_mask_or_empty_does_not_persist_key(tmp_path: Path):
    """POST 提交 api_key='***' 或 '' → DB 中不存 api_key。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            for sentinel in ("***", ""):
                r = await _request(
                    app,
                    "POST",
                    "/api/model-configs",
                    json={
                        "capability": "reasoning",
                        "provider": "openai",
                        "model": "gpt-4o",
                        "params_json": {"api_key": sentinel, "base_url": "https://api.openai.com/v1"},
                    },
                )
                assert r.status_code == 201, r.text
                cid = r.json()["config_id"]
                assert r.json()["has_api_key"] is False

                settings = app.state.settings
                conn = get_connection(settings.db_path)
                try:
                    row = conn.execute(
                        "SELECT params_json FROM model_configs WHERE config_id = ?", (cid,)
                    ).fetchone()
                finally:
                    conn.close()
                stored = json.loads(row["params_json"])
                assert "api_key" not in stored

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 3. 零外呼回归锁定（ModelRouter + deconstruct 端点）
# ---------------------------------------------------------------------------


def test_model_router_resolve_raises_when_no_config(tmp_path: Path):
    """空库（无 model_configs）下 ModelRouter.resolve 必须抛 ModelNotConfiguredError。"""
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    router = ModelRouter(settings.db_path)
    with pytest.raises(ModelNotConfiguredError):
        router.resolve("reasoning")

    # 验证 ai_call_logs 表零新增
    conn = get_connection(settings.db_path)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM ai_call_logs").fetchone()["n"]
    finally:
        conn.close()
    assert n == 0


def test_model_router_call_with_fallback_raises_when_no_config(tmp_path: Path):
    """空库下 call_with_fallback 同样抛 ModelNotConfiguredError。"""
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    router = ModelRouter(settings.db_path)
    with pytest.raises(ModelNotConfiguredError):
        router.call_with_fallback("reasoning", [{"role": "user", "content": "hi"}])

    conn = get_connection(settings.db_path)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM ai_call_logs").fetchone()["n"]
    finally:
        conn.close()
    assert n == 0


def test_deconstruct_without_model_config_returns_422(tmp_path: Path):
    """无 model_config 时启动 deconstruct 返 422（不再 201 + FAILED 也不 500）。

    启动前预检查 ModelRouter.list_enabled("reasoning")；为空 → 422 拒绝。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            # sync prompts（注册 deconstructor_* ACTIVE 行；缺 model_config 走 ModelRouter.resolve）
            docs_dir = Path.cwd().resolve() / "docs" / "agents" / "prompts"
            r = await _request(
                app, "POST", f"/api/agents/sync?docs_dir={docs_dir.as_posix()}"
            )
            assert r.status_code == 200, r.text

            # 建一个空 project
            r = await _request(app, "POST", "/api/projects", json={"name": "no-config"})
            assert r.status_code == 201
            pid = r.json()["project_id"]

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/deconstruct",
                json={"book_title": "无配置测试", "text": "第一章 正文"},
            )
            # 关键断言：必须 422 而非 500 或 201+FAILED；detail 含 capability 信息。
            assert r.status_code == 422, r.text
            detail = r.json().get("detail", "")
            assert "reasoning" in detail

            # 校验：未启动 run，无 ai_call_logs 写入。
            settings = app.state.settings
            conn = get_connection(settings.db_path)
            try:
                n_runs = conn.execute("SELECT COUNT(*) AS n FROM workflow_runs").fetchone()["n"]
                n_logs = conn.execute("SELECT COUNT(*) AS n FROM ai_call_logs").fetchone()["n"]
            finally:
                conn.close()
            assert n_runs == 0
            assert n_logs == 0

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 4. 拆书文本上限
# ---------------------------------------------------------------------------


def test_deconstruct_text_too_large_returns_422(tmp_path: Path, monkeypatch):
    """text 超过上限 → 422（不真造 5MB 字符串：用 monkeypatch 缩小上限到 16 字节）。"""
    from packages.core.api.routers import reference as reference_router

    monkeypatch.setattr(reference_router, "_MAX_DECONSTRUCT_TEXT_LEN", 16)
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "POST", "/api/projects", json={"name": "oversize"})
            assert r.status_code == 201
            pid = r.json()["project_id"]

            r = await _request(
                app,
                "POST",
                f"/api/projects/{pid}/deconstruct",
                json={"book_title": "X", "text": "x" * 17},  # > 16
            )
            assert r.status_code == 422, r.text
            assert "16" in r.json()["detail"]

    asyncio.run(run())
