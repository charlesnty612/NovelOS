"""角色摘录保真度修复的突变规格（2026-10-16 角色设定丢失批次 · 成员 E3）。

独占源文件：``packages/core/context_engine/builders_common/excerpts.py``。
判别用例：``tests/unit/test_character_excerpt_fidelity.py``。

三条修复各自的**独立失效方向**（撤一条只让对应方向变红）：

- ``CE-1~CE-3``：**``inject_mode`` 不再进 excerpt** ⇒ ``relevance`` 里
  「always 实体始终完整保留」的三条规则退回死代码。character / location / faction
  三处**必须各有一条**：少修任何一处，该处的判别用例仍绿（这正是本组要防的
  「判别面只枚举了一部分」）。
- ``CE-4~CE-5``：``core_traits_summary`` 的取值键与取值类型。两条方向相反——
  CE-4 退回已退役的 ``personality``（现行键失效），CE-5 放松类型判据
  （list/dict 被 ``str()`` 噪声塞进 prompt）。
- ``CE-6~CE-8``：JSON 列的形状兜底与降级留痕。CE-6/7 撤形状判据（``[1,2,3]``
  又会以数组形态进 ``current_state``），CE-8 把告警判据放宽到**合法空态也告警**
  （反向失效：噪声淹没信号）。
- ``CE-10~CE-12``：触发扫描面读 ``key_beats`` / ``character_changes_planned`` 的
  **object 形态**（校验契约要求）。CE-10/11 撤掉 object 分支 ⇒ 只写在 key_beats 里的
  角色又被降级；CE-12 放松类型判据 ⇒ 非字符串实体的 repr 进字面匹配（噪声命中）。

⚠️ 本组**不含** ``_apply_injection_policy`` 策略本身的任何变异——改「未命中要不要降级」
是产品裁决（Lead 保留），不是本组的清欠账面。相关行为现状由 ``test_snapshot_*`` 一组
钉住，不在本组变异。
"""

MUTATIONS = [
    (
        # CE-1：character 侧不再带 inject_mode ⇒ relevance 的 always 分支恒假
        "CE-1",
        "packages/core/context_engine/builders_common/excerpts.py",
        '            "inject_mode": inject_mode,\n',
        "",
        "tests/unit/test_character_excerpt_fidelity.py",
    ),
    (
        # CE-2：location 侧不再带 inject_mode（CE-1 的兄弟处，缺这条则少修一处仍全绿）
        "CE-2",
        "packages/core/context_engine/builders_common/excerpts.py",
        '            # 与 character 侧同因：relevance._is_location_relevant 的 always 分支\n'
        '            # 同样读 ``loc.get("inject_mode")``，不带这个键 ⇒ 恒假（见模块 docstring）。\n'
        '            "inject_mode": _load_inject_mode(r["inject_mode"]),\n',
        "",
        "tests/unit/test_character_excerpt_fidelity.py",
    ),
    (
        # CE-3：faction 侧不再带 inject_mode（第三处）
        "CE-3",
        "packages/core/context_engine/builders_common/excerpts.py",
        '            "data_json": _parse_json(r["data_json"]) or {},\n'
        '            "inject_mode": _load_inject_mode(r["inject_mode"]),\n'
        '        }\n\n'
        '    loc_out: list[dict[str, Any]] = []',
        '            "data_json": _parse_json(r["data_json"]) or {},\n'
        '        }\n\n'
        '    loc_out: list[dict[str, Any]] = []',
        "tests/unit/test_character_excerpt_fidelity.py",
    ),
    (
        # CE-4：core_traits_summary 退回读已退役的 personality 键 ⇒ 恒为 ""
        "CE-4",
        "packages/core/context_engine/builders_common/excerpts.py",
        '_CORE_TRAIT_KEYS = ("distinctive_trait", "personality")',
        '_CORE_TRAIT_KEYS = ("personality",)',
        "tests/unit/test_character_excerpt_fidelity.py",
    ),
    (
        # CE-5：放松类型判据 ⇒ list/dict 值被当作摘要塞进 prompt
        # 锚点带上一行 ``val = core.get(key)`` 做上下文锚定——同一判据在
        # ``_summarize_entity`` 里有第二处同款声明，裸单行会命中 2 次（ABORT）。
        "CE-5",
        "packages/core/context_engine/builders_common/excerpts.py",
        "        val = core.get(key)\n"
        "        if isinstance(val, str) and val.strip():\n",
        "        val = core.get(key)\n"
        "        if val:\n",
        "tests/unit/test_character_excerpt_fidelity.py",
    ),
    (
        # CE-6：core_json 退回 _parse_json ⇒ 非法 JSON 又被静默吞成 {}
        "CE-6",
        "packages/core/context_engine/builders_common/excerpts.py",
        '        core = _decode_entity_json(r["core_json"], kind="character", entity_id=cid)\n',
        '        core = _parse_json(r["core_json"]) or {}\n',
        "tests/unit/test_character_excerpt_fidelity.py",
    ),
    (
        # CE-7：state_json 退回 _parse_json ⇒ [1,2,3] 以数组形态进 current_state
        "CE-7",
        "packages/core/context_engine/builders_common/excerpts.py",
        '        state = _decode_entity_json(\n'
        '            r["latest_state_json"], kind="character_state", entity_id=cid,\n'
        '        )\n',
        '        state = _parse_json(r["latest_state_json"]) or {}\n',
        "tests/unit/test_character_excerpt_fidelity.py",
    ),
    (
        # CE-8：告警判据放宽到合法空态也告警（反向失效：噪声淹没信号）
        "CE-8",
        "packages/core/context_engine/builders_common/excerpts.py",
        '    return raw.strip() not in ("", "{}", "[]", "null")',
        '    return True',
        "tests/unit/test_character_excerpt_fidelity.py",
    ),
    (
        # CE-9：告警整段摘掉 ⇒ 脏列降级重新变成零留痕（CE-6/7 的对偶）
        "CE-9",
        "packages/core/context_engine/builders_common/excerpts.py",
        "    if decoded == {} and _json_column_declares_content(raw):",
        "    if False:",
        "tests/unit/test_character_excerpt_fidelity.py",
    ),
    (
        # CE-10：key_beats 的 object 元素不再进扫描面 ⇒ 只写在 key_beats 里的角色
        # 又被降级为 summary（原实现只收字符串元素，而校验器要求元素是 object）
        "CE-10",
        "packages/core/context_engine/builders_common/excerpts.py",
        "                    elif isinstance(b, dict):\n"
        "                        parts.extend(_beat_scan_text(b))\n",
        "",
        "tests/unit/test_character_excerpt_fidelity.py",
    ),
    (
        # CE-11：character_changes_planned 的 object 元素不再进扫描面（CE-10 的兄弟处）
        "CE-11",
        "packages/core/context_engine/builders_common/excerpts.py",
        '                    elif isinstance(c, dict):\n'
        '                        for key in ("name", "character_id"):\n'
        '                            v = c.get(key)\n'
        '                            if isinstance(v, str) and v.strip():\n'
        '                                parts.append(v)\n',
        "",
        "tests/unit/test_character_excerpt_fidelity.py",
    ),
    (
        # CE-12：_beat_scan_text 把非字符串实体值也收进扫描面 ⇒ repr 噪声进字面匹配
        "CE-12",
        "packages/core/context_engine/builders_common/excerpts.py",
        '        elif isinstance(v, list):\n'
        '            out.extend(x.strip() for x in v\n'
        '                       if isinstance(x, str) and x.strip())\n',
        '        elif isinstance(v, list):\n'
        '            out.extend(str(x) for x in v if x is not None)\n',
        "tests/unit/test_character_excerpt_fidelity.py",
    ),
]
