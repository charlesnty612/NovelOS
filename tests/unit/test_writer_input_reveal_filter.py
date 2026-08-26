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
