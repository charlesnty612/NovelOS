"""C-5「未完成评审 run 不得提供被审版本」—— 突变规格（2026-11-05 · 批次三十六 批 3）。

缺陷形状（AGENTS 坑区「测量对象 ≠ 被审对象」的提交侧变体）
--------------------------------------------------------
``packages/workflows/chapter_commit/gate.py::_latest_review_report_draft_version``
此前只按 ``started_at DESC`` 取最近若干条 chapter-review run，**不筛 status**。
同域另外两处（``routers/workflows/control.py::_latest_completed_review_end``、
``chapter_commit/commit.py`` 的内层时序守卫）都筛 ``status='COMPLETED'``，唯此处不筛。

事故形状：最新草稿 v2、COMPLETED 评审量过 v2（较早）、更晚一条 FAILED（驳回）/
PAUSED 评审量过 v1 ⇒ 门禁取到 **v1** ⇒ 快通道下 commit 照跑 ⇒
``commits.draft_version`` 记 v1 ⇒ **导出物按它取到旧稿**
（``packages/core/exporter/builder.py``）。失败的评审不构成「作者审过这一版」的证据。

一条突变
--------
**GATE-REVIEW-STATUS-1** 撤掉 SQL 里的 ``AND wr.status = 'COMPLETED'``
  ⇒ ``tests/unit/quality/test_gate_reviewed_draft_version.py`` 的
  ``test_non_completed_review_run_does_not_supply_the_version[FAILED]`` /
  ``[PAUSED]`` 与 ``test_only_non_completed_review_run_falls_back_to_latest`` 必红
  （两条一起覆盖「有合格候选」与「一个合格候选都没有」两个方向）。

自查（写完必做）
----------------
1. 锚点（含缩进的 3 行片段）在目标文件里恰好命中 **1** 次；
2. replacement 真的改变语义（去掉状态过滤），不是「插行注释」式等价突变；
3. 交接前清点残留：``find . -name "*.mutbak"`` + 搜 ``MUTATION-PROBE-B36-C5``。
"""

MUTATIONS = [
    (
        "GATE-REVIEW-STATUS-1",
        "packages/workflows/chapter_commit/gate.py",
        # 2026-11-08 批次三十八 REF-04：谓词已提为 review_state.REVIEW_VALID_PREDICATE
        # （wf.name + wr.status 合成一个常量），故锚点改为「整段谓词引用」，
        # 突变改为只保留 name 条件 ⇒ 仍然是「撤掉 status 过滤」这一条语义。
        """            WHERE wr.chapter_id = ? AND {REVIEW_VALID_PREDICATE}
            ORDER BY wr.started_at DESC""",
        """            WHERE wr.chapter_id = ? AND wf.name = 'chapter-review'
            ORDER BY wr.started_at DESC  -- MUTATION-PROBE-B36-C5: 撤掉 status 过滤""",
        "tests/unit/quality/test_gate_reviewed_draft_version.py",
    ),
]
