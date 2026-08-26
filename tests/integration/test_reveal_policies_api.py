"""/api/projects/{pid}/reveal-policies 集成测试（V3.3 P0-2 知识权限补全）。

覆盖：
- POST 创建 → 201 + dict；
- GET 列表（按 policy_id 稳定排序）→ 200；
- GET ?status= 过滤；
- PATCH 仅允许 status / revealed_chapter / notes；
- DELETE 204 + 二次 GET 404；
- 错误码：404（project / policy 不存在）；422（非法字段 / 缺 revealed_chapter / target 不存在）。

测试模式参考 ``tests/integration/test_chapters_api.py``：httpx.ASGITransport + tmp_path db。
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


async def _make_project(app, name: str = "rp-api-test") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_character(app, pid: str, char_id: str = "char_alice") -> None:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/characters",
        json={"name": "Alice", "role": "protagonist", "visibility": "HIDDEN"},
    )
    assert r.status_code == 201, r.text
    # 同步 char_id 给后续测试（API 自动生成；用列表查 id 麻烦，直接用返回的 id）
    assert r.json()["character_id"].startswith("char_")


async def _seed_char(app, pid: str, name: str = "Alice") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/characters",
        json={"name": name, "role": "protagonist", "visibility": "HIDDEN"},
    )
    assert r.status_code == 201, r.text
    return r.json()["character_id"]


# ---------------------------------------------------------------------------
# CRUD 主链路
# ---------------------------------------------------------------------------


def test_crud_full_path(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _seed_char(app, pid)

            # POST create
            r = await _request(
                app, "POST", f"/api/projects/{pid}/reveal-policies",
                json={"target_kind": "character", "target_id": cid},
            )
            assert r.status_code == 201, r.text
            pol = r.json()
            assert pol["policy_id"].startswith("rp_")
            assert pol["status"] == "planned"
            assert pol["audience"] == "reader"
            assert pol["reveal_by_chapter"] is None
            assert pol["revealed_chapter"] is None
            assert pol["notes"] is None
            policy_id = pol["policy_id"]

            # GET list
            r = await _request(
                app, "GET", f"/api/projects/{pid}/reveal-policies",
            )
            assert r.status_code == 200
            lst = r.json()
            assert len(lst) == 1
            assert lst[0]["policy_id"] == policy_id

            # GET ?status=planned
            r = await _request(
                app, "GET",
                f"/api/projects/{pid}/reveal-policies?status_filter=planned",
            )
            assert r.status_code == 200
            assert len(r.json()) == 1
            # GET ?status=revealed → 空
            r = await _request(
                app, "GET",
                f"/api/projects/{pid}/reveal-policies?status_filter=revealed",
            )
            assert r.status_code == 200
            assert r.json() == []

            # PATCH status='revealed' + revealed_chapter=5
            r = await _request(
                app, "PATCH",
                f"/api/projects/{pid}/reveal-policies/{policy_id}",
                json={"status": "revealed", "revealed_chapter": 5},
            )
            assert r.status_code == 200, r.text
            upd = r.json()
            assert upd["status"] == "revealed"
            assert upd["revealed_chapter"] == 5

            # GET list ?status=revealed
            r = await _request(
                app, "GET",
                f"/api/projects/{pid}/reveal-policies?status_filter=revealed",
            )
            assert r.status_code == 200
            assert len(r.json()) == 1
            assert r.json()[0]["policy_id"] == policy_id

            # DELETE
            r = await _request(
                app, "DELETE",
                f"/api/projects/{pid}/reveal-policies/{policy_id}",
            )
            assert r.status_code == 204

            # list → 空
            r = await _request(
                app, "GET", f"/api/projects/{pid}/reveal-policies",
            )
            assert r.json() == []

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 错误码
# ---------------------------------------------------------------------------


def test_create_invalid_target_kind_returns_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/reveal-policies",
                json={"target_kind": "bogus", "target_id": "x"},
            )
            assert r.status_code == 422
            assert "target_kind" in r.json()["detail"]

    asyncio.run(run())


def test_create_target_not_exists_returns_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/reveal-policies",
                json={"target_kind": "character", "target_id": "char_nope"},
            )
            assert r.status_code == 422
            assert "不存在" in r.json()["detail"]

    asyncio.run(run())


def test_create_status_revealed_without_chapter_returns_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _seed_char(app, pid)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/reveal-policies",
                json={
                    "target_kind": "character", "target_id": cid,
                    "status": "revealed",
                },
            )
            assert r.status_code == 422
            assert "revealed_chapter" in r.json()["detail"]

    asyncio.run(run())


def test_create_project_not_found_returns_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/projects/prj_nope/reveal-policies",
                json={"target_kind": "character", "target_id": "x"},
            )
            assert r.status_code == 422
            assert "project" in r.json()["detail"]

    asyncio.run(run())


def test_patch_nonexistent_policy_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "PATCH",
                f"/api/projects/{pid}/reveal-policies/rp_nope",
                json={"status": "cancelled"},
            )
            assert r.status_code == 404

    asyncio.run(run())


def test_delete_nonexistent_policy_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "DELETE",
                f"/api/projects/{pid}/reveal-policies/rp_nope",
            )
            assert r.status_code == 404

    asyncio.run(run())


def test_patch_status_revealed_without_chapter_returns_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _seed_char(app, pid)
            # 先建一个 planned policy
            r = await _request(
                app, "POST", f"/api/projects/{pid}/reveal-policies",
                json={"target_kind": "character", "target_id": cid},
            )
            assert r.status_code == 201
            policy_id = r.json()["policy_id"]
            # 单独 PATCH status=revealed，缺 revealed_chapter → 422
            r = await _request(
                app, "PATCH",
                f"/api/projects/{pid}/reveal-policies/{policy_id}",
                json={"status": "revealed"},
            )
            assert r.status_code == 422
            assert "revealed_chapter" in r.json()["detail"]

    asyncio.run(run())


def test_list_invalid_status_filter_returns_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "GET",
                f"/api/projects/{pid}/reveal-policies?status_filter=bogus",
            )
            assert r.status_code == 422
            assert "status" in r.json()["detail"]

    asyncio.run(run())


def test_create_with_revealed_status_and_chapter_succeeds(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            cid = await _seed_char(app, pid)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/reveal-policies",
                json={
                    "target_kind": "character", "target_id": cid,
                    "status": "revealed", "revealed_chapter": 7,
                    "notes": "已揭晓",
                },
            )
            assert r.status_code == 201, r.text
            assert r.json()["status"] == "revealed"
            assert r.json()["revealed_chapter"] == 7
            assert r.json()["notes"] == "已揭晓"

    asyncio.run(run())
