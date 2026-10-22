# -*- coding: utf-8 -*-
"""``normalize_chapters.py`` 的 dry-run 副作用与退出码（审计 C F-3，2026-10-23）。

缺陷形状（dry-run 写副作用 + 裸 traceback）
------------------------------------------
``sqlite3.connect(args.db)`` 对**不存在的路径会静默创建 0 字节库**，而脚本所有操作
（含默认 dry-run）都用这条 RW 连接 ⇒ 路径写错时：① 磁盘上多出一个空库；② 查询
抛 ``no such table`` → 未捕获 → **exit 1 + 裸 traceback**（运维看不出是人参错还是
库坏）。

本批修法与判别（各对应 l15 一条突变）：
1. ``is_file()`` 前置守卫 + dry-run 用 ``mode=ro`` ⇒ 坏路径 **不产生任何文件** + rc 2；
2. 顶层 ``except sqlite3.Error`` ⇒ 单行诊断 + rc 2（不再裸 traceback）；
3. ``--apply`` 仍走普通 RW 连接（**别把 apply 一起锁死**）——本文件有一条真实写入用例；
4. 逐章打印可见字数 diff 摘要（落库前可核对）。

零真实 LLM、零内容仓接触；全部 tmp_path 临时库。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import scripts.normalize_chapters as nc
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

_DRAFT_CONTENT = "他说：「走吧。」"


def _seed(tmp_path: Path) -> tuple[str, str, str]:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    db_path = str(settings.db_path)
    pid, cid, did = new_id("prj"), new_id("ch"), new_id("dr")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, '规整测试', NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, 1, '第一章', '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, now, now),
        )
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "prompt_version, model_id, created_at) "
            "VALUES (?, ?, 1, ?, 'test:writer:v1', NULL, NULL, ?)",
            (did, cid, _DRAFT_CONTENT, now),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path, pid, did


def _content(db_path: str, draft_id: str) -> str:
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT content FROM drafts WHERE draft_id = ?", (draft_id,)
        ).fetchone()["content"]
    finally:
        conn.close()


def test_missing_db_path_returns_2_and_creates_no_file(tmp_path: Path, capsys):
    target = tmp_path / "nope" / "missing.db"
    rc = nc.main(["--db", str(target), "--project", "prj_x"])
    assert rc == 2
    assert not target.exists(), "dry-run 不得创建 0 字节库（F-3 的核心副作用）"
    assert not target.parent.exists(), "连父目录都不该被创建"
    err = capsys.readouterr().err
    assert "ERROR" in err and "Traceback" not in err


def test_not_a_database_returns_2_single_line(tmp_path: Path, capsys):
    bad = tmp_path / "not_a_db.sqlite"
    bad.write_bytes(b"this is definitely not sqlite" * 8)
    rc = nc.main(["--db", str(bad), "--project", "prj_x"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "ERROR" in err and "Traceback" not in err, "数据库错误必须单行诊断（exit 2）"


def test_readonly_connection_rejects_writes(tmp_path: Path):
    db_path, _pid, _did = _seed(tmp_path)
    conn = nc._open_db(Path(db_path), read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE probe_should_fail (x INTEGER)")
    finally:
        conn.close()
    # 反向：apply 用的 RW 连接可写（别把 apply 一起锁死）
    conn = nc._open_db(Path(db_path), read_only=False)
    try:
        conn.execute("CREATE TABLE probe_ok (x INTEGER)")
        conn.commit()
    finally:
        conn.close()


def test_dry_run_does_not_write_and_reports_diff(tmp_path: Path, capsys):
    db_path, pid, did = _seed(tmp_path)
    before = _content(db_path, did)
    rc = nc.main(["--db", db_path, "--project", pid])
    assert rc == 0
    assert _content(db_path, did) == before, "dry-run 不得写库"
    out = capsys.readouterr().out
    assert "dry-run，未写入" in out
    assert "diff：可见字" in out, "dry-run 必须打印 diff 摘要"


def test_apply_still_writes(tmp_path: Path):
    db_path, pid, did = _seed(tmp_path)
    before = _content(db_path, did)
    rc = nc.main(["--db", db_path, "--project", pid, "--apply"])
    assert rc == 0
    after = _content(db_path, did)
    assert after != before, "--apply 必须仍可写（F-3 的回归边界）"
    assert "\u201c" in after, "「」应被规整成弯双引号 “”"
