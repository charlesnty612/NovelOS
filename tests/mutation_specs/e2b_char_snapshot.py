"""H2（手动新增角色进不了 story_state 快照）判别链的突变规格（2026-10-16 批次十一 · E2）。

**执行**：由 Lead 统一跑 ``tests/mutation_specs/run_spec.py``；本文件只做只读预检
（``validate_specs.py``），且**预检必须后置**——它读的正是执行器临时改写的源文件
（坑区「预检与执行器并发 ⇒ validate_specs 报出假『锚点未命中』」，2026-10-16 批次八）。

**本组不修任何东西**——E2 的结论是「快照滞后一版」是真缺陷、但它**不是**外部报告
里「闺蜜变业务员」的因果链（writer 的角色数据直查 characters 表，不经快照），
故不动生产代码。这组规格守的是**判别链本身**：证明 E2 的 6 条绿测 + 1 条 xfail
不是空断言，机制一动就得红。

七条各守一个机制（撤任意一条只让对应用例红，互不串味）

--------------------------------------------------------------------------
E2C1 ``commit_delta`` 的 DB 权威重建（``commits.py:489``）
     撤掉 ⇒ ``test_next_commit_picks_up_manual_character`` 红。
     守的是「快照滞后**一版**而不是永久缺失」——这条界定的正是 E2 的结论边界。
E2C2 ``snapshot._load_characters`` 不读 characters 表（恒返回 ``[]``）
     ⇒ test 2 / test 4 / test_snapshot_relationships 三条同红（本就是同一机制的三处断言）。
E2C3 快照角色条目**注入** ``core_json``
     ⇒ ``test_snapshot_character_entry_drops_definition_side_columns`` 红。
     这条是**反向**突变：钉住「core_json 被 SELECT 后丢弃」这个事实——
     ``snapshot.py:334`` 写着「暂未注入……保留 raw 字段供未来」，哪天有人「顺手补上」
     就会静默改变 observer 的 ``previous_state`` 体积与角色圣经内容口径。
E2C4 ``check_state_sync`` 的 characters 巡检取错 id 列（``character_id`` → ``name``）
     ⇒ ``test_check_state_sync_reports_manual_character_as_only_in_db`` 红。
     守的是「巡检网在这个方向**没有**洞」——若有人改口径，结论必须跟着变。
E2C5 触发注入策略：auto 未命中**不降级**（返回 full）
     ⇒ ``test_writer_degrades_unmentioned_character_to_name_only`` 红。
     这条是外部报告的**真凶机制**：未提及的角色只剩名字 ⇒ 模型自造职业。
E2C6 ``_load_relationships_for`` 恒查不到行
     ⇒ ``test_snapshot_relationships_come_from_relationship_table`` 红。
E2C7 读路径不信任快照（``queries.get_current_state`` 无条件从 DB 重建）
     ⇒ ``test_manual_character_is_absent_from_current_snapshot`` 由 **xfail 变 XPASS**，
     ``strict=True`` 下整个文件红。这条**模拟的正是 H2 的修法**：修好之后该 xfail 必须
     翻面并强制移除标记，否则测试就在替缺陷背书。⚠️ 它同时会把 test 2 的
     ``state_version == 2`` 断言打红（``if True:`` 路径恒填 0），属预期——
     CAUGHT 判定看的是「文件红不红」，不要求只有一条用例红。

三条自查（写完必做，勿跳过）
--------------------------
1. **锚点恰好命中 1 次**——7 条全部逐条 ``str.count`` 核过（均为 1）；
   ``commits.py`` / ``snapshot.py`` / ``queries.py`` / ``check_state_sync.py`` /
   ``excerpts.py`` 均为 **LF**（实测无 CR），故锚点里的换行可直接写真换行
   （若目标文件是 CRLF，则必须写 ``\\n`` 两个字符——批次六 G4M03 / l2_state_list 同款）。
2. **每条都真的改行为**——``run_spec.py`` 比对写入前后 SHA256，没变即 ``[ABORT]``。
   本组 7 条全部是**真换语义**：无一条是「插了行注释、逻辑未变」那种自伤写法
   （坑区「脚本报存活时先怀疑量具，再怀疑变异」第 2 次）。
3. **交接前清点突变残留**——``find . -name "*.mutbak"`` + ``grep -rn "MUTATION-PROBE-E2C"``
   （区分大小写；``MUTATION`` 本身是**正常测试函数名**的一部分，用裸词搜会误报）。
"""

MUTATIONS = [
    (
        # commit 事务内不再以 DB 为权威重建 7 集合 ⇒ 手工角色永远补不回来
        "E2C1",
        "packages/core/story_state/commits.py",
        """        if is_main_branch_for_write:
            new_state = rebuild_snapshot_collections_from_db(
                conn,
                project_id,
                new_state,
                list(ALL_REBUILD_COLLECTIONS),
            )""",
        """        if is_main_branch_for_write:
            pass  # MUTATION-PROBE-E2C1 撤掉 DB 权威重建""",
        "tests/unit/test_manual_character_snapshot_gap.py",
    ),
    (
        # 快照不再从 characters 表取角色 ⇒ 快照角色段恒空
        "E2C2",
        "packages/core/story_state/snapshot.py",
        """    return out


def _load_relationships_for(""",
        """    return []  # MUTATION-PROBE-E2C2 快照不再读 characters 表


def _load_relationships_for(""",
        "tests/unit/test_manual_character_snapshot_gap.py",
    ),
    (
        # 反向突变：把 core_json 补进快照角色条目（「顺手补上」那个未来动作）
        "E2C3",
        "packages/core/story_state/snapshot.py",
        """        out.append(
            {
                "character_id": cid,
                "name": r["name"],
                "current_state": state_json,""",
        """        out.append(
            {
                "character_id": cid,
                "name": r["name"],
                "core_json": _parse_json_column(r["core_json"]),  # MUTATION-PROBE-E2C3
                "current_state": state_json,""",
        "tests/unit/test_manual_character_snapshot_gap.py",
    ),
    (
        # 巡检取错 id 列 ⇒ 报出来的漂移是「名字」而非 id，巡检网结论失真
        "E2C4",
        "scripts/check_state_sync.py",
        """        "db_table": "characters",
        "db_id_column": "character_id",""",
        """        "db_table": "characters",
        "db_id_column": "name",  # MUTATION-PROBE-E2C4""",
        "tests/unit/test_manual_character_snapshot_gap.py",
    ),
    (
        # 触发策略：auto 未命中也全量注入 ⇒ 未提及角色不再被砍成一行摘要
        "E2C5",
        "packages/core/context_engine/builders_common/excerpts.py",
        """    # 未命中 → 降级为一行摘要
    if summary_overrides is None:""",
        """    # 未命中 → 降级为一行摘要
    if True:  # MUTATION-PROBE-E2C5 未命中也全量注入
        return "full", {**full_excerpt, "_injection": "full"}
    if summary_overrides is None:""",
        "tests/unit/test_manual_character_snapshot_gap.py",
    ),
    (
        # 关系聚合恒查不到行 ⇒ 快照里 relationships 恒空
        "E2C6",
        "packages/core/story_state/snapshot.py",
        """        WHERE from_character_id = ?
        ORDER BY relationship_id ASC
        \"\"\",
        (endpoint_id,),""",
        """        WHERE from_character_id = ?
        ORDER BY relationship_id ASC
        \"\"\",
        ("__mutation_no_such_character__",),  # MUTATION-PROBE-E2C6""",
        "tests/unit/test_manual_character_snapshot_gap.py",
    ),
    (
        # 模拟 H2 的修法：读路径不信任快照，每次从 DB 重建
        # ⇒ xfail(strict) 的复现用例 XPASS，文件必须红（提醒移除标记）
        "E2C7",
        "packages/core/story_state/queries.py",
        """        if snap is None:
            conn = get_connection(service_self.db_path)
            try:
                initial = build_initial_state(conn, project_id)
            finally:
                conn.close()
            initial["state_version"] = 0
            return initial""",
        """        if True:  # MUTATION-PROBE-E2C7 读路径一律从 DB 重建
            conn = get_connection(service_self.db_path)
            try:
                initial = build_initial_state(conn, project_id)
            finally:
                conn.close()
            initial["state_version"] = 0
            return initial""",
        "tests/unit/test_manual_character_snapshot_gap.py",
    ),
]
