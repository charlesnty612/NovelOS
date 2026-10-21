"""build_writer_input paged 模式 + reveal_policies 二次过滤单测（V3.3 P0-2）。

覆盖：
- 无 reveal_policy → HIDDEN 实体仍出现（行为与原实现一致）；
- HIDDEN 实体 + planned reader-audience policy → 实体从 character_state_excerpts /
  world_state_excerpts.locations / .active_factions 中**移除**（连摘要也不留）；
- planned character-only audience（'character:char_x'）→ 同样屏蔽（writer 无角色绑定）；
- 同一实体改 status='revealed' → 实体重新出现；
- 非 HIDDEN 实体（PUBLIC/VISIBLE/RESTRICTED）+ planned policy → 不受影响（仅 HIDDEN
  触发移除；planned policy 单独存在不代表 HIDDEN）；
- full 模式不受影响（reveal_policy 过滤仅在 paged 模式生效）；
- 统计字段 stats.hidden_filtered 准确；
- observer 输入**不受影响**（observer 是作者视角，需要看到 HIDDEN）；
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.core.context_engine.builders import (  # noqa: E402
    _audience_blocks_writer,
    _cache_reset,
    build_director_input,
    build_observer_input,
    build_writer_input,
)
from packages.core.db import apply_migrations, get_connection  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402
from packages.domain.knowledge import RevealPolicyService  # noqa: E402
from packages.domain.project.service import ProjectService  # noqa: E402

MIGRATIONS_DIR = ROOT / "database" / "migrations"


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _project(db_path: Path) -> str:
    return ProjectService(db_path).create(
        type("P", (), {"name": "p", "premise": None, "genre": None,
                        "target_words": None, "foreshadow_overdue_chapters": None})(),
    )["project_id"]


def _chapter(db_path: Path, project_id: str, number: int = 1) -> str:
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


def _character(db_path: Path, pid: str, cid: str, visibility: str = "HIDDEN") -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO characters (character_id, project_id, name, role, core_json,
                                    visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, 'supporting', '{}', ?, NULL, ?, ?)
            """,
            (cid, pid, cid, visibility, now, now),
        )
        conn.execute(
            """
            INSERT INTO character_states (character_id, state_version, state_json,
                                          visibility, created_at)
            VALUES (?, 1, '{}', 'VISIBLE', ?)
            """,
            (cid, now),
        )
        conn.commit()
    finally:
        conn.close()


def _location(db_path: Path, pid: str, lid: str, visibility: str = "HIDDEN") -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO locations (location_id, project_id, name, statement, data_json,
                                   visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, '', '{}', ?, NULL, ?, ?)
            """,
            (lid, pid, lid, visibility, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _faction(db_path: Path, pid: str, fid: str, visibility: str = "HIDDEN") -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO factions (faction_id, project_id, name, statement, data_json,
                                  visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, '', '{}', ?, NULL, ?, ?)
            """,
            (fid, pid, fid, visibility, now, now),
        )
        conn.commit()
    finally:
        conn.close()


_SCENE_PLAN = {
    "purpose": "夜访",
    "characters": ["char_alice"],
    "location": "loc_village",
    "conflict": "信任测试",
    "turn": "试探",
}


# ---------------------------------------------------------------------------
# _audience_blocks_writer：纯函数单元
# ---------------------------------------------------------------------------


def test_audience_blocks_writer_reader_only():
    """任务书口径：audience 含 reader → 屏蔽（writer 是 reader 视角）。"""
    assert _audience_blocks_writer("reader") is True


def test_audience_blocks_writer_character_only():
    """character-only audience 不阻断 writer（writer 无角色绑定，但也不是
    策略受众；HIDDEN 实体的处理由 visibility + 是否存在 reader-policy 决定）。"""
    assert _audience_blocks_writer("character:char_x") is False


def test_audience_blocks_writer_mixed_reader_and_character():
    # 混合：含 reader → 触发屏蔽
    assert _audience_blocks_writer("reader,character:char_x") is True


def test_audience_blocks_writer_character_only_multi():
    # 多个 character: 前缀，无 reader → 不触发
    assert _audience_blocks_writer("character:a,character:b") is False


def test_audience_blocks_writer_empty_or_invalid():
    assert _audience_blocks_writer("") is False
    assert _audience_blocks_writer("   ") is False


# ---------------------------------------------------------------------------
# paged writer 过滤：HIDDEN + planned reader-policy → 移除
# ---------------------------------------------------------------------------


def test_paged_no_policy_keeps_hidden_entity_unchanged(tmp_path: Path):
    """零破坏：无任何 reveal_policy → 行为与原实现一致（HIDDEN 实体仍出现）。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _character(db_path, pid, "char_alice", visibility="HIDDEN")
    payload = build_writer_input(
        db_path, cid, _SCENE_PLAN, context_mode="paged",
    )
    chars = payload["character_state_excerpts"]
    assert any(c.get("character_id") == "char_alice" for c in chars)
    assert payload["context_paging_stats"]["hidden_filtered"] == 0


def test_paged_hidden_entity_with_planned_reader_policy_is_removed(tmp_path: Path):
    """HIDDEN character + planned reader-audience policy → 实体从字符列表移除。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _character(db_path, pid, "char_alice", visibility="HIDDEN")
    # 创建 planned reader policy
    RevealPolicyService(db_path).create(
        project_id=pid,
        target_kind="character",
        target_id="char_alice",
        audience="reader",
    )
    payload = build_writer_input(
        db_path, cid, _SCENE_PLAN, context_mode="paged",
    )
    chars = payload["character_state_excerpts"]
    # HIDDEN 实体已整体移除（无任何条目）
    assert not any(c.get("character_id") == "char_alice" for c in chars)
    # stats.hidden_filtered == 1
    assert payload["context_paging_stats"]["hidden_filtered"] == 1


def test_paged_hidden_location_with_planned_policy_is_removed(tmp_path: Path):
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _location(db_path, pid, "loc_village", visibility="HIDDEN")
    RevealPolicyService(db_path).create(
        project_id=pid,
        target_kind="location",
        target_id="loc_village",
    )
    payload = build_writer_input(
        db_path, cid, _SCENE_PLAN, context_mode="paged",
    )
    locs = payload["world_state_excerpts"]["locations"]
    assert not any(loc.get("location_id") == "loc_village" for loc in locs)
    assert payload["context_paging_stats"]["hidden_filtered"] == 1


def test_paged_hidden_faction_with_planned_policy_is_removed(tmp_path: Path):
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _faction(db_path, pid, "fac_evil", visibility="HIDDEN")
    RevealPolicyService(db_path).create(
        project_id=pid,
        target_kind="faction",
        target_id="fac_evil",
    )
    payload = build_writer_input(
        db_path, cid, _SCENE_PLAN, context_mode="paged",
    )
    facs = payload["world_state_excerpts"]["active_factions"]
    assert not any(f.get("faction_id") == "fac_evil" for f in facs)
    assert payload["context_paging_stats"]["hidden_filtered"] == 1


def test_paged_character_only_audience_does_not_block_writer(tmp_path: Path):
    """任务书口径：audience 仅含 'character:<id>'（无 reader）→ 不构成对 writer
    的阻断。HIDDEN 实体仍出现（HIDDEN + 无 reader-policy 等同无 policy 行为）。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _character(db_path, pid, "char_alice", visibility="HIDDEN")
    RevealPolicyService(db_path).create(
        project_id=pid,
        target_kind="character",
        target_id="char_alice",
        audience="character:char_alice",
    )
    payload = build_writer_input(
        db_path, cid, _SCENE_PLAN, context_mode="paged",
    )
    assert any(c.get("character_id") == "char_alice" for c in payload["character_state_excerpts"])
    assert payload["context_paging_stats"]["hidden_filtered"] == 0


def test_paged_revealed_status_restores_entity(tmp_path: Path):
    """同一实体改 status='revealed' → 不再被过滤，重新出现在 payload 中。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _character(db_path, pid, "char_alice", visibility="HIDDEN")
    svc = RevealPolicyService(db_path)
    pol = svc.create(
        project_id=pid,
        target_kind="character",
        target_id="char_alice",
    )
    # planned → 被移除
    payload = build_writer_input(
        db_path, cid, _SCENE_PLAN, context_mode="paged",
    )
    assert not any(c.get("character_id") == "char_alice" for c in payload["character_state_excerpts"])
    assert payload["context_paging_stats"]["hidden_filtered"] == 1
    # 改 status='revealed'
    _cache_reset()
    svc.update(pol.policy_id, status="revealed", revealed_chapter=3)
    payload = build_writer_input(
        db_path, cid, _SCENE_PLAN, context_mode="paged",
    )
    chars = payload["character_state_excerpts"]
    assert any(c.get("character_id") == "char_alice" for c in chars)
    assert payload["context_paging_stats"]["hidden_filtered"] == 0


def test_paged_cancelled_status_does_not_filter(tmp_path: Path):
    """cancelled 状态 → 不参与过滤（HIDDEN 实体恢复出现）。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _character(db_path, pid, "char_alice", visibility="HIDDEN")
    svc = RevealPolicyService(db_path)
    pol = svc.create(
        project_id=pid,
        target_kind="character",
        target_id="char_alice",
    )
    svc.update(pol.policy_id, status="cancelled")
    payload = build_writer_input(
        db_path, cid, _SCENE_PLAN, context_mode="paged",
    )
    assert any(c.get("character_id") == "char_alice" for c in payload["character_state_excerpts"])
    assert payload["context_paging_stats"]["hidden_filtered"] == 0


def test_paged_non_hidden_entity_with_planned_policy_unchanged(tmp_path: Path):
    """非 HIDDEN（VISIBLE/PUBLIC/RESTRICTED）+ planned policy → 不触发移除
    （planned policy 单独存在不代表 HIDDEN）。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _character(db_path, pid, "char_alice", visibility="VISIBLE")
    RevealPolicyService(db_path).create(
        project_id=pid,
        target_kind="character",
        target_id="char_alice",
    )
    payload = build_writer_input(
        db_path, cid, _SCENE_PLAN, context_mode="paged",
    )
    assert any(c.get("character_id") == "char_alice" for c in payload["character_state_excerpts"])
    assert payload["context_paging_stats"]["hidden_filtered"] == 0


def test_paged_multiple_hidden_entities_counted(tmp_path: Path):
    """多个 HIDDEN + planned reader → stats.hidden_filtered == 多个之和。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _character(db_path, pid, "char_alice", visibility="HIDDEN")
    _character(db_path, pid, "char_bob", visibility="HIDDEN")
    _character(db_path, pid, "char_carol", visibility="VISIBLE")
    _location(db_path, pid, "loc_cave", visibility="HIDDEN")
    svc = RevealPolicyService(db_path)
    for kind, tid in (
        ("character", "char_alice"),
        ("character", "char_bob"),
        ("location", "loc_cave"),
    ):
        svc.create(project_id=pid, target_kind=kind, target_id=tid)
    payload = build_writer_input(
        db_path, cid, _SCENE_PLAN, context_mode="paged",
    )
    chars = payload["character_state_excerpts"]
    assert not any(c.get("character_id") in ("char_alice", "char_bob") for c in chars)
    assert any(c.get("character_id") == "char_carol" for c in chars)
    assert payload["context_paging_stats"]["hidden_filtered"] == 3


def test_paged_full_mode_ignores_reveal_policy(tmp_path: Path):
    """full 模式：reveal_policy 过滤不生效（HIDDEN 实体仍出现）。"""
    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _character(db_path, pid, "char_alice", visibility="HIDDEN")
    RevealPolicyService(db_path).create(
        project_id=pid,
        target_kind="character",
        target_id="char_alice",
    )
    payload = build_writer_input(
        db_path, cid, _SCENE_PLAN, context_mode="full",
    )
    # full 模式无 stats.hidden_filtered 字段
    assert "context_paging_stats" not in payload
    # 字符仍出现
    assert any(c.get("character_id") == "char_alice" for c in payload["character_state_excerpts"])


def test_observer_input_unaffected_by_reveal_policy(tmp_path: Path):
    """observer 路径**不动**：HIDDEN 实体 + planned policy → observer payload
    仍包含该实体（作者视角必须看到全部）。"""
    db_path = _fresh_db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _character(db_path, pid, "char_alice", visibility="HIDDEN")
    RevealPolicyService(db_path).create(
        project_id=pid,
        target_kind="character",
        target_id="char_alice",
    )
    payload = build_observer_input(db_path, cid)
    # observer.previous_state.characters 应包含 HIDDEN char_alice（observer 全可见）
    chars = payload["previous_state"].get("characters") or []
    assert any(c.get("character_id") == "char_alice" for c in chars)


# ---------------------------------------------------------------------------
# 2026-10-21（P1-3 / P1-4）：**装配缓存键**必须带 world 三表与 reveal_policies 维度。
#
# 缺陷形状（与 E1 角色指纹逐字同形）：``_world_state_excerpts`` 整份读
# locations / factions / world_rules 进 payload；writer 侧还按 reveal_policies 裁剪
# HIDDEN 实体。两者都是装配输入，而 ``packages/domain/**`` 内**没有任何 story_states
# 写入**（手工 PATCH 设定、新加一条 planned+reader 策略都不推 ``state_version``），
# 缓存失效又只在 chapter_commit 后按章兜底 ⇒ 重跑同一章会命中旧装配：
#   · 改世界观 → 正文沿用旧设定；
#   · 新加知识权限策略 → HIDDEN 角色仍在 writer 上下文里（**剧透**，该拦的没拦）。
#
# 判别口径同 ``test_e1_character_cache_key.py``：``_cache_reset`` + 统计真实重装次数
# （深拷贝下对象身份不可用，命中/未命中只能数 ``_build_*_uncached`` 调用）。
# ---------------------------------------------------------------------------


def _spy_uncached(monkeypatch, module, attr: str) -> dict[str, int]:
    counter = {"n": 0}
    real = getattr(module, attr)

    def counting(*args, **kwargs):
        counter["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, attr, counting)
    return counter


def _set_location(db_path: Path, location_id: str, *, statement: str, data_json: str) -> None:
    """改地点设定，并钉 ``inject_mode='always'``。

    夹具纪律（否则断言红在「excerpt 被裁剪」而不是「缓存脏命中」）：``auto`` 模式下
    未命中触发语料的地点只产出 ``{location_id, name, summary_marker}``——``statement``
    压根不在 payload 里，测不到内容变化。
    """
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE locations SET statement = ?, data_json = ?, inject_mode = 'always', "
            "updated_at = ? WHERE location_id = ?",
            (statement, data_json, now_iso(), location_id),
        )
        conn.commit()
    finally:
        conn.close()


def _location_statements(payload: dict) -> list[str]:
    world = payload.get("world_state_excerpts") or {}
    out = []
    for loc in world.get("locations") or []:
        if isinstance(loc, dict) and loc.get("statement") is not None:
            out.append(str(loc["statement"]))
    return out


def test_world_change_misses_writer_cache(tmp_path: Path, monkeypatch):
    """P1-3：改 locations 设定后重跑同一章 ⇒ 必须重装（不得命中旧装配）。"""
    from packages.core.context_engine import writer_input as wi_mod

    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _location(db_path, pid, "loc_dock", visibility="VISIBLE")
    _set_location(db_path, "loc_dock", statement="OLD-城南码头", data_json="{}")
    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    p1 = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="full")
    assert any("OLD-城南码头" == s for s in _location_statements(p1)), _location_statements(p1)
    build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="full")
    assert calls["n"] == 1, "未改动时必须命中缓存（量具自检）"

    # 作者在世界页把地点改写成「城北」（state_version / chapter_no 均不变）
    _set_location(db_path, "loc_dock", statement="NEW-城北码头", data_json='{"tide":"low"}')

    p2 = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="full")
    assert calls["n"] == 2, "改世界观设定后必须重装——否则命中旧装配（正文沿用旧设定）"
    assert any("NEW-城北码头" == s for s in _location_statements(p2)), (
        f"重装后必须读到新设定，实得 {_location_statements(p2)}"
    )


def test_world_change_misses_director_cache(tmp_path: Path, monkeypatch):
    """P1-3：director 键同样必须带 world 维度（planner 不能拿旧世界设定排计划）。"""
    from packages.core.context_engine import director_input as di_mod

    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _location(db_path, pid, "loc_dock", visibility="VISIBLE")
    _set_location(db_path, "loc_dock", statement="OLD-城南码头", data_json="{}")
    calls = _spy_uncached(monkeypatch, di_mod, "_build_director_input_uncached")

    p1 = build_director_input(db_path, pid, cid, "意图")
    assert any("OLD-城南码头" == s for s in _location_statements(p1)), _location_statements(p1)
    build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 1, "未改动时必须命中缓存（量具自检）"

    _set_location(db_path, "loc_dock", statement="NEW-城北码头", data_json="{}")

    p2 = build_director_input(db_path, pid, cid, "意图")
    assert calls["n"] == 2, "改世界观设定后 director 必须重装"
    assert any("NEW-城北码头" == s for s in _location_statements(p2)), _location_statements(p2)


def test_new_reveal_policy_misses_writer_cache(tmp_path: Path, monkeypatch):
    """P1-4：**新加一条 planned+reader 策略必须 miss**——否则 HIDDEN 角色继续剧透。

    这是最危险的方向：该拦住的没拦住（writer 上下文里还能看到未揭示实体）。
    """
    from packages.core.context_engine import writer_input as wi_mod

    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _character(db_path, pid, "char_alice", visibility="HIDDEN")
    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    p1 = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    assert any(
        c.get("character_id") == "char_alice"
        for c in p1["character_state_excerpts"]
    ), "无 policy 时 HIDDEN 角色应出现（零破坏前提）"
    build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    assert calls["n"] == 1, "未改动时必须命中缓存（量具自检）"

    # 作者新增一条「planned + reader 受众」的知识权限策略（不推 state_version）
    RevealPolicyService(db_path).create(
        project_id=pid,
        target_kind="character",
        target_id="char_alice",
        audience="reader",
    )

    p2 = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    assert calls["n"] == 2, (
        "新增 reveal_policy 后必须重装——否则命中旧装配，HIDDEN 角色继续留在 writer "
        "上下文里（剧透方向，零告警）"
    )
    assert not any(
        c.get("character_id") == "char_alice"
        for c in p2["character_state_excerpts"]
    ), "重装后该角色必须被 reveal_policy 过滤掉"
    assert p2["context_paging_stats"]["hidden_filtered"] >= 1


def test_reveal_policy_status_change_misses_writer_cache(tmp_path: Path, monkeypatch):
    """P1-4 反向：把策略改成 ``revealed`` 后，实体必须重新出现（也要 miss）。"""
    from packages.core.context_engine import writer_input as wi_mod

    _cache_reset()
    db_path = _fresh_db(tmp_path)
    pid = _project(db_path)
    cid = _chapter(db_path, pid)
    _character(db_path, pid, "char_alice", visibility="HIDDEN")
    svc = RevealPolicyService(db_path)
    svc.create(
        project_id=pid,
        target_kind="character",
        target_id="char_alice",
        audience="reader",
    )
    calls = _spy_uncached(monkeypatch, wi_mod, "_build_writer_input_uncached")

    p1 = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    assert not any(c.get("character_id") == "char_alice" for c in p1["character_state_excerpts"])
    build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    assert calls["n"] == 1, "未改动时必须命中缓存（量具自检）"

    # 揭示该实体（status → revealed）
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE reveal_policies SET status = 'revealed', updated_at = ? "
            "WHERE target_id = 'char_alice'",
            (now_iso(),),
        )
        conn.commit()
    finally:
        conn.close()

    p2 = build_writer_input(db_path, cid, _SCENE_PLAN, context_mode="paged")
    assert calls["n"] == 2, "改 policy 状态后必须重装"
    assert any(
        c.get("character_id") == "char_alice"
        for c in p2["character_state_excerpts"]
    ), "已揭示的实体必须重新出现在 writer 上下文里"
