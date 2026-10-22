r"""分层看守（``core`` ↛ ``workflows``）的**判据本身**的突变规格。

2026-10-22 批次二十二 · novelos-dev-a。

为什么突变目标是**测试文件**
----------------------------
这一轮要验的不是业务代码，而是**看守的判据**——而判据就住在
``tests/unit/test_no_core_to_workflows_dep.py`` 里（``_static_violations`` /
``_fold_str`` / ``_module_string_env`` / ``_import_bindings`` /
``_resolve_import_callee`` / ``scan_dynamic_workflow_imports`` /
``ALLOWED_DYNAMIC_WORKFLOW_IMPORTS`` …）。
⇒ 突变目标 = **该测试文件本身**（``rel``），验证对象 = **同文件里的判据**。
这是「突变规格必须打在**判据**上」的直接应用；跑测时改的只是**判据代码**，
模块仍可正常 import ⇒ 不会变成收集期错误（**假捕获**）。

三条突变打**三个不同的判据**
----------------------------
* **Q4-FOLD-1**——撤掉 ``_fold_str`` 的 ``BinOp(Add)`` 折叠分支。
  ⇒ 拆字符串形态（``"packages" + "." + "workflows"``）**折叠不出来**
  ⇒ 它不再被规则 R 抓到 ⇒ 8 形态里标 ``violation`` 通道的那几条**必红**。

* **Q4-CALLEE-1**——撤掉 ``_import_bindings`` 对 ``from X import Y`` 的解析。
  ⇒ ``from importlib import import_module`` 之后，``import_module(...)`` 不再被
  识别为「已知 import callee」⇒ **不可判定**通道失效 ⇒ 8 形态里标
  ``unfingerprintable`` 通道的那几条（字典取值 / 星号解包）**必红**。
  ⇒ 这条专打「callee 解析」这条判据（上一条打的是「折叠」）。

* **Q4-WHITELIST-1**——给 ``ALLOWED_DYNAMIC_WORKFLOW_IMPORTS`` **多放一条** core 文件。
  ⇒ 白名单被悄悄放宽（一个库层文件从此可以动态 import 业务流程包而不报）
  ⇒ ④ 的**精确集合相等**断言必红。
  ⇒ ⚠️ 旧版只钉**条数** ⇒ 把唯一那条**换成另一个文件**（条数仍是 1）测试照绿
  ⇒ 这条突变正是「精确集合相等」相对「只钉条数」的价值证明。

三条都是「**破坏判据但不破坏 import**」的写法。
"""

MUTATIONS = [
    # ------------------------------------------------------------------
    # ① 撤掉 BinOp 拼接折叠 ⇒ 拆字符串形态重新漏过
    # ------------------------------------------------------------------
    (
        "Q4-FOLD-1-binop-string-folding-removed",
        "tests/unit/test_no_core_to_workflows_dep.py",
        "        if left is not None and right is not None:\n            return left + right",
        "        if False:\n            return left + right",
        "tests/unit/test_no_core_to_workflows_dep.py",
    ),
    # ------------------------------------------------------------------
    # ② 撤掉 from-import 的可调用别名解析 ⇒ 不可判定通道失效
    # ------------------------------------------------------------------
    (
        "Q4-CALLEE-1-from-import-callable-alias-resolution-removed",
        "tests/unit/test_no_core_to_workflows_dep.py",
        "        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:",
        "        elif False:  # MUT: 不再解析 from-import 的可调用别名",
        "tests/unit/test_no_core_to_workflows_dep.py",
    ),
    # ------------------------------------------------------------------
    # ③ 白名单多放一条 core 文件 ⇒ 精确集合相等断言必红
    # ------------------------------------------------------------------
    (
        "Q4-WHITELIST-1-allowed-dynamic-imports-grows",
        "tests/unit/test_no_core_to_workflows_dep.py",
        'ALLOWED_DYNAMIC_WORKFLOW_IMPORTS: frozenset[tuple[str, str]] = frozenset(\n'
        '    {("packages/core/api/main.py", "packages.workflows")}\n'
        ')',
        'ALLOWED_DYNAMIC_WORKFLOW_IMPORTS: frozenset[tuple[str, str]] = frozenset(\n'
        '    {\n'
        '        ("packages/core/api/main.py", "packages.workflows"),\n'
        '        ("packages/core/quality/engine.py", "packages.workflows"),\n'
        '    }\n'
        ')',
        "tests/unit/test_no_core_to_workflows_dep.py",
    ),
]
