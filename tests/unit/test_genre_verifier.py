"""题材核销层 v1（``packages.core.genre.verifier``）单测。

覆盖：
1. 三态——配比命中（无 issue）/ 配比偏差（GENRE-RATIO-DEVIATION）/ 无 scene_type 跳过；
2. 字数带一致性（GENRE-WORD-BAND-DEVIATION；在带内不报）；
3. 红线文本命中（GENRE-REDLINE-HIT）；
4. 无绑定 → 整段跳过（bound=False / checked=False / 零 issue）；
5. informational 语义：issue 恒 warning、rule_id 前缀 GENRE-、非 blocking；
   **blocking delta fixture 下 quality 门禁判定零影响**（BLOCKING_RULES 不含 GENRE-）。

数据来源：chapters / drafts / workflow_runs.checkpoint_json（scene_plan + length_report）。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.genre import RATIO_DEVIATION_THRESHOLD, verify_chapter
from packages.core.genre.verifier import (
    RULE_RATIO_DEVIATION,
    RULE_REDLINE_HIT,
    RULE_WORD_BAND_DEVIATION,
    GenreIssue,
)
from packages.core.ids import new_id, now_iso
from packages.core.quality import QualityEngine
from packages.core.quality.aggregate import compute_overall
from packages.core.quality.issues import BLOCKING_RULES, is_blocking_issue, make_issue
from packages.core.quality.models import Issue, QualityContext

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"

_PACK_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.0.0",
    "ratio_declarations": {"action": 0.7, "transition": 0.3},
    "pacing": {
        "chapter_word_band": {"low": 2400, "high": 3600},
        "redlines": ["压抑段≤2章"],
    },
}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return db_path


def _insert_project(db_path: Path, name: str = "核销项目") -> str:
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


def _insert_chapter(
    db_path: Path, pid: str, *, plan_json: dict | None = None, number: int = 1,
) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, who_knows, created_at, updated_at) "
            "VALUES (?, ?, ?, 'C', ?, 'DRAFTED', 'VISIBLE', NULL, ?, ?)",
            (cid, pid, number, json.dumps(plan_json or {}, ensure_ascii=False), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_draft(db_path: Path, cid: str, content: str, version: int = 1) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO drafts (draft_id, chapter_id, version, content, created_by, "
            "created_at) VALUES (?, ?, ?, ?, 'test:writer:v1', ?)",
            (new_id("drf"), cid, version, content, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_write_run(
    db_path: Path,
    cid: str,
    *,
    scene_plan: dict | None = None,
    length_report: dict | None = None,
) -> str:
    """插一条 chapter-write run（checkpoint_json 带 scene_plan / length_report）。"""
    run_id = new_id("wfr")
    workflow_id = new_id("wf")
    now = now_iso()
    checkpoint = {}
    if scene_plan is not None:
        checkpoint["scene_plan"] = scene_plan
    if length_report is not None:
        checkpoint["length_report"] = length_report
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO workflows (workflow_id, name, version, definition_json, "
            "created_at, updated_at) VALUES (?, 'chapter-write', 'v1', '{}', ?, ?)",
            (workflow_id, now, now),
        )
        conn.execute(
            "INSERT INTO workflow_runs (run_id, workflow_id, chapter_id, status, "
            "current_node, checkpoint_json, started_at, ended_at) "
            "VALUES (?, ?, ?, 'COMPLETED', 'save_draft', ?, ?, ?)",
            (run_id, workflow_id, cid, json.dumps(checkpoint, ensure_ascii=False), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def _create_and_bind_pack(db_path: Path, pid: str, payload: dict | None = None) -> None:
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO genre_packs (pack_id, name, genre_tag, version, payload_json, "
            "source_path, created_at, updated_at) VALUES ('gp_kc', '男主快穿', '快穿', 3, "
            "?, NULL, ?, ?)",
            (json.dumps(_PACK_PAYLOAD if payload is None else payload, ensure_ascii=False), now, now),
        )
        conn.execute(
            "UPDATE projects SET genre_pack_id = 'gp_kc' WHERE project_id = ?", (pid,)
        )
        conn.commit()
    finally:
        conn.close()


def _scene_plan(pairs: list[tuple[str, int]]) -> dict:
    return {
        "scenes": [
            {"scene_id": f"s{i}", "purpose": "p", "scene_type": t, "target_words": w}
            for i, (t, w) in enumerate(pairs, start=1)
        ]
    }


# ---------------------------------------------------------------------------
# 1) 配比三态
# ---------------------------------------------------------------------------


def test_ratio_within_tolerance_no_issue(tmp_path: Path):
    """动作/过场 = 70/30（与声明一致）→ 无 issue，checked=True。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_and_bind_pack(db_path, pid)
    _insert_write_run(db_path, cid, scene_plan=_scene_plan([("action", 2100), ("transition", 900)]))

    result = verify_chapter(db_path, cid)
    assert result.bound is True
    assert result.pack_id == "gp_kc"
    assert result.pack_version == 3
    assert result.checked is True
    assert result.issues == []
    rc = result.ratio_check
    assert rc["checked"] is True
    assert set(rc["declared"]) == {"action", "transition"}
    assert rc["declared"]["action"] == 0.7
    assert abs(rc["declared"]["transition"] - 0.3) < 1e-9
    assert rc["total_deviation"] == 0.0
    assert rc["deviation_exceeded"] is False
    assert rc["typed_scene_count"] == 2


def test_ratio_deviation_emits_issue(tmp_path: Path):
    """动作/过场 = 50/50（声明 70/30）→ 总偏离 0.4 > 0.1 → GENRE-RATIO-DEVIATION。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_and_bind_pack(db_path, pid)
    _insert_write_run(
        db_path, cid,
        scene_plan=_scene_plan([("action", 1500), ("transition", 1500)]),
        length_report={"visible_chars": 3000},
    )

    result = verify_chapter(db_path, cid)
    ratio_issues = [i for i in result.issues if i.rule_id == RULE_RATIO_DEVIATION]
    assert len(ratio_issues) == 1
    issue = ratio_issues[0]
    assert issue.severity == "warning"
    assert issue.category == "payoff"
    assert issue.rule_id.startswith("GENRE-")
    assert "action" in issue.message and "transition" in issue.message
    assert result.ratio_check["total_deviation"] > RATIO_DEVIATION_THRESHOLD
    assert result.ratio_check["deviation_exceeded"] is True


def test_ratio_skipped_without_scene_type(tmp_path: Path):
    """scene_plan 无任何 scene_type → 跳过（no_scene_type），零 issue。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_and_bind_pack(db_path, pid)
    _insert_write_run(
        db_path, cid,
        scene_plan={"scenes": [{"scene_id": "s1", "purpose": "p", "target_words": 3000}]},
    )

    result = verify_chapter(db_path, cid)
    assert result.issues == []
    assert result.ratio_check["checked"] is False
    assert result.ratio_check["reason"] == "no_scene_type"
    assert "no_scene_type" in result.skipped


def test_ratio_skipped_without_declaration(tmp_path: Path):
    """题材包未声明 ratio_declarations → 跳过（reason=ratio_declarations_not_declared）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_and_bind_pack(
        db_path, pid, payload={"schema_version": "genre-pack.v1.0.0"},
    )
    _insert_write_run(db_path, cid, scene_plan=_scene_plan([("action", 3000)]))

    result = verify_chapter(db_path, cid)
    assert result.issues == []
    assert result.ratio_check["reason"] == "ratio_declarations_not_declared"


def test_ratio_declaration_normalized(tmp_path: Path):
    """声明份额不为 1（如 0.7 / 0.7）→ 归一后按相对比例核（0.5 / 0.5 命中）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_and_bind_pack(
        db_path, pid,
        payload={
            "schema_version": "genre-pack.v1.0.0",
            "ratio_declarations": {"action": 0.7, "transition": 0.7},
        },
    )
    _insert_write_run(db_path, cid, scene_plan=_scene_plan([("action", 1500), ("transition", 1500)]))

    result = verify_chapter(db_path, cid)
    assert result.ratio_check["declared"] == {"action": 0.5, "transition": 0.5}
    assert result.issues == []


# ---------------------------------------------------------------------------
# 2) 字数带 + 红线
# ---------------------------------------------------------------------------


def test_word_band_deviation_issue(tmp_path: Path):
    """实际 2000 字 < 声明带下限 2400 → GENRE-WORD-BAND-DEVIATION。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_and_bind_pack(db_path, pid)
    _insert_write_run(db_path, cid, length_report={"visible_chars": 2000})
    _insert_draft(db_path, cid, "字" * 2000)

    result = verify_chapter(db_path, cid)
    issues = [i for i in result.issues if i.rule_id == RULE_WORD_BAND_DEVIATION]
    assert len(issues) == 1
    assert issues[0].category == "pacing"
    assert "2400" in issues[0].message and "3600" in issues[0].message
    wb = result.redline_check["word_band"]
    assert wb["checked"] is True and wb["within_band"] is False
    assert wb["source"] == "length_report"


def test_word_band_in_band_no_issue(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_and_bind_pack(db_path, pid)
    _insert_write_run(db_path, cid, length_report={"visible_chars": 3000})

    result = verify_chapter(db_path, cid)
    assert [i for i in result.issues if i.rule_id == RULE_WORD_BAND_DEVIATION] == []
    assert result.redline_check["word_band"]["within_band"] is True


def test_word_band_falls_back_to_draft_when_no_length_report(tmp_path: Path):
    """无 length_report → 用最新草稿的 visible_chars 兜底（source='draft'）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_and_bind_pack(db_path, pid)
    _insert_draft(db_path, cid, "字 字\n字" * 1000)  # 可见字符 3000

    result = verify_chapter(db_path, cid)
    wb = result.redline_check["word_band"]
    assert wb["source"] == "draft"
    assert wb["word_count"] == 3000
    assert wb["within_band"] is True


def test_redline_hit_from_chapter_deviations(tmp_path: Path):
    """红线原文出现在 plan_json.deviations → GENRE-REDLINE-HIT。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid,
        plan_json={"deviations": ["本章 压抑段≤2章 超限，人工确认"]},
    )
    _create_and_bind_pack(db_path, pid)
    _insert_write_run(db_path, cid, length_report={"visible_chars": 3000})

    result = verify_chapter(db_path, cid)
    hits = [i for i in result.issues if i.rule_id == RULE_REDLINE_HIT]
    assert len(hits) == 1
    assert hits[0].severity == "warning"
    assert result.redline_check["redlines"]["hits"] == ["压抑段≤2章"]


def test_redline_no_hit_when_absent(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_and_bind_pack(db_path, pid)
    _insert_write_run(db_path, cid, length_report={"visible_chars": 3000})

    result = verify_chapter(db_path, cid)
    assert [i for i in result.issues if i.rule_id == RULE_REDLINE_HIT] == []
    assert result.redline_check["redlines"]["checked"] is True
    assert result.redline_check["redlines"]["hits"] == []


# ---------------------------------------------------------------------------
# 3) 未绑定 / 显式 pack / 容错
# ---------------------------------------------------------------------------


def test_unbound_project_skips_everything(tmp_path: Path):
    """未绑定题材包 → bound=False / checked=False / 零 issue（零行为变化）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "正文" * 100)

    result = verify_chapter(db_path, cid)
    assert result.bound is False
    assert result.checked is False
    assert result.issues == []
    assert result.pack_id is None
    assert "no_binding" in result.skipped
    assert result.to_dict()["issue_count"] == 0


def test_explicit_pack_argument_used_over_binding(tmp_path: Path):
    """显式传 pack（未绑定项目）→ bound=True，按该 pack 核销。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _insert_draft(db_path, cid, "字" * 2000)

    result = verify_chapter(
        db_path, cid,
        {"pack_id": "gp_inline", "version": 1, "payload": _PACK_PAYLOAD},
    )
    assert result.bound is True
    assert result.pack_id == "gp_inline"
    assert result.pack_version == 1
    assert [i.rule_id for i in result.issues] == [RULE_WORD_BAND_DEVIATION]


def test_missing_chapter_does_not_raise(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    result = verify_chapter(db_path, "ch_missing", _PACK_PAYLOAD)
    assert result.bound is False
    assert result.issues == []
    assert "chapter_not_found" in result.skipped


def test_result_to_dict_shape(tmp_path: Path):
    """to_dict 的段结构（review_report.genre_check 落点契约）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_and_bind_pack(db_path, pid)
    _insert_write_run(
        db_path, cid,
        scene_plan=_scene_plan([("action", 3000)]),
        length_report={"visible_chars": 3000},
    )

    data = verify_chapter(db_path, cid).to_dict()
    assert set(data) == {
        "bound", "checked", "pack_id", "pack_version", "issues", "issue_count",
        "rule_ids", "ratio_check", "redline_check", "skipped", "error",
    }
    assert data["rule_ids"] == [RULE_RATIO_DEVIATION]
    issue_dict = data["issues"][0]
    assert set(issue_dict) == {
        "rule_id", "severity", "category", "message", "location", "suggestion",
        "evidence_refs",
    }


# ---------------------------------------------------------------------------
# 4) informational 语义（不阻断）+ blocking delta fixture 零影响
# ---------------------------------------------------------------------------


def test_genre_issues_are_never_blocking():
    """GENRE- 规则恒 warning、category 合法、不在 BLOCKING_RULES；质量门禁不受影响。"""
    assert all(not rid.startswith("GENRE-") for rid in BLOCKING_RULES)
    issue = GenreIssue(
        rule_id=RULE_RATIO_DEVIATION,
        message="m",
        category="payoff",
        location="ch_x",
    )
    assert issue.severity == "warning"
    assert is_blocking_issue(issue) is False
    # 与 quality Issue 同形的 dict（informational 通道）可直接构造 quality Issue
    quality_issue = Issue(**issue.to_quality_issue(), judge_trace=None)
    assert quality_issue.rule_id == RULE_RATIO_DEVIATION
    assert is_blocking_issue(quality_issue) is False


def _blocking_delta_ctx(chapter_id: str = "ch_001") -> QualityContext:
    """blocking delta fixture：delta 缺 schema_version → SCHEMA_VALIDATION_FAILED。

    与 ``tests/unit/quality/test_engine.py::test_engine_error_zero`` 同构造口径。
    """
    return QualityContext(
        chapter_id=chapter_id,
        chapter_number=2,
        draft="正文" * 60,
        plan={"key_beats": []},
        snapshot_pre={"state_version": 1, "characters": [], "events": {}, "hooks": [],
                      "world": {"world_rules": []}},
        delta={"chapter_id": chapter_id, "delta_id": "d1"},
        payoff_history=[1, 1],
        ai_chars=60,
        human_chars=40,
    )


def test_enforce_gate_unaffected_by_genre_issues():
    """blocking delta fixture：加入 GENRE- issues 后，门禁阻断判定与 overall 零变化。"""
    engine = QualityEngine()
    report = engine.evaluate(_blocking_delta_ctx())

    blocking_before = sorted(i.rule_id for i in report.issues if is_blocking_issue(i))
    assert blocking_before == ["SCHEMA_VALIDATION_FAILED"], "fixture 应恰好命中一条阻断规则"
    assert report.overall == 0

    genre_quality_issues = [
        Issue(
            **GenreIssue(
                rule_id=RULE_RATIO_DEVIATION, message="配比偏差", category="payoff",
                location="ch_001",
            ).to_quality_issue(),
            judge_trace=None,
        ),
        Issue(
            **GenreIssue(
                rule_id=RULE_WORD_BAND_DEVIATION, message="字数带越界", category="pacing",
                location="ch_001",
            ).to_quality_issue(),
            judge_trace=None,
        ),
    ]
    with_genre = [*report.issues, *genre_quality_issues]
    blocking_after = sorted(i.rule_id for i in with_genre if is_blocking_issue(i))
    assert blocking_after == blocking_before, "GENRE- issues 不得进入阻断集合"

    subscores = {
        name: getattr(report, name)
        for name in ("plot", "character", "continuity", "style", "pacing",
                     "foreshadowing", "ai_trace")
    }
    overall_after, _ = compute_overall(dict(subscores), list(with_genre))
    assert overall_after == report.overall == 0


def test_genre_issues_keep_partial_score_without_blocking_rule():
    """无阻断规则时：genre issues 不把 overall 归零（informational 语义）。"""
    engine = QualityEngine()
    ctx = _blocking_delta_ctx()
    # 换成合法 delta（无 schema 错误）→ 无 blocking issue
    ctx.delta = {
        "delta_id": "d_ok", "delta_version": 1, "chapter_id": "ch_001",
        "workflow_run_id": "wfr_t", "previous_state_version": 1,
        "created_by": "observer:test", "created_at": "2026-09-13T00:00:00Z",
        "schema_version": "state-delta-v0",
        "character_changes": [], "world_changes": [], "relationship_changes": [],
        "new_events": [], "resolved_hooks": [], "new_hooks": [], "debt_changes": [],
    }
    report = engine.evaluate(ctx)
    assert not [i for i in report.issues if is_blocking_issue(i)]

    genre_issue = make_issue(
        severity="warning", category="payoff", rule_id=RULE_RATIO_DEVIATION,
        message="配比偏差", chapter_id="ch_001",
    )
    subscores = {
        name: getattr(report, name)
        for name in ("plot", "character", "continuity", "style", "pacing",
                     "foreshadowing", "ai_trace")
    }
    overall, _ = compute_overall(dict(subscores), [*report.issues, genre_issue])
    assert overall == report.overall
    assert overall > 0
