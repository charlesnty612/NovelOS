"""/api/health 集成测试（Sprint 0）。

不引入 pytest-asyncio，用 httpx + 自管事件循环。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from packages.core.api.main import create_app
from packages.core.config import Settings


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def test_health_endpoint_returns_ok(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    app = create_app(settings)

    async def run():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as client:
                r = await client.get("/api/health")
                return r

    r = asyncio.run(run())
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    # Sprint 14：0007_chapter_summaries.sql 已落地 → 32 业务表（31 + chapter_summaries）
    # Sprint 15 / V1.3：0008_author_style_samples_and_overdue.sql 加 author_style_samples → 33 业务表
    assert data["tables"] == 33


def test_root_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Sprint 5 起：当 ``apps/web/dist/index.html`` 真实存在时 SPA 自动启用，
    # ``GET /`` 会被 SPA fallback 接管返回 HTML（见 tests/api/test_spa_hosting.py）。
    # 本测试断言的是「SPA 关闭时 GET / 仍返回服务信息 JSON」——纯后端入口语义。
    # 因此显式 monkeypatch ``NOVELOS_WEB_DIST`` 指向不含 index.html 的 tmp 路径。
    monkeypatch.setenv("NOVELOS_WEB_DIST", str(tmp_path / "no_spa_dist"))

    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    app = create_app(settings)

    async def run():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as client:
                r = await client.get("/")
                return r

    r = asyncio.run(run())
    assert r.status_code == 200
    assert r.json()["service"] == "novelos"
