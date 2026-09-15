"""0028 大纲槽（chapters.outline_json）：策展大纲 → planner 输入链路。

缺陷背景（2026-09-15）：改造前 ``chapters.plan_json`` 一名两用（init 写大纲、
chapter-plan 白名单整体覆盖成计划），而 ``build_director_input`` 的 chapter 段
从不注入 ``chapter_goal / key_beats``——大纲**从未抵达 planner**，planner 只能顺着
story state 惯性自推计划。本文件钉住修复后的三条契约：

1. 取值口径：``outline_json`` 非空 → 用它；为空 → 回落 ``plan_json`` 同名字段；
2. 注入事实：``payload["chapter"]["outline"]`` 必须带出大纲（缺它 = 缺陷复发）；
3. 缓存纪律：改大纲必须让 director 装配缓存 miss（否则改了也不生效——硬规则 2）；
4. 生命周期：chapter-plan 的 ``save_plan`` 只覆盖 ``plan_json``，**不碰** ``outline_json``。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

OUTLINE = {
    "chapter_goal": "林策以账房之眼当众拆穿退婚案的程序瑕疵，拿回主动权",
    "key_beats": ["退婚现场第一次开口", "当众念出嫁妆单副本", "裴元绍面色骤变"],
    "expected_role": "setup",
}


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
            "status, created_at, updated_at) "
            "VALUES (?, '项目', NULL, NULL, NULL, 'ACTIVE', ?, ?)",
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(
    db_path: Path,
    project_id: str,
    *,
    plan_json: dict | None = None,
    outline_json: dict | None = None,
) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "outline_json, status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, 1, '第一章', ?, ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)",
            (
                cid,
                project_id,
                json.dumps(plan_json or {}, ensure_ascii=False),
                None if outline_json is None else json.dumps(outline_json, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _set_outline(db_path: Path, chapter_id: str, outline: dict | None) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapters SET outline_json = ? WHERE chapter_id = ?",
            (None if outline is None else json.dumps(outline, ensure_ascii=False), chapter_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. 取值口径
# ---------------------------------------------------------------------------


def test_resolve_prefers_outline_json(tmp_path: Path):
    from packages.core.context_engine.builders_common import resolve_chapter_outline

    chapter = {
        "outline_json": dict(OUTLINE),
        "plan_json": {"chapter_goal": "planner 现推的目标", "key_beats": ["x"]},
    }
    assert resolve_chapter_outline(chapter) == OUTLINE


def test_resolve_falls_back_to_plan_json_when_outline_absent(tmp_path: Path):
    from packages.core.context_engine.builders_common import resolve_chapter_outline

    chapter = {
        "outline_json": {},
        "plan_json": {
            "chapter_goal": "旧项目 plan_json 里的大纲",
            "key_beats": ["a", "b"],
            "core_conflict": "冲突",
            # 执行面字段不应混进 outline
            "character_changes_planned": [{"character_id": "char_x"}],
            "deviations": ["d"],
        },
    }
    outline = resolve_chapter_outline(chapter)
    assert outline["chapter_goal"] == "旧项目 plan_json 里的大纲"
    assert outline["key_beats"] == ["a", "b"]
    assert outline["core_conflict"] == "冲突"
    assert "character_changes_planned" not in outline
    assert "deviations" not in outline


def test_resolve_returns_empty_when_both_absent():
    from packages.core.context_engine.builders_common import resolve_chapter_outline

    assert resolve_chapter_outline({}) == {}
    assert resolve_chapter_outline(None) == {}
    assert resolve_chapter_outline({"outline_json": {}, "plan_json": {}}) == {}


# ---------------------------------------------------------------------------
# 2. 注入事实（缺陷复发的红测试：删掉 payload["chapter"]["outline"] 必红）
# ---------------------------------------------------------------------------


def test_director_payload_carries_outline_from_outline_json(tmp_path: Path):
    from packages.core.context_engine.builders import _cache_reset, build_director_input

    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, outline_json=OUTLINE)

    out = build_director_input(db_path, pid, cid, "意图")
    assert out["chapter"]["outline"]["chapter_goal"] == OUTLINE["chapter_goal"]
    assert out["chapter"]["outline"]["key_beats"] == OUTLINE["key_beats"]


def test_director_payload_outline_falls_back_for_legacy_project(tmp_path: Path):
    from packages.core.context_engine.builders import _cache_reset, build_director_input

    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    # 存量形态：无 outline_json，大纲只在 plan_json 里
    cid = _insert_chapter(
        db_path, pid,
        plan_json={"chapter_goal": "存量大纲目标", "key_beats": ["b1"]},
    )

    out = build_director_input(db_path, pid, cid, "意图")
    assert out["chapter"]["outline"]["chapter_goal"] == "存量大纲目标"


# ---------------------------------------------------------------------------
# 3. 缓存纪律：改大纲必须 miss
# ---------------------------------------------------------------------------


def test_outline_change_invalidates_director_cache(tmp_path: Path):
    from packages.core.context_engine.builders import _cache_reset, build_director_input

    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, outline_json=OUTLINE)

    out1 = build_director_input(db_path, pid, cid, "意图")
    assert out1["chapter"]["outline"]["chapter_goal"] == OUTLINE["chapter_goal"]

    # 同状态重跑 → 命中缓存（内容一致、非同一对象）
    out2 = build_director_input(db_path, pid, cid, "意图")
    assert out2 == out1
    assert out2 is not out1

    # 改大纲（state_version / plan_json 不变）→ 键的 outline 维度变化 → 必须 miss
    _set_outline(db_path, cid, {**OUTLINE, "chapter_goal": "换掉的大纲目标"})
    out3 = build_director_input(db_path, pid, cid, "意图")
    assert out3["chapter"]["outline"]["chapter_goal"] == "换掉的大纲目标"
    assert out3 != out1


# ---------------------------------------------------------------------------
# 4. 生命周期：chapter-plan 不写 outline_json
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["PLANNED"])
def test_save_plan_does_not_touch_outline_json(tmp_path: Path, status: str):
    from packages.workflows.chapter_plan.pipeline import _save_plan_node

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, outline_json=OUTLINE)

    _save_plan_node({
        "db_path": str(db_path),
        "project_id": pid,
        "chapter_id": cid,
        "director_planner_output": {
            "chapter_goal": "planner 重新推出来的目标",
            "core_conflict": "冲突",
            "turning_point": "转折",
            "expected_role": "escalation",
            "expected_word_count": 2500,
            "key_beats": [],
        },
    })

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json, outline_json FROM chapters WHERE chapter_id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    plan = json.loads(row["plan_json"])
    assert plan["chapter_goal"] == "planner 重新推出来的目标", "计划面应被覆盖"
    assert json.loads(row["outline_json"]) == OUTLINE, "大纲面不得被 chapter-plan 污染"


# ---------------------------------------------------------------------------
# 5. 领域层：outline_json 可经 ChapterUpdate 读写
# ---------------------------------------------------------------------------


def test_service_roundtrip_outline_json(tmp_path: Path):
    from packages.domain.chapter.models import ChapterUpdate
    from packages.domain.chapter.service import ChapterService

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)

    svc = ChapterService(db_path)
    assert svc.get(cid)["outline_json"] is None

    svc.update(cid, ChapterUpdate(outline_json=dict(OUTLINE)))
    assert svc.get(cid)["outline_json"] == OUTLINE

    # 显式 None → 清空（回落路径重新生效）
    svc.update(cid, ChapterUpdate(outline_json=None))
    assert svc.get(cid)["outline_json"] is None
