"""``packages/core/agent_runtime/structured_output.py`` 的契约闸门突变（2026-10-23 批次二十七）。

背景（审计 A Y-2）
-----------------
该文件 885 行、承载 `validate_contract` 的全部结构闸门，此前**零突变规格**（227 个 .py
中 72 个有自身规格，agent_runtime 6 文件 2104 行零覆盖）。而它的失效方向恰恰是
「静默放行」：闸门被摘掉后，腐烂输出会穿过契约直接落库（2026-09-21 已发生一次）。

五条突变各钉一个闸门（对应本批 Y-1 补的反例，撤前已确认反例存在）
--------------------------------------------------------------
* **SO-DIR-SCHEMA** 撤 `director` 家族 `schema_version` 校验 ⇒
  ``test_validate_contract_director_ok`` 的 Y-1 反例必红。
* **SO-DIR-ARRAY** 撤计划数组形状核销（`_DIRECTOR_PLAN_ARRAYS` 循环体置空）⇒
  ``test_validate_contract_director_rejects_non_object_element`` 的 Y-1 反例必红
  （腐烂 key_beats 被放行）。
  ⚠️ **2026-10-23 task-23 订正（声称与实测对齐）**：本条原先声称由
  ``test_director_planner_scene_plan_optional_plan_only`` /
  ``test_missing_plan_arrays_still_pass`` 承载 ⇒ **那两条在
  ``tests/unit/test_director_planner_contract.py``**，而本规格的 test_file 是
  ``tests/unit/test_structured_output.py`` ⇒ 那个文件里**一条计划数组形状反例都
  没有**（只有 observer 的形状反例）⇒ 突变被跑到时**无人红** ⇒ ``[SURVIVED]``。
  ⇒ 已在 ``tests/unit/test_structured_output.py`` 补承载用例（三个计划数组各一条
  非 object 元素反例 + key_beats 必填串字段 + 非 list 形态），并把守卫**覆盖面**
  钉住（恰好三个数组）⇒ 实跑 **5/5 CAUGHT**。
* **SO-WHITELIST** 撤 `director_planner` 的 input ID 白名单（`_whitelist_errors` 短路）⇒
  ``test_whitelist_accepts_ids_present_in_input`` / ``test_whitelist_null_location_allowed``
  / ``test_whitelist_not_applied_when_other_expected`` 的 Y-1 反例必红。
* **SO-OBSERVER-MISSING** 撤 observer「7 数组必须齐全」核销 ⇒
  ``test_validate_contract_observer_ok`` 的 Y-1 反例必红。
* **SO-WRITER-PROSE** 撤 writer「prose 必填」核销 ⇒
  ``test_validate_contract_writer_ok`` 的 Y-1 反例必红。

锚点均为多行上下文（单行 ``if`` 在多处同形，容易误命中）；replacement 不含双引号
（PowerShell argv 剥引号，见 ``q1_style_sample_key.py`` 留痕）。

⚠️ **门禁盲区留痕（Lead 登记，task-23 同批修）**：本规格的目标文件
``packages/core/agent_runtime/structured_output.py`` 自批次二十五建规格后**未改动**
⇒ ``run_spec --changed`` 按「目标文件是否改动」筛选 ⇒ **本规格从未被选中** ⇒
上面那条存活突变在批次二十五/二十六/二十七的门禁里**都没跑过** ⇒
「三批门禁全绿」并不等于「每条突变都被跑到过」。
"""

MUTATIONS = [
    (
        "SO-DIR-SCHEMA",
        "packages/core/agent_runtime/structured_output.py",
        '    if payload.get("schema_version") != "director-plan.v1":\n'
        "        raise AgentOutputError(\n",
        "    if False:  # MUTATION-SO-DIR-SCHEMA: 撤 schema_version 校验\n"
        "        raise AgentOutputError(\n",
        "tests/unit/test_structured_output.py",
    ),
    (
        "SO-DIR-ARRAY",
        "packages/core/agent_runtime/structured_output.py",
        "    for key, required_str_fields in _DIRECTOR_PLAN_ARRAYS:\n"
        "        errs.extend(_array_shape_errors(payload, key, required_str_fields))\n",
        "    for key, required_str_fields in _DIRECTOR_PLAN_ARRAYS:\n"
        "        pass  # MUTATION-SO-DIR-ARRAY: 撤计划数组形状核销\n",
        "tests/unit/test_structured_output.py",
    ),
    (
        "SO-WHITELIST",
        "packages/core/agent_runtime/structured_output.py",
        "    if input_payload is not None:\n"
        "        errs = _whitelist_errors(payload, input_payload)\n",
        "    if input_payload is not None:\n"
        "        errs = []  # MUTATION-SO-WHITELIST: 撤 input ID 白名单\n",
        "tests/unit/test_director_planner_contract.py",
    ),
    (
        "SO-OBSERVER-MISSING",
        "packages/core/agent_runtime/structured_output.py",
        "    missing = OBSERVER_ALLOWED_KEYS - keys\n"
        "    if missing:\n",
        "    missing = set()  # MUTATION-SO-OBSERVER-MISSING: 撤缺数组核销\n"
        "    if missing:\n",
        "tests/unit/test_structured_output.py",
    ),
    (
        "SO-WRITER-PROSE",
        "packages/core/agent_runtime/structured_output.py",
        '    if "prose" not in payload:\n'
        '        raise AgentOutputError("writer output missing required field \'prose\'")\n',
        '    if False:  # MUTATION-SO-WRITER-PROSE: 撤 prose 必填\n'
        '        raise AgentOutputError("writer output missing required field \'prose\'")\n',
        "tests/unit/test_structured_output.py",
    ),
]
