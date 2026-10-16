"""``genre_check.error`` 可达性修复的突变规格（2026-10-16 批次十 · Lead 自办）。

三条，各钉一个失效方向：

* **GATE-1** 撤掉修复：布尔门退回 ``if not result.bound: return None``
  ⇒ ``error`` 重新不可达（布尔门遮蔽复发）。
* **GATE-2** 过度修正一：把整段 ``if`` 删掉，永远 ``return result.to_dict()``
  ⇒ 「项目确实没绑定题材包」时报告里也凭空多出 ``genre_check`` 段，违反「零行为变化」。
* **GATE-3** 过度修正二：把 ``and result.error is None`` 去掉 ⇒ 同 GATE-2 的后果，
  但形态更像「顺手清理」，因此单列一条独立突变。

⚠️ 这组的价值不在数量，在于它是**布尔门**这一新失效家族的判别面：三条里有两条
是「改过头」，只钉一条撤掉方向的修复，一个「一律返回」的实现照样全绿。
"""

MUTATIONS = [
    (
        "GATE-1",
        "packages/workflows/chapter_review/pipeline.py",
        "    if not result.bound and result.error is None:\n        return None\n",
        "    if not result.bound:  # GATE-MUTATION: 退回布尔门遮蔽\n        return None\n",
        "tests/unit/test_genre_check_report_reachability.py",
    ),
    (
        "GATE-2",
        "packages/workflows/chapter_review/pipeline.py",
        "    if not result.bound and result.error is None:\n        return None\n",
        "    # GATE-MUTATION: 整段删掉，一律返回\n",
        "tests/unit/test_genre_check_report_reachability.py",
    ),
    (
        "GATE-3",
        "packages/workflows/chapter_review/pipeline.py",
        "    if not result.bound and result.error is None:\n",
        "    if not result.bound:  # GATE-MUTATION: 去掉 error 判据\n",
        "tests/unit/test_genre_check_report_reachability.py",
    ),
]
