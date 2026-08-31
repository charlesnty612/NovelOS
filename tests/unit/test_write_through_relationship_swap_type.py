"""relationship 换型 update 写透回归（生产事故 wfr_9d7eb9cb1eeb）。

生产事故：commit 一条 ``op=update, from=A, to=B, relation_type=alliance`` 的 delta，
领域表里已有 ``relationship_id=A:B, relation_type=antagonistic_exchange`` 的行→
write_through 按 (A,B,alliance) SELECT 漏判 → INSERT 撞 PK 炸 UNIQUE。

修复（write_through / applier 双向）：
  - 身份=端点对 (from,to)（主键 from:to 不含 type）；
  - op=update 按 (from,to,新型) 未命中时，按 target_id 或 (project,from,to) 不限
    type 兜底找既有行→走 UPDATE（含 relation_type 列）；
  - applier 侧换型 update 按 (from,to) 端点对回退匹配→原位替换。

本测试覆盖（≥5 例）：
  T1 领域表换型 update：既有 (A→B, type=X, id=A:B) → commit update(A→B, type=Y,
     target_id=A:B) → 提交不炸 UNIQUE、单行、relation_type=Y、state_json 新 after。
  T2 applier 快照换型：同场景 → state["characters"][A].relationships 单条、type=Y。
  T3 同型 update 回归：type 不变 → UPDATE（不走兜底 swap 路径），无重复行/条目。
  T4 add 全新 pair 回归：INSERT 新行 + 快照 append。
  T5 幂等（IntegrityError 兜底路径）：同一条换型 update 重复 apply → 仍单行。
"""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from packages.core.story_state.applier import apply_delta
from packages.core.story_state.write_through import write_through


# ---------------------------------------------------------------------------
# DDL（最小化：与既有 write_through 测试一致——不开 FK；含 0017 唯一索引）
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
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
)
"""

DDL_IDX_RELATIONSHIPS_UNIQUE = """
CREATE UNIQUE INDEX idx_relationships_unique
    ON relationships(project_id, from_character_id, to_character_id, relation_type)
"""


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_conn(tmp_path: Path):
    db_file = tmp_path / "novelos_swap.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    for ddl in (
        DDL_PROJECTS,
        DDL_CHAPTERS,
        DDL_CHARACTERS,
        DDL_CHARACTER_STATES,
        DDL_RELATIONSHIPS,
        DDL_IDX_RELATIONSHIPS_UNIQUE,
    ):
        conn.execute(ddl)
    conn.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?)",
        ("prj_swap", "SwapType", "ACTIVE", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO chapters VALUES (?,?,?,?,?,?)",
        ("ch_swap", "prj_swap", 1, "PLANNED", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO characters VALUES (?,?,?,?,?,?,?,?,?)",
        ("char_a", "prj_swap", "A", "supporting", "{}", "PUBLIC", None,
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO characters VALUES (?,?,?,?,?,?,?,?,?)",
        ("char_b", "prj_swap", "B", "supporting", "{}", "PUBLIC", None,
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def _seed_rel(
    conn: sqlite3.Connection,
    rid: str,
    *,
    type_: str,
    state: dict,
    who_knows: list | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO relationships
            (relationship_id, project_id, from_character_id, to_character_id,
             relation_type, state_json, last_state_version, visibility, who_knows)
        VALUES (?, 'prj_swap', 'char_a', 'char_b', ?, ?, 1, 'PUBLIC', ?)
        """,
        (
            rid, type_, json.dumps(state, ensure_ascii=False),
            json.dumps(who_knows, ensure_ascii=False) if who_knows is not None else None,
        ),
    )
    conn.commit()


def _base_delta(rel_changes: list[dict]) -> dict:
    return {
        "delta_id": "dlt_swap",
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": "ch_swap",
        "workflow_run_id": "run_swap",
        "previous_state_version": 1,
        "created_by": "observer:v1",
        "created_at": "2026-01-01T00:00:00Z",
        "supersedes": None,
        "notes": None,
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": rel_changes,
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }


def _baseline_state_with_rels(rels: list[dict]) -> dict:
    return {
        "state_version": 1,
        "characters": [
            {
                "character_id": "char_a",
                "name": "A",
                "current_state": {},
                "knowledge": [],
                "beliefs": [],
                "relationships": copy.deepcopy(rels),
                "facet": "state",
                "definition": {},
            },
            {
                "character_id": "char_b",
                "name": "B",
                "current_state": {},
                "knowledge": [],
                "beliefs": [],
                "relationships": [],
                "facet": "state",
                "definition": {},
            },
        ],
        "world": {"factions": {}, "world_rules": []},
        "hooks": [],
        "debts": [],
    }


# ---------------------------------------------------------------------------
# T1 领域表换型 update：不再炸 UNIQUE、单行、relation_type 切到新型
# ---------------------------------------------------------------------------


def test_swap_type_update_does_not_break_unique_and_updates_type(
    db_conn: sqlite3.Connection,
) -> None:
    """生产事故复现：库中既有 (A→B, type=antagonistic_exchange, rid='char_a:char_b')，
    commit 一条 update(A→B, type=alliance, target_id='char_a:char_b') → 不炸
    UNIQUE、唯一行的 relation_type 切到 alliance、state_json = 新 after。
    """
    _seed_rel(db_conn, "char_a:char_b", type_="antagonistic_exchange",
              state={"intensity": 2, "since_chapter": 1})

    delta = _base_delta([
        {
            "change_id": "rc_swap_db",
            "op": "update",
            "from_character_id": "char_a",
            "to_character_id": "char_b",
            "relation_type": "alliance",
            "target_id": "char_a:char_b",
            "before": {"intensity": 2, "since_chapter": 1},
            "after": {"intensity": 7, "since_chapter": 6, "reason": "trauma→bond"},
            "who_knows": None,
            "visibility": None,
            "confidence": 0.9,
            "evidence": {"chapter_id": "ch_swap", "excerpt": "x"},
            "risk_level": "LOW",
        }
    ])

    # 不应抛 UNIQUE 异常
    write_through(db_conn, "prj_swap", delta, new_version=6)

    count = db_conn.execute(
        "SELECT COUNT(*) AS c FROM relationships WHERE project_id = 'prj_swap'"
    ).fetchone()
    assert count["c"] == 1, "换型 update 必须保持单行（不 INSERT 新行）"

    row = db_conn.execute(
        "SELECT relationship_id, relation_type, state_json, last_state_version "
        "FROM relationships WHERE relationship_id = ?",
        ("char_a:char_b",),
    ).fetchone()
    assert row is not None
    assert row["relation_type"] == "alliance", (
        f"换型 update 必须把 relation_type 切到 alliance；实际={row['relation_type']}"
    )
    assert json.loads(row["state_json"]) == {
        "intensity": 7, "since_chapter": 6, "reason": "trauma→bond"
    }
    assert row["last_state_version"] == 6


# ---------------------------------------------------------------------------
# T2 快照侧：换型 update 后 state["characters"][A].relationships 单条、新型
# ---------------------------------------------------------------------------


def test_swap_type_applier_replaces_in_place_no_duplicate(
    db_conn: sqlite3.Connection,
) -> None:
    """applier 侧：seed 一条 (A→B, type=ally)；apply update(A→B, type=rival,
    target_id='char_a:char_b') → state["characters"][A].relationships 单条、
    type=rival、旧 ally 条目被原位替换（无副本）。
    """
    state = _baseline_state_with_rels([
        {
            "relationship_id": "char_a:char_b",
            "from_character_id": "char_a",
            "to_character_id": "char_b",
            "relation_type": "ally",
            "state_json": {"intensity": 3},
        }
    ])

    delta = _base_delta([
        {
            "change_id": "rc_swap_app",
            "op": "update",
            "from_character_id": "char_a",
            "to_character_id": "char_b",
            "relation_type": "rival",
            "target_id": "char_a:char_b",
            "before": {"intensity": 3},
            "after": {"intensity": 9, "since": "ch6"},
        }
    ])

    new_state = apply_delta(state, delta)
    rels = new_state["characters"][0]["relationships"]
    assert len(rels) == 1, f"换型 update 必须单条（原位替换，无副本）；实际 {len(rels)} 条"
    assert rels[0]["relation_type"] == "rival"
    assert rels[0]["state_json"] == {"intensity": 9, "since": "ch6"}
    assert rels[0]["relationship_id"] == "char_a:char_b"


# ---------------------------------------------------------------------------
# T3 同型 update 回归：type 不变 → 走精确三元组分支，正常 UPDATE
# ---------------------------------------------------------------------------


def test_same_type_update_works_no_duplicate(
    db_conn: sqlite3.Connection,
) -> None:
    """回归：type=ally 不变 → 走三元组精确命中分支，正常 UPDATE（无重复行）。"""
    _seed_rel(db_conn, "char_a:char_b", type_="ally",
              state={"intensity": 3}, who_knows=["char_a"])

    delta = _base_delta([
        {
            "change_id": "rc_same",
            "op": "update",
            "from_character_id": "char_a",
            "to_character_id": "char_b",
            "relation_type": "ally",
            "target_id": "char_a:char_b",
            "before": {"intensity": 3},
            "after": {"intensity": 9, "note": "deepen"},
            "who_knows": None,  # 缺省=沿用 ['char_a']
            "visibility": None,
            "confidence": 0.9,
            "evidence": {"chapter_id": "ch_swap", "excerpt": "x"},
            "risk_level": "LOW",
        }
    ])
    write_through(db_conn, "prj_swap", delta, new_version=4)

    count = db_conn.execute(
        "SELECT COUNT(*) AS c FROM relationships WHERE project_id='prj_swap'"
    ).fetchone()
    assert count["c"] == 1
    row = db_conn.execute(
        "SELECT relation_type, state_json, who_knows FROM relationships "
        "WHERE relationship_id='char_a:char_b'"
    ).fetchone()
    assert row["relation_type"] == "ally", "同型 update 不应改 type"
    assert json.loads(row["state_json"]) == {"intensity": 9, "note": "deepen"}
    assert json.loads(row["who_knows"]) == ["char_a"], "缺省 who_knows 应沿用"


# ---------------------------------------------------------------------------
# T4 add 全新 pair 回归：INSERT 新行 + 快照 append（不被 pair 兜底吞并）
# ---------------------------------------------------------------------------


def test_add_new_pair_inserts_no_collision(
    db_conn: sqlite3.Connection,
) -> None:
    """回归：add 全新 pair（C→D）→ 正常 INSERT 新行；add 不同 type 同 pair
    (A→B, type=rival) 也正常 INSERT 新行（不撞 0017 唯一索引，不被吞并）。
    """
    db_conn.execute(
        "INSERT INTO characters VALUES (?,?,?,?,?,?,?,?,?)",
        ("char_c", "prj_swap", "C", "supporting", "{}", "PUBLIC", None,
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    db_conn.execute(
        "INSERT INTO characters VALUES (?,?,?,?,?,?,?,?,?)",
        ("char_d", "prj_swap", "D", "supporting", "{}", "PUBLIC", None,
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    db_conn.commit()

    delta = _base_delta([
        {
            "change_id": "rc_add_new",
            "op": "add",
            "from_character_id": "char_c",
            "to_character_id": "char_d",
            "relation_type": "ally",
            "target_id": "rel_cd",
            "after": {"intensity": 5},
            "who_knows": None,
            "visibility": None,
            "confidence": 0.9,
            "evidence": {"chapter_id": "ch_swap", "excerpt": "x"},
            "risk_level": "LOW",
        }
    ])
    write_through(db_conn, "prj_swap", delta, new_version=2)

    row = db_conn.execute(
        "SELECT relation_type, state_json FROM relationships WHERE relationship_id='rel_cd'"
    ).fetchone()
    assert row is not None, "全新 pair add 应 INSERT 新行"
    assert row["relation_type"] == "ally"
    assert json.loads(row["state_json"]) == {"intensity": 5}

    # 快照侧 add 全新 pair 也应 append
    state = _baseline_state_with_rels([])
    delta_app = _base_delta([
        {
            "change_id": "rc_add_app",
            "op": "add",
            "from_character_id": "char_a",
            "to_character_id": "char_b",
            "relation_type": "ally",
            "target_id": None,
            "after": {"intensity": 1},
        }
    ])
    new_state = apply_delta(state, delta_app)
    rels = new_state["characters"][0]["relationships"]
    assert len(rels) == 1
    assert rels[0]["relation_type"] == "ally"


# ---------------------------------------------------------------------------
# T5 幂等：同一条换型 update 重复 apply 两次（模拟并发/重试）→ 仍单行
# ---------------------------------------------------------------------------


def test_swap_type_update_idempotent_under_retry(
    db_conn: sqlite3.Connection,
) -> None:
    """幂等：同一条换型 update delta 连 apply 两次（第二次走 IntegrityError 兜底
    分支或 swap 兜底分支均应幂等）→ 仍单行、state_json/last_state_version 是
    第二次的值。
    """
    _seed_rel(db_conn, "char_a:char_b", type_="antagonistic_exchange",
              state={"intensity": 2})

    delta = _base_delta([
        {
            "change_id": "rc_swap_idem",
            "op": "update",
            "from_character_id": "char_a",
            "to_character_id": "char_b",
            "relation_type": "alliance",
            "target_id": "char_a:char_b",
            "before": {"intensity": 2},
            "after": {"intensity": 7},
            "who_knows": None,
            "visibility": None,
            "confidence": 0.9,
            "evidence": {"chapter_id": "ch_swap", "excerpt": "x"},
            "risk_level": "LOW",
        }
    ])

    # 第一次：换型 update 命中既有（antagonistic_exchange）行→走 swap UPDATE 分支
    write_through(db_conn, "prj_swap", delta, new_version=6)
    # 第二次：同 pair 同 type=alliance 行已存在→走三元组精确命中→正常 UPDATE
    write_through(db_conn, "prj_swap", delta, new_version=7)

    count = db_conn.execute(
        "SELECT COUNT(*) AS c FROM relationships WHERE project_id='prj_swap'"
    ).fetchone()
    assert count["c"] == 1, "重复 apply 必须幂等为单行"

    row = db_conn.execute(
        "SELECT relation_type, state_json, last_state_version FROM relationships "
        "WHERE relationship_id='char_a:char_b'"
    ).fetchone()
    assert row["relation_type"] == "alliance"
    assert json.loads(row["state_json"]) == {"intensity": 7}
    assert row["last_state_version"] == 7


# ---------------------------------------------------------------------------
# 附加：swap UPDATE 命中时三态语义（who_knows/visibility 显式覆盖/缺失沿用）
# ---------------------------------------------------------------------------


def test_swap_type_update_respects_visibility_and_who_knows(
    db_conn: sqlite3.Connection,
) -> None:
    """换型 update 显式带 who_knows/visibility → swap UPDATE 路径也覆盖；
    缺省时仍沿用实体现状。
    """
    _seed_rel(db_conn, "char_a:char_b", type_="antagonistic_exchange",
              state={"intensity": 1}, who_knows=["char_a"])

    delta = _base_delta([
        {
            "change_id": "rc_swap_tristate",
            "op": "update",
            "from_character_id": "char_a",
            "to_character_id": "char_b",
            "relation_type": "alliance",
            "target_id": "char_a:char_b",
            "before": {"intensity": 1},
            "after": {"intensity": 5},
            "who_knows": ["char_a", "char_b"],
            "visibility": "RESTRICTED",
            "confidence": 0.9,
            "evidence": {"chapter_id": "ch_swap", "excerpt": "x"},
            "risk_level": "LOW",
        }
    ])
    write_through(db_conn, "prj_swap", delta, new_version=3)

    row = db_conn.execute(
        "SELECT relation_type, visibility, who_knows FROM relationships "
        "WHERE relationship_id='char_a:char_b'"
    ).fetchone()
    assert row["relation_type"] == "alliance"
    assert row["visibility"] == "RESTRICTED"
    assert json.loads(row["who_knows"]) == ["char_a", "char_b"]
