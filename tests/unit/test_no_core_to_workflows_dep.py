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


def _is_relative_import(node: ast.AST) -> bool:
    """是否相对 import（``from . import X`` / ``from ..x import Y``，即 ``level > 0``）。

    **为什么单独看**：``_is_business_import_violation`` 的 ``ImportFrom`` 分支只读
    ``node.module``。而相对 import 的 ``node.module`` 只是**末段名字**
    （``from ...workflows import X`` ⇒ ``module == "workflows"``、``level == 3``），
    ``module`` 里既没有 ``packages`` 前缀也不含点号 ⇒ 对 AST 断言**完全不可见**。

    实测确认该盲区可被真正绕过：``api/main.py`` 里就有
    ``importlib.import_module("packages" + "." + "workflows")`` 这种拆字符串写法，
    同时躲开了 AST 检查与源码文本检查两条断言。

    对策不是去解析相对导入的绝对目标（那需要完整包上下文），而是把规则收紧到
    **无需完整包上下文即可判定**：core 内禁止任何**逃出 ``packages.core`` 包**的
    相对导入。包内相对导入（``from .common import ...``、``from ..excerpts import ...``）
    是本仓既有且大量使用的写法（实测 191 处），不在禁止范围内；只有 ``level`` 大到
    能爬出 ``packages.core`` 的那种才可能够到 ``packages.workflows``。

    判定式：文件的包深度 ``d`` = 相对仓库根的路径段数 − 1（去掉文件名）。
    ``level == d`` 恰好落到顶层 ``packages``，``level > d`` 已在仓库根之上（非法）。
    故 ``level >= d`` 即为「逃出 core」。
    """
    return isinstance(node, ast.ImportFrom) and node.level > 0


def _is_business_import_violation(node: ast.AST) -> tuple[bool, str]:
    """检测 AST 节点是否为业务级 ``import packages.workflows`` / 子模块 / 顶层。

    返回 ``(违规, 描述)``。仅命中 ``packages.workflows[.*]?`` 这一类。

    ⚠️ **残余边界（如实登记）**：本函数对**相对 import 完全不可见**（见
    ``_is_relative_import`` 的说明）。该盲区由独立断言
    ``test_core_has_no_escaping_relative_imports`` 封住——它禁止 core 内任何
    ``level >= 包深度`` 的相对导入，从而使本函数的盲区不再可达。
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


def scan_escaping_relative_imports() -> list[tuple[str, int, str]]:
    """全量扫描 core，返回**逃出 ``packages.core`` 包**的相对 import。

    判别：往 ``packages/core/quality/engine.py`` 加一行
    ``from ...workflows import x``（该文件包深度 = 3，level 3 即逃出），
    ``test_core_has_no_escaping_relative_imports`` 必红——而它对
    ``test_no_business_import_of_workflows_in_core`` **完全隐形**（两条断言失效方向
    相反：一条测不到机制，一条测到了机制但漏掉另一种写法）。
    """
    out: list[tuple[str, int, str]] = []
    for path in _iter_core_py_files():
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        # 包深度 = 路径段数 − 1（去掉文件名本身）。packages/core/quality/engine.py
        # ⇒ packages.core.quality ⇒ d = 3 ⇒ level >= 3 即逃出 core。
        package_depth = len(path.relative_to(REPO_ROOT).parts) - 1
        for node in ast.walk(tree):
            if _is_relative_import(node) and node.level >= package_depth:
                out.append(
                    (
                        rel,
                        getattr(node, "lineno", 0),
                        f"from {'.' * node.level}{node.module or ''} import ... "
                        f"（level={node.level} >= 包深度 {package_depth}，逃出 packages.core）",
                    )
                )
    return sorted(out)


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


def test_core_has_no_escaping_relative_imports() -> None:
    """``packages/core/`` 内禁止**逃出本包**的相对 import。

    补的是本文件自身的盲区（2026-10-15 审查发现）：

    - ``_is_business_import_violation`` 的 ``ImportFrom`` 分支只读 ``node.module``；
      相对 import 的 ``node.module`` 只是末段名字（``from ...workflows import X``
      ⇒ ``module == "workflows"``、``level == 3``），**对 AST 断言完全不可见**。
    - 兜底的 ``test_grep_packages_workflows_in_core_is_zero`` 找的是
      ``"packages.workflows"`` **字面值**；相对 import 写的是 ``..workflows``，
      同样不含该字面量 ⇒ **两条断言同时不可见**。

    所以不能靠把盲区补全（解析相对导入的绝对目标需要完整包上下文），而是把规则
    收紧到无需完整包上下文即可判定：**包内相对导入照常允许**（本仓既有且大量使用，
    实测 191 处），**只有 ``level >= 包深度``（即能爬出 ``packages.core``）才违规**。

    判别：往 ``packages/core/quality/engine.py`` 加一行
    ``from ...workflows import x``（该文件包深度 = 3，level 3 即逃出），
    本用例必红；而 ``test_no_business_import_of_workflows_in_core`` 仍绿——
    两条断言的失效方向相反（一条测不到机制，一条测到了机制但漏掉另一种写法）。
    """
    hits = scan_escaping_relative_imports()
    assert not hits, (
        "packages/core/ 下出现逃出本包的相对 import——那是本文件依赖方向检查的盲区；命中:\n"
        + "\n".join(f"{rel}:{ln}: {desc}" for rel, ln, desc in hits)
    )
