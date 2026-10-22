"""装配缓存键的「所属卷」维度：撤掉 ``volume_scope`` ⇒ 必红（2026-10-23 P0-1 候选 C）。

缺陷形状（硬规则 2「凡进 payload 的装配输入必须入键」的第 N 次同形）
----------------------------------------------------------------
``chapters.volume_id`` 决定三处同卷取数（近窗摘要 / 上一章尾段 / FTS 召回，见
``builders_common/summaries.py`` 与 ``retrieval/service.py``）——它是装配输入。
而 ``VolumeService.assign_chapter`` 只改 ``chapters.volume_id`` + ``updated_at``，
**不推** ``state_version``、不动 plan/outline ⇒ 修前缓存键没有任何一维会因挂卷而变
⇒「先装配、后挂卷」在同 ``state_version`` + 同 ``chapter_no`` 下命中**旧卷窗口**：
换卷后新卷首章仍把上一世的摘要 / 尾段 / 召回当「近窗」注入（快穿冷开场被破），零告警。

三条突变各钉一个失效方向
------------------------
* **VOL-1** 撤掉 director 键的 ``volume_scope`` 元素 ⇒ 挂卷前后键不变（键长 18→17）
  ⇒ ``test_assigning_volume_misses_cache_and_empties_window`` 必红（它同时断言
  「窗口变空」与「键只差一位」）。
* **VOL-2** 撤掉 writer 键的 ``volume_scope`` 元素 ⇒ 同上（键长 23→22）
  ⇒ 同用例的 writer 断言必红。
* **VOL-3** 主 peek SQL 改成 ``NULL AS volume_id`` ⇒ ``_peek_chapter_context`` 恒
  读不到卷 ⇒ 键维恒落哨兵 ``"__none__"``，而 payload 侧同卷过滤照旧按真实列生效
  ⇒ 同一脏命中形状 ⇒ 同用例必红（这条钉住「peek 真的把列读出来了」，防
  「加了键维但取值恒为哨兵」的假修复）。

⚠️ **锚点选择（并发批次纪律）**
------------------------------
* VOL-1/VOL-2 用**多行上下文**锚定（维度行 + 紧随其后的既有维度行）——那两行的
  顺序在既有批次里稳定，且各自在目标文件中**只命中 1 次**；单行
  ``        volume_scope,`` 虽然当前也唯一，但多行锚更抗后续漂移。
* 两处锚点都**刻意不落在**既有规格 ``E1-1`` / ``E1-2`` / ``SKILL-KEY-1`` /
  ``SKILL-KEY-2`` / ``STYLE-KEY-1`` / ``TITLE-KEY-1`` 的锚点原文上（本维度是按
  「新增行、不改既有行」的纪律加进键元组的）。
* replacement 一律不含双引号（Windows PowerShell 5.1 向原生程序传 argv 会剥内嵌
  引号 ⇒ 含引号的 replacement 会被静默变形，见 ``q1_style_sample_key.py`` 的留痕）。

判别
----
``tests/unit/test_volume_scope_cache_key.py``：
- ``test_assigning_volume_misses_cache_and_empties_window``（挂卷 ⇒ miss + 窗口清空）；
- ``test_reassigning_back_restores_window_and_misses_again``（换挂回原卷 ⇒ 窗口恢复，
  正反一对，防「挂卷后一律空」的假修复）；
- ``test_unassigned_and_empty_peek_share_sentinel_shape``（哨兵同形 + 键长 18/23）。
"""

MUTATIONS = [
    (
        "VOL-1",
        "packages/core/context_engine/director_input.py",
        "        volume_scope,\n        project_meta_fp,\n",
        "        project_meta_fp,\n",
        "tests/unit/test_volume_scope_cache_key.py",
    ),
    (
        "VOL-2",
        "packages/core/context_engine/writer_input.py",
        "        volume_scope,\n        title_fp,\n",
        "        title_fp,\n",
        "tests/unit/test_volume_scope_cache_key.py",
    ),
    (
        # 把「真的读了 chapters.volume_id」改成恒 NULL：键维永远落哨兵，
        # 挂卷不再 miss（payload 侧仍按真实列过滤 ⇒ 脏命中形状）。
        "VOL-3",
        "packages/core/context_engine/builders_common/common.py",
        "           ch.volume_id                        AS volume_id,\n",
        "           NULL AS volume_id,\n",
        "tests/unit/test_volume_scope_cache_key.py",
    ),
]
