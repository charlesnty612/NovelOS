"""Sprint 15 / V1.3：Context Engine 扩展单元测试。

覆盖（任务书 V1.3）：
1. writer 输入含 ``author_style_samples``（含引导语 + samples list）。
2. 文风样例截断：注入 3 条 / 每条 2000 字 → writer 仅取最近 2 条、每条截断到 1000 字。
3. preview_context L2 items 含 kind=author_style_sample。
4. 项目级 overdue 阈值可配：项目 ``foreshadow_overdue_chapters=5`` → 当前 chapter_no
   距离 introduced 超过 5 章即 overdue。
5. SQL 截断修复：构造 60 条「非 overdue 低 importance」伏笔 + 1 条 overdue → overdue
   出现在 open_foreshadow_list 中（不再被预取 60 截断边界丢掉）。
6. SQL 截断：构造 100 条伏笔（其中 1 条 overdue）→ overdue 仍出现在结果前 20 条。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.context_engine.builders import (
    _FORESHADOW_OVERDUE_CHAPTERS,
    _STYLE_SAMPLE_PER_CHARS,
    _STYLE_SAMPLES_CAP,
    build_director_input,
    build_writer_input,
)
from packages.core.context_engine.preview import preview_context
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(
    db_path: Path,
    name: str = "项目",
    *,
    foreshadow_overdue_chapters: int | None = None,
) -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        if foreshadow_overdue_chapters is None:
            conn.execute(
                "INSERT INTO projects (project_id, name, premise, genre, target_words, "
                "status, foreshadow_overdue_chapters, created_at, updated_at) "
                "VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', 30, ?, ?)",
                (pid, name, now, now),
            )
        else:
            conn.execute(
                "INSERT INTO projects (project_id, name, premise, genre, target_words, "
                "status, foreshadow_overdue_chapters, created_at, updated_at) "
                "VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?, ?)",
                (pid, name, foreshadow_overdue_chapters, now, now),
            )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, project_id: str, number: int) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, '', '{}', 'COMMITTED', 'VISIBLE', NULL, ?, ?)",
            (cid, project_id, number, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_style_sample(
    db_path: Path,
    project_id: str,
    title: str,
    content: str,
    *,
    created_offset_ms: int = 0,
) -> str:
    sample_id = new_id("asty")
    # 用不同 created_at 让排序稳定：按 ISO 时间减 ms 偏移
    ts = (
        now_iso().replace("+00:00", f"+00:00:00.{created_offset_ms:03d}")
        if created_offset_ms
        else now_iso()
    )
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO author_style_samples (sample_id, project_id, title, content, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (sample_id, project_id, title, content, ts, ts),
        )
        conn.commit()
    finally:
        conn.close()
    return sample_id


def _insert_hook(
    db_path: Path,
    project_id: str,
    *,
    name: str = "hook",
    status: str = "OPEN",
    importance: float = 0.5,
    introduced_chapter_id: str | None = None,
    visibility: str = "RESTRICTED",
) -> str:
    hook_id = new_id("hook")
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO hooks
                (hook_id, project_id, name, introduced_chapter_id, status, importance,
                 expected_payoff_chapter_id, payoff_chapter_id, visibility, who_knows,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, ?, ?)
            """,
            (hook_id, project_id, name, introduced_chapter_id, status,
             importance, visibility, now_iso(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return hook_id


# ---------------------------------------------------------------------------
# 1. writer 输入含 author_style_samples
# ---------------------------------------------------------------------------


def test_writer_input_includes_author_style_samples_key(tmp_path: Path):
    """build_writer_input 含 author_style_samples（instruction + samples list）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=3)
    _insert_style_sample(db_path, pid, "雨夜", "雨敲在瓦上，一夜未歇。")

    out = build_writer_input(db_path, cid, scene_plan={})
    assert "author_style_samples" in out
    samples = out["author_style_samples"]
    assert "instruction" in samples
    assert "句式" in samples["instruction"]
    assert "samples" in samples
    assert len(samples["samples"]) == 1
    assert samples["samples"][0]["title"] == "雨夜"


def test_writer_input_author_style_samples_empty_when_none(tmp_path: Path):
    """无样例时 samples=[]；instruction 仍存在。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=1)

    out = build_writer_input(db_path, cid, scene_plan={})
    samples = out["author_style_samples"]
    assert samples["samples"] == []
    assert "instruction" in samples


# ---------------------------------------------------------------------------
# 2. 文风样例截断（最近 2 篇 / 每篇 ≤1000 字）
# ---------------------------------------------------------------------------


def test_author_style_samples_truncate_to_two_and_per_chars(tmp_path: Path):
    """注入 3 条 / 每条 2000 字 → writer 仅取最近 2 条 + 每条截断 1000 字。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=1)

    # created_at 倒序：先插的 created_offset_ms=0 最早；最后插的最新。
    # 为了让 sample_3 最新、sample_1 最旧，给 sample_3 created_offset_ms 最大。
    long_content = "字" * 2000
    _insert_style_sample(db_path, pid, "oldest", long_content, created_offset_ms=1)
    _insert_style_sample(db_path, pid, "middle", long_content, created_offset_ms=2)
    _insert_style_sample(db_path, pid, "newest", long_content, created_offset_ms=3)

    out = build_writer_input(db_path, cid, scene_plan={})
    samples = out["author_style_samples"]["samples"]
    assert len(samples) == _STYLE_SAMPLES_CAP == 2
    # 顺序按 created_at DESC → "newest" 在前
    titles = [s["title"] for s in samples]
    assert titles == ["newest", "middle"], titles
    # 每篇截断 ≤ 1000 字
    for s in samples:
        assert len(s["excerpt"]) == _STYLE_SAMPLE_PER_CHARS == 1000


# ---------------------------------------------------------------------------
# 3. preview 含 author_style_sample kind
# ---------------------------------------------------------------------------


def test_preview_includes_author_style_sample_kind(tmp_path: Path):
    """preview_context L2 items 含 kind='author_style_sample'。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, number=2)
    _insert_style_sample(db_path, pid, "散文A", "晨光初照，林间鸟鸣。")

    out = preview_context(db_path, pid, cid)
    l2_items = next(layer for layer in out["layers"] if layer["id"] == "L2")["items"]
    sty_items = [it for it in l2_items if it["kind"] == "author_style_sample"]
    assert len(sty_items) == 1
    assert sty_items[0]["name"] == "散文A"


# ---------------------------------------------------------------------------
# 4. 项目级 overdue 阈值可配
# ---------------------------------------------------------------------------


def test_overdue_threshold_per_project_configurable(tmp_path: Path):
    """项目 foreshadow_overdue_chapters=5 → introduced=10 / current=16 → overdue。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path, foreshadow_overdue_chapters=5)
    cur_cid = _insert_chapter(db_path, pid, number=16)
    intro_cid = _insert_chapter(db_path, pid, number=10)
    _insert_hook(db_path, pid, name="must_be_overdue", introduced_chapter_id=intro_cid)

    out = build_director_input(db_path, pid, cur_cid, "意图")
    items = {h["name"]: h for h in out["open_foreshadow_list"]}
    assert items["must_be_overdue"]["overdue"] is True
    assert items["must_be_overdue"]["chapters_since_introduced"] == 6


def test_overdue_threshold_fallback_to_30_when_null(tmp_path: Path):
    """未指定逾期阈值时 fallback 30（与 DDL DEFAULT 对齐）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)  # default 30
    cur_cid = _insert_chapter(db_path, pid, number=31)
    intro_cid = _insert_chapter(db_path, pid, number=1)
    _insert_hook(db_path, pid, name="edge_31_minus_1", introduced_chapter_id=intro_cid)

    out = build_director_input(db_path, pid, cur_cid, "意图")
    items = {h["name"]: h for h in out["open_foreshadow_list"]}
    # 31 - 1 = 30 → 不 overdue（> not >=）
    assert items["edge_31_minus_1"]["overdue"] is False
    # 与 fallback 常量一致
    assert _FORESHADOW_OVERDUE_CHAPTERS == 30


# ---------------------------------------------------------------------------
# 5. SQL 截断修复：>60 条伏笔下 overdue 项不再丢失
# ---------------------------------------------------------------------------


def test_open_foreshadow_keeps_overdue_when_total_exceeds_cap(tmp_path: Path):
    """60 条非 overdue 低 importance + 1 条 overdue → overdue 项必须出现在清单。

    旧实现（预取 LIMIT 60 再内存排序）会把 overdue 项丢；新实现（SQL 直接
    ORDER BY overdue_flag DESC LIMIT 20）必须保留。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    # 让所有非 overdue 伏笔 introduced 在「很近」的位置（差 < 30 章），保证它们非 overdue
    # 然后让 1 条 overdue 引入很早。
    cur_cid = _insert_chapter(db_path, pid, number=100)
    intro_fresh = _insert_chapter(db_path, pid, number=99)  # 差 1 → 非 overdue
    intro_old = _insert_chapter(db_path, pid, number=1)    # 差 99 → overdue

    # 60 条「非 overdue 低 importance」伏笔：让 importance 较小，让 overdue 项按
    # ``overdue_flag DESC, importance DESC`` 排序时仍能靠 overdue_flag 排第一
    for i in range(60):
        _insert_hook(
            db_path, pid,
            name=f"non_overdue_{i}",
            importance=0.1,
            introduced_chapter_id=intro_fresh,
        )
    # 1 条 overdue
    _insert_hook(
        db_path, pid,
        name="must_survive_overdue",
        importance=0.5,
        introduced_chapter_id=intro_old,
    )

    out = build_director_input(db_path, pid, cur_cid, "意图")
    items = out["open_foreshadow_list"]
    names = [h["name"] for h in items]
    assert "must_survive_overdue" in names, names
    # SQL 排序：overdue 项排第一
    assert names[0] == "must_survive_overdue", names[:5]


def test_open_foreshadow_sql_order_strict_at_large_volume(tmp_path: Path):
    """100 条伏笔（其中 1 条 overdue）→ overdue 出现在结果前 20 条。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cur_cid = _insert_chapter(db_path, pid, number=200)
    intro_fresh = _insert_chapter(db_path, pid, number=199)
    intro_old = _insert_chapter(db_path, pid, number=1)

    for i in range(99):
        _insert_hook(
            db_path, pid,
            name=f"noise_{i}",
            importance=0.9,  # 高 importance 但非 overdue
            introduced_chapter_id=intro_fresh,
        )
    _insert_hook(
        db_path, pid,
        name="overdue_loud",
        importance=0.1,
        introduced_chapter_id=intro_old,
    )

    out = build_director_input(db_path, pid, cur_cid, "意图")
    items = out["open_foreshadow_list"]
    assert len(items) == 20
    # overdue 项必须在结果中（不会被 99 条 importance=0.9 挤掉）
    assert items[0]["name"] == "overdue_loud", [h["name"] for h in items[:3]]
    assert all(h["name"] != "overdue_loud" or h["overdue"] is True for h in items)
