"""V3.3 Capability 模块开关化（轻量方案）测试。

覆盖：
- 默认（disabled_modules 空列表）：既有行为不变（arc 端点 200）。
- 禁用 arc 后：``GET /api/projects/{pid}/arc`` 返回 404，未被禁的端点正常。
- ``discover_routers(disabled=...)`` 直接调用：被禁模块的 APIRouter 不出现在结果里。
- ``Settings.load()`` 对 ``NOVELOS_DISABLED_MODULES`` 的解析（逗号/strip/空串）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.api.routers import discover_routers
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso


def _make_client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _create_app(tmp_path: Path, disabled_modules: list[str] | None = None):
    settings = Settings(
        data_dir=tmp_path, log_level="WARNING", disabled_modules=disabled_modules
    )
    apply_migrations(settings.db_path)
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "模块开关项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


# ---------------------------------------------------------------------------
# Settings 解析
# ---------------------------------------------------------------------------


def test_settings_disabled_modules_default_is_empty_list():
    """默认构造（不传参）→ disabled_modules 为空列表。"""
    s = Settings(data_dir="./_ignored")
    assert s.disabled_modules == []


def test_settings_disabled_modules_accepts_list():
    """显式传 list → 直接保留。"""
    s = Settings(data_dir="./_ignored", disabled_modules=["arc", "reference"])
    assert s.disabled_modules == ["arc", "reference"]


def test_settings_load_parses_disabled_modules_env(monkeypatch, tmp_path: Path):
    """NOVELOS_DISABLED_MODULES 解析：逗号 + strip + 丢空串。"""
    monkeypatch.setenv("NOVELOS_DISABLED_MODULES", " simulation , reference ,,arc ")
    monkeypatch.setenv("NOVELOS_DATA_DIR", str(tmp_path))
    s = Settings.load()
    assert s.disabled_modules == ["simulation", "reference", "arc"]


def test_settings_load_empty_env_yields_empty_list(monkeypatch, tmp_path: Path):
    """空字符串 / 未设置 → 空列表。"""
    monkeypatch.delenv("NOVELOS_DISABLED_MODULES", raising=False)
    monkeypatch.setenv("NOVELOS_DATA_DIR", str(tmp_path))
    s = Settings.load()
    assert s.disabled_modules == []


# ---------------------------------------------------------------------------
# discover_routers 直接调用
# ---------------------------------------------------------------------------


def test_discover_routers_default_includes_arc():
    """disabled=None（默认）→ arc router 在内。"""
    routers = discover_routers()
    assert any(
        "/projects/{project_id}/arc" in [r.path for r in router.routes] for router in routers
    )


def test_discover_routers_disabled_skips_arc():
    """disabled={"arc"} → arc router 被剔除。"""
    routers = discover_routers(disabled={"arc"})
    assert not any(
        "/projects/{project_id}/arc" in [r.path for r in router.routes] for router in routers
    )


# ---------------------------------------------------------------------------
# 端到端：create_app 接线
# ---------------------------------------------------------------------------


def test_arc_endpoint_200_when_not_disabled(tmp_path: Path):
    """默认（空 disabled）→ arc 端点照常 200。"""
    app = _create_app(tmp_path, disabled_modules=[])

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(app, "GET", f"/api/projects/{pid}/arc")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["project_id"] == pid
            assert body["chapters"] == []

    asyncio.run(run())


def test_arc_endpoint_404_when_arc_disabled(tmp_path: Path):
    """禁用 arc → 端点 404（与 SPA fallback 对 /api/... 的处理一致）。"""
    app = _create_app(tmp_path, disabled_modules=["arc"])

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(app, "GET", f"/api/projects/{pid}/arc")
            # arc router 未挂载 → /api/projects/{pid}/arc 不在路由表
            # lifespan_context 路径下 SPA fallback 未启用（dist 不在），落到 404
            assert r.status_code == 404, r.text

    asyncio.run(run())


def test_unrelated_endpoint_works_when_arc_disabled(tmp_path: Path):
    """禁用 arc 不影响其他模块（如 projects 列表）。"""
    app = _create_app(tmp_path, disabled_modules=["arc"])

    async def run():
        async with app.router.lifespan_context(app):
            # 列表接口（projects）应不受影响
            r = await _request(app, "GET", "/api/projects")
            assert r.status_code == 200, r.text
            # 健康检查也照常
            r2 = await _request(app, "GET", "/api/health")
            assert r2.status_code == 200, r2.text

    asyncio.run(run())


def test_default_settings_keeps_existing_behavior(tmp_path: Path):
    """不带 disabled_modules（默认）→ 行为与改动前一致：arc 端点 200。"""
    # 显式不传 disabled_modules，验证默认参数兼容既有调用方
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            # 直插一个 chapter 触发最小 arc 行为
            conn = get_connection(app.state.settings.db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO chapters
                        (chapter_id, project_id, number, title, plan_json,
                         status, visibility, who_knows, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)
                    """,
                    (
                        new_id("ch"),
                        pid,
                        1,
                        "首章",
                        json.dumps({"key_beats": []}, ensure_ascii=False),
                        now_iso(),
                        now_iso(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            r = await _request(app, "GET", f"/api/projects/{pid}/arc")
            assert r.status_code == 200, r.text
            body = r.json()
            assert len(body["chapters"]) == 1

    asyncio.run(run())
