"""build_arc_view reveal_policies 扩展字段单测（V3.3 P0-2 知识权限补全）。

覆盖：
- 空项目：reveal_policies = {planned:0, revealed:0, cancelled:0, overdue:[]}；
- 三种 status 计数 + overdue 列表（status=planned + reveal_by_chapter <= 当前最大章号）；
- reveal_by_chapter 为 NULL → 不进 overdue；
- revealed / cancelled → 不进 overdue；
- 当前最大章号为 NULL（无章节）→ overdue 列表为空；
- overdue 列表只含必要字段（policy_id/target_kind/target_id/reveal_by_chapter/audience）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.core.arc.service import build_arc_view  # noqa: E402
from packages.core.config import Settings  # noqa: E402
from packages.core.db import apply_migrations, get_connection  # noqa: E402
from packages.core.ids import new_id, now_iso  # noqa: E402
from packages.domain.knowledge import RevealPolicyService  # noqa: E402
from packages.domain.project.service import ProjectService  # noqa: E402

MIGRATIONS_DIR = ROOT / "database" / "migrations"


def _make_settings(tmp_path: Path) -> Settings:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings


def _make_project(db_path: str, name: str = "rp-arc") -> str:
    return ProjectService(db_path).create(
        type("P", (), {"name": name, "premise": None, "genre": None,
                        "target_words": None, "foreshadow_overdue_chapters": None})(),
    )["project_id"]


def _make_chapter(db_path: str, pid: str, number: int) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json,
                 status, visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, 't', '{}', 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, pid, number, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _make_character(db_path: str, pid: str, cid: str) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO characters (character_id, project_id, name, role, core_json,
                                    visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, 'supporting', '{}', 'PUBLIC', NULL, ?, ?)
            """,
            (cid, pid, cid, now, now),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# reveal_policies 段在 arc 视图中
# ---------------------------------------------------------------------------


def test_arc_view_reveal_policies_default_empty(tmp_path: Path):
    settings = _make_settings(tmp_path)
    pid = _make_project(str(settings.db_path))
    result = build_arc_view(settings.db_path, pid)
    assert "reveal_policies" in result
    rp = result["reveal_policies"]
    assert rp == {"planned": 0, "revealed": 0, "cancelled": 0, "overdue": []}


def test_arc_view_reveal_policies_counts_three_statuses(tmp_path: Path):
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    # 需要至少一个 entity 才能创建 policy
    _make_character(db_path, pid, "char_alice")
    _make_character(db_path, pid, "char_bob")
    _make_character(db_path, pid, "char_carol")
    _make_character(db_path, pid, "char_dave")
    svc = RevealPolicyService(db_path)
    svc.create(project_id=pid, target_kind="character", target_id="char_alice")
    svc.create(
        project_id=pid, target_kind="character", target_id="char_bob",
        status="revealed", revealed_chapter=3,
    )
    svc.create(
        project_id=pid, target_kind="character", target_id="char_carol",
        status="cancelled",
    )
    svc.create(
        project_id=pid, target_kind="character", target_id="char_dave",
        reveal_by_chapter=20,
    )
    result = build_arc_view(settings.db_path, pid)
    rp = result["reveal_policies"]
    assert rp["planned"] == 2     # alice + dave
    assert rp["revealed"] == 1    # bob
    assert rp["cancelled"] == 1   # carol
    # 无章节 → overdue 空（dave 的 reveal_by_chapter=20 不会触发）
    assert rp["overdue"] == []


def test_arc_view_reveal_policies_overdue_appears_when_max_chapter_passes(tmp_path: Path):
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_alice")
    _make_character(db_path, pid, "char_bob")
    _make_character(db_path, pid, "char_carol")
    svc = RevealPolicyService(db_path)
    # alice：reveal_by_chapter=5
    svc.create(
        project_id=pid, target_kind="character", target_id="char_alice",
        reveal_by_chapter=5,
    )
    # bob：reveal_by_chapter=10
    svc.create(
        project_id=pid, target_kind="character", target_id="char_bob",
        reveal_by_chapter=10,
    )
    # carol：reveal_by_chapter=NULL → 不进 overdue
    svc.create(project_id=pid, target_kind="character", target_id="char_carol")

    # 当前最大章号 = 7 → alice 已到期（5<=7），bob 未到期（10>7）
    _make_chapter(db_path, pid, 1)
    _make_chapter(db_path, pid, 2)
    _make_chapter(db_path, pid, 3)
    _make_chapter(db_path, pid, 4)
    _make_chapter(db_path, pid, 5)
    _make_chapter(db_path, pid, 6)
    _make_chapter(db_path, pid, 7)

    result = build_arc_view(settings.db_path, pid)
    overdue = result["reveal_policies"]["overdue"]
    assert len(overdue) == 1
    item = overdue[0]
    assert item["policy_id"].startswith("rp_")
    assert item["target_kind"] == "character"
    assert item["target_id"] == "char_alice"
    assert item["reveal_by_chapter"] == 5
    assert item["audience"] == "reader"
    # 必要字段校验（防字段遗漏）
    assert set(item.keys()) == {
        "policy_id", "target_kind", "target_id",
        "reveal_by_chapter", "audience",
    }


def test_arc_view_reveal_policies_no_chapters_no_overdue(tmp_path: Path):
    """无章节时 current_max_chapter_no 为 None → overdue 为空（即使 reveal_by_chapter=1）。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_alice")
    svc = RevealPolicyService(db_path)
    svc.create(
        project_id=pid, target_kind="character", target_id="char_alice",
        reveal_by_chapter=1,
    )
    result = build_arc_view(settings.db_path, pid)
    assert result["reveal_policies"]["overdue"] == []


def test_arc_view_reveal_policies_revealed_not_in_overdue(tmp_path: Path):
    """status=revealed 即使 reveal_by_chapter 早于当前最大章号，也不进 overdue。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_alice")
    svc = RevealPolicyService(db_path)
    svc.create(
        project_id=pid, target_kind="character", target_id="char_alice",
        reveal_by_chapter=5, status="revealed", revealed_chapter=4,
    )
    _make_chapter(db_path, pid, 1)
    _make_chapter(db_path, pid, 2)
    _make_chapter(db_path, pid, 3)
    _make_chapter(db_path, pid, 4)
    _make_chapter(db_path, pid, 5)
    result = build_arc_view(settings.db_path, pid)
    assert result["reveal_policies"]["overdue"] == []
    assert result["reveal_policies"]["revealed"] == 1


def test_arc_view_reveal_policies_cancelled_not_in_overdue(tmp_path: Path):
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    _make_character(db_path, pid, "char_alice")
    svc = RevealPolicyService(db_path)
    svc.create(
        project_id=pid, target_kind="character", target_id="char_alice",
        reveal_by_chapter=5, status="cancelled",
    )
    _make_chapter(db_path, pid, 1)
    _make_chapter(db_path, pid, 10)
    result = build_arc_view(settings.db_path, pid)
    assert result["reveal_policies"]["overdue"] == []
    assert result["reveal_policies"]["cancelled"] == 1
