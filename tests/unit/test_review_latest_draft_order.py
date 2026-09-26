"""评审节点「最新一版」的口径必须与共享解析单点同源（2026-09-18 本批次第 4 项）。

缺陷：``chapter_review._basic_checks_node`` 的默认分支按 ``ORDER BY created_at DESC``
取「最新」，而 ``draft_resolver.resolve_draft``（P1-1 的共享单点：题材核销层 /
质量报告 / 指定版本复审都用它）按 ``ORDER BY version DESC`` 取。同一份评审报告里
因此存在**两个可能的「最新」**：``version`` 是单调发布序（作者看到的 v2/v3 就是它），
``created_at`` 只是墙钟戳——同秒并列、手工改库、时钟回拨都会让二者分叉。

判别方式：让两个定义**指向不同的版本**，再看报告量的是哪一版。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.wordcount import visible_chars
from packages.workflows.chapter_review.pipeline import _basic_checks_node

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

# 与既有用例同款：多段 + 含对话的中性填充（``neutral_prose`` 保证零 AI 命中）。
from tests.unit.neutral_prose import neutral_prose  # noqa: E402

_OLD_TEXT_CHARS = 1700
_NEW_TEXT_CHARS = 2000


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
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, 'P', NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, pid: str) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, 1, 'C', '{}', 'DRAFTED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_draft(
    db_path: Path, cid: str, version: int, content: str, *, created_at: str
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "created_at) VALUES (?, ?, ?, ?, 'human', ?)",
            (new_id("drf"), cid, version, content, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def test_basic_checks_latest_follows_version_not_created_at(tmp_path: Path):
    """``created_at`` 倒挂（v2 的墙钟戳比 v1 早）时，「最新」仍必须是 v2。

    两个定义在这里指向不同版本：按 ``created_at`` 会量到 v1（1700 字），
    按 ``version``（共享单点口径）量到 v2（2000 字）。报告必须报 2000/v2。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    # v1 先发布但墙钟戳更晚（同秒并列 / 手工改库 / 时钟回拨都能造出这个形状）
    _insert_draft(
        db_path, cid, 1, neutral_prose(_OLD_TEXT_CHARS),
        created_at="2026-09-18T12:00:00+08:00",
    )
    _insert_draft(
        db_path, cid, 2, neutral_prose(_NEW_TEXT_CHARS),
        created_at="2026-09-18T09:00:00+08:00",
    )

    rep = _basic_checks_node(
        {"db_path": db_path, "chapter_id": cid, "target_word_count": _NEW_TEXT_CHARS}
    )["review_report"]

    assert rep["draft_version"] == 2
    assert rep["word_count"] == visible_chars(neutral_prose(_NEW_TEXT_CHARS))


def test_basic_checks_explicit_version_still_wins(tmp_path: Path):
    """显式指定版本优先级不变（复审旧版路径不受本次收敛影响）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, 1, neutral_prose(_OLD_TEXT_CHARS), created_at=now_iso())
    _insert_draft(db_path, cid, 2, neutral_prose(_NEW_TEXT_CHARS), created_at=now_iso())

    rep = _basic_checks_node(
        {"db_path": db_path, "chapter_id": cid, "target_word_count": _NEW_TEXT_CHARS,
         "draft_version": 1}
    )["review_report"]

    assert rep["draft_version"] == 1
    assert rep["word_count"] == visible_chars(neutral_prose(_OLD_TEXT_CHARS))
