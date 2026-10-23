"""chapter_commit 的**缓存失效失败不许静默**（dev-b 批次二十二 P2-4）。

缺陷形状
--------
``packages/workflows/chapter_commit/commit.py`` 的 ``_commit_node`` 在 commit 成功后
显式失效本章装配缓存（兜底：正常路径 state_version 已推进、缓存键自然失效；此处覆盖
「plan_json 被 UPDATE 但 state_version 未变」的边界场景）。改造前该块的收尾是::

    except Exception:  # noqa: BLE001 —— 失效失败不阻断 commit
        pass

⇒ **「不阻断 commit」这个决定是对的**（保留），但 ``pass`` 让**失效失败零痕迹**。
后果不是「commit 失败」，而是：失效没生效 ⇒ 该章后续装配**静默命中旧缓存**
（脏命中）⇒ 读者看到的是一份**用旧上下文**生成的新正文，且**无人知道**。

本文件钉住改造后的形状
----------------------
- **失效失败 ⇒ commit 仍成功**（不抛、不阻断——行为逐字不变）；
- **失效失败 ⇒ 有 warning 记录**（带 project_id / chapter_no / 异常摘要）；
- **正常路径 ⇒ 不得有该 warning**（防「无脑每次都加」）。

零真实 LLM、零内容仓接触、零 dev 库（``tmp_path`` 临时库 + 合成行）。
"""

from __future__ import annotations

import logging
from pathlib import Path

import packages.workflows.chapter_commit.commit as commit_mod
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project_chapter_draft(db_path: Path) -> tuple[str, str]:
    """项目 + 章节（REVIEWED）+ 一条 draft（commit 节点要读它做 FTS upsert）。"""
    pid = new_id("prj")
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, '项目', NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "plan_json, status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, 1, 'C', '{}', 'REVIEWED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, now, now),
        )
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "prompt_version, model_id, created_at) "
            "VALUES (?, ?, 1, '正文。', 'test:writer:v1', 'writer:v1', NULL, ?)",
            (new_id("dr"), cid, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid, cid


def _fake_commit_delta(
    self, delta_id, author_approval, run_id, **kwargs  # noqa: ANN001, ARG001
):
    """把真实 commit 换成最小实现（本用例要测的是**失效失败**那条路径）。

    ⚠️ **替身必须跟得上生产签名**（2026-10-23 · D-10）：生产侧 ``commit_delta``
    现在还会被传 ``draft_version=``（``packages/workflows/chapter_commit/commit.py``
    的 ``_reviewed_draft_version_of(ctx)``）。替身漏了这个 kwarg ⇒
    ``TypeError: _fake_commit_delta() got an unexpected keyword argument 'draft_version'``
    ⇒ **测试红的是替身过期，不是生产行为变了**。
    ⇒ 这里用 ``**kwargs`` 吸收新增 kwarg（生产侧再加参数也不会让替身过期）。
    """
    return {"commit_id": new_id("cmt"), "state_version": 2, "delta_id": delta_id}


def _ctx(db_path: Path, chapter_id: str) -> dict:
    return {
        "db_path": str(db_path),
        "chapter_id": chapter_id,
        "run_id": new_id("wfr"),
        "delta_id": new_id("dlt"),
        "needs_high_risk_approval": False,
        "human_input": {},
        "_high_risk_approved": False,
    }


def _boom_invalidate(*_args, **_kwargs):
    raise RuntimeError("simulated cache invalidation failure")


def test_cache_invalidation_failure_still_commits_but_leaves_a_warning(
    tmp_path, monkeypatch, caplog
):
    """失效失败 ⇒ **commit 仍成功** + **有 warning 记录**。"""
    from packages.core.context_engine import builders as builders_mod
    from packages.core.story_state import service as svc_mod

    db_path = _fresh_db(tmp_path)
    pid, cid = _insert_project_chapter_draft(db_path)
    monkeypatch.setattr(svc_mod.StoryStateService, "commit_delta", _fake_commit_delta)
    monkeypatch.setattr(
        builders_mod, "_invalidate_cache_for_chapter", _boom_invalidate,
    )

    with caplog.at_level(logging.WARNING):
        out = commit_mod._commit_node(_ctx(db_path, cid))

    assert out["status_after"] == "COMMITTED", "失效失败**不得**阻断 commit"
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, (
        "失效失败必须留 warning（否则后续装配会**静默命中旧缓存**而无人知道）"
    )
    text = "\n".join(r.getMessage() for r in warnings)
    assert pid in text, f"warning 须带 project_id 以便定位，实得 {text!r}"
    assert "1" in text, f"warning 须带 chapter_no，实得 {text!r}"
    assert "simulated cache invalidation failure" in text, (
        f"warning 须带异常摘要，实得 {text!r}"
    )


def test_successful_invalidation_leaves_no_warning(tmp_path, monkeypatch, caplog):
    """正常路径（失效成功）⇒ **不得**出现该 warning（防「无脑每次都加」）。"""
    from packages.core.story_state import service as svc_mod

    db_path = _fresh_db(tmp_path)
    _pid, cid = _insert_project_chapter_draft(db_path)
    monkeypatch.setattr(svc_mod.StoryStateService, "commit_delta", _fake_commit_delta)

    with caplog.at_level(logging.WARNING):
        out = commit_mod._commit_node(_ctx(db_path, cid))

    assert out["status_after"] == "COMMITTED"
    texts = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert not any("装配缓存失效失败" in t for t in texts), (
        f"失效成功时不得出现该 warning，实得 {texts!r}"
    )
