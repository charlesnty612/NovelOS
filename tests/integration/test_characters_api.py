"""/api/characters 集成测试（Sprint 1）。

覆盖：create → get → list → update → delete 主链路 + 负例（404、422）。
专项断言：create 后 GET /characters/{id}/states 恰有 v1 一行；visibility/role 校验。
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


async def _make_project(app, name: str = "测试项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def test_create_get_list_update_delete_happy_path(tmp_path: Path):
    """完整主链路 + 角色创建后自动有 v1 state 快照。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)

            # create
            r = await _request(
                app, "POST", f"/api/projects/{pid}/characters",
                json={
                    "name": "林夕",
                    "role": "protagonist",
                    "core_json": {"性格": "内敛", "核心创伤": "童年失怙"},
                    "visibility": "PUBLIC",
                    "who_knows": ["林夕"],
                },
            )
            assert r.status_code == 201, r.text
            created = r.json()
            cid = created["character_id"]
            assert cid.startswith("char_")
            assert created["name"] == "林夕"
            assert created["role"] == "protagonist"
            assert created["visibility"] == "PUBLIC"
            assert created["who_knows"] == ["林夕"]
            assert created["core_json"] == {"性格": "内敛", "核心创伤": "童年失怙"}
            assert created["latest_state_version"] == 1
            assert created["latest_state_json"] == {}

            # 专项断言：/states 端点恰有 v1 一行
            r = await _request(app, "GET", f"/api/characters/{cid}/states")
            assert r.status_code == 200
            states = r.json()
            assert len(states) == 1
            assert states[0]["state_version"] == 1
            assert states[0]["state_json"] == {}

            # get（带 latest state）
            r = await _request(app, "GET", f"/api/characters/{cid}")
            assert r.status_code == 200
            assert r.json()["latest_state_version"] == 1

            # list by project
            r = await _request(app, "GET", f"/api/projects/{pid}/characters")
            assert r.status_code == 200
            lst = r.json()
            assert len(lst) == 1
            assert lst[0]["character_id"] == cid

            # update（改 role 与 core_json；不动 state）
            r = await _request(
                app, "PATCH", f"/api/characters/{cid}",
                json={"role": "mentor", "core_json": {"性格": "外刚内柔"}},
            )
            assert r.status_code == 200, r.text
            upd = r.json()
            assert upd["role"] == "mentor"
            assert upd["core_json"] == {"性格": "外刚内柔"}
            assert upd["latest_state_version"] == 1  # state 未动

            # delete
            r = await _request(app, "DELETE", f"/api/characters/{cid}")
            assert r.status_code == 204

            # get after delete → 404
            r = await _request(app, "GET", f"/api/characters/{cid}")
            assert r.status_code == 404

            # /states after delete → 404
            r = await _request(app, "GET", f"/api/characters/{cid}/states")
            assert r.status_code == 404

    asyncio.run(run())


def test_create_with_invalid_role_returns_422(tmp_path: Path):
    """role 不在枚举 → 422。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/characters",
                json={"name": "X", "role": "fake_role"},
            )
            assert r.status_code == 422

    asyncio.run(run())


def test_get_nonexistent_character_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/characters/char_does_not_exist")
            assert r.status_code == 404

            r = await _request(app, "GET", "/api/characters/char_does_not_exist/states")
            assert r.status_code == 404

    asyncio.run(run())


def test_create_under_missing_project_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/projects/prj_nope/characters",
                json={"name": "Y"},
            )
            assert r.status_code == 404

    asyncio.run(run())


def test_list_states_returns_v1_for_fresh_character(tmp_path: Path):
    """专项断言：刚创建的角色 GET /states 恰有 v1 一行。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/characters",
                json={"name": "Z", "role": "supporting"},
            )
            cid = r.json()["character_id"]

            r = await _request(app, "GET", f"/api/characters/{cid}/states")
            assert r.status_code == 200
            data = r.json()
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["state_version"] == 1
            assert data[0]["state_json"] == {}

    asyncio.run(run())
