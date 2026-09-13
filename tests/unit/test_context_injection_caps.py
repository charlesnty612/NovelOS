"""V3.9 批次 2.2：无上限注入项加 cap 测试。

覆盖三类台账/清单的条数上限 + 确定性排序：
- ``hook_ledger_excerpt``：importance DESC → hook_id ASC，LIMIT 20（旧：全量、hook_id ASC）；
- ``narrative_debt_excerpt``：severity DESC → debt_id ASC，LIMIT 20（旧：全量）；
- ``plot_graph_excerpt.unresolved_branches``：branch_id ASC，LIMIT 20（旧：全量 ACTIVE）；
- 台账视图（hook_ledger_excerpt）与待核销视图（open_foreshadow_list）**并存**，
  语义/排序口径不同（本批只加 cap，不做合并）。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.context_engine.builders import build_director_input
from packages.core.context_engine.builders_common import (
    _DEBT_LEDGER_CAP,
    _HOOK_LEDGER_CAP,
    _UNRESOLVED_BRANCHES_CAP,
)
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


def _insert_project(db_path: Path) -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO projects (project_id, name, premise, genre, target_words, status, created_at, updated_at)
            VALUES (?, '项目', NULL, NULL, NULL, 'ACTIVE', ?, ?)
            """,
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, project_id: str, number: int = 1) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json, status,
                 visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, project_id, number, f"第{number}章", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_hook(
    db_path: Path, project_id: str, hook_id: str, *, importance: float, status: str = "OPEN",
) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO hooks (hook_id, project_id, name, status, importance,
                               visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'RESTRICTED', NULL, ?, ?)
            """,
            (hook_id, project_id, f"伏笔 {hook_id}", status, importance, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_debt(
    db_path: Path, project_id: str, debt_id: str, *, severity: float, status: str = "open",
) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO narrative_debts (debt_id, project_id, description, severity, status,
                                         visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'RESTRICTED', NULL, ?, ?)
            """,
            (debt_id, project_id, f"债务 {debt_id}", severity, status, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_branch(db_path: Path, project_id: str, branch_id: str, *, status: str = "ACTIVE") -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO branches (branch_id, project_id, name, base_state_version, status, created_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (branch_id, project_id, f"分支 {branch_id}", status, now),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. hook_ledger_excerpt cap + 排序
# ---------------------------------------------------------------------------


def test_hook_ledger_excerpt_capped_and_sorted(tmp_path: Path):
    """21 条 planted 伏笔 → 20 条，importance DESC 优先，同 importance 按 hook_id ASC。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    # hook_00 重要性最低；hook_01..hook_20 依次升高 → 应砍掉 hook_00
    for i in range(21):
        _insert_hook(db_path, pid, f"hook_{i:02d}", importance=round(i / 100.0, 2))
    # 另加一条 RESOLVED（不应进台账）
    _insert_hook(db_path, pid, "hook_paid", importance=0.99, status="RESOLVED")

    out = build_director_input(db_path, pid, cid, "意图")
    hooks = out["hook_ledger_excerpt"]

    assert len(hooks) == _HOOK_LEDGER_CAP == 20
    ids = [h["hook_id"] for h in hooks]
    assert "hook_paid" not in ids
    assert "hook_00" not in ids, "importance 最低的一条应被 cap 掉"
    # 排序：importance DESC + hook_id ASC（确定性）
    importances = [h["importance"] for h in hooks]
    assert importances == sorted(importances, reverse=True), importances
    assert ids == [f"hook_{i:02d}" for i in range(20, 0, -1)], ids

    # 同 fixture 再装配 → 顺序稳定（同键命中缓存，内容逐字段相等）
    again = build_director_input(db_path, pid, cid, "意图")
    assert [h["hook_id"] for h in again["hook_ledger_excerpt"]] == ids


def test_hook_ledger_same_importance_breaks_tie_by_hook_id(tmp_path: Path):
    """同 importance 的伏笔按 hook_id ASC 兜底（稳定序）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    for hook_id in ("hook_c", "hook_a", "hook_b"):
        _insert_hook(db_path, pid, hook_id, importance=0.5)

    hooks = build_director_input(db_path, pid, cid, "意图")["hook_ledger_excerpt"]

    assert [h["hook_id"] for h in hooks] == ["hook_a", "hook_b", "hook_c"]


def test_open_foreshadow_list_still_coexists_with_ledger(tmp_path: Path):
    """台账视图与待核销视图并存且口径不同（本批只加 cap，不合并）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=1)
    _insert_hook(db_path, pid, "hook_x", importance=0.9)

    out = build_director_input(db_path, pid, cid, "意图")

    assert "hook_ledger_excerpt" in out and "open_foreshadow_list" in out
    ledger = out["hook_ledger_excerpt"][0]
    pending = out["open_foreshadow_list"][0]
    # 台账：原始状态机字段；待核销：overdue 计算 + 章节号（字段集不同）
    assert "expected_payoff_chapter" in ledger
    assert "overdue" in pending and "chapters_since_introduced" in pending
    assert "overdue" not in ledger


# ---------------------------------------------------------------------------
# 2. narrative_debt_excerpt cap + 排序
# ---------------------------------------------------------------------------


def test_narrative_debt_excerpt_capped_and_sorted(tmp_path: Path):
    """21 条 open 债务 → 20 条，severity DESC 优先（砍掉 severity 最低的一条）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    for i in range(21):
        _insert_debt(db_path, pid, f"debt_{i:02d}", severity=round(i / 100.0, 2))

    debts = build_director_input(db_path, pid, cid, "意图")["narrative_debt_excerpt"]

    assert len(debts) == _DEBT_LEDGER_CAP == 20
    ids = [d["debt_id"] for d in debts]
    assert "debt_00" not in ids
    assert ids == [f"debt_{i:02d}" for i in range(20, 0, -1)], ids
    severities = [d["severity"] for d in debts]
    assert severities == sorted(severities, reverse=True), severities


# ---------------------------------------------------------------------------
# 3. plot_graph_excerpt.unresolved_branches cap
# ---------------------------------------------------------------------------


def test_unresolved_branches_capped(tmp_path: Path):
    """21 条 ACTIVE 分支 → 20 条（branch_id ASC 稳定序）；非 ACTIVE 不出现。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    for i in range(21):
        _insert_branch(db_path, pid, f"br_{i:02d}")
    _insert_branch(db_path, pid, "br_merged", status="MERGED")

    plot = build_director_input(db_path, pid, cid, "意图")["plot_graph_excerpt"]
    branches = plot["unresolved_branches"]

    assert len(branches) == _UNRESOLVED_BRANCHES_CAP == 20
    ids = [b["branch_id"] for b in branches]
    assert ids == [f"br_{i:02d}" for i in range(20)], ids
    assert "br_20" not in ids  # branch_id 最大的一条被 cap
    assert "br_merged" not in ids
