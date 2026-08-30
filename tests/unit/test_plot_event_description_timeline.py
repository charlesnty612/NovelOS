"""V3.1 P1-1.1 plot_events 三处缺口回归测试。

覆盖：
- B1：``plot_events.description`` 全链路透出（PlotEvent 模型 / to_dict / list /
  get 读取）。SQLite 有列、行映射先前丢弃 → 修复后 list/get 都拿得到；None 兜底。
- B2：commit 写透路径 new_events INSERT 后同步 timeline_events 索引；幂等
  （同 event_id 二次写透不重复）；time 缺 timeline_day 时不插。
- B3：迁移 0019 回填存量 plot_events 中已有 timeline_day 但缺 timeline_events
  索引的事件；幂等重跑。

测试形态：
- B1：直接调 ``PlotService`` + 校验 ``to_dict()`` / 数据库列读取。
- B2：调 ``write_through.write_through`` 内层 + 直读 ``timeline_events`` 表。
- B3：直接 ``apply_migrations`` + 直读 ``timeline_events`` 表验证回填与幂等。

与 ``tests/unit/test_story_state_write_through_null_guard.py`` 的全链路 ASGI
不同，本文件用直连 SQLite / 直调 service 接口——目标聚焦三处缺陷的最小验证，
减少 ASGI 噪音。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.story_state.write_through import write_through
from packages.domain.plot.models import PlotEvent
from packages.domain.plot.service import PlotService

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


# ----------------------------------------------------------------- helpers


def _json_day(day: int) -> str:
    """构造 ``{"timeline_day": day, "in_story_date": null}`` JSON 字串。"""
    return json.dumps({"timeline_day": day, "in_story_date": None}, ensure_ascii=False)


def _random_hex(n: int = 12) -> str:
    """测试内 helper：生成 n 位 hex（仅取 uuid4 前 n 位），用于构造短 ID。"""
    from uuid import uuid4
    return uuid4().hex[:n]


def _bootstrap(tmp_path: Path) -> tuple[str, str]:
    """初始化 DB（跑完所有迁移）+ 建一个 project，返回 (db_path, project_id)。"""
    db_path = str(tmp_path / "test.db")
    apply_migrations(db_path, MIGRATIONS_DIR)
    pid = f"prj_{_random_hex()}"
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', datetime('now'), datetime('now'))",
            (pid, "t"),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path, pid


# =================================================================== B1
# plot_events.description 全链路


def test_b1_plot_event_dataclass_has_description_field():
    """B1：PlotEvent dataclass 必须有 description 字段（默认 None）。"""
    ev = PlotEvent(id="evt_x", project_id="prj_x", type="revelation")
    assert hasattr(ev, "description"), "PlotEvent 缺少 description 字段"
    assert ev.description is None
    d = ev.to_dict()
    assert "description" in d, "PlotEvent.to_dict 必须暴露 description"
    assert d["description"] is None


def test_b1_plot_event_to_dict_includes_description_value():
    """B1：to_dict 把 description 值透出。"""
    ev = PlotEvent(
        id="evt_x", project_id="prj_x", type="revelation",
        description="玉惜轩夜访，林渊未答关键一问。",
    )
    d = ev.to_dict()
    assert d["description"] == "玉惜轩夜访，林渊未答关键一问。"


def test_b1_service_get_event_returns_description(tmp_path: Path):
    """B1：get_event 路径——DB 写入 description 后模型读出来。"""
    db_path, pid = _bootstrap(tmp_path)
    eid = f"evt_{_random_hex()}"
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO plot_events (
                event_id, project_id, type, cause_json, effects_json,
                participants_json, location_id, time_json, status,
                introduced_chapter_id, visibility, who_knows, description
            ) VALUES (?, ?, 'revelation', '[]', '[]', '[]', NULL,
                      '{"timeline_day": 1, "in_story_date": null}',
                      'recorded', NULL, 'RESTRICTED', NULL, ?)
            """,
            (eid, pid, "DB 中已落 description"),
        )
        conn.commit()
    finally:
        conn.close()

    svc = PlotService(db_path)
    ev = svc.get_event(eid)
    assert ev is not None
    assert ev.description == "DB 中已落 description", (
        "B1: get_event 必须从 DB description 列读出，"
        f"实际 {ev.description!r}"
    )


def test_b1_service_list_events_returns_description(tmp_path: Path):
    """B1：list_events 路径——行映射补 description（含 None 兜底）。"""
    db_path, pid = _bootstrap(tmp_path)
    eid_with = f"evt_{_random_hex()}"
    eid_without = f"evt_{_random_hex()}"
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO plot_events (
                event_id, project_id, type, cause_json, effects_json,
                participants_json, location_id, time_json, status,
                introduced_chapter_id, visibility, who_knows, description
            ) VALUES (?, ?, 'conflict', '[]', '[]', '[]', NULL,
                      '{"timeline_day": 2}', 'recorded', NULL,
                      'RESTRICTED', NULL, ?)
            """,
            (eid_with, pid, "有描述"),
        )
        conn.execute(
            """
            INSERT INTO plot_events (
                event_id, project_id, type, cause_json, effects_json,
                participants_json, location_id, time_json, status,
                introduced_chapter_id, visibility, who_knows, description
            ) VALUES (?, ?, 'transition', '[]', '[]', '[]', NULL,
                      '{"timeline_day": 3}', 'recorded', NULL,
                      'RESTRICTED', NULL, NULL)
            """,
            (eid_without, pid),
        )
        conn.commit()
    finally:
        conn.close()

    svc = PlotService(db_path)
    events = svc.list_events(pid)
    by_id = {e.id: e.description for e in events}
    assert by_id.get(eid_with) == "有描述", (
        f"list_events 应带回 description，实际 {by_id.get(eid_with)!r}"
    )
    assert by_id.get(eid_without) is None, (
        f"description 缺失应兜底为 None，实际 {by_id.get(eid_without)!r}"
    )


# =================================================================== B2
# write_through new_events 同步 timeline_events


def _seed_chapter(db_path: str, pid: str) -> str:
    """建一个 chapter 用于 write_through 写穿（commit 路径依赖 chapter_id）。"""
    cid = f"chap_{_random_hex()}"
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, status, "
            "plan_json, created_at, updated_at) "
            "VALUES (?, ?, 1, 't', 'COMMITTED', '{}', "
            "datetime('now'), datetime('now'))",
            (cid, pid),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def test_b2_write_through_inserts_timeline_event_with_day_index(tmp_path: Path):
    """B2：commit 写透路径——plot_events INSERT 之后 timeline_events 同步出现。"""
    db_path, pid = _bootstrap(tmp_path)
    chap_id = _seed_chapter(db_path, pid)
    eid = f"evt_{_random_hex()}"

    delta = {
        "chapter_id": chap_id,
        "new_events": [
            {
                "event_id": eid,
                "type": "revelation",
                "cause": [],
                "effects": [],
                "participants": [],
                "time": {"timeline_day": 5, "in_story_date": "2026-09-01"},
                "description": "时间线索引同步测试",
                "visibility": "RESTRICTED",
                "who_knows": [],
            }
        ],
    }
    conn = get_connection(db_path)
    try:
        write_through(conn, pid, delta, new_version=1)
        conn.commit()
        row = conn.execute(
            "SELECT day_index, time_ref, description "
            "FROM timeline_events WHERE event_id = ?",
            (eid,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, (
        "B2: write_through new_events 后 timeline_events 应出现对应行，"
        "但查不到"
    )
    assert row["day_index"] == 5, f"day_index 应为 5，实际 {row['day_index']}"
    assert row["time_ref"] == "2026-09-01", (
        f"time_ref 应取 in_story_date 字符串，实际 {row['time_ref']!r}"
    )
    assert row["description"] == "时间线索引同步测试", (
        f"description 应复用 plot_events.description 字段，"
        f"实际 {row['description']!r}"
    )


def test_b2_write_through_is_idempotent_when_timeline_row_exists(tmp_path: Path):
    """B2：event_id 已有 timeline_events 行时，写透路径不重复插。

    真实场景：迁移 0019 已为该 event 写好 timeline 索引，或运维手工补行。
    write_through new_events INSERT 之后 SELECT 命中已有行 → 跳过 INSERT。
    """
    db_path, pid = _bootstrap(tmp_path)
    chap_id = _seed_chapter(db_path, pid)
    eid = f"evt_{_random_hex()}"

    # 预置：先手工插 timeline_events（满足 FK → plot_events）；同时插
    # plot_events 满足 FK。然后删 plot_events 行，让 write_through 重新插
    # plot_events（避免 UNIQUE 冲突），保留 timeline 行触发 B2 守卫命中。
    # 该测试需要 FK 暂时关闭（plot_events → timeline_events 双向依赖下
    # 无法直接 DELETE plot_events）。get_connection 默认 PRAGMA foreign_keys=ON，
    # 在本测试连接里临时 PRAGMA OFF（仅测试内有效，不污染生产 DB）。
    conn = get_connection(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            """
            INSERT INTO plot_events (
                event_id, project_id, type, cause_json, effects_json,
                participants_json, location_id, time_json, status,
                introduced_chapter_id, visibility, who_knows, description
            ) VALUES (?, ?, 'conflict', '[]', '[]', '[]', NULL,
                      '{"timeline_day": 7}', 'recorded', NULL,
                      'RESTRICTED', NULL, '预置事件')
            """,
            (eid, pid),
        )
        conn.execute(
            """
            INSERT INTO timeline_events (
                timeline_event_id, project_id, event_id, day_index,
                time_ref, description, visibility, who_knows
            ) VALUES (?, ?, ?, 7, NULL, '预置索引', 'RESTRICTED', NULL)
            """,
            (f"tle_pre_{_random_hex()}", pid, eid),
        )
        # 删 plot_events，让 write_through 重新 INSERT 避免 UNIQUE 冲突
        conn.execute("DELETE FROM plot_events WHERE event_id = ?", (eid,))
        conn.commit()
    finally:
        conn.close()

    delta = {
        "chapter_id": chap_id,
        "new_events": [
            {
                "event_id": eid,
                "type": "conflict",
                "cause": [],
                "effects": [],
                "participants": [],
                "time": {"timeline_day": 7},
                "description": "B2 幂等性测试",
                "visibility": "RESTRICTED",
                "who_knows": [],
            }
        ],
    }
    conn = get_connection(db_path)
    try:
        write_through(conn, pid, delta, new_version=1)
        conn.commit()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM timeline_events WHERE event_id = ?",
            (eid,),
        ).fetchone()["n"]
    finally:
        conn.close()
    assert n == 1, (
        f"B2 幂等性：已有 timeline 行时 B2 守卫应阻止重复插，"
        f"实际 {n} 行"
    )


def test_b2_write_through_skips_when_no_timeline_day(tmp_path: Path):
    """B2：time 缺 timeline_day（None / 非 int）时不插 timeline_events。"""
    db_path, pid = _bootstrap(tmp_path)
    chap_id = _seed_chapter(db_path, pid)
    eid_none = f"evt_{_random_hex()}"
    eid_str = f"evt_{_random_hex()}"

    delta = {
        "chapter_id": chap_id,
        "new_events": [
            {
                "event_id": eid_none,
                "type": "other",
                "cause": [],
                "effects": [],
                "participants": [],
                "time": {"timeline_day": None},  # 缺/None → 不插
                "description": None,
                "visibility": "RESTRICTED",
                "who_knows": [],
            },
            {
                "event_id": eid_str,
                "type": "other",
                "cause": [],
                "effects": [],
                "participants": [],
                "time": {"timeline_day": "not-an-int"},  # 非 int → 不插
                "description": None,
                "visibility": "RESTRICTED",
                "who_knows": [],
            },
        ],
    }
    conn = get_connection(db_path)
    try:
        write_through(conn, pid, delta, new_version=1)
        conn.commit()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM timeline_events "
            "WHERE event_id IN (?, ?)",
            (eid_none, eid_str),
        ).fetchone()["n"]
    finally:
        conn.close()
    assert n == 0, (
        f"timeline_day 为 None/非 int 时不应插 timeline_events，"
        f"实际 {n} 行"
    )


# =================================================================== B3
# 迁移 0019 回填 timeline_events


def test_b3_migration_0019_is_registered(tmp_path: Path):
    """B3：0019 已被 apply_migrations 注册。"""
    db_path = str(tmp_path / "test.db")
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT filename FROM _migrations WHERE filename = ?",
            ("0019_backfill_timeline_events.sql",),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, "0019 迁移未被 apply_migrations 记录"


def test_b3_migration_backfills_timeline_index(tmp_path: Path):
    """B3：迁移 0019 把 plot_events 中有 timeline_day 但缺索引的事件回填。"""
    db_path = str(tmp_path / "test.db")
    # 先跑 0017（覆盖 plot_events 全列）+ 手动构造缺口
    apply_migrations(db_path, MIGRATIONS_DIR)
    pid = f"prj_{_random_hex()}"
    eid_with = f"evt_{_random_hex()}"
    eid_already = f"evt_{_random_hex()}"

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', datetime('now'), datetime('now'))",
            (pid, "t"),
        )
        # 缺口事件：time_json 含 timeline_day，无 timeline_events 行
        conn.execute(
            """
            INSERT INTO plot_events (
                event_id, project_id, type, cause_json, effects_json,
                participants_json, location_id, time_json, status,
                introduced_chapter_id, visibility, who_knows, description
            ) VALUES (?, ?, 'revelation', '[]', '[]', '[]', NULL,
                      '{"timeline_day": 3, "in_story_date": "2026-09-01"}',
                      'recorded', NULL, 'RESTRICTED', NULL, '回填描述')
            """,
            (eid_with, pid),
        )
        # 已有 timeline_events 行的事件：迁移不该覆盖（运维手工补的场景）
        eid_already_tle_id = f"tle_existing_{_random_hex()}"
        conn.execute(
            """
            INSERT INTO plot_events (
                event_id, project_id, type, cause_json, effects_json,
                participants_json, location_id, time_json, status,
                introduced_chapter_id, visibility, who_knows, description
            ) VALUES (?, ?, 'transition', '[]', '[]', '[]', NULL,
                      '{"timeline_day": 99}', 'recorded', NULL,
                      'RESTRICTED', NULL, '不应被覆盖')
            """,
            (eid_already, pid),
        )
        conn.execute(
            """
            INSERT INTO timeline_events (
                timeline_event_id, project_id, event_id, day_index,
                time_ref, description, visibility, who_knows
            ) VALUES (?, ?, ?, 99, '23:30', '手工预置', 'RESTRICTED', NULL)
            """,
            (eid_already_tle_id, pid, eid_already),
        )
        # 删除 0019 的注册记录 → 模拟「0019 即将首次应用」（0019 SQL 在
        # _migrations 已注册时直接跳过，无法触发 INSERT 行为）。
        conn.execute(
            "DELETE FROM _migrations WHERE filename = '0019_backfill_timeline_events.sql'",
        )
        conn.commit()
    finally:
        conn.close()

    # 触发 0019
    apply_migrations(db_path, MIGRATIONS_DIR)

    conn = get_connection(db_path)
    try:
        # 缺口事件：应出现一条 timeline 行（确定性 id ``tle_bf_<event_id 后 16 位>``）
        new_row = conn.execute(
            "SELECT day_index, time_ref, description "
            "FROM timeline_events WHERE event_id = ?",
            (eid_with,),
        ).fetchone()
        # 已有的事件：不被覆盖
        existing_rows = conn.execute(
            "SELECT description FROM timeline_events WHERE event_id = ?",
            (eid_already,),
        ).fetchall()
    finally:
        conn.close()
    assert new_row is not None, (
        f"B3: 0019 应回填 eid_with={eid_with} 的 timeline 行"
    )
    assert new_row["day_index"] == 3, (
        f"回填 day_index 应取 time_json.timeline_day=3，实际 {new_row['day_index']}"
    )
    assert new_row["time_ref"] == "2026-09-01", (
        f"回填 time_ref 应取 time_json.in_story_date，实际 {new_row['time_ref']!r}"
    )
    assert new_row["description"] == "回填描述", (
        f"回填 description 应复用 plot_events.description，"
        f"实际 {new_row['description']!r}"
    )
    assert len(existing_rows) == 1, (
        "已有 timeline_events 行的事件不应被回填脚本新增行"
    )
    assert existing_rows[0]["description"] == "手工预置", (
        f"已有 timeline 行不应被回填脚本覆盖，原值应保留；"
        f"实际 description={existing_rows[0]['description']!r}"
    )


def test_b3_migration_is_idempotent_on_rerun(tmp_path: Path):
    """B3：0019 重跑（同一 DB 连跑两遍）结果一致——索引行数稳定。"""
    db_path = str(tmp_path / "test.db")
    apply_migrations(db_path, MIGRATIONS_DIR)
    pid = f"prj_{_random_hex()}"

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES (?, ?, 'ACTIVE', datetime('now'), datetime('now'))",
            (pid, "t"),
        )
        # 构造 3 个缺口事件
        for i in range(3):
            conn.execute(
                """
                INSERT INTO plot_events (
                    event_id, project_id, type, cause_json, effects_json,
                    participants_json, location_id, time_json, status,
                    introduced_chapter_id, visibility, who_knows, description
                ) VALUES (?, ?, 'revelation', '[]', '[]', '[]', NULL,
                          ?, 'recorded', NULL, 'RESTRICTED', NULL, NULL)
                """,
                (f"evt_b3_{i:03d}", pid, _json_day(i + 1)),
            )
        # 删除 0019 注册，触发首次回填
        conn.execute(
            "DELETE FROM _migrations WHERE filename = '0019_backfill_timeline_events.sql'",
        )
        conn.commit()
    finally:
        conn.close()

    # 首次跑
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        first_count = conn.execute(
            "SELECT COUNT(*) AS n FROM timeline_events WHERE event_id LIKE 'evt_b3_%'",
        ).fetchone()["n"]
    finally:
        conn.close()
    assert first_count == 3, f"首次回填应产出 3 行，实际 {first_count}"

    # 重跑：手动清掉 0019 注册后再 apply（模拟「迁移系统丢了 _migrations 记录」）
    conn = get_connection(db_path)
    try:
        conn.execute(
            "DELETE FROM _migrations WHERE filename = '0019_backfill_timeline_events.sql'",
        )
        conn.commit()
    finally:
        conn.close()
    apply_migrations(db_path, MIGRATIONS_DIR)
    conn = get_connection(db_path)
    try:
        second_count = conn.execute(
            "SELECT COUNT(*) AS n FROM timeline_events WHERE event_id LIKE 'evt_b3_%'",
        ).fetchone()["n"]
    finally:
        conn.close()
    assert second_count == 3, (
        f"重跑 0019 仍应保持 3 行（幂等），实际 {second_count}"
    )
