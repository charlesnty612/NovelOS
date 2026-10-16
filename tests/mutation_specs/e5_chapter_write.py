"""chapter_write **写路径**降级范围的突变规格（2026-10-16 批次十 · E5 组）。

同批次九 ``l0_degradation_scope.py`` 的 B 面（降级范围过宽），但落在
``packages/workflows/chapter_write/``——**写路径为主**，因此价值最高：
``save_draft`` 包在 try 里的是真正的 ``UPDATE``，而 WAL 单写者下并发写 /
嵌套另开连接写库会在 ``busy_timeout`` 5s 后抛 ``database is locked``
（AGENTS 坑区 M1-a 实证，daemon 线程还会把 traceback 吞掉）。

收窄形态（五处，全部走共享单点 ``packages.core.db.is_missing_schema_error``）

=========  ==========================================  ==========================
ID         文件 / 站                                    撤掉收窄后的静默后果
=========  ==========================================  ==========================
E5-W1      ``writer._latest_draft_text``              revise 静默退化为 write
                                                       整章重写 + 保真闸门解除
E5-SD1     ``save_draft._save_draft_node``（**写**）   偏差注记没落库被读成「无偏差」
E5-SP1     ``scene_planner`` reference_canon 读        情绪曲线 / 爽点排布从模型视野消失
E5-SP2     ``scene_planner`` world_rules 读            项目级硬设定从模型视野消失
E5-P1      ``pipeline._writer_node`` ai_call_logs 读   真实模型调用被记成 ``mock/mock``
=========  ==========================================  ==========================

**两个方向各一条**（各站 ×2 = 10 条）：

* ``-REV``：撤掉守卫 ⇒ 回到裸 ``except sqlite3.OperationalError``（欠收窄）⇒
  「锁 / I/O 必须上抛」那一组用例必红；
* ``-OVER``：守卫恒成立 ⇒ 连「缺表 / 缺列」也上抛（过收窄）⇒
  「缺表仍降级」那一组用例必红。

⚠️ ``-OVER`` 写成 ``if True:`` 会被 ruff 抱怨，但 ``run_spec.py`` 只做
「改源码 → 跑定向用例 → 还原」，不跑 lint；它的作用是**只改判定不改结构**，
若改成删整个 ``except`` 块则会把「降级路径还在不在」一起改掉，两条变异互相
掩盖。脚本报存活时先怀疑变异本身（AGENTS 坑区「变异未改行为」一条）。

⚠️ 本文件涉及的 ``scene_planner.py`` / ``save_draft.py`` **带 UTF-8 BOM**：
``run_spec.py`` 的 ``read_text/write_text(encoding="utf-8")`` 不剥离也不吞掉
BOM（``utf-8`` 编解码器把它当 U+FEFF 正字符），还原走 ``shutil.copy2``
逐字节回写 ⇒ BOM 不会在突变过程中丢失。本组锚点均在文件中部，不受 BOM 影响。
"""

MUTATIONS = [
    # ---------------- writer._latest_draft_text ----------------
    (
        "E5-W1-REV",
        "packages/workflows/chapter_write/writer.py",
        """        if not is_missing_schema_error(exc):
            raise
        return \"\"""",
        """        return \"\"""",
        "tests/unit/test_db_error_scope_e5.py",
    ),
    (
        "E5-W1-OVER",
        "packages/workflows/chapter_write/writer.py",
        """        if not is_missing_schema_error(exc):
            raise
        return \"\"""",
        """        if True:
            raise
        return \"\"""",
        "tests/unit/test_db_error_scope_e5.py",
    ),
    # ---------------- save_draft._save_draft_node（写路径） ----------------
    (
        "E5-SD1-REV",
        "packages/workflows/chapter_write/save_draft.py",
        """                if not is_missing_schema_error(exc):
                    raise
        # 先看是否有 draft 行（按 chapter + version 累计）""",
        """                pass
        # 先看是否有 draft 行（按 chapter + version 累计）""",
        "tests/unit/test_db_error_scope_e5.py",
    ),
    (
        "E5-SD1-OVER",
        "packages/workflows/chapter_write/save_draft.py",
        """                if not is_missing_schema_error(exc):
                    raise
        # 先看是否有 draft 行（按 chapter + version 累计）""",
        """                if True:
                    raise
        # 先看是否有 draft 行（按 chapter + version 累计）""",
        "tests/unit/test_db_error_scope_e5.py",
    ),
    # ---------------- scene_planner · reference_canon ----------------
    (
        "E5-SP1-REV",
        "packages/workflows/chapter_write/scene_planner.py",
        """            if not is_missing_schema_error(exc):
                raise
            reference_canon_inject = None""",
        """            reference_canon_inject = None""",
        "tests/unit/test_db_error_scope_e5.py",
    ),
    (
        "E5-SP1-OVER",
        "packages/workflows/chapter_write/scene_planner.py",
        """            if not is_missing_schema_error(exc):
                raise
            reference_canon_inject = None""",
        """            if True:
                raise
            reference_canon_inject = None""",
        "tests/unit/test_db_error_scope_e5.py",
    ),
    # ---------------- scene_planner · world_rules ----------------
    (
        "E5-SP2-REV",
        "packages/workflows/chapter_write/scene_planner.py",
        """            if not is_missing_schema_error(exc):
                raise
        finally:
            conn2.close()""",
        """            pass
        finally:
            conn2.close()""",
        "tests/unit/test_db_error_scope_e5.py",
    ),
    (
        "E5-SP2-OVER",
        "packages/workflows/chapter_write/scene_planner.py",
        """            if not is_missing_schema_error(exc):
                raise
        finally:
            conn2.close()""",
        """            if True:
                raise
        finally:
            conn2.close()""",
        "tests/unit/test_db_error_scope_e5.py",
    ),
    # ---------------- pipeline._writer_node · ai_call_logs ----------------
    (
        "E5-P1-REV",
        "packages/workflows/chapter_write/pipeline.py",
        """        if not is_missing_schema_error(exc):
            raise
        row = None""",
        """        row = None""",
        "tests/unit/test_db_error_scope_e5.py",
    ),
    (
        "E5-P1-OVER",
        "packages/workflows/chapter_write/pipeline.py",
        """        if not is_missing_schema_error(exc):
            raise
        row = None""",
        """        if True:
            raise
        row = None""",
        "tests/unit/test_db_error_scope_e5.py",
    ),
]
