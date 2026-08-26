"""RevealPolicyService CRUD 单测（V3.3 P0-2 知识权限补全）。

覆盖：
- create：完整 happy path + 默认值 + 时间戳；
- create 校验：非法 target_kind / status / 缺 revealed_chapter / 实体不存在 /
  project 不存在 → ValidationError；
- list：按 status 过滤 + project 不存在 → ValidationError + 默认排序；
- get：存在 / 不存在；
- update：仅 status / revealed_chapter / notes 三字段；空 fields → 不写盘；
- update 校验：status='revealed' 必带 revealed_chapter（无则复用现有，缺则 422）；
- delete：存在 / 不存在 → NotFoundError；
- 校验枚举对齐 0014 DDL CHECK；
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.core.db import apply_migrations, get_connection  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402
from packages.domain.knowledge import (  # noqa: E402
    NotFoundError,
    RevealPolicyService,
    ValidationError,
)

MIGRATIONS_DIR = ROOT / "database" / "migrations"


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _make_project(db_path: Path, name: str = "rp-test") -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _make_character(db_path: Path, project_id: str, char_id: str) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO characters (character_id, project_id, name, role, core_json,
                                    visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, 'x', 'supporting', '{}', 'HIDDEN', NULL, ?, ?)
            """,
            (char_id, project_id, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _make_location(db_path: Path, project_id: str, loc_id: str) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO locations (location_id, project_id, name, statement, data_json,
                                   visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, 'x', '', '{}', 'HIDDEN', NULL, ?, ?)
            """,
            (loc_id, project_id, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _make_event(db_path: Path, project_id: str, event_id: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO plot_events
                (event_id, project_id, type, cause_json, effects_json,
                 participants_json, location_id, time_json, status,
                 introduced_chapter_id, visibility, who_knows)
            VALUES (?, ?, 'other', '[]', '[]', '[]', NULL,
                    '{"timeline_day":1}', 'planned', NULL, 'HIDDEN', NULL)
            """,
            (event_id, project_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_happy_path_defaults(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_alice")

    svc = RevealPolicyService(db_path)
    pol = svc.create(
        project_id=pid,
        target_kind="character",
        target_id="char_alice",
    )
    assert pol.policy_id.startswith("rp_")
    assert pol.project_id == pid
    assert pol.target_kind == "character"
    assert pol.target_id == "char_alice"
    # defaults
    assert pol.audience == "reader"
    assert pol.status == "planned"
    assert pol.reveal_by_chapter is None
    assert pol.revealed_chapter is None
    assert pol.notes is None
    assert pol.created_at and pol.updated_at
    # 数据库写入校验
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM reveal_policies WHERE policy_id = ?", (pol.policy_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None


def test_create_with_all_fields(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    _make_event(db_path, pid, "event_1")

    svc = RevealPolicyService(db_path)
    pol = svc.create(
        project_id=pid,
        target_kind="event",
        target_id="event_1",
        reveal_by_chapter=10,
        audience="reader",
        status="revealed",
        revealed_chapter=8,
        notes="伏笔于第 8 章揭晓",
    )
    assert pol.reveal_by_chapter == 10
    assert pol.status == "revealed"
    assert pol.revealed_chapter == 8
    assert pol.notes == "伏笔于第 8 章揭晓"


def test_create_invalid_target_kind_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = RevealPolicyService(db_path)
    with pytest.raises(ValidationError, match="target_kind"):
        svc.create(
            project_id=pid,
            target_kind="bogus",
            target_id="x",
        )


def test_create_invalid_status_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_a")
    svc = RevealPolicyService(db_path)
    with pytest.raises(ValidationError, match="status"):
        svc.create(
            project_id=pid,
            target_kind="character",
            target_id="char_a",
            status="bogus",
        )


def test_create_status_revealed_requires_revealed_chapter(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_a")
    svc = RevealPolicyService(db_path)
    with pytest.raises(ValidationError, match="revealed_chapter"):
        svc.create(
            project_id=pid,
            target_kind="character",
            target_id="char_a",
            status="revealed",
        )


def test_create_target_not_exists_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = RevealPolicyService(db_path)
    with pytest.raises(ValidationError, match="不存在"):
        svc.create(
            project_id=pid,
            target_kind="character",
            target_id="char_nonexistent",
        )


def test_create_project_not_exists_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    svc = RevealPolicyService(db_path)
    with pytest.raises(ValidationError, match="project"):
        svc.create(
            project_id="prj_nope",
            target_kind="character",
            target_id="char_x",
        )


def test_create_invalid_audience_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_a")
    svc = RevealPolicyService(db_path)
    with pytest.raises(ValidationError, match="audience"):
        svc.create(
            project_id=pid,
            target_kind="character",
            target_id="char_a",
            audience="",
        )


def test_create_negative_reveal_by_chapter_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_a")
    svc = RevealPolicyService(db_path)
    with pytest.raises(ValidationError, match="reveal_by_chapter"):
        svc.create(
            project_id=pid,
            target_kind="character",
            target_id="char_a",
            reveal_by_chapter=0,
        )


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_empty(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = RevealPolicyService(db_path)
    assert svc.list(pid) == []


def test_list_with_status_filter(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_a")
    _make_character(db_path, pid, "char_b")
    _make_event(db_path, pid, "event_1")
    svc = RevealPolicyService(db_path)
    svc.create(project_id=pid, target_kind="character", target_id="char_a")
    p2 = svc.create(
        project_id=pid, target_kind="character", target_id="char_b",
        status="revealed", revealed_chapter=3,
    )
    svc.create(project_id=pid, target_kind="event", target_id="event_1")
    all_items = svc.list(pid)
    assert len(all_items) == 3
    planned = svc.list(pid, status="planned")
    assert len(planned) == 2
    revealed = svc.list(pid, status="revealed")
    assert len(revealed) == 1
    assert revealed[0].policy_id == p2.policy_id


def test_list_invalid_status_filter_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = RevealPolicyService(db_path)
    with pytest.raises(ValidationError, match="status"):
        svc.list(pid, status="bogus")


def test_list_project_not_exists_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    svc = RevealPolicyService(db_path)
    with pytest.raises(ValidationError, match="project"):
        svc.list("prj_nope")


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_existing(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_a")
    svc = RevealPolicyService(db_path)
    pol = svc.create(project_id=pid, target_kind="character", target_id="char_a")
    fetched = svc.get(pol.policy_id)
    assert fetched is not None
    assert fetched.policy_id == pol.policy_id


def test_get_nonexistent_returns_none(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    svc = RevealPolicyService(db_path)
    assert svc.get("rp_nope") is None


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_status_to_revealed_requires_chapter(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_a")
    svc = RevealPolicyService(db_path)
    pol = svc.create(project_id=pid, target_kind="character", target_id="char_a")
    # 当前 status=planned，无 revealed_chapter；改 status=revealed → 422
    with pytest.raises(ValidationError, match="revealed_chapter"):
        svc.update(pol.policy_id, status="revealed")


def test_update_status_to_revealed_with_chapter_succeeds(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_a")
    svc = RevealPolicyService(db_path)
    pol = svc.create(project_id=pid, target_kind="character", target_id="char_a")
    upd = svc.update(pol.policy_id, status="revealed", revealed_chapter=5)
    assert upd.status == "revealed"
    assert upd.revealed_chapter == 5
    # updated_at 应推进
    assert upd.updated_at >= pol.updated_at


def test_update_notes_only(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_a")
    svc = RevealPolicyService(db_path)
    pol = svc.create(project_id=pid, target_kind="character", target_id="char_a")
    upd = svc.update(pol.policy_id, notes="新备注")
    assert upd.notes == "新备注"
    assert upd.status == "planned"  # 其它字段不变


def test_update_empty_fields_noop(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_a")
    svc = RevealPolicyService(db_path)
    pol = svc.create(project_id=pid, target_kind="character", target_id="char_a")
    upd = svc.update(pol.policy_id)
    # updated_at 不变（未写盘）
    assert upd.updated_at == pol.updated_at


def test_update_nonexistent_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    svc = RevealPolicyService(db_path)
    with pytest.raises(NotFoundError):
        svc.update("rp_nope", status="cancelled")


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_happy_path(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_a")
    svc = RevealPolicyService(db_path)
    pol = svc.create(project_id=pid, target_kind="character", target_id="char_a")
    svc.delete(pol.policy_id)
    assert svc.get(pol.policy_id) is None
    assert svc.list(pid) == []


def test_delete_nonexistent_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    svc = RevealPolicyService(db_path)
    with pytest.raises(NotFoundError):
        svc.delete("rp_nope")


def test_target_kind_routing_for_all_eight_kinds(tmp_path: Path):
    """验证 8 种 target_kind 都能走通引用校验（占位实体分别插入对应表）。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    # 先建 characters（relationship FK 依赖）
    _make_character(db_path, pid, "char_a")
    _make_character(db_path, pid, "char_b")
    _make_location(db_path, pid, "loc_x")
    _make_event(db_path, pid, "event_x")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO factions (faction_id, project_id, name, statement, "
            "data_json, visibility, created_at, updated_at) "
            "VALUES ('fac_x', ?, 'fx', '', '{}', 'HIDDEN', ?, ?)",
            (pid, now, now),
        )
        conn.execute(
            "INSERT INTO world_rules (world_rule_id, project_id, name, statement, "
            "data_json, visibility, created_at, updated_at) "
            "VALUES ('wrule_x', ?, 'r', '', '{}', 'HIDDEN', ?, ?)",
            (pid, now, now),
        )
        conn.execute(
            "INSERT INTO hooks (hook_id, project_id, name, status, importance, "
            "visibility, created_at, updated_at) "
            "VALUES ('hook_x', ?, 'h', 'OPEN', 0.5, 'HIDDEN', ?, ?)",
            (pid, now, now),
        )
        conn.execute(
            "INSERT INTO narrative_debts (debt_id, project_id, description, "
            "severity, status, visibility, created_at, updated_at) "
            "VALUES ('debt_x', ?, 'd', 0.5, 'open', 'HIDDEN', ?, ?)",
            (pid, now, now),
        )
        conn.execute(
            """
            INSERT INTO relationships
                (relationship_id, project_id, from_character_id, to_character_id,
                 relation_type, state_json, last_state_version, visibility, who_knows)
            VALUES ('rel_x', ?, 'char_a', 'char_b', 'ally', '{}', 0, 'HIDDEN', NULL)
            """,
            (pid,),
        )
        conn.commit()
    finally:
        conn.close()

    svc = RevealPolicyService(db_path)
    created = []
    for kind, tid in (
        ("character", "char_a"),
        ("location", "loc_x"),
        ("faction", "fac_x"),
        ("world_rule", "wrule_x"),
        ("event", "event_x"),
        ("hook", "hook_x"),
        ("debt", "debt_x"),
        ("relationship", "rel_x"),
    ):
        p = svc.create(project_id=pid, target_kind=kind, target_id=tid)
        created.append(p)
    assert len(svc.list(pid)) == 8
