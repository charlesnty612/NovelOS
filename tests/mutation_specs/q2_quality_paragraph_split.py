r"""质量域突变规格 · 段切分口径单点（2026-10-22 · novelos-dev-a）。

补的缺口
--------
``packages/core/quality/ai_patterns_text.py`` 是「段切分口径的**唯一单点**」
（``_split_paragraphs``，``\n+``），而它此前**零突变规格**——实测（2026-10-22，
``HEAD=d06a79a``）13 个质量域模块无任何突变规格，它是其中之一。

为什么这条单点必须有人看守
--------------------------
「人类基线 0.00/千字，全弧零出现」那次事故（AGENTS 坑区）的真因是**量具口径**
与语料形态不匹配：人类锚点书**一行一段**，而当时 ``count_short_paras`` 独自用
``\n\n`` 切段 ⇒ 19 章被读成 19 个整章大段 ⇒ 规则**构造上不可能命中**。
修复动作是把段级算子收敛到唯一入口 ``_split_paragraphs``（``\n+``）。
⇒ 「这个单点被改回去」是**已发生过的事故的回归**，不是假想风险。

本文件四条突变打**四个不同的失效方向**，不是同一条改四遍：

* **QS-SPLIT-1**（``ai_patterns_text.py``）——单点本身的**口径回退**（``\n+``→``\n\n``）。
* **QS-SPLIT-2**（``ai_patterns_paragraph.py``）——**短段族**绕过单点、内联第二份切分。
* **QS-SPLIT-3**（``ai_patterns_paragraph.py``）——**长段族**绕过单点、内联第二份切分。
* **QS-SPLIT-4**（``ai_patterns_text.py``）——**最小判定长度**被撤（600→0）。

为什么 QS-SPLIT-2/3 的 replacement 刻意写成 ``prose.split("\n\n")`` 这个**字面形态**
------------------------------------------------------------------------------
``tests/unit/quality/test_readability_rules.py`` 的形状级断言正是按这个字面扫
``ai_patterns*.py`` **全集**（非注释行里找 ``prose.split("\n\n")``）⇒ 内联切分一旦
出现**立刻红**，且报点名文件与行号。该断言配套一条**名册断言**，防止**域静默缩小**
（AGENTS 坑区「看守自己被悄悄缩小了执行域」：V4.0 拆成 5 个文件后若只读
``module.__file__``，4 个族子模块全落在看守之外）。⇒ QS-SPLIT-2/3 的判别点
**专属**：不依赖任何语料、也不依赖任何阈值。

为什么选 ``test_readability_rules.py`` 而不是 ``test_human_baseline_ai_thresholds.py``
----------------------------------------------------------------------------------
后者是**语料依赖**的（``gate.require_reference_corpus`` ⇒ 语料在内容仓
``NovelOS-Content``、不推 GitHub ⇒ CI 上恒 skip）⇒ 拿它当守门测试会出现
「本地红、CI 绿」的假覆盖（坑区：「没跑」与「跑过且通过」在退出码上同形）。
本文件所选 test_file 的 **corpus-gated 调用点数 = 0**（实测 13 个候选文件逐个数过），
⇒ 每条突变在**任何环境**都真红。

⚠️ 本文件只声明「这些突变会被捕获」；**不代表段切分没有别的漏点**——
「有规格」与「有覆盖」是两件事（坑区：「断言了结果 ≠ 判别了机制」）。
"""

MUTATIONS = [
    # ------------------------------------------------------------------
    # ① 单点口径回退：\n+ → \n\n（事故回归）
    # 撤掉它 ⇒ 人类锚点书（一行一段）被读成整章大段 ⇒ 段级算子构造上不可能命中，
    # 而「人类基线 0.00」会被重新读成一个**测出来的**结论（真因是量具假象）。
    # ------------------------------------------------------------------
    (
        "QS-SPLIT-1-paragraph-split-reverts-to-blank-line",
        "packages/core/quality/ai_patterns_text.py",
        r'    return [p.strip() for p in re.split(r"\n+", prose) if p.strip()]',
        r'    return [p.strip() for p in re.split(r"\n\n", prose) if p.strip()]',
        "tests/unit/quality/test_readability_rules.py",
    ),
    # ------------------------------------------------------------------
    # ② 短段族绕过单点，内联第二份切分
    # 判别点 = 形状级断言（扫 ai_patterns*.py 全集找内联切分字面）。
    # ------------------------------------------------------------------
    (
        "QS-SPLIT-2-short-para-inlines-second-split",
        "packages/core/quality/ai_patterns_paragraph.py",
        "    for para in _split_paragraphs(prose):",
        r'    for para in prose.split("\n\n"):',
        "tests/unit/quality/test_readability_rules.py",
    ),
    # ------------------------------------------------------------------
    # ③ 长段族绕过单点，内联第二份切分
    # 判别点 = 形状级断言 + 「单换行与双换行等价」的算子级断言（两个独立判别点）。
    # ------------------------------------------------------------------
    (
        "QS-SPLIT-3-long-para-inlines-second-split",
        "packages/core/quality/ai_patterns_paragraph.py",
        "    return [p for p in _split_paragraphs(prose) if visible_chars(p) > warn_chars]",
        r'    return [p for p in prose.split("\n\n") if visible_chars(p) > warn_chars]',
        "tests/unit/quality/test_readability_rules.py",
    ),
    # ------------------------------------------------------------------
    # ④ 最小判定长度被撤：600 → 0
    # 撤掉它 ⇒ 单测样例 / 引文这类几十字的片段也开始按「排版密度」判问题
    # ⇒ 误报面从真实章节扩大到一切短文本（量具自身失去适用域）。
    # ------------------------------------------------------------------
    (
        "QS-SPLIT-4-readability-min-length-guard-removed",
        "packages/core/quality/ai_patterns_text.py",
        "_MIN_PROSE_CHARS_FOR_READABILITY = 600",
        "_MIN_PROSE_CHARS_FOR_READABILITY = 0",
        "tests/unit/quality/test_readability_rules.py",
    ),
]
