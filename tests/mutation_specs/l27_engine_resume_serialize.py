"""批次三十六 · 工作流引擎域（批 1）—— engine 的 resume 原子性 / 序列化兜底 /
delta 残留清理 的突变规格。

对应 AGENTS 硬规则（可逆 / 数据损坏 / 门禁型机制）：

* **B36-RESUME-1** 撤掉 ``_mark_run_running`` 的 ``AND status='PAUSED'``
  ⇒ 同一条 run 的并发 resume 双双重启（重复 AI 调用 / 重复写库）。
  目标用例 ``test_concurrent_mark_run_running_only_one_wins`` /
  ``test_mark_run_running_rejects_non_paused_run`` 必红。
* **B36-SERIALIZE-1** 把 ``_update_node_row`` 的兜底换回裸 ``_dump_json``
  ⇒ 节点产出不可序列化时 ``TypeError`` 穿出 ``_run_nodes``，run 永久 RUNNING。
  目标用例 ``test_unserializable_node_output_fails_run_instead_of_stuck_running`` 必红。
* **B36-DELTA-1** 撤掉 ``_finalize_run`` 的 delta 清理
  ⇒ FAILED / CANCELLED run 遗留的 proposed/validated delta 永久悬空。
  目标用例 ``test_failed_run_supersedes_only_pending_deltas`` 等必红。

⚠️ 三条互不覆盖：只钉 RESUME 时序列化缺陷能全绿；只钉 SERIALIZE 时并发 resume
能全绿；只钉两者时 delta 残留能全绿。
"""

MUTATIONS = [
    (
        "B36-RESUME-1",
        "packages/core/workflow_runtime/engine.py",
        "                    \"UPDATE workflow_runs SET status = 'RUNNING', ended_at = NULL \"\n"
        "                    \"WHERE run_id = ? AND status = 'PAUSED'\",\n",
        "                    \"UPDATE workflow_runs SET status = 'RUNNING', ended_at = NULL \"\n"
        "                    \"WHERE run_id = ?\",\n",
        "tests/unit/test_b36_engine_resume_and_finalize.py",
    ),
    (
        "B36-SERIALIZE-1",
        "packages/core/workflow_runtime/engine.py",
        "        if output is not None:\n"
        "            output_json, dump_err = _dump_json_safe(output)\n",
        "        if output is not None:\n"
        "            output_json, dump_err = _dump_json(output), None\n",
        "tests/unit/test_b36_engine_resume_and_finalize.py",
    ),
    (
        "B36-DELTA-1",
        "packages/core/workflow_runtime/engine.py",
        "            if actual_terminal in (\"FAILED\", \"CANCELLED\"):\n"
        "                delta_cur = conn.execute(\n",
        "            if False:  # MUTATION-B36-DELTA-1: 撤掉失败/取消 run 的 delta 清理\n"
        "                delta_cur = conn.execute(\n",
        "tests/unit/test_b36_engine_resume_and_finalize.py",
    ),
]
