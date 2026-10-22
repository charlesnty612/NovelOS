r"""质量域突变规格 · 8 条 Guardrail（2026-10-22 · novelos-dev-a）。

补的缺口
--------
``packages/core/quality/guardrails.py``（808 行，本仓质量域**最大**的文件）此前
**零突变规格**——实测（2026-10-22，``HEAD=d06a79a``）13 个质量域模块无任何突变规格，
它是其中之一。

挑哪几条（判据：机制核心 + 被误改概率高 + 撤掉后**静默降级不报错**）
------------------------------------------------------------------
* **QG-Q8-DEF-1**——Q8 人工占比不足时的 **severity 默认值**。
  撤掉它 ⇒ 纯 AI 章（human_ratio=0）默认被判 **error** ⇒ 在 enforce 默认下
  **全拦**。这是 2026-09-21 批次 3.3 的裁决：默认 warning、仅作者显式
  ``NOVELOS_QUALITY_Q8_STRICT=1`` 才升级 error（AGENTS 坑区「可测算子只作体检、
  不作处方」同族：把一个可优化指标升成硬门禁 ⇒ 工具自身变成负担）。
* **QG-Q8-STRICT-1**——**env 开关本身**失效。
  撤掉它 ⇒ 作者设了 ``NOVELOS_QUALITY_Q8_STRICT=1`` 也**不生效** ⇒
  「显式升级为 error 阻断」这条承诺静默失效（与 ① 方向相反：一个过度拦、
  一个升不上去，**两条都要钉**，否则「默认值」与「开关」任一侧被改都无人发现）。
* **QG-Q7-OPENER-1**——「段落首词占比」指标的计数被撤。
  撤掉它 ⇒ 该指标恒为 0 ⇒ Q7 的「然而/但是 段首词占比」这条**永远不会触发**，
  零告警（规则「登记了却不生效」的同族）。
* **QG-PARA-1**——``guardrails`` 自有的段切分实现与全仓唯一入口**分叉**。

⚠️ **一条「等价突变」与它导致的先验判断被推翻（2026-10-22 批次十九）**
--------------------------------------------------------------------
``guardrails._paragraphs`` 有**一份独立于** ``ai_patterns_text._split_paragraphs``
的段切分实现（外层 ``\n\s*\n``，内层再 ``p.split("\n")``）。
曾把它作为 ``QG-PARA-1`` 放进本文件**实跑**（anchor
``    parts = re.split(r"\n\s*\n", draft)`` → replacement
``    parts = re.split(r"\n\n", draft)``）⇒ **实测 [SURVIVED]**。

**首轮我判它是「缺守门测试」——该判断错了（先验判断被推翻，必须留痕）**：
2026-10-22 用 20,025 个输入（定向边界样本 + 随机 fuzz）逐位复算，发现
``guardrails._paragraphs`` 与 ``_split_paragraphs`` 的输出**零处不一致**——
因为内层的 ``p.split("\n")`` 把外层分块**再按单换行切一遍**，净结果就是
「任意换行都切」⇒ 与唯一入口**逐字相同**。⇒ 那条突变是**等价突变**
（改它不改变任何可观测行为）⇒ **任何测试都抓不到它**，不是「缺守门测试」。
（此前本仓登记的「两套口径 / 口径分叉」结论据此作废。）

⇒ 因此本文件里那条突变**换成了可观测的等价物**：撤掉内层 ``p.split("\n")``
（⇒ 单换行不再切段 ⇒ 两份实现分叉 ⇒ 可观测），守门用例是
``tests/unit/quality/test_guardrails.py
::test_guardrails_paragraph_split_matches_the_single_entry``
（钉「两份实现不得分叉」这条不变式）。

⚠️ 另附一条给 Lead 的输入：既然两份实现**结果等价**，那么「收敛成单点」是
**零行为变更**的重构 ⇒ **不需要**重算 guardrails 8 条规则的阈值（那才是
「无阈值重测就勿合并」顾虑的来源）⇒ 裁决的输入已变，请复核。

⚠️ 本文件只声明「这些突变会被捕获」；**不代表 8 条 guardrail 都有覆盖**——
「有规格」与「有覆盖」是两件事。
"""

MUTATIONS = [
    # ------------------------------------------------------------------
    # ① Q8 默认 severity：必须从 issues.MVP_RULE_OVERRIDES 取（唯一权威）
    # ------------------------------------------------------------------
    (
        "QG-Q8-DEF-1-q8-default-severity-hardcoded-to-error",
        "packages/core/quality/guardrails.py",
        '    return rule_default_severity("RULE_Q8_HUMAN_RATIO_LOW", "compliance")',
        '    return "error"',
        "tests/unit/quality/test_guardrails.py",
    ),
    # ------------------------------------------------------------------
    # ② env 开关失效：NOVELOS_QUALITY_Q8_STRICT=1 不再升级为 error
    # ------------------------------------------------------------------
    (
        "QG-Q8-STRICT-1-q8-strict-env-switch-dead",
        "packages/core/quality/guardrails.py",
        "    if _q8_strict_enabled():",
        "    if False:",
        "tests/unit/quality/test_guardrails.py",
    ),
    # ------------------------------------------------------------------
    # ③ Q7 段落首词计数被撤 ⇒ 该指标恒 0 ⇒ 规则永不触发
    # ------------------------------------------------------------------
    (
        "QG-Q7-OPENER-1-q7-paragraph-opener-count-zeroed",
        "packages/core/quality/guardrails.py",
        '        opener_count = sum(1 for p in paras if p.startswith("然而") or p.startswith("但是"))',
        "        opener_count = 0",
        "tests/unit/quality/test_guardrails.py",
    ),
    # ------------------------------------------------------------------
    # ④ guardrails 自有的段切分实现：与唯一入口**分叉**（撤掉内层单换行细分）
    # ⚠️ 曾计划用「外层分隔符 \n\s*\n → \n\n」作这条突变 ⇒ **实测是等价突变**
    #    （改完行为一字不变 ⇒ 任何测试都抓不到它）⇒ 换成这条**可观测**的。
    # ------------------------------------------------------------------
    (
        "QG-PARA-1-guardrails-paragraph-split-diverges-from-single-entry",
        "packages/core/quality/guardrails.py",
        '        for sub in p.split("\\n"):',
        "        for sub in [p]:",
        "tests/unit/quality/test_guardrails.py",
    ),
]
