"""director 装配缓存键的 5 个台账维度 + 写路径推 ``state_version``（2026-10-23 M-1 / D-7）。

缺陷形状（硬规则 2「凡进 payload 的装配参数必须入键」的同形状**第 6/7 次**）
------------------------------------------------------------------
第二轮审查实测抓到 **director 侧又缺 5 个维度**——伏笔台账（``hooks``）/ 叙事债务
（``narrative_debts``）/ 情节图（``plot_events`` + ``branches``）/ 项目级逾期阈值
（``projects.foreshadow_overdue_chapters``）/ **director 侧**章标题（``chapters.title``）。
它们各自进 director payload，而它们的写路径**只 commit()、不推** ``state_version``
⇒ 改数据后重跑同一章**命中旧装配** ⇒ planner 拿着改动前的数据排计划，**零告警**。

⇒ 修复是**两条同时满足**（AGENTS 硬规则 2 的根治条件）：
① **键维度齐全**；② **写路径推** ``story_states.state_version``。
只做 ① 是治标（键维度一旦漏了别的输入，照样脏命中）。

本组的 9 条突变各钉一个失效方向
--------------------------------
* **LEDGER-KEY-1 ~ 5**（撤维度）：逐个撤掉 director 键的 ``hooks_fp`` /
  ``debts_fp`` / ``plot_fp`` / ``foreshadow_fp`` / ``title_fp`` ⇒ 对应用例必红。
  ⚠️ 这 5 条用例**用裸 SQL 改数据**（绕过写路径的 version 推进）⇒ 撤维度后**没有任何
  机制能让键变** ⇒ 必红（若用例走 service，version 推进会兜住 ⇒ 撤维度仍绿 ⇒
  突变守不住这条修复）。
* **LEDGER-SV-1 ~ 4**（撤 version 推进）：把某条写路径的
  ``bump_project_state_version(...)`` 调用去掉 ⇒ 对应的
  ``test_*_write_path_advances_state_version`` 必红（这一组专门证明
  「写路径推 version」不是摆设）。

⚠️ **锚点唯一性**：既有规格 ``E1-1`` / ``E1-2`` / ``SKILL-KEY-1`` / ``SKILL-KEY-2`` /
``STYLE-KEY-1`` / ``TITLE-KEY-1`` / ``META-KEY-1`` / ``VOL-1`` 的锚点原文都在
``director_input.py`` 的**别的行**上 ⇒ 本组 5 条维度锚点一律用**两行上下文**（维度行 +
紧随其后的既有维度行），既唯一又不打断既有规格。

⚠️ **replacement 不含双引号**（Windows PowerShell 5.1 向原生程序传 argv 会剥内嵌引号
⇒ 含引号的 replacement 会被静默变形，见 ``q1_style_sample_key.py`` 的留痕）。

判别
----
``tests/unit/test_ctx_c_ledger_key.py``（11 例：5 个维度场景 + 反向不变量
「未改必命中」+ 4 条写路径推 version + 「无 story_state 行 ⇒ 不造行」）。
"""

MUTATIONS = [
    # ------------------------------------------------------------------
    # 撤维度：director 键退回（5 个台账维度逐个撤）
    # ------------------------------------------------------------------
    (
        "LEDGER-KEY-1",
        "packages/core/context_engine/director_input.py",
        "        hooks_fp,\n        debts_fp,\n",
        "        debts_fp,\n",
        "tests/unit/test_ctx_c_ledger_key.py",
    ),
    (
        "LEDGER-KEY-2",
        "packages/core/context_engine/director_input.py",
        "        debts_fp,\n        plot_fp,\n",
        "        plot_fp,\n",
        "tests/unit/test_ctx_c_ledger_key.py",
    ),
    (
        "LEDGER-KEY-3",
        "packages/core/context_engine/director_input.py",
        "        plot_fp,\n        foreshadow_fp,\n",
        "        foreshadow_fp,\n",
        "tests/unit/test_ctx_c_ledger_key.py",
    ),
    (
        "LEDGER-KEY-4",
        "packages/core/context_engine/director_input.py",
        "        foreshadow_fp,\n        title_fp,\n",
        "        title_fp,\n",
        "tests/unit/test_ctx_c_ledger_key.py",
    ),
    (
        "LEDGER-KEY-5",
        "packages/core/context_engine/director_input.py",
        "        title_fp,\n        active_canon_id,",
        "        active_canon_id,",
        "tests/unit/test_ctx_c_ledger_key.py",
    ),
    # ------------------------------------------------------------------
    # 撤 version 推进：写路径不再推 story_states.state_version
    # ------------------------------------------------------------------
    (
        # 伏笔写路径（create_hook）：去掉同事务内的 version 推进。
        "LEDGER-SV-1",
        "packages/domain/ledger/service.py",
        "                # 的伏笔台账，零告警）。\n"
        "                bump_project_state_version(conn, project_id)\n"
        "                conn.commit()\n",
        "                # 的伏笔台账，零告警）。\n"
        "                conn.commit()\n",
        "tests/unit/test_ctx_c_ledger_key.py",
    ),
    (
        # 情节事件写路径（create_event）：去掉同事务内的 version 推进。
        "LEDGER-SV-2",
        "packages/domain/plot/service.py",
        "            bump_project_state_version(conn, project_id)\n"
        "            conn.commit()\n",
        "            conn.commit()\n",
        "tests/unit/test_ctx_c_ledger_key.py",
    ),
    (
        # 项目 PATCH 写路径（逾期阈值等 payload 消费列）：去掉 version 推进。
        "LEDGER-SV-3",
        "packages/domain/project/service.py",
        "            if push_state_version:\n"
        "                bump_project_state_version(conn, project_id)\n",
        "            if push_state_version:\n"
        "                pass  # LEDGER-SV-MUT: 不推 state_version\n",
        "tests/unit/test_ctx_c_ledger_key.py",
    ),
    (
        # 章标题 PATCH 写路径：去掉 version 推进。
        # ⚠️ anchor 里 ``prow["project_id"]`` 的双引号必须**原样**出现在源码里
        # （mutation 执行器按字面文本替换，不做转义）⇒ 这里写成 Python 字符串拼接的
        # 单引号 + 双引号组合；replacement 一律不含双引号（PS 5.1 argv 剥引号留痕）。
        "LEDGER-SV-4",
        "packages/domain/chapter/service.py",
        "                if prow is not None:\n"
        "                    bump_project_state_version(conn, prow[\"project_id\"])\n",
        "                if prow is not None:\n"
        "                    pass  # LEDGER-SV-MUT: 不推 state_version\n",
        "tests/unit/test_ctx_c_ledger_key.py",
    ),
]
