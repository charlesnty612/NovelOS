"""V3.9 批次 4.3：QualityReport.draft_version 落库与序列化兼容。

覆盖：
- 模型字段可空：不带 draft_version 的 report 构造后为 None（存量调用方兼容）；
- ``QualityService.save_report`` 在 report.draft_version 为 None 时补当前最新 draft 版本；
- 显式传入的 draft_version 不被覆盖；
- 该章无 draft 时保持 None（列可空）；
- ``latest_report`` / ``list_reports`` 读回 draft_version（SELECT * + 列透传）。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.models import QualityReport
from packages.core.quality.service import QualityService


def _make_db(tmp_path: Path) -> str:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return str(settings.db_path)


def _make_project(db_path: str) -> str:
    from packages.domain.project.models import ProjectCreate
    from packages.domain.project.service import ProjectService

    return ProjectService(db_path).create(ProjectCreate(name="draft_version 项目"))[
        "project_id"
    ]


def _make_chapter(db_path: str, pid: str) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO chapters
                (chapter_id, project_id, number, title, plan_json,
                 status, visibility, who_knows, created_at, updated_at)
            VALUES (?, ?, 1, 'C1', '{}', 'DRAFTED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_draft(db_path: str, cid: str, version: int) -> str:
    draft_id = new_id("dr")
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO drafts
                (draft_id, chapter_id, version, content, created_by,
                 prompt_version, model_id, created_at)
            VALUES (?, ?, ?, ?, 'writer:v1', NULL, NULL, ?)
            """,
            (draft_id, cid, version, f"正文 v{version}", now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return draft_id


# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------


def test_model_draft_version_defaults_to_none():
    """存量构造路径（不带 draft_version）→ None，不抛校验错。"""
    report = QualityReport(overall=80, chapter_id="ch_x")
    assert report.draft_version is None
    dumped = report.model_dump(by_alias=True)
    assert dumped["draft_version"] is None


def test_model_accepts_explicit_draft_version():
    report = QualityReport(overall=80, chapter_id="ch_x", draft_version=7)
    assert report.draft_version == 7
    assert report.model_dump(by_alias=True)["draft_version"] == 7


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------


def test_save_report_fills_latest_draft_version(tmp_path: Path):
    """未显式给 draft_version → 落库前取当前最新 draft 的 version（v2）。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    _insert_draft(db_path, cid, 1)
    _insert_draft(db_path, cid, 2)

    report = QualityReport(overall=72, chapter_id=cid, report_id=new_id("qr"))
    QualityService(db_path).save_report(report, project_id=pid, chapter_id=cid)

    # 回写 report 对象（调用方 dump 出去即带该版本）
    assert report.draft_version == 2

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT draft_version FROM quality_reports WHERE report_id = ?",
            (report.report_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row["draft_version"] == 2


def test_save_report_keeps_explicit_draft_version(tmp_path: Path):
    """显式 draft_version（如审校指定版本）优先于「最新 draft」补全。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    _insert_draft(db_path, cid, 1)
    _insert_draft(db_path, cid, 2)

    report = QualityReport(
        overall=72, chapter_id=cid, report_id=new_id("qr"), draft_version=1
    )
    QualityService(db_path).save_report(report, project_id=pid, chapter_id=cid)

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT draft_version FROM quality_reports WHERE report_id = ?",
            (report.report_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row["draft_version"] == 1


def test_save_report_without_drafts_keeps_null(tmp_path: Path):
    """该章尚无 draft → draft_version 落 NULL（列可空，兼容存量行）。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)

    report = QualityReport(overall=60, chapter_id=cid, report_id=new_id("qr"))
    QualityService(db_path).save_report(report, project_id=pid, chapter_id=cid)

    assert report.draft_version is None
    row = QualityService(db_path).latest_report(cid)
    assert row is not None
    assert row["draft_version"] is None


def test_latest_and_list_reports_read_back_draft_version(tmp_path: Path):
    """读路径（SELECT * → _row_to_dict）透传 draft_version。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    _insert_draft(db_path, cid, 3)

    svc = QualityService(db_path)
    svc.save_report(
        QualityReport(overall=88, chapter_id=cid, report_id=new_id("qr")),
        project_id=pid,
        chapter_id=cid,
    )

    latest = svc.latest_report(cid)
    assert latest is not None
    assert latest["draft_version"] == 3

    rows = svc.list_reports(pid)
    assert len(rows) == 1
    assert rows[0]["draft_version"] == 3
