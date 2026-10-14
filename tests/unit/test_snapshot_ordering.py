"""V3.1 P1-1 回归测试（节 4）：recent_events 顺序稳定性（rebuild 路径）。

由 test_snapshot_rebuild.py 按既有分节拆出。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.unit.snapshot_rebuild_helpers import (
    _create_app,
    _evidence,
    _fetch_latest_snapshot_json,
    _make_chapter,
    _make_meta,
    _make_project,
    _request,
)

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


