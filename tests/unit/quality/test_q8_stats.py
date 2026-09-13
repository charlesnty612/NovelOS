"""V3.9 批次 3.3：Q8 统计口径修正（created_by → AI 侧）+ 默认 severity 裁决。

证据（生产代码 + data/novelos.db 实测）：
- ``chapter_write/pipeline.py`` save_draft 硬编码 ``created_by='writer:v1'``；
- ``domain/chapter/service.py`` 人工改稿固定 ``created_by='human'``；
- 旧 ``compute_char_stats`` 只认 ``agent:*`` / ``human``，其余忽略 ⇒ 生产 draft 全被丢弃，
  Q8 恒 NO_DATA（或只统计到人工侧）。

覆盖：
- classify_created_by 三态；writer:v1 → ai（推断）并产口径 note；
- compute_char_stats_detail 计数 + note；未知 created_by 不计入且留痕；
- 生产接线：``build_quality_context`` + ``QualityEngine.evaluate`` → Q8 warning（非 error）、
  report 含 RULE_Q8_STATS_NOTE info、纯 AI 章不再是 NO_DATA；
- 显式 ``NOVELOS_QUALITY_Q8_STRICT=1`` → error（blocking，overall=0）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.engine import QualityEngine
from packages.core.quality.service import (
    CharStats,
    build_quality_context,
    classify_created_by,
    compute_char_stats,
    compute_char_stats_detail,
)

# ---------------------------------------------------------------------------
# helpers（真实 sqlite：走生产 migration + service 读路径）
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> str:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return str(settings.db_path)


def _make_project(db_path: str) -> str:
    from packages.domain.project.models import ProjectCreate
    from packages.domain.project.service import ProjectService

    return ProjectService(db_path).create(ProjectCreate(name="q8 统计口径项目"))[
        "project_id"
    ]


def _make_chapter(db_path: str, pid: str) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json,
                 status, visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, 1, 'C1', '{"key_beats": []}', 'DRAFTED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_draft(
    db_path: str, cid: str, version: int, content: str, created_by: str
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO drafts
                (draft_id, chapter_id, version, content, created_by,
                 prompt_version, model_id, created_at)
            VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)
            """,
            (new_id("dr"), cid, version, content, created_by, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _valid_delta(chapter_id: str) -> dict:
    """最小合法 delta（通过 schema_validity），避免无关 blocking error 干扰断言。"""
    return {
        "delta_id": "d_q8",
        "delta_version": 1,
        "chapter_id": chapter_id,
        "workflow_run_id": "wfr_q8",
        "previous_state_version": 1,
        "created_by": "observer:v1",
        "created_at": "2026-09-13T00:00:00Z",
        "schema_version": "state-delta-v0",
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }


# ---------------------------------------------------------------------------
# 分类与统计
# ---------------------------------------------------------------------------


def test_classify_created_by_three_way():
    assert classify_created_by("agent:writer:v1") == "ai"
    assert classify_created_by("writer:v1") == "ai"
    assert classify_created_by("polisher:v1") == "ai"
    assert classify_created_by("human") == "human"
    assert classify_created_by("mystery:x") == "unknown"
    assert classify_created_by("") == "unknown"


def test_compute_char_stats_counts_writer_v1_as_ai(tmp_path: Path):
    """生产形态：v1 由 writer:v1 写出 → 计入 ai_chars；人工插字 → human_chars。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    base = "一二三四五六七八九十" * 10  # 100 字
    added = "补充二十个字符的人工修改内容"  # 15 字（insert）
    _insert_draft(db_path, cid, 1, base, "writer:v1")
    _insert_draft(db_path, cid, 2, base + added, "human")

    detail = compute_char_stats_detail(db_path, cid)
    assert detail.ai_chars == 100
    assert detail.human_chars == len(added)
    assert detail.inferred_ai_chars == 100
    assert detail.unknown_created_by == ()
    assert detail.versions == 2
    assert detail.note is not None and "推断为 AI" in detail.note

    # 兼容旧接口
    assert compute_char_stats(db_path, cid) == (100, len(added))


def test_compute_char_stats_agent_prefix_still_ai(tmp_path: Path):
    """回归：显式 ``agent:*`` 仍计 ai，且不产生「推断」note。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    _insert_draft(db_path, cid, 1, "AI 生成正文", "agent:writer:v1")

    detail = compute_char_stats_detail(db_path, cid)
    assert detail.ai_chars == len("AI 生成正文")
    assert detail.inferred_ai_chars == 0
    assert detail.note is None


def test_compute_char_stats_unknown_created_by_ignored_with_note(tmp_path: Path):
    """未知 created_by：不计入任何桶（不污染占比），但留痕说明。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    _insert_draft(db_path, cid, 1, "某天外导入的正文", "mystery:import")

    detail = compute_char_stats_detail(db_path, cid)
    assert (detail.ai_chars, detail.human_chars) == (0, 0)
    assert detail.unknown_created_by == ("mystery:import",)
    assert detail.note is not None and "未知 created_by" in detail.note


def test_char_stats_note_none_when_classifiable():
    stats = CharStats(
        ai_chars=10,
        human_chars=5,
        inferred_ai_chars=0,
        unknown_created_by=(),
        versions=2,
    )
    assert stats.note is None


# ---------------------------------------------------------------------------
# 生产接线：build_quality_context + QualityEngine
# ---------------------------------------------------------------------------


def test_build_quality_context_pure_ai_chapter_not_no_data(tmp_path: Path):
    """纯 AI 章（生产 writer:v1）：Q8 有数据 → warning（默认），不再是 NO_DATA、不再 error。

    改造前：ai=0 / human=0 → RULE_Q8_NO_DATA；改造后（默认口径）：ai>0、ratio=0
    → RULE_Q8_HUMAN_RATIO_LOW warning + 口径 note。
    """
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    _insert_draft(db_path, cid, 1, "这是一整章由写作代理生成的正文内容。" * 5, "writer:v1")

    ctx = build_quality_context(
        db_path,
        project_id=pid,
        chapter_id=cid,
        delta=_valid_delta(cid),
        snapshot_pre={},
    )
    assert ctx.ai_chars > 0
    assert ctx.human_chars == 0
    assert ctx.char_stats_note and "推断为 AI" in ctx.char_stats_note

    report = QualityEngine().evaluate(ctx)
    rule_ids = [i.rule_id for i in report.issues]
    assert "RULE_Q8_NO_DATA" not in rule_ids, "生产 created_by 不再退化为 NO_DATA"
    q8 = [i for i in report.issues if i.rule_id == "RULE_Q8_HUMAN_RATIO_LOW"]
    assert q8 and q8[0].severity == "warning"
    assert any(i.rule_id == "RULE_Q8_STATS_NOTE" for i in report.issues)
    assert report.overall > 0, "默认口径下纯 AI 章不被 Q8 归零"


def test_build_quality_context_strict_env_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """显式 NOVELOS_QUALITY_Q8_STRICT=1：Q8 升级 error（blocking）⇒ overall=0。"""
    from packages.core.quality import guardrails as g

    monkeypatch.setenv(g.Q8_STRICT_ENV_VAR, "1")
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    _insert_draft(db_path, cid, 1, "纯 AI 正文。" * 10, "writer:v1")

    ctx = build_quality_context(
        db_path,
        project_id=pid,
        chapter_id=cid,
        delta=_valid_delta(cid),
        snapshot_pre={},
    )
    report = QualityEngine().evaluate(ctx)
    q8 = [i for i in report.issues if i.rule_id == "RULE_Q8_HUMAN_RATIO_LOW"]
    assert q8 and q8[0].severity == "error"
    assert report.overall == 0
