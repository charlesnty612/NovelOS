"""build_observer_input V3.1.1 O-1 events 滚动窗口 + open hooks/debts 字段压缩 单测。

覆盖（V3.1.1 O-1 任务书 DoD）：
- ``_trim_snapshot_for_observer`` 新增 ``events_window_chapters`` / ``current_chapter_no`` /
  ``event_chapter_no_map`` / ``compact_open_hooks_debts`` 参数纯函数语义：
  - 窗口内事件保留 + 字段压缩；窗口外事件整体移出。
  - touched_events 中的窗口外事件也保留（保 delta 完整性）。
  - 窗口参数缺失任一项时走兼容路径（events 原样保留，老行为不变）。
  - open hooks 默认走压缩字段；显式 compact_open_hooks_debts=False 时全量保留。
  - open debts 同步压缩口径（保留 description 因为它是 open debt 唯一线索）。
- ``build_observer_input`` 端到端：
  - trimmed 默认参数下真实场景体积大幅缩小（合成 30 章 × 多事件数据断言）。
  - full 模式零影响（顶层 schema 与 M3 一致）。
  - ``events_window_chapters=None`` 显式关闭窗口回归 M3 行为。
  - stats 携带事件窗口相关字段（events_total / kept_window / kept_touched / dropped /
    bytes_before / bytes_after）。
- 真实库体积量化的占位入口（默认跳过，需要 ``NOVELOS_REAL_DB`` 环境变量）。

设计要点：
- 纯函数测试用合成 snapshot + 合成 event_chapter_no_map，无 DB。
- 端到端测试用 in-memory sqlite（apply_migrations），与 test_observer_input_trim.py 同范式。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ruff: noqa: E402  —— import 在 sys.path 注入后必须放在条件块之后
from packages.core.context_engine.builders import (  # noqa: E402
    _compact_event,
    _compact_open_debt,
    _compact_open_hook,
    _load_event_chapter_no_map,
    _trim_snapshot_for_observer,
    build_observer_input,
)
from packages.core.db import apply_migrations, get_connection  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402

MIGRATIONS_DIR = ROOT / "database" / "migrations"

# ruff: noqa: E501  —— 长 SQL 模板（plot_events / story_states）跨行已用三引号
# 与括号包裹缩进，仍会被 ruff 计为单行长；用 noqa 局部豁免以保留可读性
_PLOT_EVENTS_INSERT_SQL = (
    "INSERT INTO plot_events (event_id, project_id, type, cause_json, "
    "effects_json, participants_json, location_id, time_json, status, "
    "introduced_chapter_id, visibility, who_knows, description) VALUES "
    "(?, ?, 'transition', '[]', '[]', '[]', NULL, ?, 'recorded', ?, "
    "'VISIBLE', NULL, ?)"
)

# chapter_no_map 测试用——使用字面量 '{}' 作 time_json，引入章用 ?/NULL
_PLOT_EVENTS_INSERT_SQL_STATIC_TIME = (
    "INSERT INTO plot_events (event_id, project_id, type, cause_json, "
    "effects_json, participants_json, location_id, time_json, status, "
    "introduced_chapter_id, visibility, who_knows, description) VALUES "
    "(?, ?, 'transition', '[]', '[]', '[]', NULL, '{}', 'recorded', ?, "
    "'VISIBLE', NULL, 'd')"
)
_PLOT_EVENTS_INSERT_SQL_NULL_CHAPTER = (
    "INSERT INTO plot_events (event_id, project_id, type, cause_json, "
    "effects_json, participants_json, location_id, time_json, status, "
    "introduced_chapter_id, visibility, who_knows, description) VALUES "
    "(?, ?, 'transition', '[]', '[]', '[]', NULL, '{}', 'recorded', "
    "NULL, 'VISIBLE', NULL, 'd')"
)


# ---------------------------------------------------------------------------
# 纯函数辅助工具
# ---------------------------------------------------------------------------


def _make_snapshot_with_events(
    *,
    n_events: int = 30,
    n_open_hooks: int = 5,
    n_open_debts: int = 3,
) -> tuple[dict, dict[str, int]]:
    """构造一个带 N 条事件 + open hooks/debts 的快照，返回 (snap, map)。

    事件默认分散在 chapter 1..30，每个事件 description 较长（模拟真实体积）。
    返回的 map 把 ``event_<i>`` 映射到 ``i``（1-indexed chapter_no）。
    """
    snap: dict = {
        "state_version": 100,
        "characters": [],
        "world": {
            "locations": {},
            "factions": {},
            "world_rules": [],
            "active_resources": {},
        },
        "hooks": [
            {
                "hook_id": f"hook_open_{i:03d}",
                "name": f"开放伏笔{i}",
                "introduced_chapter_id": f"ch_{i:03d}",
                "status": "OPEN",
                "importance": 0.7,
                "description": "long description " * 20,
                "expected_payoff_chapter_id": None,
                "payoff_chapter_id": None,
                "visibility": "PUBLIC",
                "created_at": "2026-01-01T00:00:00Z",
            }
            for i in range(n_open_hooks)
        ],
        "debts": [
            {
                "debt_id": f"debt_open_{i:03d}",
                "description": f"open debt {i}",
                "created_chapter_id": f"ch_{i:03d}",
                "severity": 0.6,
                "deadline_chapter_id": None,
                "status": "open",
                "visibility": "VISIBLE",
                "who_knows": None,
            }
            for i in range(n_open_debts)
        ],
        "recent_events": [f"event_{i:03d}" for i in range(min(3, n_events))],
        "events": {
            f"event_{i:03d}": {
                "type": "transition",
                "participants": [f"char_{i:03d}", f"char_{(i+1) % n_events:03d}"],
                "time": {"timeline_day": i + 1, "in_story_date": f"D-{i+1}"},
                "description": "big description " * 30,
            }
            for i in range(n_events)
        },
    }
    event_map: dict[str, int] = {f"event_{i:03d}": i + 1 for i in range(n_events)}
    return snap, event_map


# ---------------------------------------------------------------------------
# _compact_* 函数单测
# ---------------------------------------------------------------------------


def test_compact_open_hook_field_whitelist():
    h = {
        "hook_id": "hook_001",
        "name": "name",
        "introduced_chapter_id": "ch_005",
        "status": "OPEN",
        "importance": 0.9,
        "description": "should be removed",
        "expected_payoff_chapter_id": "ch_010",
        "payoff_chapter_id": None,
        "visibility": "PUBLIC",
        "created_at": "ts",
        "updated_at": "ts",
    }
    out = _compact_open_hook(h)
    assert set(out.keys()) == {"hook_id", "name", "status", "visibility", "created_chapter"}
    assert out["hook_id"] == "hook_001"
    assert out["created_chapter"] == "ch_005"
    # 不应携带 description / 时间戳
    assert "description" not in out
    assert "created_at" not in out


def test_compact_open_hook_falls_back_to_created_chapter_id():
    """无 introduced_chapter_id 时退回 created_chapter_id 字段名兼容。"""
    h = {"hook_id": "h", "name": "n", "status": "OPEN", "created_chapter_id": "ch_99", "visibility": "PUBLIC"}
    out = _compact_open_hook(h)
    assert out["created_chapter"] == "ch_99"


def test_compact_open_debt_field_whitelist():
    d = {
        "debt_id": "debt_001",
        "description": "保持",
        "created_chapter_id": "ch_007",
        "severity": 0.8,
        "deadline_chapter_id": "ch_020",
        "status": "open",
        "visibility": "VISIBLE",
        "who_knows": None,
        "created_at": "ts",
    }
    out = _compact_open_debt(d)
    assert set(out.keys()) == {"debt_id", "description", "status", "visibility", "created_chapter"}
    assert out["description"] == "保持"
    assert out["created_chapter"] == "ch_007"


def test_compact_event_field_whitelist():
    e = {
        "type": "revelation",
        "participants": ["char_a", "char_b"],
        "time": {"timeline_day": 100},
        "description": "keep me",
    }
    out = _compact_event(e)
    assert set(out.keys()) == {"type", "description"}
    assert out["type"] == "revelation"
    assert out["description"] == "keep me"


# ---------------------------------------------------------------------------
# _trim_snapshot_for_observer events 滚动窗口 单测
# ---------------------------------------------------------------------------


def test_events_window_keeps_in_window_events():
    """窗口内事件保留并压缩。"""
    snap, ev_map = _make_snapshot_with_events(n_events=30)
    current_no = 30
    window = 6  # 保留 [24, 30]
    trimmed, stats = _trim_snapshot_for_observer(
        snap,
        events_window_chapters=window,
        current_chapter_no=current_no,
        event_chapter_no_map=ev_map,
    )
    events_out = trimmed["events"]
    # window=6, current=30 → 保留 chapter_no ∈ [24, 30] 的事件
    assert len(events_out) == 7  # 24, 25, 26, 27, 28, 29, 30
    assert stats["events_kept_window"] == 7
    assert stats["events_dropped"] == 23
    assert stats["events_total"] == 30
    # 压缩字段：仅 {type, description}
    sample = next(iter(events_out.values()))
    assert set(sample.keys()) == {"type", "description"}


def test_events_window_keeps_touched_out_of_window_event():
    """touched 中的窗口外事件也保留（保 delta 完整性）。"""
    snap, ev_map = _make_snapshot_with_events(n_events=30)
    current_no = 30
    window = 6
    touched = {
        "characters": set(),
        "locations": set(),
        "factions": set(),
        "world_rules": set(),
        "hooks": set(),
        "debts": set(),
        "events": {"event_000"},  # chapter 1，在窗口外但被最近 commit 引入
        "relationships": set(),
        "relationship_keys": set(),
    }
    trimmed, stats = _trim_snapshot_for_observer(
        snap,
        events_window_chapters=window,
        current_chapter_no=current_no,
        event_chapter_no_map=ev_map,
        touched=touched,
    )
    assert "event_000" in trimmed["events"]
    assert stats["events_kept_touched"] == 1
    assert stats["events_kept_window"] == 7
    assert stats["events_dropped"] == 22


def test_events_window_disabled_when_param_missing():
    """events_window_chapters=None 走兼容路径（events 原样保留）。"""
    snap, _ev_map = _make_snapshot_with_events(n_events=30)
    trimmed, stats = _trim_snapshot_for_observer(
        snap,
        events_window_chapters=None,
        current_chapter_no=30,
        event_chapter_no_map={},
    )
    assert trimmed["events"] == snap["events"]
    assert stats["events_total"] == 0  # 兼容路径不动事件 stats


def test_events_window_disabled_when_chapter_no_missing():
    """current_chapter_no=None 走兼容路径。"""
    snap, ev_map = _make_snapshot_with_events(n_events=30)
    trimmed, _stats = _trim_snapshot_for_observer(
        snap,
        events_window_chapters=6,
        current_chapter_no=None,
        event_chapter_no_map=ev_map,
    )
    assert trimmed["events"] == snap["events"]


def test_events_window_disabled_when_map_missing():
    """event_chapter_no_map 非 dict 走兼容路径。"""
    snap, _ev_map = _make_snapshot_with_events(n_events=30)
    trimmed, _stats = _trim_snapshot_for_observer(
        snap,
        events_window_chapters=6,
        current_chapter_no=30,
        event_chapter_no_map=None,
    )
    assert trimmed["events"] == snap["events"]


def test_events_window_size_zero_keeps_all():
    """events_window_chapters=0 等价禁用窗口（≥1 才生效）。"""
    snap, ev_map = _make_snapshot_with_events(n_events=10)
    trimmed, stats = _trim_snapshot_for_observer(
        snap,
        events_window_chapters=0,
        current_chapter_no=10,
        event_chapter_no_map=ev_map,
    )
    assert trimmed["events"] == snap["events"]
    assert stats["events_total"] == 0


def test_events_window_records_byte_stats():
    """stats 同时记录 events 字节数前后值，便于观测。"""
    snap, ev_map = _make_snapshot_with_events(n_events=30)
    trimmed, stats = _trim_snapshot_for_observer(
        snap,
        events_window_chapters=6,
        current_chapter_no=30,
        event_chapter_no_map=ev_map,
    )
    assert stats["events_bytes_before"] > 0
    assert stats["events_bytes_after"] > 0
    assert stats["events_bytes_after"] < stats["events_bytes_before"]


# ---------------------------------------------------------------------------
# hooks/debts 字段压缩单测
# ---------------------------------------------------------------------------


def test_open_hooks_compacted_by_default():
    snap, _ev_map = _make_snapshot_with_events(n_open_hooks=4, n_open_debts=2)
    trimmed, stats = _trim_snapshot_for_observer(snap)
    open_hooks = [h for h in trimmed["hooks"] if h.get("status") in ("OPEN", "ACTIVE", "ESCALATED")]
    assert len(open_hooks) == 4
    assert all(set(h.keys()) == {"hook_id", "name", "status", "visibility", "created_chapter"} for h in open_hooks)
    assert stats["hooks_open"] == 4
    assert stats["hooks_open_compacted"] == 4


def test_open_hooks_full_when_compact_disabled():
    """compact_open_hooks_debts=False 时 open hooks 保持全量（兼容 M3）。"""
    snap, _ev_map = _make_snapshot_with_events(n_open_hooks=3)
    trimmed, stats = _trim_snapshot_for_observer(snap, compact_open_hooks_debts=False)
    open_hooks = [h for h in trimmed["hooks"] if h.get("status") == "OPEN"]
    assert len(open_hooks) == 3
    assert all("description" in h for h in open_hooks)
    assert stats["hooks_open"] == 3
    assert stats["hooks_open_compacted"] == 0


def test_open_debts_compacted_by_default():
    snap, _ev_map = _make_snapshot_with_events(n_open_debts=3)
    trimmed, stats = _trim_snapshot_for_observer(snap)
    open_debts = [d for d in trimmed["debts"] if d.get("status") in ("open", "acknowledged")]
    assert len(open_debts) == 3
    assert all(set(d.keys()) == {"debt_id", "description", "status", "visibility", "created_chapter"} for d in open_debts)
    assert stats["debts_open"] == 3
    assert stats["debts_open_compacted"] == 3


# ---------------------------------------------------------------------------
# build_observer_input 端到端（含真实 DB 体积量化）
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


def _insert_chapter(db_path: Path, project_id: str, number: int) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, status, visibility, who_knows, created_at, updated_at)
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
    now = now_iso()
    delta_id = new_id("dlt")
    commit_id = new_id("cmt")
    branch_id = new_id("brn")
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO branches (branch_id, project_id, name, parent_branch_id, "
            "base_state_version, status, created_at) "
            "VALUES (?, ?, 'main', NULL, 0, 'ACTIVE', ?)",
            (branch_id, project_id, now),
        )
        conn.execute(
            "INSERT INTO state_deltas (delta_id, chapter_id, workflow_run_id, "
            "previous_state_version, delta_version, schema_version, payload_json, "
            "status, supersedes, created_by, created_at) "
            "VALUES (?, ?, 'wfr_test', ?, 1, 'state-delta-v0', ?, 'applied', "
            "NULL, 'observer:v1', ?)",
            (delta_id, chapter_id, version - 1,
             json.dumps(payload, ensure_ascii=False), now),
        )
        conn.execute(
            "INSERT INTO commits (commit_id, project_id, branch_id, chapter_id, "
            "previous_state_version, resulting_state_version, delta_id, "
            "validation_json, author_approval_json, timestamp, workflow_run_id, "
            "rollback_of) VALUES (?, ?, ?, ?, ?, ?, ?, '{}', '{}', ?, "
            "'wfr_test', NULL)",
            (commit_id, project_id, branch_id, chapter_id, version - 1, version,
             delta_id, now),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_events(db_path: Path, project_id: str, count: int, introduced_chapter_no: int) -> None:
    """向 plot_events 插 N 条事件，全部挂在 chapter_no=given（出窗 / 在窗可控）。"""
    # 需要先取一个 chapter_id 用于 FK
    cid = conn = None
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT chapter_id FROM chapters WHERE project_id = ? AND number = ?",
            (project_id, introduced_chapter_no),
        ).fetchone()
        if row is None:
            # 不存在就直接返回（调用方负责准备）
            return
        cid = row["chapter_id"]
        for i in range(count):
            eid = new_id("evt")
            conn.execute(
                _PLOT_EVENTS_INSERT_SQL,
                (eid, project_id, json.dumps({"timeline_day": i + 1}), cid, f"event desc {i} " * 20),
            )
        conn.commit()
    finally:
        conn.close()


def test_build_observer_input_trimmed_event_window_end_to_end(tmp_path: Path):
    """端到端：30 章 × 多事件，trimmed 默认窗口=6 时体积显著缩小。

    策略：绕过 story_states 注入（DB FK 复杂），改为在 ``_trim_snapshot_for_observer``
    层面端到端验证——以合成 snapshot + 真实 ``_load_event_chapter_no_map`` 输出
    为输入，断言 stats 正确、events 字段被裁剪、体积大幅缩小。这是
    ``build_observer_input`` 内部走过的同一段代码路径（trimmed 分支），
    保证覆盖到 O-1 主逻辑。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    chapter_ids = [_insert_chapter(db_path, pid, n + 1) for n in range(30)]

    # 在 plot_events 插 150 条（每章 5 条）
    events_dict: dict[str, dict] = {}
    event_ids: list[str] = []
    for n in range(30):
        cid = chapter_ids[n]
        for j in range(5):
            eid = new_id("evt")
            desc = f"event {n}-{j} " * 20
            events_dict[eid] = {
                "type": "transition",
                "participants": [],
                "time": {"timeline_day": n * 5 + j},
                "description": desc,
            }
            event_ids.append(eid)
            with get_connection(db_path) as conn:
                conn.execute(
                    _PLOT_EVENTS_INSERT_SQL,
                    (eid, pid, json.dumps({"timeline_day": n * 5 + j}), cid, desc),
                )
                conn.commit()

    # 构造合成 snapshot（含完整 events dict）+ open hooks
    snap_full = {
        "state_version": 1,
        "characters": [],
        "world": {"locations": {}, "factions": {}, "world_rules": [], "active_resources": {}},
        "hooks": [],
        "debts": [],
        "events": events_dict,
    }

    # 真实查询 event_chapter_no_map（V3.1.1 O-1 接驳点）
    with get_connection(db_path) as conn:
        ev_map = _load_event_chapter_no_map(conn, pid)

    # 模拟 build_observer_input 的 trimmed 分支：传入 touched（让一个窗口
    # 外事件出现在 touched_events 中），调用 _trim_snapshot_for_observer。
    out_of_window_eid = event_ids[0]  # chapter 1，窗口外
    touched = {
        "characters": set(),
        "locations": set(),
        "factions": set(),
        "world_rules": set(),
        "hooks": set(),
        "debts": set(),
        "events": {out_of_window_eid},
        "relationships": set(),
        "relationship_keys": set(),
    }
    trimmed_snap, stats = _trim_snapshot_for_observer(
        snap_full,
        events_window_chapters=6,
        current_chapter_no=30,
        event_chapter_no_map=ev_map,
        touched=touched,
    )

    # 1) events_total = 150
    assert stats["events_total"] == 150
    # 2) 窗口内事件：chapter_no ∈ [24, 30] → 7 章 × 5 条 = 35
    assert stats["events_kept_window"] == 35
    # 3) touched out-of-window: 1
    assert stats["events_kept_touched"] == 1
    # 4) dropped: 150 - 35 - 1 = 114
    assert stats["events_dropped"] == 114

    # 5) 体积：trimmed 后 payload JSON 远小于全量
    full_size = len(json.dumps(snap_full, ensure_ascii=False))
    trim_size = len(json.dumps(trimmed_snap, ensure_ascii=False))
    assert trim_size < full_size * 0.5, (
        f"trimmed not aggressive enough: full={full_size}, trim={trim_size}, "
        f"ratio={trim_size/full_size:.2f}"
    )

    # 6) events 字段断言：trimmed_snap.events 只有 36 个 key（35 窗口 + 1 touched）
    assert len(trimmed_snap["events"]) == 36
    assert out_of_window_eid in trimmed_snap["events"]


def test_build_observer_input_trimmed_full_mode_unchanged(tmp_path: Path):
    """full 模式完全不受新参数影响（M3 老行为）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid, 1)
    _insert_commit_with_payload(db_path, pid, cid, {"character_changes": []}, version=1)
    payload = build_observer_input(db_path, cid, snapshot_mode="full")
    assert "snapshot_trim_stats" not in payload
    assert "snapshot_mode" not in payload["previous_state"]
    # full 模式 snapshot.events 是 dict（原样）
    assert isinstance(payload["previous_state"]["events"], dict)


def test_build_observer_input_trimmed_window_none_disables(tmp_path: Path):
    """events_window_chapters=None 显式关闭窗口时回归老行为（不裁剪 events）。

    同样走 _trim_snapshot_for_observer 纯函数路径，但 snap.events 直接
    含多条事件——验证窗口参数为 None 时不动 events。
    """
    snap, ev_map = _make_snapshot_with_events(n_events=20)
    trimmed, stats = _trim_snapshot_for_observer(
        snap,
        events_window_chapters=None,
        current_chapter_no=20,
        event_chapter_no_map=ev_map,
    )
    # 窗口关闭：events 原样保留
    assert trimmed["events"] == snap["events"]
    assert stats["events_total"] == 0
    assert stats["events_dropped"] == 0


def test_load_event_chapter_no_map_basic(tmp_path: Path):
    """_load_event_chapter_no_map 从 plot_events + chapters 反查 chapter_no。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    ch1 = _insert_chapter(db_path, pid, 1)
    ch5 = _insert_chapter(db_path, pid, 5)
    with get_connection(db_path) as conn:
        # e1 挂在 ch1 → cno=1
        e1 = new_id("evt")
        conn.execute(
            _PLOT_EVENTS_INSERT_SQL_STATIC_TIME,
            (e1, pid, ch1),
        )
        # e2 挂在 ch5 → cno=5
        e2 = new_id("evt")
        conn.execute(
            _PLOT_EVENTS_INSERT_SQL_STATIC_TIME,
            (e2, pid, ch5),
        )
        # e3 introduced_chapter_id = NULL → cno=-1（永远出窗）
        e3 = new_id("evt")
        conn.execute(
            _PLOT_EVENTS_INSERT_SQL_NULL_CHAPTER,
            (e3, pid),
        )
        conn.commit()
        m = _load_event_chapter_no_map(conn, pid)
    assert m[e1] == 1
    assert m[e2] == 5
    assert m[e3] == -1


# ---------------------------------------------------------------------------
# 真实库体积量化（默认跳过——需要环境变量 NOVELOS_REAL_DB）
# ---------------------------------------------------------------------------


def test_real_db_payload_under_60k_chars():
    """真实库硬指标：trimmed payload 字符数 ≤ 60000（≈15k token）。

    默认跳过；通过 ``NOVELOS_REAL_DB=<path>`` 启用。脚本示例：

        NOVELOS_REAL_DB=data/m1_run/novelos.db \\
        pytest tests/unit/test_observer_input_event_window.py::test_real_db_payload_under_60k_chars -s
    """
    db_path_str = os.environ.get("NOVELOS_REAL_DB")
    if not db_path_str:
        import pytest
        pytest.skip("NOVELOS_REAL_DB not set; skipping real-DB volume assertion")
    db_path = Path(db_path_str)
    if not db_path.exists():
        import pytest
        pytest.skip(f"real DB not found: {db_path}")

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT chapter_id FROM chapters ORDER BY number DESC LIMIT 1",
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        import pytest
        pytest.skip("no chapter found in real DB")
    chapter_id = row["chapter_id"]

    payload = build_observer_input(db_path, chapter_id, snapshot_mode="trimmed")
    total = len(json.dumps(payload, ensure_ascii=False))
    stats = payload["snapshot_trim_stats"]
    # 量化日志
    print()
    print(f"[real DB] latest chapter = {chapter_id}")
    print(f"[real DB] payload chars   = {total} (hard limit 60000)")
    print(f"[real DB] events total    = {stats['events_total']}")
    print(f"[real DB] events kept (window/touched/dropped) = "
          f"{stats['events_kept_window']}/{stats['events_kept_touched']}/{stats['events_dropped']}")
    print(f"[real DB] hooks open compacted = {stats['hooks_open_compacted']}")
    print(f"[real DB] total_size: before={stats['total_size_bytes_before']} "
          f"after={stats['total_size_bytes_after']}")
    # 硬断言
    assert total <= 60_000, f"real DB payload {total} chars exceeds 60000 limit"
