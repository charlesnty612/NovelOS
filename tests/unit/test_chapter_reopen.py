"""ChapterService.reopen_for_repair 服务层单元测试（重开返修批次，2026-09-26）。

覆盖（任务书给死）：
- COMMITTED → reopen → 返回 dict 且 status=REVIEWED（合法唯一入口边）
- reason 非空 → plan_json.revision_note = "reopen: <reason>"
- 已有 revision_note 时换行拼接（不覆盖历史意见）
- reason 空白 / None → 不动 revision_note
- status != COMMITTED（DRAFTED / PLANNED / REVIEWED / RELEASED）→ ChapterTransitionError
  （显式拦截：DRAFTED→REVIEWED 本身是合法推进边，不能让 reopen 混用）
- chapter 不存在 → None（router 转 404）
- ALLOWED_NEXT 回归：COMMITTED 原有三边（PLANNED / COMMITTED / RELEASED）仍在；
  键集合不变（5 个状态）；新边仅 REVIEWED

测试模式参考 ``tests/unit/test_chapter_revision_note.py``：tmp_path + apply_migrations
+ 直插 chapters 绕开状态机。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.domain.chapter.models import ALLOWED_NEXT, ChapterUpdate
from packages.domain.chapter.service import ChapterService, ChapterTransitionError

# ---------------------------------------------------------------------------
# 工厂函数（与 test_chapter_revision_note.py 同款）
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> Settings:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings


def _make_project(db_path: str, name: str = "reopen 项目") -> str:
    from packages.domain.project.models import ProjectCreate
    from packages.domain.project.service import ProjectService

    row = ProjectService(db_path).create(ProjectCreate(name=name))
    return row["project_id"]


def _make_chapter(
    db_path: str,
    pid: str,
    *,
    number: int = 1,
    status: str = "COMMITTED",
    plan_json: dict | None = None,
) -> str:
    """直插 chapters（含 plan_json / status），绕开状态机。"""
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
                f"C{number}",
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
# 合法路径：COMMITTED → REVIEWED
# ---------------------------------------------------------------------------


def test_reopen_committed_returns_reviewed(tmp_path: Path):
    """COMMITTED 章 reopen → 返回 dict 且 status=REVIEWED。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid, status="COMMITTED")
    svc = ChapterService(db_path)

    result = svc.reopen_for_repair(cid)
    assert result is not None
    assert result["status"] == "REVIEWED"

    # DB 直查确认真翻（不是只在返回值里翻）
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM chapters WHERE chapter_id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "REVIEWED"


def test_reopen_with_reason_writes_revision_note(tmp_path: Path):
    """reason 非空 → plan_json.revision_note = 'reopen: <reason>'。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid, status="COMMITTED")
    svc = ChapterService(db_path)

    result = svc.reopen_for_repair(cid, reason="打脸段落与前文矛盾")
    assert result is not None
    assert result["status"] == "REVIEWED"
    assert result["plan_json"]["revision_note"] == "reopen: 打脸段落与前文矛盾"


def test_reopen_reason_appends_to_existing_note(tmp_path: Path):
    """已有 revision_note 时换行拼接，不覆盖历史意见。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    cid = _make_chapter(
        db_path, pid, status="COMMITTED", plan_json={"revision_note": "旧意见"}
    )
    svc = ChapterService(db_path)

    result = svc.reopen_for_repair(cid, reason="字数欠带")
    assert result is not None
    note = result["plan_json"]["revision_note"]
    assert note.startswith("旧意见")
    assert "reopen: 字数欠带" in note
    # 旧意见保留且在新意见之前
    assert note.index("旧意见") < note.index("reopen:")


def test_reopen_other_plan_keys_preserved(tmp_path: Path):
    """reopen + reason 不破坏 plan_json 其它键。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    cid = _make_chapter(
        db_path, pid, status="COMMITTED", plan_json={"chapter_goal": "目标"}
    )
    svc = ChapterService(db_path)

    result = svc.reopen_for_repair(cid, reason="节奏问题")
    assert result is not None
    assert result["plan_json"]["chapter_goal"] == "目标"
    assert result["plan_json"]["revision_note"] == "reopen: 节奏问题"


def test_reopen_blank_reason_leaves_note_untouched(tmp_path: Path):
    """reason 为 None / 空白 → 不动 revision_note。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid, status="COMMITTED")
    svc = ChapterService(db_path)

    result = svc.reopen_for_repair(cid, reason=None)
    assert result is not None
    assert "revision_note" not in result["plan_json"]

    cid2 = _make_chapter(db_path, pid, number=2, status="COMMITTED")
    result2 = svc.reopen_for_repair(cid2, reason="   ")
    assert result2 is not None
    assert "revision_note" not in result2["plan_json"]


# ---------------------------------------------------------------------------
# 通用 PATCH 借道封死（2026-09-26 收尾批次）：update() 不再放行 COMMITTED→REVIEWED
# ---------------------------------------------------------------------------


def test_update_committed_to_reviewed_blocked(tmp_path: Path):
    """update()（PATCH 通用路径）COMMITTED→REVIEWED → ChapterTransitionError。

    该边虽在 ALLOWED_NEXT 白名单内，但保留给 reopen_for_repair 专用；通用 update
    无活动 run 守卫与返修语义，放行即旁路（守卫先例：create_draft 绕白名单直写）。
    突变验证：撤掉 update() 内的显式拦截 → 本测试必红。
    """
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid, status="COMMITTED")
    svc = ChapterService(db_path)

    with pytest.raises(ChapterTransitionError) as exc_info:
        svc.update(cid, ChapterUpdate(status="REVIEWED"))
    assert exc_info.value.current == "COMMITTED"
    assert exc_info.value.target == "REVIEWED"

    # 状态不得被改写
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM chapters WHERE chapter_id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "COMMITTED"


def test_update_other_legal_transitions_unaffected(tmp_path: Path):
    """守卫只锁 COMMITTED→REVIEWED 这一条边：其余白名单边照常走 update()。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid, status="DRAFTED")
    svc = ChapterService(db_path)

    result = svc.update(cid, ChapterUpdate(status="REVIEWED"))
    assert result is not None
    assert result["status"] == "REVIEWED"


def test_reopen_still_reaches_reviewed_after_update_guard(tmp_path: Path):
    """update() 加守卫后 reopen 专用直写不受影响（专用入口不回归）。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid, status="COMMITTED")
    svc = ChapterService(db_path)

    result = svc.reopen_for_repair(cid, reason="守卫后回归")
    assert result is not None
    assert result["status"] == "REVIEWED"
    assert result["plan_json"]["revision_note"] == "reopen: 守卫后回归"


# ---------------------------------------------------------------------------
# 非法路径：非 COMMITTED 状态 / 不存在
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["PLANNED", "DRAFTED", "REVIEWED", "RELEASED"])
def test_reopen_non_committed_rejected(tmp_path: Path, status: str):
    """非 COMMITTED 状态 → ChapterTransitionError。

    DRAFTED 单独强调：DRAFTED→REVIEWED 本身是合法推进边，reopen 必须显式拦截
    而不能借道 update() 的白名单放行。
    """
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid, status=status)
    svc = ChapterService(db_path)

    with pytest.raises(ChapterTransitionError) as exc_info:
        svc.reopen_for_repair(cid)
    assert exc_info.value.current == status
    assert exc_info.value.target == "REVIEWED"


def test_reopen_unknown_chapter_returns_none(tmp_path: Path):
    """chapter 不存在 → None（router 转 404）。"""
    settings = _make_settings(tmp_path)
    svc = ChapterService(str(settings.db_path))
    assert svc.reopen_for_repair("ch_nope") is None


# ---------------------------------------------------------------------------
# ALLOWED_NEXT 回归：既有边不变
# ---------------------------------------------------------------------------


def test_allowed_next_committed_keeps_original_edges():
    """回归：COMMITTED 原有三边（PLANNED / COMMITTED / RELEASED）仍在。"""
    assert {"PLANNED", "COMMITTED", "RELEASED"} <= ALLOWED_NEXT["COMMITTED"]


def test_allowed_next_committed_only_adds_reviewed():
    """COMMITTED 的后继集合 = 原有三边 + REVIEWED，无其它新边。"""
    assert ALLOWED_NEXT["COMMITTED"] == {
        "PLANNED",
        "COMMITTED",
        "RELEASED",
        "REVIEWED",
    }


def test_allowed_next_other_states_unchanged():
    """回归：其余四状态的白名单与既有口径逐字一致（重开批次不得顺手动它们）。"""
    assert ALLOWED_NEXT["PLANNED"] == {"PLANNED", "DRAFTED"}
    assert ALLOWED_NEXT["DRAFTED"] == {"PLANNED", "DRAFTED", "REVIEWED"}
    assert ALLOWED_NEXT["REVIEWED"] == {
        "PLANNED",
        "DRAFTED",
        "REVIEWED",
        "COMMITTED",
    }
    assert ALLOWED_NEXT["RELEASED"] == {"PLANNED", "RELEASED"}
    assert set(ALLOWED_NEXT.keys()) == {
        "PLANNED",
        "DRAFTED",
        "REVIEWED",
        "COMMITTED",
        "RELEASED",
    }
