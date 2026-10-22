"""genre 域 5 个零覆盖模块的突变规格（2026-10-23 批次二十六 B 路 R-1）。

背景（审计 B N-4/N-7）
---------------------
genre 域 8 个 ``.py`` 中，突变规格原先只打到 ``models.py``（g7）与 ``verifier.py``（e3）
⇒ 其余 6 个零覆盖。而它们的失效方向恰恰多是**静默**：字数目标漂移、评审要点被截断、
未知 payload 键被裁、错误串契约漂移、出参形状少键——全都不报错。

按「改错会静默」排序（task-17 板上口径）：
``target_words`` > ``consumers`` > ``model`` > ``service`` > ``queries``。

九条突变与承接用例（逐条已确认承接面存在；缺的三处已同批补在
``tests/unit/genre/test_genre_module_contract_bits.py``）
--------------------------------------------------------
| id | 撤销的修复 / 改动 | 必红用例 |
|---|---|---|
| GM-TARGET-1 | 优先级链丢掉 ``explicit``（ctx 覆盖被题材包压过） | ``test_explicit_values_beat_pack_target`` |
| GM-TARGET-2 | 撤「章节 → 项目」反查（`db_path+chapter_id` 路径失效） | ``test_write_side_resolves_pack_target`` / ``test_review_side_resolves_pack_target`` |
| GM-CONSUMERS-1 | ``_fit_text`` 永不截断文本（**纯文本自身超预算**才可观测） | ``test_critic_rubric_truncates_and_marks_within_budget``（新增 1100 字纯文本段） |
| GM-CONSUMERS-2 | 撤「核销提示 verify_hint」注入 | ``test_critic_rubric_textualizes_payoff_focus_with_verify_hint`` |
| GM-MODEL-1 | ``GenrePackPayload`` 的 ``extra="allow"`` → ``ignore``（未知键被裁） | ``test_parse_payload_preserves_unknown_keys`` |
| GM-MODEL-2 | ``parse_payload`` 的读侧兜底收窄到永不命中的异常类型 | ``test_parse_payload_tolerates_malformed_payload`` |
| GM-SERVICE-1 | 校验错误串前缀 ``[schema]`` → ``[genre]`` | ``test_validate_payload_error_string_contract`` |
| GM-SERVICE-2 | 提供 payload 时 ``version`` 不自增（装配缓存键指纹不跟随） | ``test_update_bumps_version_when_payload_provided`` |
| GM-QUERIES-1 | ``pack_row_to_dict`` 少投影 ``version`` 一列（9 键 → 8 键） | ``test_genre_quality_reference_response_contract`` 的 9 键硬断言 |

所有锚点均为**多行上下文**且全仓唯一（validate_specs / precheck 已验）；replacement
不含双引号以外的转义形态（PowerShell argv 剥引号，见 ``q1_style_sample_key.py`` 留痕）。

⚠️ **GM-CONSUMERS-1 存活诊断（2026-10-23 批次二十六 task-20，结论 = (b) 夹具未越界）**：
原承接夹具 ``taboo 400 + style 400`` 的文本 JSON 仅 **838 字符 < 1000 预算**
⇒ ``_fit_text`` 的截断分支**根本没执行**（触发截断标记的是 payoff_focus 条目循环，
尾部「保预算」段又把标记塞回去）⇒ 把截断改成「永不执行」后该用例仍全绿（SURVIVED）。
**不是**锚点打错支路（该分支对纯文本载荷确实生效），也**不是**等价突变：纯文本超预算
（``taboo_notes`` 1100 字、无 focus）时未截断会返回 1119 字符且**零截断标记**。
⇒ 已给承接用例补「纯文本超预算」段：撤截断 ⇒ 无标记 / 超预算，用例必红。
"""

MUTATIONS = [
    # ---------------------------------------------------------------- target_words
    (
        "GM-TARGET-1",
        "packages/core/genre/target_words.py",
        "    for raw in (\n        explicit,\n        plan_dict.get(\"expected_word_count\"),\n",
        "    for raw in (\n        plan_dict.get(\"expected_word_count\"),\n",
        "tests/unit/test_target_word_count_genre_pack.py",
    ),
    (
        "GM-TARGET-2",
        "packages/core/genre/target_words.py",
        "    pid = project_id\n"
        "    if not pid and db_path and chapter_id:\n"
        "        pid = _chapter_project_id(db_path, chapter_id)\n",
        "    pid = project_id\n"
        "    if False and db_path and chapter_id:  # MUTATION-GM-TARGET-2: 撤章节反查项目\n"
        "        pid = _chapter_project_id(db_path, chapter_id)\n",
        "tests/unit/test_target_word_count_genre_pack.py",
    ),
    # ---------------------------------------------------------------- consumers
    (
        "GM-CONSUMERS-1",
        "packages/core/genre/consumers.py",
        "    candidate = {**base, key: value}\n"
        "    if _json_len(candidate) <= max_chars:\n"
        "        return candidate\n",
        "    candidate = {**base, key: value}\n"
        "    if True:  # MUTATION-GM-CONSUMERS-1: 永不截断文本（预算失效）\n"
        "        return candidate\n",
        "tests/unit/test_genre_consumers_p2.py",
    ),
    (
        "GM-CONSUMERS-2",
        "packages/core/genre/consumers.py",
        "    verify_hint = _clean_text(payoff.get(\"verify_hint\"))\n"
        "    if verify_hint:\n"
        "        parts.append(f\"核销提示 {verify_hint}\")\n",
        "    verify_hint = _clean_text(payoff.get(\"verify_hint\"))\n"
        "    if False:  # MUTATION-GM-CONSUMERS-2: 撤核销提示注入\n"
        "        parts.append(f\"核销提示 {verify_hint}\")\n",
        "tests/unit/test_genre_consumers_p2.py",
    ),
    # ---------------------------------------------------------------- model
    (
        "GM-MODEL-1",
        "packages/core/genre/model.py",
        "    model_config = ConfigDict(extra=\"allow\")\n"
        "\n"
        "    schema_version: str = GENRE_PACK_SCHEMA_VERSION\n",
        "    model_config = ConfigDict(extra=\"ignore\")  # MUTATION-GM-MODEL-1: 裁掉未知键\n"
        "\n"
        "    schema_version: str = GENRE_PACK_SCHEMA_VERSION\n",
        "tests/unit/genre/test_genre_module_contract_bits.py",
    ),
    (
        "GM-MODEL-2",
        "packages/core/genre/model.py",
        "    try:\n"
        "        return GenrePackPayload.model_validate(raw)\n"
        "    except Exception:  # noqa: BLE001 —— 读侧容忍脏数据（不炸装配路径）\n"
        "        return GenrePackPayload()\n",
        "    try:\n"
        "        return GenrePackPayload.model_validate(raw)\n"
        "    except ZeroDivisionError:  # MUTATION-GM-MODEL-2: 收窄到永不命中\n"
        "        return GenrePackPayload()\n",
        "tests/unit/genre/test_genre_module_contract_bits.py",
    ),
    # ---------------------------------------------------------------- service
    (
        "GM-SERVICE-1",
        "packages/core/genre/service.py",
        "        errs.append(f\"[schema] {path_str}: {err.message}\")\n",
        "        errs.append(f\"[genre] {path_str}: {err.message}\")\n",
        "tests/unit/genre/test_genre_module_contract_bits.py",
    ),
    (
        "GM-SERVICE-2",
        "packages/core/genre/service.py",
        "            if data.payload is not None:\n"
        "                payload_json = json.dumps(data.payload, ensure_ascii=False)\n"
        "                version += 1\n",
        "            if data.payload is not None:\n"
        "                payload_json = json.dumps(data.payload, ensure_ascii=False)\n"
        "                pass  # MUTATION-GM-SERVICE-2: version 不自增\n",
        "tests/unit/test_genre_pack_service.py",
    ),
    # ---------------------------------------------------------------- queries
    (
        "GM-QUERIES-1",
        "packages/core/genre/queries.py",
        "        \"version\": int(raw.get(\"version\") or 0),\n"
        "        \"payload\": payload,\n",
        "        \"payload\": payload,\n",
        "tests/api/test_genre_quality_reference_response_contract.py",
    ),
]
