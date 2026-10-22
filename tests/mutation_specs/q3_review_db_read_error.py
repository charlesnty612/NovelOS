"""chapter_review 的「DB 读失败不许静默」：撤掉留痕 / 降级日志级别 ⇒ 必红（dev-b 批次二十一 P0-3）。

缺陷形状
--------
``packages/workflows/chapter_review/pipeline.py`` 的 ``_basic_checks_node`` 改造前是
``except Exception: pass``，把「**读不到章节行**」与「plan 解析降级」一起吞掉 ⇒
空 plan 下**照常审稿、照常出报告、零告警**（作者看到的是一份「审过了」的报告，
而它建立在「没读到任何输入」之上）。

两条突变各钉一个失效方向
------------------------
* **REVIEW-DB-1** 撤掉报告里的留痕行（``warnings.append`` 不再执行）⇒
  ``test_db_read_failure_leaves_a_trace_in_the_review_report`` 必红
  （它断言报告里有 ``[REVIEW-DB-READ-FAILED]``）。
* **REVIEW-DB-2** 把 ``log.error`` 降成 ``log.debug`` ⇒
  ``test_db_read_failure_is_logged_as_error`` 必红（它断言有 error 级日志）。

⚠️ **两条都是「只撤机制」**：replacement 不引入新的未定义名字、不破坏 import ⇒
红的**原因**是断言失效，而不是 ``NameError`` / ``ImportError``（后者会被误记成
「捕获」——AGENTS 坑区「变异改得太狠 ⇒ 归 INVALID 而不是 CAUGHT」）。

判别
----
``tests/unit/test_ctx_b_review_db_read_error.py``（4 例：error 日志 / 报告留痕 /
文案不许谎报 / **正常路径零变化**）。
"""

MUTATIONS = [
    (
        "REVIEW-DB-1",
        "packages/workflows/chapter_review/pipeline.py",
        "    if chapter_read_error:\n        warnings.append(\n",
        "    if False:\n        warnings.append(\n",
        "tests/unit/test_ctx_b_review_db_read_error.py",
    ),
    (
        "REVIEW-DB-2",
        "packages/workflows/chapter_review/pipeline.py",
        '        _log.error(\n            "chapter_review.basic_checks chapter read failed: chapter_id=%s err=%s",\n',
        '        _log.debug(\n            "chapter_review.basic_checks chapter read failed: chapter_id=%s err=%s",\n',
        "tests/unit/test_ctx_b_review_db_read_error.py",
    ),
]
