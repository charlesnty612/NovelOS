"""VolumeService 单测（V3.4 多卷与规模——组织层）。

覆盖：
- create：默认 status='active' + 时间戳；project 不存在 / number 重复 /
  已存在 active 卷 → 抛对应异常；
- list：含 chapter_count 聚合、按 number ASC、空项目 → []；
- update：title 改写；status='sealed' → 'active' 反向跳变 → VolumeSealedError；
- seal：status='sealed' + terminal_snapshot_json 冻结当前 story_states 最新快照；
  无快照时存空对象 {}；重复 seal → VolumeConflictError；volume 不存在 → None；
- assign_chapter：active 卷可挂；sealed 卷 → VolumeSealedError；
  chapter 不存在 → VolumeValidationError；跨 project → VolumeValidationError；
  幂等（同章已在该卷上视为成功）；
- 列 ↔ chapters.volume_id 同步刷新。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.core.db import apply_migrations, get_connection  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402
from packages.domain.volume import (  # noqa: E402
    VolumeConflictError,
    VolumeNotFoundError,
    VolumeSealedError,
    VolumeService,
    VolumeValidationError,
)

MIGRATIONS_DIR = ROOT / "database" / "migrations"


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _make_project(db_path: Path, name: str = "vol-test") -> str:
    pid = new_id("prj")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO projects (project_id, name, premise, genre, target_words, "
            "status, created_at, updated_at) VALUES (?, ?, NULL, NULL, NULL, "
            "'ACTIVE', ?, ?)",
            (pid, name, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def _make_chapter(
    db_path: Path,
    project_id: str,
    number: int,
    title: str = "x",
) -> str:
    """直插 chapter（绕开 ChapterService 状态机）。"""
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json,
                 status, visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, project_id, number, title, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _seed_snapshot(db_path: Path, project_id: str, snapshot: dict) -> int:
    """直插 story_states 一行（state_version 起始=1）。

    story_states.commit_id 是 NOT NULL REFERENCES commits；commits 又依赖
    branches / chapters / state_deltas，故先插一条最小可用的 commit 链路。
    """
    version = 1
    conn = get_connection(db_path)
    try:
        # 1) branch（main）
        branch_id = "br_main_" + project_id[-8:]
        conn.execute(
            """
            INSERT OR IGNORE INTO branches
                (branch_id, project_id, name, parent_branch_id,
                 base_state_version, status, created_at)
            VALUES (?, ?, 'main', NULL, 0, 'ACTIVE', ?)
            """,
            (branch_id, project_id, now_iso()),
        )
        # 2) chapter（最小占位）
        chapter_id = new_id("ch")
        conn.execute(
            """
            INSERT OR IGNORE INTO chapters
                (chapter_id, project_id, number, title, plan_json,
                 status, visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, 1, '_seed', '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (chapter_id, project_id, now_iso(), now_iso()),
        )
        # 3) delta
        delta_id = new_id("dlt")
        conn.execute(
            """
            INSERT OR IGNORE INTO state_deltas
                (delta_id, chapter_id, workflow_run_id, previous_state_version,
                 delta_version, schema_version, payload_json, status,
                 supersedes, created_by, created_at)
            VALUES (?, ?, ?, 0, 1, 'state-delta-v0', '{}', 'applied',
                    NULL, 'system', ?)
            """,
            (delta_id, chapter_id, new_id("wfr"), now_iso()),
        )
        # 4) commit
        commit_id = new_id("cmt")
        conn.execute(
            """
            INSERT OR IGNORE INTO commits
                (commit_id, project_id, branch_id, chapter_id,
                 previous_state_version, resulting_state_version, delta_id,
                 validation_json, author_approval_json, timestamp,
                 workflow_run_id, rollback_of)
            VALUES (?, ?, ?, ?, 0, ?, ?, '{}', '{}', ?, ?, NULL)
            """,
            (commit_id, project_id, branch_id, chapter_id, version,
             delta_id, now_iso(), new_id("wfr")),
        )
        # 5) story_states
        conn.execute(
            """
            INSERT INTO story_states
                (project_id, state_version, snapshot_json, commit_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                project_id,
                version,
                json.dumps(snapshot, ensure_ascii=False),
                commit_id,
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return version


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_volume_default_status_active(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)

    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate
    row = svc.create(pid, VolumeCreate(number=1, title="第一卷"))

    assert row["volume_id"].startswith("vol_")
    assert row["project_id"] == pid
    assert row["number"] == 1
    assert row["title"] == "第一卷"
    assert row["status"] == "active"
    assert row["terminal_snapshot_json"] is None
    assert row["created_at"]
    assert row["updated_at"]


def test_create_volume_project_not_found_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate

    with pytest.raises(VolumeNotFoundError):
        svc.create("prj_nonexistent", VolumeCreate(number=1))


def test_create_volume_duplicate_number_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate

    svc.create(pid, VolumeCreate(number=1, title="v1"))
    with pytest.raises(VolumeConflictError):
        svc.create(pid, VolumeCreate(number=1, title="v1-dup"))


def test_create_volume_existing_active_conflict(tmp_path: Path):
    """同 project 下已存在 active 卷 → 拒绝；必须先 seal 旧卷。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate

    v1 = svc.create(pid, VolumeCreate(number=1, title="v1"))
    assert v1["status"] == "active"

    with pytest.raises(VolumeConflictError, match="active volume"):
        svc.create(pid, VolumeCreate(number=2, title="v2"))


def test_create_volume_after_seal_allowed(tmp_path: Path):
    """封存旧 active 卷 → 允许创建新 active 卷。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate

    v1 = svc.create(pid, VolumeCreate(number=1, title="v1"))
    svc.seal(v1["volume_id"])

    v2 = svc.create(pid, VolumeCreate(number=2, title="v2"))
    assert v2["status"] == "active"
    assert v2["number"] == 2


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_empty_project_returns_empty_list(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = VolumeService(db_path)
    assert svc.list(pid) == []


def test_list_includes_chapter_count_and_orders_by_number(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate

    v1 = svc.create(pid, VolumeCreate(number=1, title="v1"))
    svc.seal(v1["volume_id"])
    v2 = svc.create(pid, VolumeCreate(number=2, title="v2"))
    svc.seal(v2["volume_id"])
    v3 = svc.create(pid, VolumeCreate(number=3, title="v3"))

    # 给 v3 挂两章
    c1 = _make_chapter(db_path, pid, 1, "ch1")
    c2 = _make_chapter(db_path, pid, 2, "ch2")
    svc.assign_chapter(v3["volume_id"], c1)
    svc.assign_chapter(v3["volume_id"], c2)

    items = svc.list(pid)
    assert [it["number"] for it in items] == [1, 2, 3]
    assert [it["chapter_count"] for it in items] == [0, 0, 2]
    assert items[2]["status"] == "active"


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_returns_none_when_missing(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    svc = VolumeService(db_path)
    assert svc.get("vol_nonexistent") is None


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_title(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate, VolumeUpdate

    v1 = svc.create(pid, VolumeCreate(number=1, title="原始标题"))
    updated = svc.update(v1["volume_id"], VolumeUpdate(title="新标题"))
    assert updated["title"] == "新标题"
    assert updated["status"] == "active"


def test_update_sealed_to_active_blocked(tmp_path: Path):
    """sealed → active 反向跳变 → VolumeSealedError。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate, VolumeUpdate

    v1 = svc.create(pid, VolumeCreate(number=1, title="v1"))
    svc.seal(v1["volume_id"])
    with pytest.raises(VolumeSealedError):
        svc.update(v1["volume_id"], VolumeUpdate(status="active"))


def test_update_missing_volume_returns_none(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeUpdate

    assert svc.update("vol_nonexistent", VolumeUpdate(title="x")) is None


# ---------------------------------------------------------------------------
# seal
# ---------------------------------------------------------------------------


def test_seal_freezes_terminal_snapshot_from_story_states(tmp_path: Path):
    """seal → status='sealed' + terminal_snapshot_json = story_states 最新快照 JSON。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate

    v1 = svc.create(pid, VolumeCreate(number=1, title="v1"))

    # 写入一个 story_states 快照
    snap = {"characters": [{"id": "char_alice", "name": "Alice"}], "events": []}
    _seed_snapshot(db_path, pid, snap)

    sealed = svc.seal(v1["volume_id"])
    assert sealed["status"] == "sealed"
    # terminal_snapshot_json 应解析回原 dict
    assert sealed["terminal_snapshot_json"] == snap


def test_seal_no_snapshot_stores_empty_object(tmp_path: Path):
    """无 story_states 快照 → terminal_snapshot_json 存 {}。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate

    v1 = svc.create(pid, VolumeCreate(number=1, title="v1"))
    sealed = svc.seal(v1["volume_id"])
    assert sealed["status"] == "sealed"
    assert sealed["terminal_snapshot_json"] == {}


def test_seal_missing_volume_returns_none(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    svc = VolumeService(db_path)
    assert svc.seal("vol_nonexistent") is None


def test_seal_already_sealed_raises_conflict(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate

    v1 = svc.create(pid, VolumeCreate(number=1, title="v1"))
    svc.seal(v1["volume_id"])
    with pytest.raises(VolumeConflictError, match="already sealed"):
        svc.seal(v1["volume_id"])


# ---------------------------------------------------------------------------
# assign_chapter
# ---------------------------------------------------------------------------


def test_assign_chapter_happy_path(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate

    v1 = svc.create(pid, VolumeCreate(number=1, title="v1"))
    c1 = _make_chapter(db_path, pid, 1, "ch1")

    out = svc.assign_chapter(v1["volume_id"], c1)
    assert out["volume_id"] == v1["volume_id"]

    # DB 侧 chapter.volume_id 已更新
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT volume_id FROM chapters WHERE chapter_id = ?", (c1,)
        ).fetchone()
    finally:
        conn.close()
    assert row["volume_id"] == v1["volume_id"]


def test_assign_chapter_sealed_volume_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate

    v1 = svc.create(pid, VolumeCreate(number=1, title="v1"))
    svc.seal(v1["volume_id"])
    c1 = _make_chapter(db_path, pid, 1, "ch1")
    with pytest.raises(VolumeSealedError):
        svc.assign_chapter(v1["volume_id"], c1)


def test_assign_chapter_missing_chapter_raises(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate

    v1 = svc.create(pid, VolumeCreate(number=1, title="v1"))
    with pytest.raises(VolumeValidationError, match="not found"):
        svc.assign_chapter(v1["volume_id"], "ch_nonexistent")


def test_assign_chapter_cross_project_raises(tmp_path: Path):
    """chapter 与 volume 不属同 project → ValidationError。"""
    db_path = _fresh_db(tmp_path)
    pid_a = _make_project(db_path, "A")
    pid_b = _make_project(db_path, "B")
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate

    v1 = svc.create(pid_a, VolumeCreate(number=1, title="v1"))
    # 把 chapter 写到 pid_b
    c1 = _make_chapter(db_path, pid_b, 1, "ch-in-B")
    with pytest.raises(VolumeValidationError, match="project"):
        svc.assign_chapter(v1["volume_id"], c1)


def test_assign_chapter_missing_volume_raises_not_found(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    svc = VolumeService(db_path)
    with pytest.raises(VolumeNotFoundError):
        svc.assign_chapter("vol_nonexistent", "ch_nonexistent")


def test_assign_chapter_idempotent_on_same_volume(tmp_path: Path):
    """chapter 已在该卷上 → 视为成功（幂等）。"""
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate

    v1 = svc.create(pid, VolumeCreate(number=1, title="v1"))
    c1 = _make_chapter(db_path, pid, 1, "ch1")

    svc.assign_chapter(v1["volume_id"], c1)
    # 再次 assign 同一 chapter → 不报错
    out = svc.assign_chapter(v1["volume_id"], c1)
    assert out["volume_id"] == v1["volume_id"]


def test_assign_chapter_overwrites_previous_volume(tmp_path: Path):
    """同章只能属一卷——assign 到新卷时直接覆盖旧 volume_id。

    受 active 单例约束，必须先 seal v1 才能建 v2；测试覆盖语义即可。
    """
    db_path = _fresh_db(tmp_path)
    pid = _make_project(db_path)
    svc = VolumeService(db_path)
    from packages.domain.volume.models import VolumeCreate

    v1 = svc.create(pid, VolumeCreate(number=1, title="v1"))
    c1 = _make_chapter(db_path, pid, 1, "ch1")
    svc.assign_chapter(v1["volume_id"], c1)

    # seal v1 后建 v2，把同一章挂到 v2（覆盖 v1 归属）
    svc.seal(v1["volume_id"])
    v2 = svc.create(pid, VolumeCreate(number=2, title="v2"))
    svc.assign_chapter(v2["volume_id"], c1)

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT volume_id FROM chapters WHERE chapter_id = ?", (c1,)
        ).fetchone()
    finally:
        conn.close()
    assert row["volume_id"] == v2["volume_id"]
