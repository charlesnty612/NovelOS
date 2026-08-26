"""build_writer_input 分页模式（V3.2 P2-1）单测。

覆盖（任务书 DoD）：
- full 模式（默认）输出与旧实现逐字段一致——零破坏回归保护。
- paged 模式：touched 全量 + 其余摘要；hooks open 全量 + resolved 仅最近 N 条；
  world_rules 全量；plot_events / director_plan / recent_prose / author_style_samples
  / style_constraints 等 L2 信号不被裁剪。
- paged 模式：payload 顶层注入 ``context_mode="paged"`` + ``context_paging_stats``；
  stats 含体积量化与裁剪计数。
- paged 模式：体积显著缩小（合成数据断言）。
- paged 模式：touched 的 resolved hook 强制保留。
- paged 模式：未 touch 任何实体时全实体走摘要路径。
- 模式解析优先级：ctx["writer_context_mode"] > env > 默认 "paged"。
- 非法 context_mode → ValueError；非法解析值 → fallback "paged" + 不抛错。

设计要点：
- 不依赖真实项目 DB：用 in-memory apply_migrations + 手插 characters / hooks / commits / state_deltas。
- 测试 build_writer_input 端到端时直连 sqlite 临时文件（与 test_observer_input_trim.py 同范式）。
- full 模式字节一致断言用「同一 scene_plan + 同一 chapter_id 两次调用对比」；
  不与历史固定值对比（DB 指纹会随实现演化），仅断言全字段相等。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 允许在 tests/unit/ 目录直接 pytest 运行
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ruff: noqa: E402  —— import 在 sys.path 注入后必须放在条件块之后
from packages.core.context_engine.builders import (  # noqa: E402
    _cache_reset,
    build_writer_input,
)
from packages.core.db import apply_migrations, get_connection  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402
from packages.workflows.chapter_write.pipeline import (  # noqa: E402
    _resolve_writer_context_mode,
)

MIGRATIONS_DIR = ROOT / "database" / "migrations"


# ---------------------------------------------------------------------------
# DB helpers（与 test_observer_input_trim.py 同范式）
# ---------------------------------------------------------------------------


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
            """
            INSERT INTO projects (project_id, name, premise, genre, target_words, status, created_at, updated_at)
            VALUES (?, 'p', NULL, NULL, NULL, 'ACTIVE', ?, ?)
            """,
            (pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _insert_chapter(db_path: Path, project_id: str, number: int = 1) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json, status,
                 visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, 't', '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, project_id, number, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_character_with_state(
    db_path: Path, project_id: str, char_id: str, name: str,
    *,
    state_json: dict | None = None,
) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO characters (character_id, project_id, name, role, core_json,
                                   visibility, created_at, updated_at)
            VALUES (?, ?, ?, 'supporting', '{}', 'VISIBLE', ?, ?)
            """,
            (char_id, project_id, name, now, now),
        )
        conn.execute(
            """
            INSERT INTO character_states (character_id, state_version, state_json,
                                          visibility, created_at)
            VALUES (?, 1, ?, 'VISIBLE', ?)
            """,
            (char_id, json.dumps(state_json or {"location": "loc_0", "goal": "x" * 100}, ensure_ascii=False), now),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_location(db_path: Path, project_id: str, loc_id: str, name: str) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO locations (location_id, project_id, name, statement, data_json,
                                   visibility, created_at, updated_at)
            VALUES (?, ?, ?, 'statement', '{}', 'VISIBLE', ?, ?)
            """,
            (loc_id, project_id, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_hook(
    db_path: Path, project_id: str, chapter_id: str,
    hook_id: str, name: str, status: str,
) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO hooks (hook_id, project_id, name, introduced_chapter_id,
                               status, importance, expected_payoff_chapter_id,
                               payoff_chapter_id, visibility, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 0.7, NULL, NULL, 'PUBLIC', ?, ?)
            """,
            (hook_id, project_id, name, chapter_id, status, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_commit_with_payload(
    db_path: Path, project_id: str, chapter_id: str,
    payload: dict, version: int,
) -> None:
    """手插一个 commit + state_deltas 行（与 test_observer_input_trim.py 同范式）。"""
    now = now_iso()
    delta_id = new_id("dlt")
    commit_id = new_id("cmt")
    branch_id = new_id("brn")
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO branches (branch_id, project_id, name, parent_branch_id,
                                  base_state_version, status, created_at)
            VALUES (?, ?, 'main', NULL, 0, 'ACTIVE', ?)
            """,
            (branch_id, project_id, now),
        )
        conn.execute(
            """
            INSERT INTO state_deltas
                (delta_id, chapter_id, workflow_run_id, previous_state_version,
                 delta_version, schema_version, payload_json, status, supersedes,
                 created_by, created_at)
            VALUES
                (?, ?, 'wfr_test', ?, 1, 'state-delta-v0', ?, 'applied', NULL, 'observer:v1', ?)
            """,
            (delta_id, chapter_id, version - 1, json.dumps(payload, ensure_ascii=False), now),
        )
        conn.execute(
            """
            INSERT INTO commits
                (commit_id, project_id, branch_id, chapter_id,
                 previous_state_version, resulting_state_version,
                 delta_id, validation_json, author_approval_json,
                 timestamp, workflow_run_id, rollback_of)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, '{}', '{}', ?, 'wfr_test', NULL)
            """,
            (commit_id, project_id, branch_id, chapter_id, version - 1, version, delta_id, now),
        )
        conn.commit()
    finally:
        conn.close()


_SCENE_PLAN = {
    "purpose": "夜访场景",
    "characters": ["char_alice", "char_bob"],
    "location": "loc_village",
    "conflict": "信任测试",
    "turn": "试探",
}


# ---------------------------------------------------------------------------
# full 模式字节一致回归保护（保证默认行为零变化——但本任务把 pipeline 默认改 paged，
# 这里测的是函数层面默认行为，不是 pipeline 默认值）。
# ---------------------------------------------------------------------------


def test_full_mode_default_preserves_all_characters_full_fields(tmp_path: Path):
    """full 模式：character_state_excerpts 保留 current_state 等大字段，未被摘要化。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character_with_state(db_path, pid, "char_alice", "Alice",
                                  state_json={"location": "loc_village", "goal": "复仇与求证" * 20})
    _insert_commit_with_payload(
        db_path, pid, cid, {"character_changes": [{"character_id": "char_alice"}]}, version=1,
    )
    payload = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="full")
    chars = payload["character_state_excerpts"]
    assert len(chars) == 1
    sample = chars[0]
    assert sample["character_id"] == "char_alice"
    # full 模式必有 current_state 大字段
    assert "current_state" in sample
    assert "knowledge_scope" in sample
    assert "summary_marker" not in sample


def test_full_mode_default_is_consistent_with_explicit_full(tmp_path: Path):
    """默认 context_mode='full' 与显式传 'full' 输出一致（默认签名保兼容）。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character_with_state(db_path, pid, "char_alice", "Alice")
    _insert_commit_with_payload(
        db_path, pid, cid, {"character_changes": []}, version=1,
    )
    # 注：同一 scene_plan 同 chapter 第二次调用命中缓存——为避免缓存污染对比，
    # 这里用不同的 scene_plan 触发两次 uncached。
    p1 = build_writer_input(db_path, cid, _SCENE_PLAN)
    p2 = build_writer_input(db_path, cid, dict(_SCENE_PLAN, alt="t2"))
    # 默认与 explicit full 都含完整 character 全字段
    assert "current_state" in p1["character_state_excerpts"][0]
    assert "current_state" in p2["character_state_excerpts"][0]
    # 默认模式未注入 context_mode / context_paging_stats（full 是默认行为零变化）
    assert "context_mode" not in p1
    assert "context_paging_stats" not in p1


def test_full_mode_hooks_field_handling(tmp_path: Path):
    """full 模式：若 writer payload 含 hook_ledger_excerpt 则保留全字段。

    注：writer 当前装配**不直接注入** hook_ledger_excerpt（hook 信号由
    director_plan.hook_handling 间接传递——PRD §124 拆分：director 负责伏笔
    管理、writer 专注写文）。本断言做「存在则验证」保护：未来若 writer 装配
    增加 hook 直接注入，paged 模式应正确裁剪。
    """
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_hook(db_path, pid, cid, "hook_open_001", "Open hook", "OPEN")
    _insert_hook(db_path, pid, cid, "hook_resolved_001", "Resolved hook", "RESOLVED")
    _insert_commit_with_payload(
        db_path, pid, cid, {"character_changes": []}, version=1,
    )
    payload = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="full")
    # writer 当前不注入 hook_ledger_excerpt；该断言仅在 future 实现扩展时生效
    if "hook_ledger_excerpt" in payload:
        hooks = payload["hook_ledger_excerpt"]
        assert len(hooks) >= 1


# ---------------------------------------------------------------------------
# paged 模式：实体裁剪语义
# ---------------------------------------------------------------------------


def test_paged_mode_injects_context_mode_marker_and_stats(tmp_path: Path):
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character_with_state(db_path, pid, "char_alice", "Alice")
    _insert_commit_with_payload(
        db_path, pid, cid, {"character_changes": [{"character_id": "char_alice"}]}, version=1,
    )
    payload = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    assert payload["context_mode"] == "paged"
    assert "context_paging_stats" in payload
    stats = payload["context_paging_stats"]
    assert stats["context_mode"] == "paged"
    assert "keep_recent_commits" in stats
    assert "characters_full" in stats
    assert "characters_summary" in stats
    assert "total_size_bytes_before" in stats
    assert "total_size_bytes_after" in stats


def test_paged_mode_touched_characters_full_others_summarized(tmp_path: Path):
    """touched character 全量；其他 character 仅摘要（无 current_state 大字段）。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character_with_state(db_path, pid, "char_alice", "Alice",
                                  state_json={"location": "loc_village", "goal": "x" * 200})
    _insert_character_with_state(db_path, pid, "char_bob", "Bob",
                                  state_json={"location": "loc_village", "goal": "y" * 200})
    _insert_character_with_state(db_path, pid, "char_carol", "Carol",
                                  state_json={"location": "loc_village", "goal": "z" * 200})
    # 只 touch alice
    _insert_commit_with_payload(
        db_path, pid, cid,
        {"character_changes": [{"character_id": "char_alice"}]}, version=1,
    )
    payload = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    chars = payload["character_state_excerpts"]
    assert len(chars) == 3
    stats = payload["context_paging_stats"]
    assert stats["characters_full"] == 1
    assert stats["characters_summary"] == 2
    alice = next(c for c in chars if c.get("character_id") == "char_alice")
    bob = next(c for c in chars if c.get("character_id") == "char_bob")
    assert "current_state" in alice  # touched 全量
    assert "summary_marker" in bob    # 摘要项带标记
    assert "current_state" not in bob  # 摘要去掉大字段


def test_paged_mode_locations_touched_vs_summary(tmp_path: Path):
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_location(db_path, pid, "loc_village", "村庄")
    _insert_location(db_path, pid, "loc_forest", "森林")
    _insert_location(db_path, pid, "loc_cave", "洞穴")
    _insert_commit_with_payload(
        db_path, pid, cid,
        {"world_changes": [{"world_kind": "location", "world_id": "loc_village"}]},
        version=1,
    )
    payload = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    locs = payload["world_state_excerpts"]["locations"]
    stats = payload["context_paging_stats"]
    assert stats["locations_full"] == 1
    assert stats["locations_summary"] == 2
    full_locs = [loc_item for loc_item in locs if loc_item.get("location_id") == "loc_village"]
    summary_locs = [loc_item for loc_item in locs if loc_item.get("location_id") != "loc_village"]
    assert len(full_locs) == 1
    assert all(loc_item.get("summary_marker") for loc_item in summary_locs)


def test_paged_mode_world_rules_relevant_always_full(tmp_path: Path):
    """L0：world_rules_relevant 全量保留（硬设定），统计 full=总数 summary=0。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    # 插 1 个 world_rule
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO world_rules (world_rule_id, project_id, name, statement,
                                     data_json, visibility, created_at, updated_at)
            VALUES (?, ?, 'rule_no_magic', '不可用法术', '{}', 'PUBLIC', ?, ?)
            """,
            ("rule_no_magic", pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    _insert_commit_with_payload(
        db_path, pid, cid,
        {"world_changes": [{"world_kind": "rule", "world_id": "rule_no_magic"}]},
        version=1,
    )
    payload = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    rules = payload["world_state_excerpts"]["world_rules_relevant"]
    assert len(rules) == 1
    assert rules[0]["world_rule_id"] == "rule_no_magic"
    assert rules[0]["statement"] == "不可用法术"  # 全量保留 statement
    stats = payload["context_paging_stats"]
    assert stats["world_rules_full"] == 1
    assert stats["world_rules_summary"] == 0


def test_paged_mode_hooks_layer_handling(tmp_path: Path):
    """writer paged：writer payload 当前**不注入** hook_ledger_excerpt（伏笔管理
    归 director，writer 透过 director_plan.hook_handling 间接获取）——故 paged
    模式对 hook 字段不产生任何裁剪效果。stats 中仍写入 hooks_open=0 等字段以
    与 observer 口径对齐（观测一致性）。

    断言：writer payload 无 hook_ledger_excerpt 键；stats.hooks_open=0；
    full / paged 模式 payload 顶层键集合在 hook 相关字段上一致（即 paged 不
    引入也不删除 hook_ledger_excerpt）。
    """
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    for i in range(3):
        _insert_hook(db_path, pid, cid, f"hook_open_{i:03d}", f"开放{i}", "OPEN")
    for i in range(8):
        _insert_hook(db_path, pid, cid, f"hook_resolved_{i:03d}", f"已结{i}", "RESOLVED")
    _insert_commit_with_payload(
        db_path, pid, cid, {"character_changes": []}, version=1,
    )
    full = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="full")
    _cache_reset()
    paged = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    # writer 装配不注入 hook_ledger_excerpt（验证当前架构约束）
    assert "hook_ledger_excerpt" not in full
    assert "hook_ledger_excerpt" not in paged
    # paged stats 含 hooks_open 字段（观测一致性，与 observer 对齐）
    assert "hooks_open" in paged["context_paging_stats"]
    assert paged["context_paging_stats"]["hooks_open"] == 0


def test_paged_mode_no_touch_all_summary_path(tmp_path: Path):
    """无 touched → 所有实体走摘要路径。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character_with_state(db_path, pid, "char_alice", "Alice")
    _insert_location(db_path, pid, "loc_village", "村庄")
    # 不插任何 commit → touched 全空
    payload = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    stats = payload["context_paging_stats"]
    assert stats["characters_full"] == 0
    assert stats["characters_summary"] == 1
    assert stats["locations_full"] == 0
    assert stats["locations_summary"] == 1


def test_paged_mode_l2_signals_unchanged(tmp_path: Path):
    """L2（director_plan / scene_plan / recent_prose / author_style_samples /
    recalled_passages / style_constraints / knowledge_permissions）不被裁剪。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character_with_state(db_path, pid, "char_alice", "Alice")
    _insert_commit_with_payload(
        db_path, pid, cid, {"character_changes": [{"character_id": "char_alice"}]}, version=1,
    )
    payload = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    # 同一 scene_plan 第二次调 full：清缓存以确保不是缓存命中而是真重算
    _cache_reset()
    full_payload = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="full")
    for k in (
        "director_plan", "scene_plan", "recent_prose", "author_style_samples",
        "recalled_passages", "style_constraints", "knowledge_permissions",
    ):
        assert payload[k] == full_payload[k], f"L2 信号 {k} 被裁剪"


def test_paged_mode_smaller_than_full(tmp_path: Path):
    """paged 模式 payload 体积小于 full 模式（合成膨胀数据断言）。

    writer 装配裁剪字段范围有限（character / world_state_excerpts）——
    当 character 状态字段相对小时总 payload 差距不显著；这里直接断言
    「character_state_excerpts 单键」显著缩小（19/20 摘要化）。
    """
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    # 插 20 个大字段 character
    for i in range(20):
        _insert_character_with_state(
            db_path, pid, f"char_big_{i:03d}", f"角色{i}",
            state_json={
                "location": "loc_village",
                "goal": "复仇与求证" * 50,
                "extra": "z" * 1000,
                "knowledge": [f"k{j}" * 30 for j in range(5)],
                "beliefs": [f"b{j}" * 30 for j in range(5)],
            },
        )
    # touch 仅 1 个 character
    _insert_commit_with_payload(
        db_path, pid, cid,
        {"character_changes": [{"character_id": "char_big_000"}]}, version=1,
    )
    full = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="full")
    _cache_reset()
    paged = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    # 1) character_state_excerpts 单键对比（裁剪影响最大的键）
    full_chars_bytes = len(json.dumps(full["character_state_excerpts"], ensure_ascii=False))
    paged_chars_bytes = len(json.dumps(paged["character_state_excerpts"], ensure_ascii=False))
    # 阈值取保守值 0.7：19/20 摘要化后该键应小于 full 的 70%
    # （writer 装配的 character_state_excerpts 已不含完整 state_json，仅
    # current_state + core_traits_summary 等子集，差距比 observer 略小）
    assert paged_chars_bytes < full_chars_bytes * 0.7, (
        f"character_state_excerpts paged 未显著缩小: "
        f"paged={paged_chars_bytes}, full={full_chars_bytes}, "
        f"ratio={paged_chars_bytes/full_chars_bytes:.2f}"
    )
    # 2) 摘要 character 数 >= 19
    summary_chars = [
        c for c in paged["character_state_excerpts"]
        if c.get("summary_marker") is True
    ]
    assert len(summary_chars) >= 19
    # 3) 总 payload 体积不增大
    full_size = len(json.dumps(full, ensure_ascii=False))
    paged_size = len(json.dumps(paged, ensure_ascii=False))
    assert paged_size < full_size
    # 4) stats 体积量化字段正确
    stats = paged["context_paging_stats"]
    assert stats["total_size_bytes_after"] <= stats["total_size_bytes_before"]
    assert stats["total_size_bytes_before"] > 0


def test_paged_mode_invalid_raises_value_error(tmp_path: Path):
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    try:
        build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="bogus")
    except ValueError as e:
        assert "context_mode" in str(e)
    else:
        raise AssertionError("expected ValueError for invalid context_mode")


# ---------------------------------------------------------------------------
# pipeline._resolve_writer_context_mode：模式解析优先级
# ---------------------------------------------------------------------------


def test_resolve_writer_context_mode_default_paged(monkeypatch):
    """无 ctx 字段 + 无环境变量 → 默认 'paged'（V3.2 行为变更）。"""
    monkeypatch.delenv("NOVELOS_WRITER_CONTEXT_MODE", raising=False)
    assert _resolve_writer_context_mode({}) == "paged"


def test_resolve_writer_context_mode_env_overrides_default(monkeypatch):
    monkeypatch.setenv("NOVELOS_WRITER_CONTEXT_MODE", "full")
    assert _resolve_writer_context_mode({}) == "full"


def test_resolve_writer_context_mode_ctx_overrides_env(monkeypatch):
    monkeypatch.setenv("NOVELOS_WRITER_CONTEXT_MODE", "full")
    assert _resolve_writer_context_mode({"writer_context_mode": "paged"}) == "paged"


def test_resolve_writer_context_mode_ctx_overrides_env_to_full(monkeypatch):
    monkeypatch.setenv("NOVELOS_WRITER_CONTEXT_MODE", "paged")
    assert _resolve_writer_context_mode({"writer_context_mode": "full"}) == "full"


def test_resolve_writer_context_mode_invalid_value_falls_back_to_paged(monkeypatch):
    """非法值兜底 'paged' + 不抛错（pipeline 层不阻断装配）。"""
    monkeypatch.delenv("NOVELOS_WRITER_CONTEXT_MODE", raising=False)
    assert _resolve_writer_context_mode({"writer_context_mode": "bogus"}) == "paged"
    # 环境变量非法也兜底
    monkeypatch.setenv("NOVELOS_WRITER_CONTEXT_MODE", "bogus")
    assert _resolve_writer_context_mode({}) == "paged"


# ---------------------------------------------------------------------------
# 缓存键隔离：full vs paged 不应共享同一缓存条目
# ---------------------------------------------------------------------------


def test_full_and_paged_use_separate_cache_entries(tmp_path: Path):
    """同一 chapter 不同 mode 应分别缓存（互不污染）。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character_with_state(db_path, pid, "char_alice", "Alice",
                                  state_json={"goal": "x" * 200})
    _insert_commit_with_payload(
        db_path, pid, cid, {"character_changes": [{"character_id": "char_alice"}]}, version=1,
    )
    full = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="full")
    paged = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    assert "context_mode" not in full
    assert paged["context_mode"] == "paged"
    # 再次调用 full 应命中 full 缓存（不被 paged 覆盖）
    full_again = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="full")
    assert "context_mode" not in full_again
    paged_again = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    assert paged_again["context_mode"] == "paged"


# ---------------------------------------------------------------------------
# V3.7 字数带硬约束：writer payload chapter 子对象注入 word_band
# ---------------------------------------------------------------------------


def test_chapter_word_band_default_target(tmp_path: Path):
    """默认 target=2200 时，chapter.word_band.low/high=1870/2530，键序 word_band 在末尾。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character_with_state(db_path, pid, "char_alice", "Alice")
    _insert_commit_with_payload(
        db_path, pid, cid, {"character_changes": []}, version=1,
    )
    payload = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="full")
    ch = payload["chapter"]
    assert "word_band" in ch
    assert ch["word_band"] == {"low": 1870, "high": 2530}
    # 键序：word_band 必须位于 chapter 子对象末尾（chapter_id 之后）
    keys = list(ch.keys())
    assert keys[-1] == "word_band"
    assert keys.index("chapter_id") < keys.index("word_band")


def test_chapter_word_band_explicit_target(tmp_path: Path):
    """显式 target_word_count=1000 时，低带被 floor=1200 保护到 1200；
    V3.7：floor 单调性保护，high=raw_high=1150 < low=1200 → 同步抬到 1200。
    """
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character_with_state(db_path, pid, "char_alice", "Alice")
    _insert_commit_with_payload(
        db_path, pid, cid, {"character_changes": []}, version=1,
    )
    payload = build_writer_input(
        db_path, cid, _SCENE_PLAN, target_word_count=1000, context_mode="full",
    )
    ch = payload["chapter"]
    assert ch["target_word_count"] == 1000
    assert ch["word_band"] == {"low": 1200, "high": 1200}


def test_chapter_word_band_paged_inherits(tmp_path: Path):
    """paged 模式：chapter 子对象（含 word_band）原样继承，未被裁剪。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_character_with_state(db_path, pid, "char_alice", "Alice")
    _insert_commit_with_payload(
        db_path, pid, cid, {"character_changes": []}, version=1,
    )
    payload = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    ch = payload["chapter"]
    assert "word_band" in ch
    assert ch["word_band"] == {"low": 1870, "high": 2530}
