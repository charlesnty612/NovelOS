"""Context Engine P2 补全单测。

覆盖 IMPLEMENTATION-PLAN §4.2 登记的三项缺口：
1. ``plot_graph_excerpt.unresolved_branches`` 从 branches 表实际查询 ACTIVE 分支；
2. ``world_state_excerpts.sensory_anchors`` 从 locations.data_json 解析（零 DDL 方案）；
3. 章节级相关性裁剪（relevance_trim）按 plan/scene 的 involved_characters / involved_locations
   过滤 writer payload，默认开启且支持环境变量/显式参数关闭。

不依赖真实 LLM / workflow，直连 in-memory DB。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from packages.core.context_engine.builders import (
    _apply_relevance_trim,
    _extract_involved_entities,
    _extract_sensory_anchors,
    _resolve_relevance_trim,
    build_director_input,
    build_writer_input,
)
from packages.core.context_engine.preview import preview_context
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path, name: str = "项目") -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO projects (project_id, name, premise, genre, target_words, status, created_at, updated_at)
            VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)
            """,
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(
    db_path: Path,
    project_id: str,
    number: int = 1,
    *,
    plan_json: dict | None = None,
) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json, status,
                 visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, '第一章', ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (
                cid,
                project_id,
                number,
                json.dumps(plan_json or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_branch(
    db_path: Path,
    project_id: str,
    branch_id: str,
    name: str,
    status: str = "ACTIVE",
    parent_branch_id: str | None = None,
) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO branches (branch_id, project_id, name, parent_branch_id, base_state_version, status, created_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (branch_id, project_id, name, parent_branch_id, status, now),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_character(
    db_path: Path,
    project_id: str,
    char_id: str,
    name: str,
    *,
    role: str = "supporting",
    inject_mode: str = "auto",
    aliases: list[str] | None = None,
) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO characters (character_id, project_id, name, role, core_json,
                                   visibility, aliases, inject_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, '{}', 'VISIBLE', ?, ?, ?, ?)
            """,
            (
                char_id,
                project_id,
                name,
                role,
                json.dumps(aliases or [], ensure_ascii=False),
                inject_mode,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_location(
    db_path: Path,
    project_id: str,
    loc_id: str,
    name: str,
    *,
    statement: str = "",
    data_json: dict | None = None,
    inject_mode: str = "auto",
) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO locations (location_id, project_id, name, statement, data_json,
                                  visibility, aliases, inject_mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'VISIBLE', '[]', ?, ?, ?)
            """,
            (
                loc_id,
                project_id,
                name,
                statement,
                json.dumps(data_json or {}, ensure_ascii=False),
                inject_mode,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. unresolved_branches
# ---------------------------------------------------------------------------


def test_plot_graph_excerpt_lists_active_branches_as_unresolved(tmp_path: Path):
    """ACTIVE 分支应出现在 unresolved_branches；MERGED/DISCARDED/ARCHIVED 不应出现。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    _insert_branch(db_path, pid, "br_active", "主探分支", status="ACTIVE")
    _insert_branch(db_path, pid, "br_merged", "已合并分支", status="MERGED")
    _insert_branch(db_path, pid, "br_discarded", "已废弃分支", status="DISCARDED")
    _insert_branch(db_path, pid, "br_archived", "已归档分支", status="ARCHIVED")

    out = build_director_input(db_path, pid, cid, "意图")
    plot = out["plot_graph_excerpt"]
    assert isinstance(plot, dict)
    branches = plot.get("unresolved_branches") or []
    ids = {b["branch_id"] for b in branches}

    assert "br_active" in ids, "ACTIVE 分支应被视为未解决"
    assert "br_merged" not in ids, "MERGED 分支不应出现"
    assert "br_discarded" not in ids, "DISCARDED 分支不应出现"
    assert "br_archived" not in ids, "ARCHIVED 分支不应出现"

    active = next(b for b in branches if b["branch_id"] == "br_active")
    assert active["name"] == "主探分支"
    assert active["status"] == "ACTIVE"
    assert "base_state_version" in active


def test_preview_shows_unresolved_branches(tmp_path: Path):
    """preview_context 在 L1 展示未解决分支条目。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_branch(db_path, pid, "br_preview", "预览分支", status="ACTIVE")

    out = preview_context(str(db_path), pid, cid)
    l1_items = out["layers"][1]["items"]
    branch_items = [i for i in l1_items if i["kind"] == "unresolved_branch"]
    assert any(i["id"] == "br_preview" for i in branch_items)


# ---------------------------------------------------------------------------
# 2. sensory_anchors
# ---------------------------------------------------------------------------


def test_world_state_excerpts_extracts_sensory_anchors_from_data_json(tmp_path: Path):
    """locations.data_json 中 sensory_anchors 列表应被解析到 world_state_excerpts。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    _insert_location(
        db_path,
        pid,
        "loc_temple",
        "古庙",
        data_json={
            "sensory_anchors": [
                {"sense": "smell", "anchor_text": "腐朽的檀香"},
                {"sense": "sound", "anchor_text": "水滴落在石板上的回声"},
            ]
        },
    )
    _insert_location(
        db_path,
        pid,
        "loc_village",
        "村庄",
        statement="清晨炊烟袅袅",
        data_json={"atmosphere": "宁静而忙碌", "smell": "柴火与粥香"},
    )

    # 显式关闭 relevance_trim，避免无关实体被降级导致 data_json 丢失。
    out = build_writer_input(db_path, cid, {}, relevance_trim=False)
    world = out["world_state_excerpts"]
    anchors = world.get("sensory_anchors") or []

    assert len(anchors) >= 2
    temple_anchors = [a for a in anchors if a.get("location_id") == "loc_temple"]
    assert len(temple_anchors) == 2
    assert any(a.get("sense") == "smell" and a.get("anchor_text") == "腐朽的檀香" for a in temple_anchors)

    village_anchors = [a for a in anchors if a.get("location_id") == "loc_village"]
    assert village_anchors, "无 sensory_anchors 字段时应从 atmosphere/smell 等字段组合"
    assert any("粥香" in (a.get("anchor_text") or "") for a in village_anchors)


def test_extract_sensory_anchors_is_pure_function():
    """_extract_sensory_anchors 是纯函数，非法输入返回空 list。"""
    locs = [
        {
            "location_id": "loc_a",
            "name": "A",
            "data_json": {"sensory_details": "潮湿、阴暗"},
        },
        {
            "location_id": "loc_b",
            "name": "B",
            "statement": "一句话陈述",
            "data_json": {},
        },
        "not a dict",
    ]
    out = _extract_sensory_anchors(locs)
    assert len(out) == 2
    assert out[0]["location_id"] == "loc_a"
    assert "潮湿、阴暗" in out[0]["anchor_text"]
    assert out[1]["location_id"] == "loc_b"


def test_preview_shows_sensory_anchors(tmp_path: Path):
    """preview_context 在 L1 展示 sensory_anchor 条目。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_location(
        db_path,
        pid,
        "loc_preview",
        "预览地点",
        data_json={"sensory_anchors": [{"sense": "light", "anchor_text": "昏黄的油灯"}]},
    )

    out = preview_context(str(db_path), pid, cid)
    l1_items = out["layers"][1]["items"]
    anchor_items = [i for i in l1_items if i["kind"] == "sensory_anchor"]
    assert any("油灯" in (i.get("anchor_text") or "") for i in anchor_items)


# ---------------------------------------------------------------------------
# 3. relevance trim（纯函数 + 端到端）
# ---------------------------------------------------------------------------


def test_resolve_relevance_trim_env_off():
    """环境变量 NOVELOS_CONTEXT_RELEVANCE=off 时关闭；其他值开启。"""
    prev = os.environ.get("NOVELOS_CONTEXT_RELEVANCE")
    try:
        os.environ["NOVELOS_CONTEXT_RELEVANCE"] = "off"
        assert _resolve_relevance_trim(None) is False
        os.environ["NOVELOS_CONTEXT_RELEVANCE"] = "OFF"
        assert _resolve_relevance_trim(None) is False
        os.environ["NOVELOS_CONTEXT_RELEVANCE"] = "on"
        assert _resolve_relevance_trim(None) is True
        os.environ.pop("NOVELOS_CONTEXT_RELEVANCE", None)
        assert _resolve_relevance_trim(None) is True
        # 显式传值优先于环境变量
        os.environ["NOVELOS_CONTEXT_RELEVANCE"] = "off"
        assert _resolve_relevance_trim(True) is True
    finally:
        if prev is None:
            os.environ.pop("NOVELOS_CONTEXT_RELEVANCE", None)
        else:
            os.environ["NOVELOS_CONTEXT_RELEVANCE"] = prev


def test_extract_involved_entities_reads_plan_and_scene():
    """_extract_involved_entities 从 plan_json / scene_plan 正确提取涉及实体。"""
    plan = {
        "key_beats": [
            {
                "beat_id": "b1",
                "involved_characters": ["char_alice", "Bob"],
                "involved_locations": ["loc_temple"],
            }
        ],
        "character_changes_planned": [
            {"name": "Alice", "from": "怯懦", "to": "果敢"},
            {"character_id": "char_bob"},
        ],
    }
    scene = {
        "characters": ["char_alice", "Charlie"],
        "location": "loc_village",
        "beats": [
            {"involved_characters": ["char_dave"], "involved_locations": ["loc_river"]}
        ],
    }
    chars, locs = _extract_involved_entities(plan, scene)
    assert "char_alice" in chars
    assert "Bob" in chars
    assert "Alice" in chars
    assert "char_bob" in chars
    assert "Charlie" in chars
    assert "char_dave" in chars
    assert "loc_temple" in locs
    assert "loc_village" in locs
    assert "loc_river" in locs


def test_apply_relevance_trim_keeps_protagonist_and_always():
    """主角与 inject_mode='always' 的实体在裁剪后仍保留完整。"""
    payload = {
        "character_state_excerpts": [
            {"character_id": "char_prot", "name": "主角", "role": "protagonist", "core": "大量设定"},
            {"character_id": "char_always", "name": "常驻", "inject_mode": "always", "core": "大量设定"},
            {"character_id": "char_extra", "name": "路人甲", "role": "supporting", "core": "少量设定"},
        ],
        "world_state_excerpts": {
            "locations": [
                {"location_id": "loc_always", "name": "常驻地点", "inject_mode": "always", "data": "详细"},
                {"location_id": "loc_other", "name": "无关地点", "data": "详细"},
            ],
            "active_factions": [
                {"faction_id": "fac_always", "name": "常驻势力", "inject_mode": "always"},
                {"faction_id": "fac_other", "name": "无关势力"},
            ],
        },
    }
    _apply_relevance_trim(
        payload,
        relevance_trim=True,
        plan_json={},
        scene_plan={},
    )
    chars = payload["character_state_excerpts"]
    assert chars[0].get("core") == "大量设定"
    assert chars[1].get("core") == "大量设定"
    assert chars[2].get("relevance_summary") is True
    assert "core" not in chars[2]

    locs = payload["world_state_excerpts"]["locations"]
    assert locs[0].get("data") == "详细"
    assert locs[1].get("relevance_summary") is True

    facs = payload["world_state_excerpts"]["active_factions"]
    assert facs[0].get("faction_id") == "fac_always"
    assert facs[1].get("relevance_summary") is True


def test_apply_relevance_trim_keeps_involved_entities_full():
    """involved_characters / involved_locations 命中时保留完整。"""
    payload = {
        "character_state_excerpts": [
            {"character_id": "char_alice", "name": "Alice", "role": "supporting", "core": "设定"},
            {"character_id": "char_bob", "name": "Bob", "role": "supporting", "core": "设定"},
        ],
        "world_state_excerpts": {
            "locations": [
                {"location_id": "loc_temple", "name": "古庙", "data": "详细"},
                {"location_id": "loc_village", "name": "村庄", "data": "详细"},
            ],
            "active_factions": [],
        },
    }
    _apply_relevance_trim(
        payload,
        relevance_trim=True,
        plan_json={
            "key_beats": [
                {"involved_characters": ["char_alice"], "involved_locations": ["loc_temple"]}
            ]
        },
        scene_plan={},
    )
    alice = next(c for c in payload["character_state_excerpts"] if c["character_id"] == "char_alice")
    bob = next(c for c in payload["character_state_excerpts"] if c["character_id"] == "char_bob")
    assert alice.get("core") == "设定"
    assert bob.get("relevance_summary") is True

    temple = next(e for e in payload["world_state_excerpts"]["locations"] if e["location_id"] == "loc_temple")
    village = next(e for e in payload["world_state_excerpts"]["locations"] if e["location_id"] == "loc_village")
    assert temple.get("data") == "详细"
    assert village.get("relevance_summary") is True


def test_apply_relevance_trim_disabled_is_noop():
    """relevance_trim=False 时不修改 payload（仍写入 enabled 标记）。"""
    payload = {
        "character_state_excerpts": [
            {"character_id": "char_x", "name": "X", "core": "设定"},
        ],
        "world_state_excerpts": {"locations": [], "active_factions": []},
    }
    _apply_relevance_trim(
        payload,
        relevance_trim=False,
        plan_json={},
        scene_plan={},
    )
    assert payload["_relevance_trim_enabled"] is False
    assert "_relevance_trim_stats" not in payload
    assert payload["character_state_excerpts"][0].get("core") == "设定"


def test_writer_input_relevance_trim_end_to_end(tmp_path: Path):
    """build_writer_input 默认开启 relevance_trim，未涉及角色降级为仅 id/name。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    plan = {
        "key_beats": [
            {"involved_characters": ["char_alice"], "involved_locations": ["loc_temple"]}
        ]
    }
    cid = _insert_chapter(db_path, pid, plan_json=plan)

    _insert_character(db_path, pid, "char_alice", "Alice")
    _insert_character(db_path, pid, "char_bob", "Bob")
    _insert_location(db_path, pid, "loc_temple", "古庙")
    _insert_location(db_path, pid, "loc_village", "村庄")

    # 显式传 True，避免其他测试模块设置的环境变量污染本测试。
    out = build_writer_input(db_path, cid, {}, relevance_trim=True)
    assert out["_relevance_trim_enabled"] is True
    stats = out["_relevance_trim_stats"]
    assert stats["characters_full"] == 1
    assert stats["characters_summary"] == 1
    assert stats["locations_full"] == 1
    assert stats["locations_summary"] == 1

    alice = next(c for c in out["character_state_excerpts"] if c["character_id"] == "char_alice")
    bob = next(c for c in out["character_state_excerpts"] if c["character_id"] == "char_bob")
    assert "name" in alice and "core_json" in alice
    assert bob.get("relevance_summary") is True
    assert "core_json" not in bob


def test_writer_input_relevance_trim_can_be_disabled_by_param(tmp_path: Path):
    """relevance_trim=False 显式关闭裁剪。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character(db_path, pid, "char_alice", "Alice")
    _insert_character(db_path, pid, "char_bob", "Bob")

    out = build_writer_input(db_path, cid, {}, relevance_trim=False)
    assert out["_relevance_trim_enabled"] is False
    for c in out["character_state_excerpts"]:
        assert c.get("relevance_summary") is None


def test_writer_input_relevance_trim_env_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """环境变量 NOVELOS_CONTEXT_RELEVANCE=off 关闭默认裁剪。"""
    monkeypatch.setenv("NOVELOS_CONTEXT_RELEVANCE", "off")
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character(db_path, pid, "char_alice", "Alice")
    _insert_character(db_path, pid, "char_bob", "Bob")

    out = build_writer_input(db_path, cid, {})
    assert out["_relevance_trim_enabled"] is False
