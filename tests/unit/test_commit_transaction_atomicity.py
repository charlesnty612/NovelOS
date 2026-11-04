"""commit 与章节状态的**事务原子性**（2026-11-04 · 批次三十五 批 4）。

缺陷形状（AGENTS 坑区「不可逆写」族）
------------------------------------
``packages/workflows/chapter_commit/commit.py`` 的 commit 节点此前**先**调
``svc.commit_delta(...)``（内部自带事务并提交），**再另开一条连接**执行
``UPDATE chapters SET status = 'COMMITTED'``（**无 status 条件**）⇒ **两个事务**。

⇒ 两个后果：
  ① **中间崩溃** ⇒ ``story_states`` 已推进、``commits`` 行已落，而章状态未变；
     重跑 commit 会以**新的** previous_state_version 再提交一次 ⇒ ``state_version`` 再 +1
     （版本线出现一次无意的重复推进）。
  ② **并发改稿** ⇒ ``create_draft`` 把章降回 ``DRAFTED``（``domain/chapter/service.py``
     的直写 UPDATE）⇒ 无条件的 UPDATE 会把作者**刚写的稿**盖回 ``COMMITTED``。

修法
----
把章节状态 UPDATE **移进** ``commit_delta`` 的同一事务，并带上「本次读到的状态」
做**事务内乐观锁**：状态被并发改过 ⇒ 抛 :class:`StateConflictError` ⇒ 整个事务回滚
⇒ **要么都变、要么都不变**。

⚠️ 默认参数是 None ⇒ 其余四个调用方（手工提交端点 / What-if 模拟 / 分支 promote /
rollback）行为**逐位不变**（本文件最后一条用例钉住这一点）。

零真实 LLM 调用、零内容仓接触：只建临时 SQLite 库 + 直接调领域函数。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.story_state.exceptions import StateConflictError
from packages.core.story_state.service import StoryStateService
from packages.workflows.chapter_commit.commit import _commit_node

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
            (pid, "atom-proj", _now_iso(), _now_iso()),
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


def _chapter_status(db_path: Path, chapter_id: str) -> str | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM chapters WHERE chapter_id = ?", (chapter_id,)
        ).fetchone()
        return row["status"] if row is not None else None
    finally:
        conn.close()


def _state_version(db_path: Path, project_id: str) -> int | None:
    """当前状态版本 = 该项目的**最大** state_version。

    ⚠️ ``story_states`` 的主键是 ``(project_id, state_version)`` ⇒ **每版一行**
    ⇒ 取第一行会拿到 genesis 那版（曾导致本用例误判「版本没推进」）。
    ⇒ 必须取 MAX。
    """
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(state_version) AS v FROM story_states WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        return row["v"] if row is not None else None
    finally:
        conn.close()


def _commit_count(db_path: Path) -> int:
    conn = get_connection(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) AS n FROM commits").fetchone()["n"])
    finally:
        conn.close()


def _force_status(db_path: Path, chapter_id: str, status: str) -> None:
    """直接把章状态改成某值（模拟并发改稿 / 外部改动）。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapters SET status = ?, updated_at = ? WHERE chapter_id = ?",
            (status, _now_iso(), chapter_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. 正常路径：章状态与 story_states **同事务**推进
# ---------------------------------------------------------------------------


def test_commit_node_advances_chapter_status_and_state_version_together(tmp_path: Path):
    """commit 成功后：章状态 = COMMITTED **且** state_version 已推进。"""
    db_path = _fresh_db(tmp_path)
    seed = _seed(db_path)
    delta_id = _submit_delta(db_path, seed)

    before_version = _state_version(db_path, seed["project_id"])
    out = _commit_node(
        {
            "db_path": str(db_path),
            "chapter_id": seed["chapter_id"],
            "run_id": f"wfr_{seed['chapter_id']}",
            "delta_id": delta_id,
        }
    )
    assert out["status_after"] == "COMMITTED"
    # 章状态推进（这条断言打在 **DB 实际列**上，不看返回值）
    assert _chapter_status(db_path, seed["chapter_id"]) == "COMMITTED"
    # 状态版本推进
    assert _state_version(db_path, seed["project_id"]) == (before_version or 0) + 1


# ---------------------------------------------------------------------------
# 2. 乐观锁：状态被并发改过 ⇒ 冲突 ⇒ **要么都变、要么都不变**
# ---------------------------------------------------------------------------


def test_commit_delta_rejects_when_chapter_status_changed_concurrently(tmp_path: Path):
    """调用方读到的状态与事务内读到的不一致 ⇒ 抛冲突 ⇒ 事务整体回滚。

    ⚠️ 断言三件事同时成立（**缺一即非原子**）：
      ① 抛 :class:`StateConflictError`；
      ② 章状态**仍是并发改成的那值**（没被盖回 COMMITTED）；
      ③ state_version **未推进**、commits 行数**未增加**（story_states 没被提交）。
    """
    db_path = _fresh_db(tmp_path)
    seed = _seed(db_path)
    delta_id = _submit_delta(db_path, seed)

    before_version = _state_version(db_path, seed["project_id"])
    before_commits = _commit_count(db_path)
    # 模拟并发改稿：create_draft 把章降回 DRAFTED
    _force_status(db_path, seed["chapter_id"], "DRAFTED")

    with pytest.raises(StateConflictError):
        StoryStateService(db_path).commit_delta(
            delta_id,
            {"approved": True, "approver": "system:test", "notes": "atomic"},
            workflow_run_id=f"wfr_{seed['chapter_id']}",
            chapter_status_after="COMMITTED",
            chapter_status_expect="REVIEWED",
        )

    assert _chapter_status(db_path, seed["chapter_id"]) == "DRAFTED"
    assert _state_version(db_path, seed["project_id"]) == before_version
    assert _commit_count(db_path) == before_commits


def test_commit_node_does_not_overwrite_concurrent_draft(tmp_path: Path, monkeypatch):
    """端到端：并发改稿（章降回 DRAFTED）⇒ commit **不得**把它盖回 COMMITTED。

    ⚠️ 注入点：``commit_delta`` 真正执行**之前**把章状态改掉 ⇒ 模拟「节点读到
    REVIEWED 之后、事务开始之前，作者改了稿」。
    """
    db_path = _fresh_db(tmp_path)
    seed = _seed(db_path)
    delta_id = _submit_delta(db_path, seed)

    before_version = _state_version(db_path, seed["project_id"])
    before_commits = _commit_count(db_path)

    # ⚠️ 必须打**类**上的方法：commit 节点内部**自建** StoryStateService 实例
    # ⇒ 只改自己那个实例的绑定，节点根本走不到 ⇒ 注入失效（曾导致「DID NOT RAISE」）。
    original = StoryStateService.commit_delta

    def _racing_commit_delta(self, *args, **kwargs):
        _force_status(db_path, seed["chapter_id"], "DRAFTED")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(StoryStateService, "commit_delta", _racing_commit_delta)

    with pytest.raises(StateConflictError):
        _commit_node(
            {
                "db_path": str(db_path),
                "chapter_id": seed["chapter_id"],
                "run_id": f"wfr_{seed['chapter_id']}",
                "delta_id": delta_id,
            }
        )

    # 章**仍是被改稿后的 DRAFTED**（没被盖回 COMMITTED）
    assert _chapter_status(db_path, seed["chapter_id"]) == "DRAFTED"
    # story_states 未被提交（要么都变、要么都不变）
    assert _state_version(db_path, seed["project_id"]) == before_version
    assert _commit_count(db_path) == before_commits


# ---------------------------------------------------------------------------
# 3. 反向不变量：不传新参数 ⇒ 章节状态**逐位不变**
# ---------------------------------------------------------------------------


def test_commit_delta_without_chapter_status_param_leaves_chapter_untouched(tmp_path: Path):
    """默认参数为 None ⇒ 不改章节状态（其余四个调用方行为逐位不变）。"""
    db_path = _fresh_db(tmp_path)
    seed = _seed(db_path)
    delta_id = _submit_delta(db_path, seed)

    StoryStateService(db_path).commit_delta(
        delta_id,
        {"approved": True, "approver": "system:test", "notes": "atomic"},
        workflow_run_id=f"wfr_{seed['chapter_id']}",
    )

    assert _chapter_status(db_path, seed["chapter_id"]) == "REVIEWED"
    assert _commit_count(db_path) == 2  # genesis + 本次
