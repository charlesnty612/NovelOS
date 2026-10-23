"""codegen 漂移闸门的 **CLI 层**突变规格（2026-10-23 批次三十一 task-3，3B 段 / D-8）。

来源：第二轮全面审查 M-8（D-8，MAJOR，缺口）。子代理实证：把测试里的
``export_main`` / ``gen_types_main`` 换成**恒返回 0** 的替身 ⇒ **8 条用例全绿**
（无一抓到）——因为全仓只对**已同步的仓内产物**调用过 ``--check``，只断言 ``== 0``、
**从不断言 ``!= 0``**。⇒ helper 层有守、**CLI 层没有**；而 CLI 正是 CI / pre-commit
要消费的那一层。

同批另两条 minor 也在这里钉住：

| id | 撤掉的修复 / 改动 | 必红用例 |
|---|---|---|
| ``CG-CLI-EXIT-EXPORT`` | ``export_openapi`` 的 ``--check`` 恒返回 0（漂移也放行） | ``test_export_check_returns_nonzero_on_drift`` |
| ``CG-CLI-EXIT-GEN`` | ``gen_frontend_types`` 的 ``--check`` 恒返回 0（漂移也放行） | ``test_gen_types_check_returns_nonzero_on_drift`` |
| ``CG-TMP-LEAK-1`` | ``--check`` 的临时 data 目录**不清理**（旧实现每次泄漏一个空目录） | ``test_check_does_not_leak_temp_dir`` |
| ``CG-DEFAULT-PATH-1`` | 默认路径回到 **CWD 相对**（⇒ 非仓库 CWD 执行即假漂移） | ``test_default_paths_are_anchored_to_repo_root`` + ``test_missing_repo_root_gives_clear_error_not_vague_missing`` |

⚠️ ``CG-DEFAULT-PATH-1`` 一条打**两处红**：默认路径不再锚定仓库根 ⇒ ① 在别的 CWD
下 ``--check`` 看不到仓内产物（exit 1）；② 「找不到仓库根」的明确文案分支不再触发
（改成含糊的 ``MISSING``）。两条都是「同一个修复的两面」，故写在一行里。

⚠️ 锚点均为**多行 / 唯一**上下文；replacement 一律不含双引号（PowerShell argv 剥引号）。
"""

MUTATIONS = [
    (
        "CG-CLI-EXIT-EXPORT",
        "scripts/export_openapi.py",
        '            f"    (openapi info.version={version!r}, interpreter={sys.executable})",\n'
        "            file=sys.stdout if ok else sys.stderr,\n"
        "        )\n"
        "        return 0 if ok else 1\n",
        '            f"    (openapi info.version={version!r}, interpreter={sys.executable})",\n'
        "            file=sys.stdout if ok else sys.stderr,\n"
        "        )\n"
        "        return 0  # MUTATION-CG-CLI-EXIT-EXPORT: 闸门恒放行\n",
        "tests/api/test_codegen_artifacts.py",
    ),
    (
        "CG-CLI-EXIT-GEN",
        "scripts/gen_frontend_types.py",
        '            f"{msg}\\n    (source={openapi_path}, interfaces={n_ifaces})",\n'
        "            file=sys.stdout if ok else sys.stderr,\n"
        "        )\n"
        "        return 0 if ok else 1\n",
        '            f"{msg}\\n    (source={openapi_path}, interfaces={n_ifaces})",\n'
        "            file=sys.stdout if ok else sys.stderr,\n"
        "        )\n"
        "        return 0  # MUTATION-CG-CLI-EXIT-GEN: 闸门恒放行\n",
        "tests/api/test_codegen_artifacts.py",
    ),
    (
        "CG-TMP-LEAK-1",
        "scripts/export_openapi.py",
        "        finally:\n            shutil.rmtree(tmp_dir, ignore_errors=True)\n",
        "        finally:\n            pass  # MUTATION-CG-TMP-LEAK-1: 撤掉临时目录清理\n",
        "tests/api/test_codegen_artifacts.py",
    ),
    (
        "CG-DEFAULT-PATH-1",
        "scripts/_codegen_check.py",
        '    return require_repo_root() / "apps" / "web" / "openapi.json"\n',
        '    return Path("apps/web/openapi.json")  # MUTATION-CG-DEFAULT-PATH-1: CWD 相对\n',
        "tests/api/test_codegen_artifacts.py",
    ),
]
