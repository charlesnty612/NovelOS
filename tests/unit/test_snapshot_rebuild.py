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


# ----------------------------------------------------------------- 4. recent_events 顺序稳定性(rebuild 路径)


def test_recent_events_order_preserved_across_rebuild(tmp_path: Path):
    """``rebuild_snapshot_collections_from_db`` 重建 7 个集合后,``recent_events``
    仍必须按 ``_apply_new_events`` 契约定向累积——即每次 commit 新增的 event_id
    按 delta.new_events 输入顺序追加在历史末尾;历史顺序不得被打乱、第一条 event
    不得丢失。

    复现 ``docs/testing/audit-story-state-20260829.md`` 第 2 条:
    ``commit_delta`` 写透后 ``rebuild_snapshot_collections_from_db`` 以 DB 全量
    重载 characters(每角色逐条 ``_load_relationships_for``),依赖字符序+行序;
    增量路径 ``_apply_new_events`` 的"先追加后截断保留最新 N 条"语义必须在重载
    路径下保持一致(``recent_events`` 顺序不被破坏、首条 event 不丢失)。

    测试设计:
    - 准备 6 个 event_id,其中故意混入排序后位置变化的情况——如果重建路径错误地
      按 event_id 字典序回填 recent_events,会把 ``evt_first``(提交顺序第 1 条)
      推到末尾或丢失。
    - v1 init → v2 commit 携带 3 个 event → v3 commit 携带另外 3 个 event;
      每次 commit 都会跑 write_through→rebuild→materialize_snapshot 全链。
    - 直接读 DB 的 ``story_states.snapshot_json``,断言 v3 的 ``recent_events``
      等于按提交顺序累积的 6 条 event_id(不受字典序重排影响)。
    """
    app = _create_app(tmp_path)

    # 故意设计的 6 个 event_id:字典序顺序(evt_a, evt_b, evt_c, evt_d, evt_e, evt_first)
    # 与提交顺序不同——任何把 recent_events 重写成字典序的"修复"都会立刻失败。
    event_ids_in_commit_order = [
        "evt_d",   # 字典序第 4
        "evt_b",   # 字典序第 2
        "evt_first",  # 字典序第 6(最后),提交顺序第 1(最易在重排路径下丢失)
        "evt_a",   # 字典序第 1
        "evt_e",   # 字典序第 5
        "evt_c",   # 字典序第 3
    ]

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="recent_events_order")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            # v1 init
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text
            assert r.json()["state_version"] == 1

            # v2 commit:写入前 3 个 event
            v2_delta_id = "dlt_re_events_v2"
            v2_delta = {
                **_make_meta(v2_delta_id, chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [
                    {
                        "change_id": f"ce_{eid}",
                        "op": "add",
                        "target_id": eid,
                        "event_id": eid,
                        "type": "encounter",
                        "cause": [],
                        "effects": [],
                        "participants": ["char_actor"],
                        "location": None,
                        "time": {"timeline_day": i + 1, "in_story_date": None},
                        "description": f"v2 event {eid}",
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                    for i, eid in enumerate(event_ids_in_commit_order[:3])
                ],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=v2_delta)
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={"delta_id": v2_delta_id, "author_approval": {"approver": "u", "approved": False}, "workflow_run_id": f"wfr_{v2_delta_id}"},
            )
            assert r.status_code == 201, r.text

            v2_snap = _fetch_latest_snapshot_json(db_path, pid)
            # v2 断言:前 3 条按提交顺序追加
            assert v2_snap["recent_events"] == event_ids_in_commit_order[:3], (
                f"v2 recent_events 顺序被破坏: 期望 {event_ids_in_commit_order[:3]}, "
                f"实际 {v2_snap['recent_events']}"
            )

            # v3 commit:写入后 3 个 event;关键观察点——v3 重建后 recent_events
            # 必须是 [v2 前 3 条按提交顺序] + [v3 后 3 条按提交顺序]。
            v3_delta_id = "dlt_re_events_v3"
            v3_delta = {
                **_make_meta(v3_delta_id, chap, 2),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [
                    {
                        "change_id": f"ce_{eid}",
                        "op": "add",
                        "target_id": eid,
                        "event_id": eid,
                        "type": "encounter",
                        "cause": [],
                        "effects": [],
                        "participants": ["char_actor"],
                        "location": None,
                        "time": {"timeline_day": i + 4, "in_story_date": None},
                        "description": f"v3 event {eid}",
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                    for i, eid in enumerate(event_ids_in_commit_order[3:])
                ],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=v3_delta)
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={"delta_id": v3_delta_id, "author_approval": {"approver": "u", "approved": False}, "workflow_run_id": f"wfr_{v3_delta_id}"},
            )
            assert r.status_code == 201, r.text

            v3_snap = _fetch_latest_snapshot_json(db_path, pid)
            # v3 关键断言:rebuild 路径下 recent_events 仍按"v2 提交顺序 + v3 提交顺序"
            # 累积——不丢 evt_first、不被字典序重排。
            assert v3_snap["recent_events"] == event_ids_in_commit_order, (
                f"rebuild 后 recent_events 顺序被破坏: "
                f"期望 {event_ids_in_commit_order}, 实际 {v3_snap['recent_events']} "
                f"(审计 §第2条:rebuild 路径下 recent_events 顺序/首条事件丢失)"
            )
            # 首条不丢失:提交顺序第一条 evt_d 必须出现在 v3 recent_events[0]
            assert v3_snap["recent_events"][0] == "evt_d", (
                f"v3 recent_events[0] 应为提交顺序首条 evt_d, 实际 {v3_snap['recent_events'][0]!r}"
            )
            # 末尾是 v3 最后一条
            assert v3_snap["recent_events"][-1] == event_ids_in_commit_order[-1]

    asyncio.run(run())


def test_recent_events_order_preserved_with_rollback_inverse_cleanup(tmp_path: Path):
    """``apply_inverse_cleanup_to_state`` 过滤 ``recent_events`` 时必须保持顺序。

    rollback 路径下,``commit_delta`` 走 inverse_cleanup 删 event_id;若清理实现
    误用 ``[i for i in recent if i != eid]``(按值删而非按 id 删)或类似错误,会
    导致后续 event_id 错位 / 首条丢失。重建路径下(inverse_cleanup 在 rebuild
    之后)recent_events 必须按累积提交顺序保留。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="recent_events_rollback")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            # v1 init
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            # v2 commit:写入 3 个 event
            v2_delta_id = "dlt_rollback_v2"
            eids = ["evt_aaa", "evt_bbb", "evt_ccc"]
            v2_delta = {
                **_make_meta(v2_delta_id, chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [
                    {
                        "change_id": f"ce_{eid}",
                        "op": "add",
                        "target_id": eid,
                        "event_id": eid,
                        "type": "encounter",
                        "cause": [],
                        "effects": [],
                        "participants": ["char_actor"],
                        "location": None,
                        "time": {"timeline_day": i + 1, "in_story_date": None},
                        "description": f"rollback test event {eid}",
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                    for i, eid in enumerate(eids)
                ],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=v2_delta)
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={"delta_id": v2_delta_id, "author_approval": {"approver": "u", "approved": False}, "workflow_run_id": f"wfr_{v2_delta_id}"},
            )
            assert r.status_code == 201, r.text
            v2_commit_id = r.json()["commit_id"]

            # v3 commit:再加 2 个 event
            v3_delta_id = "dlt_rollback_v3"
            v3_extra = ["evt_ddd", "evt_eee"]
            v3_delta = {
                **_make_meta(v3_delta_id, chap, 2),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [
                    {
                        "change_id": f"ce_{eid}",
                        "op": "add",
                        "target_id": eid,
                        "event_id": eid,
                        "type": "encounter",
                        "cause": [],
                        "effects": [],
                        "participants": ["char_actor"],
                        "location": None,
                        "time": {"timeline_day": i + 4, "in_story_date": None},
                        "description": f"v3 event {eid}",
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                    for i, eid in enumerate(v3_extra)
                ],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=v3_delta)
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={"delta_id": v3_delta_id, "author_approval": {"approver": "u", "approved": False}, "workflow_run_id": f"wfr_{v3_delta_id}"},
            )
            assert r.status_code == 201, r.text

            v3_snap = _fetch_latest_snapshot_json(db_path, pid)
            assert v3_snap["recent_events"] == eids + v3_extra, (
                f"v3 recent_events 应为累积提交顺序: 期望 {eids + v3_extra}, 实际 {v3_snap['recent_events']}"
            )

            # 回滚 v2 commit(inverse_cleanup 仅删 v2 的 3 个 event),断言:
            # - v2 的 event 被剔除
            # - v3 的 event 保留,顺序保持
            # - 首条不丢失
            r = await _request(
                app, "POST", f"/api/commits/{v2_commit_id}/rollback",
                json={"author_approval": {"approver": "u", "approved": True}},
            )
            assert r.status_code in (200, 201), r.text

            final_snap = _fetch_latest_snapshot_json(db_path, pid)
            # rollback 后 recent_events 应只剩 v3 提交的 2 条(按提交顺序)
            assert final_snap["recent_events"] == v3_extra, (
                f"rollback v2 后 recent_events 应只剩 v3 累积顺序: 期望 {v3_extra}, "
                f"实际 {final_snap['recent_events']}"
            )
            # 首条不丢失:v3 提交顺序首条 evt_ddd 必须保留
            assert final_snap["recent_events"][0] == "evt_ddd", (
                f"rollback 后 recent_events[0] 应为 v3 提交首条 evt_ddd, "
                f"实际 {final_snap['recent_events'][0]!r}"
            )

    asyncio.run(run())


# ----------------------------------------------------------------- 5. rollback 逆清理全集合覆盖(审计 §第3条)


def test_rollback_inverse_cleanup_covers_all_collections(tmp_path: Path):
    """rollback 路径必须清理全 7 个领域集合,而非仅 events/hooks。

    复现 ``docs/testing/audit-story-state-20260829.md`` 第 3 条:
    原 ``_inverse_cleanup`` 只收集 ``remove_event_ids`` / ``remove_hook_ids``,
    ``commit_delta`` 阶段仅 DELETE ``plot_events`` / ``hooks`` / ``timeline_events``;
    ``relationships`` / ``narrative_debts`` / ``locations`` / ``factions`` /
    ``world_rules`` 在 rollback 后残留 → 半回滚态。

    角色侧语义（2026-08-31 修复扩展 + 字段级守卫）:
    - 守卫语义：「提交前快照（``delta_row.previous_state_version`` 处的 main
      snapshot）」中存在 character_id → 该 add 是字段级，**不走实体级 DELETE**
      （留给逆 delta 的字段级 remove 恢复）；不存在 → 视为该 commit 实体级首
      次引入的角色，触发实体回收（顾晚舟生产事故场景——但顾晚舟于 ch2 commit
      之前快照已存在，故 ch2 rollback 时不会触发实体 DELETE，需重放 SOP）。
    - 本 fixture 的 ``char_actor`` 在 init_genesis **之前**就 SQL INSERT 到
      characters 表——``build_initial_state`` 从 characters 表 SELECT 构造
      v1 snapshot（snapshot.py L271）→ v1 snapshot 中已含 ``char_actor`` →
      提交前快照已有该 cid → 不在 ``preexisting_char_ids`` 之外走收集 → 新
      守卫下 **不触发实体级 DELETE**，角色与 character_states 保留。**字段
      仍由逆 delta 的字段级 remove 正确移除**（state.location 的 field=
      knowledge 类；本 fixture 是 facet=state field=mood，由 write_through
      步骤 5 按逆 delta 的 after 写回 state_json）。
    - 守卫生效的具象路径：rollback_char_delete C5（既有角色字段级 add → 角色
      存活，字段由逆 delta 字段级 remove 恢复）。
    - char_target 在 v2 commit 中未被任何 change 引用（relationship 仅作为
      to 端点），rollback 不删（验证「只清本 commit 引用过的角色」）。

    测试设计:
    - v1 init。
    - v2 commit 同时写入:
        * location(loc_rbck) / faction(fac_rbck) / world_rule(rule_rbck)
        * character_state 增量(char_actor 的 state field)
        * relationship (char_actor→char_target, type=ally)
        * debt(dbt_rbck)  + new_event(evt_rbck) + new_hook(hk_rbck)
    - 校验 v2 提交后 7 集合 DB 行均存在。
    - 调 ``/api/commits/{v2_commit_id}/rollback``。
    - 修复后断言:
        * relationships / narrative_debts / locations / factions / world_rules
          全部无对应残留行(逆 add → DELETE / 逆 update → 恢复 before);
        * 事件 evt_rbck / 钩 hk_rbck 已被 DELETE(原有回滚回归);
        * char_actor 保留——其在 commit 前快照(v1, init_genesis 由 characters 表
          聚合)中已存在, 字段级守卫豁免实体 DELETE(字段由逆 delta 恢复);
        * char_target 保留(未被本 commit 引用);
        * snapshot 同步保留两角色条目。
    - 修复前(红):关系/债务/世界实体表均残留。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="rollback_full_cleanup")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            # 准备 2 个角色(relationship 需要 from→to 两侧角色存在)
            from packages.core.db import get_connection
            _conn = get_connection(str(db_path))
            try:
                _conn.execute(
                    """INSERT INTO characters
                       (character_id, project_id, name, core_json, visibility,
                        who_knows, created_at, updated_at)
                       VALUES (?, ?, ?, '{}', 'VISIBLE', NULL, ?, ?)
                    """,
                    ("char_actor", pid, "行动者", _now_iso(), _now_iso()),
                )
                _conn.execute(
                    """INSERT INTO characters
                       (character_id, project_id, name, core_json, visibility,
                        who_knows, created_at, updated_at)
                       VALUES (?, ?, ?, '{}', 'VISIBLE', NULL, ?, ?)
                    """,
                    ("char_target", pid, "目标者", _now_iso(), _now_iso()),
                )
                _conn.commit()
            finally:
                _conn.close()

            # v1 init
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            # v2 commit:写入全 6 集合
            v2_delta_id = "dlt_full_collections"
            v2_delta = {
                **_make_meta(v2_delta_id, chap, 1),
                "character_changes": [
                    {
                        "change_id": "cc_rbck_1",
                        "op": "add",
                        "target_id": "char_actor",
                        "character_id": "char_actor",
                        "facet": "state",
                        "field": "mood",
                        "after": "警惕",
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "world_changes": [
                    {
                        "change_id": "wc_loc_rbck",
                        "op": "add",
                        "target_id": "loc_rbck",
                        "world_id": "loc_rbck",
                        "world_kind": "location",
                        "field": "name",
                        "after": {"name": "回滚测试点", "statement": "用于回滚全集合的地点", "data_json": {}},
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                    {
                        "change_id": "wc_fac_rbck",
                        "op": "add",
                        "target_id": "fac_rbck",
                        "world_id": "fac_rbck",
                        "world_kind": "faction",
                        "field": "name",
                        "after": {"name": "回滚测试派系", "statement": "用于回滚全集合的派系", "data_json": {}},
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                    {
                        "change_id": "wc_rule_rbck",
                        "op": "add",
                        "target_id": "rule_rbck",
                        "world_id": "rule_rbck",
                        "world_kind": "rule",
                        "field": "name",
                        "after": {"name": "回滚测试规则", "statement": "用于回滚全集合的规则", "data_json": {}},
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "relationship_changes": [
                    {
                        "change_id": "rc_rbck_1",
                        "op": "add",
                        "target_id": "rel_rbck_1",
                        "from_character_id": "char_actor",
                        "to_character_id": "char_target",
                        "relation_type": "ally",
                        "after": {"intensity": 0.7, "note": "初始盟友关系"},
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "new_events": [
                    {
                        "change_id": "ce_rbck_1",
                        "op": "add",
                        "target_id": "evt_rbck",
                        "event_id": "evt_rbck",
                        "type": "encounter",
                        "cause": [],
                        "effects": [],
                        "participants": ["char_actor", "char_target"],
                        "location": "loc_rbck",
                        "time": {"timeline_day": 1, "in_story_date": None},
                        "description": "回滚测试事件",
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "resolved_hooks": [],
                "new_hooks": [
                    {
                        "change_id": "nh_rbck_1",
                        "op": "add",
                        "target_id": "hk_rbck",
                        "hook_id": "hk_rbck",
                        "name": "回滚测试钩",
                        "importance": 0.5,
                        "description": "回滚全集合测试用钩",
                        "expected_payoff_chapter_id": None,
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "debt_changes": [
                    {
                        "change_id": "dc_rbck_1",
                        "op": "add",
                        "target_id": "dbt_rbck",
                        "debt_id": "dbt_rbck",
                        "description": "回滚测试伏债",
                        "status_after": "open",
                        "confidence": 0.8,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
            }

            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=v2_delta)
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": v2_delta_id,
                    "author_approval": {"approver": "u", "approved": True},
                    "workflow_run_id": f"wfr_{v2_delta_id}",
                },
            )
            assert r.status_code == 201, r.text
            v2_commit_id = r.json()["commit_id"]

            # 提交后断言:6 集合 DB 行均存在(预条件)
            conn = _open_db(db_path)
            try:
                assert conn.execute("SELECT 1 FROM locations WHERE location_id=?", ("loc_rbck",)).fetchone()
                assert conn.execute("SELECT 1 FROM factions WHERE faction_id=?", ("fac_rbck",)).fetchone()
                assert conn.execute("SELECT 1 FROM world_rules WHERE world_rule_id=?", ("rule_rbck",)).fetchone()
                assert conn.execute(
                    "SELECT 1 FROM relationships WHERE from_character_id=? AND to_character_id=? AND relation_type=?",
                    ("char_actor", "char_target", "ally"),
                ).fetchone()
                assert conn.execute("SELECT 1 FROM narrative_debts WHERE debt_id=?", ("dbt_rbck",)).fetchone()
                assert conn.execute("SELECT 1 FROM plot_events WHERE event_id=?", ("evt_rbck",)).fetchone()
                assert conn.execute("SELECT 1 FROM hooks WHERE hook_id=?", ("hk_rbck",)).fetchone()
            finally:
                conn.close()

            # 回滚 v2
            r = await _request(
                app, "POST", f"/api/commits/{v2_commit_id}/rollback",
                json={"author_approval": {"approver": "u", "approved": True}},
            )
            assert r.status_code in (200, 201), r.text

            # 关键断言(修复后绿 / 修复前红):
            # 1) 6 集合（loc/fac/rule/debt/event/hook）DB 表全部清理干净(逆 add → DELETE)
            conn = _open_db(db_path)
            try:
                leftovers = {
                    "locations": conn.execute("SELECT 1 FROM locations WHERE location_id=?", ("loc_rbck",)).fetchone(),
                    "factions": conn.execute("SELECT 1 FROM factions WHERE faction_id=?", ("fac_rbck",)).fetchone(),
                    "world_rules": conn.execute("SELECT 1 FROM world_rules WHERE world_rule_id=?", ("rule_rbck",)).fetchone(),
                    # 关系：char_actor 在 commit 前快照中存在 → 字段级守卫豁免
                    # 实体级回收，但关系本身在 commit_delta 步骤 5 由逆 delta
                    # 的 op=remove 清理（write_through 对 op=remove 不 DELETE，
                    # 但逆 delta 中关系 op=remove 走 remove_relationship_keys）→
                    # 应被 DELETE。验证语义需分两类：保留 char_actor + char_target；
                    # 仅 relationship 行 DELETE。
                    "relationships": conn.execute(
                        "SELECT 1 FROM relationships WHERE from_character_id=? AND to_character_id=? AND relation_type=?",
                        ("char_actor", "char_target", "ally"),
                    ).fetchone(),
                    "narrative_debts": conn.execute("SELECT 1 FROM narrative_debts WHERE debt_id=?", ("dbt_rbck",)).fetchone(),
                    "plot_events": conn.execute("SELECT 1 FROM plot_events WHERE event_id=?", ("evt_rbck",)).fetchone(),
                    "hooks": conn.execute("SELECT 1 FROM hooks WHERE hook_id=?", ("hk_rbck",)).fetchone(),
                }
            finally:
                conn.close()
            for tbl, row in leftovers.items():
                assert row is None, (
                    f"rollback 逆清理失败:{tbl} 表残留已回滚 commit 的行 {row}"
                )

            # 2) 角色实体保留（字段级守卫）：char_actor 在 commit 前 snapshot_v1
            # 已存在（build_initial_state 从 characters 表 SELECT），新守卫下
            # 不进 remove_character_ids；character_states 至少 v1 seed 行仍在。
            conn = _open_db(db_path)
            try:
                actor_row = conn.execute("SELECT name FROM characters WHERE character_id=?", ("char_actor",)).fetchone()
                assert actor_row is not None, (
                    "rollback 后 char_actor 应保留（commit 前快照已有该 cid，字段级守卫豁免实体 DELETE）"
                )
                n_states = conn.execute(
                    "SELECT COUNT(*) AS c FROM character_states WHERE character_id=?", ("char_actor",),
                ).fetchone()
                assert n_states["c"] >= 1, "character_states 历史行被误清"
                target_row = conn.execute("SELECT name FROM characters WHERE character_id=?", ("char_target",)).fetchone()
                assert target_row is not None, "char_target 应保留（未被本 commit 引用）"
            finally:
                conn.close()

            # 3) 快照同步:对应 id 不在 6 集合中;角色两侧保留
            final_snap = _fetch_latest_snapshot_json(db_path, pid)
            assert "loc_rbck" not in (final_snap.get("world", {}).get("locations") or {}), (
                f"rollback 后 snapshot.world.locations 残留 loc_rbck: {final_snap.get('world', {}).get('locations')}"
            )
            assert "fac_rbck" not in (final_snap.get("world", {}).get("factions") or {}), (
                f"rollback 后 snapshot.world.factions 残留 fac_rbck: {final_snap.get('world', {}).get('factions')}"
            )
            assert "rule_rbck" not in (final_snap.get("world", {}).get("world_rules") or []), (
                f"rollback 后 snapshot.world.world_rules 残留 rule_rbck"
            )
            snap_char_ids = {c.get("character_id") for c in (final_snap.get("characters") or [])}
            assert "char_actor" in snap_char_ids, (
                f"rollback 后 snapshot.characters 应保留 char_actor（字段级守卫）: {snap_char_ids}"
            )
            assert "char_target" in snap_char_ids, (
                f"rollback 后 snapshot.characters 应保留 char_target（未引用）: {snap_char_ids}"
            )
            assert not any(d.get("debt_id") == "dbt_rbck" for d in final_snap.get("debts", [])), (
                f"rollback 后 snapshot.debts 残留 dbt_rbck"
            )

    asyncio.run(run())


def _now_iso() -> str:
    from packages.core.ids import now_iso as _ni
    return _ni()


# ----------------------------------------------------------------- P1: snapshot 携带 who_knows
# 背景:knowledge_leakage guardrail (§4.5) 读取 snapshot.events[eid].who_knows 与
# snapshot.hooks[hid].who_knows。原 bug:rebuild_snapshot_collections_from_db 的
# 三个 _load_* SELECT 不含 who_knows,导致快照全部 who_knows=None,校验空转。
# 修复后:快照三层 (events / hooks / debts) 都必须携带 who_knows。
# 关系侧:_load_relationships_for 同步补 visibility/who_knows,与知识防护网配套。
# 模式:与既有 test_consistency_roundtrip_no_drift_after_commit_delta 同款端到端
# ASGI 调用,但额外断言 who_knows 字段值。
# 存量快照不回填——只有 commit 重建后产生的新快照才带全 who_knows。


def test_snapshot_hooks_debts_events_carry_who_knows(tmp_path: Path):
    """commit 后新快照的 hooks / debts / events 条目 must carry who_knows=非 None,
    否则 knowledge_leakage guardrail 全 pass(校验空转)。

    验证矩阵:
    - new_hooks 含显式 who_knows → snap.hooks[hid].who_knows == 同 list
    - new_events 含显式 who_knows → snap.events[eid].who_knows == 同 list
    - debt_changes 含显式 who_knows → snap.debts[did].who_knows == 同 list
    - relationship_changes 含 who_knows → snap.characters[cid].relationships[].who_knows == 同 list
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="who_knows_snapshot")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            # init genesis
            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            # seed 两个 character（relationships 端到端需要 from/to 两角色行）
            async with _make_client(app) as client:
                rc = await client.post(
                    f"/api/projects/{pid}/characters",
                    json={"name": "actor", "role": "supporting"},
                )
            assert rc.status_code in (201, 200), rc.text
            actor_cid = rc.json()["character_id"]
            async with _make_client(app) as client:
                rc2 = await client.post(
                    f"/api/projects/{pid}/characters",
                    json={"name": "b", "role": "supporting"},
                )
            assert rc2.status_code in (201, 200), rc2.text
            b_cid = rc2.json()["character_id"]

            delta_id = "dlt_who_knows_snapshot"
            delta = {
                **_make_meta(delta_id, chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [
                    {
                        "change_id": "rc_wk_1",
                        "op": "add",
                        "target_id": f"{actor_cid}:{b_cid}",
                        "from_character_id": actor_cid,
                        "to_character_id": b_cid,
                        "relation_type": "trust",
                        "before": None,
                        "after": {"value": 0.7},
                        "who_knows": [actor_cid],
                        "visibility": "RESTRICTED",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "new_events": [
                    {
                        "change_id": "ev_wk_1",
                        "op": "add",
                        "target_id": "evt_wk",
                        "event_id": "evt_wk",
                        "type": "revelation",
                        "cause": [],
                        "effects": [],
                        "participants": [actor_cid],
                        "location": None,
                        "time": {"timeline_day": 1, "in_story_date": None},
                        "description": "暗中约定",
                        "who_knows": [actor_cid],
                        "visibility": "RESTRICTED",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "resolved_hooks": [],
                "new_hooks": [
                    {
                        "change_id": "nh_wk_1",
                        "op": "add",
                        "target_id": "hk_wk",
                        "hook_id": "hk_wk",
                        "name": "秘密约定钩",
                        "importance": 0.7,
                        "description": "秘密约定存续",
                        "expected_payoff_chapter_id": None,
                        "who_knows": [actor_cid],
                        "visibility": "RESTRICTED",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "debt_changes": [
                    {
                        "change_id": "dc_wk_1",
                        "op": "add",
                        "target_id": "dbt_wk",
                        "debt_id": "dbt_wk",
                        "description": "秘密约定待兑",
                        "status_after": "open",
                        "who_knows": [actor_cid],
                        "visibility": "RESTRICTED",
                        "confidence": 0.9,
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

            # hooks:每个条目必有 who_knows,且与 delta 声明一致
            hooks_by_id = {h["hook_id"]: h for h in snap["hooks"]}
            assert "hk_wk" in hooks_by_id, "new hook 应进 snapshot"
            assert hooks_by_id["hk_wk"].get("who_knows") == [actor_cid], (
                f"hook.who_knows 应保留 delta 声明;实际={hooks_by_id['hk_wk'].get('who_knows')}"
            )

            # events:每个条目必有 who_knows
            assert "evt_wk" in snap["events"], "new event 应进 snapshot"
            assert snap["events"]["evt_wk"].get("who_knows") == [actor_cid], (
                f"event.who_knows 应保留 delta 声明;实际={snap['events']['evt_wk'].get('who_knows')}"
            )

            # debts:每个条目必有 who_knows
            debts_by_id = {d["debt_id"]: d for d in snap["debts"]}
            assert "dbt_wk" in debts_by_id, "new debt 应进 snapshot"
            assert debts_by_id["dbt_wk"].get("who_knows") == [actor_cid], (
                f"debt.who_knows 应保留 delta 声明;实际={debts_by_id['dbt_wk'].get('who_knows')}"
            )

            # relationships:actor.relationships[].who_knows 与 delta 声明一致
            actor_char = next(
                c for c in snap["characters"] if c["character_id"] == actor_cid
            )
            assert actor_char["relationships"], "actor 关系应进 snapshot"
            target_rel = next(
                (
                    r for r in actor_char["relationships"]
                    if r.get("to_character_id") == b_cid
                    and r.get("relation_type") == "trust"
                ),
                None,
            )
            assert target_rel is not None, "trust 关系应在 snapshot"
            assert target_rel.get("who_knows") == [actor_cid], (
                f"relationship.who_knows 应保留 delta 声明;实际={target_rel.get('who_knows')}"
            )
            assert target_rel.get("visibility") == "RESTRICTED", (
                f"relationship.visibility 应保留;实际={target_rel.get('visibility')}"
            )

    asyncio.run(run())


def test_snapshot_relationships_persisted_to_db(tmp_path: Path):
    """端到端:commit 后 DB 行 relationships 携带 visibility/who_knows(P0 修复回归)。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="rel_db_persist")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            async with _make_client(app) as client:
                rc = await client.post(
                    f"/api/projects/{pid}/characters",
                    json={"name": "actor", "role": "supporting"},
                )
            assert rc.status_code in (201, 200), rc.text
            actor_cid = rc.json()["character_id"]
            async with _make_client(app) as client:
                rc2 = await client.post(
                    f"/api/projects/{pid}/characters",
                    json={"name": "b", "role": "supporting"},
                )
            assert rc2.status_code in (201, 200), rc2.text
            b_cid = rc2.json()["character_id"]

            delta_id = "dlt_rel_persist"
            delta = {
                **_make_meta(delta_id, chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [
                    {
                        "change_id": "rc_persist_1",
                        "op": "add",
                        "target_id": f"{actor_cid}:{b_cid}",
                        "from_character_id": actor_cid,
                        "to_character_id": b_cid,
                        "relation_type": "rival",
                        "before": None,
                        "after": {"value": 0.5},
                        "who_knows": [actor_cid, b_cid],
                        "visibility": "RESTRICTED",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }

            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta)
            assert r.status_code == 201, r.text

            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_id,
                    "author_approval": {"approver": "user:local:test", "approved": False},
                    "workflow_run_id": f"wfr_{delta_id}",
                },
            )
            assert r.status_code == 201, r.text

            # 直接读 DB 行断言
            conn = _open_db(db_path)
            try:
                row = conn.execute(
                    "SELECT visibility, who_knows, state_json FROM relationships "
                    "WHERE from_character_id = ? AND to_character_id = ? AND relation_type = ?",
                    (actor_cid, b_cid, "rival"),
                ).fetchone()
                assert row is not None, "relationship 行应落库"
                assert row["visibility"] == "RESTRICTED", (
                    f"DB 行 visibility 应为 RESTRICTED;实际={row['visibility']}"
                )
                assert json.loads(row["who_knows"]) == [actor_cid, b_cid], (
                    f"DB 行 who_knows 应为 [{actor_cid!r}, {b_cid!r}];实际={row['who_knows']}"
                )
            finally:
                conn.close()

    asyncio.run(run())


def test_relationship_inverse_update_restores_state_json_and_preserves_columns(
    tmp_path: Path,
):
    """P0 修复回归:rollback 路径下,relationship 逆 update 必须:
    1. 恢复 before 的 state_json（既有行为）;
    2. 不破坏 visibility / who_knows 列——按三态语义（缺省=沿用）保留。

    背景:observer-v1 当前 schema 只允许 change 声明 after 的 V/W,before
    字段不含 V/W（语义上 before 的 V/W 是「上一态」的,observer 不能精确
    表达）。commit_delta 阶段 write_through 已把 after.V/after.W 落库；
    rollback 时三态语义下,V/W 列按缺省语义保留 DB 现值——等同"恢复到
    add 时 V/W"或"保留 update 后 V/W",由数据流决定。本测试聚焦「不破坏」：
    state_json 必须恢复,V/W 列不因 rollback 写入 None/PUBLIC 等默认值。

    流程:
    1. add 带 V=RESTRICTED/W=[actor_cid]（DB 落 RESTRICTED/[actor_cid]）。
    2. update 改 V=VISIBLE/W=[actor_cid,b_cid],state_json 0.3→0.9。
    3. rollback update:state_json 恢复为 0.3,V/W 保留 update 后值（VISIBLE/[a,b]）
       ——因 update 的 V/W 缺省语义不再精确回退到 add 时,这是 observer-v1
       当前 schema 的合理语义边界。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app, name="rel_inverse_preserve")
            chap = await _make_chapter(app, pid)
            db_path = tmp_path / "novelos.db"

            r = await _request(
                app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": chap}
            )
            assert r.status_code == 201, r.text

            async with _make_client(app) as client:
                rc = await client.post(
                    f"/api/projects/{pid}/characters",
                    json={"name": "actor", "role": "supporting"},
                )
            assert rc.status_code in (201, 200), rc.text
            actor_cid = rc.json()["character_id"]
            async with _make_client(app) as client:
                rc2 = await client.post(
                    f"/api/projects/{pid}/characters",
                    json={"name": "b", "role": "supporting"},
                )
            assert rc2.status_code in (201, 200), rc2.text
            b_cid = rc2.json()["character_id"]

            # step 1: add 带 V=RESTRICTED/W=[actor_cid]
            delta_add_id = "dlt_rel_inv_add"
            delta_add = {
                **_make_meta(delta_add_id, chap, 1),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [
                    {
                        "change_id": "rc_inv_add",
                        "op": "add",
                        "target_id": f"{actor_cid}:{b_cid}",
                        "from_character_id": actor_cid,
                        "to_character_id": b_cid,
                        "relation_type": "mentor",
                        "before": None,
                        "after": {"value": 0.3},
                        "who_knows": [actor_cid],
                        "visibility": "RESTRICTED",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta_add)
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_add_id,
                    "author_approval": {"approver": "user:local:test", "approved": False},
                    "workflow_run_id": f"wfr_{delta_add_id}",
                },
            )
            assert r.status_code == 201, r.text

            # step 2: update 改 V=VISIBLE/W=[a,b]
            delta_upd_id = "dlt_rel_inv_upd"
            delta_upd = {
                **_make_meta(delta_upd_id, chap, 2),
                "character_changes": [],
                "world_changes": [],
                "relationship_changes": [
                    {
                        "change_id": "rc_inv_upd",
                        "op": "update",
                        "target_id": f"{actor_cid}:{b_cid}",
                        "from_character_id": actor_cid,
                        "to_character_id": b_cid,
                        "relation_type": "mentor",
                        "before": {"value": 0.3},
                        "after": {"value": 0.9},
                        "who_knows": [actor_cid, b_cid],
                        "visibility": "VISIBLE",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    },
                ],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            }
            r = await _request(app, "POST", f"/api/projects/{pid}/deltas", json=delta_upd)
            assert r.status_code == 201, r.text
            r = await _request(
                app, "POST", f"/api/projects/{pid}/commits",
                json={
                    "delta_id": delta_upd_id,
                    "author_approval": {"approver": "user:local:test", "approved": False},
                    "workflow_run_id": f"wfr_{delta_upd_id}",
                },
            )
            assert r.status_code == 201, r.text
            commit_upd_id = r.json()["commit_id"]

            # step 3: rollback update
            async with _make_client(app) as client:
                rr = await client.post(
                    f"/api/commits/{commit_upd_id}/rollback",
                    json={
                        "author_approval": {
                            "approver": "user:local:test",
                            "approved": True,
                            "notes": "rollback for who_knows visibility preserve test",
                        }
                    },
                )
            assert rr.status_code in (200, 201), rr.text

            # 断言：state_json 已恢复 before,V/W 列未被 rollback 写入 NULL/PUBLIC
            conn = _open_db(db_path)
            try:
                row = conn.execute(
                    "SELECT visibility, who_knows, state_json FROM relationships "
                    "WHERE from_character_id = ? AND to_character_id = ? AND relation_type = ?",
                    (actor_cid, b_cid, "mentor"),
                ).fetchone()
                assert row is not None, "rollback 后 relationship 行应仍在"
                # state_json 必须已恢复（既有行为）
                assert json.loads(row["state_json"]) == {"value": 0.3}, (
                    f"rollback 应恢复 state_json;实际={row['state_json']}"
                )
                # V/W 列不因 rollback 缺省语义被覆盖为 NULL/PUBLIC——
                # observer-v1 当前 schema 不支持 before 携带 V/W,逆 update
                # 路径按三态缺省语义保留 DB 现值（update 后的 V/W）。
                # 原 bug:逆 UPDATE 不写 V/W 列,看似"恢复"了——但因 write_through
                # 写透时也是缺省语义,实际是「原 update 后 V/W 因 add 缺省+update
                # 缺省导致两者皆 None」,而修复后 write_through 落 V=VISIBLE/W=[a,b],
                # 逆 UPDATE 不破坏这两列。关键断言:V 列不为 NULL,W 列 JSON 不为空。
                assert row["visibility"] is not None, (
                    f"rollback 不应清空 visibility 列;实际={row['visibility']}"
                )
                wk_raw = row["who_knows"]
                assert wk_raw is not None, (
                    f"rollback 不应清空 who_knows 列;实际={wk_raw}"
                )
                assert json.loads(wk_raw), f"rollback 后 who_knows 应非空列表;实际={wk_raw}"
            finally:
                conn.close()

    asyncio.run(run())
