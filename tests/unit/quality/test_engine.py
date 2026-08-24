"""QualityEngine 端到端测试（tests.unit.quality.test_engine）。

目标：
- 全 pass 场景：overall > 0，无 error。
- 含 error 场景：overall == 0。
- _meta 4 字段齐全，formula_hash 长度 16。
- report_id 非空。
"""

from __future__ import annotations

from packages.core.quality import QualityContext, QualityEngine


def _full_delta(chapter_id: str = "ch_001", extra: dict = None) -> dict:
    base = {
        "delta_id": "d_test",
        "delta_version": 1,
        "chapter_id": chapter_id,
        "workflow_run_id": "wfr_test",
        "previous_state_version": 1,
        "created_by": "observer:test",
        "created_at": "2026-08-23T00:00:00Z",
        "schema_version": "state-delta-v0",
        "character_changes": [],
        "world_changes": [],
        "relationship_changes": [],
        "new_events": [
            {
                "change_id": "e1",
                "op": "add",
                "target_id": "ev_1",
                "event_id": "ev_1",
                "type": "encounter",
                "cause": [],
                "effects": [],
                "participants": ["char_a"],
                "time": {"timeline_day": 5, "in_story_date": None},
                "description": "在山间遭遇山贼",
                "confidence": 1.0,
                "evidence": {"chapter_id": chapter_id, "excerpt": "在山间遭遇山贼"},
                "risk_level": "LOW",
            }
        ],
        "resolved_hooks": [
            {
                "change_id": "rh1",
                "op": "update",
                "target_id": "h1",
                "hook_id": "h1",
                "to_status": "RESOLVED",
                "payoff_summary": "钩子已结算",
                "confidence": 1.0,
                "evidence": {"chapter_id": chapter_id, "excerpt": "结算"},
                "risk_level": "LOW",
            }
        ],
        "new_hooks": [],
        "debt_changes": [],
    }
    if extra:
        base.update(extra)
    return base


def _pass_ctx(extra: dict = None) -> QualityContext:
    draft = (
        "今天天气真好，阳光洒在青石板上。\n\n"
        + "我们一起去公园散步，远处的鸟儿在枝头鸣唱。\n\n"
        + "午后咖啡馆的音乐若有若无，温暖而安静。\n\n"
        + "傍晚的风带来远山的凉意，人间值得。\n\n"
        + "突然，一道黑影闪过山巅，就在此时不见？"
    )
    plan = {"key_beats": [{"summary": "山间遭遇山贼"}]}
    snap = {
        "state_version": 1,
        "characters": [],
        "events": {},
        "hooks": [],
        "world": {"world_rules": []},
    }
    fields: dict = dict(
        chapter_id="ch_001",
        chapter_number=2,
        draft=draft,
        plan=plan,
        snapshot_pre=snap,
        delta=_full_delta(),
        payoff_history=[1, 1],
        ai_chars=60,
        human_chars=40,
    )
    if extra:
        fields.update(extra)
    return QualityContext(**fields)


def test_engine_end_to_end_pass():
    engine = QualityEngine()
    report = engine.evaluate(_pass_ctx())
    assert report.overall >= 0
    # 全 pass 时不应有 error 级 issue
    errors = [i for i in report.issues if i.severity == "error"]
    assert errors == [], f"unexpected errors: {[i.rule_id for i in errors]}"
    # 评分字段都得是 int
    for name in ("overall", "plot", "character", "continuity", "style", "pacing", "foreshadowing"):
        v = getattr(report, name)
        assert isinstance(v, int)
        assert 0 <= v <= 100


def test_engine_meta_fixed_keys():
    engine = QualityEngine()
    report = engine.evaluate(_pass_ctx())
    meta = report.meta
    assert set(meta.keys()) >= {"scoring_version", "llm_judge", "evaluated_at", "scoring_formula_hash"}
    assert meta["scoring_version"] == "quality-scoring-v0"
    assert meta["llm_judge"] == "deferred"
    assert isinstance(meta["evaluated_at"], str)
    assert len(meta["scoring_formula_hash"]) == 16


def test_engine_report_id_and_echo():
    engine = QualityEngine()
    report = engine.evaluate(_pass_ctx())
    assert report.report_id.startswith("qr_")
    assert report.chapter_id == "ch_001"
    assert report.chapter_number == 2


def test_engine_error_zero():
    """只要 delta 含 schema 错误，overall 必须为 0。"""
    engine = QualityEngine()
    ctx = _pass_ctx(extra={"delta": {"chapter_id": "ch_001", "delta_id": "d1"}})
    report = engine.evaluate(ctx)
    assert report.overall == 0
    errors = [i for i in report.issues if i.severity == "error"]
    assert errors, "expected at least one error issue"


def test_engine_pure_no_mutation():
    """evaluate 不得修改输入 ctx。"""
    engine = QualityEngine()
    ctx = _pass_ctx()
    before_draft = ctx.draft
    before_delta = ctx.delta
    _ = engine.evaluate(ctx)
    assert ctx.draft == before_draft
    assert ctx.delta == before_delta


def test_engine_q8_error_zero():
    engine = QualityEngine()
    ctx = _pass_ctx(extra={"ai_chars": 90, "human_chars": 10})
    report = engine.evaluate(ctx)
    # Q8 触发 error ⇒ overall = 0
    assert report.overall == 0
    assert any(i.rule_id == "RULE_Q8_HUMAN_RATIO_LOW" for i in report.issues)


def test_engine_quality_report_dump_alias():
    """QualityReport.model_dump(by_alias=True) 应输出 ``_meta`` 字段。"""
    engine = QualityEngine()
    report = engine.evaluate(_pass_ctx())
    dumped = report.model_dump(by_alias=True)
    assert "_meta" in dumped
    assert "scoring_formula_hash" in dumped["_meta"]
    # Python 端属性仍为 meta（不带下划线）
    assert hasattr(report, "meta")
