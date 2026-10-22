"""作者文风样例（``author_style_samples``）的 writer 缓存键维度（dev-b 批次十九）。

缺陷形状（硬规则 2「凡进 payload 的装配参数必须入键」的**同形状第 4 次**）：

- 0028 ``outline_json`` 进 payload 却没进键 ⇒ 改大纲后同 state_version 脏命中；
- 2026-09-16 ``prompt_label`` 硬编码 ⇒ 升版后键与 payload 脱节；
- 2026-10-16 E1 ``chars.core_json`` 进 payload 却没进键 ⇒ 改角色设定后脏命中；
- **本批** ``author_style_samples.samples`` 进 writer payload 却没进键。

为什么它会脏命中（逐条可复算）：

- 注入点：``writer_input.py::_build_writer_input_uncached`` 把项目最近 ≤2 篇
  「作者本人散文样例」写进 payload 的 ``author_style_samples.samples`` 段
  （数据源 ``builders_common/summaries.py::_author_style_samples`` 的
  ``SELECT sample_id,title,content FROM author_style_samples WHERE project_id=?``）。
- 写表端点：``api/routers/author_style_samples.py`` 的 INSERT（:164-172）/
  DELETE（:193-198）**只 ``commit()`` 该表，不推 ``story_states.state_version``**。
- 键维度：修前 writer 键 20 元里**没有任何一维**会因样例增删而变 ⇒
  同 ``state_version`` + 同 ``chapter_no`` 重跑该章 ⇒ **命中旧装配** ⇒
  正文不按作者文风写（且 ``style_constraints.genre_style`` 的 ``priority``
  标记不同步），**全程零告警**。

本文件的判别口径（**断言打在 payload 的样例内容上，不打「键不等」**）：
「键不等」是结果，「payload 里看得见新样例 / 看不见旧样例」才是机制——
后者才会在「维度被撤掉」时真的变红（``tests/mutation_specs/q1_style_sample_key.py``
的 ``STYLE-KEY-1``）。

零真实 LLM、零内容仓接触、零 dev 库（``tmp_path`` 临时库 + 合成行）。
"""

from __future__ import annotations

from pathlib import Path

import packages.core.context_engine.writer_input as wi_mod
from packages.core.context_engine import build_writer_input
from packages.core.context_engine.cache import _cache_reset
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


# ---------------------------------------------------------------------------
# 夹具（形状取自 DDL / service docstring，**不是**「跑一下看返回什么」抄来的）
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    """建临时库并跑完整迁移链（含 0008 的 ``author_style_samples``）。"""
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path, name: str = "文风项目") -> str:
    """插一行项目（projects 表最小列集）。"""
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, pid: str, number: int = 1) -> str:
    """插一行章节（chapters 表最小列集）。"""
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "plan_json, status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, 'C', '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, number, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_style_sample(
    db_path: Path,
    pid: str,
    title: str,
    content: str,
    *,
    created_at: str = "2026-10-01T00:00:00",
) -> str:
    """插一条作者文风样例（``author_style_samples`` 表的 DDL 列集）。"""
    sid = new_id("asty")
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO author_style_samples "
            "(sample_id, project_id, title, content, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, pid, title, content, created_at, created_at),
        )
        conn.commit()
    finally:
        conn.close()
    return sid


def _delete_style_sample(db_path: Path, sid: str) -> None:
    """删一条作者文风样例（与 router 的 DELETE 端点同一条路径）。"""
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM author_style_samples WHERE sample_id = ?", (sid,))
        conn.commit()
    finally:
        conn.close()


def _touch_style_sample_updated_at(db_path: Path, sid: str) -> None:
    """只 bump ``updated_at``（内容一字未动）——反向不变量专用。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE author_style_samples SET updated_at = ? WHERE sample_id = ?",
            ("2026-10-02T00:00:00", sid),
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


def _samples_of(payload: dict) -> list[dict]:
    """payload 里的样例列表（``author_style_samples.samples``）。"""
    return list((payload.get("author_style_samples") or {}).get("samples") or [])


# ---------------------------------------------------------------------------
# 判别用例
# ---------------------------------------------------------------------------


def test_adding_style_sample_misses_cache_and_payload_shows_it(tmp_path, monkeypatch):
    """加一条样例 ⇒ 同 state_version 下重建装配**必须重装**且 payload 看得见新样例。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1)
    _cache_reset()
    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    first = build_writer_input(db_path, cid, {})
    assert _samples_of(first) == [], "无样例时 payload 的样例段应为空"
    assert calls["n"] == 1, "首次装配应走 uncached 一次"

    _insert_style_sample(db_path, pid, "文风样例甲", "短句多，顿号少。")

    second = build_writer_input(db_path, cid, {})
    assert calls["n"] == 2, (
        "加样例后必须重装（不得命中旧装配）——writer 会拿到改动前的旧文风"
    )
    samples = _samples_of(second)
    assert [s["title"] for s in samples] == ["文风样例甲"], (
        f"payload 必须带上新样例，实得 {samples!r}"
    )
    assert samples[0]["excerpt"] == "短句多，顿号少。"


def test_deleting_style_sample_misses_cache_and_payload_drops_it(tmp_path, monkeypatch):
    """删一条样例 ⇒ 重建装配**必须重装**且 payload 里不再有它。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1)
    sid = _insert_style_sample(db_path, pid, "文风样例甲", "短句多，顿号少。")
    _cache_reset()
    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    first = build_writer_input(db_path, cid, {})
    assert [s["title"] for s in _samples_of(first)] == ["文风样例甲"]
    assert calls["n"] == 1

    _delete_style_sample(db_path, sid)

    second = build_writer_input(db_path, cid, {})
    assert calls["n"] == 2, "删样例后必须重装（不得命中旧装配）"
    assert _samples_of(second) == [], (
        f"删掉的样例不得继续留在 payload 里，实得 {_samples_of(second)!r}"
    )


def test_editing_style_sample_content_misses_cache(tmp_path, monkeypatch):
    """改样例**内容** ⇒ 必须重装且 payload 里的 excerpt 变新（content 在指纹列集里）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1)
    sid = _insert_style_sample(db_path, pid, "文风样例甲", "旧文风。")
    _cache_reset()
    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    build_writer_input(db_path, cid, {})
    assert calls["n"] == 1

    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE author_style_samples SET content = ? WHERE sample_id = ?",
            ("新文风，长句多。", sid),
        )
        conn.commit()
    finally:
        conn.close()

    second = build_writer_input(db_path, cid, {})
    assert calls["n"] == 2, "改样例内容后必须重装"
    assert [s["excerpt"] for s in _samples_of(second)] == ["新文风，长句多。"]


def test_unchanged_style_samples_still_hit_cache(tmp_path, monkeypatch):
    """样例不变 ⇒ **必须命中**（反向不变量：防「过度失效」把缓存改成永不命中）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1)
    _insert_style_sample(db_path, pid, "文风样例甲", "短句多，顿号少。")
    _cache_reset()
    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    first = build_writer_input(db_path, cid, {})
    second = build_writer_input(db_path, cid, {})
    assert calls["n"] == 1, (
        f"样例未变时第二次必须命中缓存（实得 uncached 调用 {calls['n']} 次）"
    )
    assert _samples_of(first) == _samples_of(second)


def test_touching_only_updated_at_still_hits_cache(tmp_path, monkeypatch):
    """只 bump ``updated_at``（内容一字未动）⇒ 仍须命中（防拿记账时间戳当指纹）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1)
    sid = _insert_style_sample(db_path, pid, "文风样例甲", "短句多，顿号少。")
    _cache_reset()
    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    build_writer_input(db_path, cid, {})
    assert calls["n"] == 1

    _touch_style_sample_updated_at(db_path, sid)

    build_writer_input(db_path, cid, {})
    assert calls["n"] == 1, "只改 updated_at 不应导致重装（时间戳不进指纹）"


def test_other_project_style_samples_do_not_affect_key(tmp_path, monkeypatch):
    """别的项目加样例 ⇒ 本项目的键不得变（指纹必须按 project_id 归组）。"""
    db_path = _fresh_db(tmp_path)
    pid_a = _insert_project(db_path, "项目甲")
    pid_b = _insert_project(db_path, "项目乙")
    cid_b = _insert_chapter(db_path, pid_b, 1)
    _cache_reset()
    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    build_writer_input(db_path, cid_b, {})
    assert calls["n"] == 1

    _insert_style_sample(db_path, pid_a, "别人的样例", "与本项目无关。")

    build_writer_input(db_path, cid_b, {})
    assert calls["n"] == 1, "别的项目的样例变化不得让本项目重装"
