"""「显式指定的草稿版本不存在 ⇒ 不可检」—— 突变规格（2026-11-04 · 批次三十五 批 5）。

缺陷形状（AGENTS 坑区「读不到折叠成没有」族的**第三种**）
--------------------------------------------------------
``packages/domain/chapter/draft_resolver.py`` 的 ``resolve_draft`` 对下列三者**同返 None**：
  ① 显式指定的版本不存在；
  ② 未指定版本且本章没有草稿；
  ③ （历史上）读库失败 —— 已在 2026-10-23 C-1 改成抛域异常。
⇒ 质量侧此前把 ① 与 ② **折叠成同一个值**（``draft=""``）⇒ ``score_style("")`` /
``score_pacing("")`` 都给 **100** ⇒ **质量门「量空正文」并放行**。
而评审侧（``chapter_review/pipeline.py``）用 ``if version:`` 把 ① 与 ② 分开、① 直接报错
⇒ **同一请求体字段两侧态度相反**。

⇒ 修法：质量侧按「**有没有指定版本**」把 ① 分出来 ⇒ 判**不可检**（fail-closed，硬停）。

两条突变各钉一个**正交**的失效方向：

* **DRAFT-VERSION-1** 撤掉「显式版本不存在 ⇒ 不可检」的判定
  ⇒ ``test_explicit_missing_draft_version_is_unavailable_not_empty_draft`` 与
  ``test_gate_blocks_when_explicit_draft_version_is_missing`` 必红。
* **DRAFT-VERSION-2** 撤掉「不可检时报告仍记**被指定的**那一版」
  ⇒ ``test_explicit_missing_draft_version_is_unavailable_not_empty_draft`` 必红
  （``ctx.draft_version`` 变 None ⇒ 报告标签指向「取最新」补全的版本
  ⇒ 与实际被量的东西不对应）。

⚠️ 只钉 DRAFT-VERSION-1 时，②（报告版本错标）能全绿；只钉 DRAFT-VERSION-2 时，
①（门禁放行）能全绿 ⇒ **两条缺一不可**。
"""

MUTATIONS = [
    (
        "DRAFT-VERSION-1",
        "packages/core/quality/service.py",
        "    if resolved is None and draft_version is not None and not draft_unavailable:\n",
        "    if False:  # MUTATION-DRAFT-VERSION-1: 撤掉「显式版本不存在 ⇒ 不可检」\n",
        "tests/unit/quality/test_draft_unavailable_fail_closed.py",
    ),
    (
        "DRAFT-VERSION-2",
        "packages/core/quality/service.py",
        "        reviewed_version = (\n"
        "            int(draft_version)\n"
        "            if isinstance(draft_version, int)\n"
        "            and not isinstance(draft_version, bool)\n"
        "            and draft_version > 0\n"
        "            else None\n"
        "        )\n",
        "        reviewed_version = None\n",
        "tests/unit/quality/test_draft_unavailable_fail_closed.py",
    ),
]
