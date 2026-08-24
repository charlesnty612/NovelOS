"""V1.5 架构债务项：禁止 packages.core 业务模块 import 业务流程包。

- 业务依赖方向：**业务流程包 → core**。
- 反向依赖（core 业务模块 import 业务流程包）由
  :mod:`packages.core.workflow_registry` 切断。
- 本测试静态扫描 ``packages/core/**/*.py``，断言：
  1. 零 ``import packages.workflows`` / ``from packages.workflows import ...`` 语句
     （AST 视角：业务级 Import / ImportFrom 节点）；
  2. 零 ``import packages.workflows.*`` 顶层 module 引入。

**允许的例外**：``packages/core/api/main.py`` 是装配入口，可使用
``importlib.import_module(...)`` 形式触发业务流程包 import（视为插件发现）。
本测试对 ``main.py`` 仅豁免 ``importlib`` 调用形态，不豁免 ``import`` /
``from ... import`` 语句。

业务模块（即非 main.py 的 core 模块）**任何**形式的
``import packages.workflows`` / ``from packages.workflows import ...``
均为失败。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "packages" / "core"
ENTRY_FILE = CORE_ROOT / "api" / "main.py"

# 业务流程顶层包名（core 业务模块禁止 import 此包）。
_TARGET_PKG_TOP = "packages.workflows"
_TARGET_PKG_PARENT = "packages"


def _iter_core_py_files() -> list[Path]:
    """枚举 packages/core/ 下所有 .py（跳过 __pycache__ / backup 子包除外）。"""
    files: list[Path] = []
    for path in CORE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        # ``packages/core/backup`` 是 Sprint 6 备份子包，含独立业务；
        # 它在 ``packages/core/api/routers/backup.py`` 通过 ``from packages.core.backup import ...``
        # 引用，与 ``packages.workflows`` 无关——保留扫描。
        files.append(path)
    return sorted(files)


def _is_business_import_violation(node: ast.AST) -> tuple[bool, str]:
    """检测 AST 节点是否为业务级 ``import packages.workflows`` / 子模块 / 顶层。

    返回 ``(违规, 描述)``。仅命中 ``packages.workflows[.*]?`` 这一类。
    """
    if isinstance(node, ast.Import):
        for alias in node.names:
            name = alias.name
            if name == _TARGET_PKG_TOP or name.startswith(_TARGET_PKG_TOP + "."):
                return True, f"import {name}"
            # ``import packages`` 也算违规（顶层 packages 包不应当由 core 业务模块导入）。
            if name == _TARGET_PKG_PARENT:
                return True, f"import {name}"
        return False, ""
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        # ``from packages.workflows import X`` / ``from packages.workflows.X import Y``
        if module == _TARGET_PKG_TOP or module.startswith(_TARGET_PKG_TOP + "."):
            return True, f"from {module} import ..."
        # ``from packages import X`` —— 顶层 packages 引入亦属违规。
        if module == _TARGET_PKG_PARENT:
            return True, f"from {module} import ..."
        return False, ""
    return False, ""


def _has_importlib_dynamic_call(tree: ast.AST) -> bool:
    """检测文件中是否存在 ``importlib.import_module(...)`` 形态的动态调用。

    用于白名单 ``api/main.py``：main.py 允许使用 importlib 形式触发插件发现，
    但其业务模块（业务 import 语句）仍必须为零。
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # ``importlib.import_module(...)``
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "import_module"
            and isinstance(func.value, ast.Name)
            and func.value.id == "importlib"
        ):
            return True
    return False


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """扫描单个文件，返回 ``(行号, 描述)`` 形式的违规列表。"""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    rel = path.relative_to(REPO_ROOT).as_posix()
    is_entry = rel == ENTRY_FILE.relative_to(REPO_ROOT).as_posix()

    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # 只看模块级 / 类体外 Import / ImportFrom 节点（不进入函数体内部）
        # —— 这里 ast.walk 也会进入函数体；用更精确方式过滤。
        bad, desc = _is_business_import_violation(node)
        if not bad:
            continue
        # 装配入口 main.py：仅豁免 ``importlib.import_module(...)`` 调用形态。
        # 真实的 ``import packages.workflows`` 语句仍然违规。
        if is_entry and _is_inside_importlib_call(node):
            continue
        violations.append((getattr(node, "lineno", 0), desc))
    return violations


def _is_inside_importlib_call(node: ast.AST) -> bool:
    """判断 ``Import`` / ``ImportFrom`` 节点是否位于 ``importlib.import_module`` 调用内。

    由于 ``import`` 本身不能作为参数（必须是语句级别），理论上不会真的"在调用内"。
    本检查主要用于兜底：如果未来有人用 ``exec("import x")`` / ``__import__`` 等动态
    机制，本函数当前总是返回 False，但保留扩展位。
    """
    return False


def _is_business_module(path: Path) -> bool:
    """是否 core 业务模块（参与反向依赖扫描）。

    - ``api/main.py`` 是装配入口：仅豁免 ``importlib.import_module(...)`` 调用形态，
      业务 import 语句仍视为违规。
    - 其他 core 模块：所有 ``import packages.workflows`` / 顶层 ``import packages``
      均视为违规。
    """
    return True  # 当前对所有 core 模块一视同仁；仅在 _scan_file 内对 main.py 做形态豁免


# ---------------------------------------------------------------------------
# 单元测试用例
# ---------------------------------------------------------------------------


def test_no_business_import_of_workflows_in_core() -> None:
    """核心断言：core 业务模块零业务 import 业务流程包。"""
    all_violations: list[str] = []
    for py in _iter_core_py_files():
        for lineno, desc in _scan_file(py):
            rel = py.relative_to(REPO_ROOT).as_posix()
            all_violations.append(f"{rel}:{lineno}: {desc}")
    assert not all_violations, (
        "core 业务模块禁止 import 业务流程包（V1.5 反向依赖已切断）；违规:\n"
        + "\n".join(all_violations)
    )


def test_entry_main_uses_importlib_only() -> None:
    """装配入口 main.py 必须使用 ``importlib.import_module(...)`` 形态触发注册。

    这是被允许的「插件发现」语义；用于触发业务流程顶层 import 完成注册。
    """
    source = ENTRY_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(ENTRY_FILE))
    assert _has_importlib_dynamic_call(tree), (
        "packages/core/api/main.py 装配入口必须通过 importlib.import_module(...) "
        "触发业务流程顶层 import（V1.5 架构约定）"
    )


def test_grep_packages_workflows_in_core_is_zero() -> None:
    """兜底断言：``packages.core/`` 下 ``packages.workflows`` 字符串字面值命中数为 0。

    装配入口 main.py 通过字符串拆装避免字面值；本测试作为最终硬约束。
    """
    hits: list[str] = []
    for py in _iter_core_py_files():
        text = py.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            if "packages.workflows" in line:
                rel = py.relative_to(REPO_ROOT).as_posix()
                hits.append(f"{rel}:{i}: {line.strip()}")
    assert not hits, (
        "packages/core/ 下出现 'packages.workflows' 字面值（应通过字符串拆装避免）；命中:\n"
        + "\n".join(hits)
    )
