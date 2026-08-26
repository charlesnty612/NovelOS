"""V3.1 P1-1: commit 事务内以 DB 为权威重建快照的回归测试。

覆盖三类场景:
1. **漂移自愈**(核心价值):手工构造"DB 有 plot_events 行但 snapshot.events 中没有"
   的历史漂移,跑一次 commit_delta,断言新快照自愈(包含该 event_id)。
2. **正向一致**:无漂移场景下 commit_delta 后,新快照的 7 个集合与 DB 完全对齐
   (按 ``check_state_sync.COLLECTIONS`` 字段口径做 id 集合对照)。
3. **既有行为兼容**:commit_delta 返回 (commit_id/state_version/snapshot_ref/delta_id),
   commits 行写入正确,state_deltas.status='applied',story_states 行存在。

测试模式与 ``tests/unit/test_story_state_write_through_null_guard.py`` 对齐:
ASGI 全链路(create_app + httpx ASGI + lifespan + commit 端点)。
"""

from __future__ import annotations

import asyncio
import json
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


async def _make_project(app, name: str = "snapshot_rebuild") -> str:
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
        "created_at": "2026-08-24T10:00:00+00:00",
        "supersedes": None,
        "notes": None,
    }


def _open_db(db_path: Path) -> sqlite3.Connection:
    """直接打开底层 sqlite3(只读路径外的写路径用于造漂移)。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_latest_snapshot_json(db_path: Path, project_id: str) -> dict:
    """直接读最新 story_states.snapshot_json(绕过 API 便于精确断言 DB 列内容)。"""
    conn = _open_db(db_path)
    try:
        row = conn.execute(
            """
            SELECT state_version, snapshot_json
            FROM story_states
            WHERE project_id = ?
            ORDER BY state_version DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        assert row is not None, "story_states 无快照"
        return json.loads(row["snapshot_json"])
    finally:
        conn.close()


# ----------------------------------------------------------------- 7 集合 DB-snapshot 对照口径
# 与 scripts/check_state_sync.COLLECTIONS 严格一致;测试独立持有副本,
# 避免运行时依赖 scripts 子模块(测试不应跨域引用运维脚本)。

_COLLECTIONS_CFG: list[dict] = [
    {
        "name": "characters",
        "db_table": "characters",
        "db_id_column": "character_id",
        "snapshot_path": ("characters",),
        "kind": "list_of_dicts",
        "id_field": "character_id",
    },
    {
        "name": "locations",
        "db_table": "locations",
        "db_id_column": "location_id",
        "snapshot_path": ("world", "locations"),
        "kind": "dict_keys",
        "id_field": None,
    },
    {
        "name": "factions",
        "db_table": "factions",
        "db_id_column": "faction_id",
        "snapshot_path": ("world", "factions"),
        "kind": "dict_keys",
        "id_field": None,
    },
    {
        "name": "world_rules",
        "db_table": "world_rules",
        "db_id_column": "world_rule_id",
        "snapshot_path": ("world", "world_rules"),
        "kind": "list_field",
        "id_field": "world_rule_id",
    },
    {
        "name": "plot_events",
        "db_table": "plot_events",
        "db_id_column": "event_id",
        "snapshot_path": ("events",),
        "kind": "dict_keys",
        "id_field": None,
    },
    {
        "name": "hooks",
        "db_table": "hooks",
        "db_id_column": "hook_id",
        "snapshot_path": ("hooks",),
        "kind": "list_of_dicts",
        "id_field": "hook_id",
    },
    {
        "name": "narrative_debts",
        "db_table": "narrative_debts",
        "db_id_column": "debt_id",
        "snapshot_path": ("debts",),
        "kind": "list_of_dicts",
        "id_field": "debt_id",
    },
]


def _drift_ids_for(db_path: Path, project_id: str, snapshot: dict) -> list[tuple[str, list[str], list[str]]]:
    """返回 [(collection_name, only_in_db, only_in_snapshot), ...] 仅包含有漂移的项。"""
    conn = _open_db(db_path)
    drifts: list[tuple[str, list[str], list[str]]] = []
    try:
        for cfg in _COLLECTIONS_CFG:
            table = cfg["db_table"]
            id_col = cfg["db_id_column"]
            try:
                db_rows = conn.execute(
                    f"SELECT {id_col} FROM {table} WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
                db_ids = {str(r[0]) for r in db_rows if r[0] is not None}
            except sqlite3.Error:
                continue
            # 快照侧抽取
            node: snapshot = snapshot  # type: ignore[assignment]
            for key in cfg["snapshot_path"]:
                if not isinstance(node, dict):
                    node = None  # type: ignore[assignment]
                    break
                node = node.get(key)  # type: ignore[assignment]
            if node is None:
                snap_ids: set[str] = set()
            elif cfg["kind"] == "dict_keys":
                snap_ids = {str(k) for k in node.keys()} if isinstance(node, dict) else set()
            elif cfg["kind"] == "list_of_dicts":
                snap_ids = set()
                if isinstance(node, list):
                    for item in node:
                        if isinstance(item, dict):
                            v = item.get(cfg["id_field"])  # type: ignore[arg-type]
                            if v is not None:
                                snap_ids.add(str(v))
            elif cfg["kind"] == "list_field":
                snap_ids = set()
                if isinstance(node, list):
                    for item in node:
                        if isinstance(item, dict):
                            v = item.get(cfg["id_field"])  # type: ignore[arg-type]
                            if v is not None:
                                snap_ids.add(str(v))
            else:
                snap_ids = set()
            only_db = sorted(db_ids - snap_ids)
            only_snap = sorted(snap_ids - db_ids)
            if only_db or only_snap:
                drifts.append((cfg["name"], only_db, only_snap))
    finally:
        conn.close()
    return drifts


# ----------------------------------------------------------------- 1. 漂移自愈(核心)


def test_drift_self_heal_plot_event(tmp_path: Path):
    """DB 已有 plot_events 行但旧快照未含该 event_id,跑一次 commit_delta 后自愈。

    模拟历史漂移场景(早期版本实测 loc_yonghe_wharf 在 DB 不在快照)——直接
    INSERT 一行 plot_events 制造漂移,然后跑 commit_delta。断言新快照 v2 自愈:
    ``ev_drift_test`` 必须出现在 snapshot.events 中,且 type 一致。

    V3.1 P1-1.1：迁移 0013 给 plot_events 加了 description 列，本测试同时验证
    description 经 DB 权威重建后保留——直接 SQL 写入时一并把 description 填好，
    重建后 ``events[ev_drift_test].description`` 应为该值（替代早期"必为 None"断言，
    该断言语义已被 V3.1 P1-1.1 推翻）。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="drift_self_heal")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            # init genesis(v1)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            # 制造漂移:直接 SQL 写一行 plot_events,绕过 write_through。
            # 这是模拟"DB 已落行但 snapshot.events 没追上"的早期版本 bug。
            # V3.1 P1-1.1：同时写入 description 字段,验证重建后该字段保留。
            drift_desc = "苏婉清在玉惜轩夜访时第一次对林渊产生疑虑。"
            conn = _open_db(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO plot_events
                        (event_id, project_id, type, cause_json, effects_json,
                         participants_json, location_id, time_json, status,
                         introduced_chapter_id, visibility, who_knows, description)
                    VALUES (?, ?, ?, '[]', '[]', '[]', NULL,
                            '{"timeline_day":1}', 'recorded', ?, 'RESTRICTED', NULL, ?)
                    """,
                    ("ev_drift_test", pid, "encounter", chap, drift_desc),
                )
                conn.commit()
            finally:
                conn.close()

            # 旧快照 v1 不含该 event(漂移已存在)
            snap_v1 = _fetch_latest_snapshot_json(db_path, pid)
            assert "ev_drift_test" not in snap_v1.get("events", {}), (
                "v1 快照预条件:ev_drift_test 不在 snapshot.events 中(模拟漂移)"
            )

            # 跑一个最小 commit_delta。delta 只带 new_hooks(LOW 风险,无需 approved=True),
            # 让 commit 流程执行 write_through→rebuild→materialize_snapshot 全链。
            delta_id = "dlt_rebuild_after_drift"
            delta = {
                **_make_meta(delta_id, chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [
                    {
                        "change_id": "nh_rebuild_1",
                        "op": "add",
                        "target_id": "hk_rebuild_marker",
                        "hook_id": "hk_rebuild_marker",
                        "name": "重建标记钩",
                        "importance": 0.5,
                        "description": "V3.1 P1-1 漂移自愈测试用钩",
                        "expected_payoff_chapter_id": None,
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "debt_changes": [],
            }

            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 201, r.text
            assert r.json()["status"] == "validated"

            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_id,
                    "author_approval": {"approver": "user:local:test", "approved": False},
                    "workflow_run_id": f"wfr_{delta_id}",
                },
            )
            assert r.status_code == 201, r.text

            # 核心断言:v2 新快照自愈
            snap_v2 = _fetch_latest_snapshot_json(db_path, pid)
            assert "ev_drift_test" in snap_v2["events"], (
                f"V3.1 P1-1 漂移自愈失败:v2 snapshot.events 应包含 'ev_drift_test',"
                f" 实际 keys={list(snap_v2['events'].keys())}"
            )
            ev_entry = snap_v2["events"]["ev_drift_test"]
            assert ev_entry["type"] == "encounter", (
                f"重建后 type 应来自 DB plot_events.type, 实际 {ev_entry['type']!r}"
            )
            assert isinstance(ev_entry["participants"], list)
            assert isinstance(ev_entry["time"], dict)
            # V3.1 P1-1.1：description 经 DB 权威重建后保留（早期"必为 None"断言已被推翻）
            assert ev_entry["description"] == drift_desc, (
                f"V3.1 P1-1.1 漂移自愈 description 保留失败:"
                f" 期望 {drift_desc!r}, 实际 {ev_entry['description']!r}"
            )

            # 同时断言 hk_rebuild_marker 也被新流程正确写入
            assert any(h.get("hook_id") == "hk_rebuild_marker" for h in snap_v2["hooks"])

            # 兜底断言:7 集合与 DB 全对齐(0 漂移)
            drifts = _drift_ids_for(db_path, pid, snap_v2)
            assert drifts == [], (
                f"漂移自愈后 7 集合仍存在差异(应为空): {drifts}"
            )

    asyncio.run(run())


# ----------------------------------------------------------------- 2. 正向一致:正常 commit 后 7 集合与 DB 对齐


def test_snapshot_consistent_after_commit(tmp_path: Path):
    """正常 commit_delta 后,新快照的 7 个集合与 DB 表 id 完全一致。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="consistent_after_commit")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            # init genesis(v1)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            # 一次 commit 带多类变化:location / hook / debt / event
            delta_id = "dlt_consistency_check"
            delta = {
                **_make_meta(delta_id, chap, 1),
                "character_changes": [],
                "world_changes": [
                    {
                        "change_id": "wc_loc_cons",
                        "op": "add",
                        "target_id": "loc_cons",
                        "world_id": "loc_cons",
                        "world_kind": "location",
                        "field": "name",
                        "after": {"name": "一致测试点", "statement": "用于一致性的地点", "data_json": {}},
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "relationship_changes": [],
                "new_events": [
                    {
                        "change_id": "ev_cons_1",
                        "op": "add",
                        "target_id": "evt_cons",
                        "event_id": "evt_cons",
                        "type": "encounter",
                        "cause": [],
                        "effects": [],
                        "participants": ["char_cons_actor"],
                        "location": "loc_cons",
                        "time": {"timeline_day": 1, "in_story_date": None},
                        # V3.1 P1-1.1：observer 给出的 description 应通过
                        # write_through 落库,重建后保留。
                        "description": "女主在一致测试点与 NPC 偶遇。",
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "resolved_hooks": [],
                "new_hooks": [
                    {
                        "change_id": "nh_cons_1",
                        "op": "add",
                        "target_id": "hk_cons",
                        "hook_id": "hk_cons",
                        "name": "一致性钩",
                        "importance": 0.5,
                        "description": "V3.1 P1-1 正向一致性测试用钩",
                        "expected_payoff_chapter_id": None,
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "debt_changes": [
                    {
                        "change_id": "dc_cons_1",
                        "op": "add",
                        "target_id": "dbt_cons",
                        "debt_id": "dbt_cons",
                        "description": "一致性测试伏债",
                        "status_after": "open",
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
            }

            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 201, r.text
            assert r.json()["status"] == "validated"

            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_id,
                    "author_approval": {"approver": "user:local:test", "approved": False},
                    "workflow_run_id": f"wfr_{delta_id}",
                },
            )
            assert r.status_code == 201, r.text

            snap = _fetch_latest_snapshot_json(db_path, pid)

            # 关键断言:7 集合双向 0 漂移(以 _drift_ids_for 为准)
            drifts = _drift_ids_for(db_path, pid, snap)
            assert drifts == [], f"正常 commit 后 7 集合存在差异: {drifts}"

            # 进一步抽样断言(可选,但更直白)
            assert "loc_cons" in snap["world"]["locations"]
            assert "hk_cons" in {h["hook_id"] for h in snap["hooks"]}
            assert "dbt_cons" in {d["debt_id"] for d in snap["debts"]}
            assert "evt_cons" in snap["events"]
            assert snap["events"]["evt_cons"]["type"] == "encounter"
            # V3.1 P1-1.1：observer 给出的 description 落库并经重建保留
            assert snap["events"]["evt_cons"]["description"] == "女主在一致测试点与 NPC 偶遇。"

    asyncio.run(run())


# ----------------------------------------------------------------- 3. 既有行为兼容


def test_commit_delta_unchanged_behavior(tmp_path: Path):
    """commit_delta 的对外契约未被破坏:返回字段、commits/story_states/state_deltas 行均符合预期。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="unchanged_behavior")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            # init genesis(v1)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text
            v1_snap = r.json()
            assert v1_snap["state_version"] == 1

            # 一次最小 LOW 风险 commit
            delta_id = "dlt_unchanged_behavior"
            delta = {
                **_make_meta(delta_id, chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [
                    {
                        "change_id": "nh_uc_1",
                        "op": "add",
                        "target_id": "hk_uc_marker",
                        "hook_id": "hk_uc_marker",
                        "name": "兼容测试钩",
                        "importance": 0.5,
                        "description": "V3.1 P1-1 兼容测试用钩",
                        "expected_payoff_chapter_id": None,
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 201, r.text
            assert r.json()["status"] == "validated"

            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_id,
                    "author_approval": {"approver": "user:local:test", "approved": False},
                    "workflow_run_id": f"wfr_{delta_id}",
                },
            )
            assert r.status_code == 201, r.text
            commit_resp = r.json()
            # commit 端点对外契约(Sprint 2/4 既有契约)
            assert set(commit_resp.keys()) >= {
                "commit_id",
                "delta_id",
                "state_version",
                "snapshot_ref",
            }, f"commit 响应字段不符既有契约: {commit_resp.keys()}"
            assert commit_resp["delta_id"] == delta_id
            assert commit_resp["state_version"] == 2
            assert commit_resp["snapshot_ref"] is not None
            assert commit_resp["snapshot_ref"].startswith(f"story_states/{pid}/state-v2-")

            # state_deltas.status = 'applied'
            conn = _open_db(db_path)
            try:
                row = conn.execute(
                    "SELECT status FROM state_deltas WHERE delta_id = ?", (delta_id,)
                ).fetchone()
                assert row is not None
                assert row["status"] == "applied"

                # commits 行
                commit_row = conn.execute(
                    "SELECT project_id, branch_id, chapter_id, previous_state_version,"
                    " resulting_state_version FROM commits WHERE commit_id = ?",
                    (commit_resp["commit_id"],),
                ).fetchone()
                assert commit_row is not None
                assert commit_row["project_id"] == pid
                assert commit_row["chapter_id"] == chap
                assert commit_row["previous_state_version"] == 1
                assert commit_row["resulting_state_version"] == 2

                # story_states v2 行存在
                v2_row = conn.execute(
                    "SELECT state_version, commit_id FROM story_states"
                    " WHERE project_id = ? AND state_version = 2",
                    (pid,),
                ).fetchone()
                assert v2_row is not None
                assert v2_row["commit_id"] == commit_resp["commit_id"]

                # hooks 表落行
                hook_row = conn.execute(
                    "SELECT hook_id FROM hooks WHERE hook_id = ?", ("hk_uc_marker",)
                ).fetchone()
                assert hook_row is not None
            finally:
                conn.close()

    asyncio.run(run())
