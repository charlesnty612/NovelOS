"""commit 与章节状态的事务原子性 —— 突变规格（2026-11-04 · 批次三十五 批 4）。

为什么这组**必须**有规格（AGENTS §三.7 第三条）
------------------------------------------------
commit 是**不可逆写数据**的门禁：一旦把章节状态推进与 ``story_states`` 拆成两个事务，
中间崩溃就会留下「版本已推进、章状态未变」的半提交态；重跑再 +1 版本。
⇒ 命中「不可逆 / 会损坏数据」⇒ 按项目规则**新测试必须有突变规格**。

两条突变各钉一个**正交**的失效方向（只钉一条 ⇒ 另一种改法能全绿）：

* **COMMIT-ATOMIC-1** 撤掉事务内的章节状态推进（目标状态改成「期望状态」⇒ 等于不推进）
  ⇒ ``test_commit_node_advances_chapter_status_and_state_version_together`` 必红
  （章状态没到 COMMITTED）。
* **COMMIT-ATOMIC-2** 撤掉事务内的乐观锁检查
  ⇒ ``test_commit_delta_rejects_when_chapter_status_changed_concurrently`` 与
  ``test_commit_node_does_not_overwrite_concurrent_draft`` 必红
  （并发改稿会被盖回 COMMITTED ⇒ 半提交态回来）。

⚠️ 撤掉「乐观锁」这一条**不会**让用例 1 红（它走正常路径）⇒ 反之撤掉「推进」也不会让
用例 2/3 红 ⇒ **两条缺一不可**。
"""

MUTATIONS = [
    (
        "COMMIT-ATOMIC-1",
        "packages/core/story_state/commits.py",
        '            conn.execute(\n'
        '                "UPDATE chapters SET status = ?, updated_at = ? WHERE chapter_id = ?",\n'
        '                (chapter_status_after, now, target_chapter_id),\n'
        '            )\n',
        '            conn.execute(\n'
        '                "UPDATE chapters SET status = ?, updated_at = ? WHERE chapter_id = ?",\n'
        '                (chapter_status_expect or chapter_status_after, now, target_chapter_id),\n'
        '            )\n',
        "tests/unit/test_commit_transaction_atomicity.py",
    ),
    (
        "COMMIT-ATOMIC-2",
        "packages/core/story_state/commits.py",
        "            if chapter_status_expect is not None:\n",
        "            if False:  # MUTATION-COMMIT-ATOMIC-2: 撤掉事务内乐观锁\n",
        "tests/unit/test_commit_transaction_atomicity.py",
    ),
]
