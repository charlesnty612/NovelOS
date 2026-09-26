"""/api/projects 的 ``writing_bible``（项目写作圣经，迁移 0029）读写暴露。

覆盖：
- POST /api/projects 带 ``writing_bible`` → 201 且响应带该字段；GET 回读一致；
- PATCH 设置 / 显式 null 清除 / 省略保留原值；
- GET /api/projects 列表同步暴露该字段；
- 不传该字段的老式创建 → 响应 ``writing_bible`` 为 null（存量客户端零破坏）。

测试模式参考 ``tests/api/test_projects_word_band.py``：httpx.ASGITransport + tmp_path db。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings

_BIBLE = "铁律：无 CP；第一位面＝现代都市；系统只做资源方，禁规则方。"


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


def test_create_with_writing_bible_roundtrips(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/projects",
                json={"name": "书1", "writing_bible": _BIBLE},
            )
            assert r.status_code == 201, r.text
            pid = r.json()["project_id"]
            assert r.json()["writing_bible"] == _BIBLE

            r = await _request(app, "GET", f"/api/projects/{pid}")
            assert r.status_code == 200
            assert r.json()["writing_bible"] == _BIBLE

            r = await _request(app, "GET", "/api/projects")
            assert r.status_code == 200
            rows = {p["project_id"]: p for p in r.json()}
            assert rows[pid]["writing_bible"] == _BIBLE

    asyncio.run(run())


def test_create_without_writing_bible_is_null(tmp_path: Path):
    """老式创建（不带该字段）→ null；响应模型必须始终含该键。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "POST", "/api/projects", json={"name": "老客户端"})
            assert r.status_code == 201, r.text
            assert r.json()["writing_bible"] is None

    asyncio.run(run())


def test_patch_writing_bible_set_clear_preserve(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "POST", "/api/projects", json={"name": "patch 目标"})
            pid = r.json()["project_id"]

            # 显式文本 → 写入
            r = await _request(
                app, "PATCH", f"/api/projects/{pid}", json={"writing_bible": _BIBLE},
            )
            assert r.status_code == 200, r.text
            assert r.json()["writing_bible"] == _BIBLE

            # 省略该字段（只改 name）→ 保留原值
            r = await _request(
                app, "PATCH", f"/api/projects/{pid}", json={"name": "改名"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["writing_bible"] == _BIBLE

            # 显式 null → 清除
            r = await _request(
                app, "PATCH", f"/api/projects/{pid}", json={"writing_bible": None},
            )
            assert r.status_code == 200, r.text
            assert r.json()["writing_bible"] is None

            r = await _request(app, "GET", f"/api/projects/{pid}")
            assert r.json()["writing_bible"] is None

    asyncio.run(run())
