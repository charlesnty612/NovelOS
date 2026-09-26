"""V3.9 批次 3.1：quality_gate 阻断只看 blocking error（真实 DB + 节点接线）。

覆盖：
- informational error（severity=error 但不在阻断白名单）⇒ run 不阻断、gate_blocked 不落；
- blocking error ⇒ 抛 ValueError（enforce）+ 落 ``plan_json.gate_blocked``；
- report 模式：blocking error 也不阻断（原有语义回归）；
- P0-1（2026-09-18）：confirm 档（``issues.CONFIRM_RULES``）在 enforce 下必须显式接受
  才放行——无声明 / 部分覆盖 / reason 为空 ⇒ 阻断（消息带规则清单 + 证据摘录）；
  全量声明 + reason ⇒ 放行且声明留痕（``_meta.gate_accepted_override``）。
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

# 事故量级正文（trigram 实测 0.3348）由两档阈值的判别测试提供，避免两处各写一份。
from tests.unit.quality.test_trigram_tiers import incident_prose as _incident_prose

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


def _ctx(db_path: str, pid: str, cid: str, mode: str, **extra) -> dict:
    ctx = {
        "db_path": db_path,
        "chapter_id": cid,
        "project_id": pid,
        "delta": {},
        "snapshot_pre": {},
        "quality_gate_mode": mode,
    }
    ctx.update(extra)
    return ctx


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


# ---------------------------------------------------------------------------
# P0-1（2026-09-18）：confirm 档 —— 不许静默通过
# ---------------------------------------------------------------------------


def _trigram_issue():
    """confirm 档命中样本（severity 仍是 warning——后果与严重度正交）。

    2026-09-18 起 ``RULE_STYLE_REPETITION_TRIGRAM`` 不在 ``CONFIRM_RULES`` 内：
    超 confirm 阈值的单条由产出侧（``scoring.score_style``）显式上修
    ``gate="confirm"``，本 helper 复刻该形态（本例 30.37% = 事故实测值）。
    """
    return make_issue(
        severity="warning",
        category="style",
        rule_id="RULE_STYLE_REPETITION_TRIGRAM",
        message="trigram 重复率 30.37% > 确认阈值 25%",
        evidence_refs=["破屋的×12", "灯亮着的×9"],
        gate="confirm",
    )


def _latest_report_meta(db_path: str, cid: str) -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT scores_json FROM quality_reports WHERE chapter_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (cid,),
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row["scores_json"])["_meta"]


def test_confirm_issue_blocks_without_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """confirm 命中 + enforce + 无 gate_override ⇒ 阻断，消息含 rule_id 与证据摘录。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    _install_fake_engine(monkeypatch, _report_with(cid, _trigram_issue()))

    with pytest.raises(ValueError) as exc:
        gate_mod._quality_gate_node(_ctx(db_path, pid, cid, "enforce"))  # noqa: SLF001
    err = str(exc.value)

    assert "quality gate blocked" in err
    assert "RULE_STYLE_REPETITION_TRIGRAM" in err
    assert "gate=confirm" in err
    assert "破屋的×12" in err, "阻断消息必须带证据摘录（不能只给规则名）"
    assert "gate_override" in err, "必须告知放行方式"

    # 复用既有落点：revision_note + gate_blocked（gate 标为 confirm 以便前端区分）
    plan = _plan_json(db_path, cid)
    assert "RULE_STYLE_REPETITION_TRIGRAM" in plan.get("revision_note", "")
    assert "破屋的×12" in plan.get("revision_note", "")
    marker = plan.get("gate_blocked") or {}
    assert marker.get("mode") == "enforce"
    assert marker.get("gate") == "confirm"
    assert marker.get("rule_ids") == ["RULE_STYLE_REPETITION_TRIGRAM"]
    # confirm 不归零 overall（fake 报告 overall=80 原样保留）
    assert _latest_report_meta(db_path, cid)["gate_summary"] == {
        "blocking_rule_ids": [],
        "confirm_rule_ids": ["RULE_STYLE_REPETITION_TRIGRAM"],
        "blocking_count": 0,
        "confirm_count": 1,
    }


def test_confirm_partial_override_still_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """部分覆盖（未涵盖实际命中的 confirm 规则）⇒ 仍然阻断，消息点名缺失项。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    report = QualityReport(
        overall=80, plot=80, character=80, continuity=80, style=80,
        pacing=80, foreshadowing=80, ai_trace=80,
        issues=[_trigram_issue()],
        chapter_id=cid,
        report_id=new_id("qr"),
    )
    _install_fake_engine(monkeypatch, report)

    ctx = _ctx(
        db_path, pid, cid, "enforce",
        gate_override={"rule_ids": ["AI-BEAT-REPEAT"], "reason": "我认了节拍重复"},
    )
    with pytest.raises(ValueError) as exc:
        gate_mod._quality_gate_node(ctx)  # noqa: SLF001
    err = str(exc.value)
    assert "quality gate blocked" in err
    assert "RULE_STYLE_REPETITION_TRIGRAM" in err
    assert "未覆盖" in err
    assert (ctx["quality_gate"] or {}).get("gate_accepted_override") is None
    assert "gate_blocked" in _plan_json(db_path, cid)


def test_confirm_empty_reason_still_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """覆盖齐全但 reason 为空 ⇒ 仍是「没声明」，照样阻断。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    _install_fake_engine(monkeypatch, _report_with(cid, _trigram_issue()))

    ctx = _ctx(
        db_path, pid, cid, "enforce",
        gate_override={"rule_ids": ["RULE_STYLE_REPETITION_TRIGRAM"], "reason": "   "},
    )
    with pytest.raises(ValueError) as exc:
        gate_mod._quality_gate_node(ctx)  # noqa: SLF001
    assert "RULE_STYLE_REPETITION_TRIGRAM" in str(exc.value)
    assert "reason" in str(exc.value)


def test_confirm_full_override_passes_and_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """全量声明 + 非空 reason ⇒ 放行，且声明在节点输出与报告 ``_meta`` 双留痕。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    _install_fake_engine(monkeypatch, _report_with(cid, _trigram_issue()))

    override = {
        "rule_ids": ["RULE_STYLE_REPETITION_TRIGRAM"],
        "reason": "本章刻意的复沓，已人工逐段确认",
    }
    out = gate_mod._quality_gate_node(  # noqa: SLF001
        _ctx(db_path, pid, cid, "enforce", gate_override=override)
    )

    assert out["quality_gate_mode"] == "enforce"
    assert out["quality_gate_summary"]["confirm_rule_ids"] == [
        "RULE_STYLE_REPETITION_TRIGRAM"
    ]
    recorded = out["quality_gate_accepted_override"]
    assert recorded["reason"] == override["reason"]
    assert recorded["rule_ids"] == ["RULE_STYLE_REPETITION_TRIGRAM"]
    assert recorded["accepted_at"]
    # 放行 ⇒ 不落 gate_blocked 标记（残留标记被清除）
    assert "gate_blocked" not in _plan_json(db_path, cid)
    # 报告留痕（API / 前端可直接读）
    meta = _latest_report_meta(db_path, cid)
    assert meta["gate_accepted_override"]["reason"] == override["reason"]
    # overall 未被 confirm 归零（fake 报告 overall=80）
    assert meta["gate_summary"]["confirm_count"] == 1


def test_incident_magnitude_prose_blocks_then_passes_with_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """事故量级（trigram 0.3348）走**真实产出路径**：无声明阻断 / 有声明放行。

    与上面几条的区别：issue 不是手搓的——由 ``scoring.score_style`` 真算出来，
    因此同时钉住「阈值判定 → 单条上修 → ``issue_gate`` → 门禁」整条链。
    """
    from packages.core.quality.scoring import score_style

    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)

    _score, issues = score_style(_incident_prose())
    trigram = [i for i in issues if i.rule_id == "RULE_STYLE_REPETITION_TRIGRAM"]
    assert len(trigram) == 1, issues
    assert trigram[0].severity == "warning", "confirm 档不引入 error（后果在 gate 轴）"
    assert trigram[0].gate == "confirm", "事故量级必须要求显式接受"

    def _report() -> QualityReport:
        return QualityReport(
            overall=80, plot=80, character=80, continuity=80, style=80,
            pacing=80, foreshadowing=80, ai_trace=80,
            issues=list(issues),
            chapter_id=cid,
            report_id=new_id("qr"),
        )

    _install_fake_engine(monkeypatch, _report())

    with pytest.raises(ValueError) as exc:
        gate_mod._quality_gate_node(_ctx(db_path, pid, cid, "enforce"))  # noqa: SLF001
    err = str(exc.value)
    assert "gate=confirm" in err, err
    assert "RULE_STYLE_REPETITION_TRIGRAM" in err, err
    assert "×" in err, "阻断消息要带重复片段证据（作者要看见自己在接受什么）"
    assert _plan_json(db_path, cid)["gate_blocked"]["gate"] == "confirm"

    _install_fake_engine(monkeypatch, _report())
    out = gate_mod._quality_gate_node(  # noqa: SLF001
        _ctx(
            db_path, pid, cid, "enforce",
            gate_override={
                "rule_ids": ["RULE_STYLE_REPETITION_TRIGRAM"],
                "reason": "刻意的复沓，已逐段确认",
            },
        )
    )
    assert out["quality_gate_accepted_override"]["reason"] == "刻意的复沓，已逐段确认"
    assert "gate_blocked" not in _plan_json(db_path, cid)


def test_confirm_report_mode_does_not_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """report 模式：confirm 命中也不阻断、不落标记（与 blocking 同口径）。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    _install_fake_engine(monkeypatch, _report_with(cid, _trigram_issue()))

    out = gate_mod._quality_gate_node(_ctx(db_path, pid, cid, "report"))  # noqa: SLF001
    assert out["quality_gate_summary"]["confirm_count"] == 1
    assert out["quality_gate_accepted_override"] is None
    assert "gate_blocked" not in _plan_json(db_path, cid)


def test_block_still_wins_over_confirm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """block 与 confirm 同时命中：走既有 block 路径（提示为「阻断·改稿建议」，非待确认）。

    2026-09-21 检修 M1 扩展：block 优先不可协商，但 confirm 侧**不得被吞**——
    ``gate_blocked.rule_ids`` 与 error 串都要同时带上 confirm 的 rule_id / override_error /
    evidence，否则作者修掉硬伤后再提交才第一次见到 confirm，且不知道要准备什么。
    """
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    report = QualityReport(
        overall=80, plot=80, character=80, continuity=80, style=80,
        pacing=80, foreshadowing=80, ai_trace=80,
        issues=[
            _trigram_issue(),
            make_issue(
                severity="error",  # type: ignore[arg-type]
                category="character_contradiction",  # type: ignore[arg-type]
                rule_id="RULE_CHAR_DEAD_ACTIVE",
                message="角色已死亡仍被设置活动字段",
            ),
        ],
        chapter_id=cid,
        report_id=new_id("qr"),
    )
    _install_fake_engine(monkeypatch, report)

    ctx = _ctx(
        db_path, pid, cid, "enforce",
        gate_override={
            "rule_ids": ["RULE_STYLE_REPETITION_TRIGRAM"],
            "reason": "确认过",
        },
    )
    with pytest.raises(ValueError) as exc:
        gate_mod._quality_gate_node(ctx)  # noqa: SLF001
    err = str(exc.value)
    assert "RULE_CHAR_DEAD_ACTIVE" in err

    # M1：confirm 侧与 blocking 侧同屏可见（撤掉合并这两条断言必红）
    assert "gate=block+confirm" in err, err
    assert "confirm_rule_ids=" in err, err
    assert "RULE_STYLE_REPETITION_TRIGRAM" in err, err
    assert "override_error=" in err, err
    assert "evidence=" in err, err

    marker = (_plan_json(db_path, cid).get("gate_blocked") or {})
    assert marker.get("gate") == "block+confirm"
    # blocking 在前（硬停优先），confirm id 追加在后
    assert marker.get("rule_ids") == [
        "RULE_CHAR_DEAD_ACTIVE",
        "RULE_STYLE_REPETITION_TRIGRAM",
    ]
    # note 同时含 blocking 建议与 confirm 的接受指引
    plan = _plan_json(db_path, cid)
    assert "质量门禁阻断·改稿建议" in plan["revision_note"], plan["revision_note"]
    assert "另有必须显式接受的命中" in plan["revision_note"], plan["revision_note"]
    assert "gate_override" in plan["revision_note"], plan["revision_note"]


def test_block_only_still_uses_plain_block_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """仅有 blocking（无 confirm）时保持旧消息形态（无 confirm 段）——不无脑加字段。"""
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    report = QualityReport(
        overall=80, plot=80, character=80, continuity=80, style=80,
        pacing=80, foreshadowing=80, ai_trace=80,
        issues=[
            make_issue(
                severity="error",  # type: ignore[arg-type]
                category="character_contradiction",  # type: ignore[arg-type]
                rule_id="RULE_CHAR_DEAD_ACTIVE",
                message="角色已死亡仍被设置活动字段",
            ),
        ],
        chapter_id=cid,
        report_id=new_id("qr"),
    )
    _install_fake_engine(monkeypatch, report)

    with pytest.raises(ValueError) as exc:
        gate_mod._quality_gate_node(_ctx(db_path, pid, cid, "enforce"))  # noqa: SLF001
    err = str(exc.value)
    assert "RULE_CHAR_DEAD_ACTIVE" in err
    assert "gate=" not in err, err
    marker = (_plan_json(db_path, cid).get("gate_blocked") or {})
    assert marker.get("gate") == "block"
    assert marker.get("rule_ids") == ["RULE_CHAR_DEAD_ACTIVE"]


def test_override_without_confirm_hit_leaves_no_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """无 confirm 命中时带 gate_override：正常通过，且**不留**接受记录。

    理由：``_meta.gate_accepted_override`` 只应记录「确实批准了什么」；
    否则审计面会出现「用 gate_override 通过了，但什么都没被覆盖」的误导记录。
    """
    db_path = _make_db(tmp_path)
    pid = _make_project(db_path)
    cid = _make_chapter(db_path, pid)
    clean = make_issue(
        severity="warning",  # type: ignore[arg-type]
        category="pacing",  # type: ignore[arg-type]
        rule_id="RULE_PACING_FLAT",
        message="段落长度极差 < 5%",
    )
    _install_fake_engine(monkeypatch, _report_with(cid, clean))

    out = gate_mod._quality_gate_node(  # noqa: SLF001
        _ctx(
            db_path, pid, cid, "enforce",
            gate_override={"rule_ids": ["RULE_STYLE_REPETITION_TRIGRAM"], "reason": "保险起见"},
        )
    )
    assert out["quality_gate_accepted_override"] is None
    assert "gate_accepted_override" not in _latest_report_meta(db_path, cid)
