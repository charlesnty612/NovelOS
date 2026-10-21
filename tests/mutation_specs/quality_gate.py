"""质量门禁**判定链**的突变规格（2026-10-21 · novelos-dev-a）。

补的缺口
--------
本仓的突变覆盖此前只覆盖了 ``packages/core/quality/`` 里的 4 个文件
（``ai_patterns*.py`` 一族），**门禁判定链本身**（谁决定「这章能不能提交」）
没有规格 ⇒ 「门禁被改弱」这一类失效无人看守。

本文件取五处**判定点**，每处都是「撤掉就静默降级、且不抛错」的形状：

* ``Q-BLOCK-1``（``aggregate.py``）——blocking error 是否把 ``overall`` 归零；
* ``Q-CONFIRM-1``（``issues.py``）——``CONFIRM_RULES`` 表内规则是否解析为 confirm；
* ``Q-WIRING-1``（``engine.py``）——AI 模式命中是否真的进了质量报告（接线层）；
* ``Q-BEAT-1``（``beat_repeat.py``）——节拍复现算子是否真的产出命中；
* ``Q-SCORE-1``（``scoring.py``）——trigram 超 confirm 阈值时是否把该条**单条上修**。

为什么必须盯这条链
------------------
门禁的后果轴（``auto`` / ``confirm`` / ``block``）与 severity **正交**
（``quality/issues.py`` 的 ``Gate``），所以链上任一环被撤掉都**不会报错**：

* 撤掉 block 判定 ⇒ 结构性损坏照样提交（AGENTS 坑区「算出来的问题没人能说『不』」）；
* 撤掉 confirm 判定 ⇒ P0-1 事故复发（9 段逐字重复、``overall: 90``、照常提交）；
* 撤掉接线 ⇒ 规则「登记了却不生效」（同一形状的另一支）；
* 撤掉单条上修 ⇒ trigram 事故量级退化为「人人必点」的橡皮图章，或反过来人人免签。

**实测结果（本文件每条都手工改源码 → 跑定向用例 → 看红 → 还原）**
------------------------------------------------------------------
| id | 目标 | 锚点唯一命中 | 手工实测 | 红在哪个用例 |
|---|---|---|---|---|
| Q-BLOCK-1 | ``packages/core/quality/aggregate.py:229`` | 1 | **红** | ``tests/unit/quality/test_issue_gate.py::test_block_still_zeroes_and_wins_over_confirm`` |
| Q-CONFIRM-1 | ``packages/core/quality/issues.py:300`` | 1 | **红** | ``tests/unit/quality/test_issue_gate.py::test_confirm_rule_id_resolves_to_confirm_regardless_of_severity`` |
| Q-WIRING-1 | ``packages/core/quality/engine.py:237`` | 1 | **红** | ``tests/unit/quality/test_ai_pattern_wiring.py::test_beat_repeat_reaches_the_quality_report`` |
| Q-BEAT-1 | ``packages/core/quality/beat_repeat.py:307`` | 1 | **红** | ``tests/unit/quality/test_ai_pattern_wiring.py::test_beat_repeat_reaches_the_quality_report`` |
| Q-SCORE-1 | ``packages/core/quality/scoring.py:371`` | 1 | **红** | ``tests/unit/quality/test_trigram_tiers.py::test_incident_band_0335_is_confirm_tier`` |

实测方式 = ``tests/mutation_specs/run_spec.py``（``copy2`` 备份 → 改写 → 跑定向用例
→ ``finally`` 还原 → SHA256 核对）。五条全部 ``[CAUGHT]``，**无存活、无中止**；
六个被测文件还原后 SHA256 与跑前逐位一致，无 ``.mutbak`` 残留。

⚠️ 本文件只声明「这些突变会被捕获」；**不代表门禁链没有别的漏点**——
「有规格」与「有覆盖」是两件事（坑区：「断言了结果 ≠ 判别了机制」）。
"""

MUTATIONS = [
    # ------------------------------------------------------------------
    # ① blocking error ⇒ overall = 0（V3.9 批次 3.1 的「悬崖」口径）
    # 撤掉它 ⇒ 结构性问题只进 issues 列表、分数照给，照常提交。
    # ------------------------------------------------------------------
    (
        "Q-BLOCK-1",
        "packages/core/quality/aggregate.py",
        "    blocked = any(is_blocking_issue(it) for it in issues)",
        "    blocked = False  # MUT: blocking error 不再把 overall 归零",
        "tests/unit/quality/test_issue_gate.py",
    ),
    # ------------------------------------------------------------------
    # ② ``rule_id ∈ CONFIRM_RULES`` ⇒ gate="confirm"（P0-1 后果轴第 2 条）
    # 撤掉它 ⇒ 表内规则退化为 auto ⇒ 「不许静默通过」这条约束静默失效。
    # ------------------------------------------------------------------
    (
        "Q-CONFIRM-1",
        "packages/core/quality/issues.py",
        "    if getattr(issue, \"rule_id\", None) in CONFIRM_RULES:\n        return \"confirm\"",
        "    if False:  # MUT: CONFIRM_RULES 分支失效\n        return \"confirm\"",
        "tests/unit/quality/test_issue_gate.py",
    ),
    # ------------------------------------------------------------------
    # ③ 接线层：``scan_ai_patterns`` 的命中是否真的进了 ``QualityReport.issues``
    # 撤掉它 ⇒ 规则侧照常产出、报告侧一条不落 ⇒ 「登记了却不生效」。
    # ------------------------------------------------------------------
    (
        "Q-WIRING-1",
        "packages/core/quality/engine.py",
        "        issues.extend(ai_pattern_issues(ctx.draft or \"\", chapter_id=ctx.chapter_id))",
        "        pass  # MUT: 撤掉 ai_pattern_issues 接线",
        "tests/unit/quality/test_ai_pattern_wiring.py",
    ),
    # ------------------------------------------------------------------
    # ④ 算子层：节拍复现（AI-BEAT-REPEAT）是否真的产出命中
    # 撤掉它 ⇒ 唯一在 CONFIRM_RULES 里的确定性算子恒不命中 ⇒ 表成了死表。
    # ------------------------------------------------------------------
    (
        "Q-BEAT-1",
        "packages/core/quality/beat_repeat.py",
        "    if not prose:\n        return []",
        "    if True:  # MUT: 扫描恒返回空列表\n        return []",
        "tests/unit/quality/test_ai_pattern_wiring.py",
    ),
    # ------------------------------------------------------------------
    # ⑤ 单条上修：trigram 超 confirm 阈值 ⇒ 该条 gate="confirm"
    # 撤掉它 ⇒ 事故量级（0.3037）不再要求签字 ⇒ 门禁退化为橡皮图章。
    # ------------------------------------------------------------------
    (
        "Q-SCORE-1",
        "packages/core/quality/scoring.py",
        "        needs_confirm = trigram_rate > STYLE_TRIGRAM_CONFIRM_THRESHOLD",
        "        needs_confirm = False  # MUT: trigram 不再上修为 confirm",
        "tests/unit/quality/test_trigram_tiers.py",
    ),
]
