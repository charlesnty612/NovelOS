r"""质量域突变规格 · 子分与算子机制（2026-10-22 · novelos-dev-a · 第二批）

补的缺口
--------
本文件覆盖实测（2026-10-22，``HEAD=d06a79a``）**零突变规格**名单里的另外 5 个模块
（第一/二/三批已覆盖 ``ai_patterns_text`` / ``ai_patterns_paragraph`` /
``continuity_time`` / ``guardrails``）：

* ``style_trend.py``——style 跨章退化监测（2 条）
* ``payoff.py``——爽感 H-1~H-5（2 条）
* ``ai_patterns_dialogue.py``——对白族（2 条）
* ``ai_trace.py``——AI 痕迹子分（2 条）
* ``normalize.py``——正文规整与内部标识检出（2 条）

十条突变**两两不同向**，逐条对应一个「撤掉就静默降级」的机制：

* **QST-MONO-1**——「连续退化」要求**严格单调**下降（平盘不触发）。
  撤掉严格性 ⇒ 平盘（90→90→90）也报「连续下降」⇒ 噪声淹没信号。
* **QST-THRESH-1**——累计降幅**门槛** ``DECAY_THRESHOLD``。
  撤掉它 ⇒ 只要单调下降哪怕只降 1 分就报。
* **QPAY-SEV-1**——payoff 类目的 severity 上限**统一从** ``MVP_SEVERITY_MATRIX``
  **读取**（避免硬编码 ⇒ 矩阵一改全局生效）。
  撤掉它 ⇒ payoff 类被永久钉死在 warning，矩阵改了它不跟 ⇒ 矩阵形同虚设。
  ⚠️ **实测发现的量具陷阱（如实登记）**：这条突变**首版写成** replacement
  ``    return "warning"`` ⇒ 实跑 **[SURVIVED]** —— 而**不是**因为缺测试，
  而是因为 **``MVP_SEVERITY_MATRIX["payoff"]["mvp_max"]`` 当前恰好就是
  ``"warning"``** ⇒ 硬编码成同一个值 = **等价突变** ⇒ **任何测试都不可能抓到它**
  （坑区「脚本报存活时先怀疑量具，再怀疑变异，最后才怀疑被测物」的顺序，
  这次顺序反过来也要成立：**等价突变要认出来，不能当成「缺测试」去补测试**）。
  ⇒ 本文件最终采用 replacement ``    return "error"``（**改成矩阵之外的值**）：
  这样才可观测 ⇒ 由 ``test_h3_filler_3ch_warning_matches_matrix``（断言
  ``rule3[0].severity == "warning"``）抓住。
  ⇒ **仍存的口径缺口（登记）**：「payoff 的 severity 必须是**矩阵当前取值**」
  这一条，在矩阵不变时**不可被任何测试观测**；只有把代码硬编码成矩阵之外的值
  才红。⇒ 若 Lead 认为这条要真正钉住，需要一条**跨模块**的守卫
  （改 ``issues.MVP_SEVERITY_MATRIX["payoff"]`` ⇒ payoff issue 的 severity 必须跟着变），
  那是**另一个规格**的事（且会动 ``issues.py``），本文件不越界。
* **QPAY-PAID-1**——本章 payoff 的「已偿债务」只认 ``status_after == "paid"``。
  撤掉它 ⇒ 任何 status_after 都算偿付 ⇒ 爽感被凭空兑现。
* **QDLG-SEV-1**——``AI-DIALOGUE-LOW`` **只有 warning 一档**（error 档 2026-09-17
  撤回：逼模型凑对话指标会产出灌水对白）。
  撤掉它 ⇒ 规则重新变 error ⇒ 回到那个已被实证否定的设计。
* **QDLG-OR-1**——回声的两种形态（复述型 / 接词型）是「**或**」关系，
  同一对相邻对白**最多计一次**。
  撤掉「或」⇒ 只在两种形态同时成立时才算命中 ⇒ 复述型真信号整族漏报。
* **QTRACE-BUCKET-1**——``_bucket_deduct`` 阶梯扣分：未达档位即**终止**，
  不用不匹配的档位覆盖已命中的扣分。
  撤掉它 ⇒ 扣分被清零 ⇒ 重度重复文本被判成「无重复」。
* **QTRACE-NOHIST-1**——跨章子信号在**无历史章节**时降级
  （``available=False`` ⇒ ratio=0 / deduct=0）。
  撤掉它 ⇒ 首章被当成「已比对过历史」⇒ 跨章子信号在全书第一章凭空生效。
* **QNORM-DEDUP-1**——内部标识检出**去重、保序**。
  撤掉去重 ⇒ 同名标识重复上报 ⇒ issue 列表被同一项灌水。
* **QNORM-RE-1**——只命中 **≥2 段 snake_case** 标识；
  camelCase（``iPhone``）、单个英文词（``OK`` / ``PPT``）、中文、纯数字**一律不报**。
  撤掉它 ⇒ 正常中文写作里会出现的英文片段全部被误报成「内部字段名漏进正文」。

⚠️ 本文件只声明「这些突变会被捕获」；**不代表这 5 个模块就没有别的漏点**。
"""

MUTATIONS = [
    # ------------------------------------------------------------------
    # ① style 退化：严格单调（平盘不触发）
    # ------------------------------------------------------------------
    (
        "QST-MONO-1-style-trend-monotonic-not-strict",
        "packages/core/quality/style_trend.py",
        "    if not (s[0] > s[1] > s[2]):",
        "    if not (s[0] >= s[1] >= s[2]):",
        "tests/unit/quality/test_style_trend.py",
    ),
    # ------------------------------------------------------------------
    # ② style 退化：累计降幅门槛
    # ------------------------------------------------------------------
    (
        "QST-THRESH-1-style-trend-decay-threshold-dropped",
        "packages/core/quality/style_trend.py",
        "    if (s[0] - s[2]) < DECAY_THRESHOLD:",
        "    if (s[0] - s[2]) < 0:",
        "tests/unit/quality/test_style_trend.py",
    ),
    # ------------------------------------------------------------------
    # ③ payoff severity 上限：必须从矩阵读（唯一权威）
    # ------------------------------------------------------------------
    (
        "QPAY-SEV-1-payoff-severity-not-read-from-matrix",
        "packages/core/quality/payoff.py",
        '    return mvp_max_severity("payoff")',
        '    return "error"',
        "tests/unit/quality/test_payoff.py",
    ),
    # ------------------------------------------------------------------
    # ④ payoff「已偿债务」只认 status_after == "paid"
    # ------------------------------------------------------------------
    (
        "QPAY-PAID-1-paid-debt-status-check-removed",
        "packages/core/quality/payoff.py",
        '        if isinstance(d, dict) and d.get("status_after") == "paid"',
        '        if isinstance(d, dict) and d.get("status_after") is not None',
        "tests/unit/quality/test_payoff.py",
    ),
    # ------------------------------------------------------------------
    # ⑤ AI-DIALOGUE-LOW 只有 warning 一档
    # ------------------------------------------------------------------
    (
        "QDLG-SEV-1-dialogue-low-severity-back-to-error",
        "packages/core/quality/ai_patterns_dialogue.py",
        '            "rule_id": "AI-DIALOGUE-LOW",\n            "severity": "warning",',
        '            "rule_id": "AI-DIALOGUE-LOW",\n            "severity": "error",',
        "tests/unit/quality/test_dialogue_low_warning_only.py",
    ),
    # ------------------------------------------------------------------
    # ⑥ 回声两形态是「或」关系
    # ------------------------------------------------------------------
    (
        "QDLG-OR-1-dialogue-echo-two-shapes-are-and-not-or",
        "packages/core/quality/ai_patterns_dialogue.py",
        "        if repeated or picking_up:",
        "        if repeated and picking_up:",
        "tests/unit/quality/test_dialogue_echo.py",
    ),
    # ------------------------------------------------------------------
    # ⑦ ai_trace 阶梯扣分：未达档位即终止，不用不匹配档位覆盖
    # ------------------------------------------------------------------
    (
        "QTRACE-BUCKET-1-bucket-deduct-overwrites-on-mismatch",
        "packages/core/quality/ai_trace.py",
        "        if value >= threshold:\n            deduct = score\n        else:\n            break",
        "        if value >= threshold:\n            deduct = score\n        else:\n            deduct = 0",
        "tests/unit/quality/test_ai_trace.py",
    ),
    # ------------------------------------------------------------------
    # ⑧ 跨章子信号：无历史时降级（available=False）
    # ------------------------------------------------------------------
    (
        "QTRACE-NOHIST-1-cross-chapter-no-history-not-degraded",
        "packages/core/quality/ai_trace.py",
        "    if not avail or not pool:\n        return 0.0, 0, False",
        "    if not avail or not pool:\n        return 0.0, 0, True",
        "tests/unit/quality/test_ai_trace.py",
    ),
    # ------------------------------------------------------------------
    # ⑨ 内部标识去重保序
    # ------------------------------------------------------------------
    (
        "QNORM-DEDUP-1-internal-identifier-dedup-removed",
        "packages/core/quality/normalize.py",
        "        if token not in seen:",
        "        if True:",
        "tests/unit/quality/test_normalize.py",
    ),
    # ------------------------------------------------------------------
    # ⑩ 内部标识形态：≥2 段 snake_case；camelCase / 单个英文词不报
    # ------------------------------------------------------------------
    (
        "QNORM-RE-1-internal-identifier-regex-too-wide",
        "packages/core/quality/normalize.py",
        r'_INTERNAL_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+")',
        r'_INTERNAL_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")',
        "tests/unit/quality/test_normalize.py",
    ),
]
