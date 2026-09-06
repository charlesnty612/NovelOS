"""write_through.resolved_hooks ABANDONED 守卫集成测试（审计 §A1）。

白盒复现：
- 审计报告 §A1（docs/testing/audit-story-state-20260829.md:25-27）：
  write_through.py:453-470 旧逻辑对 resolved_hooks 无条件 UPDATE
  ``payoff_chapter_id = chapter_id``，叠加 delta_repair 的 fill-before
  可把 ABANDONED 钩子"复活"为 RESOLVED，污染 story_state。

本测试在最小 SQLite fixture 上直接调 ``write_through``（绕开 FastAPI /
validator / delta_repair），验证：
  1. ABANDONED 钩子：resolved_hooks delta 被写透层静默拒绝，DB 行保持
     ``status='ABANDONED'``、``payoff_chapter_id`` 不变、``updated_at``
     不被刷新（守卫不触达 UPDATE）。
  2. 活跃钩子（OPEN/ACTIVE/ESCALATED/RESOLVED）回归：正常被置 RESOLVED
     且 ``payoff_chapter_id`` 被回写到 chapter_id。
  3. ABANDONED + ``__CLEAR_PAYOFF_CHAPTER__`` 哨兵分支：同样跳过——
     即"清空 payoff_chapter_id"的逆 delta 也不能动 ABANDONED 行。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from packages.core.story_state.write_through import write_through

# ---------------------------------------------------------------------------
# DB fixture：最小化项目/章节/hooks 表，匹配 0001_init.sql 关键列+CHECK。
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

DDL_HOOKS = """
CREATE TABLE hooks (
    hook_id                   TEXT PRIMARY KEY,
    project_id                TEXT NOT NULL,
    name                      TEXT NOT NULL,
    introduced_chapter_id     TEXT,
    status                    TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN','ACTIVE','ESCALATED','RESOLVED','ABANDONED')),
    importance                REAL NOT NULL DEFAULT 0.5,
    expected_payoff_chapter_id TEXT,
    payoff_chapter_id         TEXT,
    visibility                TEXT NOT NULL DEFAULT 'RESTRICTED',
    who_knows                 TEXT,
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
)
"""


@pytest.fixture()
def db_conn(tmp_path: Path):
    """最小化 SQLite 连接，PRAGMA FK 关闭（hook 不 FK chapter）。"""
    db_file = tmp_path / "novelos.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    for ddl in (DDL_PROJECTS, DDL_CHAPTERS, DDL_HOOKS):
        conn.execute(ddl)
    # seed: project + chapter
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


def _insert_hook(
    conn: sqlite3.Connection,
    hook_id: str,
    status: str,
    payoff_chapter_id: str | None = None,
    updated_at: str = "2026-01-01T00:00:00Z",
) -> None:
    conn.execute(
        """
        INSERT INTO hooks (hook_id, project_id, name, status, payoff_chapter_id,
                           created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (hook_id, "prj_test", f"hook-{hook_id}", status, payoff_chapter_id,
         "2026-01-01T00:00:00Z", updated_at),
    )
    conn.commit()


def _base_delta_resolved(
    hook_id: str,
    from_status: str | None,
    to_status: str,
    *,
    notes: str | None = None,
    payoff_chapter_id: str | None = None,
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
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [
            {
                "change_id": f"rh_{hook_id}",
                "op": "update",
                "target_id": hook_id,
                "hook_id": hook_id,
                "from_status": from_status,
                "to_status": to_status,
                "payoff_summary": "兑现",
                "notes": notes,
                "payoff_chapter_id": payoff_chapter_id,
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "excerpt": "x"},
                "risk_level": "LOW",
            }
        ],
        "new_hooks": [],
        "debt_changes": [],
    }


# ---------------------------------------------------------------------------
# 1) ABANDONED 守卫（核心修复）
# ---------------------------------------------------------------------------


def test_write_through_skips_abandoned_hook_no_payoff_clear(
    db_conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """ABANDONED 钩子 + 正常 resolved_hooks delta → 守卫跳过 + warning 留痕。

    修复前断言（红）：hook 会被置 RESOLVED 且 payoff_chapter_id='ch_test'。
    修复后断言（绿）：hook 保持 ABANDONED，payoff_chapter_id 不变，
    updated_at 不被刷新（守卫不触达 UPDATE）。
    """
    original_updated_at = "2026-01-01T00:00:00Z"
    _insert_hook(
        db_conn,
        "hook_aban",
        status="ABANDONED",
        payoff_chapter_id=None,
        updated_at=original_updated_at,
    )

    delta = _base_delta_resolved(
        "hook_aban", from_status="OPEN", to_status="RESOLVED"
    )

    with caplog.at_level(logging.WARNING, logger="packages.core.story_state.write_through"):
        write_through(db_conn, "prj_test", delta, new_version=2)

    row = db_conn.execute(
        "SELECT status, payoff_chapter_id, updated_at FROM hooks WHERE hook_id = ?",
        ("hook_aban",),
    ).fetchone()
    assert row["status"] == "ABANDONED", "ABANDONED 终态不可被复活为 RESOLVED"
    assert row["payoff_chapter_id"] is None
    # 守卫走 continue 分支，不执行 UPDATE → updated_at 应保持原值
    assert row["updated_at"] == original_updated_at

    # 留痕：warning 含 hook_id 与拒绝原因
    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("hook_aban" in m and "ABANDONED" in m for m in warning_msgs), (
        f"应留痕拒绝原因，实际 warnings={warning_msgs}"
    )


def test_write_through_skips_abandoned_hook_with_clear_payoff_sentinel(
    db_conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """ABANDONED + ``__CLEAR_PAYOFF_CHAPTER__`` 哨兵分支：仍需守卫。

    哨兵用于逆 Delta（把 payoff_chapter_id 置 NULL），但 ABANDONED 行
    上不应执行任何 UPDATE（终态不可变）。
    """
    original_updated_at = "2026-01-01T00:00:00Z"
    _insert_hook(
        db_conn,
        "hook_aban_clr",
        status="ABANDONED",
        payoff_chapter_id="ch_old",
        updated_at=original_updated_at,
    )

    delta = _base_delta_resolved(
        "hook_aban_clr",
        from_status="ACTIVE",
        to_status="RESOLVED",
        notes="rollback marker __CLEAR_PAYOFF_CHAPTER__",
    )

    with caplog.at_level(logging.WARNING, logger="packages.core.story_state.write_through"):
        write_through(db_conn, "prj_test", delta, new_version=2)

    row = db_conn.execute(
        "SELECT status, payoff_chapter_id, updated_at FROM hooks WHERE hook_id = ?",
        ("hook_aban_clr",),
    ).fetchone()
    assert row["status"] == "ABANDONED"
    # 即使哨兵要求清空 payoff_chapter_id，守卫也先于哨兵拦截
    assert row["payoff_chapter_id"] == "ch_old"
    assert row["updated_at"] == original_updated_at

    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("hook_aban_clr" in m for m in warning_msgs)


# ---------------------------------------------------------------------------
# 2) 活跃钩子回归（OPEN/ACTIVE/ESCALATED/RESOLVED → RESOLVED）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "initial_status",
    ["OPEN", "ACTIVE", "ESCALATED", "RESOLVED"],
)
def test_write_through_resolves_active_hook_and_writes_payoff(
    db_conn: sqlite3.Connection, initial_status: str
) -> None:
    """回归：活跃态钩子的 resolved_hooks delta 走正常 UPDATE 分支。

    覆盖 OPEN/ACTIVE/ESCALATED/RESOLVED 四种合法源状态——ABANDONED 已在
    上面两个用例单独验证。
    """
    _insert_hook(
        db_conn,
        f"hook_{initial_status.lower()}",
        status=initial_status,
        payoff_chapter_id=None,
    )

    delta = _base_delta_resolved(
        f"hook_{initial_status.lower()}",
        from_status=initial_status,
        to_status="RESOLVED",
    )

    write_through(db_conn, "prj_test", delta, new_version=2)

    row = db_conn.execute(
        "SELECT status, payoff_chapter_id FROM hooks WHERE hook_id = ?",
        (f"hook_{initial_status.lower()}",),
    ).fetchone()
    assert row["status"] == "RESOLVED"
    # 无显式 payoff_chapter_id 时回退到 chapter_id
    assert row["payoff_chapter_id"] == "ch_test"


def test_write_through_explicit_payoff_chapter_id_overrides_default(
    db_conn: sqlite3.Connection,
) -> None:
    """delta.payoff_chapter_id 显式给出时，应优先于 chapter_id 回退。"""
    _insert_hook(db_conn, "hook_explicit", status="ACTIVE", payoff_chapter_id=None)

    delta = _base_delta_resolved(
        "hook_explicit",
        from_status="ACTIVE",
        to_status="RESOLVED",
        payoff_chapter_id="ch_other",
    )

    write_through(db_conn, "prj_test", delta, new_version=2)

    row = db_conn.execute(
        "SELECT status, payoff_chapter_id FROM hooks WHERE hook_id = ?",
        ("hook_explicit",),
    ).fetchone()
    assert row["status"] == "RESOLVED"
    assert row["payoff_chapter_id"] == "ch_other"


# ---------------------------------------------------------------------------
# 3) 回归（不影响其他写入路径）
# ---------------------------------------------------------------------------


def test_write_through_abandoned_guard_does_not_skip_active_sibling(
    db_conn: sqlite3.Connection,
) -> None:
    """同 delta 中混入 ABANDONED + ACTIVE 两钩子：守卫仅拒绝 ABANDONED。"""
    _insert_hook(db_conn, "hook_aban2", status="ABANDONED", payoff_chapter_id=None)
    _insert_hook(db_conn, "hook_open", status="OPEN", payoff_chapter_id=None)

    delta = {
        **_base_delta_resolved("hook_aban2", "OPEN", "RESOLVED"),
        "resolved_hooks": [
            {
                **_base_delta_resolved("hook_aban2", "OPEN", "RESOLVED")[
                    "resolved_hooks"
                ][0]
            },
            {
                "change_id": "rh_open",
                "op": "update",
                "target_id": "hook_open",
                "hook_id": "hook_open",
                "from_status": "OPEN",
                "to_status": "RESOLVED",
                "payoff_summary": "兑现",
                "notes": None,
                "payoff_chapter_id": None,
                "confidence": 0.9,
                "evidence": {"chapter_id": "ch_test", "excerpt": "x"},
                "risk_level": "LOW",
            },
        ],
    }

    write_through(db_conn, "prj_test", delta, new_version=2)

    aban_row = db_conn.execute(
        "SELECT status FROM hooks WHERE hook_id = ?", ("hook_aban2",)
    ).fetchone()
    open_row = db_conn.execute(
        "SELECT status, payoff_chapter_id FROM hooks WHERE hook_id = ?", ("hook_open",)
    ).fetchone()
    assert aban_row["status"] == "ABANDONED"
    assert open_row["status"] == "RESOLVED"
    assert open_row["payoff_chapter_id"] == "ch_test"
