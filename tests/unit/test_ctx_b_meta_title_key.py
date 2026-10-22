"""缓存键第 5 次：项目元信息（name/genre/premise）+ 本章标题（dev-b 批次二十一 P0-1）。

缺陷形状（硬规则 2「凡进 payload 的装配参数必须入键」的**同形状第 5 次**）：

- 0028 ``outline_json`` 进 payload 却没进键 ⇒ 改大纲后同 state_version 脏命中；
- 2026-09-16 ``prompt_label`` 硬编码 ⇒ 升版后键与 payload 脱节；
- 2026-10-16 E1 ``chars.core_json`` 进 payload 却没进键 ⇒ 改角色设定后脏命中；
- 2026-10-22 批次十九 ``author_style_samples.samples`` 进 payload 却没进键；
- **本批 A** ``projects.name/genre/premise`` 进 **director** payload 却没进键；
- **本批 B** ``chapters.title`` 进 **writer** payload 却没进键。

失效链（逐条可复算）
--------------------
- **A**：director 装配把 ``project["name"]/genre/premise`` 写进 payload 的
  ``project`` 段，且这三个字段**被 prompt 契约显式承诺**给模型
  （``docs/agents/prompts/director_planner-v4.md`` §5：
  ``"project": { "project_id", "name", "genre", "premise" }``）；
  作者改项目前提走 ``PATCH /api/projects/{id}`` ⇒
  ``packages/domain/project/service.py`` 的 ``UPDATE projects`` **只 commit()、
  不推 ``story_states.state_version``** ⇒ 旧键无任何维度会因它而变 ⇒
  同 ``state_version`` + 同 ``chapter_no`` 重跑该章**命中旧装配** ⇒
  **planner 拿着改动前的 premise 排计划，零告警**。
- **B**：writer 装配把 ``chapter.get("title")`` 写进 payload 的 ``chapter`` 段，
  章标题**被 prompt 契约显式承诺**（``docs/agents/prompts/writer-v5.md`` §5 与示例
  ``"title": "夜叩青石"``）；改章标题走 ``PATCH /api/chapters/{id}`` ⇒
  ``packages/domain/chapter/service.py`` 的 ``UPDATE chapters`` 同样**不推
  state_version** ⇒ 同 state_version 重跑该章**命中旧装配** ⇒
  **正文按改动前的标题写，零告警**。

判别口径
--------
断言打在 **payload 的实际内容**上（``project.premise`` / ``chapter.title``），
不是「键不等」——后者是结果，前者才是机制（AGENTS 坑区「断言了结果 ≠ 判别了
机制」）。反向不变量（样例/元信息未变时**必须命中**）同时钉住，防「过度失效」
把缓存改成永不命中。

零真实 LLM、零内容仓接触、零 dev 库（``tmp_path`` 临时库 + 合成行）。
"""

from __future__ import annotations

from pathlib import Path

import packages.core.context_engine.director_input as di_mod
import packages.core.context_engine.writer_input as wi_mod
from packages.core.context_engine import build_director_input, build_writer_input
from packages.core.context_engine.cache import _cache_reset
from packages.core.context_engine.preview import preview_context
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


# ---------------------------------------------------------------------------
# 夹具（形状取自 DDL，**不是**「跑一下看返回什么」抄来的）
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path, name: str = "项目甲", premise: str = "旧前提") -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, ?, ?, '科幻', NULL, "
            "'ACTIVE', ?, ?)",
            (pid, name, premise, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, pid: str, title: str = "旧标题") -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "plan_json, status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, 1, ?, '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, title, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _set_project_premise(db_path: Path, pid: str, premise: str) -> None:
    """改项目前提（复刻 ``PATCH /api/projects/{id}`` 的写路径形状）。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE projects SET premise = ?, updated_at = ? WHERE project_id = ?",
            (premise, now_iso(), pid),
        )
        conn.commit()
    finally:
        conn.close()


def _set_project_name(db_path: Path, pid: str, name: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE projects SET name = ?, updated_at = ? WHERE project_id = ?",
            (name, now_iso(), pid),
        )
        conn.commit()
    finally:
        conn.close()


def _set_chapter_title(db_path: Path, cid: str, title: str) -> None:
    """改章标题（复刻 ``PATCH /api/chapters/{id}`` 的写路径形状）。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE chapters SET title = ?, updated_at = ? WHERE chapter_id = ?",
            (title, now_iso(), cid),
        )
        conn.commit()
    finally:
        conn.close()


def _spy_uncached(monkeypatch, module, attr: str) -> dict[str, int]:
    """统计 module.attr 的真实调用次数（命中返回深拷贝 ⇒ 不能用对象身份判定）。"""
    real = getattr(module, attr)
    counter = {"n": 0}

    def counting(*args, **kwargs):
        counter["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, attr, counting)
    return counter


# ---------------------------------------------------------------------------
# A：项目元信息（name / genre / premise）→ director 键
# ---------------------------------------------------------------------------


def test_changing_project_premise_misses_director_cache(tmp_path, monkeypatch):
    """改项目前提 ⇒ 同 state_version 下重建装配**必须重装**且 payload 带上新前提。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path, premise="旧前提")
    cid = _insert_chapter(db_path, pid)
    _cache_reset()
    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    first = build_director_input(db_path, pid, cid, "意图")
    assert first["project"]["premise"] == "旧前提"
    assert calls["n"] == 1

    _set_project_premise(db_path, pid, "新前提")

    second = build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 2, (
        "改 premise 后必须重装（不得命中旧装配）——planner 会拿到改动前的项目前提"
    )
    assert second["project"]["premise"] == "新前提", (
        f"payload 必须带上新前提，实得 {second['project']!r}"
    )


def test_unchanged_project_meta_hits_director_cache(tmp_path, monkeypatch):
    """项目元信息未变 ⇒ **必须命中**（反向不变量：防「过度失效」）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path, premise="前提")
    cid = _insert_chapter(db_path, pid)
    _cache_reset()
    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    build_director_input(db_path, pid, cid, "意图")
    build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 1, (
        f"元信息未变时第二次必须命中缓存（实得 uncached 调用 {calls['n']} 次）"
    )


# ---------------------------------------------------------------------------
# B：本章标题 → writer 键
# ---------------------------------------------------------------------------


def test_changing_chapter_title_misses_writer_cache(tmp_path, monkeypatch):
    """改章标题 ⇒ 同 state_version 下重建装配**必须重装**且 payload 带上新标题。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, title="旧标题")
    _cache_reset()
    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    first = build_writer_input(db_path, cid, {})
    assert first["chapter"]["title"] == "旧标题"
    assert calls["n"] == 1

    _set_chapter_title(db_path, cid, "新标题")

    second = build_writer_input(db_path, cid, {})
    assert calls["n"] == 2, "改标题后必须重装（不得命中旧装配）"
    assert second["chapter"]["title"] == "新标题", (
        f"payload 必须带上新标题，实得 {second['chapter']!r}"
    )


def test_unchanged_chapter_title_hits_writer_cache(tmp_path, monkeypatch):
    """章标题未变 ⇒ **必须命中**（反向不变量：防「过度失效」）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, title="标题")
    _cache_reset()
    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    build_writer_input(db_path, cid, {})
    build_writer_input(db_path, cid, {})
    assert calls["n"] == 1, (
        f"标题未变时第二次必须命中缓存（实得 uncached 调用 {calls['n']} 次）"
    )


# ---------------------------------------------------------------------------
# preview 路径也必须吃到新维度（preview 与生产共用这两个装配器）
# ---------------------------------------------------------------------------


def _preview_project_name(result: dict) -> str:
    """从 preview 结果的 L0 层里取 project 条目的 name。"""
    l0 = next(x for x in result["layers"] if x["id"] == "L0")
    item = next(x for x in l0["items"] if x["kind"] == "project")
    return str(item["name"])


def test_preview_path_also_picks_up_the_new_project_meta_dimension(tmp_path):
    """preview 路径（``namespace="preview"``）**同样**必须吃到新维度。

    判别方式**行为化**：改项目名后再次预览，L0 的 project 条目名必须变成新名——
    若新维度没进 preview 用的那条键，preview 会命中旧装配 ⇒ 名字仍是旧的。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path, name="旧名")
    cid = _insert_chapter(db_path, pid)
    _cache_reset()

    first = preview_context(db_path, pid, cid, author_intent="")
    assert _preview_project_name(first) == "旧名"

    _set_project_name(db_path, pid, "新名")

    second = preview_context(db_path, pid, cid, author_intent="")
    assert _preview_project_name(second) == "新名", (
        "preview 路径也必须因项目名变化而重装（它和生产共用同一套装配器）"
    )
