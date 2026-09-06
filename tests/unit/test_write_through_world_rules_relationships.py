"""write_through.world_rules / relationships 分支白盒测试。

覆盖目标：``packages/core/story_state/write_through.py`` 的两个长期欠测分支：
  - world_changes.kind="rule"（行 463-505）：add / update / remove；
    含「目标行不存在 + op=update/remove」silent skip 边界。
  - relationship_changes（行 507-566）：add / update / remove；
    含 0017 唯一索引 ``idx_relationships_unique`` 在 IntegrityError 时
    转 UPDATE 幂等分支（占行 534-549）。
  - 组合断言：``delta_repair.repair_delta`` 产出的 repaired delta 直接喂
    ``write_through`` 不抛异常且落库正确。

风格对齐：仿 ``tests/unit/test_write_through_hooks.py`` 的最小 SQLite fixture
（不开 FK） + 显式 seed + 直接调 ``write_through``。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from packages.core.story_state.delta_repair import repair_delta
from packages.core.story_state.write_through import write_through

# ---------------------------------------------------------------------------
# DDL（最小化：匹配 0001_init.sql + 0017 唯一索引的关键列）
# ---------------------------------------------------------------------------

DDL_PROJECTS = """
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

DDL_CHAPTERS = """
CREATE TABLE chapters (
    chapter_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    number     INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'PLANNED',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
)
"""

DDL_CHARACTERS = """
CREATE TABLE characters (
    character_id TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL,
    name         TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'supporting',
    core_json    TEXT NOT NULL DEFAULT '{}',
    visibility   TEXT NOT NULL DEFAULT 'PUBLIC',
    who_knows    TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
)
"""

DDL_CHARACTER_STATES = """
CREATE TABLE character_states (
    character_id  TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    state_json    TEXT NOT NULL DEFAULT '{}',
    visibility    TEXT NOT NULL DEFAULT 'VISIBLE',
    who_knows     TEXT,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (character_id, state_version),
    FOREIGN KEY (character_id) REFERENCES characters(character_id)
)
"""

# delta_repair 全表扫描需要的旁路表——空表即可（只要求存在）
DDL_LOCATIONS = """
CREATE TABLE locations (
    location_id TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    statement   TEXT NOT NULL DEFAULT '',
    data_json   TEXT NOT NULL DEFAULT '{}',
    visibility  TEXT NOT NULL DEFAULT 'PUBLIC',
    who_knows   TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
)
"""

DDL_FACTIONS = """
CREATE TABLE factions (
    faction_id  TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    statement   TEXT NOT NULL DEFAULT '',
    data_json   TEXT NOT NULL DEFAULT '{}',
    visibility  TEXT NOT NULL DEFAULT 'VISIBLE',
    who_knows   TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
)
"""

DDL_NARRATIVE_DEBTS = """
CREATE TABLE narrative_debts (
    debt_id              TEXT PRIMARY KEY,
    project_id           TEXT NOT NULL,
    description          TEXT NOT NULL DEFAULT '',
    created_chapter_id   TEXT,
    severity             REAL NOT NULL DEFAULT 0.5,
    deadline_chapter_id  TEXT,
    status               TEXT NOT NULL DEFAULT 'open',
    visibility           TEXT NOT NULL DEFAULT 'RESTRICTED',
    who_knows            TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
)
"""

DDL_HOOKS = """
CREATE TABLE hooks (
    hook_id                     TEXT PRIMARY KEY,
    project_id                  TEXT NOT NULL,
    name                        TEXT NOT NULL,
    introduced_chapter_id       TEXT,
    status                      TEXT NOT NULL DEFAULT 'OPEN',
    importance                  REAL NOT NULL DEFAULT 0.5,
    expected_payoff_chapter_id  TEXT,
    payoff_chapter_id           TEXT,
    visibility                  TEXT NOT NULL DEFAULT 'RESTRICTED',
    who_knows                   TEXT,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
)
"""

DDL_PLOT_EVENTS = """
CREATE TABLE plot_events (
    event_id              TEXT PRIMARY KEY,
    project_id            TEXT NOT NULL,
    type                  TEXT NOT NULL,
    cause_json            TEXT NOT NULL DEFAULT '[]',
    effects_json          TEXT NOT NULL DEFAULT '[]',
    participants_json     TEXT NOT NULL DEFAULT '[]',
    location_id           TEXT,
    time_json             TEXT NOT NULL DEFAULT '{"timeline_day":1}',
    status                TEXT NOT NULL DEFAULT 'planned',
    introduced_chapter_id TEXT,
    visibility            TEXT NOT NULL DEFAULT 'RESTRICTED',
    who_knows             TEXT,
    description           TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
)
"""

DDL_WORLD_RULES = """
CREATE TABLE world_rules (
    world_rule_id TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    name          TEXT NOT NULL,
    statement     TEXT NOT NULL DEFAULT '',
    data_json     TEXT NOT NULL DEFAULT '{}',
    visibility    TEXT NOT NULL DEFAULT 'PUBLIC',
    who_knows     TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
)
"""

DDL_RELATIONSHIPS = """
CREATE TABLE relationships (
    relationship_id     TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    from_character_id   TEXT NOT NULL,
    to_character_id     TEXT NOT NULL,
    relation_type       TEXT NOT NULL,
    state_json          TEXT NOT NULL DEFAULT '{}',
    last_state_version  INTEGER NOT NULL,
    visibility          TEXT NOT NULL DEFAULT 'PUBLIC',
    who_knows           TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(project_id),
    FOREIGN KEY (from_character_id) REFERENCES characters(character_id),
    FOREIGN KEY (to_character_id) REFERENCES characters(character_id)
)
"""

# 0017：迁移引入的唯一索引（与 write_through 的 IntegrityError 兜底分支对应）
DDL_IDX_RELATIONSHIPS_UNIQUE = """
CREATE UNIQUE INDEX idx_relationships_unique
    ON relationships(project_id, from_character_id, to_character_id, relation_type)
"""


# ---------------------------------------------------------------------------
# Stub helpers：用于 0017 IntegrityError 兜底分支复现
# ---------------------------------------------------------------------------


class _EmptyCursor:
    """sqlite3.Cursor 替身：fetchone() 返回 None，fetchall() 返回 []。"""

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def close(self):
        return None


class _StubCursor:
    """fetchone() 返回预置 first_row（用于「再查现有行」分支）。"""

    def __init__(self, first_row: sqlite3.Row | None) -> None:
        self._first = first_row

    def fetchone(self):
        return self._first

    def fetchall(self):
        return [self._first] if self._first is not None else []

    def close(self):
        return None


class _StubConn(sqlite3.Connection):
    """sqlite3.Connection 子类：在该实例上绑定 .execute 是可写的（Python attr）。

    sqlite3.Connection.execute 是 C 槽位只读（setattr 报
    ``AttributeError: attribute 'execute' of 'sqlite3.Connection' objects
    is not writable``）。但**子类实例**上的同名 attr 可写，且会被
    ``conn.execute(...)`` 调用查找优先命中实例 attr。这是本测试得以
    monkeypatch ``.execute`` 的关键。

    本类没有额外字段；唯一目的是让 ``sqlite3.connect(..., factory=_StubConn)``
    拿到的对象能 ``stub.execute = stubbed``。
    """

    pass


def _make_stub_conn_for_relationships(
    tmp_path: Path,
) -> tuple[_StubConn, sqlite3.Connection, sqlite3.Row]:
    """为 0017 兜底测试新建 _StubConn 与 base conn（同 db 文件）。
    返回 ``(stub, base, pre_occ_row)``。
      - stub：被绑 execute stubbed，覆盖「SELECT 返回 None + INSERT 抛 IntegrityError」
      - base：未 stub 的参照连接，断言阶段用于读真实落库结果
      - pre_occ_row：预占位行的 relationship_id，供 stub 在二次查询时返回
    """
    db_file = tmp_path / "novelos_stub.db"
    # base 用普通连接打开并 seed（含预占位）
    base = sqlite3.connect(str(db_file))
    base.row_factory = sqlite3.Row
    for ddl in (
        DDL_PROJECTS,
        DDL_CHAPTERS,
        DDL_CHARACTERS,
        DDL_CHARACTER_STATES,
        DDL_LOCATIONS,
        DDL_FACTIONS,
        DDL_WORLD_RULES,
        DDL_RELATIONSHIPS,
        DDL_NARRATIVE_DEBTS,
        DDL_HOOKS,
        DDL_PLOT_EVENTS,
        DDL_IDX_RELATIONSHIPS_UNIQUE,
    ):
        base.execute(ddl)
    base.execute(
        "INSERT INTO projects (project_id, name, created_at, updated_at) "
        "VALUES ('prj_test', 'Test', '2026-01-01', '2026-01-01')"
    )
    base.execute(
        "INSERT INTO chapters (chapter_id, project_id, number, created_at, updated_at) "
        "VALUES ('ch_test', 'prj_test', 1, '2026-01-01', '2026-01-01')"
    )
    base.execute(
        "INSERT INTO characters (character_id, project_id, name, created_at, updated_at) "
        "VALUES ('char_a', 'prj_test', 'a', '2026-01-01', '2026-01-01')"
    )
    base.execute(
        "INSERT INTO characters (character_id, project_id, name, created_at, updated_at) "
        "VALUES ('char_b', 'prj_test', 'b', '2026-01-01', '2026-01-01')"
    )
    base.execute(
        """
        INSERT INTO relationships
            (relationship_id, project_id, from_character_id, to_character_id,
             relation_type, state_json, last_state_version)
        VALUES ('rel_pre_occ', 'prj_test', 'char_a', 'char_b', 'ally',
                '{"intensity": 1}', 1)
        """
    )
    base.commit()

    pre_occ_row = base.execute(
        "SELECT relationship_id FROM relationships WHERE relationship_id = 'rel_pre_occ'"
    ).fetchone()

    # stub 用子类连接打开同一文件（_StubConn 仅是工厂标记，使实例 attr 可写）
    stub: _StubConn = sqlite3.connect(str(db_file), factory=_StubConn)  # type: ignore[assignment]
    stub.row_factory = sqlite3.Row
    return stub, base, pre_occ_row


# ---------------------------------------------------------------------------
# 主 fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_conn(tmp_path: Path):
    """最小化 SQLite 连接，PRAGMA FK 关闭，0017 唯一索引创建但不强制 FK。"""
    db_file = tmp_path / "novelos.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    for ddl in (
        DDL_PROJECTS,
        DDL_CHAPTERS,
        DDL_CHARACTERS,
        DDL_CHARACTER_STATES,
        DDL_LOCATIONS,
        DDL_FACTIONS,
        DDL_WORLD_RULES,
        DDL_RELATIONSHIPS,
        DDL_NARRATIVE_DEBTS,
        DDL_HOOKS,
        DDL_PLOT_EVENTS,
        DDL_IDX_RELATIONSHIPS_UNIQUE,
    ):
        conn.execute(ddl)
    conn.execute(
        "INSERT INTO projects (project_id, name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        ("prj_test", "Test", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO chapters (chapter_id, project_id, number, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("ch_test", "prj_test", 1, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def _insert_world_rule(
    conn: sqlite3.Connection,
    world_rule_id: str,
    *,
    data_json: dict | None = None,
    who_knows: list | None = None,
    visibility: str = "PUBLIC",
) -> None:
    conn.execute(
        """
        INSERT INTO world_rules
            (world_rule_id, project_id, name, statement, data_json,
             visibility, who_knows, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            world_rule_id,
            "prj_test",
            f"rule-{world_rule_id}",
            "",
            json.dumps(data_json if data_json is not None else {}, ensure_ascii=False),
            visibility,
            json.dumps(who_knows, ensure_ascii=False) if who_knows is not None else None,
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
    )
    conn.commit()


def _insert_char(conn: sqlite3.Connection, character_id: str) -> None:
    conn.execute(
        """
        INSERT INTO characters
            (character_id, project_id, name, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (character_id, "prj_test", f"char-{character_id}",
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    conn.commit()


def _base_delta(
    *,
    world_changes: list | None = None,
    relationship_changes: list | None = None,
) -> dict:
    return {
        "delta_id": "dlt_test",
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": "ch_test",
        "workflow_run_id": "run_test",
        "previous_state_version": 1,
        "created_by": "observer:v1",
        "created_at": "2026-01-01T00:00:00Z",
        "supersedes": None,
        "notes": None,
        "character_changes": [],
        "world_changes": world_changes or [],
        "relationship_changes": relationship_changes or [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }


# ---------------------------------------------------------------------------
# 1) world_rules 分支（行 463-502）：add/update/remove + 边界
# ---------------------------------------------------------------------------


def test_world_rule_add_inserts_row(db_conn: sqlite3.Connection) -> None:
    """world_kind=rule + op=add + 目标不存在 → INSERT 新行；
    write_through 行 467-481 从 ``after.data_json`` 取整段写入 ``data_json``；
    name/statement 也从 after 取；visibility=None 沿用 PUBLIC。
    """
    delta = _base_delta(
        world_changes=[
            {
                "change_id": "wc_add",
                "op": "add",
                "target_id": "wrule_new",
                "world_id": "wrule_new",
                "world_kind": "rule",
                "field": "scope",
                "before": None,
                "after": {
                    "name": "魔法守恒",
                    "statement": "能量守恒律",
                    "data_json": {"scope": "magic", "limit": 3},
                },
                "who_knows": None,
                "visibility": None,
            }
        ]
    )

    write_through(db_conn, "prj_test", delta, new_version=2)

    row = db_conn.execute(
        "SELECT name, statement, data_json, visibility, who_knows "
        "FROM world_rules WHERE world_rule_id = ?",
        ("wrule_new",),
    ).fetchone()
    assert row is not None, "rule add 后应有新行"
    assert row["name"] == "魔法守恒", "name 应取 after.name"
    assert row["statement"] == "能量守恒律", "statement 应取 after.statement"
    data = json.loads(row["data_json"])
    assert data == {"scope": "magic", "limit": 3}, f"add 应整段写入 data_json，实际={data}"
    assert row["visibility"] == "PUBLIC", "visibility=None 沿用 PUBLIC"


def test_world_rule_update_merges_into_data_json(db_conn: sqlite3.Connection) -> None:
    """world_kind=rule + op=update + 目标存在 → 顶层 field key 写进 data_json。"""
    _insert_world_rule(
        db_conn, "wrule_existing", data_json={"scope": "old", "extra": "keep"}
    )

    delta = _base_delta(
        world_changes=[
            {
                "change_id": "wc_upd",
                "op": "update",
                "target_id": "wrule_existing",
                "world_id": "wrule_existing",
                "world_kind": "rule",
                "field": "scope",
                "before": "old",
                "after": "new",
                "who_knows": None,
                "visibility": None,
            }
        ]
    )

    write_through(db_conn, "prj_test", delta, new_version=2)

    row = db_conn.execute(
        "SELECT data_json FROM world_rules WHERE world_rule_id = ?",
        ("wrule_existing",),
    ).fetchone()
    data = json.loads(row["data_json"])
    assert data["scope"] == "new", f"update 应替换顶层 key，实际={data}"
    assert data["extra"] == "keep", "update 不应丢失无关 key"


def test_world_rule_remove_deletes_key_from_data_json(db_conn: sqlite3.Connection) -> None:
    """world_kind=rule + op=remove + 目标存在 → 从 data_json 删 key。"""
    _insert_world_rule(
        db_conn, "wrule_rm", data_json={"scope": "magic", "to_drop": "x"}
    )

    delta = _base_delta(
        world_changes=[
            {
                "change_id": "wc_rm",
                "op": "remove",
                "target_id": "wrule_rm",
                "world_id": "wrule_rm",
                "world_kind": "rule",
                "field": "to_drop",
                "before": "x",
                "after": None,
                "who_knows": None,
                "visibility": None,
            }
        ]
    )

    write_through(db_conn, "prj_test", delta, new_version=2)

    row = db_conn.execute(
        "SELECT data_json FROM world_rules WHERE world_rule_id = ?",
        ("wrule_rm",),
    ).fetchone()
    data = json.loads(row["data_json"])
    assert "to_drop" not in data, f"remove 应删 key，实际={data}"
    assert data["scope"] == "magic", "remove 不应触碰其它 key"


def test_world_rule_update_nonexistent_silently_skipped(db_conn: sqlite3.Connection) -> None:
    """update/remove + 目标不存在 → silent skip（行 483-484 不抛错、不写任何东西）。"""
    delta = _base_delta(
        world_changes=[
            {
                "change_id": "wc_missing_upd",
                "op": "update",
                "target_id": "wrule_ghost",
                "world_id": "wrule_ghost",
                "world_kind": "rule",
                "field": "scope",
                "before": None,
                "after": "anything",
                "who_knows": None,
                "visibility": None,
            },
            {
                "change_id": "wc_missing_rm",
                "op": "remove",
                "target_id": "wrule_ghost",
                "world_id": "wrule_ghost",
                "world_kind": "rule",
                "field": "scope",
                "before": None,
                "after": None,
                "who_knows": None,
                "visibility": None,
            },
        ]
    )

    # 不应抛错；行 483-484 的 ``if cur is None: continue`` 守卫生效
    write_through(db_conn, "prj_test", delta, new_version=2)

    # 没有任何 wrule_ghost 行落地
    row = db_conn.execute(
        "SELECT COUNT(*) AS c FROM world_rules WHERE world_rule_id = ?",
        ("wrule_ghost",),
    ).fetchone()
    assert row["c"] == 0, "不存在目标的 update/remove 不应 INSERT"


# ---------------------------------------------------------------------------
# 2) relationships 分支（行 507-566）：add/update/remove + 0017 幂等
# ---------------------------------------------------------------------------


def test_relationship_add_inserts_new(db_conn: sqlite3.Connection) -> None:
    """relationship + op=add + 不存在 → INSERT。"""
    _insert_char(db_conn, "char_a")
    _insert_char(db_conn, "char_b")

    delta = _base_delta(
        relationship_changes=[
            {
                "change_id": "rc_add",
                "op": "add",
                "from_character_id": "char_a",
                "to_character_id": "char_b",
                "relation_type": "ally",
                "before": None,
                "after": {"intensity": 5, "since_chapter": 1},
                "who_knows": None,
                "visibility": None,
                "target_id": "rel_explicit",
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "excerpt": "x"},
                "risk_level": "LOW",
            }
        ]
    )

    write_through(db_conn, "prj_test", delta, new_version=2)

    row = db_conn.execute(
        "SELECT relationship_id, state_json, last_state_version FROM relationships "
        "WHERE from_character_id = ? AND to_character_id = ? AND relation_type = ?",
        ("char_a", "char_b", "ally"),
    ).fetchone()
    assert row is not None, "add 后应有 relationship 行"
    assert row["relationship_id"] == "rel_explicit", "explicit target_id 应被尊重"
    assert json.loads(row["state_json"]) == {"intensity": 5, "since_chapter": 1}
    assert row["last_state_version"] == 2


def test_relationship_update_existing_updates_state_json(
    db_conn: sqlite3.Connection,
) -> None:
    """relationship + op=update + 已存在 → UPDATE state_json / last_state_version。"""
    _insert_char(db_conn, "char_a")
    _insert_char(db_conn, "char_b")
    db_conn.execute(
        """
        INSERT INTO relationships
            (relationship_id, project_id, from_character_id, to_character_id,
             relation_type, state_json, last_state_version)
        VALUES (?, 'prj_test', 'char_a', 'char_b', 'ally', ?, 1)
        """,
        ("rel_seed", json.dumps({"intensity": 3}, ensure_ascii=False)),
    )
    db_conn.commit()

    delta = _base_delta(
        relationship_changes=[
            {
                "change_id": "rc_upd",
                "op": "update",
                "from_character_id": "char_a",
                "to_character_id": "char_b",
                "relation_type": "ally",
                "before": {"intensity": 3},
                "after": {"intensity": 7, "reason": "trauma"},
                "who_knows": None,
                "visibility": None,
                "target_id": "rel_seed",
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "excerpt": "x"},
                "risk_level": "LOW",
            }
        ]
    )

    write_through(db_conn, "prj_test", delta, new_version=5)

    row = db_conn.execute(
        "SELECT state_json, last_state_version FROM relationships WHERE relationship_id = ?",
        ("rel_seed",),
    ).fetchone()
    assert json.loads(row["state_json"]) == {"intensity": 7, "reason": "trauma"}
    assert row["last_state_version"] == 5


def test_relationship_remove_deletes_row(db_conn: sqlite3.Connection) -> None:
    """relationship + op=remove + 已存在 → DELETE 行。"""
    _insert_char(db_conn, "char_a")
    _insert_char(db_conn, "char_b")
    db_conn.execute(
        """
        INSERT INTO relationships
            (relationship_id, project_id, from_character_id, to_character_id,
             relation_type, state_json, last_state_version)
        VALUES ('rel_to_rm', 'prj_test', 'char_a', 'char_b', 'enemy', '{}', 1)
        """,
    )
    db_conn.commit()

    delta = _base_delta(
        relationship_changes=[
            {
                "change_id": "rc_rm",
                "op": "remove",
                "from_character_id": "char_a",
                "to_character_id": "char_b",
                "relation_type": "enemy",
                "before": {"intensity": 10},
                "after": None,
                "who_knows": None,
                "visibility": None,
                "target_id": "rel_to_rm",
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "excerpt": "x"},
                "risk_level": "LOW",
            }
        ]
    )

    write_through(db_conn, "prj_test", delta, new_version=2)

    row = db_conn.execute(
        "SELECT COUNT(*) AS c FROM relationships WHERE relationship_id = ?",
        ("rel_to_rm",),
    ).fetchone()
    assert row["c"] == 0, "remove 后行应被删除"


def test_relationship_duplicate_add_0017_idempotent_fallback(
    tmp_path: Path,
) -> None:
    """0017 唯一索引 IntegrityError → write_through 走 534-549 UPDATE 兜底分支。

    关键难点：534-549 兜底分支只会在「SELECT-then-INSERT 窗口里另一事务
    抢先 commit」时触发——真实 TOCTOU 并发场景，单线程单连接 sqlite3 难
    以精确复现。本测试用 ``_StubConn`` 子类连接 + 替换其 ``.execute`` 实例
    方法：

      - 首查 SELECT existing → 强制返回 None（人为 stub，模拟「窗口里看到
        空」）→ 走到 INSERT INTO relationships 分支。
      - INSERT 第一次 → 抛 IntegrityError（模拟 0017 唯一索引拦截）。
      - 内部重查 SELECT（existing 二次查找）→ 返真实已 commit 的占位行。
      - 后续 UPDATE → 走原 execute（落库更新预占位 rid 的 state_json）。
    最终断言：单行落地，relationship_id = 预占位的 rid，且 state_json /
    last_state_version 是 UPDATE 后的最新值。

    另：sqlite3.Connection 的 ``.execute`` 是 C 槽位只读，无法 monkeypatch；
    解决方案是 ``sqlite3.connect(..., factory=_StubConn)`` 拿到子类实例，
    其 ``.execute`` 是 Python 层 attr 可写。
    """
    stub, base, pre_occ_row = _make_stub_conn_for_relationships(tmp_path)

    insert_call_count = {"n": 0}
    select_existing_call_count = {"n": 0}

    def stubbed_execute(sql, params=()):  # type: ignore[override]
        sql_norm = " ".join(sql.split())
        # 行 514 / 行 538 - 「SELECT relationship_id FROM relationships」
        if sql_norm.startswith("SELECT relationship_id FROM relationships"):
            n = select_existing_call_count["n"]
            select_existing_call_count["n"] = n + 1
            return _EmptyCursor() if n == 0 else _StubCursor(pre_occ_row)
        if sql_norm.startswith("INSERT INTO relationships"):
            n = insert_call_count["n"]
            insert_call_count["n"] = n + 1
            if n == 0:
                raise sqlite3.IntegrityError(
                    "UNIQUE constraint failed: idx_relationships_unique"
                )
            # 兜底后不应再 INSERT
            raise AssertionError("二次 INSERT 不应发生")
        # UPDATE / DELETE relationships → 通过 base 连接执行（避免 stub 连接
        # 提交而 base 看不到——本测试断言以 base 视角为准）
        return base.execute(sql, params)

    stub.execute = stubbed_execute  # type: ignore[method-assign]

    # write_through 内若显式 conn.commit() 也得由 stub 触发——但调用路径都用
    # execute，本测试不强依赖显式 commit（落盘的 UPDATE 由 base 直接写，
    # SQLite 默认 autocommit 模式下已落盘；写透 INSERT 失败也不需 commit）。

    delta = _base_delta(
        relationship_changes=[
            {
                "change_id": "rc_dup",
                "op": "add",
                "from_character_id": "char_a",
                "to_character_id": "char_b",
                "relation_type": "ally",
                "before": None,
                "after": {"intensity": 7, "note": "added-after-race"},
                "who_knows": None,
                "visibility": None,
                "target_id": "rel_target",
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "excerpt": "x"},
                "risk_level": "LOW",
            }
        ]
    )

    # 不应抛错 → 534-549 兜底分支被命中
    write_through(stub, "prj_test", delta, new_version=5)

    # 用 base 读真实落盘结果
    count = base.execute(
        "SELECT COUNT(*) AS c FROM relationships "
        "WHERE project_id = 'prj_test' "
        "  AND from_character_id = 'char_a' AND to_character_id = 'char_b' "
        "  AND relation_type = 'ally'"
    ).fetchone()
    assert count["c"] == 1, "0017 冲突必须幂等为单行"

    row = base.execute(
        "SELECT state_json, last_state_version, relationship_id FROM relationships "
        "WHERE project_id = 'prj_test' "
        "  AND from_character_id = 'char_a' AND to_character_id = 'char_b' "
        "  AND relation_type = 'ally'"
    ).fetchone()
    assert row["relationship_id"] == "rel_pre_occ", (
        "IntegrityError 后应 UPDATE 预占行（保留 rid）"
    )
    assert json.loads(row["state_json"]) == {"intensity": 7, "note": "added-after-race"}, (
        "534-549 应 UPDATE state_json 为最新 after"
    )
    assert row["last_state_version"] == 5


def test_relationship_remove_nonexistent_no_error(
    db_conn: sqlite3.Connection,
) -> None:
    """op=remove + 不存在 → 行 565-566 ``if existing is not None`` 守卫生效。"""
    delta = _base_delta(
        relationship_changes=[
            {
                "change_id": "rc_rm_ghost",
                "op": "remove",
                "from_character_id": "char_ghost1",
                "to_character_id": "char_ghost2",
                "relation_type": "ally",
                "before": None,
                "after": None,
                "who_knows": None,
                "visibility": None,
                "target_id": "rel_ghost",
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "excerpt": "x"},
                "risk_level": "LOW",
            }
        ]
    )

    # 不应抛错
    write_through(db_conn, "prj_test", delta, new_version=2)
    # 不写入任何行
    count = db_conn.execute(
        "SELECT COUNT(*) AS c FROM relationships WHERE relationship_id = 'rel_ghost'"
    ).fetchone()
    assert count["c"] == 0


# ---------------------------------------------------------------------------
# 3) 组合：delta_repair → write_through（端到端一致性）
# ---------------------------------------------------------------------------


def test_delta_repair_then_write_through_world_rules_roundtrip(
    db_conn: sqlite3.Connection,
) -> None:
    """让 delta_repair 对一份「包含 ghost world_id + 实际存在的 rule」的 delta
    做修复；repaired.delta 直接喂 write_through 不抛异常且落库正确。
    """
    _insert_world_rule(db_conn, "wrule_real", data_json={"scope": "magic"})

    raw_delta = _base_delta(
        world_changes=[
            {
                "change_id": "wc_valid",
                "op": "update",
                "target_id": "wrule_real",
                "world_id": "wrule_real",
                "world_kind": "rule",
                "field": "scope",
                "before": "magic",
                "after": "magic-v2",
                "who_knows": None,
                "visibility": None,
            },
            {
                "change_id": "wc_ghost",
                "op": "add",
                "target_id": "wrule_unknown",
                "world_id": "wrule_unknown",
                "world_kind": "rule",
                "field": "scope",
                "before": None,
                "after": {"data_json": {"scope": "x"}},
                "who_knows": None,
                "visibility": None,
            },
        ]
    )

    db_path = db_conn.execute("PRAGMA database_list").fetchone()["file"]
    assert db_path, "fixture 必须真实落盘"

    repaired, repairs = repair_delta(raw_delta, snapshot=None, db_path=db_path)
    assert isinstance(repairs, list)
    assert len(repairs) >= 0  # 即便 repair 不打补丁也不报错

    write_through(db_conn, "prj_test", repaired, new_version=3)

    real_row = db_conn.execute(
        "SELECT data_json FROM world_rules WHERE world_rule_id = ?",
        ("wrule_real",),
    ).fetchone()
    assert json.loads(real_row["data_json"])["scope"] == "magic-v2"

    ghost_row = db_conn.execute(
        "SELECT data_json FROM world_rules WHERE world_rule_id = ?",
        ("wrule_unknown",),
    ).fetchone()
    assert ghost_row is not None, "add 分支应 INSERT ghost rule"
    assert json.loads(ghost_row["data_json"])["scope"] == "x"


def test_delta_repair_then_write_through_relationships_roundtrip(
    db_conn: sqlite3.Connection,
) -> None:
    """relationship 同上：repaired delta → write_through 不抛异常并落库正确。"""
    _insert_char(db_conn, "char_a")
    _insert_char(db_conn, "char_b")
    db_conn.execute(
        """
        INSERT INTO relationships
            (relationship_id, project_id, from_character_id, to_character_id,
             relation_type, state_json, last_state_version)
        VALUES ('rel_seed_b', 'prj_test', 'char_a', 'char_b', 'ally',
                '{"intensity": 2}', 1)
        """
    )
    db_conn.commit()

    raw_delta = _base_delta(
        relationship_changes=[
            {
                "change_id": "rc_repair_upd",
                "op": "update",
                "from_character_id": "char_a",
                "to_character_id": "char_b",
                "relation_type": "ally",
                "before": {"intensity": 2},
                "after": {"intensity": 8},
                "who_knows": None,
                "visibility": None,
                "target_id": "rel_seed_b",
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "excerpt": "x"},
                "risk_level": "LOW",
            },
            {
                "change_id": "rc_repair_add_ghost",
                "op": "add",
                "from_character_id": "char_a",
                "to_character_id": "char_b",
                "relation_type": "rival",
                "before": None,
                "after": {"intensity": 1},
                "who_knows": None,
                "visibility": None,
                "target_id": "rel_new",
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "excerpt": "x"},
                "risk_level": "LOW",
            },
        ]
    )

    db_path = db_conn.execute("PRAGMA database_list").fetchone()["file"]
    assert db_path

    repaired, repairs = repair_delta(raw_delta, snapshot=None, db_path=db_path)
    assert isinstance(repairs, list)

    write_through(db_conn, "prj_test", repaired, new_version=6)

    ally = db_conn.execute(
        "SELECT state_json, last_state_version FROM relationships "
        "WHERE relationship_id = 'rel_seed_b'"
    ).fetchone()
    assert json.loads(ally["state_json"]) == {"intensity": 8}
    assert ally["last_state_version"] == 6

    rival = db_conn.execute(
        "SELECT state_json, last_state_version FROM relationships "
        "WHERE relationship_id = 'rel_new'"
    ).fetchone()
    assert rival is not None, "add 分支应 INSERT 新关系"
    assert json.loads(rival["state_json"]) == {"intensity": 1}
    assert rival["last_state_version"] == 6


# ---------------------------------------------------------------------------
# 4) relationships 伴随列 visibility / who_knows 三态语义（P0 bug 修复回归）
# ---------------------------------------------------------------------------


def test_relationship_add_inserts_visibility_and_who_knows(
    db_conn: sqlite3.Connection,
) -> None:
    """relationship add 显式声明 who_knows/visibility → DB 行携带二者。"""
    _insert_char(db_conn, "char_a")
    _insert_char(db_conn, "char_b")
    delta = _base_delta(
        relationship_changes=[
            {
                "change_id": "rc_p0_add",
                "op": "add",
                "from_character_id": "char_a",
                "to_character_id": "char_b",
                "relation_type": "ally",
                "before": None,
                "after": {"intensity": 5},
                "who_knows": ["char_a"],
                "visibility": "RESTRICTED",
                "target_id": "rel_p0_add",
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "excerpt": "x"},
                "risk_level": "LOW",
            }
        ]
    )
    write_through(db_conn, "prj_test", delta, new_version=2)
    row = db_conn.execute(
        "SELECT visibility, who_knows FROM relationships WHERE relationship_id = ?",
        ("rel_p0_add",),
    ).fetchone()
    assert row["visibility"] == "RESTRICTED"
    assert json.loads(row["who_knows"]) == ["char_a"], (
        "who_knows 应原样写入；原 bug：DB 行 who_knows=NULL"
    )


def test_relationship_update_keeps_missing_who_knows(
    db_conn: sqlite3.Connection,
) -> None:
    """relationship update 缺省声明 who_knows → 不覆盖原值（沿用）。"""
    _insert_char(db_conn, "char_a")
    _insert_char(db_conn, "char_b")
    db_conn.execute(
        """
        INSERT INTO relationships
            (relationship_id, project_id, from_character_id, to_character_id,
             relation_type, state_json, last_state_version, visibility, who_knows)
        VALUES (?, 'prj_test', 'char_a', 'char_b', 'ally', ?, 1, 'RESTRICTED', ?)
        """,
        ("rel_p0_seed", json.dumps({"intensity": 3}, ensure_ascii=False),
         json.dumps(["char_a"], ensure_ascii=False)),
    )
    db_conn.commit()
    delta = _base_delta(
        relationship_changes=[
            {
                "change_id": "rc_p0_upd_missing",
                "op": "update",
                "from_character_id": "char_a",
                "to_character_id": "char_b",
                "relation_type": "ally",
                "before": {"intensity": 3},
                "after": {"intensity": 7},
                "who_knows": None,
                "visibility": None,
                "target_id": "rel_p0_seed",
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "excerpt": "x"},
                "risk_level": "LOW",
            }
        ]
    )
    write_through(db_conn, "prj_test", delta, new_version=5)
    row = db_conn.execute(
        "SELECT state_json, visibility, who_knows FROM relationships WHERE relationship_id = ?",
        ("rel_p0_seed",),
    ).fetchone()
    assert json.loads(row["state_json"]) == {"intensity": 7}
    # 三态语义：who_knows 缺省=不更新该列，沿用 DB 原值 ['char_a']。
    assert json.loads(row["who_knows"]) == ["char_a"], (
        "缺省 who_knows 应保持原值；原 bug：UPDATE 不带伴随列但也不存在只更新 state_json 的接口"
    )


def test_relationship_update_explicit_who_knows_overwrites(
    db_conn: sqlite3.Connection,
) -> None:
    """relationship update 显式 who_knows → 覆盖原值。"""
    _insert_char(db_conn, "char_a")
    _insert_char(db_conn, "char_b")
    db_conn.execute(
        """
        INSERT INTO relationships
            (relationship_id, project_id, from_character_id, to_character_id,
             relation_type, state_json, last_state_version, visibility, who_knows)
        VALUES (?, 'prj_test', 'char_a', 'char_b', 'ally', ?, 1, 'PUBLIC', ?)
        """,
        ("rel_p0_overwrite", json.dumps({"intensity": 1}, ensure_ascii=False),
         json.dumps(["char_a"], ensure_ascii=False)),
    )
    db_conn.commit()
    delta = _base_delta(
        relationship_changes=[
            {
                "change_id": "rc_p0_upd_set",
                "op": "update",
                "from_character_id": "char_a",
                "to_character_id": "char_b",
                "relation_type": "ally",
                "before": {"intensity": 1},
                "after": {"intensity": 9},
                "who_knows": ["char_a", "char_b"],
                "visibility": "RESTRICTED",
                "target_id": "rel_p0_overwrite",
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "excerpt": "x"},
                "risk_level": "LOW",
            }
        ]
    )
    write_through(db_conn, "prj_test", delta, new_version=3)
    row = db_conn.execute(
        "SELECT state_json, visibility, who_knows FROM relationships WHERE relationship_id = ?",
        ("rel_p0_overwrite",),
    ).fetchone()
    assert json.loads(row["state_json"]) == {"intensity": 9}
    assert row["visibility"] == "RESTRICTED"
    assert json.loads(row["who_knows"]) == ["char_a", "char_b"]


def test_relationship_add_default_visibility_public(
    db_conn: sqlite3.Connection,
) -> None:
    """relationship add 缺省 visibility → 沿用 DDL 默认 PUBLIC（与迁移 0014 对齐）。"""
    _insert_char(db_conn, "char_a")
    _insert_char(db_conn, "char_b")
    delta = _base_delta(
        relationship_changes=[
            {
                "change_id": "rc_p0_default_vis",
                "op": "add",
                "from_character_id": "char_a",
                "to_character_id": "char_b",
                "relation_type": "rival",
                "before": None,
                "after": {"intensity": 1},
                "who_knows": None,
                "visibility": None,
                "target_id": "rel_p0_default",
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "excerpt": "x"},
                "risk_level": "LOW",
            }
        ]
    )
    write_through(db_conn, "prj_test", delta, new_version=2)
    row = db_conn.execute(
        "SELECT visibility, who_knows FROM relationships WHERE relationship_id = ?",
        ("rel_p0_default",),
    ).fetchone()
    assert row["visibility"] == "PUBLIC", "缺省 visibility 应写入 PUBLIC"
    assert row["who_knows"] is None, "缺省 who_knows 应为 NULL"
