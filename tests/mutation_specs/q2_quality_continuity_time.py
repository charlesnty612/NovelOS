r"""质量域突变规格 · 章内时点矛盾算子（2026-10-22 · novelos-dev-a）。

补的缺口
--------
``packages/core/quality/continuity_time.py``（595 行，两条规则
``CONT-CLOCK-DAYBREAK`` / ``CONT-TIME-BACKSTEP``）此前**零突变规格**——实测
（2026-10-22，``HEAD=d06a79a``）13 个质量域模块无任何突变规格，它是其中之一。

这个模块的形状：**纯字面算子**，全部判定靠「守卫」串联，任何一道守卫被撤掉都
**不会抛错**，只会**少报或多报**——正是「撤掉就静默降级」的形状。

本文件四条突变分别撤掉**四道不同的守卫**，互不重复：

* **QT-MIDNIGHT-1**——跨午夜顺行豁免（23:47 → 次日 03:42 字面递减但实为顺行）。
* **QT-BACKSTEP-1**——回退**量级门槛**（后者至少早 N 小时才算回退）。
* **QT-ADVANCE-1**——中间出现时间推进标记 ⇒ 不判矛盾。
* **QT-DEADLINE-1**——右邻期限语（「子时之前」「子时将近」）⇒ 不算现场时点。

为什么这四道守卫值得逐条钉死
----------------------------
这四道都是**假报（误判）的抑制器**。它们被撤掉时，算子只会**多报**，不报错、
不崩 ⇒ 章节报告里凭空多出「时点矛盾」类 issue ⇒ 作者看到的是一条**看似有理、
实则误报**的评审意见（AGENTS 坑区「检测规则『全是误报』这一病」）。

⚠️ **量具侧边界（如实登记）**：``tests/unit/quality/test_continuity_time.py`` 里有
**1 处**语料依赖调用（``gate.require_reference_corpus``，在
``test_human_baseline_has_no_clock_opportunity_and_zero_hits``）⇒ 那一条在 CI 上恒 skip；
但本文件四条突变分别由**四条不依赖语料**的用例看守（见下表），故 CI 上同样真红。
这是选它当守门文件时**逐条核过**的，不是默认它安全。
"""

MUTATIONS = [
    # ------------------------------------------------------------------
    # ① 跨午夜顺行豁免被撤（23:47 → 次日 03:42 字面递减但实为顺行）
    # 撤掉它 ⇒ 顺行被误报成「时点回退」。
    # ------------------------------------------------------------------
    (
        "QT-MIDNIGHT-1-midnight-wrap-no-longer-exempt",
        "packages/core/quality/continuity_time.py",
        "        if second.hour <= 6.0 and first.hour >= 18.0:",
        "        if second.hour <= 6.0 and first.hour >= 24.0:",
        "tests/unit/quality/test_continuity_time.py",
    ),
    # ------------------------------------------------------------------
    # ② 回退量级门槛被撤：不再要求「至少早 N 小时」
    # 撤掉它 ⇒ 任何时点倒退（哪怕只退 1 分钟）都算回退 ⇒ 误报面急剧扩大。
    # ------------------------------------------------------------------
    (
        "QT-BACKSTEP-1-min-backstep-threshold-dropped",
        "packages/core/quality/continuity_time.py",
        "        if not (second.hour < first.hour - min_backstep_hours):",
        "        if not (second.hour < first.hour):",
        "tests/unit/quality/test_continuity_time.py",
    ),
    # ------------------------------------------------------------------
    # ③ 「中间有时间推进标记 ⇒ 不判矛盾」这道守卫被撤
    # 撤掉它 ⇒ 「三天后」这类明确推进照旧被判成矛盾。
    # ------------------------------------------------------------------
    (
        "QT-ADVANCE-1-advance-marker-guard-removed",
        "packages/core/quality/continuity_time.py",
        "        if _has_advance(prose, first.end, second.start):",
        "        if False:",
        "tests/unit/quality/test_continuity_time.py",
    ),
    # ------------------------------------------------------------------
    # ④ 「右邻期限语 ⇒ 不是现场时点」这道守卫被撤
    # 撤掉它 ⇒ 「子时之前」这类**计划语**被当成现场时钟参与比较。
    # ------------------------------------------------------------------
    (
        "QT-DEADLINE-1-deadline-guard-removed",
        "packages/core/quality/continuity_time.py",
        "        if _is_deadline(first, prose) or _is_deadline(second, prose):",
        "        if False:",
        "tests/unit/quality/test_continuity_time.py",
    ),
]
