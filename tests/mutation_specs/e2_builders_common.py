"""``builders_common`` 三模块 8 处降级收窄的突变规格（2026-10-16 批次十 · E2）。

**欠账形状**：裸 ``except sqlite3.OperationalError`` 把「迁移未跑 ⇒ 缺表/缺列」与
「运行时故障（``database is locked`` / ``disk I/O error`` / 损坏 / 只读 / 磁盘满）」
一起吞成同一个降级值。危害不是崩溃，而是**降级被当成正常**——作者以为体裁约束生效、
产出的正文却没带体裁，全程零告警。

**收窄判据**：每处的 docstring / 行内注释都只声称覆盖「缺表 / 缺列」，故收窄是
修「捕获范围与注释主张不符」，不是改契约。八处一致地改成：

    except sqlite3.OperationalError as exc:
        if not <is_missing_schema_error>(exc):
            raise
        <原降级>

**本组 8 条变异统一撤掉那两行守卫**（回到裸 except）。它们各自独立：撤任意一条只让
对应用例红。撤哪一条都不会编译失败（``except ... as exc`` 后 ``exc`` 未使用是合法
Python），因此全部落 ``CAUGHT`` 桶而非 ``INVALID``——这是与「变异写得太狠」那族的
刻意区分。

**锚点纪律**：``summaries.py`` 的两个 ``_volume_id_of_chapter*`` 收窄前后逐字同形
（连降级返回值都相同），故两条锚点各带一行**相邻独有的注释行**做上下文锚定——
否则 ``validate_specs`` 会报锚点命中 2 次并 ``[ABORT]``（批次六 G4M03 同款事故）。

**执行**：由 Lead 统一跑 ``run_spec.py``；本文件只做只读预检
（``validate_specs.py``）。
"""

MUTATIONS = [
    (
        # _project_max_state_version：state_version 是缓存键主维度，静默归 0 = 脏命中
        "E2-MS1",
        "packages/core/context_engine/builders_common/common.py",
        """        if not _is_missing_schema_error(exc):
            raise
        return 0
    if row is None:
        return 0
    return _as_int_or(row["max_sv"])""",
        """        return 0
    if row is None:
        return 0
    return _as_int_or(row["max_sv"])""",
        "tests/unit/test_db_error_scope_e2.py",
    ),
    (
        # _try_project_word_band_json：静默吞锁 ⇒ 作者字数带退回服务端默认
        "E2-MS2",
        "packages/core/context_engine/builders_common/common.py",
        """        if not _is_missing_schema_error(exc):
            raise
        return None
    if row is None:
        return None
    val = row["word_band_json"]""",
        """        return None
    if row is None:
        return None
    val = row["word_band_json"]""",
        "tests/unit/test_db_error_scope_e2.py",
    ),
    (
        # _try_project_writing_bible：圣经同时喂 payload 与缓存键指纹
        "E2-MS3",
        "packages/core/context_engine/builders_common/common.py",
        """        if not _is_missing_schema_error(exc):
            raise
        return None
    if row is None:
        return None
    val = row["writing_bible"]""",
        """        return None
    if row is None:
        return None
    val = row["writing_bible"]""",
        "tests/unit/test_db_error_scope_e2.py",
    ),
    (
        # _try_active_canon_id：canon_id 是缓存键维度
        "E2-MS4",
        "packages/core/context_engine/builders_common/common.py",
        """        if not _is_missing_schema_error(exc):
            raise
        return None
    return row["canon_id"] if row else None""",
        """        return None
    return row["canon_id"] if row else None""",
        "tests/unit/test_db_error_scope_e2.py",
    ),
    (
        # _try_project_genre_pack_ref：题材包维度退成 '__none__' ⇒ 换包后脏命中
        "E2-MS5",
        "packages/core/context_engine/builders_common/common.py",
        """        if not _is_missing_schema_error(exc):
            raise
        return None
    if row is None:
        return None
    ref = row["genre_pack_ref"]""",
        """        return None
    if row is None:
        return None
    ref = row["genre_pack_ref"]""",
        "tests/unit/test_db_error_scope_e2.py",
    ),
    (
        # _project_overdue_chapters：项目级 overdue 阈值静默退回 30
        "E2-MS6",
        "packages/core/context_engine/builders_common/ledger.py",
        """        if not is_missing_schema_error(exc):
            raise
        return _FORESHADOW_OVERDUE_CHAPTERS
    if row is None:""",
        """        return _FORESHADOW_OVERDUE_CHAPTERS
    if row is None:""",
        "tests/unit/test_db_error_scope_e2.py",
    ),
    (
        # _volume_id_of_chapter：锚点带相邻独有注释行（与 _no 版逐字同形，须区分）
        "E2-MS7",
        "packages/core/context_engine/builders_common/summaries.py",
        """        # locked` ⇒ 卷 id 读成「未挂卷」⇒ **同卷过滤静默失效**，上一世正文尾段倒灌进
        # 新卷首章（快穿冷开场被破），且零告警。
        if not is_missing_schema_error(exc):
            raise
        return None""",
        """        # locked` ⇒ 卷 id 读成「未挂卷」⇒ **同卷过滤静默失效**，上一世正文尾段倒灌进
        # 新卷首章（快穿冷开场被破），且零告警。
        return None""",
        "tests/unit/test_db_error_scope_e2.py",
    ),
    (
        # _volume_id_of_chapter_no：同上，靠相邻独有注释行区分
        "E2-MS8",
        "packages/core/context_engine/builders_common/summaries.py",
        """        # 降级；锁 / I/O 上抛（否则同卷过滤静默失效，跨位面正文回灌）。
        if not is_missing_schema_error(exc):
            raise
        return None""",
        """        # 降级；锁 / I/O 上抛（否则同卷过滤静默失效，跨位面正文回灌）。
        return None""",
        "tests/unit/test_db_error_scope_e2.py",
    ),
]
