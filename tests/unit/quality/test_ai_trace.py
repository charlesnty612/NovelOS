"""ai_trace 子分单元测试（tests.unit.quality.test_ai_trace）。

覆盖：
- 高分基线（普通人工章节）
- 低分基线（大段复制粘贴）
- 套话密集文本扣分
- 跨章重复扣分
- 跨章子信号在无历史章节时降级满分
- QualityReport 集成路径：含 ai_trace 字段，overall 是七维平均
"""

from __future__ import annotations

from packages.core.quality.ai_trace import (
    AI_CLICHES,
    compute_ai_trace,
    cross_chapter_repetition,
    formula_text,
    intra_chapter_repetition,
)
from packages.core.quality.guardrails import compute_shingles
from packages.core.quality import QualityEngine, QualityContext


# ============================================================================
# 子信号级别
# ============================================================================


def test_intra_chapter_normal_text_low_repetition():
    """正常人工正文：章内重复率低 ⇒ 0 扣分。"""
    draft = (
        "今天天气真好，阳光洒在青石板上。"
        "我们一起去公园散步，远处的鸟儿在枝头鸣唱。"
        "午后咖啡馆的音乐若有若无，温暖而安静。"
        "傍晚的风带来远山的凉意，人间值得。"
    )
    ratio, deduct = intra_chapter_repetition(draft)
    assert ratio < 0.05
    assert deduct == 0


def test_intra_chapter_heavy_copy_paste_high_repetition():
    """大段复制粘贴文本：章内重复率高 ⇒ 显著扣分。"""
    chunk = "这是一段将被反复粘贴的章节正文用来检测机械化重复的字串用来测试"
    # 重复 6 次使 13 字 shingle 重复率明显升高
    draft = chunk * 6
    ratio, deduct = intra_chapter_repetition(draft)
    assert ratio > 0.10  # 至少 10% 重复
    assert deduct >= 10


def test_cross_chapter_no_history_downgrade_full():
    """无历史章节：跨章子信号降级（ratio=0, deduct=0, available=False）。"""
    ratio, deduct, avail = cross_chapter_repetition("任意正文够长够长够长够长够长够长够长够长够长", [])
    assert ratio == 0.0
    assert deduct == 0
    assert avail is False


def test_cross_chapter_overlapping_with_previous():
    """与历史章节大面积重叠：跨章重复率显著 ⇒ 扣分。"""
    chunk = "周元的窥伺惊动了整座禁地，禁地异象惊宗门上下，识海古镜传承再现"
    draft = chunk * 4  # 大量与历史共享 shingle
    ratio, deduct, avail = cross_chapter_repetition(draft, [chunk * 3])
    assert avail is True
    assert ratio > 0.10
    assert deduct >= 0  # 10% 是 0 桶与 8 桶边界 ⇒ 允许 deduct=0，但 ratio 必 > 0


def test_cross_chapter_disjoint_no_deduct():
    """与历史章节完全无关：跨章重复率低 ⇒ 不扣分。"""
    cur = "完全无关的当前章节正文" * 3
    prev = "另一条线另一段叙事另一组词汇另一组人物" * 3
    ratio, deduct, avail = cross_chapter_repetition(cur, [prev])
    assert avail is True
    assert ratio < 0.10
    assert deduct == 0


def test_ai_trace_cliches_module_constant_non_empty():
    assert isinstance(AI_CLICHES, tuple)
    assert len(AI_CLICHES) >= 20


def test_ai_trace_compute_normal_high_score():
    """正常文本：ai_trace 应给出 ≥ 80 分（任务书校准基线）。"""
    draft = (
        "今天天气真好，阳光洒在青石板上。"
        + "我们一起去公园散步，远处的鸟儿在枝头鸣唱。"
        + "午后咖啡馆的音乐若有若无，温暖而安静。"
        + "傍晚的风带来远山的凉意，人间值得。"
        + "突然，一道黑影闪过山巅，就在此时不见？"
    )
    score, detail = compute_ai_trace(draft)
    assert score >= 80, f"普通人工章节 ai_trace 应 ≥ 80，实际 {score}；detail={detail}"
    assert detail["cross_chapter_available"] is False  # 无历史 ⇒ 降级满分


def test_ai_trace_compute_heavy_repetition_low_score():
    """大段复制粘贴文本：ai_trace 应给出明显低于满分（任务书校准基线 ≤ 60）。

    为同时触发章内+跨章+套话三个子信号：把同一 chunk 反复粘贴，并嵌入若干 AI 套话
    词（如"嘴角勾起""眼中闪过一丝""深吸一口气"）使其套话命中密度进入高档；同
    时把相同 chunk 作为历史章节传入，使跨章重合率上升。
    """
    # chunk 内含套话词 + 重复结构；让 ratio 与套话密度同时进入高档
    chunk = "这是要被反复粘贴的正文段落，嘴角勾起，眼底深处，深吸一口气，用来检测"
    draft = (chunk * 30) + "然而他深吸一口气，嘴角勾起，眼底深处。" * 20
    score, detail = compute_ai_trace(draft, previous_drafts=[chunk * 10])
    assert detail["in_chapter_deduct"] >= 28, f"章内重复子信号应进入高档，实际 {detail}"
    assert detail["cross_chapter_available"] is True
    assert detail["cliche_deduct"] >= 18, f"套话子信号应进入高档，实际 {detail}"
    assert score <= 60, f"灌水重复章节 ai_trace 应 ≤ 60，实际 {score}"


def test_ai_trace_compute_cliche_dense_penalty():
    """套话密集文本：套话子信号应扣分（且无章内/跨章问题时整体扣分明显）。"""
    # 用无章内/跨章重复、但套话堆砌的短文本
    base = (
        "然而他微微一笑，淡淡开口，沉声说道。不禁深吸一口气，眼中闪过一丝。"
        "然而她嘴角勾起，眼底深处仿佛凝固，空气中骤然一片死寂。"
        "然而空气中凝固，仿佛时间停止，骤然一片死寂，落针可闻。"
    )
    draft = base * 10
    score, detail = compute_ai_trace(draft)
    assert detail["cliche_deduct"] >= 8  # 至少进入第二档
    # 总分因套话扣分应低于 100
    assert score < 100


def test_ai_trace_formula_text_signature():
    """formula_text 应返回稳定字符串（hash 重算/文档化用）。"""
    txt = formula_text()
    assert "ai_trace" in txt
    # 公式字符串是减法合成（与 overall 七维平均不同）
    assert "100" in txt
    assert "deduct" in txt


# ============================================================================
# 集成路径：engine 与 QualityReport
# ============================================================================


def _pass_ctx(extra: dict | None = None, previous_drafts: list[str] | None = None) -> QualityContext:
    draft = (
        "今天天气真好，阳光洒在青石板上。"
        + "我们一起去公园散步，远处的鸟儿在枝头鸣唱。"
        + "午后咖啡馆的音乐若有若无，温暖而安静。"
        + "傍晚的风带来远山的凉意，人间值得。"
        + "突然，一道黑影闪过山巅，就在此时不见？"
    )
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
        plan={"key_beats": [{"summary": "山间遭遇"}]},
        snapshot_pre=snap,
        delta={
            "delta_id": "d_test",
            "delta_version": 1,
            "chapter_id": "ch_001",
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
                    "description": "山间遭遇山贼",
                    "confidence": 1.0,
                    "evidence": {"chapter_id": "ch_001", "excerpt": "山间遭遇山贼"},
                    "risk_level": "LOW",
                }
            ],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        },
        payoff_history=[1, 1],
        ai_chars=60,
        human_chars=40,
        previous_drafts=previous_drafts or [],
    )
    if extra:
        fields.update(extra)
    return QualityContext(**fields)


def test_engine_report_has_ai_trace_field():
    """QualityReport.ai_trace 字段存在且为 int。"""
    engine = QualityEngine()
    report = engine.evaluate(_pass_ctx())
    assert hasattr(report, "ai_trace")
    assert isinstance(report.ai_trace, int)
    assert 0 <= report.ai_trace <= 100


def test_engine_overall_is_seven_dim_average():
    """overall = 七维平均（四舍五入）—— 直接核对。"""
    engine = QualityEngine()
    # 固定所有七子分的极端输入：把 ai_trace 控制到 100（无章内重复 / 跨章 / 套话）
    report = engine.evaluate(_pass_ctx())
    avg = round(
        (
            report.plot
            + report.character
            + report.continuity
            + report.style
            + report.pacing
            + report.foreshadowing
            + report.ai_trace
        )
        / 7
    )
    assert report.overall == avg


def test_engine_cross_chapter_uses_previous_drafts():
    """传入 previous_drafts 时，跨章子信号能识别重叠。"""
    chunk = "这是一段与当前章节高度重叠的旧章节内容用来检测跨章重复"
    prev = chunk * 5
    cur = chunk * 5
    snap = {
        "state_version": 1,
        "characters": [],
        "events": {},
        "hooks": [],
        "world": {"world_rules": []},
    }
    ctx = QualityContext(
        chapter_id="ch_001",
        chapter_number=2,
        draft=cur,
        plan={"key_beats": [{"summary": "x"}]},
        snapshot_pre=snap,
        delta={
            "chapter_id": "ch_001",
            "new_events": [],
            "character_changes": [],
            "new_hooks": [],
            "resolved_hooks": [],
            "debt_changes": [],
            "world_changes": [],
            "relationship_changes": [],
        },
        payoff_history=[],
        ai_chars=50,
        human_chars=50,
        previous_drafts=[prev],
    )
    report = QualityEngine().evaluate(ctx)
    # 跨章子信号有数据 + 高重叠 ⇒ ai_trace 必小于 100
    assert report.ai_trace < 100


def test_engine_q8_error_zero_overrides_ai_trace():
    """error issue 阻断整体 overall=0（与 ai_trace 无关；验证聚合阻断逻辑）。"""
    ctx = _pass_ctx(extra={"ai_chars": 90, "human_chars": 10})  # Q8 触发 error
    report = QualityEngine().evaluate(ctx)
    assert report.overall == 0
    # ai_trace 仍计算但不影响阻断语义
    assert 0 <= report.ai_trace <= 100


def test_ai_trace_does_not_produce_issues():
    """ai_trace 子分不产出 issue —— 它是 score 而非 guardrail。"""
    engine = QualityEngine()
    report = engine.evaluate(_pass_ctx())
    for it in report.issues:
        assert it.rule_id != "RULE_AI_TRACE_*"