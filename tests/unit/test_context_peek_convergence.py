"""V3.9 批次 2.3：缓存键预读收敛（4 个 ``_peek_*`` → 单连接单条 JOIN）测试。

覆盖：
- **DB 访问量**：writer / director 缓存命中路径各 1 连接 1 条 SQL（收敛前 5 / 3 连接）；
- **语义等价**：合并后的 ``_peek_chapter_context`` 与四个旧 ``_peek_*`` 薄封装同值；
  ``state_version`` == ``MAX(story_states.state_version)`` ==
  ``StoryStateService.get_current_state`` 的 state_version（口径一致）；
- **一致性**：同 fixture 下命中路径与冷装配路径 payload 完全相等（deepcopy 不影响内容）；
- **降级**：极老库（reference_canons 缺失）→ peek 逐项降级，不抛错。
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from packages.core.context_engine.builders import (
    _cache_reset,
    build_director_input,
    build_writer_input,
)
from packages.core.context_engine.builders_common import (
    _peek_active_canon_id,
    _peek_chapter_context,
    _peek_chapter_no_state_version,
    _peek_chapter_no_state_version_plan,
    _peek_project_id_from_chapter,
    _peek_project_word_band_json,
)
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.story_state.service import StoryStateService

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

# 参与 DB 访问计数的模块（各自 ``from packages.core.db import get_connection``，
# 需逐模块替换模块属性）。
_COUNTER_MODULES = (
    "packages.core.context_engine.builders_common",
    "packages.core.context_engine.director_input",
    "packages.core.context_engine.writer_input",
    "packages.core.context_engine.observer_input",
    "packages.core.context_engine.canon",
    "packages.core.context_engine.relevance",
    "packages.core.story_state.service",
    "packages.core.story_state.queries",
    "packages.core.retrieval.service",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
            """
            INSERT INTO projects (project_id, name, premise, genre, target_words, status, created_at, updated_at)
            VALUES (?, ?, NULL, NULL, NULL, 'ACTIVE', ?, ?)
            """,
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(
    db_path: Path,
    project_id: str,
    number: int = 1,
    *,
    plan_json: str = "{}",
) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json, status,
                 visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, project_id, number, f"第{number}章", plan_json, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_state_version(db_path: Path, project_id: str, version: int) -> None:
    """写 story_states 一行（裸 sqlite3 连接 bypass commits FK——只关心版本维度）。"""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO story_states (project_id, state_version, snapshot_json,"
            " commit_id, created_at) VALUES (?, ?, '{\"state_version\": %d}', 'cmt_t', ?)"
            % version,
            (project_id, version, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_canon(db_path: Path, project_id: str, canon_id: str = "can_peek") -> str:
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO reference_canons (canon_id, project_id, title, reader_profile,
                canon_json, report_md, status, created_at)
            VALUES (?, ?, '参照', 'male_fantasy', '{}', '', 'active', ?)
            """,
            (canon_id, project_id, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return canon_id


def _count_db_access(monkeypatch) -> dict[str, int]:
    """包装各模块 ``get_connection``：统计连接数 + 装配代码发出的 SQL 语句数。

    计 SQL 用 ``set_trace_callback``，且**在真实连接建立之后**挂上——这样
    ``get_connection`` 自身的 3 条 PRAGMA 固定成本不计入 sql（只统计业务查询，
    PRAGMA 固定成本体现在 conns 上）。
    """
    counters = {"conns": 0, "sql": 0}

    def make_wrapper(real):
        def wrapper(db_path):
            counters["conns"] += 1
            conn = real(db_path)
            conn.set_trace_callback(
                lambda _sql: counters.__setitem__("sql", counters["sql"] + 1)
            )
            return conn

        return wrapper

    for name in _COUNTER_MODULES:
        mod = importlib.import_module(name)
        real = getattr(mod, "get_connection", None)
        if real is None:
            continue
        monkeypatch.setattr(mod, "get_connection", make_wrapper(real))
    return counters


# ---------------------------------------------------------------------------
# 1. DB 访问量：命中路径 1 连接 1 条 SQL
# ---------------------------------------------------------------------------


def test_writer_cache_hit_uses_single_connection(tmp_path: Path, monkeypatch):
    """writer 缓存命中路径：1 连接 1 条 SQL（收敛前 5 连接 / 5 条）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "x"}')
    _insert_state_version(db_path, pid, 3)
    _cache_reset()
    build_writer_input(db_path, cid, {"purpose": "场景"})  # 预热缓存

    counters = _count_db_access(monkeypatch)
    payload = build_writer_input(db_path, cid, {"purpose": "场景"})

    assert counters["conns"] == 1, counters
    assert counters["sql"] == 1, counters
    assert payload["agent"] == "writer"


def test_director_cache_hit_uses_single_connection(tmp_path: Path, monkeypatch):
    """director 缓存命中路径：1 连接 1 条 SQL（收敛前 3 连接 / 3 条）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "x"}')
    _insert_state_version(db_path, pid, 3)
    _cache_reset()
    build_director_input(db_path, pid, cid, "意图")  # 预热缓存

    counters = _count_db_access(monkeypatch)
    payload = build_director_input(db_path, pid, cid, "意图")

    assert counters["conns"] == 1, counters
    assert counters["sql"] == 1, counters
    assert payload["agent"] == "director"


# ---------------------------------------------------------------------------
# 2. 语义等价：合并 peek vs 旧薄封装 / StoryStateService 口径
# ---------------------------------------------------------------------------


def test_merged_peek_matches_legacy_peek_wrappers(tmp_path: Path):
    """``_peek_chapter_context`` 与四个旧 ``_peek_*`` 薄封装逐字段同值。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    plan = '{"chapter_goal": "目标"}'
    cid = _insert_chapter(db_path, pid, 4, plan_json=plan)
    _insert_state_version(db_path, pid, 9)
    _insert_canon(db_path, pid, "can_a")

    info = _peek_chapter_context(db_path, cid)

    assert info == {
        "found": True,
        "chapter_id": cid,
        "project_id": pid,
        "chapter_no": 4,
        "state_version": 9,
        "plan_json_raw": plan,
        # 0028 大纲槽：peek 一并预读 outline_json 原文（director 装配注入 + 缓存键维度）
        "outline_json_raw": None,
        "word_band_json": None,
        "active_canon_id": "can_a",
        # 题材库 P1a：绑定题材包指纹（未绑定 → None；director 缓存键维度）
        "genre_pack_ref": None,
    }
    assert _peek_chapter_no_state_version(db_path, pid, cid) == (4, 9)
    assert _peek_chapter_no_state_version_plan(db_path, pid, cid) == (4, 9, plan)
    assert _peek_project_id_from_chapter(db_path, cid) == pid
    assert _peek_active_canon_id(db_path, pid) == "can_a"
    assert _peek_project_word_band_json(db_path, pid) is None


def test_peek_state_version_matches_story_state_service(tmp_path: Path):
    """state_version 口径一致：MAX(story_states) == get_current_state().state_version。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1)
    # 无快照：两边都应为 0
    assert _peek_chapter_context(db_path, cid)["state_version"] == 0
    assert StoryStateService(db_path).get_current_state(pid)["state_version"] == 0
    # 有快照：取最大版本
    _insert_state_version(db_path, pid, 1)
    _insert_state_version(db_path, pid, 5)
    assert _peek_chapter_context(db_path, cid)["state_version"] == 5
    assert StoryStateService(db_path).get_current_state(pid)["state_version"] == 5


def test_peek_genre_pack_ref_follows_binding_and_version(tmp_path: Path):
    """题材库 P1a：``genre_pack_ref`` = ``<pack_id>@<version>``；解绑 → None。

    该字段是 director 缓存键的题材包维度（主 SQL 与降级逐项读须同形；
    换 version 必 miss 的端到端断言见 tests/unit/test_genre_pack_injection.py）。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1)

    assert _peek_chapter_context(db_path, cid)["genre_pack_ref"] is None

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO genre_packs (pack_id, name, genre_tag, version, payload_json, "
            "source_path, created_at, updated_at) VALUES ('gp_a', '题材包', 'tag', 3, "
            "'{}', NULL, '2026-09-13T00:00:00+00:00', '2026-09-13T00:00:00+00:00')"
        )
        conn.execute(
            "UPDATE projects SET genre_pack_id = 'gp_a' WHERE project_id = ?", (pid,)
        )
        conn.commit()
    finally:
        conn.close()

    assert _peek_chapter_context(db_path, cid)["genre_pack_ref"] == "gp_a@3"

    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE genre_packs SET version = 4 WHERE pack_id = 'gp_a'"
        )
        conn.commit()
    finally:
        conn.close()
    assert _peek_chapter_context(db_path, cid)["genre_pack_ref"] == "gp_a@4"


def test_peek_word_band_json_raw_round_trip(tmp_path: Path):
    """``word_band_json`` 原文透传（writer 缓存键指纹的输入不变）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1)
    raw = json.dumps({"low": 0.8, "high": 1.2}, ensure_ascii=False)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE projects SET word_band_json = ? WHERE project_id = ?", (raw, pid),
        )
        conn.commit()
    finally:
        conn.close()

    assert _peek_chapter_context(db_path, cid)["word_band_json"] == raw
    assert _peek_project_word_band_json(db_path, pid) == raw


def test_peek_missing_chapter_returns_empty_state(tmp_path: Path):
    """章节不存在 → 全空态（found=False，字段齐全），不抛错。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)

    info = _peek_chapter_context(db_path, "ch_missing")

    assert info["found"] is False
    assert info["chapter_no"] == 0
    assert info["state_version"] == 0
    assert info["plan_json_raw"] is None
    assert _peek_project_id_from_chapter(db_path, "ch_missing") is None
    # 传了 project_id 时以其为准（保留旧签名语义）
    assert _peek_chapter_context(db_path, "ch_missing", project_id=pid)["project_id"] == pid


# ---------------------------------------------------------------------------
# 3. 一致性：命中路径 == 冷装配路径（同 fixture 输出相等）
# ---------------------------------------------------------------------------


def test_director_cached_payload_equals_cold_payload(tmp_path: Path):
    """同 fixture：缓存命中返回与冷装配**逐字段相等**（peek 只影响取数路径）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 2, plan_json='{"chapter_goal": "目标"}')
    prev = _insert_chapter(db_path, pid, 1)
    _insert_state_version(db_path, pid, 4)

    _cache_reset()
    cold = build_director_input(db_path, pid, cid, "意图", target_word_count=3000)
    warm = build_director_input(db_path, pid, cid, "意图", target_word_count=3000)

    assert cold == warm
    assert cold["story_state_snapshot"]["current_state_version"] == 4
    assert cold["story_state_snapshot"]["current_chapter"] == 2
    assert prev  # 上一章行存在（前章尾段路径已跑）


def test_writer_cached_payload_equals_cold_payload(tmp_path: Path):
    """同 fixture：writer 命中路径与冷装配逐字段相等（含 scene target_words 预算）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1, plan_json='{"chapter_goal": "目标"}')
    _insert_state_version(db_path, pid, 2)
    scene = {"purpose": "场景", "characters": ["角色"]}

    _cache_reset()
    cold = build_writer_input(db_path, cid, scene, target_word_count=2200)
    warm = build_writer_input(db_path, cid, scene, target_word_count=2200)

    assert cold == warm
    assert cold["chapter"]["target_word_count"] == 2200


# ---------------------------------------------------------------------------
# 4. 降级：极老库（reference_canons 缺失）不阻断 peek
# ---------------------------------------------------------------------------


def test_peek_degrades_when_reference_canons_missing(tmp_path: Path):
    """JOIN 里的 reference_canons 缺失 → 逐项降级：章节三列 + state_version 仍可取。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 6, plan_json='{"chapter_goal": "目标"}')
    _insert_state_version(db_path, pid, 2)

    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE reference_canons")
        conn.commit()
    finally:
        conn.close()

    info = _peek_chapter_context(db_path, cid)

    assert info["found"] is True
    assert info["chapter_no"] == 6
    assert info["state_version"] == 2
    assert info["plan_json_raw"] == '{"chapter_goal": "目标"}'
    assert info["active_canon_id"] is None
    assert _peek_active_canon_id(db_path, pid) is None


def test_peek_never_raises_on_unreadable_db(tmp_path: Path):
    """库路径不可读 → 全空态返回，不抛错（旧 ``_peek_project_id_from_chapter`` 语义）。"""
    bad = tmp_path / "nope" / "missing.db"

    info = _peek_chapter_context(bad, "ch_x")

    assert info["found"] is False
    assert _peek_chapter_no_state_version(bad, "prj_x", "ch_x") == (0, 0)
    assert _peek_active_canon_id(bad, "prj_x") is None


def test_peek_does_not_read_snapshot_json(tmp_path: Path, monkeypatch):
    """收敛前 state_version 靠解析整份 snapshot_json；现为 MAX 轻查询（不读快照体）。

    验证方式：写一份 20KB 的 snapshot_json，追踪 peek 连接上的 SQL —— 只有 1 条，
    且语句里不出现 ``snapshot_json`` 列。
    """
    import sqlite3

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1)
    big_snapshot = json.dumps({"state_version": 1, "filler": "x" * 20000})
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO story_states (project_id, state_version, snapshot_json, commit_id, created_at)"
            " VALUES (?, 1, ?, 'cmt_big', ?)",
            (pid, big_snapshot, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    import packages.core.context_engine.builders_common as bc_mod

    seen: list[str] = []
    real_get_connection = bc_mod.get_connection

    def tracing_get_connection(db_path_arg):
        trace_conn = real_get_connection(db_path_arg)
        trace_conn.set_trace_callback(seen.append)
        return trace_conn

    monkeypatch.setattr(bc_mod, "get_connection", tracing_get_connection)

    info = _peek_chapter_context(db_path, cid)

    assert info["state_version"] == 1
    assert len(seen) == 1, seen
    assert "snapshot_json" not in seen[0]
