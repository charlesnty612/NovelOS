"""C-2：分支 HIGH 风险 delta 的 promote 必须「前置预检 + 审批透传」。

缺陷形状
--------
``branches.promote_branch`` 重放分支 commit 时，传给 ``commit_delta`` 的
``author_approval`` 只有 approver/notes/promoted_from/source_commit_id，**没有
``approved: True``**；而 ``commit_delta`` 的 HIGH 风险门要求
``author_approval.get("approved") is True`` ⇒ 分支上人工批准过的
``world_kind=rule`` / ``facet=definition`` 变更，promote 时**永远 409**，
而 ``promote_branch`` 签名又无审批参数 ⇒ 前后端无出路。

修法（Lead 拍板 = 预检 + 透传）
------------------------------
1. **重放开始前**遍历待重放 delta（``high_risk_change_ids``），收集全部 HIGH
   ``change_id``；非空且未获批 ⇒ 立刻抛 ``ApprovalRequiredError``。
   ⚠️ 必须前置：重放逐条独立提交，中途失败会留下「已重放一半」的 main commits。
2. 获批时把 ``approved: True`` 并进每个重放 delta 的 ``ap``（审批人取
   ``author_approval['approver']``，缺省 ``system:promote:<branch_id>``）。

本规格两条
----------
* **PROMOTE-APPROVAL-1**：撤掉前置预检 ⇒ 夹具（先 LOW 后 HIGH）会先把 LOW 重放
  成一条 main commit，才在 HIGH 炸掉 ⇒ ``test_promote_high_risk_precheck_blocks_before_any_replay``
  的「main commit 数仍为 0」断言必红。
* **PROMOTE-APPROVAL-2**：撤掉 ``ap["approved"] = True`` ⇒ 获批 promote 在重放
  HIGH 那条时仍被 ``commit_delta`` 门禁拒绝 ⇒
  ``test_promote_high_risk_succeeds_with_approval_and_audits_approver`` 必红。

⚠️ replacement 只删机制、不引入未定义名字（避免 NameError/ImportError 被误记
「捕获」——AGENTS 坑区「变异改得太狠 ⇒ INVALID」）。
"""

MUTATIONS = [
    (
        "PROMOTE-APPROVAL-1",
        "packages/core/story_state/branches.py",
        "    if high_risk_ids and not approved:\n"
        "        raise ApprovalRequiredError(\n"
        "            f\"分支 {branch_id!r} 的重放 delta 含 HIGH 风险 change \"\n"
        "            f\"{len(high_risk_ids)} 条，需 author_approval.approved=true 才能 promote\",\n"
        "            high_risk_change_ids=high_risk_ids,\n"
        "        )\n",
        "",
        "tests/unit/test_b36_story_state.py",
    ),
    (
        "PROMOTE-APPROVAL-2",
        "packages/core/story_state/branches.py",
        "            if approved:\n"
        "                # C-2：HIGH 风险 delta 重放必须显式带审批，否则 commit_delta 的门禁\n"
        "                # 恒拒（此前这里没带 ⇒ 含 HIGH 的分支 promote **永远 409**）。\n"
        "                ap[\"approved\"] = True\n",
        "",
        "tests/unit/test_b36_story_state.py",
    ),
]
