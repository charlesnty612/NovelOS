"""修复 wfr_3cb2182a30f6：rollback 逆清理字段级恢复。

覆盖：
1. 端到端：temp DB 起项目→commit 含字段级 world update（faction behavior str +
   location state dict + rule）的 delta→rollback→断言：
   - 最新快照三集合条目仍为完整 dict；
   - 字段值回到原 before；
   - name/statement 完好；
   - locations 表 data_json 未被抹。
2. hint 新形状单测（apply_inverse_cleanup_to_state 直接构造 cleanup）：
   - 字段级恢复（factions[fac][field]=value）；
   - entry 非 dict 跳过；
   - world_rules field 非空按字段恢复、field 为空且 value 是 dict 按键 merge。
3. applier 防御：bucket[wid]='污染str' 时 apply_delta 不抛 TypeError，
   换新 dict 入桶（warning 触发）。
4. repair_current_snapshot_world：构造污染快照→调用修复→断言 after.factions[fac]
   是完整 dict（name/statement/data_json/visibility/relationships 齐全）；
   再次调用 repaired=False；修复前 DB 表不受影响。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.story_state.applier import apply_delta
from packages.core.story_state.snapshot import (
    repair_current_snapshot_world,
)
from packages.core.story_state.write_through import apply_inverse_cleanup_to_state

# ----------------------------------------------------------------- helpers (integration)


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _make_project(app, name: str = "F5 修复项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_character(app, pid: str, name: str = "林夕") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/characters",
        json={"name": name, "role": "protagonist"},
    )
    assert r.status_code == 201, r.text
    return r.json()["character_id"]


async def _make_chapter(app, pid: str, number: int = 1, title: str = "第一章") -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/chapters",
        json={"number": number, "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


async def _make_location(app, pid: str, name: str) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/locations",
        json={"name": name, "statement": "原初描述", "data": {"weather": "sunny"}},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_faction(app, pid: str, name: str) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/factions",
        json={"name": name, "statement": "原初阵营描述",
              "data": {"members": 5}},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_world_rule(app, pid: str, name: str, statement: str) -> str:
    r = await _request(
        app, "POST", f"/api/projects/{pid}/world-rules",
        json={"name": name, "statement": statement,
              "data": {"severity": "low"}},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


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


# ----------------------------------------------------------------- 1. e2e rollback


def test_rollback_world_field_level_restore_keeps_dict_shape_and_db_data_json(tmp_path: Path):
    """端到端：commit 含字段级 world update → rollback → 快照三集合仍是 dict，
    字段值回到原 before，locations.data_json 未被抹成 {}。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            await _make_character(app, pid)
            chap = await _make_chapter(app, pid)
            # 提前建好 location / faction / rule
            loc_id = await _make_location(app, pid, "Village")
            fac_id = await _make_faction(app, pid, "Cult")
            rule_id = await _make_world_rule(app, pid, "Law of Names", "Names have power")
            r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap})
            assert r.status_code == 201, r.text

            # commit 一条 world update（字段级）：faction behavior str + location state dict + rule
            d = {
                **_make_meta("dlt_world_field", chap, 1),
                "character_changes": [],
                "world_changes": [
                    # faction：field=data_json.behavior，after="aggressive"（str）
                    {
                        "change_id": "wc_fac_1",
                        "op": "update",
                        "target_id": fac_id,
                        "world_id": fac_id,
                        "world_kind": "faction",
                        "field": "data_json.behavior",
                        "before": "neutral",
                        "after": "aggressive",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                    # location：field=data_json.weather，after={"wind": "strong"}（局部 dict）
                    {
                        "change_id": "wc_loc_1",
                        "op": "update",
                        "target_id": loc_id,
                        "world_id": loc_id,
                        "world_kind": "location",
                        "field": "data_json.weather",
                        "before": {"wind": "calm"},
                        "after": {"wind": "strong"},
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                    # rule：field=statement，after="Names bind all"（str）
                    {
                        "change_id": "wc_rule_1",
                        "op": "update",
                        "target_id": rule_id,
                        "world_id": rule_id,
                        "world_kind": "rule",
                        "field": "statement",
                        "before": "Names have power",
                        "after": "Names bind all",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=d)
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_world_field",
                    "author_approval": {"approver": "user:local:test", "approved": True},
                    "workflow_run_id": "wfr_world_field",
                },
            )
            assert r.status_code == 201, r.text
            target_commit = r.json()["commit_id"]

            # rollback → 不抛 5xx；后续 commit 也能继续
            r = await _request(
                app, "POST", f"/api/commits/{target_commit}/rollback",
                json={"author_approval": {"approver": "user:local:test", "approved": True}},
            )
            assert r.status_code == 201, r.text

            # 断言 1：GET state 拿到最新快照，三集合仍是完整 dict
            r = await _request(app, "GET", f"/api/projects/{pid}/state")
            assert r.status_code == 200, r.text
            snap = r.json()
            world = snap["world"]
            assert isinstance(world["factions"][fac_id], dict), \
                f"factions[fac_id] 不是 dict: {world['factions'][fac_id]!r}"
            assert isinstance(world["locations"][loc_id], dict), \
                f"locations[loc_id] 不是 dict: {world['locations'][loc_id]!r}"
            rules = world["world_rules"]
            rule = next(r for r in rules if r.get("world_rule_id") == rule_id)
            assert isinstance(rule, dict)

            # 断言 2：字段值回到原 before
            assert world["factions"][fac_id]["data_json"]["behavior"] == "neutral"
            assert world["locations"][loc_id]["data_json"]["weather"] == {"wind": "calm"}
            assert rule["statement"] == "Names have power"

            # 断言 3：name / statement 完好（未被整条目替换污染）
            assert world["factions"][fac_id]["name"] == "Cult"
            assert world["factions"][fac_id]["statement"] == "原初阵营描述"
            assert world["locations"][loc_id]["name"] == "Village"
            assert world["locations"][loc_id]["statement"] == "原初描述"

            # 断言 4：DB 表 location.data_json 未被抹成 {}
            conn = sqlite3.connect(str(tmp_path / "novelos.db"))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT data_json FROM locations WHERE location_id = ?",
                    (loc_id,),
                ).fetchone()
                dj = json.loads(row["data_json"])
                assert "weather" in dj, f"location.data_json 被破坏: {dj!r}"
                # faction.data_json 必须包含原 members + 还原后的 behavior
                frow = conn.execute(
                    "SELECT data_json FROM factions WHERE faction_id = ?",
                    (fac_id,),
                ).fetchone()
                fdj = json.loads(frow["data_json"])
                assert fdj.get("members") == 5
                assert fdj.get("behavior") == "neutral"
            finally:
                conn.close()

            # 断言 5：后续 commit 能正常落盘（验证污染已彻底解决）
            d2 = {
                **_make_meta("dlt_followup", chap, 3),
                "character_changes": [],
                "world_changes": [
                    {
                        "change_id": "wc_fac_2",
                        "op": "update",
                        "target_id": fac_id,
                        "world_id": fac_id,
                        "world_kind": "faction",
                        "field": "data_json.behavior",
                        "before": "neutral",
                        "after": "diplomatic",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=d2)
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": "dlt_followup",
                    "author_approval": {"approver": "user:local:test"},
                    "workflow_run_id": "wfr_followup",
                },
            )
            assert r.status_code == 201, r.text

    asyncio.run(run())


# ----------------------------------------------------------------- 2. hint 形状单测


def _baseline_world_state() -> dict:
    return {
        "state_version": 1,
        "characters": [],
        "world": {
            "current_time_in_story": None,
            "locations": {
                "loc_a": {
                    "name": "Place A", "statement": "S_A",
                    "data_json": {"weather": "old"}, "visibility": "PUBLIC",
                },
            },
            "factions": {
                "fac_b": {
                    "name": "Group B", "statement": "S_B",
                    "data_json": {"behavior": "old"}, "visibility": "PUBLIC",
                    "relationships": [],
                },
            },
            "world_rules": [
                {
                    "world_rule_id": "rule_c",
                    "name": "Rule C", "statement": "S_C",
                    "data_json": {"severity": "low"}, "visibility": "PUBLIC",
                },
            ],
            "active_resources": {},
        },
        "hooks": [],
        "debts": [],
        "recent_events": [],
        "events": {},
    }


def test_inverse_cleanup_field_level_restore_for_faction_and_location():
    """hint 字段级恢复：factions[fac_b][data_json][behavior]=value
    locations[loc_a][data_json][weather]=value；不丢 name/statement。"""
    state = _baseline_world_state()
    cleanup = {
        "restore_location_states": [
            {"world_id": "loc_a", "field": "data_json.weather", "value": {"wind": "calm"}},
        ],
        "restore_faction_states": [
            {"world_id": "fac_b", "field": "data_json.behavior", "value": "neutral"},
        ],
        "restore_world_rule_states": [
            {"world_id": "rule_c", "field": "statement", "value": "Names have power"},
        ],
    }
    apply_inverse_cleanup_to_state(state, cleanup)
    assert state["world"]["locations"]["loc_a"]["name"] == "Place A"
    assert state["world"]["locations"]["loc_a"]["data_json"]["weather"] == {"wind": "calm"}
    assert state["world"]["factions"]["fac_b"]["name"] == "Group B"
    assert state["world"]["factions"]["fac_b"]["data_json"]["behavior"] == "neutral"
    rule = state["world"]["world_rules"][0]
    assert rule["world_rule_id"] == "rule_c"
    assert rule["statement"] == "Names have power"
    assert rule["name"] == "Rule C"


def test_inverse_cleanup_skips_non_dict_entry_and_old_shape():
    """entry 非 dict 跳过；旧形状 {world_id, before} 识别并丢弃。"""
    state = _baseline_world_state()
    cleanup = {
        "restore_location_states": [
            "not-a-dict",  # 非 dict
            {"world_id": "loc_a", "before": "old-shape-polluter"},  # 旧形状
        ],
        "restore_faction_states": [
            None,
            {"world_id": "fac_b", "before": "old-shape-polluter"},
        ],
        "restore_world_rule_states": [],
    }
    apply_inverse_cleanup_to_state(state, cleanup)
    # 条目形状未被破坏（仍是 dict，非 str）
    assert isinstance(state["world"]["locations"]["loc_a"], dict)
    assert state["world"]["locations"]["loc_a"]["name"] == "Place A"
    assert isinstance(state["world"]["factions"]["fac_b"], dict)
    assert state["world"]["factions"]["fac_b"]["name"] == "Group B"


def test_inverse_cleanup_world_rules_field_and_merge():
    """world_rules：field 非空按字段恢复；field 为空且 value 是 dict 按键 merge
    （保留 world_rule_id 不被覆盖）。"""
    state = _baseline_world_state()
    # case 1：field 非空
    cleanup = {
        "restore_world_rule_states": [
            {"world_id": "rule_c", "field": "statement", "value": "Names bind all"},
        ],
    }
    apply_inverse_cleanup_to_state(state, cleanup)
    rule = state["world"]["world_rules"][0]
    assert rule["world_rule_id"] == "rule_c"
    assert rule["statement"] == "Names bind all"

    # case 2：field 为空 + value 是 dict → merge（不丢 world_rule_id，且不允许 hint 覆盖 id）
    state2 = _baseline_world_state()
    cleanup2 = {
        "restore_world_rule_states": [
            {
                "world_id": "rule_c",
                "field": "",
                "value": {
                    "world_rule_id": "rule_hacked",  # 不应被覆盖
                    "name": "Rule C Renamed",
                    "data_json": {"severity": "high"},
                },
            },
        ],
    }
    apply_inverse_cleanup_to_state(state2, cleanup2)
    rule2 = state2["world"]["world_rules"][0]
    assert rule2["world_rule_id"] == "rule_c", "world_rule_id 被 hint 覆盖，必须拒绝"
    assert rule2["name"] == "Rule C Renamed"
    assert rule2["data_json"]["severity"] == "high"


# ----------------------------------------------------------------- 3. applier 防御


def test_applier_defends_against_non_dict_bucket_entry():
    """apply_delta 在 factions[fac]='str'（污染态）上 update 不抛 TypeError；
    换新 dict 入桶（warning 触发），新 dict 含字段值。"""
    polluted = _baseline_world_state()
    polluted["world"]["factions"]["fac_b"] = "污染str"  # 模拟 rollback 旧形状污染
    delta = {
        "world_changes": [
            {
                "change_id": "wc_def",
                "op": "update",
                "target_id": "fac_b",
                "world_id": "fac_b",
                "world_kind": "faction",
                "field": "data_json.behavior",
                "before": "old",
                "after": "new",
            },
        ],
    }
    new_state = apply_delta(polluted, delta)
    # 应当被换新 dict 写入（不再是 str）
    assert isinstance(new_state["world"]["factions"]["fac_b"], dict)
    assert new_state["world"]["factions"]["fac_b"]["data_json"]["behavior"] == "new"


# ----------------------------------------------------------------- 3b. applier rule 分支非 dict 守卫（P2-1）


def test_applier_defends_against_non_dict_world_rule_element(caplog):
    """P2-1：world_rules 列表若混入 str 元素（旧形状污染），apply_delta 的
    update/remove 路径不应抛 AttributeError——非 dict 元素应被跳过且产生 warning。"""
    import logging

    polluted = _baseline_world_state()
    # 在 world_rules list 头部混入一个 str（模拟旧形状污染残留）
    polluted["world"]["world_rules"].insert(0, "污染str-应被跳过")

    # update 路径：被污染 str 不抛 AttributeError；合法的 rule_c 应被正常处理
    # （applier rule update 走 _set_top_level(r, "name", after.get("name")) 等，
    # after 是 dict 时按 name/statement/data_json 整体替换；此处不关心具体字段，
    # 仅验证不抛、str 元素被跳过、warning 命中）。
    delta_update = {
        "world_changes": [
            {
                "change_id": "wc_rule_up",
                "op": "update",
                "target_id": "rule_c",
                "world_id": "rule_c",
                "world_kind": "rule",
                "field": "",
                "after": {"name": "Rule C Renamed"},
            },
        ],
    }
    with caplog.at_level(logging.WARNING, logger="packages.core.story_state.applier"):
        new_state = apply_delta(polluted, delta_update)
    # 污染的 str 应仍在原位（被跳过），未抛 AttributeError
    assert new_state["world"]["world_rules"][0] == "污染str-应被跳过"
    rule_c = next(
        r for r in new_state["world"]["world_rules"]
        if isinstance(r, dict) and r.get("world_rule_id") == "rule_c"
    )
    # rule_c 本身可被正常 update（name 字段被改名）
    assert rule_c["name"] == "Rule C Renamed"
    # 至少一条 warning 命中 rule 分支非 dict 守卫
    assert any("world_rules 含非 dict 元素" in rec.message for rec in caplog.records)

    # remove 路径：被污染 str 应被过滤掉（不抛异常）
    polluted2 = _baseline_world_state()
    polluted2["world"]["world_rules"].insert(0, "污染str-应被过滤")
    delta_remove = {
        "world_changes": [
            {
                "change_id": "wc_rule_rm",
                "op": "remove",
                "target_id": "rule_c",
                "world_id": "rule_c",
                "world_kind": "rule",
            },
        ],
    }
    with caplog.at_level(logging.WARNING, logger="packages.core.story_state.applier"):
        new_state2 = apply_delta(polluted2, delta_remove)
    # 污染的 str 应被过滤，rule_c 应被移除
    rules_after = new_state2["world"]["world_rules"]
    assert "污染str-应被过滤" not in rules_after
    assert all(
        not (isinstance(r, dict) and r.get("world_rule_id") == "rule_c")
        for r in rules_after
    )
    assert any("world_rules 含非 dict 元素" in rec.message for rec in caplog.records)


# ----------------------------------------------------------------- 4. repair_current_snapshot_world


def _setup_minimal_db(tmp_path: Path) -> tuple[str, str]:
    """构造一个最小可跑 init_genesis + 实体写入的 DB，返回 (db_path, project_id)。"""
    from packages.core.api.main import create_app as _create
    s = Settings(data_dir=tmp_path, log_level="WARNING")
    app = _create(s)

    async def _go():
        async with app.router.lifespan_context(app):
            async with _make_client(app) as client:
                r = await client.post("/api/projects", json={"name": "repair"})
                pid = r.json()["project_id"]
                r = await client.post(
                    f"/api/projects/{pid}/characters",
                    json={"name": "Alice", "role": "protagonist"},
                )
                r = await client.post(
                    f"/api/projects/{pid}/chapters",
                    json={"number": 1, "title": "第一章"},
                )
                chap = r.json()["chapter_id"]
                await client.post(
                    f"/api/projects/{pid}/locations",
                    json={"name": "X", "statement": "SX", "data": {"k": "v"}},
                )
                await client.post(
                    f"/api/projects/{pid}/factions",
                    json={"name": "X-fac", "statement": "SX", "data": {"m": 1}},
                )
                await client.post(
                    f"/api/projects/{pid}/world-rules",
                    json={"name": "RX", "statement": "SR", "data": {}},
                )
                r = await client.post(
                    f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
                )
                assert r.status_code == 201, r.text
                return pid

    pid = asyncio.run(_go())
    return str(tmp_path / "novelos.db"), pid


def _inject_world_pollution(db_path: str, project_id: str) -> None:
    """直接 mutate story_states.snapshot_json，模拟 rollback 旧形状污染。

    从 story_states 拿到实际的世界实体 ID（loc_xxx / fac_xxx / rule_xxx），
    然后注入污染：factions[id] = str；locations[id] = 残壳 dict（无 name/statement）；
    world_rules[0] = str。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT state_version, snapshot_json FROM story_states "
            "WHERE project_id = ? ORDER BY state_version DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        snap = json.loads(row["snapshot_json"])
        loc_id = next(iter(snap["world"]["locations"].keys()))
        fac_id = next(iter(snap["world"]["factions"].keys()))
        # 污染：factions[fac_id] = str；locations[loc_id] = 残壳 dict（无 name/statement）
        snap["world"]["factions"][fac_id] = "污染串"
        snap["world"]["locations"][loc_id] = {"data_json": {"weather": "broken"}}
        snap["world"]["world_rules"][0] = "rule-str-polluted"
        conn.execute(
            "UPDATE story_states SET snapshot_json = ? WHERE project_id = ? AND state_version = ?",
            (json.dumps(snap, ensure_ascii=False), project_id, row["state_version"]),
        )
        conn.commit()
    finally:
        conn.close()


def test_repair_current_snapshot_world_restores_from_db(tmp_path: Path):
    """生产数据修复：注入三类污染 → 调 repair → 断言三集合重建为完整 dict，
    且 DB 表本身（locations/factions/world_rules）不被 repair 改动。"""
    db_path, pid = _setup_minimal_db(tmp_path)
    _inject_world_pollution(db_path, pid)

    # 拿动态生成的实体 ID
    conn_meta = sqlite3.connect(db_path)
    conn_meta.row_factory = sqlite3.Row
    try:
        loc_id = conn_meta.execute(
            "SELECT location_id FROM locations WHERE project_id = ? LIMIT 1", (pid,)
        ).fetchone()["location_id"]
        fac_id = conn_meta.execute(
            "SELECT faction_id FROM factions WHERE project_id = ? LIMIT 1", (pid,)
        ).fetchone()["faction_id"]
        rule_id = conn_meta.execute(
            "SELECT world_rule_id FROM world_rules WHERE project_id = ? LIMIT 1", (pid,)
        ).fetchone()["world_rule_id"]
        # 记录 DB 表原始状态
        loc_before = dict(conn_meta.execute(
            "SELECT location_id, name, statement, data_json FROM locations WHERE location_id = ?",
            (loc_id,),
        ).fetchone())
        fac_before = dict(conn_meta.execute(
            "SELECT faction_id, name, statement, data_json FROM factions WHERE faction_id = ?",
            (fac_id,),
        ).fetchone())
        rule_before = dict(conn_meta.execute(
            "SELECT world_rule_id, name, statement, data_json FROM world_rules WHERE world_rule_id = ?",
            (rule_id,),
        ).fetchone())
    finally:
        conn_meta.close()

    summary = repair_current_snapshot_world(db_path, pid)
    assert summary["repaired"] is True
    # after 形状应为完整 dict
    assert summary["after"]["factions"][fac_id].startswith("dict(")
    assert summary["after"]["locations"][loc_id].startswith("dict(")
    # rule 应是 dict(world_rule_id, name, ...)
    rule_shape = summary["after"]["world_rules"][0]["shape"]
    assert rule_shape.startswith("dict(") and "world_rule_id" in rule_shape
    # before 记录了污染
    assert summary["before"]["factions"][fac_id] == "str"
    assert summary["before"]["world_rules"][0]["shape"] == "str"

    # 验证 story_states 落盘已修复
    conn_after = sqlite3.connect(db_path)
    conn_after.row_factory = sqlite3.Row
    try:
        row = conn_after.execute(
            "SELECT snapshot_json FROM story_states WHERE project_id = ? "
            "ORDER BY state_version DESC LIMIT 1",
            (pid,),
        ).fetchone()
        snap = json.loads(row["snapshot_json"])
        fac = snap["world"]["factions"][fac_id]
        loc = snap["world"]["locations"][loc_id]
        rule = snap["world"]["world_rules"][0]
        assert isinstance(fac, dict)
        assert fac["name"] == "X-fac"
        assert fac["statement"] == "SX"
        assert fac["data_json"] == {"m": 1}
        assert isinstance(loc, dict)
        assert loc["name"] == "X"
        assert loc["statement"] == "SX"
        assert isinstance(rule, dict)
        assert rule["world_rule_id"] == rule_id
        assert rule["name"] == "RX"
        # DB 表本身未被 repair 改动
        assert loc_before == dict(conn_after.execute(
            "SELECT location_id, name, statement, data_json FROM locations WHERE location_id = ?",
            (loc_id,),
        ).fetchone())
        assert fac_before == dict(conn_after.execute(
            "SELECT faction_id, name, statement, data_json FROM factions WHERE faction_id = ?",
            (fac_id,),
        ).fetchone())
        assert rule_before == dict(conn_after.execute(
            "SELECT world_rule_id, name, statement, data_json FROM world_rules WHERE world_rule_id = ?",
            (rule_id,),
        ).fetchone())
    finally:
        conn_after.close()

    # 再调一次：repaired=False（幂等）
    summary2 = repair_current_snapshot_world(db_path, pid)
    assert summary2["repaired"] is False


# ----------------------------------------------------------------- 5. repair_current_snapshot_world 非 dict world 守卫（P2-2）


def _inject_non_dict_world(db_path: str, project_id: str) -> None:
    """将 story_states.snapshot_json 的 world 整体替换为 str，模拟
    materialize 之外的极端污染（解析出非 dict world）。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT state_version, snapshot_json FROM story_states "
            "WHERE project_id = ? ORDER BY state_version DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        snap = json.loads(row["snapshot_json"])
        snap["world"] = "this-should-be-a-dict"  # 强制 world 为 str
        conn.execute(
            "UPDATE story_states SET snapshot_json = ? WHERE project_id = ? AND state_version = ?",
            (json.dumps(snap, ensure_ascii=False), project_id, row["state_version"]),
        )
        conn.commit()
    finally:
        conn.close()


def test_repair_current_snapshot_world_handles_non_dict_world(tmp_path: Path):
    """P2-2：snapshot_json 的 world 为 str 时 repair 不抛 TypeError，
    按空 dict 路径处理，world 三集合被重建为 DB 权威集合。"""
    db_path, pid = _setup_minimal_db(tmp_path)
    _inject_non_dict_world(db_path, pid)

    # 不应抛 TypeError
    summary = repair_current_snapshot_world(db_path, pid)

    # repair 不抛异常；world 三集合被按 DB 重建
    assert summary["state_version"] is not None
    assert isinstance(summary["after"]["locations"], dict)
    assert isinstance(summary["after"]["factions"], dict)
    assert isinstance(summary["after"]["world_rules"], list)
    # world 至少有一项重建（来自 DB 权威）
    assert len(summary["after"]["locations"]) >= 1 or len(summary["after"]["factions"]) >= 1

    # 落盘后 world 应为 dict（含三集合键）
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT snapshot_json FROM story_states WHERE project_id = ? "
            "ORDER BY state_version DESC LIMIT 1",
            (pid,),
        ).fetchone()
        snap = json.loads(row["snapshot_json"])
        assert isinstance(snap["world"], dict)
        assert "locations" in snap["world"]
        assert "factions" in snap["world"]
        assert "world_rules" in snap["world"]
    finally:
        conn.close()
