"""/api/chapters 集成测试（Sprint 1）。

覆盖：create → get → list → update → delete 主链路 + 负例（404、409）。
专项断言：chapter 状态机非法跳变返回 409；同 project 内 number 重复返回 409。
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


async def _make_project(app, name: str = "测试") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def test_create_get_list_update_delete_happy_path(tmp_path: Path):
    """完整主链路 + 列表按 number 升序。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)

            # create（先建第 2 章，再建第 1 章，验证 list 按 number 排序）
            r2 = await _request(
                app, "POST", f"/api/projects/{pid}/chapters",
                json={"number": 2, "title": "第二章", "plan_json": {"goal": "冲突升级"}},
            )
            assert r2.status_code == 201, r2.text
            c2 = r2.json()
            assert c2["chapter_id"].startswith("ch_")
            assert c2["status"] == "PLANNED"

            r1 = await _request(
                app, "POST", f"/api/projects/{pid}/chapters",
                json={"number": 1, "title": "第一章"},
            )
            assert r1.status_code == 201
            c1 = r1.json()
            cid1, cid2 = c1["chapter_id"], c2["chapter_id"]

            # list by project（按 number 升序）
            r = await _request(app, "GET", f"/api/projects/{pid}/chapters")
            assert r.status_code == 200
            lst = r.json()
            assert [c["chapter_id"] for c in lst] == [cid1, cid2]

            # get
            r = await _request(app, "GET", f"/api/chapters/{cid1}")
            assert r.status_code == 200
            assert r.json()["number"] == 1

            # update title + plan_json
            r = await _request(
                app, "PATCH", f"/api/chapters/{cid1}",
                json={"title": "第一章 重命名", "plan_json": {"goal": "引入"}},
            )
            assert r.status_code == 200
            upd = r.json()
            assert upd["title"] == "第一章 重命名"
            assert upd["plan_json"] == {"goal": "引入"}

            # delete
            r = await _request(app, "DELETE", f"/api/chapters/{cid1}")
            assert r.status_code == 204

            # get after delete → 404
            r = await _request(app, "GET", f"/api/chapters/{cid1}")
            assert r.status_code == 404

    asyncio.run(run())


def test_status_machine_illegal_transition_returns_409(tmp_path: Path):
    """PLANNED → COMMITTED 是非法跳变（必须先 DRAFTED），应返回 409。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters",
                json={"number": 1, "title": "C1"},
            )
            cid = r.json()["chapter_id"]

            # 非法跳变 PLANNED → COMMITTED → 409
            r = await _request(
                app, "PATCH", f"/api/chapters/{cid}",
                json={"status": "COMMITTED"},
            )
            assert r.status_code == 409
            detail = r.json()["detail"]
            assert "illegal chapter status transition" in detail
            assert "PLANNED" in detail and "COMMITTED" in detail

            # 合法跳变 PLANNED → DRAFTED
            r = await _request(
                app, "PATCH", f"/api/chapters/{cid}",
                json={"status": "DRAFTED"},
            )
            assert r.status_code == 200
            assert r.json()["status"] == "DRAFTED"

            # 回退到 PLANNED（合法）
            r = await _request(
                app, "PATCH", f"/api/chapters/{cid}",
                json={"status": "PLANNED"},
            )
            assert r.status_code == 200
            assert r.json()["status"] == "PLANNED"

    asyncio.run(run())


def test_status_machine_walk_to_released(tmp_path: Path):
    """状态机完整正向：PLANNED→DRAFTED→REVIEWED→COMMITTED→RELEASED 全跑通。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters",
                json={"number": 1},
            )
            cid = r.json()["chapter_id"]

            for nxt in ["DRAFTED", "REVIEWED", "COMMITTED", "RELEASED"]:
                r = await _request(
                    app, "PATCH", f"/api/chapters/{cid}",
                    json={"status": nxt},
                )
                assert r.status_code == 200, f"failed at {nxt}: {r.text}"
                assert r.json()["status"] == nxt

    asyncio.run(run())


def test_duplicate_number_returns_409(tmp_path: Path):
    """同 project 内 number 重复 → 409。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters",
                json={"number": 5, "title": "first"},
            )
            assert r.status_code == 201

            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters",
                json={"number": 5, "title": "dup"},
            )
            assert r.status_code == 409

    asyncio.run(run())


def test_create_under_missing_project_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/projects/prj_nope/chapters",
                json={"number": 1},
            )
            assert r.status_code == 404

    asyncio.run(run())


def test_invalid_status_value_returns_422(tmp_path: Path):
    """非法 status 字符串 → 422（pydantic 校验）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/chapters",
                json={"number": 1},
            )
            cid = r.json()["chapter_id"]

            r = await _request(
                app, "PATCH", f"/api/chapters/{cid}",
                json={"status": "PUBLISHED"},  # 不在枚举内
            )
            assert r.status_code == 422

    asyncio.run(run())
