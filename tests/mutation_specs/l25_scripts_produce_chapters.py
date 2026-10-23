"""`scripts/produce_chapters.py` 的突变补盲（2026-10-23 批次三十二 · task-28 · 台账 C-1 余项）。

来源
----
`docs/impl/reviews/remaining-debt-2026-10-23.md` 的 **C-1**（「19 / 14 个脚本零突变规格」）。
批次二十九按「**改错会静默**」的顺序补了前三位（`l20_scripts_coverage.py`：
``backup_db`` > ``content_sync`` > ``open_volume``）；台账登记的完整顺序是

    backup_db > content_sync > open_volume > **produce_chapters** > smoke_e2e >
    inspect_stalled_runs > export_openapi / gen_frontend_types

⇒ 本条补第四位 ``produce_chapters``（``smoke_e2e`` / ``inspect_stalled_runs`` 本轮不要求）。

被测物与缺陷形状
----------------
``scripts/produce_chapters.py::decide_review`` 的**第 1 步**（重复率优先，函数 docstring
「判定顺序（**重复率优先**——它是对草稿本身的否决，先于对评审条目的分类）」）：

.. code-block:: python

    if ratio > repetition_threshold:
        return ReviewDecision(kind="stop", reason="repetition_exceeds_threshold", ...)

这道闸门是**本脚本存在的理由**（模块 docstring「不静默批准坏章（本脚本存在的理由）」）：
章内重复率（13 字 shingle）超阈值时**拒绝批准**，**哪怕评审报告一个 error 都没有**。
若它被摘掉 ⇒ 重复文本的决议从 ``stop / repetition_exceeds_threshold`` 变成
``approve / no_review_errors`` ⇒ **驱动自动批准重复文本**——「自动批准重复文本的驱动，
比没有驱动更糟」，而且**全程零告警**（退出码仍是 0，摘要是「全部完成」）。

⚠️ 为什么打这一处，而不是同文件里另外两个看起来也危险的点
------------------------------------------------------
- ``is_confirm_tier_failure``（confirm 档硬停）有**双保险**：先判 ``"gate=confirm" in text``，
  再兜 ``rule_id in CONFIRM_TIER_RULE_IDS`` ⇒ 只摘掉第一段，另一段仍会拦住 ⇒ 判别力更弱；
- ``_error_rule_ids`` 的 ``missing`` 分支摘掉后仍会走 ``unknown`` 分支 ``stop``
  ⇒ 同样是「从 stop 变 stop」，不是静默放行。

⇒ 唯有重复率闸门是**摘掉即静默放行**，故选它。

承接用例（**已存在**，本条不新增任何测试）
------------------------------------------
``tests/unit/test_produce_chapters.py``

- ``test_decide_review_refuses_duplicated_draft_even_without_errors``
  —— docstring 就写着「**本脚本存在的理由**：报告无 error，但草稿自身重复 ⇒ 拒绝批准」；
  断言 ``kind == "stop"`` / ``reason == "repetition_exceeds_threshold"`` /
  ``"拒绝批准" in note``。闸门被摘 ⇒ 三条断言同时红。
- ``test_decide_review_threshold_is_strict_greater_than``
  —— ``repetition_threshold = ratio - 1e-9`` 时必须 ``stop``；闸门被摘 ⇒ 变 ``approve``。

⇒ 实跑（``run_spec.py`` 定向）判定为 ``[CAUGHT]``。

锚点纪律
--------
- 锚点 = 单行 ``    if ratio > repetition_threshold:``，在 ``scripts/produce_chapters.py``
  内**恰好命中 1 次**（``validate_specs.py`` 只读预检 + ``run_spec.py --precheck`` 双重把关）；
- replacement **不含双引号**（``q1_style_sample_key.py`` 留痕：走 argv 的形态会被剥引号），
  且**真改变行为**（``if False:`` ⇒ 该分支恒不进入），**不是**「插注释 / 改排版」那种
  SHA 会变的等价变异——执行器有「写入后 SHA256 未变 ⇒ ABORT」的自检。
"""

MUTATIONS = [
    (
        "SC-PRODUCE-REPETITION",
        "scripts/produce_chapters.py",
        "    if ratio > repetition_threshold:\n",
        "    if False:  # MUTATION-SC-PRODUCE-REPETITION: 重复率闸门失效，重复文本被静默批准\n",
        "tests/unit/test_produce_chapters.py",
    ),
]
