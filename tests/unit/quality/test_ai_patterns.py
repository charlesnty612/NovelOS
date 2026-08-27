"""packages.core.quality.ai_patterns 单元测试。

覆盖：
- 无命中时返回空列表。
- 禁用词 / AI 高频套话命中。
- 连续同词开头句式套路。
- 「他/她」主语排比堆砌。
- 章尾总结/升华体套话。
- 破折号/省略号滥用（默认阈值与自定义阈值）。
- 解释腔连接词高密度。
- 命中数超阈值时 severity 升级为 error。
- 空字符串/空白字符串安全返回空列表。
"""

from __future__ import annotations

import pytest

from packages.core.quality.ai_patterns import (
    AI_PATTERN_FORBIDDEN_WORDS,
    DEFAULT_DASH_THRESHOLD_PER_1K,
    scan_ai_patterns,
)


# ---------------------------------------------------------------------------
# 基础行为
# ---------------------------------------------------------------------------


def test_empty_prose_returns_empty():
    assert scan_ai_patterns("") == []
    assert scan_ai_patterns("   \n\t  ") == []


def test_clean_prose_returns_empty():
    prose = "风吹过山岗，带来远行的消息。他站起身，望向天际。"
    assert scan_ai_patterns(prose) == []


def test_all_hits_have_required_fields():
    prose = "仿佛一道闪电。他走了。他停下脚步。他转过身。本章目标。"
    hits = scan_ai_patterns(prose)
    assert hits
    for hit in hits:
        assert "rule_id" in hit
        assert "severity" in hit
        assert hit["severity"] in ("warning", "error")
        assert "message" in hit


# ---------------------------------------------------------------------------
# AI-FORBIDDEN-WORD
# ---------------------------------------------------------------------------


def test_forbidden_word_hit():
    prose = "他仿佛看到了命运的齿轮。"
    hits = scan_ai_patterns(prose)
    fw_hits = [h for h in hits if h["rule_id"] == "AI-FORBIDDEN-WORD"]
    assert len(fw_hits) == 1
    hit = fw_hits[0]
    assert hit["severity"] == "warning"
    assert "仿佛" in hit["message"]
    assert "仿佛" in hit["words"]
    assert hit["count"] >= 1


def test_forbidden_word_backward_compatible_list():
    # 模块级禁用词列表包含原有默认词
    assert "仿佛" in AI_PATTERN_FORBIDDEN_WORDS
    assert "如同" in AI_PATTERN_FORBIDDEN_WORDS
    assert "本章目标" in AI_PATTERN_FORBIDDEN_WORDS


def test_forbidden_word_multiple_words_in_one_hit():
    prose = "本章目标仿佛如同完成任务。"
    hits = scan_ai_patterns(prose)
    fw_hits = [h for h in hits if h["rule_id"] == "AI-FORBIDDEN-WORD"]
    assert len(fw_hits) == 1
    assert set(fw_hits[0]["words"]) == {"仿佛", "如同", "本章目标"}


def test_forbidden_word_severity_upgrade_on_many_hits():
    # 构造 10 处以上命中（总次数 >= 10），应升级为 error
    prose = "仿佛" * 11
    hits = scan_ai_patterns(prose)
    fw_hits = [h for h in hits if h["rule_id"] == "AI-FORBIDDEN-WORD"]
    assert len(fw_hits) == 1
    assert fw_hits[0]["severity"] == "error"
    assert fw_hits[0]["count"] >= 10


# ---------------------------------------------------------------------------
# AI-TRIPLET-OPENING
# ---------------------------------------------------------------------------


def test_triplet_opening_hit():
    prose = "风吹过了。风吹散了。风吹远了。他回家了。"
    hits = scan_ai_patterns(prose)
    tri_hits = [h for h in hits if h["rule_id"] == "AI-TRIPLET-OPENING"]
    assert len(tri_hits) == 1
    hit = tri_hits[0]
    assert hit["severity"] == "warning"
    assert hit["word"] == "风吹"
    assert hit["count"] == 3


def test_triplet_opening_requires_three():
    # 只有两句开头相同，不命中
    prose = "风吹过了。风吹散了。雨下来了。"
    hits = scan_ai_patterns(prose)
    assert not [h for h in hits if h["rule_id"] == "AI-TRIPLET-OPENING"]


# ---------------------------------------------------------------------------
# AI-PRONOUN-PILE
# ---------------------------------------------------------------------------


def test_pronoun_pile_hit():
    prose = "他走了。他停下脚步。他转过身。"
    hits = scan_ai_patterns(prose)
    pp_hits = [h for h in hits if h["rule_id"] == "AI-PRONOUN-PILE"]
    assert len(pp_hits) == 1
    assert pp_hits[0]["count"] == 3


def test_pronoun_pile_requires_three():
    # 仅两句以他/她开头，第三句不是，不应命中
    prose = "他走了。他停下脚步。天黑了。"
    hits = scan_ai_patterns(prose)
    assert not [h for h in hits if h["rule_id"] == "AI-PRONOUN-PILE"]


# ---------------------------------------------------------------------------
# AI-ENDING-SUMMARY
# ---------------------------------------------------------------------------


def test_ending_summary_hit():
    prose = "前文叙事。\n\n这一刻，他明白了一切。从此以后，新的篇章即将开启。"
    hits = scan_ai_patterns(prose)
    es_hits = [h for h in hits if h["rule_id"] == "AI-ENDING-SUMMARY"]
    assert len(es_hits) == 1
    assert "这一刻" in es_hits[0]["words"]
    assert "从此以后" in es_hits[0]["words"]
    assert "新的篇章" in es_hits[0]["words"]


def test_ending_summary_only_last_paragraph():
    # 套话出现在非结尾段，不命中
    prose = "从此以后，他开始改变。\n\n真正的结局在最后一刻到来。"
    hits = scan_ai_patterns(prose)
    es_hits = [h for h in hits if h["rule_id"] == "AI-ENDING-SUMMARY"]
    assert len(es_hits) == 0 or "从此以后" not in es_hits[0]["words"]


# ---------------------------------------------------------------------------
# AI-PUNCT-ABUSE
# ---------------------------------------------------------------------------


def test_punct_abuse_hit():
    # 约 100 字正文，插入 7 处破折号/省略号 → 每千字约 70 处，远超阈值
    prose = "他" * 90 + "——" * 7
    hits = scan_ai_patterns(prose)
    pa_hits = [h for h in hits if h["rule_id"] == "AI-PUNCT-ABUSE"]
    assert len(pa_hits) == 1
    assert pa_hits[0]["count"] == 7
    assert pa_hits[0]["rate"] > DEFAULT_DASH_THRESHOLD_PER_1K
    assert pa_hits[0]["severity"] == "error"  # 超过阈值 2 倍


def test_punct_abuse_custom_threshold():
    # 正文 100 字，3 处省略号 → 每千字 30 处
    prose = "他" * 94 + "……" * 3
    # 默认阈值 6：命中
    hits_default = scan_ai_patterns(prose)
    assert any(h["rule_id"] == "AI-PUNCT-ABUSE" for h in hits_default)
    # 阈值 50：不命中
    hits_high = scan_ai_patterns(prose, dash_threshold_per_1k=50)
    assert not any(h["rule_id"] == "AI-PUNCT-ABUSE" for h in hits_high)


def test_punct_abuse_no_hit():
    prose = "他走了，脚步很轻。她停在门口。"
    hits = scan_ai_patterns(prose)
    assert not any(h["rule_id"] == "AI-PUNCT-ABUSE" for h in hits)


# ---------------------------------------------------------------------------
# AI-EXPLAIN-TONE
# ---------------------------------------------------------------------------


def test_explain_tone_hit():
    prose = "因为天黑了，所以他回家。换句话说，今天到此结束。也就是说，明天再来。"
    hits = scan_ai_patterns(prose)
    et_hits = [h for h in hits if h["rule_id"] == "AI-EXPLAIN-TONE"]
    assert len(et_hits) == 1
    assert et_hits[0]["count"] >= 3


def test_explain_tone_requires_multiple():
    prose = "因为天黑了，所以他回家。"
    hits = scan_ai_patterns(prose)
    # 仅 1 处，仍会产生 hit，但 count=1
    et_hits = [h for h in hits if h["rule_id"] == "AI-EXPLAIN-TONE"]
    assert len(et_hits) == 1
    assert et_hits[0]["count"] == 1


def test_explain_tone_severity_upgrade():
    # 5 处以上升级为 error
    prose = "".join([f"因为{i}，所以{i}。换句话说{i}。" for i in range(3)])
    hits = scan_ai_patterns(prose)
    et_hits = [h for h in hits if h["rule_id"] == "AI-EXPLAIN-TONE"]
    assert et_hits[0]["severity"] == "error"


# ---------------------------------------------------------------------------
# 综合 / 边界
# ---------------------------------------------------------------------------


def test_multiple_rules_in_one_prose():
    prose = (
        "仿佛命运之手。他走了。他停下脚步。他转过身。\n\n"
        "这一刻，新的篇章开启了。——一切都结束了……"
    )
    hits = scan_ai_patterns(prose)
    rule_ids = {h["rule_id"] for h in hits}
    assert "AI-FORBIDDEN-WORD" in rule_ids
    assert "AI-PRONOUN-PILE" in rule_ids
    assert "AI-ENDING-SUMMARY" in rule_ids
    assert "AI-PUNCT-ABUSE" in rule_ids
