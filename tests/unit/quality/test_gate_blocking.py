"""V3.9 批次 3.1：quality_gate 阻断只看 blocking error（真实 DB + 节点接线）。

覆盖：
- informational error（severity=error 但不在阻断白名单）⇒ run 不阻断、gate_blocked 不落；
- blocking error ⇒ 抛 ValueError（enforce）+ 落 ``plan_json.gate_blocked``；
- report 模式：blocking error 也不阻断（原有语义回归）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id, now_iso
from packages.core.quality.issues import make_issue
from packages.core.quality.models import QualityReport
from packages.workflows.chapter_commit import gate as gate_mod

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> str:
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return str(settings.db_path)


def _make_project(db_path: str) -> str:
    from packages.domain.project.models import ProjectCreate
    from packages.domain.project.service import ProjectService

    return ProjectService(db_path).create(ProjectCreate(name="gate blocking 项目"))[
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
            VALUES (?, ?, 1, 'C1', '{}', 'REVIEWED', 'VISIBLE', NULL, ?, ?)
            """,
            (cid, pid, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _report_with(cid: str, issue) -> QualityReport:
    return QualityReport(
        overall=80,
        plot=80,
        character=80,
        continuity=80,
        style=80,
        pacing=80,
        foreshadowing=80,
        ai_trace=80,
        issues=[issue],
        chapter_id=cid,
        report_id=new_id("qr"),
    )


def _install_fake_engine(monkeypatch: pytest.MonkeyPatch, report: QualityReport) -> None:
    class _FakeEngine:
        def evaluate(self, ctx):  # noqa: ANN001
            return report

    monkeypatch.setattr(gate_mod, "QualityEngine", lambda: _FakeEngine())


def _ctx(db_path: str, pid: str, cid: str, mode: str) -> dict:
    return {
        "db_path": db_path,
        "chapter_id": cid,
        "project_id": pid,
        "delta": {},
        "snapshot_pre": {},
        "quality_gate_mode": mode,
    }


def _plan_json(db_path: str, cid: str) -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT plan_json FROM chapters WHERE chapter_id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row["plan_json"] or "{}")


# ---------------------------------------------------------------------------
# 节点行为
# ---------------------------------------------------------------------------


def test_gate_ignores_informational_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """白名单外 error（informational）不阻断 run，也不落 gate_blocked。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    informational = make_issue(
        severity="error",  # type: ignore[arg-type]
        category="payoff",  # type: ignore[arg-type]
        rule_id="RULE_H3_FILLER_3CH",
        message="连续 3+ 章 payoff 为 0",
    )
    _install_fake_engine(monkeypatch, _report_with(cid, informational))

    out = gate_mod._quality_gate_node(_ctx(db_path, pid, cid, "enforce"))  # noqa: SLF001

    assert out["quality_error_count"] == 1
    assert out["quality_blocking_count"] == 0
    assert out["quality_gate_mode"] == "enforce"
    # run 未被阻断 → 不落 gate_blocked
    assert "gate_blocked" not in _plan_json(db_path, cid)
    # report 仍落库（informational error 也要可查）
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT issues_json FROM quality_reports WHERE chapter_id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    assert "RULE_H3_FILLER_3CH" in row["issues_json"]


def test_gate_blocks_on_blocking_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """白名单内 error（blocking）⇒ enforce 抛 ValueError + 落 gate_blocked。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    blocking = make_issue(
        severity="error",  # type: ignore[arg-type]
        category="character_contradiction",  # type: ignore[arg-type]
        rule_id="RULE_CHAR_DEAD_ACTIVE",
        message="角色已死亡仍被设置活动字段",
    )
    _install_fake_engine(monkeypatch, _report_with(cid, blocking))

    with pytest.raises(ValueError) as exc:
        gate_mod._quality_gate_node(_ctx(db_path, pid, cid, "enforce"))  # noqa: SLF001

    assert "quality gate blocked" in str(exc.value)
    assert "RULE_CHAR_DEAD_ACTIVE" in str(exc.value)
    plan = _plan_json(db_path, cid)
    assert plan.get("gate_blocked", {}).get("rule_ids") == ["RULE_CHAR_DEAD_ACTIVE"]


def test_gate_report_mode_never_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """report 模式：即使是 blocking error 也不阻断（原有语义回归）。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    blocking = make_issue(
        severity="error",  # type: ignore[arg-type]
        category="schema_validity",  # type: ignore[arg-type]
        rule_id="SCHEMA_VALIDATION_FAILED",
        message="delta schema 校验失败",
    )
    _install_fake_engine(monkeypatch, _report_with(cid, blocking))

    out = gate_mod._quality_gate_node(_ctx(db_path, pid, cid, "report"))  # noqa: SLF001
    assert out["quality_blocking_count"] == 1
    assert "gate_blocked" not in _plan_json(db_path, cid)
