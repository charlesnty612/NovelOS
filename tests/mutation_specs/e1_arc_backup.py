"""E1 组突变规格：「except sqlite3.OperationalError 降级范围」收窄（2026-10-16 批次十）。

批次九已清 ``_genre_pack_excerpt``（``L0-GP1~3``）；本组清其余 7 处。**形状相同**：
裸 ``except sqlite3.OperationalError`` 把「锁 / I/O / 损坏 / 只读 / 磁盘满」与
「缺表 / 缺列」混为一谈，而每个 except 的注释都只声称覆盖后者。

本组 7 条突变（每条撤掉一个收窄守卫 ⇒ 对应反向用例红）：

======  =======================================  ================================
编号     锚点（守卫）                             守护用例文件
======  =======================================  ================================
E1-ARC1 ``arc/service.py`` ``_project_overdue_chapters``  unit/test_db_error_scope_e1
E1-ARC2 ``arc/service.py`` ``_load_volumes``              同上
E1-ARC3 ~~``_summarize_reveal_policies``~~ **已删**（Lead 驳回该处收窄，见文末）
E1-ARC4 ``arc/service.py`` ``build_arc_view`` LEFT JOIN 回退 同上
E1-WS1  ``world_switch.py`` ``_fill_evidence``             同上
E1-DBM1 ``db_maintenance.py`` ``prune-logs``               同上
E1-BK1  ``backup/service.py`` ``_existing_tables`` 过滤    同上（**不是**撤守卫）
======  =======================================  ================================

**锚点纪律**（AGENTS.md 坑区「同一形态声明两处 ⇒ 锚点命中 2 次」）：
ARC2 与 ARC3 的收窄块逐字同形（都是 ``except … as exc: / if not …: raise / return out``），
故锚点**必须带上各自的注释段**才唯一。

**E1-BK1 为什么不撤守卫**：``packages/core/backup/service.py:399`` 的
``except sqlite3.OperationalError: pass`` 在 ``finally`` 里，本机实测
（3.12.13 / SQLite 3.53.1）：

* 收窄成「非结构缺失就 raise」会**顶掉在途异常**（finally 里 raise 替换
  ``except Exception: rollback(); raise`` 正在传播的那个）⇒ 拿不到真因；
* 而 ``PRAGMA foreign_keys = ON`` 在锁下 / 只读连接下**都不抛**、事务内还是
  静默 no-op ⇒ 收窄的**行为差为空**，写出来的突变只会判「存活」（= 无效变异）。

所以 backup 的真正防线在**数据路径**：``_existing_tables`` 用 ``sqlite_master``
过滤（不是靠捕获异常）。E1-BK1 撤掉那个过滤 ⇒ 缺表会被送进
``PRAGMA foreign_key_check(<缺表>)``。实测该 PRAGMA 对不存在的表抛
``OperationalError: no such table``（与真缺表消息逐字相同 ⇒ 若改成异常捕获，
锁 / I/O 故障将与缺表**同形**，正是本组要消灭的形状）。
"""

MUTATIONS = [
    (
        # 逾期阈值：锁被读成「该列取不到」⇒ 静默退回 30
        "E1-ARC1",
        "packages/core/arc/service.py",
        """        if not is_missing_schema_error(exc):
            raise
        return _DEFAULT_FORESHADOW_OVERDUE_CHAPTERS""",
        "        return _DEFAULT_FORESHADOW_OVERDUE_CHAPTERS",
        "tests/unit/test_db_error_scope_e1.py",
    ),
    (
        # 卷结构：锁被读成「本书没有卷」⇒ volumes 整节静默为空
        "E1-ARC2",
        "packages/core/arc/service.py",
        """    except sqlite3.OperationalError as exc:
        # 收窄（2026-10-16 批次十）：docstring 声称「0015 未跑（volumes 表不存在）」，
        # 故只放结构缺失；锁 / I/O / 损坏 / 只读 / 磁盘满一律上抛——否则
        # 「读不到卷」被读成「本书没有卷」，卷结构整节静默消失。
        if not is_missing_schema_error(exc):
            raise
        return out""",
        "    except sqlite3.OperationalError:\n        return out",
        "tests/unit/test_db_error_scope_e1.py",
    ),
    # ⚠️ E1-ARC3 已于 2026-10-16 批次十**删除**（不是失效后放着不管）。
    #
    # 原条目守的是 ``_summarize_reveal_policies`` 的收窄（撤掉守卫 → 锁被读成
    # 「本书一条剧透策略都没有」）。Lead 复核时**驳回**了这一处收窄：该函数的
    # docstring 原文是「SQL 异常（0014 未跑**等** OperationalError）→ 返回全零 +
    # 空 overdue」，那句「等」是**显式的宽口径契约**而非笔误，按
    # ``db.is_missing_schema_error`` 文档化的前提①（注释声称更宽 ⇒ 收窄=改契约），
    # 须先裁决。⇒ 收窄被回退，本条的锚点随之归零。
    #
    # 这正是本文件上方那条「锚点会因**别人新增**同名声明而重复命中」的**反向版本**：
    # 锚点也会因**机制被删**而命中 0 次。处理方式同样是**删掉 + 写明理由**，
    # 不是留着让 run_spec 每次 ABORT。
    #
    # 删它不丢覆盖：保留宽口径这件事本身改由
    # ``tests/unit/test_db_error_scope_e1.py::test_arc_reveal_policies_keeps_wide_contract_pending_ruling``
    # 钉住（钉的是「现在就这样」，不是「这样才对」），详见该用例 docstring。
    (
        # LEFT JOIN 回退：撤守卫后，瞬时锁被静默降级成「无卷信息」且回退查询还成功
        # （⇒ 响应完全正常、只是少了一节）。撤守卫同时删掉注释块，保持语句块完整。
        "E1-ARC4",
        "packages/core/arc/service.py",
        """        except sqlite3.OperationalError as exc:
            # 收窄（2026-10-16 批次十）：只放「volumes 表/列缺失」过。
            # 注释声称的范围就是「0015 未跑（volumes 表缺失）」，而裸 except 会把
            # database is locked 也吞进来、进而用**同样被锁**的裸 chapters 查询重试
            # ——失败点漂移到 fallback SQL，真因（锁）被藏进一条看似无害的重试里。
            if not is_missing_schema_error(exc):
                raise
            # volumes 表缺失 → 回退到无卷信息的纯 chapters 查询""",
        """        except sqlite3.OperationalError:
            # volumes 表缺失 → 回退到无卷信息的纯 chapters 查询""",
        "tests/unit/test_db_error_scope_e1.py",
    ),
    (
        # 本组后果最重：eras={} ⇒ _era_decision 无条件 archive ⇒ --apply 归档跨位面实体
        "E1-WS1",
        "scripts/world_switch.py",
        """    except sqlite3.OperationalError as exc:  # pragma: no cover - 极老库无 plot_events
        # 收窄（2026-10-16 批次十）：注释声称的范围就是「极老库无 plot_events」（缺表），
        # 故只放结构缺失。
        #
        # ⚠️ **此处是本文件后果最重的一处降级**，收窄的理由不只是「一致性」：
        # rows=[] ⇒ eras={} ⇒ :func:`_era_decision` 落到「完全没有证据」分支，
        # 对**每个** canon 实体返回 ``("archive", "closed_era")``。也就是说——
        # **一次读锁就把「跨位面实体不该归档」判反了，--apply 会把本该 skip 的实体
        # 全部置 inject_mode='archived'**。裸 except 让这条路径全程零告警。
        if not is_missing_schema_error(exc):
            raise
        rows = []""",
        """    except sqlite3.OperationalError:  # pragma: no cover - 极老库无 plot_events
        rows = []""",
        "tests/unit/test_db_error_scope_e1.py",
    ),
    (
        # 运维清理：锁被读成「没有过期日志」⇒ 打印 nothing to prune 且退出码 0
        "E1-DBM1",
        "scripts/db_maintenance.py",
        """                if not is_missing_schema_error(exc):
                    raise
                print(f"[skip] ai_call_logs unavailable ({exc}); nothing to prune")""",
        """                print(f"[skip] ai_call_logs unavailable ({exc}); nothing to prune")""",
        "tests/unit/test_db_error_scope_e1.py",
    ),
    (
        # backup 数据路径的真正防线：缺表靠 sqlite_master 过滤，不靠捕获异常。
        # 撤掉后缺表会被送进 PRAGMA foreign_key_check(<缺表>) ⇒ 实测抛
        # OperationalError: no such table（与真缺表消息逐字相同）。
        "E1-BK1",
        "packages/core/backup/service.py",
        "    return tuple(t for t in tables if t in known)",
        "    return tuple(tables)",
        "tests/unit/test_db_error_scope_e1.py",
    ),
]
