"""V3.9 批次 1.4：context_engine 装配缓存的键维度 / 命名空间 / 深拷贝隔离。

覆盖（roadmap §批次 1.4 验收：「缓存键维度测试（换 author_intent/target_word_count 必 miss）」）：
1. director 键含 author_intent → 换意图必 miss；
2. director 键含 target_word_count → 换目标字数必 miss；
3. writer 键含 target_word_count → 换目标字数必 miss（word_band / scene target_words 跟随）；
4. preview dry-run 带独立命名空间 → 与生产同参数也互不命中；
5. preview 的默认口径（空意图 / 默认目标字数）不覆盖生产条目（作者意图不被静默丢弃）；
6. 命中返回深拷贝 → 调用方就地改写不污染缓存条目（director / writer 双向）。

判定"真装配"用 spy 统计 ``_build_*_uncached`` 调用次数，而非对象身份——
V3.9 批次 1.4 起命中返回深拷贝，``p1 is p2`` 恒假、不再是命中/未命中的有效信号。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.context_engine.builders import (
    _cache_reset,
    build_director_input,
    build_writer_input,
)
from packages.core.context_engine.cache import (
    _PREVIEW_CACHE_NAMESPACE,
    _cache_namespace_tag,
)
from packages.core.context_engine.preview import preview_context
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.wordcount import DEFAULT_TARGET_WORD_COUNT

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path, name: str = "项目") -> str:
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


def _insert_chapter(
    db_path: Path,
    pid: str,
    number: int,
    *,
    plan_json: str = "{}",
    content: str = "",
) -> str:
    """插入章节 + 一份 draft 行（与 test_v2_wave_c_retrieval.py 同范式）。"""
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, "
            "plan_json, status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, 'C', ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, number, plan_json, now, now),
        )
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "prompt_version, model_id, created_at) "
            "VALUES (?, ?, 1, ?, 'agent:writer:v1', 'writer:v1', 'mock/test', ?)",
            (new_id("dr"), cid, content, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _spy_uncached(monkeypatch, module, attr: str) -> dict[str, int]:
    """统计 module.attr 的真实调用次数（返回可变计数盒）。"""
    counter = {"n": 0}
    real = getattr(module, attr)

    def counting(*args, **kwargs):
        counter["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, attr, counting)
    return counter


# ---------------------------------------------------------------------------
# 1. director：author_intent 维度
# ---------------------------------------------------------------------------


def test_director_cache_miss_on_different_author_intent(tmp_path: Path, monkeypatch):
    from packages.core.context_engine import director_input as di_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "x"}')
    _cache_reset()

    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    a1 = build_director_input(db_path, pid, cid, "意图 A")
    b1 = build_director_input(db_path, pid, cid, "意图 B")
    assert a1["author_intent"]["raw"] == "意图 A"
    assert b1["author_intent"]["raw"] == "意图 B"
    assert calls["n"] == 2, "不同 author_intent 必须 miss（各自装配一次）"

    a2 = build_director_input(db_path, pid, cid, "意图 A")
    assert a2["author_intent"]["raw"] == "意图 A"
    assert calls["n"] == 2, "回到原意图应命中自己的条目，不再重装"


# ---------------------------------------------------------------------------
# 1b. writer：author_intent 维度（F-10 修复，2026-09-16）
# ---------------------------------------------------------------------------


def test_writer_cache_miss_on_different_author_intent(tmp_path: Path, monkeypatch):
    """writer 键含 author_intent 指纹 → 换意图必 miss（不得返回旧 payload）。

    旧缺陷形状（与 director V3.9 批次 1.4 同形）：author_intent 进 payload 但不进键
    ⇒ 改意图后同 state_version 脏命中旧装配，作者新要求被静默丢弃。
    """
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "x"}')
    _cache_reset()

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")
    scene = {"purpose": "开场", "scenes": [{"purpose": "s1"}]}

    a1 = build_writer_input(db_path, cid, scene, author_intent="意图 A")
    b1 = build_writer_input(db_path, cid, scene, author_intent="意图 B")
    assert a1["author_intent"]["raw"] == "意图 A"
    assert b1["author_intent"]["raw"] == "意图 B"
    assert calls["n"] == 2, "不同 author_intent 必须 miss（各自装配一次）"

    a2 = build_writer_input(db_path, cid, scene, author_intent="意图 A")
    assert a2["author_intent"]["raw"] == "意图 A"
    assert calls["n"] == 2, "回到原意图应命中自己的条目，不再重装"

    # 有 / 无意图各自成键：不得互命中（否则无意图装配会读到带意图的旧条目，反之
    # 亦然——「缺省不出现键」纪律只在键维度正确时才成立）
    plain = build_writer_input(db_path, cid, scene)
    assert "author_intent" not in plain
    assert calls["n"] == 3, "有 / 无意图必须各自 miss"


# ---------------------------------------------------------------------------
# 2/3. target_word_count 维度（director + writer）
# ---------------------------------------------------------------------------


def test_director_cache_miss_on_different_target_word_count(tmp_path: Path, monkeypatch):
    from packages.core.context_engine import director_input as di_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "x"}')
    _cache_reset()

    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    p2000 = build_director_input(db_path, pid, cid, "", target_word_count=2000)
    p3000 = build_director_input(db_path, pid, cid, "", target_word_count=3000)
    assert p2000["chapter"]["target_word_count"] == 2000
    assert p3000["chapter"]["target_word_count"] == 3000
    assert calls["n"] == 2, "不同 target_word_count 必须 miss"

    p2000_again = build_director_input(db_path, pid, cid, "", target_word_count=2000)
    assert p2000_again["chapter"]["target_word_count"] == 2000
    assert calls["n"] == 2, "回到原目标字数应命中自己的条目"


def test_writer_cache_miss_on_different_target_word_count(tmp_path: Path, monkeypatch):
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "x"}')
    _cache_reset()

    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")
    scene = {"purpose": "开场", "scenes": [{"purpose": "s1"}, {"purpose": "s2"}]}

    p2000 = build_writer_input(db_path, cid, scene, target_word_count=2000)
    p3000 = build_writer_input(db_path, cid, scene, target_word_count=3000)
    assert calls["n"] == 2, "不同 target_word_count 必须 miss"

    # 目标字数决定的三处产出都跟随（旧键缺维度会拿回陈旧值）
    assert p2000["chapter"]["target_word_count"] == 2000
    assert p3000["chapter"]["target_word_count"] == 3000
    assert p2000["chapter"]["word_band"] != p3000["chapter"]["word_band"]
    assert [s["target_words"] for s in p2000["scene_plan"]["scenes"]] == [1000, 1000]
    assert [s["target_words"] for s in p3000["scene_plan"]["scenes"]] == [1500, 1500]

    p2000_again = build_writer_input(db_path, cid, scene, target_word_count=2000)
    assert p2000_again["chapter"]["word_band"] == p2000["chapter"]["word_band"]
    assert calls["n"] == 2, "回到原目标字数应命中自己的条目"


# ---------------------------------------------------------------------------
# 4/5. preview 命名空间隔离
# ---------------------------------------------------------------------------


def test_preview_namespace_isolates_same_params_from_production(
    tmp_path: Path, monkeypatch,
):
    """预览与生产用**完全相同**的参数：仍必须各自装配一次（键空间隔离）。"""
    from packages.core.context_engine import builders as cb
    from packages.core.context_engine import director_input as di_mod
    from packages.core.context_engine import writer_input as wi_mod

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "x"}')
    cb._cache_reset()

    scene = {"purpose": "场景", "scenes": [{"purpose": "开场"}]}
    # 生产先建立自己的条目
    build_director_input(db_path, pid, cid, "同参数意图", target_word_count=3000)
    build_writer_input(db_path, cid, scene, target_word_count=3000)

    d_calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")
    w_calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    preview_context(
        db_path, pid, cid,
        author_intent="同参数意图",
        scene_plan=scene,
        target_word_count=3000,
    )
    assert d_calls["n"] == 1, "预览必须自建 director（不得命中生产条目）"
    assert w_calls["n"] == 1, "预览必须自建 writer（不得命中生产条目）"

    # 生产调用回到自己的条目：预览的写入没有覆盖它
    build_director_input(db_path, pid, cid, "同参数意图", target_word_count=3000)
    build_writer_input(db_path, cid, scene, target_word_count=3000)
    assert d_calls["n"] == 1
    assert w_calls["n"] == 1

    # 键空间确有两条（生产 + 预览），预览那条带 "preview" 命名空间标记
    ns_preview = _cache_namespace_tag(_PREVIEW_CACHE_NAMESPACE)
    writer_keys = [k for k in cb._assembly_cache if k[0] == pid and k[3] == "writer"]
    assert len(writer_keys) == 2
    assert any(k[-1] == ns_preview for k in writer_keys)
    director_keys = [k for k in cb._assembly_cache if k[0] == pid and k[3] == "director"]
    assert len(director_keys) == 2
    assert any(k[-1] == ns_preview for k in director_keys)


def test_preview_defaults_do_not_overwrite_production_entries(tmp_path: Path):
    """前端挂载章节页即打预览（空意图 / 默认目标字数）；随后生产装配不得读回预览条目。

    旧行为：preview 与生产共用键 → 预览条目命中，作者意图被静默丢弃、目标字数退回默认值。
    注：V3.9 批次 5.1 起 preview 的默认目标字数 = ``DEFAULT_TARGET_WORD_COUNT``（3000，
    改造前为 2200）；本用例的隔离性由 `"preview"` 命名空间保证，与默认值具体多少无关。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "x"}')
    _cache_reset()

    prod = build_director_input(db_path, pid, cid, "生产意图", target_word_count=3000)
    assert prod["author_intent"]["raw"] == "生产意图"

    preview = preview_context(db_path, pid, cid)
    l0 = next(layer for layer in preview["layers"] if layer["id"] == "L0")
    chapter_item = next(it for it in l0["items"] if it["kind"] == "chapter")
    assert f"target={DEFAULT_TARGET_WORD_COUNT}" in chapter_item["name"], (
        "预览按默认目标字数 dry-run"
    )
    l2 = next(layer for layer in preview["layers"] if layer["id"] == "L2")
    assert not any(it["kind"] == "author_intent" for it in l2["items"])

    prod2 = build_director_input(db_path, pid, cid, "生产意图", target_word_count=3000)
    assert prod2["author_intent"]["raw"] == "生产意图", "预览不得覆盖生产条目"
    assert prod2["chapter"]["target_word_count"] == 3000


# ---------------------------------------------------------------------------
# 6. 深拷贝隔离：调用方就地改写不污染缓存
# ---------------------------------------------------------------------------


def test_cached_payload_is_isolated_from_caller_mutation(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "x"}')
    _cache_reset()

    scene = {"purpose": "原样", "scenes": [{"purpose": "开场"}]}
    w1 = build_writer_input(db_path, cid, scene, target_word_count=2000)
    # chapter_write pipeline 的既有就地改写模式（mode / draft_text / revision_note）
    w1["mode"] = "revise"
    w1["draft_text"] = "改后的草稿"
    w1["revision_note"] = "把结尾收紧"
    w1["chapter"]["expected_role"] = "climax"
    w1["chapter"]["target_word_count"] = 9999
    w1["scene_plan"]["scenes"][0]["target_words"] = 1

    w2 = build_writer_input(db_path, cid, scene, target_word_count=2000)
    assert "mode" not in w2
    assert "draft_text" not in w2
    assert "revision_note" not in w2
    assert w2["chapter"]["expected_role"] == "setup"
    assert w2["chapter"]["target_word_count"] == 2000
    assert w2["scene_plan"]["scenes"][0]["target_words"] == 2000

    # director 同理（chapter_plan pipeline 改 chapter.expected_role）
    d1 = build_director_input(db_path, pid, cid, "意图")
    d1["author_intent"]["raw"] = "被改写的意图"
    d1["chapter"]["expected_role"] = "climax"
    d2 = build_director_input(db_path, pid, cid, "意图")
    assert d2["author_intent"]["raw"] == "意图"
    assert d2["chapter"]["expected_role"] == "setup"
