"""Backup 单元测试（节 3）：JSON 内嵌 id 重映射 / volumes 白名单 /
F3 提交前 FK 校验 / F4 projects 后加列动态写。

由 test_backup.py 按既有分节拆出。守门用例 test_json_id_columns_cover_schema
因被 packages/core/backup/README.md 按「文件::用例名」引用，保留在门面 test_backup.py。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.backup import BackupService
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from tests.unit.backup_fixtures import (
    _insert_row,
    _seed_minimal_project,
    make_db_path,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    """转发到 backup_fixtures.make_db_path（pytest 按模块命名空间发现 fixture）。"""
    return make_db_path(tmp_path)


# ---------------------------------------------------------------------------
# V3.9 全量检修：F1 JSON 内嵌 id 重映射 / F2 volumes 入白名单 /
#                  F3 提交前 FK 校验 / F4 projects 后加列动态写
# ---------------------------------------------------------------------------


def _seed_cross_reference_project(db_path: str) -> dict[str, str]:
    """构造跨表引用 fixture（F1/F2/F4 共用）。

    覆盖链路：项目（含 word_band_json / genre_pack_id）→ 角色 ↔ 关系 ↔ 事件 ↔
    钩子 ↔ 卷 → 章节（挂卷）→ 场景 / 草稿 → 两版本快照（story_states v1/v2）+ 两条
    delta，且实体 id 同时出现在 JSON 列的**值**与**键**位置。

    返回 dict：``pid`` 与各实体旧 id（键名 = 实体角色）。
    """
    now = now_iso()
    ids = {k: new_id(prefix) for k, prefix in [
        ("c1", "char"), ("c2", "char"), ("loc1", "loc"), ("wr1", "wrule"),
        ("debt1", "debt"), ("hook1", "hook"), ("ch1", "ch"), ("ch2", "ch"),
        ("vol1", "vol"), ("branch1", "br"), ("ev1", "event"), ("ev2", "event"),
        ("tle1", "tle"), ("rel1", "rel"), ("d1", "dlt"), ("d2", "dlt"),
        ("cmt1", "cmt"), ("cmt2", "cmt"), ("sc1", "sc"), ("dr1", "dr"),
        ("mem1", "mem"), ("qr1", "qr"),
    ]}
    pid = new_id("prj")
    ids["pid"] = pid
    pack_id = "gp_cross_ref"

    conn = get_connection(db_path)
    try:
        _insert_row(
            conn,
            """
            INSERT INTO genre_packs
                (pack_id, name, genre_tag, version, payload_json, source_path,
                 created_at, updated_at)
            VALUES
                (:pack_id, '跨引用题材包', '测试', 1, '{"schema_version":"x"}',
                 NULL, :created_at, :updated_at)
            """,
            {"pack_id": pack_id, "created_at": now, "updated_at": now},
        )
        _insert_row(
            conn,
            """
            INSERT INTO projects
                (project_id, name, premise, genre, target_words, status,
                 foreshadow_overdue_chapters, word_band_json, genre_pack_id,
                 created_at, updated_at)
            VALUES
                (:project_id, '跨引用项目', 'F1 fixture', '科幻', 90000, 'ACTIVE',
                 30, '{"low_ratio":0.9,"high_ratio":1.1,"floor":1500}',
                 :pack_id, :created_at, :updated_at)
            """,
            {
                "project_id": pid,
                "pack_id": pack_id,
                "created_at": now,
                "updated_at": now,
            },
        )
        for key, name in (("c1", "角色甲"), ("c2", "角色乙")):
            _insert_row(
                conn,
                """
                INSERT INTO characters
                    (character_id, project_id, name, role, core_json, visibility,
                     who_knows, aliases, inject_mode, created_at, updated_at)
                VALUES
                    (:character_id, :project_id, :name, 'supporting',
                     '{"personality":["沉稳"]}', 'PUBLIC', :who_knows, '[]',
                     'auto', :created_at, :updated_at)
                """,
                {
                    "character_id": ids[key],
                    "project_id": pid,
                    "name": name,
                    "who_knows": json.dumps([ids["c1"]], ensure_ascii=False),
                    "created_at": now,
                    "updated_at": now,
                },
            )
        # character_states：state_json 内嵌 location 与来源事件 id
        _insert_row(
            conn,
            """
            INSERT INTO character_states
                (character_id, state_version, state_json, visibility, who_knows,
                 created_at)
            VALUES
                (:character_id, 1, :state_json, 'VISIBLE', :who_knows, :created_at)
            """,
            {
                "character_id": ids["c1"],
                "state_json": json.dumps(
                    {
                        "location": ids["loc1"],
                        "knowledge": [{"source_event_id": ids["ev1"]}],
                    },
                    ensure_ascii=False,
                ),
                "who_knows": json.dumps([ids["c2"]], ensure_ascii=False),
                "created_at": now,
            },
        )
        _insert_row(
            conn,
            """
            INSERT INTO locations
                (location_id, project_id, name, statement, data_json, visibility,
                 who_knows, aliases, inject_mode, created_at, updated_at)
            VALUES
                (:location_id, :project_id, '旧城', '陈述', '{}', 'PUBLIC',
                 NULL, '[]', 'auto', :created_at, :updated_at)
            """,
            {"location_id": ids["loc1"], "project_id": pid,
             "created_at": now, "updated_at": now},
        )
        _insert_row(
            conn,
            """
            INSERT INTO world_rules
                (world_rule_id, project_id, name, statement, data_json,
                 visibility, who_knows, created_at, updated_at)
            VALUES
                (:world_rule_id, :project_id, '规则一', '陈述', '{}', 'PUBLIC',
                 NULL, :created_at, :updated_at)
            """,
            {"world_rule_id": ids["wr1"], "project_id": pid,
             "created_at": now, "updated_at": now},
        )
        _insert_row(
            conn,
            """
            INSERT INTO volumes
                (volume_id, project_id, number, title, status,
                 terminal_snapshot_json, arc_summary, created_at, updated_at)
            VALUES
                (:volume_id, :project_id, 1, '第一卷', 'active', NULL,
                 '卷摘要', :created_at, :updated_at)
            """,
            {"volume_id": ids["vol1"], "project_id": pid,
             "created_at": now, "updated_at": now},
        )
        _insert_row(
            conn,
            """
            INSERT INTO branches
                (branch_id, project_id, name, parent_branch_id,
                 base_state_version, status, created_at)
            VALUES
                (:branch_id, :project_id, 'main', NULL, 0, 'ACTIVE',
                 :created_at)
            """,
            {"branch_id": ids["branch1"], "project_id": pid,
             "created_at": now},
        )
        for key, number, title in (("ch1", 1, "第一章"), ("ch2", 2, "第二章")):
            _insert_row(
                conn,
                """
                INSERT INTO chapters
                    (chapter_id, project_id, number, title, plan_json, status,
                     visibility, who_knows, volume_id, created_at, updated_at)
                VALUES
                    (:chapter_id, :project_id, :number, :title, :plan_json,
                     'PLANNED', 'VISIBLE', NULL, :volume_id, :created_at,
                     :updated_at)
                """,
                {
                    "chapter_id": ids[key],
                    "project_id": pid,
                    "number": number,
                    "title": title,
                    "plan_json": json.dumps(
                        {"chapter_goal": "目标", "scenes": [ids["sc1"]]},
                        ensure_ascii=False,
                    ),
                    "volume_id": ids["vol1"],
                    "created_at": now,
                    "updated_at": now,
                },
            )
        _insert_row(
            conn,
            """
            INSERT INTO scenes
                (scene_id, chapter_id, order_index, plan_json, visibility,
                 who_knows)
            VALUES
                (:scene_id, :chapter_id, 1, :plan_json, 'VISIBLE', NULL)
            """,
            {
                "scene_id": ids["sc1"],
                "chapter_id": ids["ch1"],
                "plan_json": json.dumps(
                    {"characters": [ids["c1"], ids["c2"]],
                     "location": ids["loc1"]},
                    ensure_ascii=False,
                ),
            },
        )
        _insert_row(
            conn,
            """
            INSERT INTO drafts
                (draft_id, chapter_id, version, content, created_by,
                 prompt_version, model_id, created_at)
            VALUES
                (:draft_id, :chapter_id, 1, '正文内容。', 'human', NULL, NULL,
                 :created_at)
            """,
            {"draft_id": ids["dr1"], "chapter_id": ids["ch1"],
             "created_at": now},
        )
        _insert_row(
            conn,
            """
            INSERT INTO hooks
                (hook_id, project_id, name, introduced_chapter_id, status,
                 importance, expected_payoff_chapter_id, payoff_chapter_id,
                 visibility, who_knows, created_at, updated_at)
            VALUES
                (:hook_id, :project_id, '跨引用伏笔', :intro_chap, 'OPEN', 0.5,
                 :payoff_chap, NULL, 'RESTRICTED', :who_knows, :created_at,
                 :updated_at)
            """,
            {
                "hook_id": ids["hook1"],
                "project_id": pid,
                "intro_chap": ids["ch1"],
                "payoff_chap": ids["ch2"],
                "who_knows": json.dumps([ids["c1"]], ensure_ascii=False),
                "created_at": now,
                "updated_at": now,
            },
        )
        _insert_row(
            conn,
            """
            INSERT INTO narrative_debts
                (debt_id, project_id, description, created_chapter_id,
                 severity, deadline_chapter_id, status, visibility, who_knows,
                 created_at, updated_at)
            VALUES
                (:debt_id, :project_id, '债务描述', :created_chapter, 0.5,
                 :deadline_chapter, 'open', 'RESTRICTED', :who_knows,
                 :created_at, :updated_at)
            """,
            {
                "debt_id": ids["debt1"],
                "project_id": pid,
                "created_chapter": ids["ch1"],
                "deadline_chapter": ids["ch2"],
                "who_knows": json.dumps([ids["c1"], ids["c2"]],
                                        ensure_ascii=False),
                "created_at": now,
                "updated_at": now,
            },
        )
        _insert_row(
            conn,
            """
            INSERT INTO relationships
                (relationship_id, project_id, from_character_id,
                 to_character_id, relation_type, state_json, last_state_version,
                 visibility, who_knows)
            VALUES
                (:relationship_id, :project_id, :from_id, :to_id, 'ally',
                 '{"intensity":0.5}', 1, 'VISIBLE', :who_knows)
            """,
            {
                "relationship_id": ids["rel1"],
                "project_id": pid,
                "from_id": ids["c1"],
                "to_id": ids["c2"],
                "who_knows": json.dumps([ids["c1"]], ensure_ascii=False),
            },
        )
        # plot_events：cause / effects / participants / who_knows 四列都内嵌 id
        for key, causes, effects, day in (
            ("ev1", [ids["ev2"]], [], 1),
            ("ev2", [], [ids["ev1"]], 2),
        ):
            _insert_row(
                conn,
                """
                INSERT INTO plot_events
                    (event_id, project_id, type, cause_json, effects_json,
                     participants_json, location_id, time_json, status,
                     introduced_chapter_id, visibility, who_knows, description)
                VALUES
                    (:event_id, :project_id, 'conflict', :cause_json,
                     :effects_json, :participants_json, :location_id,
                     '{"timeline_day":1}', 'recorded', :introduced_chapter_id,
                     'RESTRICTED', :who_knows, '事件描述')
                """,
                {
                    "event_id": ids[key],
                    "project_id": pid,
                    "cause_json": json.dumps(causes),
                    "effects_json": json.dumps(effects),
                    "participants_json": json.dumps([ids["c1"], ids["c2"]]),
                    "location_id": ids["loc1"],
                    "introduced_chapter_id": ids["ch1"],
                    "who_knows": json.dumps([ids["c1"]], ensure_ascii=False),
                },
            )
        _insert_row(
            conn,
            """
            INSERT INTO timeline_events
                (timeline_event_id, project_id, event_id, day_index, time_ref,
                 description, visibility, who_knows)
            VALUES
                (:tle_id, :project_id, :event_id, 1, NULL, '时间线描述',
                 'VISIBLE', :who_knows)
            """,
            {
                "tle_id": ids["tle1"],
                "project_id": pid,
                "event_id": ids["ev1"],
                "who_knows": json.dumps([ids["c1"]], ensure_ascii=False),
            },
        )
        _insert_row(
            conn,
            """
            INSERT INTO memories
                (memory_id, project_id, kind, content, embedding_ref,
                 source_ids_json, created_at)
            VALUES
                (:memory_id, :project_id, 'semantic', '记忆内容', NULL,
                 :source_ids_json, :created_at)
            """,
            {
                "memory_id": ids["mem1"],
                "project_id": pid,
                "source_ids_json": json.dumps([ids["c1"], ids["ev1"]]),
                "created_at": now,
            },
        )
        # state_deltas（payload_json 内嵌 character/world/event/hook id）
        for key, version in (("d1", 0), ("d2", 1)):
            _insert_row(
                conn,
                """
                INSERT INTO state_deltas
                    (delta_id, chapter_id, workflow_run_id,
                     previous_state_version, delta_version, schema_version,
                     payload_json, status, supersedes, created_by, created_at)
                VALUES
                    (:delta_id, :chapter_id, 'wfr_cross_ref', :prev, 1,
                     'state-delta-v0', :payload_json, 'applied', NULL,
                     'tester', :created_at)
                """,
                {
                    "delta_id": ids[key],
                    "chapter_id": ids["ch1"],
                    "prev": version,
                    "payload_json": json.dumps(
                        {
                            "character_changes": [{"character_id": ids["c1"]}],
                            "world_changes": [
                                {"world_id": ids["loc1"],
                                 "world_kind": "location"}
                            ],
                            "new_events": [{"event_id": ids["ev1"]}],
                            "resolved_hooks": [{"hook_id": ids["hook1"]}],
                        },
                        ensure_ascii=False,
                    ),
                    "created_at": now,
                },
            )
        # commits（story_states.commit_id 的前向引用目标）
        for key, delta_key, version in (("cmt1", "d1", 1), ("cmt2", "d2", 2)):
            _insert_row(
                conn,
                """
                INSERT INTO commits
                    (commit_id, project_id, branch_id, chapter_id,
                     previous_state_version, resulting_state_version, delta_id,
                     validation_json, author_approval_json, timestamp,
                     workflow_run_id, rollback_of)
                VALUES
                    (:commit_id, :project_id, :branch_id, :chapter_id,
                     :prev, :resulting, :delta_id, :validation_json,
                     :author_approval_json, :timestamp, 'wfr_cross_ref', NULL)
                """,
                {
                    "commit_id": ids[key],
                    "project_id": pid,
                    "branch_id": ids["branch1"],
                    "chapter_id": ids["ch1"],
                    "prev": version - 1,
                    "resulting": version,
                    "delta_id": ids[delta_key],
                    "validation_json": json.dumps(
                        {"schema_valid": True,
                         "guardrail_results": [],
                         "validator_agent": "validator:v1"},
                        ensure_ascii=False,
                    ),
                    "author_approval_json": json.dumps(
                        {"required": True, "status": "approved",
                         "high_risk_change_ids": [ids["ev1"]]},
                        ensure_ascii=False,
                    ),
                    "timestamp": now,
                },
            )
        # story_states：两版本快照，id 出现在 list 元素字段 / dict 键 / 嵌套数组
        for key, version in (("version1", 1), ("version2", 2)):
            snapshot = {
                "state_version": version,
                "characters": [
                    {
                        "character_id": ids["c1"],
                        "name": "角色甲",
                        "current_state": {"location": ids["loc1"]},
                        "relationships": [
                            {
                                "relationship_id": ids["rel1"],
                                "to_character_id": ids["c2"],
                            }
                        ],
                    },
                    {
                        "character_id": ids["c2"],
                        "name": "角色乙",
                        "current_state": {},
                        "relationships": [],
                    },
                ],
                # dict 键 = 实体 id（只改 value 会让 check_state_sync 报 DRIFT）
                "world": {
                    "current_time_in_story": None,
                    "locations": {ids["loc1"]: {"name": "旧城"}},
                    "factions": {},
                    "world_rules": [{"world_rule_id": ids["wr1"]}],
                    "active_resources": {ids["loc1"]: {"resource": "粮草"}},
                },
                "hooks": [
                    {"hook_id": ids["hook1"],
                     "introduced_chapter_id": ids["ch1"]}
                ],
                "debts": [
                    {"debt_id": ids["debt1"],
                     "created_chapter_id": ids["ch1"]}
                ],
                "recent_events": [ids["ev1"]],
                "events": {
                    ids["ev1"]: {"participants": [ids["c1"], ids["c2"]]},
                    ids["ev2"]: {"participants": []},
                },
            }
            _insert_row(
                conn,
                """
                INSERT INTO story_states
                    (project_id, state_version, snapshot_json, commit_id,
                     created_at)
                VALUES
                    (:project_id, :state_version, :snapshot_json, :commit_id,
                     :created_at)
                """,
                {
                    "project_id": pid,
                    "state_version": version,
                    "snapshot_json": json.dumps(snapshot, ensure_ascii=False),
                    "commit_id": ids["cmt1"] if version == 1 else ids["cmt2"],
                    "created_at": now,
                },
            )
        _insert_row(
            conn,
            """
            INSERT INTO quality_reports
                (report_id, project_id, chapter_id, commit_id, run_id, overall,
                 scores_json, issues_json, judge_json, draft_version,
                 created_at)
            VALUES
                (:report_id, :project_id, :chapter_id, :commit_id, NULL, 80,
                 '{"overall":80}', :issues_json, NULL, 1, :created_at)
            """,
            {
                "report_id": ids["qr1"],
                "project_id": pid,
                "chapter_id": ids["ch1"],
                "commit_id": ids["cmt1"],
                "issues_json": json.dumps(
                    [{"rule_id": "x", "location": ids["ch1"],
                      "evidence_refs": [ids["ev1"]]}],
                    ensure_ascii=False,
                ),
                "created_at": now,
            },
        )
    finally:
        conn.close()
    return ids


def _collect_json_strings(value) -> set[str]:
    """递归收集已解析 JSON 结构里出现的全部字符串（含 dict 的 key）。"""
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        out: set[str] = set()
        for item in value:
            out |= _collect_json_strings(item)
        return out
    if isinstance(value, dict):
        out = set()
        for k, v in value.items():
            out |= _collect_json_strings(k)
            out |= _collect_json_strings(v)
        return out
    return set()


def _new_project_rows(db_path: str, new_pid: str) -> dict:
    """取导入副本的各表行（按业务键定位，便于与源 fixture 对照）。"""
    conn = get_connection(db_path)
    try:
        def one(sql: str, params: tuple = ()) -> dict:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row is not None else {}

        chars = {
            r["name"]: r["character_id"]
            for r in conn.execute(
                "SELECT character_id, name FROM characters WHERE project_id = ?",
                (new_pid,),
            ).fetchall()
        }
        chapter1 = one(
            "SELECT * FROM chapters WHERE project_id = ? AND number = 1", (new_pid,)
        )
        # ev1 / ev2 按 effects_json 区分：ev1 无 effect，ev2 的 effects 指向 ev1
        events = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM plot_events WHERE project_id = ?", (new_pid,)
            ).fetchall()
        ]
        ev1 = next(e for e in events if e["effects_json"] == "[]")
        ev2 = next(e for e in events if e is not ev1)
        return {
            "chars": chars,
            "project": one("SELECT * FROM projects WHERE project_id = ?", (new_pid,)),
            "volume": one("SELECT * FROM volumes WHERE project_id = ?", (new_pid,)),
            "chapter1": chapter1,
            "chapter2": one(
                "SELECT * FROM chapters WHERE project_id = ? AND number = 2",
                (new_pid,),
            ),
            "hook": one("SELECT * FROM hooks WHERE project_id = ?", (new_pid,)),
            "ev1": ev1,
            "ev2": ev2,
            "delta": one(
                "SELECT * FROM state_deltas WHERE chapter_id = ?",
                (chapter1["chapter_id"],),
            ),
            "snapshot": one(
                "SELECT * FROM story_states WHERE project_id = ? AND state_version = 2",
                (new_pid,),
            ),
            "memory": one("SELECT * FROM memories WHERE project_id = ?", (new_pid,)),
            "scene": one(
                "SELECT * FROM scenes WHERE chapter_id = ?",
                (chapter1["chapter_id"],),
            ),
            "relationship": one(
                "SELECT * FROM relationships WHERE project_id = ?", (new_pid,)
            ),
            "char_state": one(
                "SELECT * FROM character_states WHERE character_id = ?",
                (chars["角色甲"],),
            ),
            "quality": one(
                "SELECT * FROM quality_reports WHERE project_id = ?", (new_pid,)
            ),
            "commit_ids": sorted(
                r["commit_id"]
                for r in conn.execute(
                    "SELECT commit_id FROM commits WHERE project_id = ?", (new_pid,)
                ).fetchall()
            ),
        }
    finally:
        conn.close()



def test_roundtrip_remaps_json_embedded_ids(db_path: str) -> None:
    """F1：导出 → 导入后，JSON 列内嵌 id（值 + dict 键）全部指向新 namespace。

    覆盖 story_states.snapshot_json / state_deltas.payload_json / plot_events 四列 /
    hooks.who_knows / character_states.state_json / relationships / scenes.plan_json /
    memories.source_ids_json / quality_reports.issues_json，并断言无旧 id 残留 +
    check_state_sync 对副本项目 SYNC OK。
    """
    from scripts.check_state_sync import main as check_main

    ids = _seed_cross_reference_project(db_path)
    pkg = BackupService(db_path).export_project(ids["pid"])
    assert len(pkg["tables"]["volumes"]) == 1

    new_pid = BackupService(db_path).import_project(pkg)["project_id"]
    assert new_pid != ids["pid"]
    rows = _new_project_rows(db_path, new_pid)
    new_c1 = rows["chars"]["角色甲"]
    new_c2 = rows["chars"]["角色乙"]
    new_ev1 = rows["ev1"]["event_id"]
    new_ev2 = rows["ev2"]["event_id"]

    # --- story_states.snapshot_json：字符串值 / dict 键 / 嵌套数组三层都换新 ---
    snapshot_raw = rows["snapshot"]["snapshot_json"]
    assert isinstance(snapshot_raw, str), "snapshot_json 必须保持 TEXT 形态"
    snapshot = json.loads(snapshot_raw)
    assert snapshot["characters"][0]["character_id"] == new_c1
    assert snapshot["characters"][0]["current_state"]["location"] in (
        snapshot["world"]["locations"]
    )
    assert list(snapshot["world"]["locations"]) != [ids["loc1"]], (
        "world.locations 的 dict 键（= location_id）未重映射"
    )
    assert set(snapshot["events"]) == {new_ev1, new_ev2}, (
        "events 的 dict 键（= event_id）未重映射"
    )
    assert set(snapshot["world"]["active_resources"]) == set(
        snapshot["world"]["locations"]
    )
    assert snapshot["recent_events"] == [new_ev1]
    assert snapshot["world"]["world_rules"][0]["world_rule_id"] != ids["wr1"]
    assert snapshot["hooks"][0]["hook_id"] == rows["hook"]["hook_id"]
    assert snapshot["hooks"][0]["introduced_chapter_id"] == rows["chapter1"]["chapter_id"]
    assert snapshot["debts"][0]["debt_id"] != ids["debt1"]
    assert snapshot["characters"][0]["relationships"][0]["relationship_id"] == (
        rows["relationship"]["relationship_id"]
    )
    assert snapshot["characters"][0]["relationships"][0]["to_character_id"] == new_c2

    # --- story_states.commit_id 前向引用（commits 在 story_states 之后导入）---
    assert rows["snapshot"]["commit_id"] in rows["commit_ids"], (
        "story_states.commit_id 未重映射到本包 commits（前向引用漏映射）"
    )

    # --- state_deltas.payload_json ---
    payload = json.loads(rows["delta"]["payload_json"])
    assert payload["character_changes"][0]["character_id"] == new_c1
    assert payload["world_changes"][0]["world_id"] in snapshot["world"]["locations"]
    assert payload["new_events"][0]["event_id"] == new_ev1
    assert payload["resolved_hooks"][0]["hook_id"] == rows["hook"]["hook_id"]

    # --- plot_events 四列 / hooks.who_knows ---
    assert json.loads(rows["ev1"]["participants_json"]) == [new_c1, new_c2]
    assert json.loads(rows["ev2"]["participants_json"]) == [new_c1, new_c2]
    assert json.loads(rows["ev1"]["cause_json"]) == [new_ev2]
    assert json.loads(rows["ev1"]["effects_json"]) == []
    assert json.loads(rows["ev2"]["cause_json"]) == []
    assert json.loads(rows["ev2"]["effects_json"]) == [new_ev1]
    assert rows["ev1"]["location_id"] in snapshot["world"]["locations"]
    assert rows["ev1"]["introduced_chapter_id"] == rows["chapter1"]["chapter_id"]
    assert json.loads(rows["ev1"]["who_knows"]) == [new_c1]
    assert json.loads(rows["hook"]["who_knows"]) == [new_c1]

    # --- character_states.state_json / scenes.plan_json / memories / issues ---
    state_json = json.loads(rows["char_state"]["state_json"])
    assert state_json["location"] in snapshot["world"]["locations"]
    assert state_json["knowledge"][0]["source_event_id"] == new_ev1
    assert json.loads(rows["char_state"]["who_knows"]) == [new_c2]
    plan = json.loads(rows["scene"]["plan_json"])
    assert plan["characters"] == [new_c1, new_c2]
    assert plan["location"] == state_json["location"]
    assert set(json.loads(rows["memory"]["source_ids_json"])) == {new_c1, new_ev1}
    issues = json.loads(rows["quality"]["issues_json"])
    assert issues[0]["location"] == rows["chapter1"]["chapter_id"]
    assert issues[0]["evidence_refs"] == [new_ev1]

    # --- 无旧 id 残留：扫副本项目各行的 JSON 序列化文本 ---
    old_ids = {k: v for k, v in ids.items() if k != "pid"}
    blob = json.dumps(
        [rows[r] for r in (
            "project", "volume", "chapter1", "chapter2", "hook", "ev1", "ev2",
            "delta", "snapshot", "memory", "scene", "relationship",
            "char_state", "quality",
        )],
        ensure_ascii=False,
    )
    residue = sorted(f"{k}={v}" for k, v in old_ids.items() if v in blob)
    assert residue == [], f"导入副本 JSON 残留旧 id: {residue}"
    assert ids["pid"] not in blob

    # --- check_state_sync：副本项目 SYNC OK（快照集合 == DB 实体集合）---
    assert check_main(["--db", db_path, "--project", new_pid]) == 0, (
        "check_state_sync 对导入副本报漂移"
    )


def test_roundtrip_keeps_volumes_and_chapter_link(db_path: str) -> None:
    """F2：volumes 入白名单后，带卷项目 roundtrip 卷行与 chapters.volume_id 完整。"""
    ids = _seed_cross_reference_project(db_path)
    pkg = BackupService(db_path).export_project(ids["pid"])

    # 导出含卷行，且不含无关项目
    vol_rows = pkg["tables"]["volumes"]
    assert [r["volume_id"] for r in vol_rows] == [ids["vol1"]]
    assert vol_rows[0]["project_id"] == ids["pid"]

    new_pid = BackupService(db_path).import_project(pkg)["project_id"]
    conn = get_connection(db_path)
    try:
        new_vol = conn.execute(
            "SELECT * FROM volumes WHERE project_id = ?", (new_pid,)
        ).fetchone()
        assert new_vol is not None, "导入副本丢了卷行"
        assert new_vol["volume_id"] != ids["vol1"]
        assert new_vol["number"] == 1
        assert new_vol["arc_summary"] == "卷摘要"

        new_chapters = conn.execute(
            "SELECT chapter_id, volume_id FROM chapters WHERE project_id = ? "
            "ORDER BY number ASC",
            (new_pid,),
        ).fetchall()
        assert len(new_chapters) == 2
        for ch in new_chapters:
            assert ch["volume_id"] == new_vol["volume_id"], (
                "chapters.volume_id 未重映射到副本卷（悬空 / 跨项目指向）"
            )
        # 源项目卷与章节未被扰动
        orig_vol = conn.execute(
            "SELECT volume_id FROM volumes WHERE project_id = ?", (ids["pid"],)
        ).fetchone()
        assert orig_vol["volume_id"] == ids["vol1"]
        orig_ch = conn.execute(
            "SELECT volume_id FROM chapters WHERE chapter_id = ?", (ids["ch1"],)
        ).fetchone()
        assert orig_ch["volume_id"] == ids["vol1"]
    finally:
        conn.close()


def test_import_preserves_projects_late_columns(db_path: str) -> None:
    """F4：projects 后加列（word_band_json / genre_pack_id）roundtrip 保留。"""
    ids = _seed_cross_reference_project(db_path)
    pkg = BackupService(db_path).export_project(ids["pid"])
    assert pkg["project"]["word_band_json"] == (
        '{"low_ratio":0.9,"high_ratio":1.1,"floor":1500}'
    )
    assert pkg["project"]["genre_pack_id"] == "gp_cross_ref"

    new_pid = BackupService(db_path).import_project(pkg)["project_id"]
    conn = get_connection(db_path)
    try:
        new_project = conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (new_pid,)
        ).fetchone()
        assert new_project["word_band_json"] == (
            '{"low_ratio":0.9,"high_ratio":1.1,"floor":1500}'
        )
        assert new_project["genre_pack_id"] == "gp_cross_ref"
        # 其余既有列语义不变
        assert new_project["target_words"] == 90000
        assert new_project["foreshadow_overdue_chapters"] == 30
        assert new_project["status"] == "ACTIVE"
        assert new_project["name"] == "跨引用项目（导入）"
    finally:
        conn.close()


def test_import_drops_project_keys_outside_schema(db_path: str) -> None:
    """F4 反向：包内 project 多出的键（非目标库列）被丢弃，不炸导入。"""
    pid = _seed_minimal_project(db_path)
    pkg = BackupService(db_path).export_project(pid)
    pkg["project"]["not_a_real_column"] = "注入尝试"
    pkg["project"]["project_id); DROP TABLE projects;--"] = "注入尝试"

    new_pid = BackupService(db_path).import_project(pkg)["project_id"]
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM projects WHERE project_id = ?", (new_pid,)
        ).fetchone()
        assert row is not None
        assert "not_a_real_column" not in row.keys()
    finally:
        conn.close()


def _make_dangling_fk_package(db_path: str) -> tuple[dict, str]:
    """构造带悬空 FK 的包：scenes.chapter_id 指向不存在的章节。"""
    ids = _seed_cross_reference_project(db_path)
    pkg = BackupService(db_path).export_project(ids["pid"])
    assert len(pkg["tables"]["scenes"]) == 1
    pkg["tables"]["scenes"][0]["chapter_id"] = "ch_missing_chapter_f3"
    return pkg, ids["pid"]


def test_import_rejects_dangling_fk_and_rolls_back(db_path: str) -> None:
    """F3：包内悬空 FK → 提交前 foreign_key_check 拦截，整体回滚 + 明确报错。"""
    pkg, src_pid = _make_dangling_fk_package(db_path)

    conn = get_connection(db_path)
    try:
        before = {
            t: conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
            for t in ("projects", "scenes", "chapters", "volumes", "hooks",
                      "characters", "story_states", "state_deltas", "commits")
        }
    finally:
        conn.close()

    with pytest.raises(ValueError, match="dangling foreign keys"):
        BackupService(db_path).import_project(pkg)

    # 报错消息须指出具体列（便于定位坏包）
    try:
        BackupService(db_path).import_project(pkg)
    except ValueError as exc:
        msg = str(exc)
        assert "scenes.chapter_id" in msg, msg
        assert "chapters" in msg, msg

    conn = get_connection(db_path)
    try:
        for table, n_before in before.items():
            n_after = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            assert n_after == n_before, (
                f"悬空 FK 导入未回滚：{table} {n_before} -> {n_after}"
            )
        # 源项目仍在，且没有半成品项目
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM projects WHERE project_id = ?", (src_pid,)
        ).fetchone()["c"] == 1
    finally:
        conn.close()


def test_import_ignores_pre_existing_fk_violations(db_path: str) -> None:
    """F3 边界：库内既有（非本次导入引入）的悬挂引用不应阻断干净包导入。"""
    pid = _seed_minimal_project(db_path)
    conn = get_connection(db_path)
    try:
        # 手工制造一条历史遗留违例：scenes.chapter_id 指向不存在的章节
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            """
            INSERT INTO scenes (scene_id, chapter_id, order_index, plan_json,
                                visibility, who_knows)
            VALUES ('sc_legacy_dangling', 'ch_legacy_missing', 1, '{}',
                    'VISIBLE', NULL)
            """
        )
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()

    pkg = BackupService(db_path).export_project(pid)
    new_pid = BackupService(db_path).import_project(pkg)["project_id"]
    assert new_pid != pid

    # 历史违例仍在（导入不负责清理），但不影响导入本身
    conn = get_connection(db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM projects WHERE project_id = ?", (new_pid,)
        ).fetchone()["c"] == 1
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM scenes WHERE scene_id = 'sc_legacy_dangling'"
        ).fetchone()["c"] == 1
    finally:
        conn.close()
