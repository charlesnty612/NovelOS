"""/api/projects 集成测试（Sprint 1）。

覆盖：create → get → list → update → delete 主链路 + 负例（404、409）。
测试模式参考 ``tests/integration/test_health.py``：httpx.ASGITransport + tmp_path db。
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


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


def test_create_get_list_update_delete_happy_path(tmp_path: Path):
    """完整主链路：建项目 → 读单个 → 列所有 → 更新 → 删除。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            # create
            r = await _request(
                app, "POST", "/api/projects",
                json={"name": "末日孤舟", "premise": "AI 觉醒", "genre": "科幻",
                      "target_words": 80000},
            )
            assert r.status_code == 201, r.text
            created = r.json()
            assert created["project_id"].startswith("prj_")
            assert created["name"] == "末日孤舟"
            assert created["status"] == "ACTIVE"
            assert created["created_at"] == created["updated_at"]
            pid = created["project_id"]

            # get
            r = await _request(app, "GET", f"/api/projects/{pid}")
            assert r.status_code == 200
            assert r.json()["project_id"] == pid

            # list（应至少含 1 项）
            r = await _request(app, "GET", "/api/projects")
            assert r.status_code == 200
            assert any(p["project_id"] == pid for p in r.json())

            # update
            r = await _request(
                app, "PATCH", f"/api/projects/{pid}",
                json={"status": "PAUSED", "target_words": 90000},
            )
            assert r.status_code == 200, r.text
            upd = r.json()
            assert upd["status"] == "PAUSED"
            assert upd["target_words"] == 90000
            assert upd["updated_at"] != upd["created_at"]
            assert upd["name"] == "末日孤舟"  # 未修改

            # delete
            r = await _request(app, "DELETE", f"/api/projects/{pid}")
            assert r.status_code == 204

            # get after delete → 404
            r = await _request(app, "GET", f"/api/projects/{pid}")
            assert r.status_code == 404

    asyncio.run(run())


def test_get_nonexistent_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/projects/prj_does_not_exist")
            assert r.status_code == 404
            assert "not found" in r.json()["detail"]

    asyncio.run(run())


def test_delete_with_children_returns_409(tmp_path: Path):
    """删除有 character 子记录的项目应被 409 拒绝。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            # 1) 建项目
            r = await _request(app, "POST", "/api/projects", json={"name": "A"})
            assert r.status_code == 201
            pid = r.json()["project_id"]

            # 2) 建一个 character（确保 FK 子记录存在）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/characters",
                json={"name": "小明"},
            )
            assert r.status_code == 201, r.text

            # 3) 删除项目 → 409
            r = await _request(app, "DELETE", f"/api/projects/{pid}")
            assert r.status_code == 409
            assert "dependent rows" in r.json()["detail"]

    asyncio.run(run())


def test_update_with_invalid_status_returns_422(tmp_path: Path):
    """status 不在枚举内 → 422（pydantic 校验 / IntegrityError）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "POST", "/api/projects", json={"name": "B"})
            pid = r.json()["project_id"]

            # 非法 status 字符串 → pydantic 校验 422
            r = await _request(
                app, "PATCH", f"/api/projects/{pid}",
                json={"status": "BOGUS"},
            )
            assert r.status_code == 422

    asyncio.run(run())


def test_list_empty_returns_empty_array(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/projects")
            assert r.status_code == 200
            assert r.json() == []

    asyncio.run(run())
