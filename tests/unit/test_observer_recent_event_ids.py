"""build_observer_input V3.1.1 O-3 recent_event_ids 单测。

覆盖点（packages/core/context_engine/builders.py 的 O-3 改造）：
1. recent_event_ids 默认从 plot_events 取最近 N 条注入 payload.config.recent_event_ids。
2. recent_event_ids 显式传入时优先用调用方的 list。
3. recent_event_ids_limit=0 时白名单为空 list。
4. plot_events 为空时白名单为空 list。
5. payload.config.recent_event_ids 类型始终为 list[str]。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.config import Settings
from packages.core.context_engine.builders import (
    _DEFAULT_RECENT_EVENT_IDS_LIMIT,
    _load_recent_event_ids,
    build_observer_input,
)
from packages.core.db import apply_migrations, get_connection


def _create_db(tmp_path: Path) -> str:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings.db_path


def _make_project_and_chapter(db_path: str) -> tuple[str, str]:
    conn = get_connection(db_path)
    try:
        # project
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES ('p_001', 'O3Test', 'ACTIVE', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        )
        # chapter
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, status, created_at, updated_at) "
            "VALUES ('ch_001', 'p_001', 1, '测试章', 'REVIEWED', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        )
        # plot_events（5 条）
        for i in range(5):
            conn.execute(
                "INSERT INTO plot_events (event_id, project_id, type, time_json, status) "
                "VALUES (?, 'p_001', 'revelation', '{\"timeline_day\":1}', 'recorded')",
                (f"evt_ch60_{i:03d}",),
            )
        conn.commit()
    finally:
        conn.close()
    return "p_001", "ch_001"


def test_load_recent_event_ids_default_limit(tmp_path: Path):
    """默认 limit=30：从 plot_events 取最近 30 条（实际 5 条）。"""
    db_path = _create_db(tmp_path)
    _make_project_and_chapter(db_path)
    ids = _load_recent_event_ids(db_path, "p_001", limit=_DEFAULT_RECENT_EVENT_IDS_LIMIT)
    assert isinstance(ids, list)
    assert len(ids) == 5
    # 按 rowid DESC：后插入的在前
    assert ids[0] == "evt_ch60_004"


def test_load_recent_event_ids_limit_smaller(tmp_path: Path):
    """显式 limit=2：取最近 2 条。"""
    db_path = _create_db(tmp_path)
    _make_project_and_chapter(db_path)
    ids = _load_recent_event_ids(db_path, "p_001", limit=2)
    assert ids == ["evt_ch60_004", "evt_ch60_003"]


def test_load_recent_event_ids_limit_zero_returns_empty(tmp_path: Path):
    """limit=0 / 负数 → 空 list（白名单关闭）。"""
    db_path = _create_db(tmp_path)
    _make_project_and_chapter(db_path)
    assert _load_recent_event_ids(db_path, "p_001", limit=0) == []
    assert _load_recent_event_ids(db_path, "p_001", limit=-1) == []


def test_load_recent_event_ids_no_plot_events(tmp_path: Path):
    """plot_events 表无行 → 空 list。"""
    db_path = _create_db(tmp_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, status, created_at, updated_at) "
            "VALUES ('p_empty', 'Empty', 'ACTIVE', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        )
        conn.commit()
    finally:
        conn.close()
    assert _load_recent_event_ids(db_path, "p_empty", limit=30) == []


def test_build_observer_input_includes_recent_event_ids_in_config(tmp_path: Path):
    """默认路径：build_observer_input 自动注入 recent_event_ids 到 payload.config。"""
    db_path = _create_db(tmp_path)
    _make_project_and_chapter(db_path)
    payload = build_observer_input(db_path, "ch_001", snapshot_mode="full")
    cfg = payload.get("config") or {}
    assert "recent_event_ids" in cfg
    assert isinstance(cfg["recent_event_ids"], list)
    assert len(cfg["recent_event_ids"]) == 5


def test_build_observer_input_explicit_recent_event_ids_overrides(tmp_path: Path):
    """显式传 recent_event_ids 时优先于 DB 自动取数。"""
    db_path = _create_db(tmp_path)
    _make_project_and_chapter(db_path)
    payload = build_observer_input(
        db_path, "ch_001",
        snapshot_mode="full",
        recent_event_ids=["evt_custom_001", "evt_custom_002"],
    )
    cfg = payload.get("config") or {}
    assert cfg["recent_event_ids"] == ["evt_custom_001", "evt_custom_002"]


def test_build_observer_input_recent_event_ids_limit_zero(tmp_path: Path):
    """recent_event_ids_limit=0 → 空白名单。"""
    db_path = _create_db(tmp_path)
    _make_project_and_chapter(db_path)
    payload = build_observer_input(
        db_path, "ch_001",
        snapshot_mode="full",
        recent_event_ids_limit=0,
    )
    cfg = payload.get("config") or {}
    assert cfg["recent_event_ids"] == []


def test_default_recent_event_ids_limit_is_30():
    """_DEFAULT_RECENT_EVENT_IDS_LIMIT 锁死为 30（任务书定值）。"""
    assert _DEFAULT_RECENT_EVENT_IDS_LIMIT == 30
