r"""质量域突变规格 · 规则注册表与检查口径（2026-10-22 · novelos-dev-a · 第三批）

补的缺口
--------
本文件覆盖实测（2026-10-22，``HEAD=d06a79a``）**零突变规格**名单里的最后 4 个模块
（前两批已覆盖 ``ai_patterns_text`` / ``ai_patterns_paragraph`` /
``continuity_time`` / ``guardrails`` / ``style_trend`` / ``payoff`` /
``ai_patterns_dialogue`` / ``ai_trace`` / ``normalize``）：

* ``outline_check.py``——卷纲检查（2 条）
* ``wordcount.py``——字数度量与字数带（2 条）
* ``ai_flavor.py``——AI 味共享词表与密度工具（2 条）
* ``continuity_taxonomy.py``——连续性 rule_id 闭集与分类法（2 条）

这 4 个模块的共同点：**它们是「口径的权威来源」**——别的模块靠它们给出答案。
口径被悄悄改掉时，下游只会**安静地算出另一个数**，不报错、不崩。

八条突变逐条对应一个「撤掉就静默降级」的机制：

* **QOL-BEATS-1**——DS-10 颗粒度：每章 ``key_beats`` 少于 ``MIN_BEATS_PER_CHAPTER``
  ⇒ ``OUTLINE-THIN-BEATS``。撤掉它 ⇒ 薄章永远不报 ⇒ 卷纲质量检查对「太薄」失明。
* **QOL-COST-1**——DS-9 代价覆盖：含代价语义的章占比低于 ``cost_ratio_floor``
  ⇒ ``OUTLINE-NO-COST``。撤掉它 ⇒「每次跃迁伴随等价损失」这条要求无人检查。
* **QWC-BAND-1**——``word_band`` 的**保单调**：``floor`` 抬升后 ``high`` 同步抬，
  保证 ``low <= high``。撤掉它 ⇒ 低带可高于高带 ⇒ 下游按 band 判定「字数合理区间」
  时出现**空集/负区间**。
* **QWC-CFG-1**——``resolve_band_config`` 的**单键取值域校验**
  （``low_ratio`` ∈ (0, 1]；非法即抛 ``ValueError``）。
  撤掉它 ⇒ 项目级覆盖能配出 ``low_ratio = 0`` / ``1.01`` 这类非法带 ⇒ 错误配置
  静默生效。
  ⚠️ **实测发现的「死代码」缺口（如实登记，本文件不越界修）**：``wordcount.py`` 里
  **还有**一道 ``if cfg["high_ratio"] < cfg["low_ratio"]:`` 的带单调性检查，
  而它 **在结构上不可达**——因为逐键校验已保证 ``high_ratio >= 1`` 且
  ``low_ratio <= 1`` ⇒ 恒有 ``high_ratio >= low_ratio`` ⇒ 那道检查**永远不会触发**。
  ⇒ 后果有二：① ``resolve_band_config`` docstring 里承诺的
  「``high_ratio`` ≥ ``low_ratio``（带单调性）」这条**不是由那道检查保证的**；
  ② 既有 ``test_resolve_band_config_high_ratio_lower_than_low_ratio``（输入
  ``{"low_ratio": 0.95, "high_ratio": 0.9}``）**实际被逐键的 ``high_ratio >= 1``
  规则拦下**，它测的不是自己名字说的那道单调性 ⇒ **测试名与它实际测的东西不符**
  （坑区「测试的自我描述与事实不符」同族）。
  ⇒ 突变验证也救不了：那道分支**没有任何输入能让它触发** ⇒ 本文件**不放**指向它的
  突变（放进去＝保证存活的条目＝收尾闸门恒红），改为在此登记，**交 Lead 裁决**
  （改 ``packages/core/quality/wordcount.py`` 属本轮禁改范围）。
* **QFLAVOR-DENS-1**——``marker_hits_per_kchars`` 是「密度」的**唯一定义**
  （style 密度与 ai_trace 套话密度共用）。
  撤掉它 ⇒ 密度口径从「每千字」变成「每百字」⇒ 全部密度指标放大 10 倍。
* **QFLAVOR-EMPTY-1**——空文本 ⇒ 0.0（分母为 0 时不得抛异常、不得返非零）。
* **QTAX-NS-1**——``is_continuity_rule_id`` 判的是「连续性命名空间**闭集全量**」
  （确定性 + LLM 两侧），不是前缀猜测。撤掉它 ⇒ 未登记的 ``CONT-`` id 也被当作
  连续性 ⇒ 分类法名册形同虚设。
* **QTAX-CAT-1**——``taxonomy_category``：不在闭集内返回 ``None``，
  **不得回退**到某个默认 category。撤掉它 ⇒ 未知 rule_id 被静默归到 ``style``
  ⇒ 报告的分类分桶对未知 id 说谎。

⚠️ 本文件只声明「这些突变会被捕获」；**不代表这 4 个模块就没有别的漏点**。
"""

MUTATIONS = [
    # ------------------------------------------------------------------
    # ① DS-10 薄章判定
    # ------------------------------------------------------------------
    (
        "QOL-BEATS-1-outline-thin-beats-check-dead",
        "packages/core/quality/outline_check.py",
        "        if k < MIN_BEATS_PER_CHAPTER:",
        "        if k < 0:",
        "tests/unit/quality/test_outline_check.py",
    ),
    # ------------------------------------------------------------------
    # ② DS-9 代价覆盖率门槛
    # ------------------------------------------------------------------
    (
        "QOL-COST-1-outline-cost-coverage-floor-dropped",
        "packages/core/quality/outline_check.py",
        "    if ratio < cost_ratio_floor:",
        "    if ratio < 0.0:",
        "tests/unit/quality/test_outline_check.py",
    ),
    # ------------------------------------------------------------------
    # ③ word_band 保单调
    # ------------------------------------------------------------------
    (
        "QWC-BAND-1-word-band-loses-monotonicity-guard",
        "packages/core/quality/wordcount.py",
        "    if high < low:\n        high = low",
        "    if high < low:\n        pass",
        "tests/unit/test_wordcount.py",
    ),
    # ------------------------------------------------------------------
    # ④ 覆盖配置的单键取值域校验（low_ratio ∈ (0, 1]）
    # 注：**不是**那道 `cfg["high_ratio"] < cfg["low_ratio"]` 的带单调性检查——
    # 那道分支在结构上不可达（见文件 docstring「死代码」登记），放进来必存活。
    # ------------------------------------------------------------------
    (
        "QWC-CFG-1-band-config-low-ratio-bounds-removed",
        "packages/core/quality/wordcount.py",
        "            if fv <= 0 or fv > 1:",
        "            if False:",
        "tests/unit/test_wordcount.py",
    ),
    # ------------------------------------------------------------------
    # ④b high_ratio 的**下界**被放宽（≥1 变成 >0）
    # 这是「删掉不可达单调性分支」这件事的**看守**：带单调性不变式此后**只**由
    # 逐键校验结构性保证 ⇒ 逐键校验一旦被放宽，不变式就会失守 ⇒ 必须红。
    # ------------------------------------------------------------------
    (
        "QWC-CFG-2-band-config-high-ratio-floor-widened",
        "packages/core/quality/wordcount.py",
        "            if fv < 1:",
        "            if fv < 0:",
        "tests/unit/test_wordcount.py",
    ),
    # ------------------------------------------------------------------
    # ⑤ 密度口径：每千字（唯一定义）
    # ------------------------------------------------------------------
    (
        "QFLAVOR-DENS-1-density-caliber-per-kchar-broken",
        "packages/core/quality/ai_flavor.py",
        "    hits = sum(text.count(m) for m in markers)\n    return hits / (n / 1000.0)",
        "    hits = sum(text.count(m) for m in markers)\n    return hits / (n / 100.0)",
        "tests/unit/quality/test_ai_flavor.py",
    ),
    # ------------------------------------------------------------------
    # ⑥ 空文本 ⇒ 0.0
    # ------------------------------------------------------------------
    (
        "QFLAVOR-EMPTY-1-empty-text-density-not-zero",
        "packages/core/quality/ai_flavor.py",
        "    if not text:\n        return 0.0",
        "    if not text:\n        return 1.0",
        "tests/unit/quality/test_ai_flavor.py",
    ),
    # ------------------------------------------------------------------
    # ⑦ 连续性命名空间 = 闭集全量（不是前缀猜测）
    # ------------------------------------------------------------------
    (
        "QTAX-NS-1-continuity-namespace-becomes-prefix-guess",
        "packages/core/quality/continuity_taxonomy.py",
        "    return isinstance(rule_id, str) and rule_id in ALL_CONTINUITY_RULE_IDS",
        '    return isinstance(rule_id, str) and rule_id.startswith("CONT-")',
        "tests/unit/quality/test_continuity_taxonomy.py",
    ),
    # ------------------------------------------------------------------
    # ⑧ 未知 rule_id 不得回退到默认 category
    # ------------------------------------------------------------------
    (
        "QTAX-CAT-1-unknown-rule-id-falls-back-to-style",
        "packages/core/quality/continuity_taxonomy.py",
        "    return CRITIC_TAXONOMY.get(rule_id) if isinstance(rule_id, str) else None",
        '    return "style" if isinstance(rule_id, str) else None',
        "tests/unit/quality/test_continuity_taxonomy.py",
    ),
]
