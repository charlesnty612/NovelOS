"""G7（题材包 / 评审 / 拆书）response_model 契约的突变规格（2026-10-15 批次（六））。

数据文件，执行器是共用的 ``tests/mutation_specs/run_spec.py``（**不许改**）::

    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py \
        tests/mutation_specs/g7_genre_quality_reference.py

每条突变都必须**真的改变行为**——等价变异（改了等于没改）会让执行器报
``[ABORT] SHA256 未变``，或者更糟：报告成「存活」，把「该测试测不到这个机制」
误记成「修复无效」。

⚠️ 本文件三条硬要求（执行器会逐条查，查不过就是脚本自身缺陷）：

1. **锚点在目标文件里恰好命中 1 次**（``original.count(anchor) != 1`` 即 ABORT）；
2. 变异必须改字节（执行器算写入前后的 SHA256）；
3. 变异必须让 ``tests/api/test_genre_quality_reference_response_contract.py``
   **红**，且红的是**预期那条**。

分组（按失效方向排——三类的「变异存活」含义完全不同）：

- **A 组 · 少声明一个字段**（M06/M10/M11/M15/M17）：模型少了字段 ⇒
  ``set(Model.model_fields) == EXPECTED_*`` 与 OpenAPI ``properties`` 两条断言必红。
  ⚠️ 本组的**响应键集断言抓不到**（``extra="allow"`` 下少声明的键照样出现在响应里），
  这正是 AGENTS.md 坑区「断言了结果 ≠ 判别了机制」的 M20 形状。
- **B 组 · 去掉 response_model**（M01~M05 / M12~M14 / M16）：端点不再声明契约 ⇒
  OpenAPI 里该 path 的 ``$ref`` 消失，**接线断言**必红。
  ⚠️ 只断言「schema 里存在这个模型」抓不到（模型被别的端点引用着）。
- **C 组 · 去掉 extra="allow"**（M07/M08/M09/M18）：多出来的键被 ``response_model``
  静默裁掉 ⇒ 「未知键透传」用例必红。这组守的是「数据在库里、API 读不到、零报错」
  那族缺陷。
"""

from __future__ import annotations

TEST_FILE = "tests/api/test_genre_quality_reference_response_contract.py"

MUTATIONS = [
    # ---------------------------------------------------------------- A 组：少声明一个字段
    (
        "G7-M06",  # GenrePackBinding 少声明 pack_id
        "packages/core/genre/models.py",
        "    project_id: str\n    pack_id: Optional[str] = None\n    bound: bool",
        "    project_id: str\n    bound: bool",
        TEST_FILE,
    ),
    (
        "G7-M10",  # QualityReportIssue 少声明 gate（正交轴消失）
        "packages/core/quality/models.py",
        '    # 正交轴：回答「谁能说通过」，不从 severity 推导（2026-09-18 P0-1）。\n'
        '    # 默认 "auto" 同时承担「2026-09-18 前的存量 issue 无此键」的兼容职责。\n'
        '    gate: Gate = "auto"',
        "    # ⚠️ MUTATION-G7-M10：删掉 gate 声明",
        TEST_FILE,
    ),
    (
        "G7-M11",  # DeconstructStartResponse 少声明 error
        "packages/domain/reference/models.py",
        "    # 仅 FAILED 时带出（run.error）\n    error: Optional[str] = None",
        "    # ⚠️ MUTATION-G7-M11：删掉 error 声明",
        TEST_FILE,
    ),
    (
        "G7-M15",  # CanonDetailOut 少声明 extracts
        "packages/domain/reference/models.py",
        "    created_at: str\n    extracts: list[CanonExtractOut] = Field(default_factory=list)",
        "    created_at: str\n    # ⚠️ MUTATION-G7-M15：删掉 extracts 声明",
        TEST_FILE,
    ),
    (
        "G7-M17",  # StyleSampleOut 少声明 content
        "packages/domain/reference/models.py",
        "    title: str\n    content: str\n    created_at: str\n    updated_at: str",
        "    title: str\n    # ⚠️ MUTATION-G7-M17：删掉 content 声明\n"
        "    created_at: str\n    updated_at: str",
        TEST_FILE,
    ),
    # ---------------------------------------------------------------- B 组：去掉 response_model
    (
        "G7-M01",  # GET /chapters/{cid}/quality 不声明 response_model
        "packages/core/api/routers/quality.py",
        '@router.get("/chapters/{chapter_id}/quality", response_model=QualityReportResponse)',
        '@router.get("/chapters/{chapter_id}/quality")',
        TEST_FILE,
    ),
    (
        "G7-M02",  # POST .../quality/evaluate 不声明 response_model
        "packages/core/api/routers/quality.py",
        "    status_code=status.HTTP_201_CREATED,\n    response_model=QualityReportResponse,\n",
        "    status_code=status.HTTP_201_CREATED,\n    # ⚠️ MUTATION-G7-M02：删掉 response_model\n",
        TEST_FILE,
    ),
    (
        "G7-M03",  # GET /projects/{pid}/genre-pack 不声明 response_model
        "packages/core/api/routers/genre.py",
        '@router.get("/projects/{project_id}/genre-pack", response_model=GenrePackBinding)',
        '@router.get("/projects/{project_id}/genre-pack")',
        TEST_FILE,
    ),
    (
        "G7-M04",  # POST .../genre-pack/bind 不声明 response_model
        "packages/core/api/routers/genre.py",
        '@router.post("/projects/{project_id}/genre-pack/bind", response_model=GenrePackBinding)',
        '@router.post("/projects/{project_id}/genre-pack/bind")',
        TEST_FILE,
    ),
    (
        "G7-M05",  # POST .../genre-pack/unbind 不声明 response_model
        "packages/core/api/routers/genre.py",
        '@router.post("/projects/{project_id}/genre-pack/unbind", response_model=GenrePackBinding)',
        '@router.post("/projects/{project_id}/genre-pack/unbind")',
        TEST_FILE,
    ),
    (
        "G7-M12",  # POST .../deconstruct 不声明 response_model
        "packages/core/api/routers/reference.py",
        '    "/projects/{project_id}/deconstruct",\n'
        "    status_code=status.HTTP_201_CREATED,\n"
        "    response_model=DeconstructStartResponse,\n",
        '    "/projects/{project_id}/deconstruct",\n'
        "    status_code=status.HTTP_201_CREATED,\n"
        "    # ⚠️ MUTATION-G7-M12：删掉 response_model\n",
        TEST_FILE,
    ),
    (
        "G7-M13",  # POST .../deconstruct-upload 不声明 response_model
        "packages/core/api/routers/reference.py",
        '    "/projects/{project_id}/deconstruct-upload",\n'
        "    status_code=status.HTTP_201_CREATED,\n"
        "    response_model=DeconstructStartResponse,\n",
        '    "/projects/{project_id}/deconstruct-upload",\n'
        "    status_code=status.HTTP_201_CREATED,\n"
        "    # ⚠️ MUTATION-G7-M13：删掉 response_model\n",
        TEST_FILE,
    ),
    (
        "G7-M14",  # GET /canons/{canon_id} 不声明 response_model
        "packages/core/api/routers/reference.py",
        '@router.get("/canons/{canon_id}", response_model=CanonDetailOut)',
        '@router.get("/canons/{canon_id}")',
        TEST_FILE,
    ),
    (
        "G7-M16",  # POST .../to-style-sample 不声明 response_model
        "packages/core/api/routers/reference.py",
        '    "/projects/{project_id}/canons/{canon_id}/to-style-sample",\n'
        "    status_code=status.HTTP_201_CREATED,\n"
        "    response_model=StyleSampleOut,\n",
        '    "/projects/{project_id}/canons/{canon_id}/to-style-sample",\n'
        "    status_code=status.HTTP_201_CREATED,\n"
        "    # ⚠️ MUTATION-G7-M16：删掉 response_model\n",
        TEST_FILE,
    ),
    # ---------------------------------------------------------------- C 组：去掉 extra="allow"
    (
        "G7-M07",  # GenrePackBinding 关掉 extra="allow" ⇒ 未来派生键被静默裁掉
        "packages/core/genre/models.py",
        '    model_config = ConfigDict(extra="allow")',
        '    model_config = ConfigDict(extra="ignore")  # ⚠️ MUTATION-G7-M07',
        TEST_FILE,
    ),
    (
        "G7-M08",  # QualityReportResponse 关掉 extra="allow" ⇒ 表里新增的列被静默裁掉
        "packages/core/quality/models.py",
        '    model_config = ConfigDict(extra="allow")\n\n'
        "    report_id: str\n"
        "    project_id: str\n"
        "    chapter_id: str\n"
        "    commit_id: Optional[str] = None",
        '    model_config = ConfigDict(extra="ignore")  # ⚠️ MUTATION-G7-M08\n\n'
        "    report_id: str\n"
        "    project_id: str\n"
        "    chapter_id: str\n"
        "    commit_id: Optional[str] = None",
        TEST_FILE,
    ),
    (
        "G7-M09",  # QualityReportIssue 关掉 extra="allow" ⇒ issue 的证据扩展键被静默裁掉
        "packages/core/quality/models.py",
        '    model_config = ConfigDict(extra="allow")\n\n    severity: Severity',
        '    model_config = ConfigDict(extra="ignore")  # ⚠️ MUTATION-G7-M09\n\n'
        "    severity: Severity",
        TEST_FILE,
    ),
    (
        "G7-M18",  # CanonExtractOut 关掉 extra="allow" ⇒ extract 的派生键被静默裁掉
        "packages/domain/reference/models.py",
        '    model_config = ConfigDict(extra="allow")\n\n    extract_id: str',
        '    model_config = ConfigDict(extra="ignore")  # ⚠️ MUTATION-G7-M18\n\n'
        "    extract_id: str",
        TEST_FILE,
    ),
]
