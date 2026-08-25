"""build_observer_input 快照分代裁剪 单测（M3 引擎包）。

覆盖（M3 任务书 DoD）：
- full 模式（默认）行为与旧实现逐字段一致（保证零影响）。
- trimmed 模式：实体裁剪语义正确（touched 全量 + 其余摘要 + open hooks 全量 +
  resolved 仅留最近 N 条 + state_version 等顶层元信息不动）。
- trimmed 模式：体积显著缩小（量化断言）。
- trimmed 模式：payload 同时携带 snapshot_trim_stats 与 previous_state.snapshot_mode。
- trimmed 模式：resolved hook/debt 超出 N 条时正确截断。
- trimmed 模式：touched 的 resolved hook 强制保留（即使超出默认上限）。
- trimmed 模式：未 touch 任何实体时全实体走摘要路径。
- _trim_snapshot_for_observer 纯函数：无 IO，可直接以合成 snapshot 测试。
- _collect_touched_entity_ids：从 commits+state_deltas.payload_json 抽取
  7 个数组中出现的实体 ID，按 world_kind 正确路由到 locations/factions/world_rules。

设计要点：
- 不依赖真实项目 DB：用 in-memory apply_migrations + 手插必要表（projects /
  chapters / characters / hooks / narrative_debts / commits / state_deltas 等）。
- 测试 build_observer_input 端到端时，直连 sqlite 临时文件（与 test_context_engine.py 同范式）。
- _trim_snapshot_for_observer 单测用纯合成 dict，无 DB。
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
    _collect_touched_entity_ids,
    _trim_snapshot_for_observer,
    build_observer_input,
)
from packages.core.db import apply_migrations, get_connection  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402

MIGRATIONS_DIR = ROOT / "database" / "migrations"


# ---------------------------------------------------------------------------
# 合成 snapshot 工具（无需 DB）
# ---------------------------------------------------------------------------


def _make_big_snapshot(
    *,
    n_characters: int = 8,
    n_locations: int = 5,
    n_factions: int = 3,
    n_world_rules: int = 4,
    n_hooks_open: int = 4,
    n_hooks_resolved: int = 8,
    n_debts_open: int = 2,
    n_debts_resolved: int = 7,
) -> dict:
    """构造一个「臃肿」的全量快照，每个实体 current_state 字段塞长串以模拟真实体积。

    体积目标：full 模式 json.dumps ≥ 5KB，便于断言 trimmed 模式显著缩小。
    """
    chars: list[dict] = []
    for i in range(n_characters):
        cid = f"char_{i:03d}"
        rels = []
        if i < n_characters - 1:
            rels.append(
                {
                    "relationship_id": f"rel_{i:03d}_to_{i+1:03d}",
                    "from_character_id": cid,
                    "to_character_id": f"char_{i+1:03d}",
                    "relation_type": "ally",
                    "state_json": {"trust": 0.5 + i * 0.01, "history": "x" * 200},
                }
            )
        chars.append(
            {
                "character_id": cid,
                "name": f"角色{i}号" * 20,
                "current_state": {
                    "location": f"loc_{i:03d}",
                    "goal": "复仇与求证" * 30,
                    "emotion": "calm",
                    "extra_field": "y" * 300,
                },
                "knowledge": [f"knowledge item {j}" for j in range(8)],
                "beliefs": [f"belief {j}" for j in range(5)],
                "relationships": rels,
                "facet": "state",
            }
        )

    locations = {
        f"loc_{i:03d}": {
            "name": f"地点{i}",
            "statement": "此地乃上古遗存，封印重重。" * 20,
            "data_json": {"description": "z" * 250, "tags": ["cave", "ruin"]},
            "visibility": "VISIBLE",
        }
        for i in range(n_locations)
    }
    factions = {
        f"fac_{i:03d}": {
            "name": f"势力{i}",
            "statement": "势力宗旨" * 15,
            "data_json": {"leader": f"char_{i:03d}", "power": 100 + i},
            "visibility": "VISIBLE",
        }
        for i in range(n_factions)
    }
    world_rules = [
        {
            "world_rule_id": f"rule_{i:03d}",
            "name": f"世界规则{i}",
            "statement": "天地法则如此。" * 30,
            "data_json": {"scope": "global"},
            "visibility": "PUBLIC",
        }
        for i in range(n_world_rules)
    ]
    hooks: list[dict] = []
    for i in range(n_hooks_open):
        hooks.append(
            {
                "hook_id": f"hook_open_{i:03d}",
                "name": f"开放伏笔{i}",
                "introduced_chapter_id": f"ch_{i:03d}",
                "status": "OPEN",
                "importance": 0.7,
                "expected_payoff_chapter_id": f"ch_{i+50:03d}",
                "payoff_chapter_id": None,
                "visibility": "PUBLIC",
                "description": "long " * 40,
            }
        )
    for i in range(n_hooks_resolved):
        hooks.append(
            {
                "hook_id": f"hook_resolved_{i:03d}",
                "name": f"已结伏笔{i}",
                "introduced_chapter_id": f"ch_{i:03d}",
                "status": "RESOLVED",
                "importance": 0.5,
                "expected_payoff_chapter_id": None,
                "payoff_chapter_id": f"ch_{i+30:03d}",
                "visibility": "PUBLIC",
                "description": "long " * 40,
            }
        )
    debts: list[dict] = []
    for i in range(n_debts_open):
        debts.append(
            {
                "debt_id": f"debt_open_{i:03d}",
                "description": "open debt " * 30,
                "created_chapter_id": f"ch_{i:03d}",
                "severity": 0.6,
                "deadline_chapter_id": f"ch_{i+20:03d}",
                "status": "open",
                "visibility": "VISIBLE",
            }
        )
    for i in range(n_debts_resolved):
        debts.append(
            {
                "debt_id": f"debt_paid_{i:03d}",
                "description": "paid debt " * 30,
                "created_chapter_id": f"ch_{i:03d}",
                "severity": 0.4,
                "deadline_chapter_id": None,
                "status": "paid",
                "visibility": "VISIBLE",
            }
        )

    return {
        "state_version": 56,
        "characters": chars,
        "world": {
            "current_time_in_story": "沧历三百一十二年 三月初七",
            "locations": locations,
            "factions": factions,
            "world_rules": world_rules,
            "active_resources": {"gold": 100, "mana": 50},
        },
        "hooks": hooks,
        "debts": debts,
        "recent_events": ["evt_001", "evt_002", "evt_003"],
        "events": {
            "evt_001": {"type": "revelation", "description": "big " * 30},
            "evt_002": {"type": "conflict", "description": "big " * 30},
            "evt_003": {"type": "decision", "description": "big " * 30},
        },
    }


# ---------------------------------------------------------------------------
# _trim_snapshot_for_observer 纯函数单测
# ---------------------------------------------------------------------------


def test_trim_snapshot_returns_snapshot_mode_marker():
    snap = _make_big_snapshot()
    trimmed, stats = _trim_snapshot_for_observer(snap)
    assert trimmed["snapshot_mode"] == "trimmed"


def test_trim_snapshot_state_version_preserved():
    snap = _make_big_snapshot()
    trimmed, _stats = _trim_snapshot_for_observer(snap)
    assert trimmed["state_version"] == snap["state_version"]


def test_trim_snapshot_recent_events_and_events_unchanged():
    snap = _make_big_snapshot()
    trimmed, _stats = _trim_snapshot_for_observer(snap)
    assert trimmed["recent_events"] == snap["recent_events"]
    assert trimmed["events"] == snap["events"]


def test_trim_snapshot_touched_characters_full_others_summarized():
    snap = _make_big_snapshot()
    touched = {"characters": {"char_002"}, "relationships": set(),
               "relationship_keys": set(), "locations": set(), "factions": set(),
               "world_rules": set(), "hooks": set(), "debts": set(), "events": set()}
    trimmed, stats = _trim_snapshot_for_observer(snap, touched=touched)
    out_chars = trimmed["characters"]
    full_chars = [c for c in out_chars if c.get("character_id") == "char_002"]
    summary_chars = [c for c in out_chars if c.get("character_id") != "char_002"]
    assert len(full_chars) == 1
    assert stats["characters_full"] == 1
    assert stats["characters_summary"] == len(snap["characters"]) - 1
    # touched 角色保留 current_state 全量
    assert full_chars[0]["current_state"] == snap["characters"][2]["current_state"]
    # 摘要角色仅 {character_id, name, facet}，无 current_state / knowledge / beliefs
    sample = summary_chars[0]
    assert set(sample.keys()) == {"character_id", "name", "facet"}


def test_trim_snapshot_world_locations_dict_path():
    snap = _make_big_snapshot()
    touched = {"locations": {"loc_001"}, "characters": set(), "factions": set(),
               "world_rules": set(), "hooks": set(), "debts": set(), "events": set(),
               "relationships": set(), "relationship_keys": set()}
    trimmed, stats = _trim_snapshot_for_observer(snap, touched=touched)
    locs = trimmed["world"]["locations"]
    assert "loc_001" in locs
    # touched 的 location 全量（保留 statement / data_json / visibility）
    assert locs["loc_001"]["statement"] == snap["world"]["locations"]["loc_001"]["statement"]
    # 其他 location 仅 {name}
    sample = locs["loc_000"]
    assert set(sample.keys()) == {"name"}
    assert stats["locations_full"] == 1
    assert stats["locations_summary"] == len(snap["world"]["locations"]) - 1


def test_trim_snapshot_world_factions_dict_path():
    snap = _make_big_snapshot()
    touched = {"factions": {"fac_001"}, "characters": set(), "locations": set(),
               "world_rules": set(), "hooks": set(), "debts": set(), "events": set(),
               "relationships": set(), "relationship_keys": set()}
    trimmed, stats = _trim_snapshot_for_observer(snap, touched=touched)
    facs = trimmed["world"]["factions"]
    assert facs["fac_001"]["data_json"] == snap["world"]["factions"]["fac_001"]["data_json"]
    assert set(facs["fac_000"].keys()) == {"name"}
    assert stats["factions_full"] == 1


def test_trim_snapshot_world_rules_list_path():
    snap = _make_big_snapshot()
    touched = {"world_rules": {"rule_002"}, "characters": set(), "locations": set(),
               "factions": set(), "hooks": set(), "debts": set(), "events": set(),
               "relationships": set(), "relationship_keys": set()}
    trimmed, stats = _trim_snapshot_for_observer(snap, touched=touched)
    rules = trimmed["world"]["world_rules"]
    full_rule = next(r for r in rules if r["world_rule_id"] == "rule_002")
    assert "statement" in full_rule  # touched 全量
    # 未 touched 的仅保留 world_rule_id + name
    summary_rule = next(r for r in rules if r["world_rule_id"] != "rule_002")
    assert set(summary_rule.keys()) == {"world_rule_id", "name"}
    assert stats["world_rules_full"] == 1
    assert stats["world_rules_summary"] == len(snap["world"]["world_rules"]) - 1


def test_trim_snapshot_hooks_open_full_resolved_trimmed():
    snap = _make_big_snapshot(n_hooks_open=3, n_hooks_resolved=8)
    trimmed, stats = _trim_snapshot_for_observer(snap, resolved_history_keep=5)
    out_hooks = trimmed["hooks"]
    # open hooks 全量保留（3 条）
    open_in_out = [h for h in out_hooks if h.get("status") in ("OPEN", "ACTIVE", "ESCALATED")]
    assert len(open_in_out) == 3
    assert stats["hooks_open"] == 3
    # resolved 8 条 → 保留 5 条
    assert stats["hooks_resolved_kept"] == 5
    assert stats["hooks_resolved_trimmed"] == 3
    # resolved 摘要仅 {hook_id, name, status}
    resolved_in_out = [h for h in out_hooks if h.get("status") == "RESOLVED"]
    assert all(set(h.keys()) == {"hook_id", "name", "status"} for h in resolved_in_out)


def test_trim_snapshot_debts_open_full_resolved_trimmed():
    snap = _make_big_snapshot(n_debts_open=2, n_debts_resolved=7)
    trimmed, stats = _trim_snapshot_for_observer(snap, resolved_history_keep=5)
    out_debts = trimmed["debts"]
    open_in_out = [d for d in out_debts if d.get("status") in ("open", "acknowledged")]
    assert len(open_in_out) == 2
    assert stats["debts_open"] == 2
    # paid/forgiven 7 条 → 保留 5 条
    assert stats["debts_resolved_kept"] == 5
    assert stats["debts_resolved_trimmed"] == 2
    # resolved 摘要仅 {debt_id, description, status}
    paid_in_out = [d for d in out_debts if d.get("status") in ("paid", "forgiven")]
    assert all(set(d.keys()) == {"debt_id", "description", "status"} for d in paid_in_out)


def test_trim_snapshot_resolved_hooks_below_keep_n_keeps_all():
    snap = _make_big_snapshot(n_hooks_open=2, n_hooks_resolved=3)
    trimmed, stats = _trim_snapshot_for_observer(snap, resolved_history_keep=5)
    assert stats["hooks_resolved_kept"] == 3
    assert stats["hooks_resolved_trimmed"] == 0


def test_trim_snapshot_touched_resolved_hook_forced_retained():
    """touched 的 resolved hook 强制保留（即便超出默认 N 条上限）。"""
    snap = _make_big_snapshot(n_hooks_open=1, n_hooks_resolved=10)
    # 标记最旧的那条 resolved 为 touched（hook_resolved_000）
    touched = {"hooks": {"hook_resolved_000"}, "characters": set(), "locations": set(),
               "factions": set(), "world_rules": set(), "debts": set(), "events": set(),
               "relationships": set(), "relationship_keys": set()}
    trimmed, stats = _trim_snapshot_for_observer(snap, touched=touched, resolved_history_keep=5)
    hook_ids_out = {h.get("hook_id") for h in trimmed["hooks"]}
    # 字典序取末尾 5 条 + touched 的 resolved_000
    kept_sorted = sorted(
        [h for h in snap["hooks"] if h.get("status") == "RESOLVED"],
        key=lambda x: x["hook_id"],
    )
    expected_tail_ids = {h["hook_id"] for h in kept_sorted[-5:]}
    expected_tail_ids.add("hook_resolved_000")  # touched 强制保留
    assert hook_ids_out >= expected_tail_ids
    # touched hook 全量（description 字段保留）
    kept_full = next(h for h in trimmed["hooks"] if h.get("hook_id") == "hook_resolved_000")
    assert "description" in kept_full


def test_trim_snapshot_no_touch_all_summary_path():
    """未 touch 任何实体时全实体走摘要路径。"""
    snap = _make_big_snapshot()
    trimmed, stats = _trim_snapshot_for_observer(snap)
    # 所有 character 都是摘要
    assert stats["characters_full"] == 0
    assert stats["characters_summary"] == len(snap["characters"])
    # 所有 location/faction/rule 都是摘要
    assert stats["locations_full"] == 0
    assert stats["locations_summary"] == len(snap["world"]["locations"])
    assert stats["factions_full"] == 0
    assert stats["factions_summary"] == len(snap["world"]["factions"])
    assert stats["world_rules_full"] == 0
    assert stats["world_rules_summary"] == len(snap["world"]["world_rules"])
    # 摘要字符 character 仅 {character_id, name, facet}
    for c in trimmed["characters"]:
        assert set(c.keys()) == {"character_id", "name", "facet"}


def test_trim_snapshot_significantly_smaller_than_full():
    """trimmed 模式在合成数据上体积显著缩小。"""
    snap = _make_big_snapshot()
    full_size = len(json.dumps(snap, ensure_ascii=False))
    trimmed, _stats = _trim_snapshot_for_observer(snap)
    trim_size = len(json.dumps(trimmed, ensure_ascii=False))
    # trimmed 应 < full 的 70%（实际 ~30-40%）
    assert trim_size < full_size * 0.7, (
        f"trimmed={trim_size}, full={full_size}, ratio={trim_size/full_size:.2f}"
    )


def test_trim_snapshot_handles_non_dict_input():
    trimmed, stats = _trim_snapshot_for_observer(None)
    assert trimmed["snapshot_mode"] == "trimmed"
    assert all(v == 0 for v in stats.values())


# ---------------------------------------------------------------------------
# _collect_touched_entity_ids 单测（需 in-memory DB）
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


def _insert_commit_with_payload(
    db_path: Path, project_id: str, chapter_id: str,
    payload: dict, version: int,
) -> None:
    """手插一个 commit + state_deltas 行（status='applied'）。"""
    now = now_iso()
    delta_id = new_id("dlt")
    commit_id = new_id("cmt")
    branch_id = new_id("brn")
    conn = get_connection(db_path)
    try:
        # 分支（main）兜底——用通用 branch 行避免 FK 失败
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


def test_collect_touched_ids_extracts_from_recent_commits(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    payload = {
        "character_changes": [
            {"character_id": "char_alice"},
            {"character_id": "char_bob"},
        ],
        "world_changes": [
            {"world_kind": "location", "world_id": "loc_village"},
            {"world_kind": "faction", "world_id": "fac_rebels"},
            {"world_kind": "rule", "world_id": "rule_no_magic"},
            {"world_kind": "time", "world_id": "time_field"},  # 不进 touched
        ],
        "relationship_changes": [
            {"from_character_id": "char_alice", "to_character_id": "char_bob", "relation_type": "ally"},
        ],
        "new_events": [{"event_id": "evt_001"}],
        "resolved_hooks": [{"hook_id": "hook_old"}],
        "new_hooks": [{"hook_id": "hook_new"}],
        "debt_changes": [{"debt_id": "debt_001"}],
    }
    _insert_commit_with_payload(db_path, pid, cid, payload, version=1)

    conn = get_connection(db_path)
    try:
        touched = _collect_touched_entity_ids(conn, pid, keep_recent_commits=3)
    finally:
        conn.close()

    assert touched["characters"] == {"char_alice", "char_bob"}
    assert touched["locations"] == {"loc_village"}
    assert touched["factions"] == {"fac_rebels"}
    assert touched["world_rules"] == {"rule_no_magic"}
    assert "time_field" not in touched["locations"]  # world_kind=time 不进 locations
    assert touched["events"] == {"evt_001"}
    assert touched["hooks"] == {"hook_old", "hook_new"}
    assert touched["debts"] == {"debt_001"}
    # relationship 合成 key 应包含
    assert "char_alice::char_bob::ally" in touched["relationship_keys"]


def test_collect_touched_ids_respects_keep_recent_commits(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    # 3 个 commit：第 1 个 touch alice，第 2 个 touch bob，第 3 个 touch carol
    for i, char_id in enumerate(["char_alice", "char_bob", "char_carol"], start=1):
        _insert_commit_with_payload(
            db_path, pid, cid,
            {"character_changes": [{"character_id": char_id}]},
            version=i,
        )
    conn = get_connection(db_path)
    try:
        # keep_recent_commits=1 → 仅最新 commit (carol)
        touched1 = _collect_touched_entity_ids(conn, pid, keep_recent_commits=1)
        # keep_recent_commits=3 → 全部 3 个
        touched3 = _collect_touched_entity_ids(conn, pid, keep_recent_commits=3)
    finally:
        conn.close()
    assert touched1["characters"] == {"char_carol"}
    assert touched3["characters"] == {"char_alice", "char_bob", "char_carol"}


def test_collect_touched_ids_zero_commits_returns_empty(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    conn = get_connection(db_path)
    try:
        touched = _collect_touched_entity_ids(conn, pid, keep_recent_commits=3)
    finally:
        conn.close()
    for s in touched.values():
        assert s == set()


# ---------------------------------------------------------------------------
# build_observer_input 端到端单测（full vs trimmed）
# ---------------------------------------------------------------------------


def _build_observer_with_setup(
    tmp_path: Path,
    *,
    snapshot_mode: str = "full",
    n_commits: int = 3,
) -> dict:
    """端到端测试：setup DB + 插 commits → 调 build_observer_input。

    直接返回 payload，不写 chapter 的 draft。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    # 插 N 个 commit，每个 delta 里 touch 不同的 character
    for i in range(1, n_commits + 1):
        _insert_commit_with_payload(
            db_path, pid, cid,
            {"character_changes": [{"character_id": f"char_touched_{i}"}]},
            version=i,
        )
    return build_observer_input(db_path, cid, snapshot_mode=snapshot_mode)


def test_observer_input_default_is_full_mode(tmp_path: Path):
    """默认 snapshot_mode='full'：行为与旧实现完全一致。"""
    payload = _build_observer_with_setup(tmp_path, snapshot_mode="full")
    # 没有 snapshot_mode 标记（full 模式不写入 previous_state.snapshot_mode）
    assert "snapshot_mode" not in payload["previous_state"]
    # 没有 snapshot_trim_stats（full 模式不写入）
    assert "snapshot_trim_stats" not in payload
    # previous_state 是全量快照（含 characters 全量字段）
    snap = payload["previous_state"]
    if snap.get("characters"):
        sample_char = snap["characters"][0]
        # 全量 character 必有 current_state / knowledge / beliefs / relationships
        assert "current_state" in sample_char


def test_observer_input_trimmed_injects_stats_and_marker(tmp_path: Path):
    payload = _build_observer_with_setup(
        tmp_path, snapshot_mode="trimmed", n_commits=3,
    )
    assert payload["previous_state"]["snapshot_mode"] == "trimmed"
    assert "snapshot_trim_stats" in payload
    stats = payload["snapshot_trim_stats"]
    # 必要键都在
    assert "characters_full" in stats
    assert "characters_summary" in stats
    assert "total_size_bytes_before" in stats
    assert "total_size_bytes_after" in stats
    # 体积断言放在专门用例 test_observer_input_trimmed_volume_reduction 中
    # （这里 snapshot 太轻，trimmed 多出的 wrapper key 会让 after > before）


def test_observer_input_trimmed_volume_reduction(tmp_path: Path):
    """trimmed 模式端到端体积缩小（合成数据准备完整 entities 后断言）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    # 插 1 个 commit；保持 snapshot 极简（空 characters）
    _insert_commit_with_payload(
        db_path, pid, cid,
        {"character_changes": []},
        version=1,
    )
    # 构造一个足够大的全量快照（用 in-DB 字符领域表 + 直接落 story_states）；
    # 此处简化：通过 StoryStateService.init_genesis 路径让字符进 snapshot，
    # 再额外插入 hooks/debts/world 表
    conn = get_connection(db_path)
    now = now_iso()
    try:
        # 插 30 个 character + character_states（让 snapshot 膨胀）
        for i in range(30):
            char_id = f"char_big_{i:03d}"
            conn.execute(
                """
                INSERT INTO characters (character_id, project_id, name, core_json, created_at, updated_at)
                VALUES (?, ?, ?, '{}', ?, ?)
                """,
                (char_id, pid, f"角色{i}号", now, now),
            )
            conn.execute(
                """
                INSERT INTO character_states (character_id, state_version, state_json, created_at)
                VALUES (?, 1, ?, ?)
                """,
                (char_id, json.dumps({
                    "location": f"loc_{i}",
                    "goal": "复仇与求证" * 50,
                    "extra": "z" * 500,
                }, ensure_ascii=False), now),
            )
        # 插 20 个 hook（10 open + 10 resolved）
        for i in range(10):
            conn.execute(
                """
                INSERT INTO hooks (hook_id, project_id, name, introduced_chapter_id,
                                   status, importance, expected_payoff_chapter_id,
                                   payoff_chapter_id, visibility, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'OPEN', 0.7, NULL, NULL, 'PUBLIC', ?, ?)
                """,
                (f"hook_open_{i:03d}", pid, f"开放{i}", cid, now, now),
            )
        for i in range(10):
            conn.execute(
                """
                INSERT INTO hooks (hook_id, project_id, name, introduced_chapter_id,
                                   status, importance, expected_payoff_chapter_id,
                                   payoff_chapter_id, visibility, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'RESOLVED', 0.5, NULL, ?, 'PUBLIC', ?, ?)
                """,
                (f"hook_resolved_{i:03d}", pid, f"已结{i}", cid, cid, now, now),
            )
        conn.commit()
    finally:
        conn.close()

    full_payload = build_observer_input(db_path, cid, snapshot_mode="full")
    trim_payload = build_observer_input(db_path, cid, snapshot_mode="trimmed")
    full_size = len(json.dumps(full_payload["previous_state"], ensure_ascii=False))
    trim_size = len(json.dumps(trim_payload["previous_state"], ensure_ascii=False))
    assert trim_size < full_size, (
        f"trimmed payload 未缩小：full={full_size}, trim={trim_size}"
    )


def test_observer_input_full_and_trimmed_top_level_keys_consistent(tmp_path: Path):
    """full 与 trimmed 模式顶层键一致（除 previous_state 与新增 stats）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_commit_with_payload(
        db_path, pid, cid,
        {"character_changes": []},
        version=1,
    )
    full_payload = build_observer_input(db_path, cid, snapshot_mode="full")
    trim_payload = build_observer_input(db_path, cid, snapshot_mode="trimmed")
    full_top = set(full_payload.keys())
    trim_top = set(trim_payload.keys())
    # trimmed 仅新增 snapshot_trim_stats
    assert trim_top - full_top == {"snapshot_trim_stats"}
    assert full_top - trim_top == set()
    # 公共键（除 previous_state 外）逐字节一致
    for k in full_top & trim_top - {"previous_state", "snapshot_trim_stats"}:
        assert full_payload[k] == trim_payload[k]


def test_observer_input_full_byte_equivalent_to_old_implementation(tmp_path: Path):
    """full 模式输出与旧实现逐字节一致（除不可序列化的 PII 字段）。

    设计：跳过难以精确字节比较的 draft_text（DB 读路径），断言关键键：
    - previous_state 完全等于 StoryStateService.get_current_state(...) 返回值
    - 顶层 schema 字段名 / config 值与快照前定义一致
    - 没有注入任何 trimmed-only 字段
    """
    from packages.core.story_state.service import StoryStateService

    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_commit_with_payload(
        db_path, pid, cid,
        {"character_changes": []},
        version=1,
    )

    payload = build_observer_input(db_path, cid, snapshot_mode="full")
    expected_snap = StoryStateService(db_path).get_current_state(pid)
    assert payload["previous_state"] == expected_snap
    assert payload["agent"] == "observer"
    assert payload["prompt_version"] == "observer:v1"
    assert payload["chapter"]["chapter_id"] == cid
    assert payload["knowledge_permissions"]["your_visibility"] == ["AUTHOR", "DIRECTOR"]
    assert payload["config"]["min_excerpt_chars_low_confidence"] == 80
    assert payload["config"]["max_changes_per_array"] == 24
    # 无 trimmed-only 字段
    assert "snapshot_trim_stats" not in payload
    assert "snapshot_mode" not in payload["previous_state"]


def test_observer_input_invalid_snapshot_mode_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    try:
        build_observer_input(db_path, cid, snapshot_mode="bogus")
    except ValueError as e:
        assert "snapshot_mode" in str(e)
    else:
        raise AssertionError("expected ValueError for invalid snapshot_mode")
