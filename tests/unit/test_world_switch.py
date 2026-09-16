# -*- coding: utf-8 -*-
"""scripts/world_switch.py —— 世界切换（快穿位面）作用域归档。

覆盖：
- dry-run 不写库（全表行级快照断言）；
- apply：上一世 canon 实体 → ``inject_mode='never'``、主角豁免、非终态 hooks →
  ``ABANDONED`` + name 结算标记、open/acknowledged debts → ``forgiven``；
- 结算单落 ``volumes.terminal_snapshot_json[world_settlement]`` + ``arc_summary``
  追加结算块（保留既有内容；重跑不重复追加）；
- 幂等：连跑两次第二次零变更、零写入（含 ``updated_at``）；
- 更晚卷归属的实体 / hook / debt 跳过不归档；
- 豁免名单打错 → 拒写；``--apply`` 无豁免名单 → 拒写；结算单 volume_number 错配 → 拒写；
- **装配口径**：切换后为第二世界第 1 章装配 writer / director，主角仍在注入面内、
  上一世实体已从注入面退出（突变验证的看守点：去掉豁免分支该用例必红）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from packages.core.context_engine import build_director_input, build_writer_input
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from scripts.world_switch import (
    ARCHIVED_INJECT_MODE,
    DEBT_ARCHIVE_STATUS,
    HOOK_ARCHIVE_STATUS,
    SETTLEMENT_MARKER,
    SETTLEMENT_SNAPSHOT_KEY,
    build_plan,
    load_settlement,
    main,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

PROTAGONIST = "林昭"
SUPPORTING = "苏婉清"
LOCATION = "临江县仓"
FACTION = "永丰号·吴七爷一系"
NEXT_WORLD_ACTOR = "雁门守将·陆昀"


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def _conn(db_path: Path) -> sqlite3.Connection:
    conn = get_connection(db_path)
    return conn


def _exec(db_path: Path, sql: str, params: tuple) -> None:
    conn = _conn(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "world.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path, pid: str = "prj_world") -> str:
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO projects (project_id, name, premise, genre, target_words, status,"
        " created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)",
        (pid, "快穿测试项目", now, now),
    )
    return pid


def _insert_volume(db_path: Path, pid: str, number: int, *, status: str = "active",
                   arc_summary: str | None = None,
                   terminal_snapshot_json: str | None = None) -> str:
    vid = new_id("vol")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO volumes (volume_id, project_id, number, title, status,"
        " terminal_snapshot_json, created_at, updated_at, arc_summary)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (vid, pid, number, f"第{number}卷", status, terminal_snapshot_json, now, now,
         arc_summary),
    )
    return vid


def _insert_chapter(db_path: Path, pid: str, number: int, volume_id: str, *,
                    title: str = "", plan_json: dict | None = None) -> str:
    cid = new_id("ch")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status,"
        " visibility, who_knows, created_at, updated_at, volume_id)"
        " VALUES (?, ?, ?, ?, ?, 'PLANNED', 'VISIBLE', NULL, ?, ?, ?)",
        (cid, pid, number, title or f"第{number}章",
         json.dumps(plan_json or {}, ensure_ascii=False), now, now, volume_id),
    )
    return cid


def _insert_character(db_path: Path, pid: str, name: str, *, role: str = "supporting",
                      inject_mode: str = "auto") -> str:
    eid = new_id("char")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO characters (character_id, project_id, name, role, core_json,"
        " visibility, who_knows, created_at, updated_at, aliases, inject_mode)"
        " VALUES (?, ?, ?, ?, '{}', 'PUBLIC', NULL, ?, ?, '[]', ?)",
        (eid, pid, name, role, now, now, inject_mode),
    )
    return eid


def _insert_location(db_path: Path, pid: str, name: str, *,
                     inject_mode: str = "auto") -> str:
    eid = new_id("loc")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO locations (location_id, project_id, name, statement, data_json,"
        " visibility, who_knows, created_at, updated_at, aliases, inject_mode)"
        " VALUES (?, ?, ?, '', '{}', 'PUBLIC', NULL, ?, ?, '[]', ?)",
        (eid, pid, name, now, now, inject_mode),
    )
    return eid


def _insert_faction(db_path: Path, pid: str, name: str, *,
                    inject_mode: str = "auto") -> str:
    eid = new_id("fac")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO factions (faction_id, project_id, name, statement, data_json,"
        " visibility, who_knows, created_at, updated_at, aliases, inject_mode)"
        " VALUES (?, ?, ?, '', '{}', 'VISIBLE', NULL, ?, ?, '[]', ?)",
        (eid, pid, name, now, now, inject_mode),
    )
    return eid


def _insert_hook(db_path: Path, pid: str, name: str, *, status: str = "OPEN",
                 introduced_chapter_id: str | None = None) -> str:
    hid = new_id("hook")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO hooks (hook_id, project_id, name, introduced_chapter_id, status,"
        " importance, expected_payoff_chapter_id, payoff_chapter_id, visibility,"
        " who_knows, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, 0.5, NULL, NULL, 'RESTRICTED', NULL, ?, ?)",
        (hid, pid, name, introduced_chapter_id, status, now, now),
    )
    return hid


def _insert_debt(db_path: Path, pid: str, description: str, *, status: str = "open",
                 created_chapter_id: str | None = None) -> str:
    did = new_id("debt")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO narrative_debts (debt_id, project_id, description,"
        " created_chapter_id, severity, deadline_chapter_id, status, visibility,"
        " who_knows, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 0.5, NULL, ?, 'RESTRICTED', NULL, ?, ?)",
        (did, pid, description, created_chapter_id, status, now, now),
    )
    return did


def _insert_plot_event(db_path: Path, pid: str, *, introduced_chapter_id: str | None,
                       participants: list[dict] | None = None,
                       location_id: str | None = None) -> str:
    eid = new_id("event")
    _exec(
        db_path,
        "INSERT INTO plot_events (event_id, project_id, type, cause_json, effects_json,"
        " participants_json, location_id, time_json, status, introduced_chapter_id,"
        " visibility, who_knows, description)"
        " VALUES (?, ?, 'other', '[]', '[]', ?, ?, '{}', 'planned', ?, 'RESTRICTED',"
        " NULL, '')",
        (eid, pid, json.dumps(participants or [], ensure_ascii=False),
         location_id, introduced_chapter_id),
    )
    return eid


def _write_settlement(tmp_path: Path, *, volume_number: int = 1,
                      name: str = "settlement.json", note: str = "第一世界收束") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({
        "volume_number": volume_number,
        "settlement": {
            "恶名清洗度": "七成：县仓承付账转为公账，恶名转为可谈",
            "账本结余": "二十四两八钱 + 三百一十七户赊账承付权",
            "据点规模": "县仓南库 + 北山玄都观 + 城北小茶馆三处",
            "人心归附度": "城南登记领粮二百三十七人，三家粮行转为供货方",
            "记忆损耗": "老娘的口头禅、周账房的姓名与面容已不可考",
        },
        "chips": ["情报卷·灾变预判", "人脉卷·账房旧识"],
        "note": note,
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _seed(tmp_path: Path) -> dict:
    """建一个「第 1 世已写完、准备开第 2 世」的最小项目。"""
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    vol1 = _insert_volume(db, pid, 1, arc_summary="第一世：临江灾变囤货")
    vol2 = _insert_volume(db, pid, 2)
    ch1 = _insert_chapter(db, pid, 1, vol1)
    ch2 = _insert_chapter(db, pid, 2, vol1)
    ch25 = _insert_chapter(db, pid, 25, vol2, title="雁门卫的第一笔买卖", plan_json={
        "chapter_goal": "林昭在雁门卫醒来，接手原身的失守罪状",
        "key_beats": ["林昭在军寨醒来，接下八百石军粮亏空"],
    })
    ids = {
        "db": db, "project": pid, "vol1": vol1, "vol2": vol2,
        "ch1": ch1, "ch2": ch2, "ch25": ch25,
        "hero": _insert_character(db, pid, PROTAGONIST, role="protagonist"),
        "supporting": _insert_character(db, pid, SUPPORTING),
        "next_actor": _insert_character(db, pid, NEXT_WORLD_ACTOR),
        "location": _insert_location(db, pid, LOCATION),
        "location_no_evidence": _insert_location(db, pid, "北山玄都观后山"),
        "faction": _insert_faction(db, pid, FACTION),
        "hook_open": _insert_hook(db, pid, "永丰号旧账未清", introduced_chapter_id=ch1),
        "hook_resolved": _insert_hook(db, pid, "周账房的真账本", status="RESOLVED",
                                      introduced_chapter_id=ch2),
        "hook_next": _insert_hook(db, pid, "北狄异动", introduced_chapter_id=ch25),
        "debt_open": _insert_debt(db, pid, "林昭欠玄都观稻谷一百二十斛", status="open",
                                  created_chapter_id=ch1),
        "debt_paid": _insert_debt(db, pid, "欠哑伯工钱七块二角", status="paid",
                                  created_chapter_id=ch2),
        "debt_next": _insert_debt(db, pid, "欠盐引行三百两", status="open",
                                  created_chapter_id=ch25),
    }
    # 归属证据：第一世的实体出现在第一世的 plot_event；第二世界的实体出现在第二世界。
    _insert_plot_event(db, pid, introduced_chapter_id=ch1,
                       participants=[{"character_id": ids["supporting"]},
                                     {"faction_id": ids["faction"]}],
                       location_id=ids["location"])
    _insert_plot_event(db, pid, introduced_chapter_id=ch25,
                       participants=[{"character_id": ids["next_actor"]}])
    return ids


def _dump(db_path: Path) -> dict:
    """全表行级快照（幂等 / 不写库断言的判据）。"""
    tables = ("characters", "locations", "factions", "hooks", "narrative_debts",
              "volumes", "chapters")
    out: dict = {}
    conn = _conn(db_path)
    try:
        for table in tables:
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
            out[table] = rows
    finally:
        conn.close()
    return out


def _inject_modes(db_path: Path, table: str) -> dict:
    conn = _conn(db_path)
    try:
        id_col = {"characters": "character_id", "locations": "location_id",
                  "factions": "faction_id"}[table]
        return {r[id_col]: r["inject_mode"]
                for r in conn.execute(f"SELECT {id_col}, inject_mode FROM {table}")}
    finally:
        conn.close()


def _hook(db_path: Path, hook_id: str) -> dict:
    conn = _conn(db_path)
    try:
        return dict(conn.execute("SELECT * FROM hooks WHERE hook_id = ?",
                                 (hook_id,)).fetchone())
    finally:
        conn.close()


def _volume_row(db_path: Path, volume_id: str) -> dict:
    conn = _conn(db_path)
    try:
        return dict(conn.execute("SELECT * FROM volumes WHERE volume_id = ?",
                                 (volume_id,)).fetchone())
    finally:
        conn.close()


def _names_in(value) -> set[str]:
    """递归收集 payload 里出现的字符串叶子（用于装配断言）。"""
    found: set[str] = set()
    if isinstance(value, str):
        found.add(value)
    elif isinstance(value, dict):
        for item in value.values():
            found |= _names_in(item)
    elif isinstance(value, list):
        for item in value:
            found |= _names_in(item)
    return found


def _excerpt_entries(payload: dict, key: str) -> list[dict]:
    """只取真正进注入面的条目（剥掉 ``_suppressed_*`` 元标记块）。"""
    return [c for c in payload.get(key) or [] if isinstance(c, dict)
            and "_suppressed_" not in c]


# ---------------------------------------------------------------------------
# 1. dry-run 不写库
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path, capsys):
    ids = _seed(tmp_path)
    settlement = _write_settlement(tmp_path)
    before = _dump(ids["db"])

    code = main([
        "--project", ids["project"], "--from-volume", "1", "--to-volume", "2",
        "--settlement", str(settlement), "--keep-name", PROTAGONIST,
        "--db", str(ids["db"]),
    ])
    out = capsys.readouterr().out

    assert code == 0
    assert _dump(ids["db"]) == before, "dry-run 不得写库"
    assert "dry-run：未写库" in out
    assert PROTAGONIST in out and SUPPORTING in out


# ---------------------------------------------------------------------------
# 2. apply：归档上一世 + 豁免主角 + 落结算单
# ---------------------------------------------------------------------------


def test_apply_archives_previous_world_and_keeps_protagonist(tmp_path):
    ids = _seed(tmp_path)
    settlement = _write_settlement(tmp_path)

    code = main([
        "--project", ids["project"], "--from-volume", "1", "--to-volume", "2",
        "--settlement", str(settlement), "--keep-name", PROTAGONIST, "--apply",
        "--db", str(ids["db"]),
    ])

    assert code == 0
    assert _inject_modes(ids["db"], "characters")[ids["supporting"]] == ARCHIVED_INJECT_MODE
    assert _inject_modes(ids["db"], "locations")[ids["location"]] == ARCHIVED_INJECT_MODE
    assert _inject_modes(ids["db"], "factions")[ids["faction"]] == ARCHIVED_INJECT_MODE
    # 主角：豁免 → 保持原值（auto），没被归档
    assert _inject_modes(ids["db"], "characters")[ids["hero"]] == "auto"
    # 更晚卷的实体不受影响
    assert _inject_modes(ids["db"], "characters")[ids["next_actor"]] == "auto"

    hook = _hook(ids["db"], ids["hook_open"])
    assert hook["status"] == HOOK_ARCHIVE_STATUS
    assert SETTLEMENT_MARKER in hook["name"]
    assert _hook(ids["db"], ids["hook_resolved"])["status"] == "RESOLVED"
    assert _hook(ids["db"], ids["hook_next"])["status"] == "OPEN"

    conn = _conn(ids["db"])
    try:
        debts = {r["debt_id"]: r["status"]
                 for r in conn.execute("SELECT debt_id, status FROM narrative_debts")}
    finally:
        conn.close()
    assert debts[ids["debt_open"]] == DEBT_ARCHIVE_STATUS
    assert debts[ids["debt_paid"]] == "paid"
    assert debts[ids["debt_next"]] == "open"

    # 结算单落 volumes
    vol1 = _volume_row(ids["db"], ids["vol1"])
    snapshot = json.loads(vol1["terminal_snapshot_json"])
    record = snapshot[SETTLEMENT_SNAPSHOT_KEY]
    assert record["settlement_payload"]["settlement"]["恶名清洗度"].startswith("七成")
    assert record["settlement_payload"]["chips"] == ["情报卷·灾变预判", "人脉卷·账房旧识"]
    # 校验后的五口径段单独存一份（消费方可直接按键取用，不必再挖 settlement_payload）
    assert record["settlement"]["据点规模"].startswith("县仓南库")
    assert record["volume_number"] == 1 and record["to_volume"] == 2
    assert record["kept"]["characters"] == [ids["hero"]]
    assert record["scope"]["characters"]["archived"] == 1  # 苏婉清（主角豁免）
    assert record["scope"]["hooks"]["archived"] == 1

    # arc_summary 保留既有内容（seed 里预置的卷纲）并追加结算块
    assert "第一世：临江灾变囤货" in vol1["arc_summary"]
    assert SETTLEMENT_MARKER in vol1["arc_summary"]


# ---------------------------------------------------------------------------
# 3. 幂等：第二次零变更、零写入
# ---------------------------------------------------------------------------


def test_apply_is_idempotent(tmp_path, capsys):
    ids = _seed(tmp_path)
    settlement = _write_settlement(tmp_path)
    argv = ["--project", ids["project"], "--from-volume", "1", "--to-volume", "2",
            "--settlement", str(settlement), "--keep-name", PROTAGONIST, "--apply",
            "--db", str(ids["db"])]

    assert main(argv) == 0
    after_first = _dump(ids["db"])
    capsys.readouterr()

    assert main(argv) == 0
    out_second = capsys.readouterr().out
    after_second = _dump(ids["db"])

    assert after_second == after_first, "第二次 apply 必须零写入（含 updated_at）"
    assert "[变更] 本次改 0 行" in out_second
    assert "[写入] " not in out_second
    # arc_summary 的结算块只出现一次（成对标记整块替换，不重复追加）
    vol1 = _volume_row(ids["db"], ids["vol1"])
    assert vol1["arc_summary"].count(SETTLEMENT_MARKER) == 1


# ---------------------------------------------------------------------------
# 4. 结算单 / 豁免名单的校验与拒写
# ---------------------------------------------------------------------------


def test_keeps_list_typo_blocks_apply(tmp_path, capsys):
    ids = _seed(tmp_path)
    settlement = _write_settlement(tmp_path)
    before = _dump(ids["db"])

    code = main([
        "--project", ids["project"], "--from-volume", "1", "--to-volume", "2",
        "--settlement", str(settlement), "--keep-name", "林昭昭", "--apply",
        "--db", str(ids["db"]),
    ])
    out = capsys.readouterr().out

    assert code == 1
    assert "KEEP-NOT-FOUND" in out
    assert _dump(ids["db"]) == before


def test_apply_without_keep_is_refused(tmp_path, capsys):
    ids = _seed(tmp_path)
    settlement = _write_settlement(tmp_path)
    before = _dump(ids["db"])

    code = main([
        "--project", ids["project"], "--from-volume", "1", "--to-volume", "2",
        "--settlement", str(settlement), "--apply", "--db", str(ids["db"]),
    ])
    out = capsys.readouterr().out

    assert code == 1
    assert "KEEP-EMPTY" in out
    assert _dump(ids["db"]) == before


def test_apply_no_keep_flag_allows_archiving_everything(tmp_path, capsys):
    ids = _seed(tmp_path)
    settlement = _write_settlement(tmp_path)

    code = main([
        "--project", ids["project"], "--from-volume", "1", "--to-volume", "2",
        "--settlement", str(settlement), "--no-keep", "--apply",
        "--db", str(ids["db"]),
    ])
    out = capsys.readouterr().out

    assert code == 0
    assert "--no-keep" in out
    assert _inject_modes(ids["db"], "characters")[ids["hero"]] == ARCHIVED_INJECT_MODE


def test_settlement_volume_mismatch_blocks(tmp_path, capsys):
    ids = _seed(tmp_path)
    settlement = _write_settlement(tmp_path, volume_number=3)
    before = _dump(ids["db"])

    code = main([
        "--project", ids["project"], "--from-volume", "1", "--to-volume", "2",
        "--settlement", str(settlement), "--keep-name", PROTAGONIST, "--apply",
        "--db", str(ids["db"]),
    ])
    out = capsys.readouterr().out

    assert code == 1
    assert "SETTLEMENT-VOLUME" in out
    assert _dump(ids["db"]) == before


# ---------------------------------------------------------------------------
# 5. 归属判定：更晚卷的实体 / 行跳过
# ---------------------------------------------------------------------------


def test_later_volume_entities_are_skipped(tmp_path, capsys):
    ids = _seed(tmp_path)
    settlement = _write_settlement(tmp_path)

    code = main([
        "--project", ids["project"], "--from-volume", "1", "--to-volume", "2",
        "--settlement", str(settlement), "--keep-name", PROTAGONIST,
        "--json", "--db", str(ids["db"]),
    ])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    skipped = {row["id"]: row["reason"] for row in payload["skipped"]}
    assert skipped[ids["next_actor"]] == "later_volume"
    assert payload["scope_counts"]["characters"]["total"] == 2  # 主角 + 苏婉清
    assert payload["scope_counts"]["hooks"]["total"] == 2
    assert payload["scope_counts"]["debts"]["total"] == 2


def test_settlement_missing_key_is_warning_only(tmp_path, capsys):
    ids = _seed(tmp_path)
    path = tmp_path / "partial.json"
    path.write_text(json.dumps({
        "volume_number": 1,
        "settlement": {"恶名清洗度": "五成"},
    }, ensure_ascii=False), encoding="utf-8")

    code = main([
        "--project", ids["project"], "--from-volume", "1", "--to-volume", "2",
        "--settlement", str(path), "--keep-name", PROTAGONIST, "--apply",
        "--db", str(ids["db"]),
    ])
    out = capsys.readouterr().out

    assert code == 0
    assert "SETTLEMENT-KEY" in out and "SETTLEMENT-CHIPS" in out


# ---------------------------------------------------------------------------
# 6. 既有内容不被覆盖
# ---------------------------------------------------------------------------


def test_existing_arc_summary_and_terminal_snapshot_are_preserved(tmp_path):
    ids = _seed(tmp_path)
    # 覆写卷 1：既有 arc_summary（人工策展） + 已有终态快照（例如 seal 写入的故事快照）
    story_snapshot = json.dumps({"characters": [{"character_id": "char_x"}]},
                               ensure_ascii=False)
    _exec(ids["db"],
          "UPDATE volumes SET arc_summary = ?, terminal_snapshot_json = ?"
          " WHERE volume_id = ?",
          ("第一世手写卷纲：临江灾变囤货", story_snapshot, ids["vol1"]))
    settlement = _write_settlement(tmp_path)

    code = main([
        "--project", ids["project"], "--from-volume", "1", "--to-volume", "2",
        "--settlement", str(settlement), "--keep-name", PROTAGONIST, "--apply",
        "--db", str(ids["db"]),
    ])

    assert code == 0
    vol1 = _volume_row(ids["db"], ids["vol1"])
    assert "第一世手写卷纲：临江灾变囤货" in vol1["arc_summary"]
    snapshot = json.loads(vol1["terminal_snapshot_json"])
    assert snapshot["characters"] == [{"character_id": "char_x"}], "既有快照键必须保留"
    assert SETTLEMENT_SNAPSHOT_KEY in snapshot


def test_keep_mode_always_promotes_kept_entity(tmp_path):
    ids = _seed(tmp_path)
    settlement = _write_settlement(tmp_path)

    code = main([
        "--project", ids["project"], "--from-volume", "1", "--to-volume", "2",
        "--settlement", str(settlement), "--keep-name", PROTAGONIST,
        "--keep-mode", "always", "--apply", "--db", str(ids["db"]),
    ])

    assert code == 0
    assert _inject_modes(ids["db"], "characters")[ids["hero"]] == "always"


def test_skip_debts_leaves_ledger_untouched(tmp_path):
    ids = _seed(tmp_path)
    settlement = _write_settlement(tmp_path)

    code = main([
        "--project", ids["project"], "--from-volume", "1", "--to-volume", "2",
        "--settlement", str(settlement), "--keep-name", PROTAGONIST,
        "--skip-debts", "--apply", "--db", str(ids["db"]),
    ])

    assert code == 0
    conn = _conn(ids["db"])
    try:
        status = conn.execute("SELECT status FROM narrative_debts WHERE debt_id = ?",
                              (ids["debt_open"],)).fetchone()["status"]
    finally:
        conn.close()
    assert status == "open"


# ---------------------------------------------------------------------------
# 7. 装配口径（突变验证看守点）
# ---------------------------------------------------------------------------


def test_switched_world_injection_scope(tmp_path):
    """切换后为第二世界第 1 章装配：主角仍在注入面内，上一世实体退出注入面。

    **突变验证**：去掉 build_plan 的豁免分支（主角被一起归档）→ 本用例必红。
    """
    ids = _seed(tmp_path)
    settlement = _write_settlement(tmp_path)
    assert main([
        "--project", ids["project"], "--from-volume", "1", "--to-volume", "2",
        "--settlement", str(settlement), "--keep-name", PROTAGONIST, "--apply",
        "--db", str(ids["db"]),
    ]) == 0

    writer = build_writer_input(ids["db"], ids["ch25"], {}, namespace="world-switch-test")
    director = build_director_input(ids["db"], ids["project"], ids["ch25"], "",
                                    namespace="world-switch-test")

    # 主角：仍在真正进注入面的条目里（豁免生效）
    writer_chars = _excerpt_entries(writer, "character_state_excerpts")
    assert any(c.get("character_id") == ids["hero"] for c in writer_chars)
    assert any(c.get("character_id") == ids["hero"]
               for c in _excerpt_entries(director, "character_state_excerpts"))

    # 上一世实体：已退出注入面（不进 character_state_excerpts 条目 / locations / factions）
    for payload in (writer, director):
        chars = _excerpt_entries(payload, "character_state_excerpts")
        assert all(c.get("character_id") != ids["supporting"] for c in chars)
        world = payload.get("world_state_excerpts") or {}
        assert all(loc.get("location_id") != ids["location"]
                   for loc in _excerpt_entries(world, "locations"))
        assert all(fac.get("faction_id") != ids["faction"]
                   for fac in _excerpt_entries(world, "active_factions"))
        # 上一世 hooks 已终态化 → 不再进开放伏笔 / 台账
        assert all(h.get("hook_id") != ids["hook_open"]
                   for h in payload.get("hook_ledger_excerpt") or [])
        assert all(h.get("hook_id") != ids["hook_open"]
                   for h in payload.get("open_foreshadow_list") or [])

    # 未结债务已核销 → director 的债务台账里没有它
    assert all(d.get("debt_id") != ids["debt_open"]
               for d in director.get("narrative_debt_excerpt") or [])


def test_build_plan_reports_reasons_for_every_row(tmp_path):
    ids = _seed(tmp_path)
    settlement_path = _write_settlement(tmp_path)
    raw = load_settlement(settlement_path)
    issues: list = []
    plan = build_plan(
        db_path=ids["db"], project_id=ids["project"], from_volume=1, to_volume=2,
        settlement_payload=raw, settlement_body=raw["settlement"],
        keep_ids=[], keep_names=[], keep_mode="unchanged", include_debts=True,
        issues=issues,
    )

    # 每一行都有理由码 + 作用域标记（报告可审计）
    assert plan.rows, "计划不应为空"
    assert all(r.reason for r in plan.rows)
    kinds = {r.kind for r in plan.rows}
    assert kinds == {"character", "location", "faction", "hook", "debt"}
    # 无证据的实体在报告里被标出来（供抽查）
    assert any(r.extra.get("evidence_none") for r in plan.by_kind("location"))
    assert any(i.code == "EVIDENCE-NONE" for i in plan.issues)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
