"""题材包 excerpt 的**降级范围**契约（2026-10-16 批次九）。

背景（结清 2026-10-15 登记的挂账）
----------------------------------
``_genre_pack_excerpt`` 原先裸 ``except sqlite3.OperationalError`` 一律降级为「未绑定」，
而它的注释只声称覆盖「0025 未跑的极老库」（缺表 / 缺列）。**捕获范围比注释主张宽** ⇒
``database is locked`` / ``disk I/O error`` / ``database disk image is malformed``
会被**静默吞掉**——作者以为体裁约束生效、产出的正文却没带体裁，且零告警。这正是本
项目最忌的「静默降级」形状（同 ``routers/workflows/fast_mode.py`` 的收窄理由）。

处置：收窄到「缺表 / 缺列」才降级，其余上抛；并删掉 ``planner_input`` / ``scene_planner``
两层**无差别再吞**的外层 wrapper——它们会把内层刚收窄的范围原样放大。

两个面各守一半（缺一不可）
--------------------------
- **内层**（``_genre_pack_excerpt``）：缺表 / 缺列 → ``(None, None)``；锁 / I/O → 上抛。
- **调用方**（``_merge_genre_pack_planner_fields`` / ``_collect_scene_planner_inputs``）：
  非结构故障必须冒到调用方——只测内层测不到「外层又吞了一次」。

判别（撤修复必红）：把内层改回裸 ``except sqlite3.OperationalError`` ⇒ 第 3/4 例红；
把任一层外层 wrapper 加回来 ⇒ 对应的调用方用例红。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from packages.core.context_engine.builders_common import _genre_pack_excerpt
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"


class _BoomConn:
    """伪连接：``execute`` 直接抛指定消息的 ``OperationalError``（不碰真库）。"""

    def __init__(self, message: str) -> None:
        self._message = message

    def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise sqlite3.OperationalError(self._message)


# ---------------------------------------------------------------------------
# 内层：只有「缺表 / 缺列」才降级
# ---------------------------------------------------------------------------


def test_missing_table_degrades_to_unbound():
    """极老库缺 ``genre_packs`` 表 → 视为未绑定，不炸。"""
    assert _genre_pack_excerpt(
        _BoomConn("no such table: genre_packs"), "prj_x"
    ) == (None, None)


def test_missing_column_degrades_to_unbound():
    """0025 未跑 ⇒ 缺 ``projects.genre_pack_id`` 列 → 视为未绑定，不炸。"""
    assert _genre_pack_excerpt(
        _BoomConn("no such column: pj.genre_pack_id"), "prj_x"
    ) == (None, None)


@pytest.mark.parametrize(
    "message",
    ["database is locked", "disk I/O error", "database disk image is malformed"],
)
def test_runtime_db_failures_propagate(message: str):
    """锁 / I/O / 损坏**不是**「未绑定」，必须上抛，不得静默丢题材包。"""
    with pytest.raises(sqlite3.OperationalError):
        _genre_pack_excerpt(_BoomConn(message), "prj_x")


# ---------------------------------------------------------------------------
# 调用方：外层 wrapper 已删除，非结构故障必须冒出来
# ---------------------------------------------------------------------------


def test_planner_merge_propagates_runtime_db_error(tmp_path: Path, monkeypatch):
    """``_merge_genre_pack_planner_fields`` 不得再吞掉非结构故障。"""
    from packages.workflows.chapter_plan import planner_input as pi

    def _boom(_conn, _pid, *, consumer=None):  # noqa: ANN001
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(pi, "_genre_pack_excerpt", _boom)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        pi._merge_genre_pack_planner_fields({}, str(tmp_path / "x.db"), "prj_x")


def test_scene_planner_propagates_runtime_db_error(tmp_path: Path, monkeypatch):
    """``_collect_scene_planner_inputs`` 不得再吞掉非结构故障。"""
    from packages.workflows.chapter_write import scene_planner as sp

    db_path = tmp_path / "t.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    pid = new_id("prj")
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words,"
            " status, created_at, updated_at) VALUES (?,?,NULL,NULL,NULL,'ACTIVE',?,?)",
            (pid, "p", now, now),
        )
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json,"
            " status, visibility, who_knows, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, pid, 1, "C", '{"chapter_goal": "x"}', "PLANNED", "VISIBLE",
             None, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    def _boom(_conn, _pid, *, consumer=None):  # noqa: ANN001
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(sp, "_genre_pack_excerpt", _boom)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        sp._collect_scene_planner_inputs(str(db_path), cid, {})
