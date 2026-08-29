"""/api/projects/{pid}/events + timeline + relationships 集成测试（Sprint 1）。

写法参考 ``tests/integration/test_world_api.py``：
- httpx.ASGITransport + 自管事件循环
- 每个用例用 ``tmp_path`` 注入独立 SQLite
- ``create_app(settings)`` 构建应用，``discover_routers`` 自动挂载 ``plots.py``

覆盖：
- event 主链路 create→get→list→update→delete
- cause 引用不存在的 event_id → 422
- 创建 event 时若指定 time.timeline_day → 时间线自动同步出现对应行
- 过滤参数 ?type=&status= 生效
- timeline 显式插入/删除
- relationships 只读端点
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


def _build_app(
    tmp_path: Path,
    *,
    with_characters: int = 0,
    with_location: bool = False,
) -> tuple[object, str, list[str], str | None]:
    """构造最小测试 app。

    返回 ``(app, project_id, character_ids, location_id)``。
    """
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    conn = get_connection(settings.db_path)
    pid = f"prj_{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (pid, "test", "ACTIVE", now, now),
    )

    char_ids: list[str] = []
    for i in range(with_characters):
        cid = f"char_{uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO characters (character_id, project_id, name, role, core_json, "
            "visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, 'supporting', '{}', 'PUBLIC', NULL, ?, ?)",
            (cid, pid, f"角色{i}", now, now),
        )
        char_ids.append(cid)

    location_id: str | None = None
    if with_location:
        location_id = f"loc_{uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO locations (location_id, project_id, name, statement, data_json, "
            "visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, '', '{}', 'PUBLIC', NULL, ?, ?)",
            (location_id, pid, "测试地点", now, now),
        )

    conn.commit()
    conn.close()

    app = create_app(settings)
    # create_app 已经通过 discover_routers() 自动挂载了 plots.py
    return app, pid, char_ids, location_id


def _run(coro):
    return asyncio.run(coro)


def test_event_crud_happy_path(tmp_path: Path):
    app, pid, chars, _ = _build_app(tmp_path, with_characters=2)

    async def main():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as c:
                # create
                r = await c.post(
                    f"/api/projects/{pid}/events",
                    json={
                        "type": "revelation",
                        "participants": chars,
                        "time": {"timeline_day": 5, "in_story_date": "霜月十四"},
                        "status": "planned",
                        "visibility": "RESTRICTED",
                    },
                )
                assert r.status_code == 201, r.text
                ev = r.json()
                assert ev["id"].startswith("event_")
                assert ev["type"] == "revelation"
                assert ev["time"]["timeline_day"] == 5
                eid = ev["id"]

                # get
                r = await c.get(f"/api/events/{eid}")
                assert r.status_code == 200

                # list
                r = await c.get(f"/api/projects/{pid}/events")
                assert r.status_code == 200
                assert len(r.json()) == 1

                # update
                r = await c.patch(
                    f"/api/events/{eid}",
                    json={"status": "recorded"},
                )
                assert r.status_code == 200
                assert r.json()["status"] == "recorded"

                # delete（无引用 → 成功；204 No Content）
                r = await c.delete(f"/api/events/{eid}")
                assert r.status_code == 204
                assert r.text == ""

                r = await c.get(f"/api/events/{eid}")
                assert r.status_code == 404

    _run(main())


def test_event_cause_references_missing_event_422(tmp_path: Path):
    app, pid, _, _ = _build_app(tmp_path)

    async def main():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as c:
                r = await c.post(
                    f"/api/projects/{pid}/events",
                    json={
                        "type": "conflict",
                        "cause": ["event_does_not_exist_xxx"],
                        "time": {"timeline_day": 1},
                    },
                )
                assert r.status_code == 422, r.text
                assert "cause" in r.json()["detail"]

    _run(main())


def test_event_participants_missing_character_422(tmp_path: Path):
    app, pid, _, _ = _build_app(tmp_path)

    async def main():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as c:
                r = await c.post(
                    f"/api/projects/{pid}/events",
                    json={
                        "type": "encounter",
                        "participants": ["char_ghost"],
                        "time": {"timeline_day": 1},
                    },
                )
                assert r.status_code == 422, r.text
                assert "participants" in r.json()["detail"]

    _run(main())


def test_event_create_auto_inserts_timeline(tmp_path: Path):
    """创建 event 时若 time.timeline_day=7 → /timeline 自动出现 day_index=7 的行。"""
    app, pid, _, _ = _build_app(tmp_path)

    async def main():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as c:
                r = await c.post(
                    f"/api/projects/{pid}/events",
                    json={
                        "type": "decision",
                        "time": {"timeline_day": 7},
                    },
                )
                assert r.status_code == 201, r.text

                r = await c.get(f"/api/projects/{pid}/timeline")
                assert r.status_code == 200
                tlines = r.json()
                assert len(tlines) == 1
                assert tlines[0]["day_index"] == 7

    _run(main())


def test_event_list_filters(tmp_path: Path):
    app, pid, _, _ = _build_app(tmp_path)

    async def main():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as c:
                # 创建 3 条不同 type / status 的 event
                await c.post(
                    f"/api/projects/{pid}/events",
                    json={"type": "revelation", "time": {"timeline_day": 1}},
                )
                await c.post(
                    f"/api/projects/{pid}/events",
                    json={"type": "conflict", "time": {"timeline_day": 2}},
                )
                await c.post(
                    f"/api/projects/{pid}/events",
                    json={
                        "type": "conflict",
                        "status": "recorded",
                        "time": {"timeline_day": 3},
                    },
                )

                # 无过滤 → 3
                r = await c.get(f"/api/projects/{pid}/events")
                assert len(r.json()) == 3

                # type=conflict → 2
                r = await c.get(f"/api/projects/{pid}/events?type=conflict")
                assert len(r.json()) == 2

                # status=recorded → 1
                r = await c.get(f"/api/projects/{pid}/events?status=recorded")
                assert len(r.json()) == 1

                # 组合 type=conflict & status=recorded → 1
                r = await c.get(
                    f"/api/projects/{pid}/events?type=conflict&status=recorded"
                )
                assert len(r.json()) == 1

                # 非法 type → 422
                r = await c.get(f"/api/projects/{pid}/events?type=BOGUS")
                assert r.status_code == 422

    _run(main())


def test_event_delete_referenced_409(tmp_path: Path):
    """event A 被 event B 的 cause 引用 → 删除 A 应返回 409。"""
    app, pid, _, _ = _build_app(tmp_path)

    async def main():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as c:
                r1 = await c.post(
                    f"/api/projects/{pid}/events",
                    json={"type": "revelation", "time": {"timeline_day": 1}},
                )
                a_id = r1.json()["id"]

                r2 = await c.post(
                    f"/api/projects/{pid}/events",
                    json={
                        "type": "conflict",
                        "cause": [a_id],
                        "time": {"timeline_day": 2},
                    },
                )
                assert r2.status_code == 201, r2.text

                # 尝试删除 A 应失败
                r = await c.delete(f"/api/events/{a_id}")
                assert r.status_code == 409, r.text
                assert "cause" in r.json()["detail"]

    _run(main())


def test_timeline_explicit_create_and_delete(tmp_path: Path):
    app, pid, _, _ = _build_app(tmp_path)

    async def main():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as c:
                # 先建 event（会同步 timeline_day=1）
                r = await c.post(
                    f"/api/projects/{pid}/events",
                    json={"type": "transition", "time": {"timeline_day": 1}},
                )
                ev_id = r.json()["id"]

                # 手动追加一条
                r = await c.post(
                    f"/api/projects/{pid}/timeline",
                    json={
                        "event_id": ev_id,
                        "day_index": 99,
                        "time_ref": "23:30",
                        "description": "深夜追加",
                    },
                )
                assert r.status_code == 201, r.text
                tle = r.json()
                assert tle["day_index"] == 99
                tle_id = tle["id"]

                r = await c.get(f"/api/projects/{pid}/timeline")
                # 至少 2 条（自动 + 手动），按 day_index 排序 → 自动的 1 在前
                rows = r.json()
                assert rows[0]["day_index"] == 1
                assert any(x["id"] == tle_id for x in rows)

                r = await c.delete(f"/api/timeline/{tle_id}")
                assert r.status_code == 204
                assert r.text == ""

                r = await c.delete(f"/api/timeline/{tle_id}")
                assert r.status_code == 404

    _run(main())


def test_relationships_read_only(tmp_path: Path):
    """relationships S1 只读端点：能列项目下的关系。"""
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    conn = get_connection(settings.db_path)
    pid = f"prj_{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (pid, "t", "ACTIVE", now, now),
    )
    ca = f"char_{uuid4().hex[:12]}"
    cb = f"char_{uuid4().hex[:12]}"
    for cid in (ca, cb):
        conn.execute(
            "INSERT INTO characters (character_id, project_id, name, role, core_json, "
            "visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, 'supporting', '{}', 'PUBLIC', NULL, ?, ?)",
            (cid, pid, "c", now, now),
        )
    conn.execute(
        "INSERT INTO relationships (relationship_id, project_id, from_character_id, "
        "to_character_id, relation_type, state_json, last_state_version) "
        "VALUES (?, ?, ?, ?, 'ally', '{\"intensity\":0.8}', 1)",
        (f"rel_{uuid4().hex[:12]}", pid, ca, cb),
    )
    conn.commit()
    conn.close()

    app = create_app(settings)

    async def main():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as c:
                r = await c.get(f"/api/projects/{pid}/relationships")
                assert r.status_code == 200
                rels = r.json()
                assert len(rels) == 1
                assert rels[0]["relation_type"] == "ally"
                assert rels[0]["state"]["intensity"] == 0.8

    _run(main())


def test_event_create_missing_timeline_day_422_with_readable_detail(tmp_path: Path):
    """time 缺 timeline_day → 422，detail 含可读中文提示。

    白盒审计发现 PlotTab 旧表单默认提交 ``time={}``，必然 422；
    仅当后端错误信息足够可读，前端才容易把问题定位到「缺少 timeline_day」。
    """
    app, pid, _, _ = _build_app(tmp_path)

    async def main():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as c:
                r = await c.post(
                    f"/api/projects/{pid}/events",
                    json={"type": "revelation", "time": {}},
                )
                assert r.status_code == 422, r.text
                detail = r.json()["detail"]
                # 必须指出 timeline_day 字段缺失，且有中文提示便于前端/用户看懂
                assert "timeline_day" in detail, detail
                assert "时间" in detail, detail

    _run(main())

    _run(main())
