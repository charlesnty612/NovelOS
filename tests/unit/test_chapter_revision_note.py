"""ChapterService.update_revision_note 服务层单元测试（Sprint 6，task R1）。

覆盖（任务书给死）：
- 设置成功：非空白字符串 → plan_json["revision_note"] = note
- 清除成功：空串 / 全空白 → 删除 revision_note 键
- 状态守卫：chapter.status ∈ {PLANNED, DRAFTED, REVIEWED} 放行；
            COMMITTED / RELEASED → RevisionNoteStatusNotAllowed
- 章节不存在 → 返回 None（router 转 404）
- plan_json NULL/空 → 视为 {} 再操作，写回保留其它键

测试模式参考 ``tests/unit/test_arc.py``：tmp_path + apply_migrations + 直插 chapters
绕开 ChapterService 状态机，单独跑 update_revision_note。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.domain.chapter.service import (
    ChapterService,
    RevisionNoteStatusNotAllowed,
)

# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> Settings:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings


def _make_project(db_path: str, name: str = "revision_note 项目") -> str:
    from packages.domain.project.models import ProjectCreate
    from packages.domain.project.service import ProjectService

    row = ProjectService(db_path).create(ProjectCreate(name=name))
    return row["project_id"]


def _make_chapter(
    db_path: str,
    pid: str,
    *,
    number: int = 1,
    title: str = "C1",
    status: str = "PLANNED",
    plan_json: dict | None = None,
) -> str:
    """直插 chapters（含 plan_json），绕开 ChapterService 状态机。"""
    cid = new_id("ch")
    payload = plan_json if plan_json is not None else {}
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json,
                 status, visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'VISIBLE', NULL, ?, ?)
            """,
            (
                cid,
                pid,
                number,
                title,
                json.dumps(payload, ensure_ascii=False),
                status,
                now_iso(),
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


# ---------------------------------------------------------------------------
# 设置 / 清除
# ---------------------------------------------------------------------------


def test_update_revision_note_set_success(tmp_path: Path):
    """非空白字符串 → plan_json["revision_note"] = note；返回更新后 dict 含 note。"""
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path))
    cid = _make_chapter(str(settings.db_path), pid)
    svc = ChapterService(str(settings.db_path))

    result = svc.update_revision_note(cid, "请加强第二幕的张力。")
    assert result is not None
    assert result["chapter_id"] == cid
    assert result["plan_json"]["revision_note"] == "请加强第二幕的张力。"
    assert result["status"] == "PLANNED"


def test_update_revision_note_overwrites_existing(tmp_path: Path):
    """已有 note → 覆盖更新。"""
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path))
    cid = _make_chapter(
        str(settings.db_path),
        pid,
        plan_json={"revision_note": "旧意见"},
        )
    svc = ChapterService(str(settings.db_path))

    result = svc.update_revision_note(cid, "新意见")
    assert result["plan_json"]["revision_note"] == "新意见"


def test_update_revision_note_clear_with_empty_string(tmp_path: Path):
    """空串 → 删除 revision_note 键；其它 plan_json 键保留。"""
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path))
    cid = _make_chapter(
        str(settings.db_path),
        pid,
        plan_json={"revision_note": "需要改稿", "key_beats": ["b1"]},
        )
    svc = ChapterService(str(settings.db_path))

    result = svc.update_revision_note(cid, "")
    assert "revision_note" not in result["plan_json"]
    # 其它键保留
    assert result["plan_json"]["key_beats"] == ["b1"]


def test_update_revision_note_clear_with_whitespace(tmp_path: Path):
    """全空白字符串 → 视为清除。"""
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path))
    cid = _make_chapter(
        str(settings.db_path),
        pid,
        plan_json={"revision_note": "旧意见"},
        )
    svc = ChapterService(str(settings.db_path))

    result = svc.update_revision_note(cid, "   \t\n  ")
    assert "revision_note" not in result["plan_json"]


def test_update_revision_note_preserves_other_plan_keys(tmp_path: Path):
    """写入 note 后 plan_json 其它键不被破坏。"""
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path))
    cid = _make_chapter(
        str(settings.db_path),
        pid,
        plan_json={"key_beats": ["b1", "b2"], "extra": {"foo": "bar"}},
        )
    svc = ChapterService(str(settings.db_path))

    result = svc.update_revision_note(cid, "新意见")
    assert result["plan_json"]["revision_note"] == "新意见"
    assert result["plan_json"]["key_beats"] == ["b1", "b2"]
    assert result["plan_json"]["extra"] == {"foo": "bar"}


def test_update_revision_note_handles_null_plan_json(tmp_path: Path):
    """原 plan_json 为 NULL（直插时写空串代表 {}）→ 视为 {} 再操作。"""
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path))
    # 直插时 plan_json 给空字符串，DB 视角视为 NULL/空
    cid = _make_chapter(str(settings.db_path), pid, plan_json={})
    svc = ChapterService(str(settings.db_path))

    result = svc.update_revision_note(cid, "note1")
    assert result["plan_json"]["revision_note"] == "note1"


def test_update_revision_note_updates_updated_at(tmp_path: Path):
    """写入 note 应刷新 updated_at。"""
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path))
    cid = _make_chapter(str(settings.db_path), pid)
    svc = ChapterService(str(settings.db_path))

    before = svc.get(cid)
    before_updated = before["updated_at"]

    result = svc.update_revision_note(cid, "新意见")
    assert result["updated_at"] >= before_updated


# ---------------------------------------------------------------------------
# 状态机守卫
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["PLANNED", "DRAFTED", "REVIEWED"])
def test_update_revision_note_allowed_statuses(tmp_path: Path, status: str):
    """PLANNED / DRAFTED / REVIEWED 放行。"""
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path))
    cid = _make_chapter(str(settings.db_path), pid, status=status)
    svc = ChapterService(str(settings.db_path))

    result = svc.update_revision_note(cid, "note")
    assert result is not None
    assert result["plan_json"]["revision_note"] == "note"


@pytest.mark.parametrize("status", ["COMMITTED", "RELEASED"])
def test_update_revision_note_locked_statuses_raise(tmp_path: Path, status: str):
    """COMMITTED / RELEASED → RevisionNoteStatusNotAllowed（router 转 409）。"""
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path))
    cid = _make_chapter(str(settings.db_path), pid, status=status)
    svc = ChapterService(str(settings.db_path))

    with pytest.raises(RevisionNoteStatusNotAllowed) as exc_info:
        svc.update_revision_note(cid, "note")
    assert exc_info.value.current == status


def test_update_revision_note_locked_does_not_mutate(tmp_path: Path):
    """COMMITTED/RELEASED 触发异常时，不应修改 plan_json。"""
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path))
    cid = _make_chapter(
        str(settings.db_path),
        pid,
        status="COMMITTED",
        plan_json={"key_beats": ["b1"]},
        )
    svc = ChapterService(str(settings.db_path))

    with pytest.raises(RevisionNoteStatusNotAllowed):
        svc.update_revision_note(cid, "新意见")

    after = svc.get(cid)
    assert "revision_note" not in after["plan_json"]
    assert after["plan_json"]["key_beats"] == ["b1"]


# ---------------------------------------------------------------------------
# 章节不存在
# ---------------------------------------------------------------------------


def test_update_revision_note_unknown_chapter_returns_none(tmp_path: Path):
    """chapter 不存在 → 返回 None（router 转 404）。"""
    settings = _make_settings(tmp_path)
    svc = ChapterService(str(settings.db_path))

    assert svc.update_revision_note("ch_nope", "note") is None


# ---------------------------------------------------------------------------
# 持久化校验
# ---------------------------------------------------------------------------


def test_update_revision_note_persists_across_read(tmp_path: Path):
    """写入 revision_note 后，再次读取仍存在。"""
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path))
    cid = _make_chapter(str(settings.db_path), pid)
    svc = ChapterService(str(settings.db_path))

    svc.update_revision_note(cid, "持久化测试")

    again = svc.get(cid)
    assert again["plan_json"]["revision_note"] == "持久化测试"


def test_update_revision_note_clear_persists_across_read(tmp_path: Path):
    """清除 revision_note 后，再次读取该键不在。"""
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path))
    cid = _make_chapter(
        str(settings.db_path),
        pid,
        plan_json={"revision_note": "原意见"},
        )
    svc = ChapterService(str(settings.db_path))

    svc.update_revision_note(cid, "")

    again = svc.get(cid)
    assert "revision_note" not in again["plan_json"]
