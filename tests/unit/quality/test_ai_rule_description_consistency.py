"""规则 description 与实现模块 docstring 的数字必须一致（2026-10-19 批次十三）。

缺陷形状
--------
「**同一事实在两处写着不同的数**」——本仓已登记两次同族事故：
① 同一字段声明在两个模型里同形（``retry_count: int`` 出现两次）；
② 同一份报告里两个「最新」（``version DESC`` vs ``created_at DESC``）。

本批抓到的是第三处，但形状相同：**``CONT-TIME-BACKSTEP`` 规则的 description 写
「原始 6 条候选中 5 条是误报」，而算子实现模块的 docstring 写「原始 8 条候选里
只有 1 条真（precision 12.5%）」** —— 6 候选 ⇒ 1/6 ≈ 16.7%，与 12.5% 不符。

**为什么能定案而不必复算**
-----------------------
模块 docstring 里**逐条列出了误报实例**（段首戳 5 条 + 右邻期限语 1 条 +
跨午夜顺行 1 条 = **7 条**误报）⇒ 7 + 1 条真 = 8，与 12.5% = 1/8 逐位吻合。
纯靠仓内已登记的判档表即可定案——AGENTS §一 硬约束 1 禁止指向 dev 库既有项目
复算，所以「必须复算」这条路本来就不通。

**为什么值得钉一条测试**
----------------------
description 是**模型与维护者都会读**的权威说明；它与实现模块的判档表分叉时，
下一个人会照 description 去调守卫（"6 条候选里 5 条是误报 ⇒ 误报率 83%"），
而真实误报率是 87.5%（7/8）⇒ **照着错数字做决策**。

判别
----
本用例比较的是**两处文本里的同一个量**，不是「文本里有没有某个数」；
两边都换掉（或只换一边）都必红。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from packages.core.quality.ai_patterns import AI_PATTERN_RULES

ROOT = Path(__file__).resolve().parents[3]

#: rule_id → 该算子实现模块的源文件（相对仓库根）。
#: 规则 description 里的「候选数」必须与该模块 docstring 登记的判档表一致。
_RULE_DOCSTRING_SOURCES = {
    "CONT-TIME-BACKSTEP": "packages/core/quality/continuity_time.py",
}

#: description 侧：「原始 N 条候选中 M 条是误报」
_DESC_RE = re.compile(r"原始\s*(\d+)\s*条候选中\s*(\d+)\s*条是误报")
#: docstring 侧：「原始 N 条候选里只有 T 条真」
_DOC_RE = re.compile(r"原始\s*(\d+)\s*条候选里只有\s*(\d+)\s*条真")


def _rule_description(rule_id: str) -> str:
    for rule in AI_PATTERN_RULES:
        if rule.rule_id == rule_id:
            return rule.description
    raise AssertionError(f"AI_PATTERN_RULES 里没有 {rule_id}")


def _module_docstring(rel_path: str) -> str:
    src = (ROOT / rel_path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    doc = ast.get_docstring(tree)
    assert doc, f"{rel_path} 没有模块 docstring"
    return doc


def test_rule_description_candidate_count_matches_module_docstring():
    for rule_id, rel_path in _RULE_DOCSTRING_SOURCES.items():
        desc = _rule_description(rule_id)
        doc = _module_docstring(rel_path)

        desc_m = _DESC_RE.search(desc)
        assert desc_m, (
            f"{rule_id} 的 description 里找不到「原始 N 条候选中 M 条是误报」——"
            f"本用例守的是两个来源的**同一个量**，描述句式若被改写，"
            f"要么同步改本用例，要么该条判据已失效（不许静默放过）\n    desc={desc!r}"
        )
        doc_m = _DOC_RE.search(doc)
        assert doc_m, (
            f"{rel_path} 的模块 docstring 里找不到「原始 N 条候选里只有 T 条真」"
            f"\n    doc={doc[:400]!r}"
        )

        n_desc, m_desc = int(desc_m.group(1)), int(desc_m.group(2))
        n_doc, t_doc = int(doc_m.group(1)), int(doc_m.group(2))

        assert n_desc == n_doc, (
            f"{rule_id} 的候选数在两处不同：description 说 {n_desc} 条、"
            f"{rel_path} 的 docstring 说 {n_doc} 条 ⇒ 照错数字调守卫会调偏"
        )
        assert m_desc + t_doc == n_desc, (
            f"{rule_id} 的候选数内部不自洽：{m_desc} 条误报 + {t_doc} 条真 "
            f"= {m_desc + t_doc}，但 description 声称共 {n_desc} 条"
        )
