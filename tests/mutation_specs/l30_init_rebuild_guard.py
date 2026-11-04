"""project_init 重跑重建的 fail-closed 预检（批次三十六 C-1，2026-11-04）。

事故
----
挂载模式重跑 project-init 时，rebuild 段按卷 ``DELETE FROM drafts`` +
``DELETE FROM chapters``，**没有任何状态过滤**：

* 已提交章被 ``commits.chapter_id`` 的 FK（0001，NO ACTION）挡住 ⇒ run FAILED、
  数据无损，但错误消息是裸 ``IntegrityError``（不可读）；
* **真正会丢的是「只有草稿、尚未提交」的章**——drafts 被显式删除，连
  ``outline_json`` 策展槽一起消失，且全程无告警。

修法
----
在任何 ``DELETE`` 之前做 fail-closed 预检：只允许重建 ``status='PLANNED'`` 且
没有任何 ``drafts`` 行的章；其余逐章列出 ``number/status/有无草稿`` 并抛显式
异常 ⇒ 单事务回滚、**零删除**。

突变
----
**INIT-REBUILD-1** 把预检短路成 ``if False`` ⇒ 有草稿的章被照常删除 + 重建
⇒ ``test_init_rebuild_refuses_drafted_chapter_and_deletes_nothing`` 必红
（它断言 ``ValueError``、消息内容、以及章节/草稿/outline_json 逐行不变）。

⚠️ 该规格同时覆盖「状态维度」与「草稿维度」：撤掉整段预检后，两个方向的用例
都会红；只删其中一个维度时由参数化用例分别看守（见同文件）。
"""

MUTATIONS = [
    (
        "INIT-REBUILD-1",
        "packages/workflows/project_init/pipeline.py",
        "                if blockers:\n                    raise ValueError(\n",
        "                if False:  # MUTATION-INIT-REBUILD-1: 撤掉 fail-closed 预检\n                    raise ValueError(\n",
        "tests/unit/test_b36_init_deconstruct.py",
    ),
]
