"""台账注入的章号位置过滤（2026-09-15）。

缺陷背景：`_hook_ledger_excerpt` / `_open_foreshadow_list` / `_narrative_debt_excerpt`
原本返回项目**全部**未闭环项，不看引入章号。正常顺行生成时无害（后面还没写），
但**重产早期章**时后文才引入的钩子/债务会倒灌进 planner 输入。实证：书1 重产 ch1 时，
planner 引用了 ch21 才引入的 `hook_..._second_arc_identity`，正文里冒出
「三年之约已经兑现，破屋锚点再次确认」——弧末术语跑进了开篇。

契约：注入只看**本章或更早**；`introduced_chapter_id` 为 NULL（项目级、无法判位）保留；
不传 `current_chapter_no`（None）维持原全量口径。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "t.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _setup(db_path: Path):
    pid, ts = new_id("prj"), now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, status, "
            "created_at, updated_at) VALUES (?, 'p', NULL, NULL, NULL, 'ACTIVE', ?, ?)",
            (pid, ts, ts),
        )
        chs = {}
        for n in (1, 5, 20):
            cid = new_id("ch")
            conn.execute(
                "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
                "status, visibility, who_knows, created_at, updated_at) "
                "VALUES (?,?,?,'t','{}','PLANNED','VISIBLE',NULL,?,?)",
                (cid, pid, n, ts, ts),
            )
            chs[n] = cid
        # 钩子：ch1 / ch20 各一枚（都 OPEN）+ 一枚项目级（introduced 为 NULL）
        for hid, name, intro in (
            ("hook_early", "开篇钩子", chs[1]),
            ("hook_late", "弧末钩子", chs[20]),
            ("hook_global", "项目级钩子", None),
        ):
            conn.execute(
                "INSERT INTO hooks (hook_id, project_id, name, introduced_chapter_id, status, "
                "importance, expected_payoff_chapter_id, payoff_chapter_id, visibility, "
                "who_knows, created_at, updated_at) "
                "VALUES (?,?,?,?,'OPEN',0.8,NULL,NULL,'VISIBLE',NULL,?,?)",
                (hid, pid, name, intro, ts, ts),
            )
        # 债务：ch1 / ch20 各一条
        for did, desc, created in (
            ("debt_early", "开篇债", chs[1]),
            ("debt_late", "弧末债", chs[20]),
        ):
            conn.execute(
                "INSERT INTO narrative_debts (debt_id, project_id, description, "
                "created_chapter_id, severity, deadline_chapter_id, status, visibility, "
                "who_knows, created_at, updated_at) "
                "VALUES (?,?,?,?,0.6,NULL,'open','VISIBLE',NULL,?,?)",
                (did, pid, desc, created, ts, ts),
            )
        conn.commit()
    finally:
        conn.close()
    return pid


def _hooks(conn, pid, n):
    from packages.core.context_engine.builders_common import _hook_ledger_excerpt
    return {h["hook_id"] for h in _hook_ledger_excerpt(conn, pid, current_chapter_no=n)}


def _foreshadow(conn, pid, n):
    from packages.core.context_engine.builders_common import _open_foreshadow_list
    return {h["hook_id"] for h in _open_foreshadow_list(conn, pid, current_chapter_no=n)}


def _debts(conn, pid, n):
    from packages.core.context_engine.builders_common import _narrative_debt_excerpt
    return {d["debt_id"] for d in _narrative_debt_excerpt(conn, pid, current_chapter_no=n)}


def test_early_chapter_does_not_see_later_hooks_and_debts(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _setup(db_path)
    conn = get_connection(db_path)
    try:
        # ch1：只看得到 ch1 的 + 项目级的；弧末（ch20）的不得出现
        assert _hooks(conn, pid, 1) == {"hook_early", "hook_global"}
        assert _foreshadow(conn, pid, 1) == {"hook_early", "hook_global"}
        assert _debts(conn, pid, 1) == {"debt_early"}
        # ch20：全都能看到
        assert _hooks(conn, pid, 20) == {"hook_early", "hook_late", "hook_global"}
        assert _debts(conn, pid, 20) == {"debt_early", "debt_late"}
        # 不传章号 → 维持原全量口径（preview 等场景）
        assert _hooks(conn, pid, None) == {"hook_early", "hook_late", "hook_global"}
        assert _debts(conn, pid, None) == {"debt_early", "debt_late"}
    finally:
        conn.close()


def test_director_payload_excludes_later_hooks(tmp_path: Path):
    """端到端：ch1 的 director 装配里不得出现 ch20 才引入的钩子。"""
    from packages.core.context_engine.builders import _cache_reset, build_director_input

    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _setup(db_path)
    conn = get_connection(db_path)
    try:
        cid = conn.execute(
            "SELECT chapter_id FROM chapters WHERE project_id = ? AND number = 1", (pid,)
        ).fetchone()["chapter_id"]
    finally:
        conn.close()

    out = build_director_input(db_path, pid, cid, "意图")
    ids = {h["hook_id"] for h in out["hook_ledger_excerpt"]}
    assert "hook_late" not in ids, "ch1 的装配里出现了 ch20 才引入的钩子（倒灌复发）"
    assert "hook_early" in ids
