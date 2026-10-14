"""Backup 单元测试（节 2）：自引用外键重映射 + 跨项目间接过滤。

由 test_backup.py 按既有分节拆出；_seed_branch_with_parent /
_seed_second_independent_project 为本节私有。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.core.backup import BackupService
from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from tests.unit.backup_fixtures import (
    _insert_row,
    _seed_minimal_project,
    make_db_path,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    """转发到 backup_fixtures.make_db_path（pytest 按模块命名空间发现 fixture）。"""
    return make_db_path(tmp_path)


# ---------------------------------------------------------------------------
# P2-2 补测：自引用外键（state_deltas.supersedes）+ 间接过滤（timeline_events）
# ---------------------------------------------------------------------------


def _seed_branch_with_parent(db_path: str, pid: str) -> tuple[str, str]:
    """在源项目下造一对分支（parent + child），child.parent_branch_id 指向 parent；
    返回 (parent_branch_id, child_branch_id)。验证自引用第二轮改写。
    """
    now = now_iso()
    parent_id = new_id("br")
    child_id = new_id("br")
    conn = get_connection(db_path)
    try:
        _insert_row(
            conn,
            """
            INSERT INTO branches
                (branch_id, project_id, name, parent_branch_id,
                 base_state_version, status, created_at)
            VALUES
                (:branch_id, :project_id, 'parent', NULL, 0, 'ACTIVE',
                 :created_at)
            """,
            {
                "branch_id": parent_id,
                "project_id": pid,
                "created_at": now,
            },
        )
        _insert_row(
            conn,
            """
            INSERT INTO branches
                (branch_id, project_id, name, parent_branch_id,
                 base_state_version, status, created_at)
            VALUES
                (:branch_id, :project_id, 'child', :parent_id, 0, 'ACTIVE',
                 :created_at)
            """,
            {
                "branch_id": child_id,
                "project_id": pid,
                "parent_id": parent_id,
                "created_at": now,
            },
        )
    finally:
        conn.close()
    return parent_id, child_id


def _seed_second_independent_project(db_path: str) -> str:
    """构造第二个项目（无关项目），用于 timeline_events 间接过滤的阴性对照。

    返回第二个项目 id；在该项目下插一条 plot_events + 一条 timeline_events，
    验证导出时不会混入源项目包。
    """
    now = now_iso()
    other_pid = new_id("prj")
    conn = get_connection(db_path)
    try:
        _insert_row(
            conn,
            """
            INSERT INTO projects
                (project_id, name, premise, genre, target_words, status,
                 foreshadow_overdue_chapters, created_at, updated_at)
            VALUES
                (:project_id, :name, :premise, :genre, :target_words,
                 'ACTIVE', 30, :created_at, :updated_at)
            """,
            {
                "project_id": other_pid,
                "name": "无关项目",
                "premise": "不应混入导出包",
                "genre": "科幻",
                "target_words": 100_000,
                "created_at": now,
                "updated_at": now,
            },
        )
        # 无关项目的 plot_event + timeline_event（带 distinct 字符串，便于断言"不在包内"）
        other_event_id = "event_other_uniq_marker_xyz"
        _insert_row(
            conn,
            """
            INSERT INTO plot_events
                (event_id, project_id, type, cause_json, effects_json,
                 participants_json, time_json, status, visibility,
                 who_knows)
            VALUES
                (:event_id, :project_id, 'other', '[]', '[]', '[]',
                 '{"timeline_day":1}', 'planned', 'RESTRICTED', NULL)
            """,
            {"event_id": other_event_id, "project_id": other_pid},
        )
        _insert_row(
            conn,
            """
            INSERT INTO timeline_events
                (timeline_event_id, project_id, event_id, day_index, time_ref,
                 description)
            VALUES
                (:tle_id, :project_id, :event_id, 1, NULL, :desc)
            """,
            {
                "tle_id": "tle_other_uniq_marker_xyz",
                "project_id": other_pid,
                "event_id": other_event_id,
                "desc": "无关项目时间线 - 不应被导出",
            },
        )
    finally:
        conn.close()
    return other_pid


def test_import_remaps_branches_self_reference(db_path: str) -> None:
    """branches.parent_branch_id 自引用：导出 → 导入后，新项目两条分支都在，
    child.parent_branch_id 必须指向新映射后的 parent（不是旧 parent，也不是 NULL）。
    """
    pid = _seed_minimal_project(db_path)
    parent_id, child_id = _seed_branch_with_parent(db_path, pid)

    pkg = BackupService(db_path).export_project(pid)
    # 导出包内含 3 条分支（_seed_minimal_project 的 main + parent + child）
    assert len(pkg["tables"]["branches"]) == 3

    new_project = BackupService(db_path).import_project(pkg)
    new_pid = new_project["project_id"]

    conn = get_connection(db_path)
    try:
        new_branches = conn.execute(
            "SELECT branch_id, name, parent_branch_id FROM branches "
            "WHERE project_id = ? ORDER BY name ASC, branch_id ASC",
            (new_pid,),
        ).fetchall()
        names = sorted([r["name"] for r in new_branches])
        assert names == ["child", "main", "parent"], names

        # 找到新 child / parent（按 name 匹配）
        new_child = next(r for r in new_branches if r["name"] == "child")
        new_parent = next(r for r in new_branches if r["name"] == "parent")
        new_main = next(r for r in new_branches if r["name"] == "main")

        # 主键重映射：new id ≠ old id
        assert new_child["branch_id"] != child_id
        assert new_parent["branch_id"] != parent_id

        # 第二轮改写：new_child.parent_branch_id == new_parent.branch_id（且 ≠ 旧 id）
        assert new_child["parent_branch_id"] == new_parent["branch_id"], (
            f"second-pass rewrite failed: "
            f"new_child.parent_branch_id={new_child['parent_branch_id']!r} "
            f"expected new_parent.branch_id={new_parent['branch_id']!r} "
            f"(old parent_id={parent_id!r})"
        )
        assert new_child["parent_branch_id"] != parent_id

        # main 是根分支（源 parent_branch_id=NULL），导入后应仍为 NULL
        assert new_main["parent_branch_id"] is None
    finally:
        conn.close()


def test_import_remaps_state_deltas_self_reference(db_path: str) -> None:
    """state_deltas 自引用外键（supersedes）：导出 → 导入后，新项目两条 deltas 存在，
    且后一条的 supersedes 指向新映射后的 id（不是旧 id，也不是 NULL）。
    """
    now = now_iso()
    pid = _seed_minimal_project(db_path)
    conn = get_connection(db_path)
    try:
        cid_chapter = conn.execute(
            "SELECT chapter_id FROM chapters WHERE project_id = ?",
            (pid,),
        ).fetchone()["chapter_id"]

        # workflow_runs 行（满足 state_deltas.workflow_run_id FK）
        # 先插 workflows 根（workflow_id FK）
        _insert_row(
            conn,
            """
            INSERT INTO workflows
                (workflow_id, name, version, definition_json, created_at,
                 updated_at)
            VALUES
                ('wf_chapter_commit', 'chapter-commit', 'v1', '{}',
                 :created_at, :updated_at)
            """,
            {"created_at": now, "updated_at": now},
        )
        run_id = new_id("wfr")
        _insert_row(
            conn,
            """
            INSERT INTO workflow_runs
                (run_id, workflow_id, chapter_id, status, current_node,
                 checkpoint_json, error, retry_count, started_at)
            VALUES
                (:run_id, 'wf_chapter_commit', :chapter_id, 'COMPLETED', NULL,
                 '{}', NULL, 0, :started_at)
            """,
            {
                "run_id": run_id,
                "chapter_id": cid_chapter,
                "started_at": now,
            },
        )

        # 两条 state_deltas：后一条 supersedes 前一条
        d1_id = new_id("dlt")
        d2_id = new_id("dlt")
        _insert_row(
            conn,
            """
            INSERT INTO state_deltas
                (delta_id, chapter_id, workflow_run_id, previous_state_version,
                 delta_version, schema_version, payload_json, status,
                 supersedes, created_by, created_at)
            VALUES
                (:delta_id, :chapter_id, :run_id, 0, 1, 'state-delta-v0',
                 '{}', 'proposed', NULL, 'tester', :created_at)
            """,
            {
                "delta_id": d1_id,
                "chapter_id": cid_chapter,
                "run_id": run_id,
                "created_at": now,
            },
        )
        _insert_row(
            conn,
            """
            INSERT INTO state_deltas
                (delta_id, chapter_id, workflow_run_id, previous_state_version,
                 delta_version, schema_version, payload_json, status,
                 supersedes, created_by, created_at)
            VALUES
                (:delta_id, :chapter_id, :run_id, 0, 1, 'state-delta-v0',
                 '{}', 'proposed', :super_id, 'tester', :created_at)
            """,
            {
                "delta_id": d2_id,
                "chapter_id": cid_chapter,
                "run_id": run_id,
                "super_id": d1_id,
                "created_at": now,
            },
        )
    finally:
        conn.close()

    # 导出 → 导入
    pkg = BackupService(db_path).export_project(pid)
    # 导出包内 state_deltas 必须含 2 行
    assert len(pkg["tables"]["state_deltas"]) == 2
    # 旧 d2.supersedes = d1_id（源 id）
    d2_old = next(
        r for r in pkg["tables"]["state_deltas"]
        if r["delta_id"] == d2_id
    )
    assert d2_old["supersedes"] == d1_id

    new_project = BackupService(db_path).import_project(pkg)
    new_pid = new_project["project_id"]

    # 新项目下 state_deltas 应有 2 行
    conn = get_connection(db_path)
    try:
        # 新 delta 主键由 uuid4 随机生成，导出端按 delta_id ASC 排序后可能让
        # d2 出现在 d1 之前，导致导入后 rowid ASC 不再与源 INSERT 顺序一致。
        # 这里用语义识别：d1.supersedes=NULL（源行就是 NULL），d2 有 supersedes。
        new_deltas = conn.execute(
            "SELECT delta_id, supersedes FROM state_deltas "
            "WHERE chapter_id IN (SELECT chapter_id FROM chapters "
            "WHERE project_id = ?)",
            (new_pid,),
        ).fetchall()
        assert len(new_deltas) == 2
        null_rows = [r for r in new_deltas if r["supersedes"] is None]
        non_null_rows = [r for r in new_deltas if r["supersedes"] is not None]
        assert len(null_rows) == 1, (
            f"expected exactly 1 delta with NULL supersedes, got {len(null_rows)}"
        )
        assert len(non_null_rows) == 1, (
            f"expected exactly 1 delta with non-NULL supersedes, got {len(non_null_rows)}"
        )
        # 源库 d1.supersedes=NULL，d2.supersedes=d1_id；语义识别 new_d1/new_d2
        new_d1_id = null_rows[0]["delta_id"]
        new_d2_row = non_null_rows[0]
        new_d2_id = new_d2_row["delta_id"]

        # 关键断言：新 id 不等于旧 id（确认发生了重映射）
        assert new_d1_id != d1_id
        assert new_d2_id != d2_id

        # 第二轮改写：d2.supersedes 必须是新 d1.id，**不是**旧 d1.id，也**不是**NULL
        assert new_d2_row["supersedes"] == new_d1_id, (
            f"second-pass rewrite failed: "
            f"new_d2.supersedes={new_d2_row['supersedes']!r} "
            f"expected new_d1_id={new_d1_id!r} (old d1_id={d1_id!r})"
        )
        assert new_d2_row["supersedes"] != d1_id
    finally:
        conn.close()


def test_export_filters_timeline_events_by_plot_event_project(db_path: str) -> None:
    """timeline_events 间接过滤：源项目 plot_event 关联的 timeline_events 应在包内，
    无关项目的 timeline_events 应被剔除。
    """
    pid = _seed_minimal_project(db_path)

    # 源项目造一个 plot_event + 两条 timeline_events（都挂到源 plot_event）
    conn = get_connection(db_path)
    try:
        src_event_id = "event_src_marker_abc"
        _insert_row(
            conn,
            """
            INSERT INTO plot_events
                (event_id, project_id, type, cause_json, effects_json,
                 participants_json, time_json, status, visibility,
                 who_knows)
            VALUES
                (:event_id, :project_id, 'other', '[]', '[]', '[]',
                 '{"timeline_day":1}', 'planned', 'RESTRICTED', NULL)
            """,
            {"event_id": src_event_id, "project_id": pid},
        )
        # 两条 timeline_events，distinct 描述便于断言
        for idx, desc in enumerate(["源项目 tle 1", "源项目 tle 2"]):
            _insert_row(
                conn,
                """
                INSERT INTO timeline_events
                    (timeline_event_id, project_id, event_id, day_index,
                     time_ref, description)
                VALUES
                    (:tle_id, :project_id, :event_id, :day, NULL, :desc)
                """,
                {
                    "tle_id": f"tle_src_marker_{idx}",
                    "project_id": pid,
                    "event_id": src_event_id,
                    "day": idx + 1,
                    "desc": desc,
                },
            )
    finally:
        conn.close()

    # 造一个无关项目（带自己的 plot_event + timeline_event）
    other_pid = _seed_second_independent_project(db_path)

    # 导出源项目包
    pkg = BackupService(db_path).export_project(pid)
    tle_rows = pkg["tables"]["timeline_events"]

    # 关键断言 1：只含源项目 timeline_events（2 行），不含无关项目
    assert len(tle_rows) == 2, (
        f"expected 2 timeline_events for source project, got {len(tle_rows)}"
    )
    assert all(r["project_id"] == pid for r in tle_rows)
    # 无关项目的 marker 必须不在包内
    for r in tle_rows:
        assert "tle_other_uniq_marker_xyz" not in r["timeline_event_id"]
        assert r["description"] != "无关项目时间线 - 不应被导出"

    # 关键断言 2：导入后新项目 timeline_events 行数=2，且事件 id 已重映射
    new_project = BackupService(db_path).import_project(pkg)
    new_pid = new_project["project_id"]

    conn = get_connection(db_path)
    try:
        new_tle = conn.execute(
            "SELECT timeline_event_id, project_id, event_id FROM timeline_events "
            "WHERE project_id = ? ORDER BY timeline_event_id ASC",
            (new_pid,),
        ).fetchall()
        assert len(new_tle) == 2

        # 新 event_id ≠ 旧 event_id（plot_events 也被重映射）
        for r in new_tle:
            assert r["event_id"] != src_event_id, (
                "timeline_events.event_id should be remapped to new plot_events.event_id"
            )
            # 新 event_id 必须是新的 event_ 前缀
            assert r["event_id"].startswith("event_")
            assert r["event_id"] != "event_src_marker_abc"

        # 无关项目仍存在且不受影响
        other_count = conn.execute(
            "SELECT COUNT(*) AS c FROM timeline_events WHERE project_id = ?",
            (other_pid,),
        ).fetchone()["c"]
        assert other_count == 1, (
            f"unrelated project timeline_events disturbed: {other_count}"
        )
    finally:
        conn.close()


