"""质量域「rule_id 总账」——把「质量域一共有哪些 rule_id」变成可断言的对象。

2026-10-22 批次二十一 · novelos-dev-a。

要解决的问题
------------
项目 review（P1-4）实测：``packages/core/quality/`` 的**注册表口径**只有 **32** 条
（``AI_PATTERN_RULES`` 15 ＋ ``CRITIC_TAXONOMY`` 11 ＋
``DETERMINISTIC_CONTINUITY_RULE_IDS`` 2 ＋ ``BLOCKING_RULES`` 6 ＋
``CONFIRM_RULES`` 1，去重 32），但质量域**自己**还有 **24 个硬编码 rule_id**
（``guardrails`` 12 / ``payoff`` 8 / ``scoring`` 3 / ``style_trend`` 1），
**没有任何注册表能枚举它们** ⇒ 「质量域一共有哪些 rule_id」**拿不到总账**。

本文件把总账**钉住**：从源码字面量抽出全集 ⇒ 集合相等断言 ⇒ **新增 / 删除
/ 改名必红**。

口径（逐条写明，避免「换个口径就换个结论」）
--------------------------------------------
* **抽取范围**：``packages/core/quality/`` 下的 ``*.py``（**只取源码字面量**）。
* **rule_id 形态**：``(AI|CONT|GENRE)-`` 连字符型，或 ``(RULE|SCHEMA|SCORING)_``
  下划线型（正则锚定）。
* **明确排除**（它们**不是** rule_id 形态，不进台账）：
  ``continuity_taxonomy.CRITIC_TAXONOMY`` 的 5 个**分类法 id**
  （``PACE`` / ``LOGIC`` / ``CHARACTER`` / ``FORESHADOW`` / ``OTHER``）、
  以及 ``issues.scoring_missing_subscore``（小写哨兵）。
  ⇒ 故台账 **N = 50**（注册表内 26 ＋ 注册表外 24）。
* **注册表口径**：``AI_PATTERN_RULES`` ∪ ``CRITIC_TAXONOMY`` ∪
  ``DETERMINISTIC_CONTINUITY_RULE_IDS`` ∪ ``BLOCKING_RULES`` ∪ ``CONFIRM_RULES``。
* **签发点口径**：某个**调用**把该 rule_id 放进 ``rule_id=`` 关键字参数，或放进
  字典项 ``"rule_id":``；值表达式允许经**模块级字符串常量** / **局部变量** /
  **三元表达式**解析（**流不敏感上界**）⇒ 这是「上界」，可能把「只是被赋值、
  从未真正签发」也算进去（已知会高估的只有本仓一处形态，见下）。
* **已知缺口**：有 **6 个** rule_id **没有 Python 签发点**——它们全是
  ``CRITIC_TAXONOMY`` 的**键**（``AI-FLAVOR`` / ``CONT-BEAT-DEVIATION`` /
  ``CONT-PROCEDURE`` / ``CONT-REGISTER`` / ``CONT-TIMELINE`` /
  ``CONT-UNANCHORED-REF``）⇒ 由 **LLM critic** 产出（模型填 ``rule_id``），
  Python 不签发 ⇒ **列为已知缺口，不硬凑**（本文件显式登记它们）。

⇒ 断言「每个 rule_id 至少有一个签发点 **或** 被显式登记为已知缺口」：
新增一个**没有签发点**的 rule_id ⇒ 必红；已知缺口被补上签发点 ⇒ **也必红**
（提示维护者更新总账）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from packages.core import quality as _pkg

#: rule_id 字面量形态（口径见模块 docstring）。
_RULE_ID_RE = re.compile(r"^(?:AI|CONT|GENRE)-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
_RULE_ID_UNDERSCORE_RE = re.compile(r"^(?:RULE|SCHEMA|SCORING)_[A-Z0-9]+(?:_[A-Z0-9]+)+$")

#: 台账：``rule_id -> (所在文件元组, 是否在注册表内)``。
#: **集合相等** ⇒ 新增 / 删除 / 改名必红；「所在文件」与「注册表内外」两条
#: 成员关系也逐条钉住 ⇒ 规则被挪文件、或从注册表进出，都必红。
_LEDGER: dict[str, tuple[tuple[str, ...], bool]] = {
    "AI-ANTHRO-VEHICLE": (("ai_patterns.py", "ai_patterns_paragraph.py"), True),
    "AI-BEAT-REPEAT": (("ai_patterns.py", "beat_repeat.py", "engine.py", "issues.py"), True),
    "AI-CONTRAST-PAIR": (("ai_patterns.py", "ai_patterns_sentence.py"), True),
    "AI-DIALOGUE-ECHO": (("ai_patterns.py", "ai_patterns_dialogue.py"), True),
    "AI-DIALOGUE-LOW": (("ai_patterns.py", "ai_patterns_dialogue.py", "engine.py"), True),
    "AI-ENDING-SUMMARY": (("ai_patterns.py",), True),
    "AI-EXPLAIN-TONE": (("ai_patterns.py", "ai_patterns_sentence.py"), True),
    "AI-FLAVOR": (("continuity_taxonomy.py",), True),
    "AI-FORBIDDEN-WORD": (("ai_patterns.py", "ai_patterns_sentence.py"), True),
    "AI-LONG-PARA": (("ai_patterns.py", "ai_patterns_paragraph.py"), True),
    "AI-PRONOUN-PILE": (("ai_patterns.py", "ai_patterns_sentence.py"), True),
    "AI-PUNCT-ABUSE": (("ai_patterns.py", "ai_patterns_sentence.py"), True),
    "AI-SHORT-PARA": (("ai_patterns.py", "ai_patterns_paragraph.py"), True),
    "AI-TRIPLET-OPENING": (("ai_patterns.py", "ai_patterns_sentence.py"), True),
    "CONT-BEAT-DEVIATION": (("continuity_taxonomy.py",), True),
    "CONT-CLOCK-DAYBREAK": (("ai_patterns.py", "continuity_taxonomy.py", "continuity_time.py"), True),
    "CONT-PROCEDURE": (("continuity_taxonomy.py",), True),
    "CONT-REGISTER": (("continuity_taxonomy.py",), True),
    "CONT-TIME-BACKSTEP": (("ai_patterns.py", "continuity_taxonomy.py", "continuity_time.py"), True),
    "CONT-TIMELINE": (("continuity_taxonomy.py",), True),
    "CONT-UNANCHORED-REF": (("continuity_taxonomy.py",), True),
    "RULE_CHAR_BEFORE_MISMATCH": (("guardrails.py", "scoring.py"), False),
    "RULE_CHAR_DEAD_ACTIVE": (("guardrails.py", "issues.py", "scoring.py"), True),
    "RULE_FORESHADOW_NO_HOOKS": (("scoring.py",), False),
    "RULE_H1_NO_END_HOOK": (("payoff.py",), False),
    "RULE_H2_NO_CLIMAX_3CH": (("payoff.py",), False),
    "RULE_H3_FILLER_2CH": (("payoff.py",), False),
    "RULE_H3_FILLER_3CH": (("payoff.py",), False),
    "RULE_H4_NO_CLIMAX": (("payoff.py",), False),
    "RULE_H4_NO_EARLY_CONFLICT": (("payoff.py",), False),
    "RULE_H4_NO_HOOK": (("payoff.py",), False),
    "RULE_H5_REALM_INCONSISTENT": (("payoff.py",), False),
    "RULE_KNOWLEDGE_LEAK": (("guardrails.py", "scoring.py"), False),
    "RULE_PLOT_NO_PLAN": (("scoring.py",), False),
    "RULE_Q6_NGRAM_OVERLAP": (("guardrails.py",), False),
    "RULE_Q6_OVERLAP_RATE": (("guardrails.py", "issues.py"), True),
    "RULE_Q7_AI_MARKERS": (("guardrails.py",), False),
    "RULE_Q7_FLAT_SENTENCE": (("guardrails.py",), False),
    "RULE_Q7_PARA_OPENER": (("guardrails.py",), False),
    "RULE_Q8_HUMAN_RATIO_LOW": (("guardrails.py", "issues.py"), True),
    "RULE_Q8_HUMAN_RATIO_LOW_WARN": (("guardrails.py",), False),
    "RULE_Q8_NO_DATA": (("guardrails.py",), False),
    "RULE_Q8_STATS_NOTE": (("guardrails.py",), False),
    "RULE_STYLE_REPETITION_TRIGRAM": (("scoring.py",), False),
    "RULE_STYLE_TREND_DECAY": (("style_trend.py",), False),
    "RULE_TIMELINE_NEGATIVE_DAY": (("guardrails.py", "scoring.py"), False),
    "RULE_TIMELINE_NON_MONOTONIC": (("guardrails.py", "scoring.py"), False),
    "RULE_WORLD_HARD_RULE_CHANGED": (("guardrails.py", "issues.py", "scoring.py"), True),
    "RULE_WORLD_SOFT_RULE_CHANGED": (("guardrails.py", "scoring.py"), False),
    "SCHEMA_VALIDATION_FAILED": (("guardrails.py", "issues.py"), True),
}

#: **已知缺口**：没有 Python 签发点的 rule_id——全部是 ``CRITIC_TAXONOMY`` 的键，
#: 由 **LLM critic** 产出（模型填 ``rule_id``），Python 侧不签发。
#: ⇒ 显式登记，**不硬凑**；其中任意一条被补上签发点 ⇒ 本文件必红（提示更新总账）。
_KNOWN_ISSUANCE_GAPS = frozenset(
    {
        "AI-FLAVOR",
        "CONT-BEAT-DEVIATION",
        "CONT-PROCEDURE",
        "CONT-REGISTER",
        "CONT-TIMELINE",
        "CONT-UNANCHORED-REF",
    }
)


# ---------------------------------------------------------------------------
# 抽取器（口径见模块 docstring；与生成台账时用的是同一套逻辑）
# ---------------------------------------------------------------------------


def _literals_of(node: ast.AST, consts: dict[str, set[str]], local: dict[str, set[str]]) -> set[str]:
    """表达式里可能出现的字符串字面量（经模块常量 / 局部变量 / 三元解析一层）。"""
    out: set[str] = set()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.add(node.value)
    elif isinstance(node, ast.Name):
        out |= consts.get(node.id, set()) | local.get(node.id, set())
    elif isinstance(node, ast.IfExp):
        out |= _literals_of(node.body, consts, local) | _literals_of(node.orelse, consts, local)
    elif isinstance(node, ast.Subscript):
        out |= _literals_of(node.value, consts, local)
    return out


def _scan_package() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """扫 ``packages/core/quality/*.py`` ⇒ (字面量出现处, 签发点)。"""
    pkg_dir = Path(_pkg.__file__).parent
    literal_files: dict[str, set[str]] = {}
    issuance: dict[str, set[str]] = {}
    for path in sorted(pkg_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        consts: dict[str, set[str]] = {}
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                if (
                    isinstance(target, ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    consts[target.id] = {node.value.value}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and (_RULE_ID_RE.match(node.value) or _RULE_ID_UNDERSCORE_RE.match(node.value))
            ):
                literal_files.setdefault(node.value, set()).add(path.name)
        for func in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            local: dict[str, set[str]] = {}
            for assign in ast.walk(func):
                if isinstance(assign, ast.Assign) and isinstance(assign.targets[0], ast.Name):
                    values: set[str] = set()
                    for value in (
                        assign.value.elts
                        if isinstance(assign.value, (ast.Tuple, ast.List))
                        else [assign.value]
                    ):
                        values |= _literals_of(value, consts, local)
                    local.setdefault(assign.targets[0].id, set()).update(values)
            for node in ast.walk(func):
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if kw.arg == "rule_id":
                            for value in _literals_of(kw.value, consts, local):
                                issuance.setdefault(value, set()).add(f"{path.name}:{node.lineno}")
                if isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values):
                        if isinstance(key, ast.Constant) and key.value == "rule_id":
                            for lit in _literals_of(value, consts, local):
                                issuance.setdefault(lit, set()).add(f"{path.name}:{node.lineno}")
    return literal_files, issuance


def _registry_rule_ids() -> set[str]:
    """注册表口径（见模块 docstring）。"""
    from packages.core.quality import ai_patterns, continuity_taxonomy, issues

    registry = {r.rule_id for r in ai_patterns.AI_PATTERN_RULES}
    registry |= set(continuity_taxonomy.CRITIC_TAXONOMY)
    registry |= set(continuity_taxonomy.DETERMINISTIC_CONTINUITY_RULE_IDS)
    registry |= set(issues.BLOCKING_RULES)
    registry |= set(issues.CONFIRM_RULES)
    return registry


# ---------------------------------------------------------------------------
# 断言
# ---------------------------------------------------------------------------


def test_rule_id_ledger_is_exact():
    """① **总账集合相等**：源码里出现的 rule_id 全集 == 钉住的台账 ⇒ 增/删/改名必红。

    这是本文件的主断言：它把「质量域一共有哪些 rule_id」从**没人说得清**变成
    **一个必须显式更新才能通过的数字**（当前 N = 50）。
    """
    literal_files, _ = _scan_package()
    actual = set(literal_files)
    expected = set(_LEDGER)
    assert actual == expected, (
        "质量域 rule_id 全集变了："
        f"多出 {sorted(actual - expected)} / 少了 {sorted(expected - actual)}"
        f"（钉住 {len(expected)} 条，实测 {len(actual)} 条）"
    )


def test_rule_id_file_membership_matches_the_ledger():
    """② 每个 rule_id 的**所在文件**与台账一致 ⇒ 规则被挪文件必红。"""
    literal_files, _ = _scan_package()
    wrong = {
        rid: (sorted(literal_files.get(rid, set())), sorted(_LEDGER[rid][0]))
        for rid in _LEDGER
        if literal_files.get(rid, set()) != set(_LEDGER[rid][0])
    }
    assert not wrong, f"rule_id 所在文件与台账不符（实测 vs 台账）：{wrong}"


def test_rule_id_registry_membership_matches_the_ledger():
    """③ 每个 rule_id 的「在注册表内 / 外」与台账一致 ⇒ 规则进出注册表必红。

    当前：**26 条在注册表内 ＋ 24 条注册表外（硬编码）**。
    ⇒ 这 24 条硬编码就是「拿不到总账」的根因——本条让它们的数量与身份
    **变成显式的、可被 review 的数字**。
    """
    registry = _registry_rule_ids()
    wrong = {
        rid: (rid in registry, _LEDGER[rid][1])
        for rid in _LEDGER
        if (rid in registry) != _LEDGER[rid][1]
    }
    assert not wrong, f"rule_id 的注册表内外状态与台账不符（实测 vs 台账）：{wrong}"
    in_registry = sum(1 for rid in _LEDGER if _LEDGER[rid][1])
    assert in_registry == 26, f"注册表内 rule_id 数变了：{in_registry}（台账钉住 26）"
    assert len(_LEDGER) - in_registry == 24, "注册表外（硬编码）rule_id 数变了"


def test_every_rule_id_has_an_issuance_point_or_is_a_known_gap():
    """④ 每个 rule_id 至少有一个**签发点**，或被显式登记为**已知缺口**。

    ⇒ 新增一个「只登记、不签发」的 rule_id 必红（它既不在台账、也不在已知缺口）。
    ⇒ 已知缺口若被补上签发点，**也必红**（提示维护者更新总账 ⇒ 缺口只能被关闭，
    不能被遗忘）。
    """
    literal_files, issuance = _scan_package()
    missing = {rid for rid in literal_files if not issuance.get(rid)}
    assert missing == set(_KNOWN_ISSUANCE_GAPS), (
        "无签发点的 rule_id 集合变了："
        f"实测 {sorted(missing)} / 台账登记 {sorted(_KNOWN_ISSUANCE_GAPS)}"
        "（新增无签发点的规则必登记为已知缺口；缺口被补上签发点后请更新总账）"
    )
