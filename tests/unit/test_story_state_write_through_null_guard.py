"""story_state world_changes 写透 None 兜底回归测试。

根因：
真实 LLM（observer）输出的 world_changes add 项的 ``after`` 缺 ``statement`` /
``data_json`` 时，``packages/core/story_state/service.py`` 的 ``_write_through``
三处 world add 分支（location / faction / rule）原写法为：
    base_stmt = (after or {}).get("statement") if isinstance(after, dict) else ""
当 after 为 dict 但缺键时 ``.get()`` 返回 None，写透 INSERT 触发
``NOT NULL constraint failed: locations.statement``（DDL 的 ``DEFAULT ''``
只在列缺省时生效，显式传 None 仍然报错），commit run 整体 FAILED。

修复：三处分支统一做 None 兜底——
    base_stmt = ((after or {}).get("statement") if isinstance(after, dict) else None) or ""
``after`` 非 dict / 键缺失 / 值为 None 时都落到兜底值
（name 兜底为 world_id，statement 兜底 ''，data_json 兜底 {}）。

本测试覆盖（全部用 ASGI 全链路，模拟真实 observer → submit → commit 路径）：
1. ``after={"name": "xx"}``（缺 statement / data_json）三表正常落行，statement=''、data_json='{}'；
2. ``after={}``（缺 name）→ name 列落为 world_id，statement/data_json 兜底。

测试位置：``tests/unit/test_story_state_write_through_null_guard.py``。
- 该路径原先无任何专属测试文件，按任务书要求新建于此；
- 测试套件全链路（``create_app`` + httpx ASGI + lifespan + commit 端点），与
  ``tests/integration/test_story_state_*.py`` 同形态，但不依赖外部任何 fixture。
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings


# ----------------------------------------------------------------- helpers


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "null_guard") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, number: int = 1, title: str = "第一章") -> str:
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


def _evidence(chapter_id: str) -> dict:
    return {"chapter_id": chapter_id, "scene_id": None, "excerpt": "excerpt", "span": None}


def _make_meta(delta_id: str, chapter_id: str, prev_version: int) -> dict:
    return {
        "delta_id": delta_id,
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": f"wfr_{delta_id}",
        "previous_state_version": prev_version,
        "created_by": "observer:v1",
        "created_at": "2026-08-23T10:00:00+00:00",
        "supersedes": None,
        "notes": None,
    }


# ----------------------------------------------------------------- 1. 缺 statement / data_json 兜底


def test_write_through_world_add_missing_statement_data_json(tmp_path: Path):
    """三处 world add 分支：after 仅含 name，缺 statement / data_json。

    修复前：``base_stmt = (after or {}).get("statement") if isinstance(after, dict) else ""``
    当 after={"name": "xx"} 时 ``.get("statement")`` 返回 None，
    INSERT INTO locations/factions/world_rules 触发
    ``NOT NULL constraint failed: *.statement``，commit run FAILED。

    修复后：统一 None 兜底，三表各落 1 行，statement=''、data_json='{}'。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            chap = await _make_chapter(app, pid)

            # init genesis（v1）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            delta_id = "dlt_w_null_guard"
            delta = {
                **_make_meta(delta_id, chap, 1),
                "character_changes": [],
                "world_changes": [
                    # location：after 仅 name
                    {
                        "change_id": "wc_loc",
                        "op": "add",
                        "target_id": "loc_cave",
                        "world_id": "loc_cave",
                        "world_kind": "location",
                        "field": "name",
                        "after": {"name": "无名洞窟"},  # 缺 statement / data_json
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                    # faction：after 仅 name
                    {
                        "change_id": "wc_fac",
                        "op": "add",
                        "target_id": "fac_bandits",
                        "world_id": "fac_bandits",
                        "world_kind": "faction",
                        "field": "name",
                        "after": {"name": "山匪帮"},
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                    # rule：after 仅 name（world_kind=rule 需 approved=True）
                    {
                        "change_id": "wc_rule",
                        "op": "add",
                        "target_id": "wrule_no_magic",
                        "world_id": "wrule_no_magic",
                        "world_kind": "rule",
                        "field": "name",
                        "after": {"name": "禁魔法则"},
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "HIGH",  # world_kind=rule 必走 HIGH 门
                    },
                ],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }

            # submit delta
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 201, r.text
            assert r.json()["status"] == "validated"

            # commit（含 rule 需 approved=True）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_id,
                    "author_approval": {
                        "approver": "user:local:test",
                        "approved": True,  # rule 必须
                    },
                    "workflow_run_id": f"wfr_{delta_id}",
                },
            )
            assert r.status_code == 201, r.text

            # 断言三表落行 + statement='' + data_json='{}'
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                # locations
                loc_row = conn.execute(
                    "SELECT location_id, name, statement, data_json FROM locations WHERE location_id = ?",
                    ("loc_cave",),
                ).fetchone()
                assert loc_row is not None, "locations 未落行"
                assert loc_row["name"] == "无名洞窟"
                assert loc_row["statement"] == ""
                assert loc_row["data_json"] == "{}"

                # factions
                fac_row = conn.execute(
                    "SELECT faction_id, name, statement, data_json FROM factions WHERE faction_id = ?",
                    ("fac_bandits",),
                ).fetchone()
                assert fac_row is not None, "factions 未落行"
                assert fac_row["name"] == "山匪帮"
                assert fac_row["statement"] == ""
                assert fac_row["data_json"] == "{}"

                # world_rules
                rule_row = conn.execute(
                    "SELECT world_rule_id, name, statement, data_json FROM world_rules WHERE world_rule_id = ?",
                    ("wrule_no_magic",),
                ).fetchone()
                assert rule_row is not None, "world_rules 未落行"
                assert rule_row["name"] == "禁魔法则"
                assert rule_row["statement"] == ""
                assert rule_row["data_json"] == "{}"
            finally:
                conn.close()

    asyncio.run(run())


# ----------------------------------------------------------------- 2. after 缺 name → name 兜底为 world_id


def test_write_through_world_add_missing_name_falls_back_to_world_id(tmp_path: Path):
    """after 完全不含 name 键（或 after={}）时，name 列兜底为 world_id。

    验证：``base_name = ((after or {}).get("name") if isinstance(after, dict) else None) or wid``
    当 ``after={}`` 时 ``.get("name")`` 返回 None，``None or wid`` 落为 world_id。
    statement / data_json 同步兜底（'' / '{}'）。

    注：业务校验要求 ``op=add`` 时 after 非 None，故用 after={}（合法）而非 after=None。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            chap = await _make_chapter(app, pid)

            # init genesis（v1）
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            delta_id = "dlt_w_null_name"
            delta = {
                **_make_meta(delta_id, chap, 1),
                "character_changes": [],
                "world_changes": [
                    {
                        "change_id": "wc_loc",
                        "op": "add",
                        "target_id": "loc_anon",
                        "world_id": "loc_anon",
                        "world_kind": "location",
                        "field": "name",
                        "after": {},  # 缺 name / statement / data_json
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                    {
                        "change_id": "wc_fac",
                        "op": "add",
                        "target_id": "fac_anon",
                        "world_id": "fac_anon",
                        "world_kind": "faction",
                        "field": "name",
                        "after": {},
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                    {
                        "change_id": "wc_rule",
                        "op": "add",
                        "target_id": "wrule_anon",
                        "world_id": "wrule_anon",
                        "world_kind": "rule",
                        "field": "name",
                        "after": {},
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "HIGH",
                    },
                ],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }

            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 201, r.text
            assert r.json()["status"] == "validated"

            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_id,
                    "author_approval": {"approver": "user:local:test", "approved": True},
                    "workflow_run_id": f"wfr_{delta_id}",
                },
            )
            assert r.status_code == 201, r.text

            # 断言 name 兜底为 world_id
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                loc_row = conn.execute(
                    "SELECT location_id, name, statement, data_json FROM locations WHERE location_id = ?",
                    ("loc_anon",),
                ).fetchone()
                assert loc_row is not None
                assert loc_row["name"] == "loc_anon", "name 应兜底为 world_id"
                assert loc_row["statement"] == ""
                assert loc_row["data_json"] == "{}"

                fac_row = conn.execute(
                    "SELECT faction_id, name, statement, data_json FROM factions WHERE faction_id = ?",
                    ("fac_anon",),
                ).fetchone()
                assert fac_row is not None
                assert fac_row["name"] == "fac_anon", "name 应兜底为 world_id"
                assert fac_row["statement"] == ""
                assert fac_row["data_json"] == "{}"

                rule_row = conn.execute(
                    "SELECT world_rule_id, name, statement, data_json FROM world_rules WHERE world_rule_id = ?",
                    ("wrule_anon",),
                ).fetchone()
                assert rule_row is not None
                assert rule_row["name"] == "wrule_anon", "name 应兜底为 world_id"
                assert rule_row["statement"] == ""
                assert rule_row["data_json"] == "{}"
            finally:
                conn.close()

    asyncio.run(run())