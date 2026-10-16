"""组 E3 降级范围收窄的突变规格（2026-10-16 批次十）。

同族缺陷：**裸 ``except sqlite3.OperationalError``（或裸 ``except Exception``）把运行时
故障当成「迁移未跑的极老库」静默吞掉**，而各处注释只声称覆盖「缺表 / 缺列」。
批次九已清 ``builders_common/common.py::_genre_pack_excerpt`` 一处并抽出共享单点
``packages.core.db.is_missing_schema_error``；本组清剩下的 6 处。

三条失效方向（各自独立，撤一条只让对应用例红）

1. **收窄被撤**（E3-1~E3-5）：把 ``if not is_missing_schema_error(exc): raise`` 整段去掉，
   锁 / I/O / 损坏又被静默吞回「降级」。
2. **守卫整体摘掉**（E3-6）：把 ``except sqlite3.OperationalError as exc`` 换成裸
   ``except Exception``——异常类型判别面从「DB 故障」缩到「一切」，连**不是 DB 的**
   异常也开始上抛，是「守卫比声称的更宽」的反向失效（本组刻意保留非 DB 降级分支）。
3. **非 DB 降级分支被删**（E3-7）：连带砍掉保留下来的 ``except Exception: return 0``，
   连非 DB 异常也开始炸——与 2 方向相反，同样是「判别面与注释不符」。

⚠️ 2 / 3 这两条在 ``writer_input.py`` 的两处 ``get_connection`` 守卫上**必须成对存在**：
只留一条会让「撤掉另一条」完全不可观测（同坑区「判别面只枚举了一部分」）。

⚠️ 本组**不含** ``is_missing_schema_error`` 自身的变异：它是共享单点，由
``tests/unit/test_genre_pack_schema_error_scope.py`` 与本组用例共同看守；在这里再放一条
会与 L0-GP 组语义重叠。

判别用例一律落在 ``tests/unit/test_db_error_scope_e3.py``（本组唯一产出）。
"""

MUTATIONS = [
    (
        # writer 字数带覆盖读：撤掉收窄 ⇒ 锁 / I/O 又被读成「项目没设字数带」
        "E3-1",
        "packages/core/context_engine/writer_input.py",
        """            except sqlite3.OperationalError as exc:
                # ①②之外一律上抛（降级范围与上方注释逐条对齐）。
                if not is_missing_schema_error(exc):
                    raise
                prow = None""",
        """            except sqlite3.OperationalError:
                prow = None""",
        "tests/unit/test_db_error_scope_e3.py",
    ),
    (
        # reveal_policies 查询：撤掉收窄 ⇒ 「读不到 policy」被读成「没有 policy」
        "E3-2",
        "packages/core/context_engine/writer_input.py",
        """        #   判别：tests/unit/test_db_error_scope_e3.py
        if not is_missing_schema_error(exc):
            raise
        return 0
    finally:
        conn.close()""",
        """        return 0
    finally:
        conn.close()""",
        "tests/unit/test_db_error_scope_e3.py",
    ),
    (
        # HIDDEN 实体 visibility 查询：撤掉收窄 ⇒ 静默 return 0 = 一条都没过滤
        "E3-3",
        "packages/core/context_engine/writer_input.py",
        """        #   判别：tests/unit/test_db_error_scope_e3.py
        if not is_missing_schema_error(exc):
            raise
        return 0
    finally:
        conn2.close()""",
        """        return 0
    finally:
        conn2.close()""",
        "tests/unit/test_db_error_scope_e3.py",
    ),
    (
        # 第一处建连守卫：撤掉收窄 ⇒ 锁 / I/O 时「HIDDEN 过滤整段失效」
        "E3-4",
        "packages/core/context_engine/writer_input.py",
        """        # 而是「本该拦住的 HIDDEN 实体被原样放进 writer 上下文」），比别处的降级更糟。
        if not is_missing_schema_error(exc):
            raise
        return 0""",
        """        return 0""",
        "tests/unit/test_db_error_scope_e3.py",
    ),
    (
        # 第二处建连守卫：撤掉收窄（同 E3-4，另一条链；两处必须分别有判别）
        "E3-5",
        "packages/core/context_engine/writer_input.py",
        """        # （降级方向是 HIDDEN 实体泄露，见本函数 docstring）。
        if not is_missing_schema_error(exc):
            raise
        return 0""",
        """        return 0""",
        "tests/unit/test_db_error_scope_e3.py",
    ),
    (
        # 守卫整体摘掉（异常类型判别面缩到「一切」）：非 DB 异常也开始上抛
        "E3-6",
        "packages/core/context_engine/writer_input.py",
        """    except sqlite3.OperationalError as exc:
        # ⚠️ 收窄（2026-10-16 批次十）：建连失败只在「结构缺失」时降级为「不阻断装配」。""",
        """    except Exception as exc:  # MUTATION-E3-6
        # ⚠️ 收窄（2026-10-16 批次十）：建连失败只在「结构缺失」时降级为「不阻断装配」。""",
        "tests/unit/test_db_error_scope_e3.py",
    ),
    (
        # 非 DB 降级分支被删（与 E3-6 方向相反）：非 DB 异常也开始炸
        # （删的是**第一处**建连守卫的 ``except Exception`` 臂）
        "E3-7",
        "packages/core/context_engine/writer_input.py",
        """        if not is_missing_schema_error(exc):
            raise
        return 0
    except Exception:  # noqa: BLE001 —— 非 DB 异常维持既有降级（非本条债的处置面）
        return 0
    try:
        hidden_chars: set[str] = set()""",
        """        if not is_missing_schema_error(exc):
            raise
        return 0
    try:
        hidden_chars: set[str] = set()""",
        "tests/unit/test_db_error_scope_e3.py",
    ),
    (
        # 题材核销的绑定读取：撤掉收窄 ⇒ 锁 / I/O 被读成「项目没绑定题材包」
        # （校验器失效方向：本章题材核销整段跳过、零 issue、零告警）
        "E3-8",
        "packages/core/genre/verifier.py",
        """            if not is_missing_schema_error(exc):
                raise
            return None""",
        """            return None""",
        "tests/unit/test_db_error_scope_e3.py",
    ),
    (
        # 召回的同卷过滤：撤掉收窄 ⇒ 换卷后新卷第一章召回上一世正文
        "E3-9",
        "packages/core/retrieval/service.py",
        """        if not is_missing_schema_error(exc):
            raise
        return None""",
        """        return None""",
        "tests/unit/test_db_error_scope_e3.py",
    ),
]
