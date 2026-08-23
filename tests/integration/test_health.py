"""/api/health 集成测试（Sprint 0）。

不引入 pytest-asyncio，用 httpx + 自管事件循环。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

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
    # 28 业务表
    assert data["tables"] == 28


def test_root_endpoint(tmp_path: Path):
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
