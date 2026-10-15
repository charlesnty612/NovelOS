"""L4（评审 / 拆书 / 风格样本的**列表**端点）契约的突变规格（2026-10-15 批次（六））。

数据文件，执行器是共用的 ``tests/mutation_specs/run_spec.py``（**不许改**）::

    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py \
        tests/mutation_specs/l4_quality_reference_list.py

⚠️ **本规格只写不跑**：执行器会**临时改源码**，与并发成员的 pytest 同跑会污染他们的
结果（收尾闸门由 Lead 用 ``--all`` 统一跑）。

三条硬要求（执行器逐条查，查不过即判脚本自身缺陷并中止）：

1. 锚点在目标文件里**恰好命中 1 次**（``count != 1`` ⇒ ``[ABORT]``）；
2. 变异必须**真的改字节**（写入前后 SHA256 不同 ⇒ 否则 ``[ABORT]``「变异没生效」）；
3. 变异必须让 ``tests/api/test_quality_reference_list_response_contract.py``
   **红**，且红的是**预期那条**。

⚠️ 第三条最容易被绕过：**等价变异**（改了等于没改）会让脚本报「存活」，把「该测试
测不到这个机制」误记成「修复无效」。本文件每条都在注释里写明「靠哪条用例、靠哪个
判别点红」——靠「响应键集」还是靠「OpenAPI properties」，二者**失效方向相反**。

分组（按失效方向排——同是「少声明一个字段」，红的位置完全不同）：

- **A 组 · 撤掉 ``response_model``**（M01~M03）：端点退回 ``list[dict]`` ⇒
  OpenAPI 里 items 变回匿名 object ⇒ ``test_every_modeled_list_declares_a_named_item_ref``
  的**形状**断言必红。
  ⚠️ 只断言「schema 里存在这个模型」抓不到（模型被别的端点引用着）——所以本组的
  判别点是 ``path → array → items.$ref`` 的**逐跳**指向。
- **B 组 · 少声明一个字段**（M04/M05）：两组的**红点不同**，这正是要分开的原因——
  - M04（``CanonSummaryOut`` 开了 ``extra="allow"``）：响应**照样**有那个键，
    只有 ``set(model_fields)`` 与 OpenAPI ``properties`` 会红。
    ⇒ 若测试只断言响应键集，本条**存活**（坑区 M20 的形状）。
  - M05（``StyleSample`` **不开** ``extra``）：那个键会被 ``response_model``
    **过滤掉**，响应键集直接少一个 ⇒ 断言响应用例就够红。
  两组都留着，是为了把「有没有 extra」这个变量对失效方向的**影响本身**钉住。
- **C 组 · 关掉 ``extra="allow"``**（M06/M07）：多出来的键被静默裁掉 ⇒
  「未知键透传」用例必红。这组守的是「数据在库里、API 读不到、零报错」那族缺陷。
- **D 组 · 形状混淆**（M08）：canon 列表改成吐**单体**投影。两边都是 9 键、极易被
  读成同形 ⇒ 本组守的正是「同键数 ≠ 同形状」这条推理陷阱。
"""

from __future__ import annotations

TEST_FILE = "tests/api/test_quality_reference_list_response_contract.py"

MUTATIONS = [
    # ------------------------------------------------- A 组：撤掉 response_model
    (
        # items 退回匿名 object ⇒ 形状用例红（不是「模型没了」那类红）。
        "L4-M01",  # GET /projects/{pid}/quality 列表不声明 response_model
        "packages/core/api/routers/quality.py",
        '@router.get(\n'
        '    "/projects/{project_id}/quality",\n'
        "    response_model=list[QualityReportResponse],\n"
        ")",
        "@router.get(\n"
        '    "/projects/{project_id}/quality",\n'
        "    # ⚠️ MUTATION-L4-M01：撤掉列表端点的 response_model\n"
        ")",
        TEST_FILE,
    ),
    (
        # 同上；判别点是 /canons 那条 items.$ref。
        "L4-M02",  # GET /projects/{pid}/canons 列表不声明 response_model
        "packages/core/api/routers/reference.py",
        '@router.get("/projects/{project_id}/canons", response_model=list[CanonSummaryOut])',
        '@router.get("/projects/{project_id}/canons")',
        TEST_FILE,
    ),
    (
        # 同上；判别点是 style-samples 那条 items.$ref + 单体 POST 的 $ref 相等断言。
        "L4-M03",  # GET /projects/{pid}/style-samples 列表不声明 response_model
        "packages/core/api/routers/author_style_samples.py",
        '@router.get("/projects/{pid}/style-samples", response_model=list[StyleSample])',
        '@router.get("/projects/{pid}/style-samples")',
        TEST_FILE,
    ),
    # ------------------------------------------------- B 组：少声明一个字段
    (
        # ⚠️ 开了 extra="allow" ⇒ **响应键集抓不到**（键仍以 extra 身份透出）。
        # 判别点：set(CanonSummaryOut.model_fields) == EXPECTED_CANON_SUMMARY_KEYS
        #          与 OpenAPI properties；外加必填集变化（少一个必填列）。
        "L4-M04",  # CanonSummaryOut 少声明 rhythm_chapter_count
        "packages/domain/reference/models.py",
        '    logline: str = ""\n    spine_count: int = 0\n    rhythm_chapter_count: int = 0',
        '    logline: str = ""\n    spine_count: int = 0\n'
        "    # ⚠️ MUTATION-L4-M04：删掉 rhythm_chapter_count 声明",
        TEST_FILE,
    ),
    (
        # ⚠️ 不开 extra ⇒ 该键被 response_model **过滤掉** ⇒ 响应键集直接少一个。
        # 与 M04 的失效方向相反：两者的红点不同，这就是两组都要留的理由。
        "L4-M05",  # StyleSample 少声明 updated_at
        "packages/core/api/routers/author_style_samples.py",
        "    title: str\n    content: str\n    created_at: str\n    updated_at: str",
        "    title: str\n    content: str\n    created_at: str\n"
        "    # ⚠️ MUTATION-L4-M05：删掉 updated_at 声明",
        TEST_FILE,
    ),
    # ------------------------------------------------- C 组：关掉 extra="allow"
    (
        # ⚠️ summary_of 返回**手写字面量 dict**，没有天然注入点 ⇒ 本组的判别点是
        # 那条 monkeypatch service 多返回一个 technique_count 的用例。
        # 锚点必须带到 logline 才唯一（CanonDetailOut 也是 extra=allow + canon_id 起头）。
        "L4-M06",  # CanonSummaryOut 关掉 extra ⇒ 未来派生键被静默裁掉
        "packages/domain/reference/models.py",
        '    model_config = ConfigDict(extra="allow")\n\n'
        "    canon_id: str\n"
        "    project_id: str\n"
        "    title: str\n"
        "    reader_profile: str\n"
        "    status: str\n"
        "    created_at: str\n"
        "    # 以下三个由 summary_of 从 canon_json 派生；解析失败/类型不符时回落为 \"\" / 0。\n"
        '    logline: str = ""',
        '    model_config = ConfigDict(extra="ignore")  # ⚠️ MUTATION-L4-M06\n\n'
        "    canon_id: str\n"
        "    project_id: str\n"
        "    title: str\n"
        "    reader_profile: str\n"
        "    status: str\n"
        "    created_at: str\n"
        "    # 以下三个由 summary_of 从 canon_json 派生；解析失败/类型不符时回落为 \"\" / 0。\n"
        '    logline: str = ""',
        TEST_FILE,
    ),
    (
        # ⚠️ 与 G7-M08 **意图重叠但判别点不同**：G7 靠单体 GET 的「未知 issue 键透出」
        # 用例，本条靠**列表侧**独有的 ALTER TABLE 缝（quality_reports 的列表是
        # SELECT *，新列会出现在行里）。两条都得红——否则说明单体的测试没把列表的缝堵上。
        "L4-M07",  # QualityReportResponse 关掉 extra ⇒ SELECT * 的新列被静默裁掉
        "packages/core/quality/models.py",
        '    model_config = ConfigDict(extra="allow")\n\n'
        "    report_id: str\n"
        "    project_id: str\n"
        "    chapter_id: str\n"
        "    commit_id: Optional[str] = None",
        '    model_config = ConfigDict(extra="ignore")  # ⚠️ MUTATION-L4-M07\n\n'
        "    report_id: str\n"
        "    project_id: str\n"
        "    chapter_id: str\n"
        "    commit_id: Optional[str] = None",
        TEST_FILE,
    ),
    # ------------------------------------------------- D 组：形状混淆
    (
        # ⚠️ 守的是一条**推理陷阱**：canon 列表与 canon 单体「都是 9 键」，
        # 于是「同一条数据、同一个键数」极易被当成同形 ⇒ 有人拿 CanonDetailOut 去套
        # 列表端点。改成吐 detail 投影后：响应键集从 9 变 12（多 report_md /
        # canon_json / extracts，缺三个派生量），基准断言与「不同形」用例双双报红。
        "L4-M08",  # canons 列表改成吐单体 detail 投影
        "packages/core/api/routers/reference.py",
        "    svc = ReferenceService(_db_path(request))\n"
        "    return svc.list_active_summaries(project_id)",
        "    svc = ReferenceService(_db_path(request))\n"
        "    # ⚠️ MUTATION-L4-M08：改成吐 detail 投影\n"
        "    return [\n"
        "        svc.get_canon_detail(c['canon_id'])\n"
        "        for c in svc.list_active_summaries(project_id)\n"
        "    ]",
        TEST_FILE,
    ),
]
