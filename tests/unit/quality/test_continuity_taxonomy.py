"""critic 连续性分类法：提示词契约 + 规则 id 名册 + 下游出口。

三条防线（缺一条则分类法就会「声明了但没人看得见」）：

1. **提示词确实声明了分类法**（`docs/agents/prompts/critic-v1.md`）——闭集 id、
   `reason` 必填、`quote` 逐字、以及「恒 auto / 永不作自动改稿触发」的红线；
2. **id 名册是单源且与确定性算子一致**——`CRITIC_TAXONOMY` 的每个 id 都能对应
   契约校验器认可的 category（**不新增 category**），确定性算子的 id 在
   `DETERMINISTIC_CONTINUITY_RULE_IDS` 里且与 `continuity_time` 模块实际产出的
   rule_id 逐字一致；
3. **下游出口存在且只搬运可溯源字段**——`continuity_findings()`。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.agent_runtime import structured_output
from packages.core.quality import continuity_taxonomy as tax
from packages.core.quality.ai_patterns import AI_PATTERN_RULES
from packages.core.quality.continuity_time import scan_time_conflicts

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "docs" / "agents" / "prompts"

# 契约校验器认可的 category 闭集（`_validate_critic` 内引用；从模块取，不复制常量）。
_VALIDATOR_CATEGORIES = structured_output._VALIDATOR_ALLOWED_CATEGORIES


def _prompt() -> str:
    return (PROMPTS_DIR / "critic-v1.md").read_text(encoding="utf-8")


def _taxonomy_section() -> str:
    """§3.11 分类法那一段（到 §4 Forbidden 前）。

    **必须按段取**：分类 id 在 §7 的 schema 枚举里也出现一次，全文搜索会把「§3.11 里
    被删掉」这件事掩盖过去——实测（撤掉表格行的变异在全文搜索下测不出来）。
    """
    text = _prompt()
    start = text.index("11. **分类法（`rule_id`，必填")
    end = text.index("## 4. Forbidden")
    assert start < end
    return text[start:end]


# ---------------------------------------------------------------------------
# 1. 提示词契约
# ---------------------------------------------------------------------------


def test_prompt_declares_every_taxonomy_rule_id():
    """§3.11 的表里必须逐个列出闭集 id（防「代码有表、提示词没写」）。"""
    section = _taxonomy_section()
    missing = [rid for rid in tax.CRITIC_TAXONOMY if rid not in section]
    assert not missing, f"critic-v1.md §3.11 缺少分类法 id：{missing}"


def test_prompt_table_mapping_matches_the_registry():
    """id → category 的**配套关系**必须与代码名册逐条一致（表格行级对拍）。

    只查「id 出现过」不够：删掉表格行、id 仍可能留在下方的纪律句里，
    而 category 的配套关系已经丢了。
    """
    section = _taxonomy_section()
    drift = [
        (rid, cat)
        for rid, cat in tax.CRITIC_TAXONOMY.items()
        if f"| `{rid}` | `{cat}` |" not in section
    ]
    assert not drift, f"critic-v1.md §3.11 表格与 CRITIC_TAXONOMY 不一致：{drift}"
    assert "**必须配套**" in section, "§3.11 必须写明 rule_id 与 category 配套"


def test_taxonomy_section_pins_the_evidence_and_gate_rules():
    """§3.11 必须自带三条纪律：逐字引用、后果档恒 auto、不得作自动改稿/驳回触发器。"""
    section = _taxonomy_section()
    for token in ("逐字", "后果档恒为 `auto`", "自动改稿", "自动驳回"):
        assert token in section, f"critic-v1.md §3.11 缺纪律短语：{token}"


def test_prompt_requires_rule_id_and_reason_fields():
    """`rule_id` 与 `reason` 必须是**必填**字段（职责段与 schema 段都写死）。"""
    text = _prompt()
    for token in ("`rule_id`", "`reason`"):
        assert token in text, f"critic-v1.md 未声明 {token}"
    assert "每个 `issues[]` 元素 required 字段" in text
    assert "required 字段：`rule_id`, `category`, `severity`, `quote`, `reason`, `suggestion`" in text


def test_prompt_pins_quote_must_be_verbatim():
    """逐字引用是溯源过滤器的前提，必须在 §3.11 写明（不得改写 / 不得拼接）。"""
    assert "**必须逐字取自正文**" in _taxonomy_section()
    assert "子串机检" in _prompt()


def test_prompt_forbids_out_of_taxonomy_rule_ids():
    """越界 id / 与 category 不配套必须在 §4 Forbidden 里禁令化。"""
    text = _prompt()
    assert "§3.11 闭集之外的 `rule_id`" in text


def test_prompt_states_continuity_gate_is_auto_and_never_auto_repair():
    """红线：连续性类 findings 恒 auto、不得作自动改稿/驳回触发器。"""
    text = _prompt()
    assert "后果档恒为 `auto`" in text or "后果档恒 `auto`" in text
    assert "自动改稿" in text and "自动驳回" in text
    # 确定性算子 id 与 critic 自己的 rule_id 是两个命名空间，提示词要说清
    assert "CONT-CLOCK-DAYBREAK" in text and "CONT-TIME-BACKSTEP" in text


def test_prompt_keeps_e_crt_11_and_genre_contract():
    """新增 E-CRT-11 的同时不得破坏既有题材契约（防误删）。"""
    text = _prompt()
    assert "E-CRT-11" in text
    for marker in ("E-CRT-10", "可选输入：`genre_rubric`", "payoff_focus", "taboo_notes"):
        assert marker in text, f"critic-v1.md 丢失既有契约短语：{marker}"


# ---------------------------------------------------------------------------
# 2. 名册：单源、与契约兼容、与确定性算子一致
# ---------------------------------------------------------------------------


def test_taxonomy_categories_are_validator_approved():
    """分类法只能映射到契约校验器认可的 category——加新值等于改契约（非本批职责）。"""
    bad = {rid: cat for rid, cat in tax.CRITIC_TAXONOMY.items() if cat not in _VALIDATOR_CATEGORIES}
    assert not bad, f"分类法出现契约外的 category：{bad}"


def test_taxonomy_covers_continuity_and_existing_dimensions():
    """五条连续性分类 + 既有五类各有一个稳定 id。"""
    assert tax.CONTINUITY_RULE_IDS == {
        "CONT-TIMELINE",
        "CONT-UNANCHORED-REF",
        "CONT-REGISTER",
        "CONT-PROCEDURE",
        "CONT-BEAT-DEVIATION",
    }
    for rid in ("AI-FLAVOR", "PACE", "CHARACTER", "LOGIC", "FORESHADOW", "OTHER"):
        assert rid in tax.CRITIC_TAXONOMY


def test_deterministic_ids_match_the_operator_module():
    """确定性算子的 id 名册必须与 `continuity_time` **实际产出**的 rule_id 逐字一致。"""
    produced = {hit["rule_id"] for hit in scan_time_conflicts("凌晨两点十七分。\n\n天亮了。")}
    assert produced == {"CONT-CLOCK-DAYBREAK"}, produced
    assert "CONT-CLOCK-DAYBREAK" in tax.DETERMINISTIC_CONTINUITY_RULE_IDS
    assert "CONT-TIME-BACKSTEP" in tax.DETERMINISTIC_CONTINUITY_RULE_IDS
    # 两个 id 也必须在 AI_PATTERN_RULES 里登记（确定性侧的另一处）
    logged = {r.rule_id for r in AI_PATTERN_RULES}
    assert tax.DETERMINISTIC_CONTINUITY_RULE_IDS <= logged


def test_deterministic_ids_are_not_in_the_critic_closed_set():
    """两个命名空间不得混：确定性 id 不是 critic 要填的值。"""
    overlap = tax.DETERMINISTIC_CONTINUITY_RULE_IDS & set(tax.CRITIC_TAXONOMY)
    assert not overlap, f"确定性 id 混进 critic 闭集：{overlap}"


def test_is_continuity_rule_id_covers_both_namespaces():
    assert tax.is_continuity_rule_id("CONT-TIMELINE")
    assert tax.is_continuity_rule_id("CONT-CLOCK-DAYBREAK")
    assert not tax.is_continuity_rule_id("AI-FLAVOR")
    assert not tax.is_continuity_rule_id(None)
    assert tax.taxonomy_category("CONT-PROCEDURE") == "other"
    assert tax.taxonomy_category("NOT-A-RULE") is None


# ---------------------------------------------------------------------------
# 3. 下游出口
# ---------------------------------------------------------------------------


def test_continuity_findings_extracts_only_continuity_issues():
    """出口只提连续性条目、只搬五个字段（不臆造、不改写 LLM 原话）。"""
    issues = [
        {
            "rule_id": "CONT-TIMELINE",
            "category": "logic",
            "severity": "high",
            "quote": "窗外的天际线已经有一层淡淡的亮了",
            "reason": "同场景开场是凌晨两点十七分",
            "suggestion": "补一句时间跨度交代",
        },
        {
            "rule_id": "AI-FLAVOR",
            "category": "ai_flavor",
            "severity": "low",
            "quote": "竹影斜斜地落在青石地砖上",
            "reason": "模板状语",
            "suggestion": "换具体动作",
        },
        {"category": "logic", "severity": "high", "quote": "缺 rule_id 的老格式"},
        "not-a-dict",
    ]
    out = tax.continuity_findings(issues)
    assert len(out) == 1
    assert out[0]["rule_id"] == "CONT-TIMELINE"
    assert out[0]["quote"] == "窗外的天际线已经有一层淡淡的亮了"
    assert set(out[0]) == {"rule_id", "category", "severity", "quote", "suggestion"}
    assert tax.continuity_findings(None) == []


def test_continuity_findings_accepts_deterministic_ids_too():
    """确定性算子 id 若被上游塞进 issues，同样应被提出（宽松口径，只认命名空间）。"""
    out = tax.continuity_findings(
        [{"rule_id": "CONT-TIME-BACKSTEP", "quote": "亥时", "suggestion": "补交代"}]
    )
    assert len(out) == 1 and out[0]["rule_id"] == "CONT-TIME-BACKSTEP"
