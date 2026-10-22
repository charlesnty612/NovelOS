"""L17（证据表**语义**字段看守）的突变规格（2026-10-23 批次二十六 · task-16）。

数据文件，执行器是共用的 ``tests/mutation_specs/run_spec.py``（**不许改**）::

    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py \
        tests/mutation_specs/l17_evidence_semantics.py

背景
----
审计 B 实测：``scripts/detector_evidence_check.py`` 此前**只比代码派生值**
⇒ 证据表的**语义面**改错了也不红（28 项扰动里 **26 项**漏过）。
⇒ task-16 给看守补了 6 类语义判据 + 探针成对 + description 数字登记。
⇒ 本规格逐条**撤掉**这些判据，验证新用例**必须红**——否则那些用例是空壳。

分组（按「撤掉后哪条新用例应当红」排）：

- **A 组 · 撤语义字段判据**（M01~M05）：corpus 键集 / measured 派生 /
  separation 舍入包络 / basis_ref 文件存在 / severity_escalation 比对；
- **B 组 · 撤探针成对**（M06）：探针删空不再红；
- **C 组 · 撤 description 登记复核**（M07）：登记指向错值不再红；
- **D 组 · 撤命名常量**（M08）：``DEFAULT_DASH_ERROR_MULTIPLIER`` 的**值** 2 → 3
  ⇒ ① 证据表 `severity_escalation.value` 与 `code_refs` 的值比对必红
  ② 升级门的 2× 边界用例（1.9× ⇒ warning / 2.1× ⇒ error）必红
  ⇒ **两处都抓**才算这条突变被覆盖。

  ⚠️ **2026-10-23 task-19 订正**：本条**原先**是「把 ``* DEFAULT_DASH_ERROR_MULTIPLIER``
  还原成 ``* 2``」——而常量正是 2 ⇒ **行为逐位不变** ⇒ 是**等价突变**
  ⇒ Lead 串行实跑抓到 ``[SURVIVED]``。⇒ 已改成**改常量的值**（真行为突变）。
  （AGENTS 坑区：``SURVIVED`` 时先判「等价」，再判「缺守门测试」——本条是前者。）

⚠️ **每条突变都必须让 ``tests/unit/quality/test_detector_evidence.py`` 红**，
且红的是**预期那条**（不是「别的用例碰巧也红了」）。
"""

from __future__ import annotations

TEST_FILE = "tests/unit/quality/test_detector_evidence.py"
GUARD = "scripts/detector_evidence_check.py"

MUTATIONS = [
    # ---------------------------------------------------------------- A 组：撤语义字段判据
    (
        "L17-M01",  # ① corpus ∈ corpora 键集 —— 撤掉
        GUARD,
        "            if not isinstance(corpus_id, str) or corpus_id not in corpora:",
        "            if not isinstance(corpus_id, str) or False:  # ⚠️ MUTATION-L17-M01",
        TEST_FILE,
    ),
    (
        "L17-M02",  # ③ measured ⇒ corpus 非 null —— 撤掉
        GUARD,
        '                if side.get("corpus") is None:\n'
        '                    result.errors.append(\n'
        '                        f"{rid} 标 evidence_status=measured 但 {side_name}.corpus 为 null"',
        "                if False:  # ⚠️ MUTATION-L17-M02\n"
        "                    result.errors.append(\n"
        '                        f"{rid} 标 evidence_status=measured 但 {side_name}.corpus 为 null"',
        TEST_FILE,
    ),
    (
        "L17-M03",  # ④ separation 舍入包络 —— 撤掉
        GUARD,
        "                elif not (envelope[0] - 1e-9 <= float(separation) <= envelope[1] + 1e-9):",
        "                elif False:  # ⚠️ MUTATION-L17-M03",
        TEST_FILE,
    ),
    (
        "L17-M04",  # ⑤ basis_ref 指向的文件必须存在 —— 撤掉
        GUARD,
        "            if not target.exists():",
        "            if False:  # ⚠️ MUTATION-L17-M04",
        TEST_FILE,
    ),
    (
        "L17-M05",  # ⑥ severity_escalation 与代码派生值比对 —— 整块跳过
        GUARD,
        "    # ⑥ severity_escalation 与代码派生的升级路径逐条相符 -------------------",
        "    return  # ⚠️ MUTATION-L17-M05：跳过 severity_escalation 比对",
        TEST_FILE,
    ),
    # ---------------------------------------------------------------- B 组：撤探针成对
    (
        "L17-M06",  # Q-2 每条规则探针成对 —— 撤掉
        GUARD,
        "        if trues < 1 or falses < 1:",
        "        if False:  # ⚠️ MUTATION-L17-M06",
        TEST_FILE,
    ),
    # ---------------------------------------------------------------- C 组：撤 description 登记复核
    (
        "L17-M07",  # Q-4 逐条登记复核 —— 撤掉
        GUARD,
        "            registered[key] = reg\n"
        "            _verify_description_registration(result, rid, reg, entry, resolver)",
        "            registered[key] = reg\n"
        "            pass  # ⚠️ MUTATION-L17-M07：跳过登记复核",
        TEST_FILE,
    ),
    # ---------------------------------------------------------------- D 组：撤命名常量
    (
        "L17-M08",  # Q-3 升级乘数**改值** 2 → 3 ⇒ 证据表 + 边界用例都必须红
        "packages/core/quality/ai_patterns_sentence.py",
        "# ⇒ 提为命名常量（可被证据表 `code_refs` 钉住、可被突变规格覆盖）。\n"
        "DEFAULT_DASH_ERROR_MULTIPLIER = 2",
        "# ⇒ 提为命名常量（可被证据表 `code_refs` 钉住、可被突变规格覆盖）。\n"
        "DEFAULT_DASH_ERROR_MULTIPLIER = 3  # ⚠️ MUTATION-L17-M08",
        TEST_FILE,
    ),
]
