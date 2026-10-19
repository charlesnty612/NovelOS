"""规则 description ↔ 实现模块 docstring 的数字一致性（2026-10-19 批次十三）。

事故
----
``CONT-TIME-BACKSTEP`` 的规则 description 写「原始 **6** 条候选中 **5** 条是误报」，
而算子实现模块 ``continuity_time.py`` 的 docstring 写「原始 **8** 条候选里只有
**1** 条真（precision 12.5%）」——6 候选 ⇒ 1/6 ≈ 16.7%，与 12.5% 不符。
两处是**同一个量**（时点回退这条候选检查的原始候选数），却写着两个数。

裁决依据（未复算）
------------------
模块 docstring **逐条列出了误报实例**：段首戳 5 条 + 右邻期限语 1 条 +
跨午夜顺行 1 条 = 7 条误报 ⇒ 7 + 1 = 8，与 12.5% = 1/8 逐位吻合 ⇒ 8 是真值。
（AGENTS §一 硬约束 1 禁止指向 dev 库既有项目复算，故只能靠仓内已登记的判档表定案。）

突变
----
**DESC-1** 把 description 改回「6 条候选中 5 条是误报」
⇒ ``test_rule_description_candidate_count_matches_module_docstring`` 必红。
"""

MUTATIONS = [
    (
        "DESC-1",
        "packages/core/quality/ai_patterns.py",
        '"实测：原始 8 条候选中 7 条是误报（上列三类守卫正是抽样抓出来的），"',
        '"实测：原始 6 条候选中 5 条是误报（上列三类守卫正是抽样抓出来的），"  # MUTATION-DESC-1',
        "tests/unit/quality/test_ai_rule_description_consistency.py",
    ),
]
