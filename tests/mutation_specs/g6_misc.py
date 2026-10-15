"""批次（六）· G6 备份/导出/杂项 router 的突变规格（纯数据）。

每条 = ``(mid, 文件路径, 锚点, 替换, 目标测试文件)``，由共用的
``tests/mutation_specs/run_spec.py`` 执行（该执行器自带四条硬断言：
绝对路径起进程 / 启动失败与测试失败分桶 / 必须有「跑过了」的痕迹 / 锚点未命中即中止）。

设计口径
--------
1. **每条突变必须真的改变行为**——把锚点换成一段「跑起来看不出区别」的文本，
   脚本报存活只是量具失灵，不代表被测物安全（AGENTS 坑区「变异等于没改」）。
2. **锚点在目标文件里必须恰好命中 1 次**，否则执行器按设计报 ``[ABORT]``。
   本文件末尾的 ``python tests/mutation_specs/run_spec.py`` 自带该检查，改锚点后
   必须重跑确认没有变成「跳过」。
3. 判别点按**失效方向**分布，不是凑数：
   - ``少声明一个字段``：对 ``extra="allow"`` 的模型**不改变响应体**（字段以 extra
     身份透出），只能靠 OpenAPI ``properties`` 抓住——这正是本组把「响应键集」
     与「schema properties」两条断言都写上的理由（M14/M20 同族）。
   - ``去掉 response_model``：OpenAPI 的 ``$ref`` 消失。
   - ``去掉 extra="allow"``：迁移新增列被静默裁掉（症状：数据在库里、API 读不到）。
   - ``去掉 response_model_exclude_unset``：条件键从「缺席」变成凭空的 ``null``。
4. ⚠️ **本执行器会临时改源码 ⇒ 禁与任何 pytest 并发跑**（含 ``-n 4``）。
"""

from __future__ import annotations

_TEST = "tests/api/test_misc_router_response_contract.py"

MUTATIONS = [
    # ---------------------------------------------------------------- backup
    (
        "G6M1-backup-missing-field",
        "packages/core/api/routers/backup.py",
        "    exported_from_project_id: str\n    metadata: BackupMetadata",
        "    metadata: BackupMetadata",
        _TEST,
    ),
    (
        "G6M2-backup-no-response-model",
        "packages/core/api/routers/backup.py",
        '@router.get("/projects/{project_id}/backup", response_model=BackupPackage)',
        '@router.get("/projects/{project_id}/backup")',
        _TEST,
    ),
    (
        "G6M3-import-missing-field-extra-allow",
        "packages/core/api/routers/backup.py",
        "    writing_bible: str | None = None\n"
        "    # 迁移 0031：NULL=未设置 / 0=显式关 / 1=显式开（三态，勿当 bool 用）",
        "    # 迁移 0031：NULL=未设置 / 0=显式关 / 1=显式开（三态，勿当 bool 用）",
        _TEST,
    ),
    (
        "G6M4-import-drop-extra-allow",
        "packages/core/api/routers/backup.py",
        '    model_config = ConfigDict(extra="allow")\n\n    project_id: str',
        "    project_id: str",
        _TEST,
    ),
    (
        "G6M5-import-drop-exclude-unset",
        "packages/core/api/routers/backup.py",
        "    # 而后者恰恰是要作者看见的。exclude_unset 让「键缺席」保持缺席。\n"
        "    response_model_exclude_unset=True,\n",
        "",
        _TEST,
    ),
    # --------------------------------------------------------------- export
    (
        "G6M6-export-give-response-model",
        "packages/core/api/routers/export.py",
        '@router.get("/projects/{project_id}/export")',
        '@router.get("/projects/{project_id}/export", response_model=dict)',
        _TEST,
    ),
    # ---------------------------------------------------------- signing check
    (
        "G6M7-signing-drop-exclude-unset",
        "packages/core/api/routers/signing_check.py",
        "    response_model=SigningCheckResult,\n    response_model_exclude_unset=True,\n",
        "    response_model=SigningCheckResult,\n",
        _TEST,
    ),
    (
        "G6M8-signing-missing-field",
        "packages/core/api/routers/signing_check.py",
        '    # "<pack_id>@<version>" 或 "default"\n    word_band_source: str\n',
        '    # "<pack_id>@<version>" 或 "default"\n',
        _TEST,
    ),
    (
        "G6M9-signing-no-response-model",
        "packages/core/api/routers/signing_check.py",
        "@router.get(\n"
        '    "/projects/{project_id}/signing-check",\n'
        "    response_model=SigningCheckResult,\n"
        "    response_model_exclude_unset=True,\n"
        ")",
        '@router.get("/projects/{project_id}/signing-check")',
        _TEST,
    ),
    (
        "G6M10-signing-genre-rule-missing-field",
        "packages/core/api/routers/signing_check.py",
        "    status: str\n    detail: str\n    keywords_source: str | None = None",
        "    status: str\n    detail: str",
        _TEST,
    ),
    # ------------------------------------------------------------ ai-call-logs
    (
        "G6M11-ailog-missing-field",
        "packages/core/api/routers/ai_call_logs.py",
        "    # runner.py L52-69：只存 *_id 键值，**原文不落 DB**\n    input_context_ids: list[str]\n",
        "    # runner.py L52-69：只存 *_id 键值，**原文不落 DB**\n",
        _TEST,
    ),
    (
        "G6M12-ailog-leak-api-key",
        "packages/core/api/routers/ai_call_logs.py",
        "    cost: float | None = None\n    created_at: str\n",
        "    cost: float | None = None\n    api_key: str | None = None\n    created_at: str\n",
        _TEST,
    ),
    (
        "G6M13-ailog-no-response-model",
        "packages/core/api/routers/ai_call_logs.py",
        '@router.get("/ai-call-logs/{log_id}", response_model=AiCallLogDetail)',
        '@router.get("/ai-call-logs/{log_id}")',
        _TEST,
    ),
    # ---------------------------------------------------------- style samples
    (
        "G6M14-style-missing-field",
        "packages/core/api/routers/author_style_samples.py",
        "    content: str\n    created_at: str\n    updated_at: str",
        "    content: str\n    created_at: str",
        _TEST,
    ),
    (
        "G6M15-style-no-response-model",
        "packages/core/api/routers/author_style_samples.py",
        "    status_code=status.HTTP_201_CREATED,\n    response_model=StyleSample,\n",
        "    status_code=status.HTTP_201_CREATED,\n",
        _TEST,
    ),
]
