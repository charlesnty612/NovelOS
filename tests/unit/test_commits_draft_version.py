"""commits.draft_version ——「这次定稿的是哪一版草稿」（2026-10-23 · D-10）。

修的形状：**测量/消费对象 ≠ 被审对象**（本仓反复出现的同一形状的第 4 处）——
前三处分别是：0024（质量报告记录的是哪一版）、``gate._reviewed_draft_version``
（门禁量的是不是被审那一版）、评审/核销两处。本次补的是**产物面**：
外发/导出带的是不是**已定稿**那一版。

覆盖：
- ``commit_delta(draft_version=N)`` ⇒ ``commits.draft_version == N``
  （断言打在 **DB 实际列**上——返回 dict 里根本没有这个字段，看返回值等于没测）；
- 不传 ⇒ NULL（存量逐字兼容：导出侧回退「取最新」）；
- 归一化边界：bool / 0 / 负数 / str 一律折成 None（**不做静默类型转换**）；
- commit 节点端到端：``ctx["quality_reviewed_draft_version"]`` ⇒ 落库；
- 反向不变量：**不得**由「取最新 draft」反推版本号（那正是缺陷本身）。

零真实 LLM 调用、零内容仓接触：只建临时 SQLite 库 + 直接调领域函数。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.story_state.commits import _normalize_draft_version
from packages.core.story_state.service import StoryStateService
from packages.workflows.chapter_commit.commit import (
    _commit_node,
    _reviewed_draft_version_of,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _seed(db_path: Path) -> dict:
    """最小 fixture：project + character + chapter(REVIEWED) + genesis v1。"""
    suffix = uuid.uuid4().hex[:8]
    pid = f"prj_{suffix}"
    cid = f"char_{suffix}"
    chap = f"ch_{suffix}"
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', ?, ?)",
            (pid, "d10-proj", _now_iso(), _now_iso()),
        )
        conn.execute(
            "INSERT INTO characters (character_id, project_id, name, role, core_json, "
            "visibility, created_at, updated_at) VALUES (?, ?, ?, 'protagonist', ?, "
            "'VISIBLE', ?, ?)",
            (cid, pid, "Alice", json.dumps({"personality": "calm"}), _now_iso(), _now_iso()),
        )
        conn.execute(
            "INSERT INTO character_states (character_id, state_version, state_json, "
            "visibility, created_at) VALUES (?, 1, ?, 'VISIBLE', ?)",
            (cid, json.dumps({"location": "Village", "emotion": "calm"}), _now_iso()),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, created_at, updated_at) "
            "VALUES (?, ?, 1, '第一章', '{}', 'REVIEWED', 'VISIBLE', ?, ?)",
            (chap, pid, _now_iso(), _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    StoryStateService(db_path).init_genesis(pid, chap)
    return {"project_id": pid, "character_id": cid, "chapter_id": chap}


def _submit_delta(db_path: Path, seed: dict) -> str:
    """提交一条最小合法 delta，返回 delta_id。"""
    suffix = seed["chapter_id"].split("_", 1)[1]
    delta = {
        "delta_id": f"dlt_{suffix}",
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": seed["chapter_id"],
        "workflow_run_id": f"wfr_{suffix}",
        "previous_state_version": 1,
        "created_by": "observer:v1",
        "created_at": _now_iso(),
        "supersedes": None,
        "notes": None,
        "character_changes": [
            {
                "change_id": f"cc_{suffix}",
                "op": "update",
                "target_id": seed["character_id"],
                "character_id": seed["character_id"],
                "facet": "state",
                "field": "state.location",
                "before": "Village",
                "after": "Cave",
                "confidence": 0.9,
                "evidence": {
                    "chapter_id": seed["chapter_id"],
                    "scene_id": None,
                    "excerpt": "excerpt",
                    "span": None,
                },
                "risk_level": "LOW",
            }
        ],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    result = StoryStateService(db_path).submit_delta(delta)
    assert result["status"] == "validated"
    return result["delta_id"]


def _commit_rows(db_path: Path) -> list[dict]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT commit_id, chapter_id, draft_version, rollback_of "
            "FROM commits ORDER BY timestamp, commit_id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. 落库：commit_delta 把「定的是哪一版」写进 commits.draft_version
# ---------------------------------------------------------------------------


def test_commit_delta_persists_draft_version(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    seed = _seed(db_path)
    delta_id = _submit_delta(db_path, seed)

    StoryStateService(db_path).commit_delta(
        delta_id,
        {"approved": True, "approver": "system:test", "notes": "d10"},
        workflow_run_id=f"wfr_{seed['chapter_id']}",
        draft_version=3,
    )

    rows = _commit_rows(db_path)
    # genesis 行 + 本次 commit 行
    assert len(rows) == 2, rows
    assert rows[-1]["draft_version"] == 3
    assert rows[-1]["chapter_id"] == seed["chapter_id"]


def test_commit_delta_without_draft_version_stores_null(tmp_path: Path):
    """存量兼容：不传 ⇒ NULL（导出侧按「未记录」回退取最新）。"""
    db_path = _fresh_db(tmp_path)
    seed = _seed(db_path)
    delta_id = _submit_delta(db_path, seed)

    StoryStateService(db_path).commit_delta(
        delta_id,
        {"approved": True, "approver": "system:test", "notes": "d10"},
        workflow_run_id=f"wfr_{seed['chapter_id']}",
    )

    rows = _commit_rows(db_path)
    assert len(rows) == 2, rows
    assert rows[-1]["draft_version"] is None


def test_genesis_commit_has_null_draft_version(tmp_path: Path):
    """genesis 行也走同一张表 ⇒ 必须可空，否则迁移会把老库撑爆。"""
    db_path = _fresh_db(tmp_path)
    seed = _seed(db_path)
    rows = _commit_rows(db_path)
    assert len(rows) == 1, rows
    assert rows[0]["draft_version"] is None
    assert rows[0]["chapter_id"] == seed["chapter_id"]


# ---------------------------------------------------------------------------
# 2. 归一化边界：类型错误不得静默变成「看起来正确的版本号」
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (1, 1),
        (7, 7),
        (None, None),
        (0, None),
        (-1, None),
        (True, None),
        (False, None),
        ("3", None),
        (3.0, None),
        ([3], None),
    ],
)
def test_normalize_draft_version(raw, expected):
    assert _normalize_draft_version(raw) == expected


def test_normalize_draft_version_rejects_bool_specifically():
    """``True`` 在 Python 里是 int 的子类 —— 不拦住会变成 version=1，与真版本 1 混淆。"""
    assert _normalize_draft_version(True) is None
    assert _normalize_draft_version(True) != 1


# ---------------------------------------------------------------------------
# 3. commit 节点端到端：ctx 的「被审版本」落进 commits
# ---------------------------------------------------------------------------


def test_commit_node_records_reviewed_draft_version(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    seed = _seed(db_path)
    delta_id = _submit_delta(db_path, seed)

    out = _commit_node(
        {
            "db_path": str(db_path),
            "chapter_id": seed["chapter_id"],
            "run_id": f"wfr_{seed['chapter_id']}",
            "delta_id": delta_id,
            "quality_reviewed_draft_version": 2,
        }
    )
    assert out["status_after"] == "COMMITTED"

    rows = _commit_rows(db_path)
    assert rows[-1]["draft_version"] == 2


def test_commit_node_without_reviewed_draft_version_stores_null(tmp_path: Path):
    """反向不变量：拿不到被审版本 ⇒ 落 NULL，**不得**拿「最新 draft」反推。

    「取最新反推」= 把「审完又改稿」的那一版误认成定稿版 —— 正是本次要修的缺陷。
    """
    db_path = _fresh_db(tmp_path)
    seed = _seed(db_path)
    delta_id = _submit_delta(db_path, seed)

    _commit_node(
        {
            "db_path": str(db_path),
            "chapter_id": seed["chapter_id"],
            "run_id": f"wfr_{seed['chapter_id']}",
            "delta_id": delta_id,
        }
    )

    rows = _commit_rows(db_path)
    assert rows[-1]["draft_version"] is None


def test_commit_node_ignores_non_positive_reviewed_version(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    seed = _seed(db_path)
    delta_id = _submit_delta(db_path, seed)

    _commit_node(
        {
            "db_path": str(db_path),
            "chapter_id": seed["chapter_id"],
            "run_id": f"wfr_{seed['chapter_id']}",
            "delta_id": delta_id,
            "quality_reviewed_draft_version": 0,
        }
    )

    rows = _commit_rows(db_path)
    assert rows[-1]["draft_version"] is None


# ---------------------------------------------------------------------------
# 4. ctx 键读取：只有 quality_gate 节点写的那个键算数
# ---------------------------------------------------------------------------


def test_reviewed_draft_version_of_reads_gate_key():
    assert _reviewed_draft_version_of({"quality_reviewed_draft_version": 5}) == 5


@pytest.mark.parametrize(
    "ctx",
    [
        {},
        {"quality_reviewed_draft_version": None},
        {"quality_reviewed_draft_version": 0},
        {"quality_reviewed_draft_version": -3},
        {"quality_reviewed_draft_version": True},
        {"quality_reviewed_draft_version": "5"},
        {"draft_version": 9},
    ],
)
def test_reviewed_draft_version_of_rejects_others(ctx):
    """只认 quality_gate 写的那个键；``draft_version`` 不是本函数的口径。

    （``ctx["draft_version"]`` 是 start 端点的通用入参，gate 侧另有解析逻辑；
    commit 侧的权威来源是「门禁实际量过的那一版」，不是「调用方说想量哪一版」。）
    """
    assert _reviewed_draft_version_of(ctx) is None
