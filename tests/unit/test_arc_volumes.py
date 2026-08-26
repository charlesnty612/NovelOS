"""build_arc_view volumes 扩展字段单测（V3.4 多卷与规模——组织层）。

覆盖：
- 空项目：volumes = []；chapter 元素 volume_id/volume_number 为 null；
- 多卷场景：顶层 volumes 小节按 number ASC 排序、含 chapter_count 聚合；
- chapter 元素正确回填 volume_id / volume_number（LEFT JOIN 命中）；
- 无卷的 chapter → volume_id=null / volume_number=null（LEFT JOIN 不命中）；
- volumes 表缺失（0015 未跑）→ 容错：volumes=[]，chapter 元素 volume 字段
  仍为 null（不阻断 arc 装配）。
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
from packages.domain.project.service import ProjectService  # noqa: E402
from packages.domain.volume import VolumeService  # noqa: E402
from packages.domain.volume.models import VolumeCreate  # noqa: E402

MIGRATIONS_DIR = ROOT / "database" / "migrations"


def _make_settings(tmp_path: Path) -> Settings:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings


def _make_project(db_path: str, name: str = "vol-arc") -> str:
    from packages.domain.project.models import ProjectCreate
    return ProjectService(db_path).create(ProjectCreate(name=name))["project_id"]


def _make_chapter(
    db_path: str,
    pid: str,
    number: int,
    title: str = "t",
    plan: dict | None = None,
) -> str:
    cid = new_id("ch")
    now = now_iso()
    import json as _json
    plan_str = _json.dumps(plan or {}, ensure_ascii=False)
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json,
                 status, visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'PLANNED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, pid, number, title, plan_str, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _make_volume(db_path: str, pid: str, number: int, title: str = "v") -> str:
    return VolumeService(db_path).create(
        pid, VolumeCreate(number=number, title=title),
    )["volume_id"]


# ---------------------------------------------------------------------------
# 默认值与空项目
# ---------------------------------------------------------------------------


def test_arc_view_volumes_default_empty(tmp_path: Path):
    """空项目：volumes = []；chapter 元素 volume_id/volume_number 为 null。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    # 无任何 chapter 与 volume
    result = build_arc_view(db_path, pid)
    assert "volumes" in result
    assert result["volumes"] == []


def test_arc_view_chapter_without_volume_has_null_volume_fields(tmp_path: Path):
    """有 chapter 但未挂卷 → volume_id=null, volume_number=null（LEFT JOIN 未命中）。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)

    _make_chapter(db_path, pid, 1, "无卷章")
    assert len(result := build_arc_view(db_path, pid)["chapters"]) == 1
    ch = result[0]
    assert ch["volume_id"] is None
    assert ch["volume_number"] is None


# ---------------------------------------------------------------------------
# 多卷分组 + chapter 归属
# ---------------------------------------------------------------------------


def test_arc_view_volumes_includes_chapter_count_and_orders_by_number(tmp_path: Path):
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path, "多卷分组")

    # 建两卷
    v1 = _make_volume(db_path, pid, 1, "卷一")
    VolumeService(db_path).seal(v1)
    v2 = _make_volume(db_path, pid, 2, "卷二")

    # 5 章：1-2 章挂卷一（虽然已 sealed 不让挂——只测卷二挂后列表聚合）
    c3 = _make_chapter(db_path, pid, 3, "ch3")
    c4 = _make_chapter(db_path, pid, 4, "ch4")
    c5 = _make_chapter(db_path, pid, 5, "ch5")
    _make_chapter(db_path, pid, 6, "ch-unbound")

    VolumeService(db_path).assign_chapter(v2, c3)
    VolumeService(db_path).assign_chapter(v2, c4)
    VolumeService(db_path).assign_chapter(v2, c5)

    result = build_arc_view(db_path, pid)

    # volumes 顶层小节：按 number ASC 排序
    vols = result["volumes"]
    assert [v["number"] for v in vols] == [1, 2]
    assert vols[0]["status"] == "sealed"
    assert vols[1]["status"] == "active"
    # chapter_count：卷一=0、卷二=3
    assert vols[0]["chapter_count"] == 0
    assert vols[1]["chapter_count"] == 3
    assert vols[0]["volume_id"] == v1
    assert vols[1]["volume_id"] == v2

    # chapter 元素：3-5 章挂卷二；6 章未挂卷
    chapters = result["chapters"]
    by_no = {ch["number"]: ch for ch in chapters}
    assert by_no[3]["volume_id"] == v2
    assert by_no[3]["volume_number"] == 2
    assert by_no[4]["volume_id"] == v2
    assert by_no[5]["volume_id"] == v2
    # 未挂卷
    assert by_no[6]["volume_id"] is None
    assert by_no[6]["volume_number"] is None


def test_arc_view_volumes_chapter_count_zero_when_no_assignment(tmp_path: Path):
    """建了卷但没挂章 → chapter_count = 0。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    v = _make_volume(db_path, pid, 1, "空卷")
    _make_chapter(db_path, pid, 1, "ch1")  # 不挂卷
    result = build_arc_view(db_path, pid)
    assert result["volumes"] == [{"volume_id": v, "number": 1, "title": "空卷",
                                   "status": "active", "chapter_count": 0}]


# ---------------------------------------------------------------------------
# 容错：volumes 表缺失（0015 未跑）→ 不阻断 arc 装配
# ---------------------------------------------------------------------------


def test_arc_view_volumes_safe_when_table_missing(tmp_path: Path, monkeypatch):
    """模拟「0015 未跑」场景（volumes 表缺失）→ 容错：volumes=[]，chapter.volume=null。"""
    settings = _make_settings(tmp_path)
    db_path = str(settings.db_path)
    pid = _make_project(db_path)
    _make_chapter(db_path, pid, 1, "ch1")

    # 直接 DROP volumes 表模拟 0015 未跑
    conn = get_connection(db_path)
    try:
        conn.execute("DROP TABLE volumes")
        conn.commit()
    finally:
        conn.close()

    result = build_arc_view(db_path, pid)
    assert result["volumes"] == []
    ch = result["chapters"][0]
    assert ch["volume_id"] is None
    assert ch["volume_number"] is None
