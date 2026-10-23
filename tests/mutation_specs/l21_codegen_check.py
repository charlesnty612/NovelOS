"""codegen 产物漂移闸门（``--check``）的突变规格（2026-10-23 批次三十，task-25/W-1）。

来源：台账 C-2。既有 ``tests/api/test_openapi_contract.py`` 只守**集合级**
（``info.version`` / ``paths`` / ``components.schemas`` 名集合）；实测把
``AdoptRequest.content`` 的 ``type`` 由 ``string`` 改 ``number``（名字集合不变）时，
那三条断言**全部通过** ⇒ **字段级漂移此前无看守**。本批补全文级 ``--check``。

三条突变与承接用例
------------------
| id | 撤掉的修复 / 改动 | 必红用例 |
|---|---|---|
| CG-ALWAYS-OK | ``check_artifact`` 恒返回一致（= 「闸门永远放行」这种最危险的假实现） | ``test_drift_is_detected`` / ``test_missing_artifact_is_drift`` |
| CG-NO-NEWLINE-NORM | ``normalize_newlines`` 退化为恒等（= 去掉换行归一 ⇒ 闸门随平台 / ``core.autocrlf`` 变脸） | 实测首个红在 ``test_repo_openapi_artifact_matches_regeneration``（仓库产物是 CRLF、重新生成是 LF）；``test_line_ending_difference_is_not_drift`` 同批也红 |
| CG-NO-MISSING | 产物缺失不再判不一致（删掉产物可蒙混过关） | ``test_missing_artifact_is_drift`` |

⚠️ 刻意**不**收 ``read_bytes`` → ``read_text`` 这一条：那是**等价突变**
（``read_text`` 的 universal-newline 转换恰好也把 CRLF 归一成 LF，行为不可观测）。
本仓纪律：突变 ``[SURVIVED]`` 时先判「等价」，再判「缺守门测试」——为一个不可观测的
差异去补一条写不出来的用例是错的。相关踩坑留痕见 ``scripts/_codegen_check.py`` docstring。

锚点均为全仓唯一的完整两行语句（validate_specs / precheck 已验）。
"""

MUTATIONS = [
    (
        "CG-ALWAYS-OK",
        "scripts/_codegen_check.py",
        "    if normalize_newlines(actual) == normalize_newlines(rendered):\n"
        '        return True, f"OK：{path} 与重新生成结果一致"\n',
        "    if True:  # MUTATION-CG-ALWAYS-OK: 闸门永远放行\n"
        '        return True, f"OK：{path} 与重新生成结果一致"\n',
        "tests/api/test_codegen_artifacts.py",
    ),
    (
        "CG-NO-NEWLINE-NORM",
        "scripts/_codegen_check.py",
        '    return text.replace("\\r\\n", "\\n")\n',
        "    return text  # MUTATION-CG-NO-NEWLINE-NORM: 去掉换行归一\n",
        "tests/api/test_codegen_artifacts.py",
    ),
    (
        "CG-NO-MISSING",
        "scripts/_codegen_check.py",
        "    if not path.exists():\n"
        '        return False, f"MISSING：产物不存在：{path}"\n',
        "    if False:  # MUTATION-CG-NO-MISSING: 产物缺失不再判漂移\n"
        '        return False, f"MISSING：产物不存在：{path}"\n',
        "tests/api/test_codegen_artifacts.py",
    ),
]
