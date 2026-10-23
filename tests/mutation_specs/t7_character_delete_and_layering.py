"""Task-7（2026-10-23 · D-12/M-10 + M-11 + 返工 A1/A2/A3/A6/A7）的突变规格。

六条突变，覆盖本任务的两处机制（域层引用完整性 + 分层看守）：

===========================  ==========================================
类别                         条目
===========================  ==========================================
撤掉删角色的引用检查         T7-M1（整体撤除）
只查一半端点（半修）         T7-M1b（``from`` 端点查了、``to`` 端点没查）
撤掉删 faction 的引用检查    T7-M3（world 域同款洞）
把分层白名单放宽成整目录     T7-M2（``packages/core`` 整层放行）
动态目标表漏掉 domain        T7-M4（``packages.domain`` 从目标表里消失）
分层方向表漏掉 domain→domain T7-M5（那条方向边被删）
===========================  ==========================================

⚠️ **T7-M1b 必须单独占一条**：只钉 T7-M1（整体撤除）的话，一个「只查了
``from_character_id``」的半修实现照样全绿——这正是本仓登记的「半修」形状
（见 ``tests/mutation_specs/c1_character_bible_reaches_writer.py`` 的同款告诫）。

⚠️ **T7-M1b 的锚点已迁到 world 域**：返工（A3）把「删实体前查 relationships 端点」
抽成了**两域共用**的守卫 ``packages/domain/world/service.py::assert_not_relationship_endpoint``
⇒ 半修点也在那里（改一处 ⇒ character 与 faction **两侧同时**漏 ⇒ 两条用例一起红）。

⚠️ **T7-M2 的目标文件是测试文件本身**（分层看守就住在
``tests/unit/test_no_core_to_workflows_dep.py``）：它是「元看守」的突变——
把受控例外的判定从「精确登记 (文件, 模块)」放宽成「整个 packages/core 放行」
⇒ ``test_core_to_domain_allowance_is_not_coarse`` 与
``test_new_core_to_domain_dependency_is_rejected`` 必红。

纯数据，不含任何逻辑——执行器是 ``tests/mutation_specs/run_spec.py``（共用，禁改）。
"""

MUTATIONS = [
    # ------------------------------------------------- T7-M1 撤掉删角色的引用检查
    # 撤掉 ⇒ 删角色不再抛错 ⇒ ``pytest.raises`` 判别点失效
    # ⇒ tests/unit/test_character_delete_reference_guard.py 的 ①②⑤ 必红。
    (
        "T7-M1",
        "packages/domain/character/service.py",
        """            # 引用检查：relationships 的**两个端点列都要查**（只查 from 是半修）
            assert_not_relationship_endpoint(
                conn, character_id, "character", ReferencedError
            )
""",
        "",
        "tests/unit/test_character_delete_reference_guard.py",
    ),
    # ------------------------------------- T7-M1b 半修：只查 from_character_id
    # 只查一半端点 ⇒ 角色作为 ``to_character_id`` 时**删得掉**
    # ⇒ 孤儿关系行照旧产生 ⇒ character 与 faction 的「to 端点」两条用例必红。
    (
        "T7-M1b",
        "packages/domain/world/service.py",
        """    in_use = conn.execute(
        "SELECT 1 FROM relationships "
        "WHERE from_character_id = ? OR to_character_id = ? LIMIT 1",
        (entity_id, entity_id),
    ).fetchone()
""",
        """    in_use = conn.execute(
        "SELECT 1 FROM relationships "
        "WHERE from_character_id = ? LIMIT 1",
        (entity_id,),
    ).fetchone()
""",
        "tests/unit/test_character_delete_reference_guard.py",
    ),
    # ------------------------------- T7-M3 撤掉删 faction 的引用检查（world 域）
    # 撤掉 ⇒ 删 faction 不再抛错 ⇒ 孤儿关系行照旧产生
    # ⇒ tests/unit/test_character_delete_reference_guard.py 的 ⑦⑧⑩ 必红。
    (
        "T7-M3",
        "packages/domain/world/service.py",
        """            if cfg.table == "factions":
                assert_not_relationship_endpoint(conn, eid, "faction")
""",
        "",
        "tests/unit/test_character_delete_reference_guard.py",
    ),
    # ------------------------------- T7-M2 把分层白名单放宽成「整个 core 放行」
    # 精确登记 (文件, 模块) ⇒ 改成「只要在 packages/core 下就放行」
    # ⇒ 新增 core→domain 依赖不再被拦 ⇒ 守卫测试必红。
    (
        "T7-M2",
        "tests/unit/test_no_core_to_workflows_dep.py",
        "    return (rel_path, module) in ALLOWED_CORE_TO_DOMAIN_IMPORTS",
        '    return rel_path.startswith("packages/core")  # 突变：整层放行',
        "tests/unit/test_no_core_to_workflows_dep.py",
    ),
    # ---------------------- T7-M4 动态 import 目标表漏掉 packages.domain
    # 目标表少一行 ⇒ core → domain 的**动态** import 重新变成无人看守
    # ⇒ test_dynamic_import_of_forbidden_target_is_detected 的 domain 各形态必红。
    (
        "T7-M4",
        "tests/unit/test_no_core_to_workflows_dep.py",
        '    (_TARGET_PKG_DOMAIN, "contains", _DOMAIN_TOKEN_RE),\n',
        "",
        "tests/unit/test_no_core_to_workflows_dep.py",
    ),
    # ---------------------- T7-M5 分层方向表漏掉 domain→domain 这条边
    # 方向边被删 ⇒ 首条 domain→域 依赖不受任何机器约束
    # ⇒ test_layering_directions_include_domain_to_domain 必红。
    (
        "T7-M5",
        "tests/unit/test_no_core_to_workflows_dep.py",
        """    (
        "packages/domain",
        "packages.domain",
        "domain 域之间原则上不得互相依赖（受控例外见 ALLOWED_DOMAIN_TO_DOMAIN_IMPORTS）",
    ),
""",
        "",
        "tests/unit/test_no_core_to_workflows_dep.py",
    ),
]
