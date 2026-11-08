"""批次三十六 · 批 5：chapter-plan 状态闸门 + 落库字数优先级的突变规格。

为什么写（AGENTS §三 第 7 条的三类条件）
----------------------------------------
**命中条件①「门禁/闸门」**：``B36-C8-GATE`` 撤掉的是服务端章节状态闸门。
它失效的后果是 **API 直调可对 COMMITTED 章节重生成计划**（``plan_json`` 连同
``revision_note`` / ``gate_blocked`` 一起被覆盖）= 已定稿数据被静默改写，且前端
完全看不出（它只看自己的 ``EXPECTED_STATUS``）。这类失效必须有一个机械看守。

**命中条件②「跨层且极易被好心改错」**：``B36-C18-PRIORITY`` 撤掉的是「请求体显式
``target_word_count`` 最高」这一层。它是「作者显式意图 > 模型回吐 > 继承 > 默认」
的排序，看上去像一行可优化代码，改回原序不会有任何报错、只会让作者传的 2500
被 director 回吐的 3000 顶掉（「收下即忘」复发）。

判别口径
--------
两条都断言**真实值**（引擎调用次数 / 落库 ``plan_json.expected_word_count``），
不是「函数被调过」这种恒真断言。定向验证过：撤掉后新用例必红。
"""

MUTATIONS = [
    (
        "B36-C8-GATE",
        "packages/core/api/routers/workflows/start.py",
        "    _guard_plan_chapter_status(db_path, workflow_name, chapter_id)\n",
        "    # MUTATION-B36-C8: 撤掉 chapter-plan 状态闸门\n",
        "tests/unit/test_b36_plan_outline.py",
    ),
    (
        "B36-C18-PRIORITY",
        "packages/workflows/chapter_plan/pipeline.py",
        '    explicit_target = ctx.get("target_word_count")\n    if (\n',
        "    explicit_target = ctx.get(\"target_word_count\")\n    if False and (\n"
        "  # MUTATION-B36-C18: 撤掉「显式 target 最高」\n",
        "tests/unit/test_b36_plan_outline.py",
    ),
]
