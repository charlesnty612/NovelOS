"""8 条 Guardrail 单元测试（tests.unit.quality.test_guardrails）。

目标：每条 guardrail 至少覆盖 pass / warning / error 三态中的可达态；真断言。
"""

from __future__ import annotations

from packages.core.quality import guardrails as g
from packages.core.quality.issues import Issue


# ============================================================================
# helpers
# ============================================================================


def _delta_with_chapter(chapter_id: str = "ch_001", **extra) -> dict:
    """构造最小合法的 delta（足以通过 schema_validity）。"""
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
        "new_events": [],
        "resolved_hooks": [],
        "new_hooks": [],
        "debt_changes": [],
    }
    base.update(extra)
    return base


def _snapshot(characters=None, events=None, hooks=None, world_rules=None):
    return {
        "state_version": 1,
        "characters": characters or [],
        "events": events or {},
        "hooks": hooks or [],
        "world": {"world_rules": world_rules or []},
    }


# ============================================================================
# schema_validity
# ============================================================================


def test_schema_validity_pass():
    issues = g.schema_validity(_delta_with_chapter())
    assert issues == []


def test_schema_validity_error():
    issues = g.schema_validity({"chapter_id": "ch_x", "delta_id": "d1"})
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert issues[0].category == "schema_validity"
    assert issues[0].rule_id == "SCHEMA_VALIDATION_FAILED"


def test_schema_validity_non_dict():
    issues = g.schema_validity("not a dict")  # type: ignore[arg-type]
    assert len(issues) == 1
    assert issues[0].severity == "error"


# ============================================================================
# character_contradiction
# ============================================================================


def test_character_contradiction_pass():
    snap = _snapshot(
        characters=[
            {
                "character_id": "char_a",
                "name": "阿七",
                "current_state": {"location": "青云山", "health": "alive"},
                "knowledge": [],
                "beliefs": [],
                "relationships": [],
                "facet": "state",
            }
        ]
    )
    delta = _delta_with_chapter(
        character_changes=[
            {
                "change_id": "cc1",
                "op": "update",
                "character_id": "char_a",
                "field": "location",
                "before": "青云山",
                "after": {"location": "幻雪峰"},
                "confidence": 1.0,
                "evidence": {"chapter_id": "ch_001"},
                "risk_level": "LOW",
            }
        ]
    )
    assert g.character_contradiction(snap, delta) == []


def test_character_contradiction_dead_active_error():
    snap = _snapshot(
        characters=[
            {
                "character_id": "char_dead",
                "name": "亡者",
                "current_state": {"health": "死亡"},
                "knowledge": [],
                "beliefs": [],
                "relationships": [],
                "facet": "state",
            }
        ]
    )
    delta = _delta_with_chapter(
        character_changes=[
            {
                "change_id": "cc1",
                "op": "update",
                "character_id": "char_dead",
                "field": "location",
                "before": "旧地",
                "after": {"location": "新地", "goal": "复活"},
                "confidence": 1.0,
                "evidence": {"chapter_id": "ch_001"},
                "risk_level": "LOW",
            }
        ]
    )
    issues = g.character_contradiction(snap, delta)
    assert any(
        i.severity == "error"
        and i.rule_id == "RULE_CHAR_DEAD_ACTIVE"
        for i in issues
    )


def test_character_contradiction_before_mismatch_warning():
    snap = _snapshot(
        characters=[
            {
                "character_id": "char_b",
                "name": "小明",
                "current_state": {"location": "海边"},
                "knowledge": [],
                "beliefs": [],
                "relationships": [],
                "facet": "state",
            }
        ]
    )
    delta = _delta_with_chapter(
        character_changes=[
            {
                "change_id": "cc1",
                "op": "update",
                "character_id": "char_b",
                "field": "location",
                "before": "山脚",  # 与 snap 不一致
                "after": {"location": "海角"},
                "confidence": 1.0,
                "evidence": {"chapter_id": "ch_001"},
                "risk_level": "LOW",
            }
        ]
    )
    issues = g.character_contradiction(snap, delta)
    assert any(
        i.severity == "warning"
        and i.rule_id == "RULE_CHAR_BEFORE_MISMATCH"
        for i in issues
    )


# ============================================================================
# world_rule_contradiction
# ============================================================================


def test_world_rule_hard_error():
    snap = _snapshot(
        world_rules=[
            {
                "world_rule_id": "wrule_1",
                "name": "不可逆契约",
                "statement": "契约一旦缔结不可解除",
                "data_json": {"hard": True},
            }
        ]
    )
    delta = _delta_with_chapter(
        world_changes=[
            {
                "change_id": "wc1",
                "op": "update",
                "world_kind": "rule",
                "world_id": "wrule_1",
                "field": "statement",
                "before": "契约一旦缔结不可解除",
                "after": {"statement": "契约可以解除"},
                "confidence": 1.0,
                "evidence": {"chapter_id": "ch_001"},
                "risk_level": "HIGH",
            }
        ]
    )
    issues = g.world_rule_contradiction(snap, delta)
    assert any(
        i.severity == "error"
        and i.rule_id == "RULE_WORLD_HARD_RULE_CHANGED"
        for i in issues
    )


def test_world_rule_soft_warning():
    snap = _snapshot(
        world_rules=[
            {
                "world_rule_id": "wrule_2",
                "name": "通用法则",
                "statement": "通用",
                "data_json": {"hard": False},
            }
        ]
    )
    delta = _delta_with_chapter(
        world_changes=[
            {
                "change_id": "wc1",
                "op": "update",
                "world_kind": "rule",
                "world_id": "wrule_2",
                "field": "statement",
                "before": "通用",
                "after": {"statement": "可改"},
                "confidence": 1.0,
                "evidence": {"chapter_id": "ch_001"},
                "risk_level": "MEDIUM",
            }
        ]
    )
    issues = g.world_rule_contradiction(snap, delta)
    assert any(
        i.severity == "warning"
        and i.rule_id == "RULE_WORLD_SOFT_RULE_CHANGED"
        for i in issues
    )


def test_world_rule_pass():
    snap = _snapshot(
        world_rules=[
            {
                "world_rule_id": "wrule_3",
                "name": "可改规则",
                "statement": "可改",
                "data_json": {"hard": False},
            }
        ]
    )
    delta = _delta_with_chapter(
        world_changes=[
            {
                "change_id": "wc1",
                "op": "update",
                "world_kind": "location",  # 非 rule，不检查
                "world_id": "loc_3",
                "field": "name",
                "before": "X",
                "after": {"name": "Y"},
                "confidence": 1.0,
                "evidence": {"chapter_id": "ch_001"},
                "risk_level": "LOW",
            }
        ]
    )
    issues = g.world_rule_contradiction(snap, delta)
    assert issues == []


# ============================================================================
# timeline_consistency
# ============================================================================


def test_timeline_non_monotonic_warning():
    snap = _snapshot(
        events={
            "ev_old": {
                "event_id": "ev_old",
                "time": {"timeline_day": 10, "in_story_date": None},
            }
        }
    )
    delta = _delta_with_chapter(
        new_events=[
            {
                "change_id": "e1",
                "op": "add",
                "target_id": "e_new",
                "event_id": "e_new",
                "type": "transition",
                "cause": [],
                "effects": [],
                "participants": [],
                "time": {"timeline_day": 5, "in_story_date": None},
                "confidence": 1.0,
                "evidence": {"chapter_id": "ch_001"},
                "risk_level": "LOW",
            }
        ]
    )
    issues = g.timeline_consistency(snap, delta)
    assert any(
        i.rule_id == "RULE_TIMELINE_NON_MONOTONIC" and i.severity == "warning"
        for i in issues
    )


def test_timeline_negative_day_warning():
    snap = _snapshot()
    delta = _delta_with_chapter(
        new_events=[
            {
                "change_id": "e1",
                "op": "add",
                "target_id": "e_neg",
                "event_id": "e_neg",
                "type": "transition",
                "cause": [],
                "effects": [],
                "participants": [],
                "time": {"timeline_day": -1, "in_story_date": None},
                "confidence": 1.0,
                "evidence": {"chapter_id": "ch_001"},
                "risk_level": "LOW",
            }
        ]
    )
    issues = g.timeline_consistency(snap, delta)
    assert any(i.rule_id == "RULE_TIMELINE_NEGATIVE_DAY" for i in issues)


def test_timeline_pass():
    snap = _snapshot()
    delta = _delta_with_chapter()
    assert g.timeline_consistency(snap, delta) == []


# ============================================================================
# knowledge_leakage
# ============================================================================


def test_knowledge_leakage_warning():
    snap = _snapshot(
        events={
            "ev_secret": {
                "event_id": "ev_secret",
                "name": "城主死亡真相",
                "description": "城主与杀手是同一人",
                "who_knows": ["char_a"],
                "time": {"timeline_day": 1, "in_story_date": None},
            }
        }
    )
    delta = _delta_with_chapter(
        character_changes=[
            {
                "change_id": "cc1",
                "op": "update",
                "character_id": "char_b",  # 不在 who_knows
                "field": "knowledge",
                "before": [],
                "after": {
                    "knowledge": ["城主与杀手是同一人"],
                },
                "confidence": 1.0,
                "evidence": {"chapter_id": "ch_001"},
                "risk_level": "MEDIUM",
            }
        ]
    )
    issues = g.knowledge_leakage(snap, delta)
    assert any(
        i.severity == "warning" and i.rule_id == "RULE_KNOWLEDGE_LEAK"
        for i in issues
    )


def test_knowledge_leakage_pass_when_undeclared():
    snap = _snapshot(
        events={
            "ev_open": {
                "event_id": "ev_open",
                "name": "公开事件",
                "description": "普通事实",
                "who_knows": None,  # 未声明
                "time": {"timeline_day": 1, "in_story_date": None},
            }
        }
    )
    delta = _delta_with_chapter(
        character_changes=[
            {
                "change_id": "cc1",
                "op": "update",
                "character_id": "char_b",
                "field": "knowledge",
                "before": [],
                "after": {"knowledge": ["普通事实"]},
                "confidence": 1.0,
                "evidence": {"chapter_id": "ch_001"},
                "risk_level": "LOW",
            }
        ]
    )
    assert g.knowledge_leakage(snap, delta) == []


# ============================================================================
# REQ-Q6
# ============================================================================


def test_q6_no_references_info():
    issues = g.req_q6("随便一段正文字数足够超过十三个字以上的内容", [])
    assert any(i.rule_id == "Q6_NO_REFERENCES" for i in issues)


def test_q6_ngram_warning():
    # 构造 draft 含 13+ 字连续片段与 ref 完全一致（去空白后）
    shared = "与参照文字内容完全一致的连续十三个字以上片段确保命中"
    ref = "无关前缀文字" + shared + "无关后缀文字"
    draft = "正文开头。 " + shared + " 正文结尾。" * 2
    issues = g.req_q6(draft, [ref])
    assert any(
        i.rule_id == "RULE_Q6_NGRAM_OVERLAP" and i.severity == "warning"
        for i in issues
    )


def test_q6_rate_error():
    # 制造大量重叠以突破 2% 阈值
    ref_chunk = "完全重复长度的公共子字符串用于测试Q6重叠率超过百分之二阈值"
    draft = (ref_chunk + "其他") * 10
    ref = ref_chunk * 100
    issues = g.req_q6(draft, [ref])
    assert any(
        i.rule_id == "RULE_Q6_OVERLAP_RATE" and i.severity == "error"
        for i in issues
    )


def test_q6_repeated_shingle_overlap_rate_doubles():
    """F1 修复：同一 13 字 shingle 在 draft 出现多次时，重叠率应反映所有出现位置。

    验证：把同一段公共 13 字串在 draft 中重复 2 次 vs 1 次，去空白后字符总数
    也近 2 倍 ⇒ 修复后的重叠率应近 2 倍；修复前两次几乎相同（均只计入首个位置）。
    """
    # 25 字确保含 ≥ 13 个连续公共 shingles；ref 重复以避免 ref 端容量瓶颈
    shared = "一二三四五六七八九十百千万测字"  # 15 字
    ref = shared * 10  # ref 端大量相同片段

    # 关键设计：保持 1x 和 2x 总字符数相同（N），仅 shared 出现次数不同。
    # 修复前：1x overlap=13 → 13/N；2x overlap=13（find 仅首个位置）→ 13/N，两版比例**完全相等**。
    # 修复后：1x overlap=13；2x overlap=26（两处不重叠区间）→ 26/N = 2 × 13/N，比例严格翻倍。
    n_total = 615
    draft_1x = shared + "甲" * (n_total - len(shared))              # 615 chars
    draft_2x = shared + shared + "甲" * (n_total - 2 * len(shared))  # 615 chars

    issues_1x = g.req_q6(draft_1x, [ref])
    issues_2x = g.req_q6(draft_2x, [ref])

    def _rate(issues):
        for it in issues:
            if it.rule_id == "RULE_Q6_OVERLAP_RATE":
                # message 形如 "重叠字符占比 X.XX% > 2.00%"
                pct = float(
                    it.message.split("重叠字符占比 ")[1].split("%")[0]
                )
                return pct
        return None

    rate_1x = _rate(issues_1x)
    rate_2x = _rate(issues_2x)

    # 修复前：rate_1x 与 rate_2x 几乎相同；修复后：rate_2x ≈ 2 × rate_1x
    assert rate_1x is not None, "1x 必须触发 RULE_Q6_OVERLAP_RATE"
    assert rate_2x is not None, "2x 必须触发 RULE_Q6_OVERLAP_RATE"
    # 修复前比例近似 1.0；修复后应 >= 1.5（理论 2.0，去空白后偏差极小）
    assert rate_2x >= rate_1x * 1.5, (
        f"修复失败：2x 比例={rate_2x} 未明显高于 1x 比例={rate_1x} "
        f"（修复前两者近似相等）"
    )


def test_q6_pass_no_overlap():
    draft = "完全无关的文字内容" * 5
    ref = "另一段完全不同的参照书文字" * 5
    issues = g.req_q6(draft, [ref])
    assert issues == []


def test_q6_whitelist_excludes_overlap():
    shared = "白名单放行的完全相同的一段足够长字符串确保通过白名单不再算重叠"
    draft = "开头 " + shared + " 结尾"
    ref = shared
    issues = g.req_q6(draft, [ref], whitelist=[shared])
    # 白名单命中后不应有 NGRAM 警告与重叠率 error
    assert not any(
        i.rule_id in ("RULE_Q6_NGRAM_OVERLAP", "RULE_Q6_OVERLAP_RATE")
        for i in issues
    )


# ============================================================================
# REQ-Q7
# ============================================================================


def test_q7_ai_markers_warning():
    # 每千字塞满 marker
    base = "首先" * 6 + "其次" * 6 + "最后" * 6 + "但是" * 6 + "正常句子。" * 50
    issues = g.req_q7(base)
    assert any(i.rule_id == "RULE_Q7_AI_MARKERS" for i in issues)


def test_q7_para_opener_warning():
    paras = ["然而测试开始。" + "x" * 100] * 5 + ["正常段落内容" + "y" * 100]
    draft = "\n\n".join(paras)
    issues = g.req_q7(draft)
    assert any(i.rule_id == "RULE_Q7_PARA_OPENER" for i in issues)


def test_q7_flat_sentence_warning():
    # 全部 6 字句，无 marker；文本长 ≥ 1000 ⇒ 触发句长低标准差
    sentence = "测试十个字符"  # 6 字
    draft = (sentence + "。") * 200  # 1400 字
    issues = g.req_q7(draft)
    assert any(i.rule_id == "RULE_Q7_FLAT_SENTENCE" for i in issues)


def test_q7_pass_short():
    assert g.req_q7("短文本") == []


# ============================================================================
# REQ-Q8
# ============================================================================


def test_q8_human_ratio_error():
    issues = g.req_q8(ai_chars=80, human_chars=20)
    assert any(
        i.severity == "error" and i.rule_id == "RULE_Q8_HUMAN_RATIO_LOW"
        for i in issues
    )


def test_q8_human_ratio_warning():
    issues = g.req_q8(ai_chars=70, human_chars=30)
    assert any(
        i.severity == "warning" and i.rule_id == "RULE_Q8_HUMAN_RATIO_LOW_WARN"
        for i in issues
    )


def test_q8_pass():
    issues = g.req_q8(ai_chars=40, human_chars=60)
    assert issues == []


def test_q8_no_data_info():
    issues = g.req_q8(0, 0)
    assert any(i.rule_id == "RULE_Q8_NO_DATA" for i in issues)
