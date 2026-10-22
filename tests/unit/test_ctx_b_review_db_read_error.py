"""chapter_review 的 **DB 读失败不许静默**（dev-b 批次二十一 P0-3）。

缺陷形状
--------
``packages/workflows/chapter_review/pipeline.py`` 的 ``_basic_checks_node`` 改造前是
一个 ``except Exception: pass``，把两种**语义完全不同**的失败一起吞掉：

1. **DB 读失败**（连不上库 / 锁 / 行读不出来）——此时 plan 与项目上下文
   **根本没读到**；
2. **plan 解析降级**（``_parse_plan_json`` 对非法 JSON 返回 ``{}``，恒不抛）。

⇒ 结果：**空 plan 下照常审稿、照常出报告、零告警**——作者看到的是一份「审过了」的
报告，而它建立在「没读到任何输入」之上。

本文件钉住改造后的形状
----------------------
- **DB 读失败 ⇒ 有 ``log.error``**（带 chapter_id）；
- **DB 读失败 ⇒ 审查报告里有留痕**（``warnings`` 里的 ``[REVIEW-DB-READ-FAILED]`` 行，
  走既有旁路通道 ⇒ 评审 UI 直读、**不阻断 run**）；
- **review_inputs_error 的文案不许说谎**（读失败 ≠ 章节不存在）；
- **正常路径零变化**（没有读失败时，报告里**不得**出现该行 ⇒ 防「无脑每次都加」）。

零真实 LLM、零内容仓接触、零 dev 库（``tmp_path`` 临时库 + 合成行）。
"""

from __future__ import annotations

import logging
from pathlib import Path

import packages.workflows.chapter_review.pipeline as pipeline_mod
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

RULE_ID = "[REVIEW-DB-READ-FAILED]"


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_chapter_with_draft(db_path: Path) -> tuple[str, str]:
    """插项目 + 章节 + 一条 draft（返回 (project_id, chapter_id)）。"""
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
            "VALUES (?, ?, 1, 'C', '{\"chapter_goal\": \"目标\"}', 'DRAFTED', "
            "'VISIBLE', NULL, ?, ?)",
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


def _boom(*_args, **_kwargs):
    """模拟「读库失败」。"""
    raise RuntimeError("simulated DB read failure")


def _ctx(db_path: Path, chapter_id: str) -> dict:
    return {"db_path": str(db_path), "chapter_id": chapter_id}


def test_db_read_failure_is_logged_as_error(tmp_path, monkeypatch, caplog):
    """DB 读失败 ⇒ 记 ``log.error``（带 chapter_id），**不许静默**。"""
    db_path = _fresh_db(tmp_path)
    _pid, cid = _insert_chapter_with_draft(db_path)
    monkeypatch.setattr(pipeline_mod, "get_connection", _boom)

    with caplog.at_level(logging.ERROR):
        pipeline_mod._basic_checks_node(_ctx(db_path, cid))

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "DB 读失败必须留下 error 级日志（改造前是 except: pass ⇒ 零日志）"
    text = "\n".join(r.getMessage() for r in errors)
    assert "chapter read failed" in text, f"日志须说明失败事实，实得 {text!r}"
    assert cid in text, f"日志须带 chapter_id 以便定位，实得 {text!r}"


def test_db_read_failure_leaves_a_trace_in_the_review_report(tmp_path, monkeypatch):
    """DB 读失败 ⇒ 审查报告里**必须留痕**（``warnings`` 的旁路通道行）。"""
    db_path = _fresh_db(tmp_path)
    _pid, cid = _insert_chapter_with_draft(db_path)
    monkeypatch.setattr(pipeline_mod, "get_connection", _boom)

    out = pipeline_mod._basic_checks_node(_ctx(db_path, cid))
    warnings = out["review_report"]["warnings"]

    assert any(RULE_ID in w for w in warnings), (
        f"报告里必须有 {RULE_ID} 留痕行（作者要看得见「这次审稿的输入没读到」），"
        f"实得 {warnings!r}"
    )


def test_db_read_failure_does_not_claim_chapter_not_found(tmp_path, monkeypatch):
    """「读不到」≠「不存在」⇒ ``review_inputs_error`` 不得写成 not found。"""
    db_path = _fresh_db(tmp_path)
    _pid, cid = _insert_chapter_with_draft(db_path)
    monkeypatch.setattr(pipeline_mod, "get_connection", _boom)

    out = pipeline_mod._basic_checks_node(_ctx(db_path, cid))
    err = out.get("review_inputs_error") or ""
    assert "not found" not in err, f"读失败不得谎报「章节不存在」，实得 {err!r}"
    assert "read failed" in err, f"文案须说明是读失败，实得 {err!r}"


def test_normal_path_leaves_no_db_read_trace(tmp_path, monkeypatch):
    """正常路径**零变化**：报告里不得出现该留痕行（防「无脑每次都加」）。"""
    db_path = _fresh_db(tmp_path)
    _pid, cid = _insert_chapter_with_draft(db_path)

    out = pipeline_mod._basic_checks_node(_ctx(db_path, cid))
    warnings = out["review_report"]["warnings"]

    assert not any(RULE_ID in w for w in warnings), (
        f"正常路径不得有该留痕行，实得 {warnings!r}"
    )
    assert out["review_inputs_error"] is None
