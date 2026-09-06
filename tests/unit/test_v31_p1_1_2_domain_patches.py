"""V3.1 P1-1.2 domain 层三处小修的回归测试。

覆盖：
- plot：PlotService.create_event 触发 ``_insert_timeline_event`` 时透传
  visibility/who_knows，timeline_events 行不再落 DDL 默认 'PUBLIC'/NULL，
  与 plot_events 同源；对齐 write_through / 迁移 0019 已修口径。
- plot：PlotService.create_timeline_event 手工端点可选接受 visibility/who_knows。
- plot：_row_to_timeline 行映射补 visibility/who_knows 字段。
- ledger：LedgerService.update_debt 的 who_knows 改走 ``_dump_json_or_null`` 共享
  助手后行为不变（None→DB 原值；[]→'[]'；list→JSON；who_knows=None 时不更新列）。
- reference：ReferenceService.get_canon_detail 返回值必含 ``created_at``（P0 显
  示契约，apps/web/src/api/types.ts:789）。

与既有 B1/B2/B3 回归（test_plot_event_description_timeline.py）形态一致：
- 直连 SQLite + 直调 service；不引 ASGI 噪音。
- 每个 service 路径独立测试；不依赖 workflow / router 串接。
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from packages.core.db import apply_migrations, get_connection
from packages.domain.ledger.service import LedgerService
from packages.domain.plot.service import PlotService
from packages.domain.reference.service import ReferenceService

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


# ----------------------------------------------------------------- helpers


def _random_hex(n: int = 12) -> str:
    return uuid4().hex[:n]


def _bootstrap(tmp_path: Path) -> str:
    """初始化 DB（跑完所有迁移），返回 db_path。"""
    db_path = str(tmp_path / "test.db")
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _make_project(db_path: str) -> str:
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
    return pid


# ===================================================================== plot
# PlotService._insert_timeline_event 透传 visibility/who_knows


def test_plot_create_event_inserts_timeline_with_event_visibility_who_knows(tmp_path: Path):
    """create_event 触发 _insert_timeline_event 时把 plot_events.visibility/who_knows
    透传到 timeline_events 同源，避免「事件表 RESTRICTED + 时间线 PUBLIC」分叉。"""
    db_path = _bootstrap(tmp_path)
    pid = _make_project(db_path)
    svc = PlotService(db_path)

    ev = svc.create_event(
        project_id=pid,
        type="revelation",
        time={"timeline_day": 5, "in_story_date": "2026-09-01"},
        visibility="RESTRICTED",
        who_knows=["char_a", "char_b"],
        description="同源描述",
    )
    assert ev.visibility == "RESTRICTED"
    assert ev.who_knows == ["char_a", "char_b"]

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT visibility, who_knows, description
            FROM timeline_events WHERE event_id = ?
            """,
            (ev.id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "timeline_events 应出现自动同步行"
    assert row["visibility"] == "RESTRICTED", (
        "timeline_events.visibility 应等于 plot_events.visibility，"
        f"实际 {row['visibility']!r}"
    )
    assert json.loads(row["who_knows"]) == ["char_a", "char_b"], (
        "timeline_events.who_knows 应等于 plot_events.who_knows 序列化结果，"
        f"实际 {row['who_knows']!r}"
    )
    assert row["description"] == "同源描述"


def test_plot_create_event_defaults_visibility_who_knows_propagate(tmp_path: Path):
    """create_event 不显式传 visibility/who_knows 时，timeline_events 沿用
    plot_events 同源默认：plot_events.visibility='RESTRICTED'（service 层
    _validate_visibility 默认），who_knows=NULL（service 不预设）。
    关键断言：timeline_events 不再孤立落 'PUBLIC'——这是 P1-1.2 的修复目标。
    """
    db_path = _bootstrap(tmp_path)
    pid = _make_project(db_path)
    svc = PlotService(db_path)

    ev = svc.create_event(
        project_id=pid,
        type="transition",
        time={"timeline_day": 7},
    )

    conn = get_connection(db_path)
    try:
        # plot_events 与 timeline_events 同源
        ev_row = conn.execute(
            "SELECT visibility, who_knows FROM plot_events WHERE event_id = ?",
            (ev.id,),
        ).fetchone()
        row = conn.execute(
            "SELECT visibility, who_knows FROM timeline_events WHERE event_id = ?",
            (ev.id,),
        ).fetchone()
    finally:
        conn.close()
    assert row["visibility"] == ev_row["visibility"], (
        f"timeline_events.visibility 应等于 plot_events.visibility，"
        f"timeline={row['visibility']!r} vs plot={ev_row['visibility']!r}"
    )
    assert row["who_knows"] == ev_row["who_knows"], (
        f"timeline_events.who_knows 应等于 plot_events.who_knows，"
        f"timeline={row['who_knows']!r} vs plot={ev_row['who_knows']!r}"
    )
    assert row["who_knows"] is None, "未传 who_knows 时两表都应 NULL"
    # 关键：不再分叉到 DDL 默认 PUBLIC
    assert row["visibility"] != "PUBLIC" or ev_row["visibility"] == "PUBLIC"


def test_plot_create_timeline_event_accepts_optional_visibility_who_knows(tmp_path: Path):
    """create_timeline_event 手工端点接受可选 visibility/who_knows（缺省 None
    → 'PUBLIC' / NULL 向后兼容）。"""
    db_path = _bootstrap(tmp_path)
    pid = _make_project(db_path)
    plot_svc = PlotService(db_path)
    ev = plot_svc.create_event(
        project_id=pid, type="revelation", time={"timeline_day": 1}
    )

    # 显式传值
    te = plot_svc.create_timeline_event(
        project_id=pid,
        event_id=ev.id,
        day_index=10,
        visibility="RESTRICTED",
        who_knows=["char_z"],
    )
    assert te.visibility == "RESTRICTED"
    assert te.who_knows == ["char_z"]

    # 缺省传值（向后兼容）
    te2 = plot_svc.create_timeline_event(
        project_id=pid,
        event_id=ev.id,
        day_index=20,
    )
    assert te2.visibility == "PUBLIC"
    assert te2.who_knows is None

    # list_timeline_events 行映射也带这两个字段
    items = plot_svc.list_timeline_events(pid)
    by_id = {it.id: it for it in items}
    assert by_id[te.id].visibility == "RESTRICTED"
    assert by_id[te.id].who_knows == ["char_z"]
    assert by_id[te2.id].visibility == "PUBLIC"
    assert by_id[te2.id].who_knows is None


def test_plot_row_to_timeline_reads_visibility_and_who_knows(tmp_path: Path):
    """_row_to_timeline 行映射把 visibility/who_knows 字段带回 dataclass。"""
    db_path = _bootstrap(tmp_path)
    pid = _make_project(db_path)
    plot_svc = PlotService(db_path)
    plot_svc.create_event(
        project_id=pid,
        type="conflict",
        time={"timeline_day": 2},
        visibility="VISIBLE",
        who_knows=["char_x", "char_y"],
    )

    te_list = plot_svc.list_timeline_events(pid)
    assert len(te_list) == 1
    te = te_list[0]
    assert te.visibility == "VISIBLE"
    assert te.who_knows == ["char_x", "char_y"]
    # to_dict 必含两字段
    d = te.to_dict()
    assert "visibility" in d and d["visibility"] == "VISIBLE"
    assert "who_knows" in d and d["who_knows"] == ["char_x", "char_y"]


# =================================================================== ledger
# LedgerService.update_debt who_knows 改用 _dump_json_or_null


def test_ledger_update_debt_who_knows_uses_shared_helper_list_value(tmp_path: Path):
    """update_debt 设 who_knows=['x'] → DB 列存 JSON；读取侧走 decode_who_knows。"""
    db_path = _bootstrap(tmp_path)
    pid = _make_project(db_path)
    svc = LedgerService(db_path)

    created = svc.create_debt(
        project_id=pid, payload=__import__("packages.domain.ledger.models", fromlist=["DebtCreate"]).DebtCreate(
            description="d1"
        ),
    )
    did = created["debt_id"]

    # PATCH who_knows=['x']
    upd = svc.update_debt(
        did,
        payload=__import__("packages.domain.ledger.models", fromlist=["DebtUpdate"]).DebtUpdate(
            who_knows=["x"],
        ),
    )
    assert upd is not None
    assert upd["who_knows"] == ["x"], (
        f"update_debt who_knows=['x'] 应回读为 ['x']，实际 {upd['who_knows']!r}"
    )

    # 直读 DB 验证列内容是 JSON（与 _dump_json_or_null 助手一致）
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT who_knows FROM narrative_debts WHERE debt_id = ?",
            (did,),
        ).fetchone()
    finally:
        conn.close()
    assert row["who_knows"] == '["x"]', (
        f"who_knows 应为 JSON 字符串，实际 {row['who_knows']!r}"
    )


def test_ledger_update_debt_who_knows_uses_shared_helper_empty_list(tmp_path: Path):
    """update_debt 设 who_knows=[] → DB 列存 '[]'（共享助手显式置空）；与
    hooks/debts create 路径三态语义一致。"""
    db_path = _bootstrap(tmp_path)
    pid = _make_project(db_path)
    svc = LedgerService(db_path)

    created = svc.create_debt(
        project_id=pid,
        payload=__import__("packages.domain.ledger.models", fromlist=["DebtCreate"]).DebtCreate(
            description="d2",
        ),
    )
    did = created["debt_id"]

    upd = svc.update_debt(
        did,
        payload=__import__("packages.domain.ledger.models", fromlist=["DebtUpdate"]).DebtUpdate(
            who_knows=[],
        ),
    )
    assert upd is not None
    # _decode_who_knows 对 '[]' 解析成功但允许作为 list 返回；显式空数组
    # 三态语义在 V2.0 Wave B 已约定（shared helper 对齐）
    assert upd["who_knows"] == [], (
        f"update_debt who_knows=[] 应回读为 []，实际 {upd['who_knows']!r}"
    )

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT who_knows FROM narrative_debts WHERE debt_id = ?",
            (did,),
        ).fetchone()
    finally:
        conn.close()
    assert row["who_knows"] == "[]", (
        f"who_knows 应存 '[]'（显式置空），实际 {row['who_knows']!r}"
    )


def test_ledger_update_debt_who_knows_none_does_not_touch_column(tmp_path: Path):
    """update_debt 不传 who_knows → 列沿用 DB 原值（不更新该列）。"""
    db_path = _bootstrap(tmp_path)
    pid = _make_project(db_path)
    svc = LedgerService(db_path)

    created = svc.create_debt(
        project_id=pid,
        payload=__import__("packages.domain.ledger.models", fromlist=["DebtCreate"]).DebtCreate(
            description="d3",
        ),
    )
    did = created["debt_id"]
    assert created["who_knows"] is None

    # 仅 PATCH description，不传 who_knows
    upd = svc.update_debt(
        did,
        payload=__import__("packages.domain.ledger.models", fromlist=["DebtUpdate"]).DebtUpdate(
            description="d3-改",
        ),
    )
    assert upd is not None
    assert upd["description"] == "d3-改"
    assert upd["who_knows"] is None, (
        "不传 who_knows 时不应更新该列，沿用 DB 原值（None）"
    )


# ================================================================= reference
# ReferenceService.get_canon_detail 返回 created_at


def test_reference_get_canon_detail_includes_created_at(tmp_path: Path):
    """get_canon_detail 返回 dict 必含 ``created_at``（与前端 CanonDetail 契约
    apps/web/src/api/types.ts:789 对齐）。"""
    db_path = _bootstrap(tmp_path)
    pid = _make_project(db_path)
    canon_id = f"can_{_random_hex()}"
    ts = "2026-09-01T12:00:00+00:00"
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO reference_canons (
                canon_id, project_id, title, reader_profile, status,
                canon_json, report_md, created_at
            ) VALUES (?, ?, 'Test', 'male_fantasy', 'active',
                      '{"logline": "x"}', 'r', ?)
            """,
            (canon_id, pid, ts),
        )
        conn.execute(
            """
            INSERT INTO canon_extracts (
                extract_id, canon_id, chapter_index, extract_json, created_at
            ) VALUES (?, ?, 1, '{}', ?)
            """,
            (f"ex_{_random_hex()}", canon_id, ts),
        )
        conn.commit()
    finally:
        conn.close()

    svc = ReferenceService(db_path)
    detail = svc.get_canon_detail(canon_id)
    assert detail is not None, "get_canon_detail 应返回 dict"
    assert "created_at" in detail, (
        "get_canon_detail 返回 dict 必须含 created_at 字段（P0 契约，"
        "前端 CanonDetail 详情抽屉渲染依赖）"
    )
    assert detail["created_at"] == ts, (
        f"created_at 应等于 DB 列值，实际 {detail['created_at']!r}"
    )
    # extracts 仍含 created_at
    assert len(detail["extracts"]) == 1
    assert detail["extracts"][0]["created_at"] == ts
