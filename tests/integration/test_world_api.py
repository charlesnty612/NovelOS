"""/api/projects/{pid}/locations|factions|world-rules 集成测试（Sprint 1）。

写法参考 ``tests/integration/test_health.py``：
- httpx.ASGITransport + 自管事件循环（不引入 pytest-asyncio）
- 每个用例用 ``tmp_path`` 注入独立 SQLite，避免迁移状态污染

测试方式：
- ``create_app(settings)`` 构建 FastAPI 应用，``discover_routers`` 自动挂载 ``worlds.py`` 路由。
- 验证 Service → Router → HTTP 主链路。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection


def _make_client(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _build_app(tmp_path: Path, *, pre_insert_project: bool = True) -> tuple[object, str | None]:
    """构造最小测试 app：迁移 + （预插入 project） + include_router(worlds)。

    返回 ``(app, project_id)``。``pre_insert_project=False`` 用于测 project 不存在的负例。
    """
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    project_id: str | None = None
    if pre_insert_project:
        conn = get_connection(settings.db_path)
        project_id = f"prj_{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, "测试项目", "ACTIVE", now, now),
        )
        conn.commit()
        conn.close()

    app = create_app(settings)
    # create_app 已经通过 discover_routers() 自动挂载了 worlds.py，
    # 并由 main.py 统一加上 /api 前缀；这里不需要重复 include。
    return app, project_id


def _run(coro):
    return asyncio.run(coro)


def test_locations_crud_happy_path(tmp_path: Path):
    app, pid = _build_app(tmp_path)
    assert pid is not None

    async def main():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as c:
                # create
                r = await c.post(
                    f"/api/projects/{pid}/locations",
                    json={
                        "name": "云海城",
                        "statement": "悬浮在云端的贸易都市",
                        "data": {"climate": "潮湿", "tier": "metropolis"},
                        "visibility": "PUBLIC",
                    },
                )
                assert r.status_code == 201, r.text
                loc = r.json()
                assert loc["id"].startswith("loc_")
                assert loc["name"] == "云海城"
                assert loc["data"]["tier"] == "metropolis"
                loc_id = loc["id"]

                # get
                r = await c.get(f"/api/locations/{loc_id}")
                assert r.status_code == 200
                assert r.json()["name"] == "云海城"

                # list
                r = await c.get(f"/api/projects/{pid}/locations")
                assert r.status_code == 200
                assert len(r.json()) == 1

                # update
                r = await c.patch(
                    f"/api/locations/{loc_id}",
                    json={"statement": "更新：云端贸易中枢"},
                )
                assert r.status_code == 200
                assert r.json()["statement"].startswith("更新")

                # delete (204 No Content)
                r = await c.delete(f"/api/locations/{loc_id}")
                assert r.status_code == 204
                assert r.text == ""

                r = await c.get(f"/api/locations/{loc_id}")
                assert r.status_code == 404

    _run(main())


def test_location_404_on_get(tmp_path: Path):
    app, _ = _build_app(tmp_path)

    async def main():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as c:
                r = await c.get("/api/locations/loc_does_not_exist")
                assert r.status_code == 404

    _run(main())


def test_location_invalid_visibility_422(tmp_path: Path):
    app, pid = _build_app(tmp_path)
    assert pid is not None

    async def main():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as c:
                r = await c.post(
                    f"/api/projects/{pid}/locations",
                    json={"name": "x", "visibility": "BOGUS"},
                )
                assert r.status_code == 422

    _run(main())


def test_location_delete_referenced_409(tmp_path: Path):
    """location 被 plot_events 引用时拒绝删除（409）。"""
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    conn = get_connection(settings.db_path)
    now = datetime.now(timezone.utc).isoformat()
    pid = f"prj_{uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (pid, "test", "ACTIVE", now, now),
    )
    conn.commit()

    # 直接通过 SQLite 插入一个 location 和一个引用它的 plot_event
    conn.execute(
        "INSERT INTO locations (location_id, project_id, name, statement, data_json, "
        "visibility, who_knows, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '{}', 'PUBLIC', NULL, ?, ?)",
        ("loc_refme", pid, "被引用地点", "", now, now),
    )
    conn.execute(
        "INSERT INTO plot_events (event_id, project_id, type, cause_json, effects_json, "
        "participants_json, location_id, time_json, status, visibility, who_knows) "
        "VALUES (?, ?, 'revelation', '[]', '[]', '[]', ?, "
        "'{\"timeline_day\":1}', 'planned', 'RESTRICTED', NULL)",
        ("event_refme", pid, "loc_refme"),
    )
    conn.commit()
    conn.close()

    app = create_app(settings)

    async def main():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as c:
                r = await c.delete("/api/locations/loc_refme")
                assert r.status_code == 409, r.text
                assert "引用" in r.json()["detail"]

    _run(main())


def test_factions_and_world_rules_basic(tmp_path: Path):
    app, pid = _build_app(tmp_path)
    assert pid is not None

    async def main():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as c:
                # factions 默认 visibility=VISIBLE
                r = await c.post(
                    f"/api/projects/{pid}/factions",
                    json={"name": "青云宗", "statement": "正道之首"},
                )
                assert r.status_code == 201, r.text
                fac = r.json()
                assert fac["id"].startswith("fac_")
                assert fac["visibility"] == "VISIBLE"

                r = await c.get(f"/api/projects/{pid}/factions")
                assert r.status_code == 200
                assert len(r.json()) == 1

                # world_rules 默认 visibility=PUBLIC
                r = await c.post(
                    f"/api/projects/{pid}/world-rules",
                    json={"name": "灵力法则", "statement": "灵气稀薄者不可越境"},
                )
                assert r.status_code == 201, r.text
                wr = r.json()
                assert wr["id"].startswith("wrule_")
                assert wr["visibility"] == "PUBLIC"

                r = await c.get(f"/api/projects/{pid}/world-rules")
                assert r.status_code == 200
                assert len(r.json()) == 1

                r = await c.get(f"/api/factions/{fac['id']}")
                assert r.status_code == 200

                r = await c.delete(f"/api/world-rules/{wr['id']}")
                assert r.status_code == 204
                assert r.text == ""

    _run(main())


def test_missing_project_returns_422(tmp_path: Path):
    app, _ = _build_app(tmp_path, pre_insert_project=False)

    async def main():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as c:
                r = await c.post(
                    "/api/projects/prj_no_such_project/locations",
                    json={"name": "孤儿地点"},
                )
                # project 不存在 → ValidationError → 422
                assert r.status_code == 422, r.text

    _run(main())
