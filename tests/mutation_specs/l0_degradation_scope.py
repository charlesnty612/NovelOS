"""读路径**降级范围**的突变规格（2026-10-16 批次九 · 主会话自办）。

两条同族缺陷，都在「读路径遇到异常时该不该降级」这一面上：

A. **脏 JSON 不降级**（与 ``l0_plot_safe_json`` 同形，落在另一个面上）：三处读路径走裸
   ``json.loads``，一行脏 JSON 让**整个列表端点 500**（干净行陪葬）。修法：统一走
   ``write_helpers.decode_json_field``（解析失败 / 形状不符 ⇒ 退回空值）。
B. **降级范围过宽**（结清 2026-10-15 登记的挂账）：``_genre_pack_excerpt`` 裸
   ``except sqlite3.OperationalError`` 把锁 / I/O 故障也当「未绑定」静默吞掉。修法：
   收窄到「缺表 / 缺列」，并删掉调用方的无差别外层 wrapper。

四类失效方向（各自独立，撤一条只让对应用例红）：

1. chapter ``plan_json`` 退回裸 ``json.loads``（语法就坏的文本）；
2. character ``core_json`` 退回裸 ``json.loads``；
3. character ``state_json``（states 列表 + 列表行 latest）退回裸 ``json.loads``；
4. world ``data_json`` 退回裸 ``json.loads``；
5. 兜底助手 ``decode_json_field`` 的**形状检查**被删（``return parsed if isinstance(...)
   else fallback`` → ``return parsed``）⇒ 合法 JSON 但形状不对的行仍会 500。这条守的是
   「形状」维度，前四条只守「语法」维度——少它则「数组型 plan_json」这类行无看守；
6. ``_genre_pack_excerpt`` 收窄被撤（回到裸 except）；
7/8. planner_input / scene_planner 的外层 wrapper 被加回。
"""

MUTATIONS = [
    (
        "L0-ENTJSON1",
        "packages/domain/chapter/service.py",
        'd["plan_json"] = _decode_json_field(d.get("plan_json"), fallback={})',
        'd["plan_json"] = json.loads(d["plan_json"]) if d["plan_json"] else {}',
        "tests/api/test_dirty_json_list_degradation.py",
    ),
    (
        "L0-ENTJSON2",
        "packages/domain/character/service.py",
        'd["core_json"] = _decode_json_field(d.get("core_json"), fallback={})',
        'd["core_json"] = json.loads(d["core_json"]) if d["core_json"] else {}',
        "tests/api/test_dirty_json_list_degradation.py",
    ),
    (
        "L0-ENTJSON3",
        "packages/domain/character/service.py",
        'd["state_json"] = _decode_json_field(d.get("state_json"), fallback={})',
        'd["state_json"] = json.loads(d["state_json"]) if d["state_json"] else {}',
        "tests/api/test_dirty_json_list_degradation.py",
    ),
    (
        "L0-ENTJSON4",
        "packages/domain/world/service.py",
        'data = _decode_json_field(row["data_json"], fallback={}, allowed=(dict, list))',
        'data = json.loads(row["data_json"]) if row["data_json"] else {}',
        "tests/api/test_dirty_json_list_degradation.py",
    ),
    (
        # 形状检查被删 ⇒ 「合法 JSON 但形状不符」的行仍 500（语法维度前四条已覆盖）
        "L0-ENTJSON5",
        "packages/core/story_state/write_helpers.py",
        "    return parsed if isinstance(parsed, allowed) else fallback",
        "    return parsed",
        "tests/api/test_dirty_json_list_degradation.py",
    ),
    (
        # 收窄被撤（回到裸 ``except sqlite3.OperationalError``）⇒ 锁 / I/O 又被静默吞
        # ⚠️ **锚点带尾随上下文（2026-10-20 批次十三修）**：0032 批次在同文件新增了
        # ``_skill_excerpt``，其 except 块与本处**逐字同形**（``if not
        # _is_missing_schema_error(exc): raise`` + ``return None, None``）⇒ 锚点命中
        # **2 次** ⇒ ``[ABORT]``。⇒ 锚点补上**尾随的 ``if row is None:``**
        # （skill 侧是 ``if not rows:``）⇒ 重新唯一。
        "L0-GP1",
        "packages/core/context_engine/builders_common/common.py",
        """        if not _is_missing_schema_error(exc):
            raise
        return None, None
    if row is None:
        return None, None""",
        "        return None, None\n    if row is None:\n        return None, None",
        "tests/unit/test_genre_pack_schema_error_scope.py",
    ),
    (
        # planner_input 外层 wrapper 被加回 ⇒ 内层刚收窄的范围原样放大
        "L0-GP2",
        "packages/workflows/chapter_plan/planner_input.py",
        """        planner_inject, planner_audit = _genre_pack_excerpt(
            conn, project_id, consumer=_GENRE_PACK_CONSUMER_PLANNER
        )
    finally:""",
        """        planner_inject, planner_audit = _genre_pack_excerpt(
            conn, project_id, consumer=_GENRE_PACK_CONSUMER_PLANNER
        )
    except sqlite3.OperationalError:
        planner_inject, planner_audit = None, None
    finally:""",
        "tests/unit/test_genre_pack_schema_error_scope.py",
    ),
    (
        # scene_planner 外层 wrapper 被加回（同 L0-GP2，另一处调用方）
        "L0-GP3",
        "packages/workflows/chapter_write/scene_planner.py",
        """            genre_pack_inject, genre_pack_audit = _genre_pack_excerpt(
                conn_g, project_id, consumer=_GENRE_PACK_CONSUMER_PLANNER,
            )
        finally:""",
        """            genre_pack_inject, genre_pack_audit = _genre_pack_excerpt(
                conn_g, project_id, consumer=_GENRE_PACK_CONSUMER_PLANNER,
            )
        except sqlite3.OperationalError:
            genre_pack_inject = None
            genre_pack_audit = None
        finally:""",
        "tests/unit/test_genre_pack_schema_error_scope.py",
    ),
    (
        # quality 报告 scores_json 退回「只挡语法、不挡形状」
        "L0-QJSON1",
        "packages/core/quality/service.py",
        'd["scores_json"] = _decode_json_field(d["scores_json"], fallback={})',
        'd["scores_json"] = json.loads(d["scores_json"]) if d["scores_json"] else {}',
        "tests/api/test_dirty_json_list_degradation.py",
    ),
    (
        # quality 报告 issues_json 同款
        "L0-QJSON2",
        "packages/core/quality/service.py",
        """            d["issues_json"] = _decode_json_field(
                d["issues_json"], fallback=[], allowed=(list,)
            )""",
        '            d["issues_json"] = json.loads(d["issues_json"]) if d["issues_json"] else []',
        "tests/api/test_dirty_json_list_degradation.py",
    ),
]
