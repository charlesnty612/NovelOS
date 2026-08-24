"""V2.0 Wave B 任务二：设定条件触发动态注入（Context Engine 三态）。

覆盖：
- builders._is_triggered / _summarize_entity / _apply_injection_policy / _build_trigger_corpus
- builders._character_state_excerpts / _world_state_excerpts 按 trigger_corpus 应用三态
- 3 态 × 命中 / 未命中矩阵：
  - always：无视命中，full 注入
  - never：不注入，preview 标记 suppressed
  - auto + 命中：full 注入
  - auto + 未命中：summary（一行摘要，剥离 data_json / current_state）
- 别名命中（aliases 中任一 ≥ 2 字符词面匹配）
- 回退策略：无章节 plan_json（trigger_corpus 为空）→ auto 全部按 full 注入（保兼容）
- 装配体积下降：大量未命中实体时 L1/layers.items 数减少、character_state_excerpts 不展开 data_json
- preview：注入状态标记 + suppressed 项落到 L1 items（kind=suppressed_*）
- WorldService / CharacterService：aliases / inject_mode 写入与读出
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.context_engine.builders import (
    _apply_injection_policy,
    _build_trigger_corpus,
    _character_state_excerpts,
    _is_triggered,
    _summarize_entity,
    _world_state_excerpts,
    build_director_input,
    build_writer_input,
)
from packages.core.context_engine.preview import preview_context
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.domain.character.service import CharacterService
from packages.domain.world.service import WorldService


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


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
    title: str = "第一章",
    *,
    plan_json: dict | None = None,
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
            (
                cid,
                project_id,
                number,
                title,
                json.dumps(plan_json or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_character(
    db_path: Path,
    project_id: str,
    *,
    name: str = "主角",
    aliases: list[str] | None = None,
    inject_mode: str = "auto",
    role: str = "protagonist",
) -> str:
    cid = new_id("char")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO characters (character_id, project_id, name, role,
                                    core_json, visibility, who_knows,
                                    created_at, updated_at,
                                    aliases, inject_mode)
            VALUES (?, ?, ?, ?, '{}', 'PUBLIC', NULL, ?, ?, ?, ?)
            """,
            (
                cid,
                project_id,
                name,
                role,
                now,
                now,
                json.dumps(aliases or [], ensure_ascii=False),
                inject_mode,
            ),
        )
        conn.execute(
            """
            INSERT INTO character_states
                (character_id, state_version, state_json, visibility, who_knows, created_at)
            VALUES (?, 1, '{}', 'VISIBLE', NULL, ?)
            """,
            (cid, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_location(
    db_path: Path,
    project_id: str,
    *,
    name: str = "王城",
    statement: str = "帝国首都",
    aliases: list[str] | None = None,
    inject_mode: str = "auto",
) -> str:
    lid = new_id("loc")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO locations (location_id, project_id, name, statement,
                                   data_json, visibility, who_knows,
                                   created_at, updated_at,
                                   aliases, inject_mode)
            VALUES (?, ?, ?, ?, '{}', 'PUBLIC', NULL, ?, ?, ?, ?)
            """,
            (
                lid,
                project_id,
                name,
                statement,
                now,
                now,
                json.dumps(aliases or [], ensure_ascii=False),
                inject_mode,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return lid


def _insert_faction(
    db_path: Path,
    project_id: str,
    *,
    name: str = "帝国",
    statement: str = "大陆最强势力",
    aliases: list[str] | None = None,
    inject_mode: str = "auto",
) -> str:
    fid = new_id("fac")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO factions (faction_id, project_id, name, statement,
                                  data_json, visibility, who_knows,
                                  created_at, updated_at,
                                  aliases, inject_mode)
            VALUES (?, ?, ?, ?, '{}', 'PUBLIC', NULL, ?, ?, ?, ?)
            """,
            (
                fid,
                project_id,
                name,
                statement,
                now,
                now,
                json.dumps(aliases or [], ensure_ascii=False),
                inject_mode,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return fid


# ---------------------------------------------------------------------------
# 单元层：_is_triggered / _summarize_entity / _apply_injection_policy
# ---------------------------------------------------------------------------


def test_is_triggered_name_substring_match_case_insensitive():
    assert _is_triggered("Alice", [], "alice meets the wizard") is True
    assert _is_triggered("Alice", [], "ALICE IN WONDERLAND") is True


def test_is_triggered_alias_substring_match_min_len_2():
    """aliases 词面必须 ≥ 2 字符；常用词不应误触发。"""
    # 短 alias（< 2 字符）忽略 → 未命中（无 name 提供，alias 太短全部跳过）
    assert _is_triggered(None, ["的"], "无关剧情") is False
    assert _is_triggered("", ["a"], "anything") is False
    # 正常 alias 命中
    assert _is_triggered("林昭", ["云隐剑"], "林昭拔出云隐剑") is True
    # 中文直接子串
    assert _is_triggered("林昭", [], "林昭夜奔") is True


def test_is_triggered_empty_corpus_returns_false():
    """无章节 plan 文本 → 视为未触发（让 fallback 路径兜底）。"""
    assert _is_triggered("林昭", ["云隐剑"], "") is False


def test_summarize_entity_character_format():
    out = _summarize_entity(kind="character", name="林昭", entity_id="c1", role="protagonist")
    assert out["character_id"] == "c1"
    assert out["name"] == "林昭"
    assert out["role"] == "protagonist"
    assert "林昭（protagonist）" in out["summary_line"]
    assert out["injection"] == "summary"


def test_summarize_entity_location_with_statement():
    out = _summarize_entity(kind="location", name="王城", entity_id="loc1", statement="帝国首都")
    assert out["location_id"] == "loc1"
    assert "王城" in out["summary_line"]
    assert "帝国首都" in out["summary_line"]


def test_apply_injection_policy_always_is_full():
    status, payload = _apply_injection_policy(
        kind="character",
        name="X",
        entity_id="c1",
        aliases=[],
        inject_mode="always",
        full_excerpt={"character_id": "c1", "name": "X"},
        summary_overrides=None,
        trigger_corpus_lower="anything",
        trigger_corpus_empty=False,
    )
    assert status == "full"
    assert payload["_injection"] == "full"


def test_apply_injection_policy_never_is_suppressed():
    status, payload = _apply_injection_policy(
        kind="location",
        name="X",
        entity_id="loc1",
        aliases=[],
        inject_mode="never",
        full_excerpt={"location_id": "loc1", "name": "X"},
        summary_overrides=None,
        trigger_corpus_lower="anything",
        trigger_corpus_empty=False,
    )
    assert status == "suppressed"
    assert payload["injection"] == "suppressed"
    assert payload["location_id"] == "loc1"


def test_apply_injection_policy_auto_triggered_is_full():
    status, payload = _apply_injection_policy(
        kind="character",
        name="林昭",
        entity_id="c1",
        aliases=["云隐剑"],
        inject_mode="auto",
        full_excerpt={"character_id": "c1", "name": "林昭"},
        summary_overrides={"role": "protagonist"},
        trigger_corpus_lower="林昭遇到云隐剑",
        trigger_corpus_empty=False,
    )
    assert status == "full"
    assert payload["_injection"] == "full"


def test_apply_injection_policy_auto_not_triggered_is_summary():
    status, payload = _apply_injection_policy(
        kind="character",
        name="林昭",
        entity_id="c1",
        aliases=["云隐剑"],
        inject_mode="auto",
        full_excerpt={"character_id": "c1", "name": "林昭"},
        summary_overrides={"role": "protagonist"},
        trigger_corpus_lower="完全无关的剧情描述",
        trigger_corpus_empty=False,
    )
    assert status == "summary"
    assert payload["injection"] == "summary"
    assert payload["name"] == "林昭"
    assert "林昭" in payload["summary_line"]


def test_apply_injection_policy_auto_fallback_full_when_corpus_empty():
    """回退策略：trigger_corpus 为空 → auto 默认全注入（向后兼容）。"""
    status, payload = _apply_injection_policy(
        kind="character",
        name="X",
        entity_id="c1",
        aliases=[],
        inject_mode="auto",
        full_excerpt={"character_id": "c1", "name": "X"},
        summary_overrides=None,
        trigger_corpus_lower="",
        trigger_corpus_empty=True,
    )
    assert status == "full"
    assert payload["_injection"] == "full"


def test_build_trigger_corpus_from_plan_and_tail():
    plan = {
        "chapter_goal": "林昭夜探王城",
        "core_conflict": "与刺客的对决",
        "key_beats": ["林昭潜入", "发现密道", "意外遭遇云隐剑"],
        "character_changes_planned": ["林昭觉醒"],
        "notes_for_planner": "本章需引入云隐剑",
    }
    corpus = _build_trigger_corpus(
        _FakeRow(plan_json=json.dumps(plan)),
        previous_tail_text="上一章末尾有云隐剑碎片",
    )
    assert "林昭夜探王城" in corpus
    assert "云隐剑" in corpus
    assert "上一章末尾有云隐剑碎片" in corpus


def test_build_trigger_corpus_handles_missing_plan():
    """章节行 plan_json 缺失 / 非法 → 给空串（按 fallback）。"""
    corpus = _build_trigger_corpus(_FakeRow(plan_json=None), previous_tail_text="")
    assert corpus == ""
    corpus = _build_trigger_corpus(_FakeRow(plan_json="{not valid"), previous_tail_text="")
    assert corpus == ""


class _FakeRow(dict):
    """模拟 sqlite3.Row：按 dict 索引。"""

    def __init__(self, **kwargs: str | None):
        super().__init__(**kwargs)


# ---------------------------------------------------------------------------
# 集成层：build_director_input / build_writer_input 三态矩阵
# ---------------------------------------------------------------------------


def test_director_input_auto_triggered_full_injection(tmp_path: Path):
    """auto + 命中 plan 文本 → 完整注入（保留 data_json 等所有字段）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid,
        plan_json={"chapter_goal": "林昭夜探王城"},
    )
    char_id = _insert_character(db_path, pid, name="林昭")

    out = build_director_input(db_path, pid, cid, "意图")

    chars = out["character_state_excerpts"]
    # 无未命中时 _suppressed_characters 不应出现
    supp = [c for c in chars if "_suppressed_characters" in c]
    assert supp == [], "auto+命中 时不应有 suppressed 列表"
    # 林昭注入完整（_injection='full'）；仅看真实注入条目（排除 _suppressed_characters marker）
    real_chars = [c for c in chars if "character_id" in c]
    lin = next(c for c in real_chars if c["character_id"] == char_id)
    assert lin["_injection"] == "full"
    assert "core_json" in lin  # 完整字段
    assert "current_state" in lin


def test_director_input_auto_untriggered_downgrades_to_summary(tmp_path: Path):
    """auto + 未命中 plan 文本 → 降级为一行摘要（剥离 data_json 等冗余字段）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid,
        plan_json={"chapter_goal": "完全无关的剧情", "key_beats": ["普通对话"]},
    )
    char_id = _insert_character(db_path, pid, name="林昭", role="protagonist")

    out = build_director_input(db_path, pid, cid, "意图")

    chars = out["character_state_excerpts"]
    # _suppressed_characters 是 marker dict；只取真正注入的实体
    real_chars = [c for c in chars if "character_id" in c]
    lin = next(c for c in real_chars if c["character_id"] == char_id)
    assert lin["_injection"] == "summary"
    assert "林昭" in lin["summary_line"]
    # 摘要不应包含完整字段
    assert "core_json" not in lin
    assert "current_state" not in lin


def test_director_input_always_keeps_full_regardless_of_plan(tmp_path: Path):
    """always：无视 plan 命中与否，永远完整注入。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid,
        plan_json={"chapter_goal": "完全无关"},
    )
    char_id = _insert_character(db_path, pid, name="主角", inject_mode="always")

    out = build_director_input(db_path, pid, cid, "意图")

    chars = out["character_state_excerpts"]
    real_chars = [c for c in chars if "character_id" in c]
    prot = next(c for c in real_chars if c["character_id"] == char_id)
    assert prot["_injection"] == "full"
    assert "core_json" in prot


def test_director_input_never_suppresses_character(tmp_path: Path):
    """never：不注入；预览列表保留为 ``_suppressed_characters``。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid,
        plan_json={"chapter_goal": "林昭夜探王城"},  # 即使命中也 suppressed
    )
    char_id = _insert_character(db_path, pid, name="林昭", inject_mode="never")

    out = build_director_input(db_path, pid, cid, "意图")

    # 林昭不在顶层 character_state_excerpts 中
    assert all(c.get("character_id") != char_id for c in out["character_state_excerpts"])
    # 但在 _suppressed_characters 列表中
    flat = [s for c in out["character_state_excerpts"] for s in c.get("_suppressed_characters", [])]
    supp = next((s for s in flat if s.get("character_id") == char_id), None)
    assert supp is not None, "never 角色应在 _suppressed_characters 列表"
    assert supp["injection"] == "suppressed"


def test_director_input_alias_trigger_hits_summary_to_full(tmp_path: Path):
    """别名命中：plan 文本未提 name，但命中 aliases → full 注入。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid,
        plan_json={"chapter_goal": "云隐剑碎片被发现"},
    )
    char_id = _insert_character(
        db_path, pid, name="林昭", aliases=["云隐剑"],
    )

    out = build_director_input(db_path, pid, cid, "意图")

    chars = out["character_state_excerpts"]
    real_chars = [c for c in chars if "character_id" in c]
    lin = next(c for c in real_chars if c["character_id"] == char_id)
    assert lin["_injection"] == "full"


def test_director_input_world_location_auto_summary(tmp_path: Path):
    """地点 auto + 未命中 → summary。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid,
        plan_json={"chapter_goal": "完全无关的剧情"},
    )
    loc_id = _insert_location(db_path, pid, name="王城", statement="帝国首都")

    out = build_director_input(db_path, pid, cid, "意图")

    locs = out["world_state_excerpts"]["locations"]
    loc = next(l for l in locs if l["location_id"] == loc_id)
    assert loc["_injection"] == "summary"
    assert "王城" in loc["summary_line"]


def test_director_input_world_never_suppresses_location(tmp_path: Path):
    """地点 never → 进 _suppressed_locations（world_state_excerpts 顶层）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid,
        plan_json={"chapter_goal": "云隐剑碎片被发现"},
    )
    loc_id = _insert_location(db_path, pid, name="云隐剑碎片藏匿处", inject_mode="never")

    out = build_director_input(db_path, pid, cid, "意图")

    locs = out["world_state_excerpts"]["locations"]
    assert all(l.get("location_id") != loc_id for l in locs)
    supp = out["world_state_excerpts"].get("_suppressed_locations", [])
    assert any(s.get("location_id") == loc_id for s in supp)


def test_director_input_world_faction_always_full(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid,
        plan_json={"chapter_goal": "完全无关"},
    )
    fac_id = _insert_faction(db_path, pid, name="帝国", inject_mode="always")

    out = build_director_input(db_path, pid, cid, "意图")

    facs = out["world_state_excerpts"]["active_factions"]
    f = next(x for x in facs if x["faction_id"] == fac_id)
    assert f["_injection"] == "full"


def test_director_input_world_rules_are_not_filtered(tmp_path: Path):
    """L0 world_rules 不参与触发键：保持常驻完整。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid,
        plan_json={"chapter_goal": "完全无关"},
    )
    rid = new_id("wrl")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO world_rules (world_rule_id, project_id, name, statement, "
            "data_json, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, '魔法上限', '不允许飞天遁地', '{}', 'PUBLIC', NULL, ?, ?)",
            (rid, pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    out = build_director_input(db_path, pid, cid, "意图")

    rules = out["world_state_excerpts"]["world_rules_relevant"]
    rule = next(r for r in rules if r["world_rule_id"] == rid)
    assert rule.get("_injection", "full") == "full"


def test_director_input_fallback_to_full_when_plan_empty(tmp_path: Path):
    """回退策略：章节 plan_json 为空 → auto 全部按 full 注入（保兼容）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json={})
    char_id = _insert_character(db_path, pid, name="林昭")

    out = build_director_input(db_path, pid, cid, "意图")

    chars = out["character_state_excerpts"]
    real_chars = [c for c in chars if "character_id" in c]
    lin = next(c for c in real_chars if c["character_id"] == char_id)
    assert lin["_injection"] == "full"
    assert "core_json" in lin


# ---------------------------------------------------------------------------
# Writer 同源：build_writer_input
# ---------------------------------------------------------------------------


def test_writer_input_applies_policy_with_scene_plan(tmp_path: Path):
    """Writer 触发扫描面 = chapter plan + scene_plan + recent_prose。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid,
        plan_json={"chapter_goal": "完全无关的剧情"},
    )
    char_id = _insert_character(db_path, pid, name="林昭")

    # scene_plan 里提了林昭 → 命中 → full
    scene_plan = {"purpose": "林昭与刺客对决", "location": "王城"}
    out = build_writer_input(db_path, cid, scene_plan)

    chars = out["character_state_excerpts"]
    real_chars = [c for c in chars if "character_id" in c]
    lin = next(c for c in real_chars if c["character_id"] == char_id)
    assert lin["_injection"] == "full"


def test_writer_input_no_match_downgrades_to_summary(tmp_path: Path):
    """Writer 场景 plan + scene_plan 都没提 → summary。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, plan_json={"chapter_goal": "无关剧情"})
    char_id = _insert_character(db_path, pid, name="林昭")

    out = build_writer_input(db_path, cid, {"purpose": "无关描述"})

    chars = out["character_state_excerpts"]
    lin = next(c for c in chars if c["character_id"] == char_id)
    assert lin["_injection"] == "summary"


# ---------------------------------------------------------------------------
# 装配体积下降：注入策略生效时 token 体积显著降低
# ---------------------------------------------------------------------------


def test_payload_size_drops_when_entities_downgraded(tmp_path: Path):
    """大量 auto 实体未命中 → character_state_excerpts 体积显著降低（vs auto 全注入）。

    体积口径：``len(json.dumps(...))`` 字节数。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)

    # 制造大量角色 + 详情 core_json（让 full payload 体积显著 > summary payload）
    for i in range(20):
        _insert_character_with_large_core(db_path, pid, name=f"角色{i}")

    # 1) 全 auto + 命中 → 全部 full
    cid_hit = _insert_chapter(
        db_path, pid, number=1,
        plan_json={"chapter_goal": "角色0和角色1出场"},
    )
    out_hit = build_director_input(db_path, pid, cid_hit, "意图")
    bytes_hit = sum(
        len(json.dumps(c, ensure_ascii=False))
        for c in out_hit["character_state_excerpts"]
        if "character_id" in c
    )

    # 2) 全 auto + 不命中 → 全部 summary
    cid_miss = _insert_chapter(
        db_path, pid, number=2,
        plan_json={"chapter_goal": "完全无关"},
    )
    out_miss = build_director_input(db_path, pid, cid_miss, "意图")
    bytes_miss = sum(
        len(json.dumps(c, ensure_ascii=False))
        for c in out_miss["character_state_excerpts"]
        if "character_id" in c
    )

    # summary 体积应显著小于 full（< 80%）
    assert bytes_miss < bytes_hit * 0.8, (
        f"未命中降级后体积应显著下降；hit={bytes_hit} miss={bytes_miss}"
    )


def _insert_character_with_large_core(
    db_path: Path, project_id: str, *, name: str
) -> str:
    cid = new_id("char")
    now = now_iso()
    core = {
        "personality": "性格非常详细" * 20,
        "values": ["自由", "荣誉", "复仇", "救赎", "真相"],
        "fears": ["背叛", "失去", "孤独"],
        "desires": ["寻找真相", "复仇", "保护同伴"],
        "background": "背景故事" * 30,
    }
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO characters (character_id, project_id, name, role,
                                    core_json, visibility, who_knows,
                                    created_at, updated_at,
                                    aliases, inject_mode)
            VALUES (?, ?, ?, 'supporting', ?, 'PUBLIC', NULL, ?, ?, '[]', 'auto')
            """,
            (
                cid,
                project_id,
                name,
                json.dumps(core, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO character_states
                (character_id, state_version, state_json, visibility, who_knows, created_at)
            VALUES (?, 1, ?, 'VISIBLE', NULL, ?)
            """,
            (cid, json.dumps({"location": "起点", "goal": "目标" * 20}, ensure_ascii=False), now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


# ---------------------------------------------------------------------------
# Preview 标记：注入状态展示
# ---------------------------------------------------------------------------


def test_preview_marks_full_summary_suppressed(tmp_path: Path):
    """preview L1 items 按注入状态带 injection 字段（full / summary / suppressed）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid,
        plan_json={"chapter_goal": "林昭夜探王城"},
    )
    full_char = _insert_character(db_path, pid, name="林昭", inject_mode="always")
    miss_char = _insert_character(db_path, pid, name="陈风", inject_mode="auto")
    supp_char = _insert_character(db_path, pid, name="隐者", inject_mode="never")

    out = preview_context(str(db_path), pid, cid)

    l1 = next(l for l in out["layers"] if l["id"] == "L1")
    by_id = {(it["kind"], it["id"]): it for it in l1["items"]}

    assert by_id[("character", full_char)]["injection"] == "full"
    assert by_id[("character", miss_char)]["injection"] == "summary"
    assert by_id[("suppressed_character", supp_char)]["injection"] == "suppressed"


def test_preview_summary_line_contains_role(tmp_path: Path):
    """summary 状态带 summary_line（包含 role 信息）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid,
        plan_json={"chapter_goal": "完全无关"},
    )
    char_id = _insert_character(db_path, pid, name="陈风", role="antagonist")

    out = preview_context(str(db_path), pid, cid)

    l1 = next(l for l in out["layers"] if l["id"] == "L1")
    char_item = next(
        it for it in l1["items"]
        if it["kind"] == "character" and it["id"] == char_id
    )
    assert char_item["injection"] == "summary"
    assert char_item["summary_line"]
    assert "陈风" in char_item["summary_line"]


def test_preview_world_location_summary_and_suppressed(tmp_path: Path):
    """地点 summary + 势力 suppressed 同样落到 preview L1。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid,
        plan_json={"chapter_goal": "完全无关"},
    )
    loc_id = _insert_location(db_path, pid, name="王城")
    fac_id = _insert_faction(db_path, pid, name="帝国", inject_mode="never")

    out = preview_context(str(db_path), pid, cid)

    l1 = next(l for l in out["layers"] if l["id"] == "L1")
    loc_item = next(it for it in l1["items"] if it["kind"] == "location" and it["id"] == loc_id)
    assert loc_item["injection"] == "summary"
    fac_supp = next(it for it in l1["items"] if it["kind"] == "suppressed_faction" and it["id"] == fac_id)
    assert fac_supp["injection"] == "suppressed"


# ---------------------------------------------------------------------------
# Service 层：CharacterService / WorldService 读写 aliases + inject_mode
# ---------------------------------------------------------------------------


def test_character_service_create_with_aliases_and_inject_mode(tmp_path: Path):
    from packages.domain.character.models import CharacterCreate

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    svc = CharacterService(db_path)
    payload = CharacterCreate(
        name="林昭",
        role="protagonist",
        aliases=["云隐剑", "少侠"],
        inject_mode="always",
    )
    char = svc.create(pid, payload)

    assert char["aliases"] == ["云隐剑", "少侠"]
    assert char["inject_mode"] == "always"

    # DB 实际写入
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT aliases, inject_mode FROM characters WHERE character_id = ?",
            (char["character_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(row["aliases"]) == ["云隐剑", "少侠"]
    assert row["inject_mode"] == "always"


def test_character_service_update_aliases_and_inject_mode(tmp_path: Path):
    from packages.domain.character.models import CharacterCreate, CharacterUpdate

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    svc = CharacterService(db_path)
    char = svc.create(
        pid,
        CharacterCreate(name="陈风", role="supporting", aliases=[], inject_mode="auto"),
    )
    upd = svc.update(
        char["character_id"],
        CharacterUpdate(aliases=["剑客"], inject_mode="never"),
    )
    assert upd["aliases"] == ["剑客"]
    assert upd["inject_mode"] == "never"


def test_world_service_create_location_with_aliases(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    svc = WorldService(db_path)
    loc = svc.create_location(
        project_id=pid,
        name="王城",
        statement="帝国首都",
        aliases=["云端之城"],
        inject_mode="always",
    )
    assert loc.aliases == ["云端之城"]
    assert loc.inject_mode == "always"
    assert loc.to_dict()["aliases"] == ["云端之城"]
    assert loc.to_dict()["inject_mode"] == "always"


def test_world_service_create_faction_with_inject_mode_never(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    svc = WorldService(db_path)
    fac = svc.create_faction(
        project_id=pid,
        name="刺客会",
        statement="暗中活动",
        inject_mode="never",
    )
    assert fac.inject_mode == "never"


def test_world_service_update_aliases(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    svc = WorldService(db_path)
    loc = svc.create_location(pid, name="王城", statement="帝国首都")
    upd = svc.update_location(loc.id, aliases=["帝都"], inject_mode="always")
    assert upd.aliases == ["帝都"]
    assert upd.inject_mode == "always"


def test_world_service_rejects_invalid_inject_mode(tmp_path: Path):
    """Service 层 inject_mode 非法值 → ValidationError（422）。"""
    from packages.domain.world.service import ValidationError

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    svc = WorldService(db_path)
    with pytest.raises(ValidationError):
        svc.create_location(pid, name="X", inject_mode="bogus")
    with pytest.raises(ValidationError):
        svc.create_faction(pid, name="X", inject_mode="")


def test_world_service_create_location_without_aliases_defaults_to_empty(tmp_path: Path):
    """不传 aliases → 写入 '[]'（与 DB DEFAULT 对齐）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    svc = WorldService(db_path)
    loc = svc.create_location(pid, name="无名地", statement="")
    assert loc.aliases == []
    assert loc.inject_mode == "auto"