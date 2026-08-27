"""Model Profiles 路由测试（V3.7「模型档案 + 环节绑定」）。

覆盖：
1. CRUD 路径（POST / GET / PATCH / DELETE）走 ProfileService，参数校验对齐 /model-configs。
2. 读路径脱敏：明文 api_key 永不回显，has_api_key 顶层字段。
3. PATCH 写语义：api_key mask / 空 / 新值。
4. POST mask/空 api_key 不写库。
5. DELETE 被 binding 引用 → 409（detail 含 referenced_by）。
6. /test 端点成功路径：mock provider 走 health_check 返回 ok=True。
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

_SECRET = "sk-this-is-a-secret-key-12345"


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
# 1. CRUD 路径 + 读路径脱敏
# ---------------------------------------------------------------------------


def test_create_response_masks_api_key_and_attaches_has_api_key(tmp_path: Path):
    """POST 提交含明文 api_key → 响应 params_json.api_key 是 "***" 且 has_api_key=True。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-profiles",
                json={
                    "name": "alpha",
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
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-profiles",
                json={
                    "name": "alpha",
                    "provider": "anthropic",
                    "model": "claude-3-5-sonnet",
                    "params_json": {"api_key": _SECRET},
                    "enabled": 1,
                },
            )
            assert r.status_code == 201, r.text
            pid = r.json()["profile_id"]

            r = await _request(app, "GET", "/api/model-profiles")
            assert r.status_code == 200
            listed = next(c for c in r.json() if c["profile_id"] == pid)
            assert listed["params_json"]["api_key"] == "***"
            assert _SECRET not in r.text
            assert listed["has_api_key"] is True

            r = await _request(app, "GET", f"/api/model-profiles/{pid}")
            assert r.status_code == 200
            detail = r.json()
            assert detail["params_json"]["api_key"] == "***"
            assert _SECRET not in r.text
            assert detail["has_api_key"] is True

    asyncio.run(run())


def test_post_mask_or_empty_does_not_persist_key(tmp_path: Path):
    """POST 提交 api_key='***' 或 '' → DB 中不存 api_key。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            for sentinel in ("***", ""):
                r = await _request(
                    app, "POST", "/api/model-profiles",
                    json={
                        "name": "alpha",
                        "provider": "openai",
                        "model": "gpt-4o",
                        "params_json": {"api_key": sentinel, "base_url": "https://api.openai.com/v1"},
                    },
                )
                assert r.status_code == 201, r.text
                pid = r.json()["profile_id"]
                assert r.json()["has_api_key"] is False

                settings = app.state.settings
                conn = get_connection(settings.db_path)
                try:
                    row = conn.execute(
                        "SELECT params_json FROM model_profiles WHERE profile_id = ?", (pid,)
                    ).fetchone()
                finally:
                    conn.close()
                stored = json.loads(row["params_json"])
                assert "api_key" not in stored

    asyncio.run(run())


def test_patch_mask_keeps_existing_key(tmp_path: Path):
    """PATCH api_key='***' → DB 原值保留。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-profiles",
                json={
                    "name": "alpha",
                    "provider": "openai",
                    "model": "gpt-4o",
                    "params_json": {"api_key": _SECRET, "base_url": "https://api.openai.com/v1"},
                },
            )
            pid = r.json()["profile_id"]

            r = await _request(
                app, "PATCH", f"/api/model-profiles/{pid}",
                json={"params_json": {"base_url": "https://api.openai.com/v1", "api_key": "***"}},
            )
            assert r.status_code == 200, r.text
            assert r.json()["has_api_key"] is True
            assert r.json()["params_json"]["api_key"] == "***"

            settings = app.state.settings
            conn = get_connection(settings.db_path)
            try:
                row = conn.execute(
                    "SELECT params_json FROM model_profiles WHERE profile_id = ?", (pid,)
                ).fetchone()
            finally:
                conn.close()
            assert json.loads(row["params_json"])["api_key"] == _SECRET

    asyncio.run(run())


def test_patch_empty_string_clears_key(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-profiles",
                json={
                    "name": "alpha",
                    "provider": "openai",
                    "model": "gpt-4o",
                    "params_json": {"api_key": _SECRET, "base_url": "https://api.openai.com/v1"},
                },
            )
            pid = r.json()["profile_id"]

            r = await _request(
                app, "PATCH", f"/api/model-profiles/{pid}",
                json={"params_json": {"base_url": "https://api.openai.com/v1", "api_key": ""}},
            )
            assert r.status_code == 200, r.text
            assert r.json()["has_api_key"] is False
            assert "api_key" not in r.json()["params_json"]

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 2. DELETE 被 binding 引用 → 409
# ---------------------------------------------------------------------------


def test_delete_referenced_by_binding_returns_409(tmp_path: Path):
    """DELETE 被 binding 引用 → 409，detail 含 referenced_by 列表。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            # 创建档案
            r = await _request(
                app, "POST", "/api/model-profiles",
                json={"name": "alpha", "provider": "mock", "model": "m1"},
            )
            pid = r.json()["profile_id"]

            # 写入 binding
            r = await _request(
                app, "PUT", "/api/capability-bindings/reasoning",
                json={"profile_ids": [pid]},
            )
            assert r.status_code == 200, r.text

            # DELETE 应被拒
            r = await _request(app, "DELETE", f"/api/model-profiles/{pid}")
            assert r.status_code == 409, r.text
            detail = r.json()["detail"]
            assert detail["error"] == "profile_in_use"
            assert "reasoning" in detail["referenced_by"]

    asyncio.run(run())


def test_delete_unreferenced_returns_204(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-profiles",
                json={"name": "alpha", "provider": "mock", "model": "m1"},
            )
            pid = r.json()["profile_id"]

            r = await _request(app, "DELETE", f"/api/model-profiles/{pid}")
            assert r.status_code == 204, r.text

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 3. /test 端点成功路径
# ---------------------------------------------------------------------------


def test_test_endpoint_mock_provider_returns_ok(tmp_path: Path):
    """/test 端点：mock provider 直接 ok=True（不发外网）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-profiles",
                json={"name": "alpha", "provider": "mock", "model": "m1"},
            )
            pid = r.json()["profile_id"]

            r = await _request(app, "POST", f"/api/model-profiles/{pid}/test")
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["profile_id"] == pid
            assert data["ok"] is True

    asyncio.run(run())


def test_test_endpoint_disabled_returns_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/model-profiles",
                json={"name": "alpha", "provider": "mock", "model": "m1", "enabled": 0},
            )
            pid = r.json()["profile_id"]

            r = await _request(app, "POST", f"/api/model-profiles/{pid}/test")
            assert r.status_code == 422, r.text

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 4. 校验错误
# ---------------------------------------------------------------------------


def test_create_missing_required_returns_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            # 缺 name
            r = await _request(
                app, "POST", "/api/model-profiles",
                json={"provider": "mock", "model": "m1"},
            )
            assert r.status_code == 422, r.text

            # 缺 provider
            r = await _request(
                app, "POST", "/api/model-profiles",
                json={"name": "alpha", "model": "m1"},
            )
            assert r.status_code == 422, r.text

            # 缺 model
            r = await _request(
                app, "POST", "/api/model-profiles",
                json={"name": "alpha", "provider": "mock"},
            )
            assert r.status_code == 422, r.text

    asyncio.run(run())
