"""/api/projects/{pid}/volumes 集成测试（V3.4 多卷与规模——组织层）。

覆盖：
- POST 创建 → 201 + dict；
- GET 列表（含 chapter_count）；
- GET 详情；
- PATCH 改 title；
- POST /seal → 200 + terminal_snapshot_json 冻结；
- POST /assign → 200；
- 错误码：404（project / volume 不存在）、409（number 重复 / active 单例 /
  sealed 拒绝挂章）、422（跨项目挂章）；

测试模式参考 ``tests/integration/test_reveal_policies_api.py``：
httpx.ASGITransport + tmp_path db。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso


def _make_client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "vol-api-test") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, number: int, title: str = "ch") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


def _seed_snapshot(app, pid: str, snap: dict) -> int:
    """直插 story_states 一行（用于 seal 冻结校验）。

    story_states.commit_id 是 NOT NULL REFERENCES commits；故先插 commit 链路。
    """
    conn = get_connection(app.state.settings.db_path)
    try:
        # 1) branch
        branch_id = "br_main_" + pid[-8:]
        conn.execute(
            """
            INSERT OR IGNORE INTO branches
                (branch_id, project_id, name, parent_branch_id,
                 base_state_version, status, created_at)
            VALUES (?, ?, 'main', NULL, 0, 'ACTIVE', ?)
            """,
            (branch_id, pid, now_iso()),
        )
        # 2) chapter
        chapter_id = new_id("ch")
        conn.execute(
            """
            INSERT OR IGNORE INTO chapters
                (chapter_id, project_id, number, title, plan_json,
                 status, visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, 1, '_seed', '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (chapter_id, pid, now_iso(), now_iso()),
        )
        # 3) delta
        delta_id = new_id("dlt")
        conn.execute(
            """
            INSERT OR IGNORE INTO state_deltas
                (delta_id, chapter_id, workflow_run_id, previous_state_version,
                 delta_version, schema_version, payload_json, status,
                 supersedes, created_by, created_at)
            VALUES (?, ?, ?, 0, 1, 'state-delta-v0', '{}', 'applied',
                    NULL, 'system', ?)
            """,
            (delta_id, chapter_id, new_id("wfr"), now_iso()),
        )
        # 4) commit
        commit_id = new_id("cmt")
        version = 1
        conn.execute(
            """
            INSERT OR IGNORE INTO commits
                (commit_id, project_id, branch_id, chapter_id,
                 previous_state_version, resulting_state_version, delta_id,
                 validation_json, author_approval_json, timestamp,
                 workflow_run_id, rollback_of)
            VALUES (?, ?, ?, ?, 0, ?, ?, '{}', '{}', ?, ?, NULL)
            """,
            (commit_id, pid, branch_id, chapter_id, version,
             delta_id, now_iso(), new_id("wfr")),
        )
        # 5) story_states
        conn.execute(
            """
            INSERT INTO story_states
                (project_id, state_version, snapshot_json, commit_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (pid, version, json.dumps(snap, ensure_ascii=False), commit_id, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return version


# ---------------------------------------------------------------------------
# 自动发现
# ---------------------------------------------------------------------------


def test_volumes_router_is_discovered():
    from packages.core.api.routers import discover_routers

    routers = discover_routers()
    routes = [r.path for rs in routers for r in rs.routes]
    assert any("/projects/{project_id}/volumes" in p for p in routes)


# ---------------------------------------------------------------------------
# CRUD 主链路
# ---------------------------------------------------------------------------


def test_crud_full_path(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, "vol-crud")

            # POST create
            r = await _request(
                app, "POST", f"/api/projects/{pid}/volumes",
                json={"number": 1, "title": "第一卷"},
            )
            assert r.status_code == 201, r.text
            v = r.json()
            assert v["volume_id"].startswith("vol_")
            assert v["status"] == "active"
            assert v["number"] == 1
            vid = v["volume_id"]

            # GET list（含 chapter_count=0）
            r = await _request(app, "GET", f"/api/projects/{pid}/volumes")
            assert r.status_code == 200, r.text
            lst = r.json()
            assert len(lst) == 1
            assert lst[0]["volume_id"] == vid
            assert lst[0]["chapter_count"] == 0

            # GET detail
            r = await _request(
                app, "GET", f"/api/projects/{pid}/volumes/{vid}",
            )
            assert r.status_code == 200
            assert r.json()["title"] == "第一卷"

            # PATCH title
            r = await _request(
                app, "PATCH", f"/api/projects/{pid}/volumes/{vid}",
                json={"title": "重命名"},
            )
            assert r.status_code == 200
            assert r.json()["title"] == "重命名"

            # POST assign chapter
            cid = await _make_chapter(app, pid, 1, "ch1")
            r = await _request(
                app, "POST", f"/api/projects/{pid}/volumes/{vid}/assign",
                json={"chapter_id": cid},
            )
            assert r.status_code == 200, r.text

            # chapter_count 应为 1
            r = await _request(app, "GET", f"/api/projects/{pid}/volumes")
            assert r.json()[0]["chapter_count"] == 1

            # POST seal → frozen snapshot
            snap = {"foo": "bar", "nested": {"x": 1}}
            _seed_snapshot(app, pid, snap)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/volumes/{vid}/seal",
            )
            assert r.status_code == 200, r.text
            sealed = r.json()
            assert sealed["status"] == "sealed"
            assert sealed["terminal_snapshot_json"] == snap

    asyncio.run(run())


def test_seal_without_snapshot_freezes_empty_object(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/volumes",
                json={"number": 1, "title": "v1"},
            )
            vid = r.json()["volume_id"]

            r = await _request(
                app, "POST", f"/api/projects/{pid}/volumes/{vid}/seal",
            )
            assert r.status_code == 200, r.text
            assert r.json()["terminal_snapshot_json"] == {}

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 错误码
# ---------------------------------------------------------------------------


def test_create_duplicate_number_returns_409(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r1 = await _request(
                app, "POST", f"/api/projects/{pid}/volumes",
                json={"number": 1, "title": "v1"},
            )
            assert r1.status_code == 201
            # 第二次同 number 必 sealed 旧卷后才能创建，否则 active 单例就拒了
            # 这里直接测「同 number → active 单例拒绝」
            r2 = await _request(
                app, "POST", f"/api/projects/{pid}/volumes",
                json={"number": 1, "title": "v1-dup"},
            )
            # active 单例校验先于 number 校验触发
            assert r2.status_code == 409

    asyncio.run(run())


def test_create_active_volume_conflict_returns_409(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r1 = await _request(
                app, "POST", f"/api/projects/{pid}/volumes",
                json={"number": 1, "title": "v1"},
            )
            assert r1.status_code == 201
            r2 = await _request(
                app, "POST", f"/api/projects/{pid}/volumes",
                json={"number": 2, "title": "v2"},
            )
            assert r2.status_code == 409
            assert "active" in r2.json()["detail"].lower()

    asyncio.run(run())


def test_get_unknown_volume_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "GET", f"/api/projects/{pid}/volumes/vol_nope",
            )
            assert r.status_code == 404

    asyncio.run(run())


def test_assign_to_sealed_volume_returns_409(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/volumes",
                json={"number": 1, "title": "v1"},
            )
            vid = r.json()["volume_id"]
            r = await _request(
                app, "POST", f"/api/projects/{pid}/volumes/{vid}/seal",
            )
            assert r.status_code == 200

            cid = await _make_chapter(app, pid, 1, "ch1")
            r = await _request(
                app, "POST", f"/api/projects/{pid}/volumes/{vid}/assign",
                json={"chapter_id": cid},
            )
            assert r.status_code == 409
            assert "sealed" in r.json()["detail"].lower()

    asyncio.run(run())


def test_assign_cross_project_returns_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid_a = await _make_project(app, "A")
            pid_b = await _make_project(app, "B")

            # 在 A 建一个 active volume
            r = await _request(
                app, "POST", f"/api/projects/{pid_a}/volumes",
                json={"number": 1, "title": "vA"},
            )
            vid = r.json()["volume_id"]

            # 在 B 建一个 chapter
            cid = await _make_chapter(app, pid_b, 1, "ch-in-B")

            # 把 B 的 chapter 挂到 A 的 volume → 422
            r = await _request(
                app, "POST", f"/api/projects/{pid_a}/volumes/{vid}/assign",
                json={"chapter_id": cid},
            )
            assert r.status_code == 422
            assert "project" in r.json()["detail"].lower()

    asyncio.run(run())


def test_seal_unknown_volume_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/volumes/vol_nope/seal",
            )
            assert r.status_code == 404

    asyncio.run(run())


def test_double_seal_returns_409(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/volumes",
                json={"number": 1, "title": "v1"},
            )
            vid = r.json()["volume_id"]

            r = await _request(
                app, "POST", f"/api/projects/{pid}/volumes/{vid}/seal",
            )
            assert r.status_code == 200

            r = await _request(
                app, "POST", f"/api/projects/{pid}/volumes/{vid}/seal",
            )
            assert r.status_code == 409
            assert "already sealed" in r.json()["detail"].lower()

    asyncio.run(run())
