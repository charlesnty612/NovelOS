"""SimulationService 单元测试（Sprint 10）。

绕过 HTTP 层，直接用 ``SimulationService`` 跑领域行为。覆盖：

- 主链路：建临时分支 → 顺序提交 delta → 返回 diff → main 快照/领域表/commits 数不变 → 分支 ARCHIVED。
- 校验失败：delta 缺必填字段 → SimulationError(validation_failed)，applied=0，分支仍 ARCHIVED。
- HIGH 风险审批绕过：``character_changes[].facet='definition'`` 在 simulation 路径下不被
  ApprovalRequiredError 拦截；audit 字段 ``status='simulation_bypassed'`` 记录在 commits.author_approval_json。
- 空 deltas → SimulationError(empty_deltas)。
- project 不存在 → StateNotFoundError（在 simulate 入口走 _ensure_project_exists）。
- list_simulations / get_simulation 重放：name LIKE 'sim-%'，get 重算 diff。

不依赖 FastAPI；DB 走 ``tmp_path``；Service 内部使用 sqlite3 + packages.core.db。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.simulation import SimulationError, SimulationService
from packages.core.story_state.service import StoryStateService

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_project_with_char_chapter(
    db_path: Path, *, name: str = "sim-proj", uid: str | None = None
) -> dict:
    """最小 fixture：project + character + chapter + genesis v1。

    返回 ``{"project_id": str, "character_id": str, "chapter_id": str}``。
    使用 uid 防止跨测试 delta_id 冲突。
    """
    suffix = uid or uuid.uuid4().hex[:8]
    pid = f"prj_{suffix}"
    cid = f"char_{suffix}"
    chap = f"ch_{suffix}"
    apply_migrations(db_path, MIGRATIONS_DIR)
    state = StoryStateService(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', ?, ?)",
            (pid, name, _now_iso(), _now_iso()),
        )
        conn.execute(
            "INSERT INTO characters (character_id, project_id, name, role, core_json, "
            "visibility, created_at, updated_at) VALUES (?, ?, ?, 'protagonist', ?, "
            "'VISIBLE', ?, ?)",
            (cid, pid, "Alice", json.dumps({"personality": "calm"}), _now_iso(), _now_iso()),
        )
        conn.execute(
            "INSERT INTO character_states (character_id, state_version, state_json, "
            "visibility, created_at) VALUES (?, 1, ?, 'VISIBLE', ?)",
            (cid, json.dumps({"location": "Village", "emotion": "calm"}), _now_iso()),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, created_at, updated_at) "
            "VALUES (?, ?, 1, '第一章', '{}', 'PLANNED', 'VISIBLE', ?, ?)",
            (chap, pid, _now_iso(), _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    state.init_genesis(pid, chap)
    return {"project_id": pid, "character_id": cid, "chapter_id": chap}


def _evidence(chapter_id: str) -> dict:
    return {"chapter_id": chapter_id, "scene_id": None, "excerpt": "excerpt", "span": None}


def _make_meta(delta_id: str, chapter_id: str, prev_version: int) -> dict:
    return {
        "delta_id": delta_id,
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chapter_id,
        "workflow_run_id": f"wfr_{delta_id}",
        "previous_state_version": prev_version,
        "created_by": "observer:v1",
        "created_at": _now_iso(),
        "supersedes": None,
        "notes": None,
    }


def _count_commits(db_path: Path, branch_id: str | None = None) -> int:
    conn = get_connection(db_path)
    try:
        if branch_id is None:
            r = conn.execute("SELECT COUNT(*) AS n FROM commits").fetchone()
        else:
            r = conn.execute(
                "SELECT COUNT(*) AS n FROM commits WHERE branch_id = ?", (branch_id,)
            ).fetchone()
        return int(r["n"])
    finally:
        conn.close()


def _count_main_commits(db_path: Path, project_id: str) -> int:
    """计算 main 分支上的 commit 数（不包含 sim/feature 分支的 commit）。"""
    conn = get_connection(db_path)
    try:
        r = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM commits c
            JOIN branches b ON c.branch_id = b.branch_id
            WHERE c.project_id = ? AND b.name = 'main'
            """,
            (project_id,),
        ).fetchone()
        return int(r["n"])
    finally:
        conn.close()


def _count_main_rows(db_path: Path) -> dict:
    """返回 main 领域表行数（不含 branches：临时分支是 design 上必须新增的）。"""
    conn = get_connection(db_path)
    try:
        return {
            "story_states": int(
                conn.execute("SELECT COUNT(*) AS n FROM story_states").fetchone()["n"]
            ),
            "character_states": int(
                conn.execute("SELECT COUNT(*) AS n FROM character_states").fetchone()["n"]
            ),
            "plot_events": int(
                conn.execute("SELECT COUNT(*) AS n FROM plot_events").fetchone()["n"]
            ),
            "hooks": int(conn.execute("SELECT COUNT(*) AS n FROM hooks").fetchone()["n"]),
            "branches": int(conn.execute("SELECT COUNT(*) AS n FROM branches").fetchone()["n"]),
        }
    finally:
        conn.close()


# --------------------------------------------------------------------- happy path


def test_simulate_happy_path_does_not_pollute_main(tmp_path: Path):
    """主链路：simulate 两个 delta → 返回 diff → main 领域表/snapshot/commits 全程不变 → 分支 ARCHIVED。"""
    db_path = _fresh_db(tmp_path)
    uid = uuid.uuid4().hex[:8]
    seed = _seed_project_with_char_chapter(db_path, uid=uid)
    pid, cid, chap = seed["project_id"], seed["character_id"], seed["chapter_id"]

    before = _count_main_rows(db_path)
    before_main_branches = before["branches"]
    before_main_commits = _count_main_commits(db_path, pid)
    before_main_state = StoryStateService(db_path).get_current_state(pid)
    assert before_main_state["state_version"] == 1

    sim = SimulationService(db_path)
    deltas = [
        {
            **_make_meta(f"dlt_{uid}_1", chap, 1),
            "character_changes": [
                {
                    "change_id": f"cc_{uid}_s1",
                    "op": "update",
                    "target_id": cid,
                    "character_id": cid,
                    "facet": "state",
                    "field": "state.location",
                    "before": "Village",
                    "after": "Cave",
                    "confidence": 0.95,
                    "evidence": _evidence(chap),
                    "risk_level": "LOW",
                }
            ],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [
                {
                    "change_id": f"ev_{uid}_s1",
                    "op": "add",
                    "target_id": f"event_{uid}_1",
                    "event_id": f"event_{uid}_1",
                    "type": "encounter",
                    "participants": [cid],
                    "time": {"timeline_day": 2, "in_story_date": None},
                    "description": "推演事件",
                    "confidence": 0.9,
                    "evidence": _evidence(chap),
                    "risk_level": "LOW",
                }
            ],
            "resolved_hooks": [],
            "new_hooks": [
                {
                    "change_id": f"nh_{uid}_s1",
                    "op": "add",
                    "target_id": f"hook_{uid}_1",
                    "hook_id": f"hook_{uid}_1",
                    "name": "推演伏笔",
                    "importance": 0.7,
                    "description": "推演独有的伏笔",
                    "confidence": 0.9,
                    "evidence": _evidence(chap),
                    "risk_level": "LOW",
                }
            ],
            "debt_changes": [],
        },
        {
            **_make_meta(f"dlt_{uid}_2", chap, 2),
            "character_changes": [
                {
                    "change_id": f"cc_{uid}_s2",
                    "op": "update",
                    "target_id": cid,
                    "character_id": cid,
                    "facet": "state",
                    "field": "state.emotion",
                    "before": "calm",
                    "after": "alert",
                    "confidence": 0.9,
                    "evidence": _evidence(chap),
                    "risk_level": "LOW",
                }
            ],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        },
    ]
    result = sim.simulate(pid, deltas, name=f"sim-{uid}")

    # result 结构
    assert result.simulation_id == result.branch_id
    assert result.name == f"sim-{uid}"
    assert result.base_version == 1
    assert result.applied == 2
    assert result.branch_status == "ARCHIVED"
    assert result.issues == []

    # diff 应至少含 characters / hooks / events_changed 之一
    assert "characters" in result.diff or "hooks" in result.diff or "events_changed" in result.diff

    # main 零污染（领域表行数 + main commits；branches 允许 +1）
    after = _count_main_rows(db_path)
    assert after["branches"] == before_main_branches + 1
    del after["branches"]
    before_no_br = {k: v for k, v in before.items() if k != "branches"}
    assert after == before_no_br, f"main 领域表行数变化: {before_no_br} -> {after}"
    after_main_commits = _count_main_commits(db_path, pid)
    assert after_main_commits == before_main_commits, (
        f"main commits 数变化: {before_main_commits} -> {after_main_commits}"
    )
    # main 快照不变（version 仍为 1，location 仍为 Village）
    main_state_after = StoryStateService(db_path).get_current_state(pid)
    assert main_state_after["state_version"] == 1
    char_after = next(c for c in main_state_after["characters"] if c["character_id"] == cid)
    assert char_after["current_state"]["location"] == "Village"
    # 推演事件 / 推演伏笔不应出现在 main
    assert f"event_{uid}_1" not in main_state_after.get("recent_events", [])
    assert not any(h["hook_id"] == f"hook_{uid}_1" for h in main_state_after["hooks"])

    # 分支 archived
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT status, name FROM branches WHERE branch_id = ?", (result.branch_id,)
        ).fetchone()
        assert row["status"] == "ARCHIVED"
        assert row["name"] == f"sim-{uid}"
        # 临时分支 commits 数 == 2
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM commits WHERE branch_id = ?", (result.branch_id,)
        ).fetchone()["n"]
        assert int(n) == 2
    finally:
        conn.close()


# --------------------------------------------------------------------- validation failure


def test_simulate_validation_failure_archives_branch(tmp_path: Path):
    """第二个 delta 校验失败 → 整批拒绝、分支归档、applied=0。"""
    db_path = _fresh_db(tmp_path)
    uid = uuid.uuid4().hex[:8]
    seed = _seed_project_with_char_chapter(db_path, uid=uid)
    pid, cid, chap = seed["project_id"], seed["character_id"], seed["chapter_id"]

    sim = SimulationService(db_path)
    # 第一个 delta 合法；第二个 delta 缺 change_id（业务规则不通过）
    deltas = [
        {
            **_make_meta(f"dlt_{uid}_vf1", chap, 1),
            "character_changes": [
                {
                    "change_id": f"cc_{uid}_vf1",
                    "op": "update",
                    "target_id": cid,
                    "character_id": cid,
                    "facet": "state",
                    "field": "state.location",
                    "before": "Village",
                    "after": "Town",
                    "confidence": 0.9,
                    "evidence": _evidence(chap),
                    "risk_level": "LOW",
                }
            ],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        },
        {
            **_make_meta(f"dlt_{uid}_vf2", chap, 2),
            "character_changes": [
                # 缺 change_id —— schema 不通过（change_id required）
                {
                    "op": "update",
                    "target_id": cid,
                    "character_id": cid,
                    "facet": "state",
                    "field": "state.location",
                    "before": "Town",
                    "after": "Mountain",
                    "confidence": 0.9,
                    "evidence": _evidence(chap),
                    "risk_level": "LOW",
                }
            ],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        },
    ]
    with pytest.raises(SimulationError) as exc_info:
        sim.simulate(pid, deltas)
    err = exc_info.value
    assert err.reason == "validation_failed"
    # 第一个 delta 已成功提交；第二个在校验阶段被拒 → applied == 1
    assert err.applied == 1
    assert err.branch_id is not None
    assert len(err.issues) == 1
    assert err.issues[0]["index"] == 1
    assert any("change_id" in e for e in err.issues[0]["errors"])

    # 分支仍归档
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM branches WHERE branch_id = ?", (err.branch_id,)
        ).fetchone()
        assert row["status"] == "ARCHIVED"
        rows = conn.execute("SELECT name, status FROM branches").fetchall()
        names = {r["name"] for r in rows}
        assert "main" in names
        assert len(rows) == 2  # 1 main + 1 sim 临时
        sim_row = next(r for r in rows if r["name"] != "main")
        assert sim_row["status"] == "ARCHIVED"
    finally:
        conn.close()

    # main 快照/commits/领域表不变
    main_state = StoryStateService(db_path).get_current_state(pid)
    assert main_state["state_version"] == 1


# --------------------------------------------------------------------- HIGH risk bypass


def test_simulate_high_risk_definition_bypasses_approval(tmp_path: Path):
    """character facet='definition' 触发 HIGH 风险；simulation 路径走 _skip_approval 绕过。"""
    db_path = _fresh_db(tmp_path)
    uid = uuid.uuid4().hex[:8]
    seed = _seed_project_with_char_chapter(db_path, uid=uid)
    pid, cid, chap = seed["project_id"], seed["character_id"], seed["chapter_id"]

    sim = SimulationService(db_path)
    deltas = [
        {
            **_make_meta(f"dlt_{uid}_h1", chap, 1),
            "character_changes": [
                {
                    "change_id": f"cc_{uid}_h1",
                    "op": "update",
                    "target_id": cid,
                    "character_id": cid,
                    "facet": "definition",
                    "field": "core.personality",
                    "before": "calm",
                    "after": "reckless",
                    "confidence": 0.9,
                    "evidence": _evidence(chap),
                    "risk_level": "HIGH",
                }
            ],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        },
    ]
    result = sim.simulate(pid, deltas)
    assert result.applied == 1
    assert result.branch_status == "ARCHIVED"

    # 审计：commit.author_approval_json.status == 'simulation_bypassed'
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT author_approval_json FROM commits WHERE branch_id = ?",
            (result.branch_id,),
        ).fetchone()
        approval = json.loads(row["author_approval_json"])
        assert approval["status"] == "simulation_bypassed"
        assert approval["bypass_reason"] == "what_if_simulation"
        assert approval["high_risk_change_ids"] == [f"cc_{uid}_h1"]
        assert approval["approved_at"] is None
        # main characters 表 core_json 不变（definition 仅走核心表 / 分支 snapshot）
        cur = conn.execute(
            "SELECT core_json FROM characters WHERE character_id = ?", (cid,)
        ).fetchone()
        core = json.loads(cur["core_json"]) if cur["core_json"] else {}
        assert core.get("personality") == "calm"
        # main 快照不变
        main_state = StoryStateService(db_path).get_current_state(pid)
        assert main_state["state_version"] == 1
        char = next(c for c in main_state["characters"] if c["character_id"] == cid)
        assert char["current_state"] == {"location": "Village", "emotion": "calm"}
    finally:
        conn.close()


# --------------------------------------------------------------------- empty deltas


def test_simulate_empty_deltas_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    uid = uuid.uuid4().hex[:8]
    seed = _seed_project_with_char_chapter(db_path, uid=uid)
    pid = seed["project_id"]
    sim = SimulationService(db_path)
    with pytest.raises(SimulationError) as exc_info:
        sim.simulate(pid, [])
    assert exc_info.value.reason == "empty_deltas"


def test_simulate_nonlist_deltas_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    uid = uuid.uuid4().hex[:8]
    seed = _seed_project_with_char_chapter(db_path, uid=uid)
    pid = seed["project_id"]
    sim = SimulationService(db_path)
    with pytest.raises(SimulationError) as exc_info:
        sim.simulate(pid, "not-a-list")  # type: ignore[arg-type]
    assert exc_info.value.reason == "empty_deltas"


# --------------------------------------------------------------------- project not found


def test_simulate_unknown_project_raises_not_found(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    sim = SimulationService(db_path)
    with pytest.raises(Exception) as exc_info:
        sim.simulate("prj_does_not_exist", [{"x": 1}])
    msg = str(exc_info.value)
    assert "not found" in msg.lower() or "prj_does_not_exist" in msg


# --------------------------------------------------------------------- list / get


def test_list_and_get_simulations(tmp_path: Path):
    """两次 simulate → list_simulations 返回 2 条；get_simulation 重算 diff。"""
    db_path = _fresh_db(tmp_path)
    uid = uuid.uuid4().hex[:8]
    seed = _seed_project_with_char_chapter(db_path, uid=uid)
    pid, cid, chap = seed["project_id"], seed["character_id"], seed["chapter_id"]

    sim = SimulationService(db_path)

    def _deltas_for(tag: str) -> list[dict]:
        return [
            {
                **_make_meta(f"dlt_{uid}_{tag}", chap, 1),
                "character_changes": [
                    {
                        "change_id": f"cc_{uid}_{tag}",
                        "op": "update",
                        "target_id": cid,
                        "character_id": cid,
                        "facet": "state",
                        "field": "state.location",
                        "before": "Village",
                        "after": "Cave",
                        "confidence": 0.9,
                        "evidence": _evidence(chap),
                        "risk_level": "LOW",
                    }
                ],
                "world_changes": [],
                "relationship_changes": [],
                "new_events": [],
                "resolved_hooks": [],
                "new_hooks": [],
                "debt_changes": [],
            },
        ]

    r1 = sim.simulate(pid, _deltas_for("lg1"), name=f"sim-{uid}-1")
    r2 = sim.simulate(pid, _deltas_for("lg2"), name=f"sim-{uid}-2")

    listed = sim.list_simulations(pid)
    sim_ids = {x["simulation_id"] for x in listed}
    assert r1.simulation_id in sim_ids
    assert r2.simulation_id in sim_ids
    for entry in listed:
        assert entry["name"].startswith("sim-")
        assert entry["status"] == "ARCHIVED"

    replay = sim.get_simulation(pid, r1.simulation_id)
    assert replay is not None
    assert replay.simulation_id == r1.simulation_id
    assert replay.applied == 1
    assert "characters" in replay.diff

    assert sim.get_simulation(pid, "br_nope") is None


def test_get_simulation_rejects_non_sim_branch(tmp_path: Path):
    """get_simulation 必须过滤 name LIKE 'sim-%'——非推演分支返回 None。"""
    db_path = _fresh_db(tmp_path)
    uid = uuid.uuid4().hex[:8]
    seed = _seed_project_with_char_chapter(db_path, uid=uid)
    pid = seed["project_id"]

    state = StoryStateService(db_path)
    feature = state.create_branch(pid, f"feature-{uid}")
    sim = SimulationService(db_path)
    assert sim.get_simulation(pid, feature["branch_id"]) is None


# --------------------------------------------------------------------- default name


def test_simulate_default_name_format(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    uid = uuid.uuid4().hex[:8]
    seed = _seed_project_with_char_chapter(db_path, uid=uid)
    pid, cid, chap = seed["project_id"], seed["character_id"], seed["chapter_id"]

    sim = SimulationService(db_path)
    deltas = [
        {
            **_make_meta(f"dlt_{uid}_def1", chap, 1),
            "character_changes": [
                {
                    "change_id": f"cc_{uid}_def1",
                    "op": "update",
                    "target_id": cid,
                    "character_id": cid,
                    "facet": "state",
                    "field": "state.location",
                    "before": "Village",
                    "after": "Town",
                    "confidence": 0.9,
                    "evidence": _evidence(chap),
                    "risk_level": "LOW",
                }
            ],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        },
    ]
    result = sim.simulate(pid, deltas)
    assert result.name.startswith("sim-")
    assert len(result.name) >= len("sim-YYYYMMDDTHHMMSSZ")


# --------------------------------------------------------------------- F4 fix: replay base snapshot


def test_get_simulation_replay_uses_base_snapshot_not_current_main(tmp_path: Path):
    """F4（Sprint 11 审查）：推演后 main 再 commit 一次 → get_simulation 的 diff 必须与
    推演当时一致（即 base_state 走 branches.base_state_version 的快照，不漂移到 main 当前）。

    验证路径：
    1) 推演前 main version=1（genesis v1）。
    2) 跑 simulate(pid, [delta]) → base_version=1，分支 base_state = main v1。
    3) get_simulation(pid, sim_id) → 第一次读 base_state = main v1 快照。
    4) main 再 commit 一次 → main version=2；当前 get_current_state 走 v2 快照。
    5) 再次 get_simulation(pid, sim_id) → 与第 3 步完全一致（base_version=1 仍指向 v1）。
    """
    db_path = _fresh_db(tmp_path)
    uid = uuid.uuid4().hex[:8]
    seed = _seed_project_with_char_chapter(db_path, uid=uid)
    pid, cid, chap = seed["project_id"], seed["character_id"], seed["chapter_id"]

    sim = SimulationService(db_path)
    deltas = [
        {
            **_make_meta(f"dlt_{uid}_F4", chap, 1),
            "character_changes": [
                {
                    "change_id": f"cc_{uid}_F4",
                    "op": "update",
                    "target_id": cid,
                    "character_id": cid,
                    "facet": "state",
                    "field": "state.location",
                    "before": "Village",
                    "after": "Cave",
                    "confidence": 0.9,
                    "evidence": _evidence(chap),
                    "risk_level": "LOW",
                }
            ],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        }
    ]
    result = sim.simulate(pid, deltas, name=f"sim-{uid}-F4")
    base_version = result.base_version

    # 推演时拿一次 diff（baseline）
    first_replay = sim.get_simulation(pid, result.simulation_id)
    assert first_replay is not None
    assert first_replay.base_version == base_version
    assert first_replay.base_state.get("state_version") == base_version

    # main 再 commit 一次（直接走 StoryStateService.commit_delta）
    state_svc = StoryStateService(db_path)
    new_delta_meta = {
        "delta_id": f"dlt_{uid}_F4_extra",
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": chap,
        "workflow_run_id": f"wfr_{uid}_F4_extra",
        "previous_state_version": 1,
        "created_by": "observer:v1",
        "created_at": _now_iso(),
        "supersedes": None,
        "notes": None,
    }
    extra_delta = {
        **new_delta_meta,
        "character_changes": [
            {
                "change_id": f"cc_{uid}_F4_extra",
                "op": "update",
                "target_id": cid,
                "character_id": cid,
                "facet": "state",
                "field": "state.location",
                "before": "Cave",
                "after": "Mountain",
                "confidence": 0.9,
                "evidence": _evidence(chap),
                "risk_level": "LOW",
            }
        ],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    submit_extra = state_svc.submit_delta(extra_delta)
    assert submit_extra["status"] == "validated"
    state_svc.commit_delta(
        submit_extra["delta_id"],
        {"approver": "system:test", "notes": "main commit after simulation"},
        workflow_run_id=f"wfr_{uid}_F4_extra",
        _skip_approval=True,
    )
    # 确认 main 已前进
    main_now = state_svc.get_current_state(pid)
    assert int(main_now.get("state_version") or 0) == 2

    # 重放应与推演时一致（base_version=1，base_state.version=1）
    second_replay = sim.get_simulation(pid, result.simulation_id)
    assert second_replay is not None
    assert second_replay.base_version == base_version
    assert second_replay.base_state.get("state_version") == base_version, (
        f"base_state 应仍指向 base_version={base_version} 处的快照，"
        f"实际 {second_replay.base_state.get('state_version')}"
    )
    # diff 也必须完全一致（base/final 不漂移）
    assert second_replay.diff == first_replay.diff
    assert second_replay.final_state == first_replay.final_state

