"""delta_anchoring.check_delta_source_anchoring 纯函数测试（2026-09-26 批次）。

覆盖点（任务书 A.4）：
- excerpt 归一化核验：不在源文本必报 / 逐字（含引号机械转写）命中不报；
- after bigram 低覆盖必报 / 高覆盖不报；
- 含「第一代老镖头」式构造用例——draft 含「第一镖头」而 delta 写「第一代老镖头」必须命中；
- 无 CJK bigram（纯英文/数字）与短状态词（bigram<6）跳过；
- 空 delta → 空 findings；source_texts 空 → 空 findings；
- delete op 与非 dict 条目跳过。
"""

from __future__ import annotations

from packages.core.story_state.delta_anchoring import (
    DELTA_BIGRAM_COVERAGE_MIN,
    check_delta_source_anchoring,
)

# 事故同构 draft：正文只出现「第一镖头」（无代际）。
DRAFT = (
    "戌时的更鼓从街尾传过来。第一镖头沈青崖押着最后一趟镖进了城，"
    "码头上人来人往，没有人注意到他袖口的血。"
)


def _delta_one(arr: str, item: dict) -> dict:
    return {arr: [item]}


def _char_item(op: str, after: str, excerpt: str | None = None) -> dict:
    item: dict = {
        "op": op,
        "target_id": "char_x",
        "facet": "knowledge",
        "after": after,
    }
    if excerpt is not None:
        item["evidence"] = {"chapter_id": "ch_x", "excerpt": excerpt}
    return item


def test_excerpt_not_verbatim_is_reported():
    """excerpt 不在源文本中 → OBS-EXCERPT-NOT-VERBATIM 必报。"""
    delta = _delta_one(
        "character_changes",
        _char_item("update", "沈青崖进了城", excerpt="沈青崖月夜抵达京城"),
    )
    findings = check_delta_source_anchoring(delta, [DRAFT])
    hits = [f for f in findings if f["rule_id"] == "OBS-EXCERPT-NOT-VERBATIM"]
    assert len(hits) == 1
    assert hits[0]["path"] == "character_changes[0]"
    assert hits[0]["sample"].startswith("沈青崖月夜抵达京城")


def test_excerpt_verbatim_not_reported():
    """excerpt 逐字摘自源文本 → 不报 excerpt 违规。"""
    delta = _delta_one(
        "character_changes",
        _char_item("update", "沈青崖进了城", excerpt="押着最后一趟镖进了城"),
    )
    findings = check_delta_source_anchoring(delta, [DRAFT])
    assert not [f for f in findings if f["rule_id"] == "OBS-EXCERPT-NOT-VERBATIM"]


def test_fabricated_generation_bigram_hit():
    """事故同构：draft 含「第一镖头」而 delta 写「第一代老镖头…」必须命中。

    after 大部分 bigram（一代/代老/泽被/苍生…）在源文本中不存在，覆盖率低于
    阈值（实测分布见 delta_anchoring 模块 docstring：p10=0.108 之下才报）。
    """
    after = "第一代老镖头泽被苍生福泽绵延三百年基业庇佑子孙万代昌隆恩泽遍及山南河北"
    delta = _delta_one("character_changes", _char_item("add", after))
    findings = check_delta_source_anchoring(delta, [DRAFT])
    hits = [f for f in findings if f["rule_id"] == "OBS-UNSOURCED-PHRASE"]
    assert len(hits) == 1, findings
    assert hits[0]["path"] == "character_changes[0]"
    assert hits[0]["coverage"] < DELTA_BIGRAM_COVERAGE_MIN
    assert hits[0]["sample"] == after[:50]


def test_quote_translated_excerpt_not_reported():
    """excerpt 引号被 LLM 机械转写（“→'）但文字逐字一致 → 归一化后不报。

    dev 库实证：observer 会把正文弯引号转写为直引号，逐字节比对会大量误报。
    """
    excerpt = "他掂了掂银子的分量，说：成色一般。"
    draft_with_quotes = (
        "柜台后的老人抬起头。他掂了掂银子的分量，说：“成色一般。”"
        "说完便把银子推了回来。"
    )
    delta = _delta_one(
        "character_changes",
        _char_item("update", "当铺老人压价", excerpt=excerpt),
    )
    findings = check_delta_source_anchoring(delta, [draft_with_quotes])
    assert not [f for f in findings if f["rule_id"] == "OBS-EXCERPT-NOT-VERBATIM"], (
        findings
    )


def test_short_state_word_skipped():
    """短状态词（CJK bigram < 6）跳过 bigram 检查——实测 bigram<6 组 p50=0，
    「死亡」「冷静、试探」类状态快照写法天然脱节，纳入只产噪声。"""
    delta = _delta_one("character_changes", _char_item("update", "死亡"))
    findings = check_delta_source_anchoring(delta, [DRAFT])
    assert findings == [], findings


def test_well_anchored_after_not_reported():
    """after 直接转写源文本句子 → 高覆盖，不报。"""
    after = "第一镖头沈青崖押着最后一趟镖进了城"
    delta = _delta_one("character_changes", _char_item("add", after))
    findings = check_delta_source_anchoring(delta, [DRAFT])
    assert findings == [], findings


def test_non_cjk_after_is_skipped():
    """after 无 CJK bigram（纯英文/数字）→ 跳过 bigram 检查，不报。"""
    delta = _delta_one("character_changes", _char_item("add", "Lin Yuan arrived 1987"))
    findings = check_delta_source_anchoring(delta, [DRAFT])
    assert findings == [], findings


def test_empty_delta_returns_empty_findings():
    """7 数组全空 / 缺数组键 → 空 findings。"""
    assert check_delta_source_anchoring({}, [DRAFT]) == []
    delta = {k: [] for k in (
        "character_changes", "world_changes", "relationship_changes",
        "new_events", "resolved_hooks", "new_hooks", "debt_changes",
    )}
    assert check_delta_source_anchoring(delta, [DRAFT]) == []


def test_empty_source_texts_returns_empty_findings():
    """源文本全空/缺失 → 无源可校，返回 []（不可证伪即不报）。"""
    delta = _delta_one(
        "character_changes",
        _char_item("add", "第一代老镖头开创漕运百年基业", excerpt="完全不在源里的摘录"),
    )
    assert check_delta_source_anchoring(delta, []) == []
    assert check_delta_source_anchoring(delta, ["", "  "]) == []
    assert check_delta_source_anchoring(delta, [None]) == []  # type: ignore[list-item]


def test_delete_op_and_non_dict_entries_skipped():
    """delete op 无新文本不锚定；非 dict 条目跳过（结构守卫另行处理）。"""
    delta = {
        "character_changes": [
            "裸字符串碎片",
            _char_item("delete", "第一代老镖头开创漕运百年基业"),
        ],
    }
    assert check_delta_source_anchoring(delta, [DRAFT]) == []


def test_findings_across_multiple_arrays_keep_path():
    """多数组多违规时 path 带数组名与下标。"""
    delta = {
        "world_changes": [
            _char_item("add", "北境冰原尽头的沉默古神沉睡千年"),
        ],
        "new_hooks": [
            {"op": "add", "after": "漕帮内部的银钱亏空案牵扯三代恩怨"},
        ],
    }
    findings = check_delta_source_anchoring(delta, [DRAFT])
    paths = {f["path"] for f in findings}
    assert paths == {"world_changes[0]", "new_hooks[0]"}, findings


def test_excerpt_empty_string_not_checked():
    """excerpt 为空串/空白 → 不视为摘录声明，不报。"""
    delta = _delta_one(
        "character_changes",
        _char_item("add", "第一镖头沈青崖押着最后一趟镖进了城", excerpt="  "),
    )
    assert check_delta_source_anchoring(delta, [DRAFT]) == []
