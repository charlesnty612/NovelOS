"""V3.1 P1-1.2 PlotService.create_event description 全链路回归。

覆盖：
- create 带 description → DB 落 + get/list 一致返回。
- description 缺省 → 行为零变化（不传等同旧调用）。
- description 非 str（如 int 123）→ ValidationError。
- description 空串 / 纯空白 → 归一为 None 落库。
- timeline 同步行 description 复用 plot_events.description。
- update_event 同步支持 description（含校验与归一）。
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from packages.core.db import apply_migrations, get_connection
from packages.domain.plot.service import PlotService, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


def _random_hex(n: int = 12) -> str:
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


def _read_description(db_path: str, event_id: str) -> str | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT description FROM plot_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else row["description"]


# ===================================================== create_event 带 description


def test_create_event_persists_description(tmp_path: Path):
    """create_event(description=...) → DB 落字串 + get/list 一致返回。"""
    db_path, pid = _bootstrap(tmp_path)
    svc = PlotService(db_path)
    ev = svc.create_event(
        project_id=pid,
        type="revelation",
        time={"timeline_day": 3},
        description="玉惜轩夜访，林渊未答关键一问。",
    )
    assert ev.description == "玉惜轩夜访，林渊未答关键一问。"
    assert _read_description(db_path, ev.id) == "玉惜轩夜访，林渊未答关键一问。"
    # get/list 一致
    got = svc.get_event(ev.id)
    assert got is not None and got.description == "玉惜轩夜访，林渊未答关键一问。"
    listed = {e.id: e.description for e in svc.list_events(pid)}
    assert listed[ev.id] == "玉惜轩夜访，林渊未答关键一问。"


def test_create_event_without_description_unchanged_behavior(tmp_path: Path):
    """不传 description → 旧调用零行为变化（默认 None，落库/读取一致）。"""
    db_path, pid = _bootstrap(tmp_path)
    svc = PlotService(db_path)
    ev = svc.create_event(
        project_id=pid,
        type="conflict",
        time={"timeline_day": 1},
    )
    assert ev.description is None
    assert _read_description(db_path, ev.id) is None
    got = svc.get_event(ev.id)
    assert got is not None and got.description is None


def test_create_event_description_must_be_string(tmp_path: Path):
    """description 非 str（如 int）→ ValidationError（422）。"""
    db_path, pid = _bootstrap(tmp_path)
    svc = PlotService(db_path)
    with pytest.raises(ValidationError):
        svc.create_event(
            project_id=pid,
            type="revelation",
            time={"timeline_day": 1},
            description=123,  # type: ignore[arg-type]
        )


def test_create_event_empty_description_normalized_to_none(tmp_path: Path):
    """description 空串 / 纯空白 → 归一为 None 落库。"""
    db_path, pid = _bootstrap(tmp_path)
    svc = PlotService(db_path)

    ev_empty = svc.create_event(
        project_id=pid,
        type="conflict",
        time={"timeline_day": 1},
        description="",
    )
    assert ev_empty.description is None
    assert _read_description(db_path, ev_empty.id) is None

    ev_ws = svc.create_event(
        project_id=pid,
        type="decision",
        time={"timeline_day": 2},
        description="   \t\n  ",
    )
    assert ev_ws.description is None
    assert _read_description(db_path, ev_ws.id) is None


def test_create_event_description_strips_whitespace(tmp_path: Path):
    """description 前后空白 → 仅 strip 不剥空时保留字串。"""
    db_path, pid = _bootstrap(tmp_path)
    svc = PlotService(db_path)
    ev = svc.create_event(
        project_id=pid,
        type="encounter",
        time={"timeline_day": 4},
        description="  林渊与沈芷初次对峙  ",
    )
    assert ev.description == "林渊与沈芷初次对峙"
    assert _read_description(db_path, ev.id) == "林渊与沈芷初次对峙"


def test_create_event_timeline_sync_carries_description(tmp_path: Path):
    """create_event 带 timeline_day 时 → timeline_events.description 复用 plot_events.description。"""
    db_path, pid = _bootstrap(tmp_path)
    svc = PlotService(db_path)
    ev = svc.create_event(
        project_id=pid,
        type="revelation",
        time={"timeline_day": 7},
        description="时间线同步测试描述",
    )
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT description FROM timeline_events WHERE event_id = ?",
            (ev.id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "timeline_events 应出现同步行"
    assert row["description"] == "时间线同步测试描述"


def test_create_event_timeline_sync_when_description_none(tmp_path: Path):
    """timeline 同步在 description=None 时也写 None（不传等同旧调用）。"""
    db_path, pid = _bootstrap(tmp_path)
    svc = PlotService(db_path)
    ev = svc.create_event(
        project_id=pid,
        type="conflict",
        time={"timeline_day": 8},
    )
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT description FROM timeline_events WHERE event_id = ?",
            (ev.id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["description"] is None


# ===================================================== update_event description


def test_update_event_persists_description(tmp_path: Path):
    """update_event(description=...) → 落库与读回一致。"""
    db_path, pid = _bootstrap(tmp_path)
    svc = PlotService(db_path)
    ev = svc.create_event(
        project_id=pid,
        type="revelation",
        time={"timeline_day": 1},
    )
    updated = svc.update_event(ev.id, description="追加描述")
    assert updated.description == "追加描述"
    assert _read_description(db_path, ev.id) == "追加描述"


def test_update_event_description_must_be_string(tmp_path: Path):
    """update_event description 非 str → ValidationError。"""
    db_path, pid = _bootstrap(tmp_path)
    svc = PlotService(db_path)
    ev = svc.create_event(
        project_id=pid,
        type="revelation",
        time={"timeline_day": 1},
    )
    with pytest.raises(ValidationError):
        svc.update_event(ev.id, description=123)  # type: ignore[arg-type]


def test_update_event_empty_description_normalized_to_none(tmp_path: Path):
    """update_event 空串 description → 归一为 None。"""
    db_path, pid = _bootstrap(tmp_path)
    svc = PlotService(db_path)
    ev = svc.create_event(
        project_id=pid,
        type="revelation",
        time={"timeline_day": 1},
        description="原值",
    )
    updated = svc.update_event(ev.id, description="   ")
    assert updated.description is None
    assert _read_description(db_path, ev.id) is None
