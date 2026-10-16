"""E1（2026-10-16 外部报告「角色圣经写闺蜜、正文里却是业务员」）的突变规格。

缺陷形状：``characters.core_json`` 整份进 payload，而 writer / director 装配缓存键
**无角色维度** ⇒ 改角色设定 / 手动新增角色后重跑同一章（同 chapter_no + 同
state_version）命中旧装配。修法 = 加 ``chars_fp`` 键维度
（``packages/core/context_engine/cache.py::characters_fingerprint``）。

本组刻意成对/成组，覆盖三个不同的失效方向：

* **撤掉修复**（E1-1 / E1-2）：任一侧删掉 ``chars_fp``，该侧全部脏命中用例必红；
* **改坏修复**（E1-3 ~ E1-7）：指纹算错 / 少算列 / 多算列 / 把时间戳算进去——
  只钉「撤掉修复」的话，一个 `return "const"` 或 `return MAX(updated_at)` 的实现
  照样全绿（AGENTS §三.2 正反对照：只有反向用例挡得住「一律 xxx」）；
* **守卫形同虚设**（E1-8）：删掉 ``cacheable`` 判据里的 ``chars_fp`` 分支。

⚠️ 锚点唯一性：本批并发开发会让源码漂移，锚点命中 ≠ 1 时 ``run_spec.py`` 判
``[ABORT]``（按设计强制中止，不是「跳过」）⇒ 先跑只读预检
``.venv/Scripts/python.exe tests/mutation_specs/validate_specs.py e1_character_cache_key.py``。
"""

MUTATIONS = [
    # ------------------------------------------------------------------
    # 撤掉修复：writer 键退回 15 元（无角色维度）
    # ------------------------------------------------------------------
    (
        "E1-1",
        "packages/core/context_engine/writer_input.py",
        "target_word_count, genre_pack_ref, intent_fp, bible_fp, chars_fp,\n"
        "        plan_fp, prompt_label, _cache_namespace_tag(namespace),\n",
        "target_word_count, genre_pack_ref, intent_fp, bible_fp,\n"
        "        plan_fp, prompt_label, _cache_namespace_tag(namespace),  # E1-MUT: 撤角色维度\n",
        "tests/unit/test_e1_character_cache_key.py",
    ),
    # ------------------------------------------------------------------
    # 撤掉修复：director 键退回 13 元
    # ------------------------------------------------------------------
    (
        "E1-2",
        "packages/core/context_engine/director_input.py",
        "        chars_fp, prompt_label,\n        _cache_namespace_tag(namespace),\n",
        "        prompt_label,  # E1-MUT: 撤角色维度\n        _cache_namespace_tag(namespace),\n",
        "tests/unit/test_e1_character_cache_key.py",
    ),
    # ------------------------------------------------------------------
    # 指纹退化成常量（缺了「随内容变」这一半，改动后仍全部命中）
    # ------------------------------------------------------------------
    (
        "E1-3",
        "packages/core/context_engine/cache.py",
        "        normalized = json.dumps(normalized_rows, sort_keys=True, ensure_ascii=False)\n"
        "        return hashlib.sha256(normalized.encode(\"utf-8\")).hexdigest()[:_FINGERPRINT_LEN]\n",
        "        _ = normalized_rows  # E1-MUT: 指纹退化成常量\n"
        "        return \"0000000000000000\"\n",
        "tests/unit/test_e1_character_cache_key.py",
    ),
    # ------------------------------------------------------------------
    # 少算列：把决定「条目集合与形态」的 aliases / inject_mode 摘掉
    # （core_json 仍覆盖 ⇒ 只钉 core_json 的用例仍绿，正是「漏一列 = 又一次 outline_json」）
    # ------------------------------------------------------------------
    (
        "E1-4",
        "packages/core/context_engine/cache.py",
        "    SELECT c.character_id, c.name, c.role, c.core_json, c.visibility,\n"
        "           c.aliases, c.inject_mode, c.who_knows,\n",
        "    SELECT c.character_id, c.name, c.role, c.core_json, c.visibility,\n"
        "           c.who_knows,  -- E1-MUT: aliases / inject_mode 摘掉\n",
        "tests/unit/test_e1_character_cache_key.py",
    ),
    # ------------------------------------------------------------------
    # 少算列：摘掉 character_states 的 LEFT JOIN（current_state / latest_state_version）
    # ⚠️ 必须把 SELECT 里的 cs.* 两列**一并**摘掉：只摘 JOIN 会让 SQL 报
    # ``no such column: cs.state_version``——而 ``no such column`` 正是
    # ``is_missing_schema_error`` 的标记 ⇒ 被降级成 ``_FINGERPRINT_NONE`` ⇒ 表现为
    # 「全部 miss」而不是「改状态不 miss」，红的是**另一批**用例（失败点漂移）。
    # ------------------------------------------------------------------
    (
        "E1-5",
        "packages/core/context_engine/cache.py",
        "    SELECT c.character_id, c.name, c.role, c.core_json, c.visibility,\n"
        "           c.aliases, c.inject_mode, c.who_knows,\n"
        "           cs.state_version AS latest_state_version,\n"
        "           cs.state_json   AS latest_state_json\n"
        "    FROM characters c\n"
        "    LEFT JOIN character_states cs\n"
        "      ON cs.character_id = c.character_id\n"
        "     AND cs.state_version = (\n"
        "          SELECT MAX(state_version) FROM character_states WHERE character_id = c.character_id\n"
        "     )\n"
        "    WHERE c.project_id = ?\n",
        "    SELECT c.character_id, c.name, c.role, c.core_json, c.visibility,\n"
        "           c.aliases, c.inject_mode, c.who_knows\n"
        "           -- E1-MUT: 摘掉 character_states（current_state 漏算）\n"
        "    FROM characters c\n"
        "    WHERE c.project_id = ?\n",
        "tests/unit/test_e1_character_cache_key.py",
    ),
    # ------------------------------------------------------------------
    # 多算：把记账时间戳算进指纹（「只 bump updated_at」也会 miss ⇒ 白付重装）
    # ------------------------------------------------------------------
    (
        "E1-6",
        "packages/core/context_engine/cache.py",
        "           c.aliases, c.inject_mode, c.who_knows,\n",
        "           c.aliases, c.inject_mode, c.who_knows,\n"
        "           c.updated_at,  -- E1-MUT: 把记账时间戳算进指纹\n",
        "tests/unit/test_e1_character_cache_key.py",
    ),
    # ------------------------------------------------------------------
    # 过度修正：任何异常都返回稳定占位符（静默降级 = 把「读不到」当成「没改过」）
    # ------------------------------------------------------------------
    (
        "E1-7",
        "packages/core/context_engine/cache.py",
        "    except sqlite3.OperationalError as exc:\n"
        "        if not is_missing_schema_error(exc):\n"
        "            raise\n"
        "        return _FINGERPRINT_NONE\n"
        "    try:\n"
        "        rows = conn.execute(_CHARACTERS_FINGERPRINT_SQL, (project_id,)).fetchall()\n",
        "    except sqlite3.OperationalError:\n"
        "        return _FINGERPRINT_NONE  # E1-MUT: 吞掉锁 / I/O 故障\n"
        "    try:\n"
        "        rows = conn.execute(_CHARACTERS_FINGERPRINT_SQL, (project_id,)).fetchall()\n",
        "tests/unit/test_characters_fingerprint_error_scope.py",
    ),
    # ------------------------------------------------------------------
    # 守卫形同虚设：cacheable 判据里删掉 chars_fp 分支
    # ------------------------------------------------------------------
    (
        "E1-8",
        "packages/core/context_engine/writer_input.py",
        "        and bible_fp != _FINGERPRINT_UNCACHED\n"
        "        and chars_fp != _FINGERPRINT_UNCACHED\n",
        "        and bible_fp != _FINGERPRINT_UNCACHED\n"
        "        # E1-MUT: 守卫去掉 chars_fp 分支\n",
        "tests/unit/test_e1_character_cache_key.py",
    ),
]
