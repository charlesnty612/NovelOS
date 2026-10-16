"""计划期 + 审稿链「读路径降级范围」的突变规格（2026-10-16 批次十 · 组 E4）。

同族缺陷：裸 ``except sqlite3.OperationalError``（以及把 OperationalError 一并吞掉的
``except Exception``）把**运行时故障**当成「老库缺表」静默吞掉，而各处注释 / docstring
只声称覆盖「缺表 / 缺列」。危害不是崩溃，是**降级被当成正常**、且零告警：

- ``chapter_plan/planner_input.py::load_project_style_constraints``
  → 回落 ``SCENE_PLANNER_DEFAULT_STYLE``，**正文没带项目文风**而无人察觉；
- ``chapter_plan/scene_plan_store.py::load_scene_plan``
  → 读成「未命中」，scene_planner 节点改走 LLM 重规划（多烧调用 + 场景计划换一套）；
- ``chapter_review/pipeline.py::_basic_checks_node`` 读 ``projects.word_band_json``
  → 读成「项目没配字数带」，评审报告**照常跑完**却按默认 ±15% / ±30% 判字，
  reviewer UI 上呈现与项目设定不符的「在带内 + 零 warning」，作者据此放行。

五条各自独立，撤一条只让对应用例红（守的是**不同层**，见下）：

- ``E4-PI1`` 内层：planner 第一条查询的收窄被撤；
- ``E4-PI2`` 开连接：planner 的守卫整段退回 ``except Exception``（锁从**开连接**那半边进来
  —— ``get_connection`` 的 ``PRAGMA journal_mode = WAL`` 排在 ``busy_timeout`` 之前，
  非 WAL 库上持锁写事务会让开连接本身抛，压根到不了 SELECT）；
- ``E4-SP1`` scene_plan_store 查询的收窄被撤；
- ``E4-CR1`` chapter_review **内层**收窄被撤；
- ``E4-CR2`` chapter_review **外层**无差别再吞被加回——``sqlite3.OperationalError`` 是
  ``Exception`` 的子类，这一层不改，内层收窄就是**死代码**（批次九 planner_input /
  scene_planner 踩过同款）。``E4-CR1`` 与 ``E4-CR2`` 红的用例集不同：
  只有 ``E4-CR2`` 会额外打红 ``test_word_band_connect_failure_propagates``，
  那正是**唯一**能判别外层那条的用例。

行尾纪律（踩过一次）：本组三个目标文件行尾**不一致**——``pipeline.py`` 是 CRLF，
``planner_input.py`` / ``scene_plan_store.py`` 是 LF。``run_spec.py`` / ``validate_specs.py``
都用 ``read_text``（universal newlines ⇒ 读出统一 LF），故**锚点一律写 LF**；
若改用原始字节做匹配，CRLF 文件上锚点命中 0 次，会被误报成「规格写错」。
"""

MUTATIONS = [
    (
        "E4-PI1",
        "packages/workflows/chapter_plan/planner_input.py",
        """    except sqlite3.OperationalError as exc:
        # 先关连接再判定：上抛路径也要归还连接（降级路径的行为不变）。
        conn.close()
        if not is_missing_schema_error(exc):
            raise
        return dict(SCENE_PLANNER_DEFAULT_STYLE)""",
        """    except sqlite3.OperationalError:
        conn.close()
        return dict(SCENE_PLANNER_DEFAULT_STYLE)""",
        "tests/unit/test_db_error_scope_e4.py",
    ),
    (
        "E4-PI2",
        "packages/workflows/chapter_plan/planner_input.py",
        """    except sqlite3.OperationalError as exc:
        # 非结构故障不得降级成「默认文风」：那正是「静默降级被当成正常」的形状
        # （DB 整体不可用 ≠ 这个项目没配文风）。判别单点 = packages.core.db。
        if not is_missing_schema_error(exc):
            raise
        return dict(SCENE_PLANNER_DEFAULT_STYLE)
    except Exception:  # noqa: BLE001 —— 非 DB 失败（路径类型错等）仍按默认文风
        return dict(SCENE_PLANNER_DEFAULT_STYLE)""",
        """    except Exception:  # noqa: BLE001
        return dict(SCENE_PLANNER_DEFAULT_STYLE)""",
        "tests/unit/test_db_error_scope_e4.py",
    ),
    (
        "E4-SP1",
        "packages/workflows/chapter_plan/scene_plan_store.py",
        """    except sqlite3.OperationalError as exc:
        # 0027 未迁移的老库 → 视为未命中（与「有行但空」同路径）。
        # 批次十：仅「缺表 / 缺列」降级；其余运行时故障上抛（连接由 finally 归还）。
        if not is_missing_schema_error(exc):
            raise
        return None""",
        """    except sqlite3.OperationalError:
        return None""",
        "tests/unit/test_db_error_scope_e4.py",
    ),
    (
        "E4-CR1",
        "packages/workflows/chapter_review/pipeline.py",
        """                    if not is_missing_schema_error(exc):
                        raise
                    band_row = None""",
        """                    band_row = None""",
        "tests/unit/test_db_error_scope_e4.py",
    ),
    (
        "E4-CR2",
        "packages/workflows/chapter_review/pipeline.py",
        """            if isinstance(exc, sqlite3.OperationalError):
                raise
            band_row = None""",
        """            band_row = None""",
        "tests/unit/test_db_error_scope_e4.py",
    ),
]
