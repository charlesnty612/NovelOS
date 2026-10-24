"""前章草稿内容维度：前章**重出稿** ⇒ 装配缓存必须 miss（R-2，2026-10-24）。

缺陷形状（硬规则 2「凡进 payload 的装配参数必须入键」的同形状第 8 次）
------------------------------------------------------------------
前一章的**最新草稿正文**进两条装配 payload：

* director：``previous_chapter_tail``（``_previous_chapter_tail``：前章尾 300 字 +
  前章 ``chapter_id`` / ``number``）——planner 用以规划下章衔接；
* writer：``recent_prose.last_chapter_excerpt``（``_recent_prose_tail``：尾 500 字）
  ——writer 用以衔接上一章正文。

而**草稿写路径不推** ``story_states.state_version``（``drafts`` 表由
``ChapterService.create_draft`` 写；只有 commit 才推版本）⇒ 前章**重出稿但未
commit**时（同 ``state_version`` + 同 ``chapter_no`` 重跑该章）旧键里**没有任何
维度**会变 ⇒ **命中旧装配** ⇒ planner / writer 拿着**改动前的前章尾段**排计划、
写正文，**零告警**。

⇒ 修复 = **单列一个内容指纹维度** ``prev_draft_fp``
（:func:`packages.core.context_engine.cache.previous_draft_fingerprint`），
director / writer **同源同函数** ⇒ 两侧指纹值逐字相同。

⚠️ **为什么不走「写路径推 version」**（本批拍板）
草稿写频次极高（每次重写 / 改稿都写一行）⇒ 若 drafts 写路径推
``state_version``，则**每一次改稿都会让整个项目所有章的装配缓存全部失效**
（缓存命中率塌方）⇒ 走**键维度**：粒度 = 前章草稿内容，只有前章正文真变了才 miss。

判别口径
--------
* **断言打在 payload 的实际内容上**（director 的 ``previous_chapter_tail.tail_text`` /
  writer 的 ``recent_prose.last_chapter_excerpt``），不是「键不等」——后者是结果。
* **真实装配次数**用 ``_spy_uncached`` 数（命中返回**深拷贝** ⇒ 不能用对象身份判定）。
* **反向不变量**：什么都不改 ⇒ 必须命中（防「过度失效」把缓存改成永不命中）。
* **用裸 SQL 写草稿**（绕状态机 / 绕写路径）⇒ 只有键维度能救回来 ⇒ 撤维度必红。

零真实 LLM、零内容仓接触、零 dev 库（``tmp_path`` 临时库 + 合成行）。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import packages.core.context_engine.director_input as di_mod
import packages.core.context_engine.writer_input as wi_mod
from packages.core.context_engine import build_director_input, build_writer_input
from packages.core.context_engine.cache import (
    _cache_reset,
    _previous_draft_source,
    previous_draft_fingerprint,
)
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.story_state.service import StoryStateService

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

TARGET_CHAPTER_NO = 2
"""目标章（第 2 章）；前章 = 第 1 章。"""


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _exec(db_path: Path, sql: str, params: tuple = ()) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "ctx_d_prev_draft_key.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path) -> str:
    pid = f"prj_pdk_{uuid4().hex[:10]}"
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO projects (project_id, name, premise, genre, target_words, status,"
        " created_at, updated_at) VALUES (?, ?, '前提', '科幻', NULL, 'ACTIVE', ?, ?)",
        (pid, "前章草稿缓存键测试", now, now),
    )
    return pid


def _insert_chapter(db_path: Path, pid: str, number: int, title: str = "章") -> str:
    cid = new_id("ch")
    now = now_iso()
    _exec(
        db_path,
        "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status,"
        " visibility, who_knows, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)",
        (cid, pid, number, title, now, now),
    )
    return cid


def _insert_draft(db_path: Path, chapter_id: str, content: str, *, version: int = 1) -> str:
    """**裸 SQL** 插草稿（绕 ``ChapterService.create_draft`` 写路径 ⇒ 不推
    ``state_version``）——这样「键维度」是唯一可能救回缓存的机制。"""
    did = new_id("dr")
    _exec(
        db_path,
        "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by,"
        " prompt_version, model_id, created_at)"
        " VALUES (?, ?, ?, ?, 'agent:writer:test', 'writer:v1', NULL, ?)",
        (did, chapter_id, version, content, now_iso()),
    )
    return did


def _seed(tmp_path: Path) -> dict:
    db = _fresh_db(tmp_path)
    pid = _insert_project(db)
    ch1 = _insert_chapter(db, pid, 1, title="第1章")
    ch2 = _insert_chapter(db, pid, TARGET_CHAPTER_NO, title="第2章")
    _insert_draft(db, ch1, "旧前章正文：夜色压着城楼，林昭按住刀柄。")
    # 生产写路径：init_genesis 落 state_deltas + commits + story_states v1。
    StoryStateService(db).init_genesis(pid, ch1)
    return {"db": db, "project": pid, "ch1": ch1, "target": ch2}


def _spy_uncached(monkeypatch, module, attr: str) -> dict[str, int]:
    """统计 module.attr 的真实调用次数（命中返回深拷贝 ⇒ 不能用对象身份判定）。"""
    real = getattr(module, attr)
    counter = {"n": 0}

    def counting(*args, **kwargs):
        counter["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, attr, counting)
    return counter


def _director_tail(payload: dict) -> str:
    return str((payload.get("previous_chapter_tail") or {}).get("tail_text") or "")


def _writer_tail(payload: dict) -> str:
    return str((payload.get("recent_prose") or {}).get("last_chapter_excerpt") or "")


# ---------------------------------------------------------------------------
# 场景 1：director 侧——前章重出稿 ⇒ 必须重装且尾段换成新稿
# ---------------------------------------------------------------------------


def test_new_previous_draft_misses_director_cache_and_payload_shows_new_tail(
    tmp_path, monkeypatch,
):
    """前章重出稿 ⇒ director 必须重装，且 ``previous_chapter_tail`` 带上新稿。"""
    ids = _seed(tmp_path)
    db, pid, target = ids["db"], ids["project"], ids["target"]
    _cache_reset()
    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    first = build_director_input(db, pid, target, "意图")
    assert "旧前章正文" in _director_tail(first), "夹具前提：前章尾段应来自第 1 章的草稿"
    assert calls["n"] == 1

    # 前章重出稿（裸 SQL ⇒ 不推 state_version ⇒ 键维度是唯一救命机制）
    _insert_draft(
        db, ids["ch1"], "新前章正文：晨光压在瓦上，林昭把刀插回鞘里。", version=2,
    )

    second = build_director_input(db, pid, target, "意图")
    assert calls["n"] == 2, (
        "前章重出稿后必须重装（不得命中旧装配）——planner 会拿到改动前的前章尾段"
    )
    assert "新前章正文" in _director_tail(second), (
        f"payload 的前章尾段必须换成新稿，实得 {_director_tail(second)!r}"
    )


# ---------------------------------------------------------------------------
# 场景 2：writer 侧——前章重出稿 ⇒ 必须重装且 recent_prose 换成新稿
# ---------------------------------------------------------------------------


def test_new_previous_draft_misses_writer_cache_and_payload_shows_new_tail(
    tmp_path, monkeypatch,
):
    """前章重出稿 ⇒ writer 必须重装，且 ``recent_prose`` 尾段换成新稿。"""
    ids = _seed(tmp_path)
    db, target = ids["db"], ids["target"]
    _cache_reset()
    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    first = build_writer_input(db, target, {})
    assert "旧前章正文" in _writer_tail(first), "夹具前提：上一章尾段应来自第 1 章的草稿"
    assert calls["n"] == 1

    _insert_draft(
        db, ids["ch1"], "新前章正文：晨光压在瓦上，林昭把刀插回鞘里。", version=2,
    )

    second = build_writer_input(db, target, {})
    assert calls["n"] == 2, (
        "前章重出稿后必须重装（不得命中旧装配）——正文会沿用改动前的上一章尾段"
    )
    assert "新前章正文" in _writer_tail(second), (
        f"payload 的上一章尾段必须换成新稿，实得 {_writer_tail(second)!r}"
    )


# ---------------------------------------------------------------------------
# 反向不变量：什么都不改 ⇒ 必须命中（防「过度失效」）
# ---------------------------------------------------------------------------


def test_unchanged_previous_draft_hits_director_cache(tmp_path, monkeypatch):
    """什么都不改 ⇒ 第二次必须命中缓存（实得 uncached 调用 1 次）。"""
    ids = _seed(tmp_path)
    db, pid, target = ids["db"], ids["project"], ids["target"]
    _cache_reset()
    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    build_director_input(db, pid, target, "意图")
    build_director_input(db, pid, target, "意图")
    assert calls["n"] == 1, (
        f"输入未变时第二次必须命中缓存（实得 uncached 调用 {calls['n']} 次）"
    )


def test_unchanged_previous_draft_hits_writer_cache(tmp_path, monkeypatch):
    """writer 侧同款反向不变量。"""
    ids = _seed(tmp_path)
    db, target = ids["db"], ids["target"]
    _cache_reset()
    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    build_writer_input(db, target, {})
    build_writer_input(db, target, {})
    assert calls["n"] == 1, (
        f"输入未变时第二次必须命中缓存（实得 uncached 调用 {calls['n']} 次）"
    )


# ---------------------------------------------------------------------------
# 口径看守：指纹与消费方**同源**（指纹看的那一章 = payload 取的那一章）
# ---------------------------------------------------------------------------


def test_fingerprint_source_matches_payload_chapter(tmp_path):
    """指纹的数据来源必须与 payload 的前章**逐字同源**（防口径漂移）。

    防的是「指纹看 A 稿、payload 拿 B 稿」⇒ 指纹恒定而 payload 变 ⇒ 脏命中。
    """
    ids = _seed(tmp_path)
    db, pid, target = ids["db"], ids["project"], ids["target"]
    _cache_reset()

    payload = build_director_input(db, pid, target, "意图")
    payload_prev = payload["previous_chapter_tail"]
    source = _previous_draft_source(
        get_connection(db), pid, TARGET_CHAPTER_NO,
    )
    assert source is not None, "夹具前提：第 2 章应有前章（第 1 章）的最新草稿"
    assert source["chapter_id"] == payload_prev["chapter_id"], (
        "指纹与 payload 必须取同一章——否则指纹恒定而 payload 变 ⇒ 脏命中"
    )
    assert source["number"] == payload_prev["chapter_no"]
    assert source["content"].endswith(payload_prev["tail_text"]), (
        "指纹吃的是整篇正文，payload 取的是尾部 ⇒ 尾部必须是正文的后缀"
    )


def test_fingerprint_is_none_without_previous_chapter(tmp_path):
    """无前章 ⇒ 指纹落 ``"none"``（与消费方的空态同形）。"""
    ids = _seed(tmp_path)
    db, pid = ids["db"], ids["project"]
    fp = previous_draft_fingerprint(db, pid, 1)
    assert fp == "none", f"第 1 章无前章 ⇒ 指纹应为 'none'，实得 {fp!r}"


def test_fingerprint_changes_when_previous_draft_changes(tmp_path):
    """前章草稿内容变了 ⇒ 指纹必须变（内容级断言，不靠「键不等」）。"""
    ids = _seed(tmp_path)
    db, pid = ids["db"], ids["project"]
    before = previous_draft_fingerprint(db, pid, TARGET_CHAPTER_NO)
    _insert_draft(
        db, ids["ch1"], "新前章正文：晨光压在瓦上，林昭把刀插回鞘里。", version=2,
    )
    after = previous_draft_fingerprint(db, pid, TARGET_CHAPTER_NO)
    assert before != after, "前章草稿变了 ⇒ 指纹必须变"
    assert before != "none" and after != "none"
