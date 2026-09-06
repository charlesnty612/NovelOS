"""0017 迁移 + 配套代码测试（缺陷：relationships 缺唯一约束、workflow_runs TOCTOU）。

覆盖：
1. write_through 的重复 relationship add delta 幂等（不产生双行、不抛错）。
2. engine.start_with_nodes* 双 start 同 chapter：
   - 第一个成功；
   - 第二个抛业务异常 ``WorkflowRunConflict``（API 路由映射为 409，非 500）。
3. 迁移后 0017 索引存在且行为正确（apply_migrations 自动跑完所有迁移）:
   - relationships 同 (from,to,type) 第二次 INSERT 报 IntegrityError;
   - workflow_runs 同 chapter 双 RUNNING 行第二次 INSERT 报 IntegrityError。
4. apply_migrations 后 0017 在 _migrations 表已登记（注册机制正确）。

设计要点（TDD 红→绿）：
- write_through / engine 配套测试用最小 SQLite + 手工 DDL（白盒），
  不依赖 apply_migrations，避免 0017 已索引化后无法复现"迁移前脏数据"。
- 迁移行为测试直接调 ``apply_migrations``，期望 0017 已被应用；当前
  缺迁移时这些测试应失败（红）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import now_iso
from packages.core.story_state.write_through import write_through
from packages.core.workflow_runtime.engine import (
    WorkflowEngine,
    WorkflowNode,
    WorkflowRunConflict,
)

# ---------------------------------------------------------------------------
# 共享 DDL fixture（白盒验证 write_through / engine 配套，不走 migrations）
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

DDL_WORKFLOWS = """
CREATE TABLE workflows (
    workflow_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    version     TEXT NOT NULL DEFAULT 'v1',
    nodes_json  TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL
)
"""

DDL_WORKFLOW_RUNS = """
CREATE TABLE workflow_runs (
    run_id          TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL,
    chapter_id      TEXT,
    status          TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING','RUNNING','PAUSED','COMPLETED','FAILED','CANCELLED')),
    current_node    TEXT,
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    error           TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    FOREIGN KEY (workflow_id) REFERENCES workflows(workflow_id),
    FOREIGN KEY (chapter_id) REFERENCES chapters(chapter_id)
)
"""


@pytest.fixture
def story_db(tmp_path: Path) -> sqlite3.Connection:
    """最小 SQLite：projects / chapters / characters / relationships。

    同步应用 ``idx_relationships_unique`` 唯一索引——写透层配套测试需要
    "迁移已生效"的 DB 状态来验证幂等路径。
    """
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    for ddl in (DDL_PROJECTS, DDL_CHAPTERS, DDL_CHARACTERS, DDL_RELATIONSHIPS):
        conn.executescript(ddl)
    # 应用唯一索引（白盒等价 0017 的 relationships 部分）
    conn.execute(
        "CREATE UNIQUE INDEX idx_relationships_unique "
        "ON relationships(project_id, from_character_id, to_character_id, relation_type)"
    )
    now = now_iso()
    conn.execute(
        "INSERT INTO projects(project_id, name, created_at, updated_at) VALUES (?,?,?,?)",
        ("prj_test", "T", now, now),
    )
    conn.execute(
        "INSERT INTO chapters(chapter_id, project_id, number, created_at, updated_at) "
        "VALUES (?,?,?,?,?)",
        ("ch_test", "prj_test", 1, now, now),
    )
    for cid in ("char_a", "char_b"):
        conn.execute(
            "INSERT INTO characters(character_id, project_id, name, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (cid, "prj_test", cid, now, now),
        )
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 1) write_through：重复 relationship add delta 幂等
# ---------------------------------------------------------------------------


def _base_delta_relationship_add(
    rel_id: str, from_id: str, to_id: str, rel_type: str, *, after: dict | None = None
) -> dict:
    return {
        "delta_id": "dlt_test",
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": "ch_test",
        "workflow_run_id": "run_test",
        "previous_state_version": 1,
        "created_by": "observer:v1",
        "created_at": now_iso(),
        "supersedes": None,
        "notes": None,
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": [
            {
                "change_id": f"relchg_{rel_id}",
                "op": "add",
                "target_id": rel_id,
                "from_character_id": from_id,
                "to_character_id": to_id,
                "relation_type": rel_type,
                "from": None,
                "after": after or {"intensity": 5},
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "excerpt": "x"},
                "risk_level": "LOW",
            }
        ],
        "new_events": [],
        "new_hooks": [],
        "resolved_hooks": [],
        "debt_changes": [],
    }


def test_write_through_relationship_add_idempotent(story_db: sqlite3.Connection) -> None:
    """重复 add 同一 (from,to,type) → 唯一行、state 刷新、version 推进、不报错。

    修复前断言（红）：第二次 add 触发 IntegrityError（不幂等）或产生双行。
    修复后断言（绿）：写透层捕获 IntegrityError 走 UPDATE 分支；rows 始终为 1；
    state_json 与 last_state_version 已更新到第二次 add 的 after。
    """
    rel_id = "rel_dup"
    delta1 = _base_delta_relationship_add(rel_id, "char_a", "char_b", "ally")
    write_through(story_db, "prj_test", delta1, new_version=2)

    # 第二次 add 同 (from,to,type) 但 state 不同 —— 期望 UPDATE（幂等）
    delta2 = _base_delta_relationship_add(
        rel_id, "char_a", "char_b", "ally", after={"intensity": 9}
    )
    # 不应抛 IntegrityError
    write_through(story_db, "prj_test", delta2, new_version=3)

    rows = story_db.execute(
        "SELECT relationship_id, state_json, last_state_version "
        "FROM relationships "
        "WHERE project_id=? AND from_character_id=? AND to_character_id=? AND relation_type=?",
        ("prj_test", "char_a", "char_b", "ally"),
    ).fetchall()
    assert len(rows) == 1, f"expected exactly 1 row, got {len(rows)}: {rows}"
    row = rows[0]
    assert row["relationship_id"] == rel_id
    # UPDATE 分支应刷新 state_json 到 delta2 的 after
    assert "9" in row["state_json"], row["state_json"]
    assert row["last_state_version"] == 3


# ---------------------------------------------------------------------------
# 2) engine 双 start 同 chapter → 第二个抛 WorkflowRunConflict
# ---------------------------------------------------------------------------


def _make_engine_full(tmp_path: Path) -> WorkflowEngine:
    """带完整 migrations（含 0017）的 engine。"""
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return WorkflowEngine(settings.db_path)


def _ensure_chapter(db_path: Path, chapter_id: str, project_id: str = "prj_test") -> None:
    conn = get_connection(db_path)
    now = now_iso()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO projects(project_id, name, created_at, updated_at) "
            "VALUES (?,?,?,?)",
            (project_id, "T", now, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO chapters(chapter_id, project_id, number, "
            "created_at, updated_at) VALUES (?,?,?,?,?)",
            (chapter_id, project_id, 1, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _ai_node(name: str) -> WorkflowNode:
    def fn(_ctx):
        return {}

    return WorkflowNode(node_id=name, kind="AI", fn=fn)


def test_engine_double_start_same_chapter_raises_conflict(tmp_path: Path) -> None:
    """同 chapter 双 start：第二个抛 WorkflowRunConflict（业务异常、409 语义）。

    修复前断言（红）：第二个 start 抛 sqlite3.IntegrityError（兜底 422 / 500）；
    或两条 RUNNING 行同时存在（数据竞态）。
    修复后断言（绿）：第二个抛 ``WorkflowRunConflict``，消息含 chapter_id；
    workflow_runs 表内 RUNNING/PENDING 行不超过 1。
    """
    engine = _make_engine_full(tmp_path)
    chapter_id = "ch_double_start"
    _ensure_chapter(engine.db_path, chapter_id)
    # 慢节点：让同步 start_with_nodes 阻塞足够久，第二个 start 时首个仍 RUNNING
    import time

    def _slow_fn(_ctx):
        time.sleep(0.8)
        return {}


    # 第一个 start：用线程跑（在后台停留 RUNNING 状态），主线程即可发起第二个 start
    import threading

    barrier = threading.Event()
    first_done = threading.Event()

    def _first_start():
        # 先让第一个节点的 fn 在 barrier 后才返回
        def _barrier_fn(_ctx):
            barrier.set()
            time.sleep(0.6)
            return {}

        barrier_nodes = [WorkflowNode(node_id="barrier", kind="State", fn=_barrier_fn)]
        try:
            engine.start_with_nodes("dup-wf", barrier_nodes, chapter_id=chapter_id)
        finally:
            first_done.set()

    t = threading.Thread(target=_first_start, daemon=True)
    t.start()
    # 等节点进入 RUNNING 后再起第二个 start
    assert barrier.wait(timeout=2.0), "first run never started"

    # 第二个 start：期望抛 WorkflowRunConflict
    with pytest.raises(WorkflowRunConflict) as exc_info:
        engine.start_with_nodes("dup-wf", [_ai_node("n1")], chapter_id=chapter_id)
    assert chapter_id in str(exc_info.value)

    # 同 chapter 下 RUNNING/PENDING 行只有 1 条
    conn = get_connection(engine.db_path)
    try:
        rows = conn.execute(
            "SELECT run_id FROM workflow_runs "
            "WHERE chapter_id=? AND status IN ('RUNNING','PENDING')",
            (chapter_id,),
        ).fetchall()
        assert len(rows) == 1, f"expected 1 active run, got {len(rows)}: {rows}"
    finally:
        conn.close()
    first_done.wait(timeout=3.0)


def test_engine_double_start_async_raises_conflict(tmp_path: Path) -> None:
    """异步版 start_with_nodes_async 双 start 同 chapter 也抛 WorkflowRunConflict。"""
    engine = _make_engine_full(tmp_path)
    chapter_id = "ch_double_async"
    _ensure_chapter(engine.db_path, chapter_id)
    # 慢节点：让第一个 async 还没推到 COMPLETED
    import time

    def _slow_fn(_ctx):
        time.sleep(0.5)
        return {}

    nodes = [WorkflowNode(node_id="slow", kind="State", fn=_slow_fn)]

    run_id_1 = engine.start_with_nodes_async("dup-wf-async", nodes, chapter_id=chapter_id)
    assert run_id_1

    with pytest.raises(WorkflowRunConflict) as exc_info:
        engine.start_with_nodes_async("dup-wf-async", nodes, chapter_id=chapter_id)
    assert chapter_id in str(exc_info.value)


# ---------------------------------------------------------------------------
# 3) 迁移层：apply_migrations 后 0017 索引行为正确
# ---------------------------------------------------------------------------


def test_migration_0017_relationships_unique_index_enforced(tmp_path: Path) -> None:
    """apply_migrations 后 relationships 唯一索引生效。

    修复前断言（红）：重复 INSERT 成功（无唯一索引）。
    修复后断言（绿）：第二次 INSERT 同 (project,from,to,type) 报 IntegrityError。
    """
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    db_path = settings.db_path
    apply_migrations(db_path)

    conn = get_connection(db_path)
    now = now_iso()
    conn.execute(
        "INSERT INTO projects(project_id, name, created_at, updated_at) VALUES (?,?,?,?)",
        ("prj_t", "T", now, now),
    )
    for cid in ("c1", "c2"):
        conn.execute(
            "INSERT INTO characters(character_id, project_id, name, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (cid, "prj_t", cid, now, now),
        )
    # 第一行 OK
    conn.execute(
        "INSERT INTO relationships(relationship_id, project_id, from_character_id, "
        "to_character_id, relation_type, last_state_version) VALUES (?,?,?,?,?,?)",
        ("rel_old1", "prj_t", "c1", "c2", "ally", 1),
    )
    conn.commit()
    # 重复 (project,from,to,type) 唯一索引拒绝
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO relationships(relationship_id, project_id, from_character_id, "
            "to_character_id, relation_type, last_state_version) VALUES (?,?,?,?,?,?)",
            ("rel_old2", "prj_t", "c1", "c2", "ally", 1),
        )
    conn.close()


def test_migration_0017_workflow_runs_active_unique_index(tmp_path: Path) -> None:
    """apply_migrations 后 workflow_runs 同 chapter 双 RUNNING 行第二次 INSERT 失败。

    修复前断言（红）：第二条 RUNNING 行 INSERT 成功（无部分唯一索引）。
    修复后断言（绿）：第二条 RUNNING 行报 IntegrityError；
    但 COMPLETED 终态可共存（部分索引不覆盖终态）。
    """
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    db_path = settings.db_path
    apply_migrations(db_path)

    conn = get_connection(db_path)
    now = now_iso()
    conn.execute(
        "INSERT INTO projects(project_id, name, created_at, updated_at) VALUES (?,?,?,?)",
        ("prj_t", "T", now, now),
    )
    conn.execute(
        "INSERT INTO chapters(chapter_id, project_id, number, created_at, updated_at) "
        "VALUES (?,?,?,?,?)",
        ("ch_t", "prj_t", 1, now, now),
    )
    # workflows 表列参考 0001：name/version/definition_json/created_at/updated_at
    conn.execute(
        "INSERT INTO workflows(workflow_id, name, version, definition_json, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?)",
        ("wf_t", "test-wf", "v1", "[]", now, now),
    )
    # 第一条 RUNNING 行
    conn.execute(
        "INSERT INTO workflow_runs(run_id, workflow_id, chapter_id, status, "
        "checkpoint_json, retry_count, started_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("wfr_t1", "wf_t", "ch_t", "RUNNING", "{}", 0, now),
    )
    conn.commit()

    # 第二条 RUNNING 行（不同 run_id、同 chapter）应被部分唯一索引拒绝
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO workflow_runs(run_id, workflow_id, chapter_id, status, "
            "checkpoint_json, retry_count, started_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("wfr_t2", "wf_t", "ch_t", "RUNNING", "{}", 0, now),
        )
    # 但 COMPLETED 终态可以共存
    conn.execute(
        "INSERT INTO workflow_runs(run_id, workflow_id, chapter_id, status, "
        "checkpoint_json, retry_count, started_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("wfr_t3", "wf_t", "ch_t", "COMPLETED", "{}", 0, now),
    )
    conn.commit()
    conn.close()


def test_migration_0017_registered_in_migrations_table(tmp_path: Path) -> None:
    """apply_migrations 后 0017 在 _migrations 表已登记（注册机制正确）。"""
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    db_path = settings.db_path
    apply_migrations(db_path)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT filename FROM _migrations WHERE filename = ?",
            ("0017_unique_constraints.sql",),
        ).fetchall()
        assert len(rows) == 1, f"0017 should be applied exactly once, got {rows}"
    finally:
        conn.close()
