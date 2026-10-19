"""polisher 提示词与代码分叉的三条突变（2026-10-19 批次十三）。

事故
----
``polisher-v1.md`` 三处写「每千字 ≤ 6 处」，生产阈值早已改成 **1.0**（6 → 3.5 → 1.0，
提示词没跟着改）⇒ 提示词的验收口径与门禁口径互相矛盾；同批另修了 payload 里的
硬编码 ``"polisher:v1"`` 标签（升版后模型照抄回声、``ai_call_logs`` 记真值 ⇒ 同链两值）。

三条突变各钉一个失效方向
------------------------
* **POLD-1** 把 ``_polish_node`` 的 payload 标签改回硬编码 ``"polisher:v1"``
  ⇒ ``test_polish_node_payload_prompt_version_matches_db_active`` 必红。
* **POLD-2** 把提示词 §6 规则 3 的阈值从 1.0 改回 6
  ⇒ ``test_polisher_prompt_threshold_equals_production_threshold`` 必红
  （这一条盯的是「**提示词**退回旧口径」这个失效方向）。
* **POLD-3** 把**代码常量**从 1.0 改成 6.0
  ⇒ 同一条阈值用例也必红 —— 这一条盯的是**反方向**：
  若只钉 POLD-2，就会漏掉「有人为了迁就提示词而把生产阈值改回 6」这种改法。
  **恒等断言必须双向**（单向 ``assert "1.0" in text`` 在有人写成「1.0 / 3.5 并列」
  时会漏放）。

⚠️ POLD-2 改的是 **``.md``** 提示词文件，不是 Python 源码——本仓此前的突变全部落在
``.py`` 上，故这里专门多验一次「runner 能改非 ``.py`` 文件且能还原」。
"""

MUTATIONS = [
    (
        "POLD-1",
        "packages/workflows/chapter_write/pipeline.py",
        '        "prompt_version": polisher_prompt_label,\n',
        '        "prompt_version": "polisher:v1",  # MUTATION-POLD-1: 改回硬编码\n',
        "tests/unit/test_polisher_prompt_contract.py",
    ),
    (
        "POLD-2",
        "docs/agents/prompts/polisher-v2.md",
        "每千**可见字符**（剔除空白，标点计入）≤ 1.0 处",
        "每千**可见字符**（剔除空白，标点计入）≤ 6 处",
        "tests/unit/test_polisher_prompt_contract.py",
    ),
    (
        "POLD-3",
        "packages/core/quality/ai_patterns_sentence.py",
        "DEFAULT_DASH_THRESHOLD_PER_1K = 1.0",
        "DEFAULT_DASH_THRESHOLD_PER_1K = 6.0  # MUTATION-POLD-3: 迁就旧提示词",
        "tests/unit/test_polisher_prompt_contract.py",
    ),
]
