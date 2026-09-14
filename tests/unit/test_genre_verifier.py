"""题材核销层 v2（``packages.core.genre.verifier``）单测。

覆盖：
1. 三态——配比命中（无 issue）/ 配比偏差（GENRE-RATIO-DEVIATION）/ 无 scene_type 跳过；
2. 字数带一致性（GENRE-WORD-BAND-DEVIATION；在带内不报）；
3. 红线文本命中（GENRE-REDLINE-HIT）；
4. 无绑定 → 整段跳过（bound=False / checked=False / 零 issue）；
5. informational 语义：issue 恒 warning、rule_id 前缀 GENRE-、非 blocking；
   **blocking delta fixture 下 quality 门禁判定零影响**（BLOCKING_RULES 不含 GENRE-）；
6. **v2 口径修订**（本轮）：弧级累计配比（章级降级为明细、不产配比 issue）、
   untyped scene 显式化（GENRE-SCENE-UNTYPED）、声明/观测键不对称的残差桶归并、
   弧级结论的样本就绪判据（ARC_MIN_TYPED_SCENES / 弧内末章）、红线语料扩面
   （整章正文可命中）、settlement 待覆盖登记。

数据来源：chapters（含 volume_id / number）/ drafts / workflow_runs.checkpoint_json
（scene_plan + length_report）。
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.core.db import apply_migrations, get_connection
from packages.core.genre import RATIO_DEVIATION_THRESHOLD, verify_chapter
from packages.core.genre.verifier import (
    ARC_MIN_TYPED_SCENES,
    RULE_RATIO_DEVIATION,
    RULE_REDLINE_HIT,
    RULE_SCENE_UNTYPED,
    RULE_WORD_BAND_DEVIATION,
    SETTLEMENT_PENDING_NOTE,
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
    volume_id: str | None = None,
) -> str:
    cid = new_id("ch")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json, "
            "status, visibility, who_knows, created_at, updated_at, volume_id) "
            "VALUES (?, ?, ?, 'C', ?, 'DRAFTED', 'VISIBLE', NULL, ?, ?, ?)",
            (
                cid, pid, number, json.dumps(plan_json or {}, ensure_ascii=False),
                now, now, volume_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return cid


def _insert_volume(db_path: Path, pid: str, *, number: int = 1) -> str:
    """插一卷并返回 volume_id（弧级核销的聚合作用域）。"""
    vid = new_id("vol")
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO volumes (volume_id, project_id, number, title, status, "
            "created_at, updated_at) VALUES (?, ?, ?, 'V', 'active', ?, ?)",
            (vid, pid, number, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return vid


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
    """动作/过场 = 50/50（声明 70/30）→ 总偏离 0.4 > 0.1 → GENRE-RATIO-DEVIATION（弧级）。

    v2：单章即「项目无卷章节池」里唯一一章 → 视为弧内末章，弧级口径出结论；
    章级同数字但只记明细（``issue_emitted=False``）。
    """
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
    assert "弧级配比总偏离" in issue.message
    assert result.ratio_check["total_deviation"] > RATIO_DEVIATION_THRESHOLD
    assert result.ratio_check["deviation_exceeded"] is True
    assert result.ratio_check["degraded"] is True
    assert result.arc_check["total_deviation"] == result.ratio_check["total_deviation"]
    assert result.arc_check["is_arc_end"] is True
    assert result.arc_check["sample_ready"] is False
    assert "样本偏薄" in issue.message


def test_ratio_skipped_without_scene_type(tmp_path: Path):
    """scene_plan 无任何 scene_type → 配比跳过（no_scene_type）。

    v2 语义变更：不再零 issue——untyped scene 显式化为 ``GENRE-SCENE-UNTYPED``
    （v1 只写 ``skipped``，实测书1 ch3/ch17 整章 untyped 时无任何可读信号）。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_and_bind_pack(db_path, pid)
    _insert_write_run(
        db_path, cid,
        scene_plan={"scenes": [{"scene_id": "s1", "purpose": "p", "target_words": 3000}]},
    )

    result = verify_chapter(db_path, cid)
    assert result.rule_ids == [RULE_SCENE_UNTYPED]
    assert "1 个 scene 未标注 scene_type" in result.issues[0].message
    assert result.issues[0].severity == "warning"
    assert result.issues[0].category == "payoff"
    assert result.ratio_check["checked"] is False
    assert result.ratio_check["reason"] == "no_scene_type"
    assert result.ratio_check["untyped_scene_count"] == 1
    assert "no_scene_type" in result.skipped
    # 弧级同样无 typed scene → 不下结论（arc_no_scene_type）
    assert result.arc_check["checked"] is False
    assert result.arc_check["reason"] == "arc_no_scene_type"
    assert "arc_ratio:arc_no_scene_type" in result.skipped


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
    """to_dict 的段结构（review_report.genre_check 落点契约；v2 增 arc_check 段）。"""
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
        "rule_ids", "ratio_check", "arc_check", "redline_check", "skipped", "error",
    }
    # 单章即弧内末章（弧作用域退化到「同项目无卷章节池」）→ 配比偏离仍由弧级产出。
    assert data["rule_ids"] == [RULE_RATIO_DEVIATION]
    assert data["ratio_check"]["issue_emitted"] is False
    assert data["arc_check"]["scope"] == "arc"
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


# ---------------------------------------------------------------------------
# 5) v2 口径修订：弧级累计配比 + untyped 显式化 + 键不对称残差桶 + 红线语料扩面
# ---------------------------------------------------------------------------

# 带残差键（catch-all）的声明：action / other 各半，用于残差桶与键不对称用例。
_RESIDUAL_PACK: dict = {
    "schema_version": "genre-pack.v1.0.0",
    "ratio_declarations": {"action": 0.5, "other": 0.5},
}


def _bind_pack_via_new_id(db_path: Path, pid: str, payload: dict, pack_id: str) -> None:
    """绑定一个指定 pack_id 的题材包（多包用例避免 pk 冲突）。"""
    now = now_iso()
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO genre_packs (pack_id, name, genre_tag, version, payload_json, "
            "source_path, created_at, updated_at) VALUES (?, 'T', 't', 1, ?, NULL, ?, ?)",
            (pack_id, json.dumps(payload, ensure_ascii=False), now, now),
        )
        conn.execute(
            "UPDATE projects SET genre_pack_id = ? WHERE project_id = ?", (pack_id, pid)
        )
        conn.commit()
    finally:
        conn.close()


def test_scene_untyped_partial_emits_issue(tmp_path: Path):
    """部分 untyped：typed 照常核配比，untyped 同时产 GENRE-SCENE-UNTYPED（不再静默）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_and_bind_pack(db_path, pid)
    _insert_write_run(
        db_path, cid,
        scene_plan={
            "scenes": [
                {"scene_id": "s1", "scene_type": "action", "target_words": 99},
                {"scene_id": "s2", "scene_type": "transition", "target_words": 1},
                {"scene_id": "s3", "purpose": "p", "target_words": 1000},
                {"scene_id": "s4", "purpose": "p", "target_words": 1000},
            ]
        },
    )

    result = verify_chapter(db_path, cid)
    # typed 侧 99/1 与声明 70/30 比 → 偏离超阈值：章级只记明细、弧级（单章即末章）出结论
    assert result.rule_ids == [RULE_SCENE_UNTYPED, RULE_RATIO_DEVIATION]
    assert result.ratio_check["untyped_scene_count"] == 2
    assert result.ratio_check["typed_scene_count"] == 2
    assert result.ratio_check["checked"] is True
    assert result.ratio_check["issue_emitted"] is False
    assert result.ratio_check["deviation_exceeded"] is True
    untyped_issue = result.issues[0]
    assert untyped_issue.rule_id == RULE_SCENE_UNTYPED
    assert "2 个 scene 未标注 scene_type" in untyped_issue.message


def test_arc_pooling_suppresses_chapter_level_noise(tmp_path: Path):
    """弧级累计（弧级阈值）：单章极端偏离、弧内池化后命中 → 零 issue。

    突变判据：撤掉弧级累计 / 就绪判据（回到章级产 issue）→ 本用例必红。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    vid = _insert_volume(db_path, pid)
    cid1 = _insert_chapter(db_path, pid, number=1, volume_id=vid)
    cid2 = _insert_chapter(db_path, pid, number=2, volume_id=vid)
    _bind_pack_via_new_id(
        db_path, pid,
        {
            "schema_version": "genre-pack.v1.0.0",
            "ratio_declarations": {"action": 0.5, "transition": 0.5},
        },
        "gp_balanced",
    )
    _insert_write_run(db_path, cid1, scene_plan=_scene_plan([("action", 1000), ("action", 1000)]))
    _insert_write_run(
        db_path, cid2, scene_plan=_scene_plan([("transition", 1000), ("transition", 1000)]),
    )

    # 章 1：本章 100% action（章级偏离 1.0），但弧未就绪（非末章 + 样本 2 < 20）→ 不下结论
    first = verify_chapter(db_path, cid1)
    assert first.rule_ids == []
    assert first.ratio_check["deviation_exceeded"] is True  # 章级事实仍在（只记不报）
    assert first.arc_check["checked"] is False
    assert first.arc_check["reason"] == "arc_not_ready"
    assert first.arc_check["is_arc_end"] is False
    assert first.arc_check["typed_scene_count"] == 2
    assert first.arc_check["volume_chapter_count"] == 2
    assert "arc_ratio:arc_not_ready" in first.skipped

    # 章 2：弧内末章 → 累计池化 50/50 命中声明 → 零 issue
    last = verify_chapter(db_path, cid2)
    assert last.rule_ids == []
    assert last.arc_check["checked"] is True
    assert last.arc_check["is_arc_end"] is True
    assert last.arc_check["total_deviation"] == 0.0
    assert last.arc_check["typed_scene_count"] == 4


def test_arc_sample_ready_concludes_mid_arc(tmp_path: Path):
    """样本判据：弧中途累计 typed scene 达 ARC_MIN_TYPED_SCENES → 立即出弧级结论。

    构造 6 章 × 4 个 action（声明 action/other 各半）：
    - 第 4 章（累计 16 < 20、非末章）→ arc_not_ready，零 issue；
    - 第 5 章（累计恰 20、非末章）→ sample_ready → 弧级 issue（偏离 1.0）。
    突变判据：去掉 ``sample_ready`` 这一就绪路径 → 第 5 章零 issue，本用例必红。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    vid = _insert_volume(db_path, pid)
    chapters = [
        _insert_chapter(db_path, pid, number=n, volume_id=vid) for n in range(1, 7)
    ]
    _bind_pack_via_new_id(db_path, pid, _RESIDUAL_PACK, "gp_arc")
    for cid in chapters:
        _insert_write_run(
            db_path, cid,
            scene_plan=_scene_plan([("action", 500)] * 4),
        )

    mid = verify_chapter(db_path, chapters[3])  # 第 4 章
    assert mid.rule_ids == []
    assert mid.arc_check["checked"] is False
    assert mid.arc_check["typed_scene_count"] == 16
    assert mid.arc_check["is_arc_end"] is False

    ready = verify_chapter(db_path, chapters[4])  # 第 5 章：恰 20 个 typed scene
    assert ready.arc_check["typed_scene_count"] == ARC_MIN_TYPED_SCENES
    assert ready.arc_check["sample_ready"] is True
    assert ready.arc_check["is_arc_end"] is False
    assert ready.arc_check["checked"] is True
    assert ready.rule_ids == [RULE_RATIO_DEVIATION]
    issue = ready.issues[0]
    assert "弧级配比总偏离" in issue.message
    assert "样本充分" in issue.message
    assert f"arc_typed_scenes:{ARC_MIN_TYPED_SCENES}" in issue.evidence_refs
    assert f"arc_scope:{vid}" in issue.evidence_refs

    # 末章：样本充分 + 弧内末章，结论位置不变（同一弧口径）
    last = verify_chapter(db_path, chapters[5])  # 第 6 章
    assert last.arc_check["is_arc_end"] is True
    assert last.rule_ids == [RULE_RATIO_DEVIATION]


def test_arc_volumeless_chapters_pool_by_project(tmp_path: Path):
    """无卷章节退化口径：同项目无卷章节聚成一个弧（不与单章同弧）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    # 空卷（仅为隔离：本用例两章都不挂卷）
    _insert_volume(db_path, pid)
    cid1 = _insert_chapter(db_path, pid, number=1)
    cid2 = _insert_chapter(db_path, pid, number=2)
    _create_and_bind_pack(db_path, pid)
    _insert_write_run(db_path, cid1, scene_plan=_scene_plan([("transition", 1000)]))
    _insert_write_run(
        db_path, cid2,
        scene_plan=_scene_plan([("transition", 1000), ("action", 1)]),
    )

    result = verify_chapter(db_path, cid2)
    assert result.arc_check["volume_id"] is None
    assert result.arc_check["scope_key"] == f"project:{pid}"
    assert result.arc_check["chapter_count"] == 2
    assert result.arc_check["typed_scene_count"] == 3
    # 池化后 action 占比极小（1/2001）→ 偏离超阈值：声明 70/30
    assert result.arc_check["deviation_exceeded"] is True


def test_arc_residual_bucket_absorbs_undeclared_types(tmp_path: Path):
    """键不对称：观测侧声明外 scene_type 并入声明 catch-all（other）残差桶。

    声明 action 0.5 / other 0.5，实测只有一个声明外维度（b：0.5）——语义上等于
    把 ``other`` 预算花在了写手自造维度上，弧级偏离应为 0。
    突变判据：回到 v1 的 union 口径（b 与 other 各自对 0 比）→ 总偏离 1.0 → 本用例必红。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _bind_pack_via_new_id(db_path, pid, _RESIDUAL_PACK, "gp_other")
    _insert_write_run(db_path, cid, scene_plan=_scene_plan([("action", 1000), ("b", 1000)]))

    result = verify_chapter(db_path, cid)
    assert result.rule_ids == []
    assert result.arc_check["total_deviation"] == 0.0
    residual = result.arc_check["residual"]
    assert residual["key"] == "other"
    assert residual["declared"] == 0.5
    assert residual["observed"] == 0.5
    assert residual["observed_only_keys"] == ["b"]
    assert result.arc_check["deviations"] == {"action": 0.0, "other": 0.0}


def test_arc_declared_only_key_is_a_real_gap(tmp_path: Path):
    """键不对称的另一半：声明了但观测缺席的键**不进**残差桶（按 0 比对 = 真缺口）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _bind_pack_via_new_id(db_path, pid, _RESIDUAL_PACK, "gp_gap")
    _insert_write_run(db_path, cid, scene_plan=_scene_plan([("action", 1000)]))

    result = verify_chapter(db_path, cid)
    assert result.rule_ids == [RULE_RATIO_DEVIATION]
    assert result.arc_check["deviations"]["other"] == -0.5
    assert result.arc_check["residual"]["declared_only_keys"] == []
    assert result.arc_check["total_deviation"] == 1.0


def test_arc_check_registers_settlement_pending(tmp_path: Path):
    """settlement 弧末核销无数据通路 → arc_check.pending 诚实登记（不做假核销）。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _create_and_bind_pack(db_path, pid)
    _insert_write_run(db_path, cid, scene_plan=_scene_plan([("action", 3000)]))

    result = verify_chapter(db_path, cid)
    assert result.arc_check["pending"] == [SETTLEMENT_PENDING_NOTE]
    assert "settlement" in result.arc_check["pending"][0]
    assert "待 scene 标注覆盖" in result.arc_check["pending"][0]


def test_redline_matches_chapter_body_corpus(tmp_path: Path):
    """红线语料扩面：红线原句只出现在**正文**（不在 deviations / length_report）也命中。

    突变判据：把语料退回 v1 的「deviations + length_report」→ 无命中，本用例必红。
    """
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _bind_pack_via_new_id(
        db_path, pid,
        {
            "schema_version": "genre-pack.v1.0.0",
            "pacing": {"redlines": ["压抑段≤2章"]},
        },
        "gp_redline",
    )
    _insert_draft(db_path, cid, "开篇平静。" + "正文" * 40 + "此处 压抑段≤2章 已超限，人工确认。")

    result = verify_chapter(db_path, cid)
    redlines = result.redline_check["redlines"]
    assert redlines["checked"] is True
    assert redlines["hits"] == ["压抑段≤2章"]
    assert redlines["corpus_sources"] == ["draft"]
    assert redlines["corpus_chars"] >= 80
    assert [i.rule_id for i in result.issues] == [RULE_REDLINE_HIT]


def test_redline_corpus_counts_plan_and_draft(tmp_path: Path):
    """语料构成与体量回报：正文 + 计划关键段 + length_report 全量入语料。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(
        db_path, pid,
        plan_json={
            "chapter_goal": "目标句",
            "deviations": ["偏差注记"],
            "key_beats": [{"beat_id": "b1", "purpose": "节拍目的句"}],
        },
    )
    _bind_pack_via_new_id(
        db_path, pid,
        {"schema_version": "genre-pack.v1.0.0", "pacing": {"redlines": ["不存在于任何语料的红线"]}},
        "gp_corpus",
    )
    _insert_draft(db_path, cid, "正文" * 500)
    _insert_write_run(db_path, cid, length_report={"visible_chars": 1000})

    result = verify_chapter(db_path, cid)
    redlines = result.redline_check["redlines"]
    assert redlines["hits"] == []
    assert redlines["corpus_sources"] == ["draft", "plan_json", "length_report"]
    # 语料已远超 v1 的 ~140 字量级（正文 1000 字已在语料内）
    assert redlines["corpus_chars"] > 1000


def test_arc_declaration_absent_skips_arc_without_query(tmp_path: Path):
    """未声明配比 → 弧级不读弧内章节（零额外查询）、零 issue、原因可追溯。"""
    db_path = _fresh_db(tmp_path)
    pid = _insert_project(db_path)
    cid = _insert_chapter(db_path, pid)
    _bind_pack_via_new_id(
        db_path, pid, {"schema_version": "genre-pack.v1.0.0"}, "gp_noratio",
    )

    result = verify_chapter(db_path, cid)
    assert result.arc_check["checked"] is False
    assert result.arc_check["reason"] == "ratio_declarations_not_declared"
    assert result.arc_check["chapter_count"] == 0
    assert "arc_ratio:ratio_declarations_not_declared" in result.skipped
    assert result.issues == []
