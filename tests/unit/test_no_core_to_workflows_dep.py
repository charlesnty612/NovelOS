r"""架构依赖方向看守（V1.5 起；2026-10-22 批次二十二重写，同批次 dev-a 修复）。

守三条方向：

1. **`packages/core` 不得 import `packages/workflows`**（业务流程包 → core，方向单向）。
2. **`packages/domain` 不得 import `packages/workflows`**（领域层不得依赖流程组装层）。
3. **`packages/**` 不得 import `apps`**（库层不得依赖应用层）。

## 2026-10-22 批次二十二：重写 + 修复

**背景**：旧实现（只看字面值）实测 7 种写法只拦 5 种——拆字符串的
``importlib.import_module("packages"+"."+"workflows")`` 与 ``__import__(...)`` 全漏，
而 ``packages/core/api/main.py`` 用的**正是**这种拆字符串形态（盲区成因）。

**重写后的判据（本文件当前实现）**：

- **静态 import**：AST 扫 ``Import`` / ``ImportFrom``（含 ``TYPE_CHECKING`` 块内、
  函数体内、类体内），**外加「import 父包 + 取子包」形态**
  （``from packages import workflows`` ⇒ 视作 ``packages.workflows``）；
  另有专门的「相对 import 逃出本包」断言（``level >= 包深度``）。
- **动态 import（规则 R，callee 无关）**：对**任何** ``ast.Call``，若其**任一实参**
  可常量折叠为**包含** ``packages.workflows`` 的字符串 ⇒ 违规（除非命中白名单）。
  ⇒ ``getattr`` / 别名 / from-import / ``functools.partial`` / ``exec`` / ``eval``
  **一次堵死**，不必逐个枚举 callee 语法。
- **不可判定**：仅当被调用者可解析为**已知 import callee**
  （``import_module`` / ``__import__`` / ``exec`` / ``eval``）且实参折不出时，
  才要求登记 ⇒ 不会把 ``log.warning(<不可折叠>)`` 这类普通调用误判。
- 两个白名单都是**精确集合相等**断言（不是只钉条数）。

⚠️ **规则 R 的窄化（本文件的实现口径，与 Lead 提议的「包含」有一处差别）**：
「包含」用的是**点分路径感知**的正则 ``(?<![\w.])packages\.workflows(?![\w])``
⇒ ``packages.workflows.chapter_write`` 命中，``notpackages.workflows`` **不**命中
（后者不是一条模块路径）。理由见「规则 R 会不会太宽」一节。

⚠️ **已知过宽面（如实登记，2026-10-22 对抗审查实测）**：规则 R 是 **callee 无关**的
⇒ **任何调用**只要实参折叠出该串就红，包括纯日志/纯比较：
``log.warning("about packages" + ".workflows")`` ⇒ **误判为违规**
（``assert x == "packages.workflows"`` 由字面量禁令拦，同样红）。
这是「宁可误报、不可漏报」的**必然代价**，当前树 **0 例**。
**处置**：遇到时**改措辞**（拆开写、或用 f-string 插值使其不可折叠），
**不要**为了过闸而往白名单里加条目（白名单只给装配入口）。

**为什么必须这样**：2026-10-22 实测，重写后的第一版**在树上就是红的**
（``2 failed``）——因为 ``_module_string_env`` 只收**模块级**绑定，而
``packages/core/api/main.py:215`` 的 ``_workflows_pkg = "packages" + "." + "workflows"``
在 ``create_app()`` **函数体内** ⇒ 折不出来 ⇒ ① 它被记成「不可判定」
② 它**永远命中不了**白名单（白名单形同虚设）。
⇒ 折叠器改为**全树**（不限作用域）+ 同名多值视为不可折叠。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "packages" / "core"
DOMAIN_ROOT = REPO_ROOT / "packages" / "domain"
ENTRY_FILE = CORE_ROOT / "api" / "main.py"

_TARGET_PKG_TOP = "packages.workflows"
_TARGET_PKG_PARENT = "packages"

#: 合法**动态** import 指向业务流程包的位置与**折叠后的目标**（精确匹配，不许前缀通配）。
#: 唯一合法用途 = 装配入口的插件发现（V1.5 约定）。
#: ⚠️ 目标必须**恰好**是 ``packages.workflows`` ⇒ 入口若改成
#: ``packages.workflows.chapter_write`` 之类，**不在**白名单 ⇒ 判违规。
ALLOWED_DYNAMIC_WORKFLOW_IMPORTS: frozenset[tuple[str, str]] = frozenset(
    {("packages/core/api/main.py", "packages.workflows")}
)

#: 动态 import 的实参**无法常量折叠**时的豁免，粒度 = **调用级**
#: （``(文件相对路径, callee 规范名)``）⇒ 同一文件里**别的**不可折叠动态 import
#: 不会被这条豁免顺带放过。
NON_FOLDABLE_DYNAMIC_IMPORTS: frozenset[tuple[str, str]] = frozenset(
    {("packages/core/api/routers/__init__.py", "importlib.import_module")}
)

#: 需要扫描的根（**core 与 domain 各跑一遍** ⇒ 不再是「半边看守」）。
GUARDED_ROOTS: tuple[str, ...] = ("packages/core", "packages/domain")

#: 依赖方向：(被扫描根, 禁止的模块前缀, 人类可读描述)。
LAYERING_DIRECTIONS: tuple[tuple[str, str, str], ...] = (
    ("packages/core", "packages.workflows", "core 不得依赖业务流程包"),
    ("packages/domain", "packages.workflows", "domain 不得依赖业务流程包"),
    ("packages", "apps", "库层不得依赖应用层 apps"),
)

#: 「字符串里出现了 ``packages.workflows``」的点分路径感知判据。
#: ``packages.workflows.chapter_write`` 命中；``notpackages.workflows`` 不命中。
_WORKFLOWS_TOKEN_RE = re.compile(r"(?<![\w.])packages\.workflows(?![\w])")


def _mentions_workflows(text: str) -> bool:
    """字符串是否**提到**业务流程包（点分路径感知）。"""
    return bool(_WORKFLOWS_TOKEN_RE.search(text))


def _iter_py_files(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*.py") if "__pycache__" not in p.parts
    )


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


# ---------------------------------------------------------------------------
# 静态 import
# ---------------------------------------------------------------------------


def _static_violations(tree: ast.AST, forbidden: str) -> list[tuple[int, str]]:
    """AST 扫静态 import：``import x`` / ``from x import y``（含任意嵌套层级）。

    **外加「import 父包 + 取子包」形态**（2026-10-22 批次二十二修复）：
    ``from packages import workflows`` 会真的执行 ``packages/workflows/__init__.py``
    （该文件存在）⇒ 是货真价实的分层破口；而旧的「只比 ``node.module``」口径
    完全看不见它 ⇒ 静态判据一度形同虚设。
    ⇒ 判据：``from X import Y`` 时，``"X.Y"`` 也可能是一条模块路径 ⇒ 一并检查。
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _matches_prefix(alias.name, forbidden):
                    out.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if _matches_prefix(module, forbidden):
                out.append((node.lineno, f"from {module} import ..."))
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                if _matches_prefix(f"{module}.{alias.name}", forbidden):
                    out.append(
                        (
                            node.lineno,
                            f"from {module} import {alias.name}（经父包取子包 "
                            f"⇒ 等价于 import {module}.{alias.name}）",
                        )
                    )
    return out


def _is_relative_import(node: ast.AST) -> bool:
    return isinstance(node, ast.ImportFrom) and node.level > 0


def scan_escaping_relative_imports(root_rel: str = "packages/core") -> list[tuple[str, int, str]]:
    """扫 ``root_rel``，返回**逃出本包**的相对 import。

    相对 import 的 ``node.module`` 只是末段名字（``from ...workflows import X`` ⇒
    ``module == "workflows"``、``level == 3``），对静态 import 断言**不可见**。
    对策不是解析绝对目标（需完整包上下文），而是收紧到**无需包上下文即可判定**：
    包内相对导入（``from .common import ...``）照常允许；
    只有 ``level >= 包深度``（能爬出本包）才违规。
    """
    out: list[tuple[str, int, str]] = []
    for path in _iter_py_files(REPO_ROOT / root_rel):
        tree = _parse(path)
        if tree is None:
            continue
        rel = _rel(path)
        package_depth = len(path.relative_to(REPO_ROOT).parts) - 1
        for node in ast.walk(tree):
            if _is_relative_import(node) and node.level >= package_depth:
                out.append(
                    (
                        rel,
                        getattr(node, "lineno", 0),
                        f"from {'.' * node.level}{node.module or ''} import ... "
                        f"（level={node.level} >= 包深度 {package_depth}，逃出 {root_rel}）",
                    )
                )
    return sorted(out)


# ---------------------------------------------------------------------------
# 动态 import（常量折叠 + 显式白名单）
# ---------------------------------------------------------------------------


def _fold_str(node: ast.AST, env: dict[str, str | None]) -> str | None:
    """把字符串表达式折叠成字面值；折叠不出来返回 ``None``（=「不可判定」）。

    支持：字符串常量 / 已在 ``env`` 里的名字 / ``a + b`` 拼接 / 纯常量 f-string。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_str(node.left, env)
        right = _fold_str(node.right, env)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None  # 含插值 ⇒ 不可折叠
        return "".join(parts)
    return None


def _module_string_env(tree: ast.AST) -> dict[str, str | None]:
    """收集**全树**（不限作用域）的字符串绑定。

    - 同一名字被赋**多个不同值** ⇒ 记 ``None``（不可判定，防遮蔽误判）。
    - 迭代到不动点 ⇒ 支持 ``A = "packages."`` → ``B = A + "workflows"`` 这类链。
    - **函数内**的局部常量**也算**：``packages/core/api/main.py`` 的
      ``_workflows_pkg`` 就在 ``create_app()`` 里 ⇒ 只收模块级绑定时它折不出来
      ⇒ 白名单命中不了（2026-10-22 批次二十二实测的 critical）。
    """
    exprs: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    exprs.setdefault(target.id, []).append(node.value)
    env: dict[str, str | None] = {}
    for _ in range(len(exprs) + 1):
        changed = False
        for name, values in exprs.items():
            folded: set[str] = set()
            ok = True
            for value in values:
                result = _fold_str(value, env)
                if result is None:
                    ok = False
                    break
                folded.add(result)
            new: str | None = folded.pop() if (ok and len(folded) == 1) else None
            if env.get(name, "\0unset") != new:
                env[name] = new
                changed = True
        if not changed:
            break
    return env


def _import_bindings(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    """返回 ``(模块别名 → 规范模块名, 可调用别名 → 规范可调用名)``。"""
    modules: dict[str, str] = {}
    callables: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    modules[alias.asname] = alias.name
                else:
                    modules[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                full = f"{node.module}.{alias.name}"
                if alias.asname:
                    callables[alias.asname] = full
                else:
                    callables[alias.name] = full
    return modules, callables


#: 已知「导入 callee」的规范名集合。
IMPORT_CALLEES = frozenset(
    {"importlib.import_module", "importlib.__import__", "__import__", "exec", "eval"}
)


def _resolve_import_callee(
    func: ast.AST,
    modules: dict[str, str],
    callables: dict[str, str],
) -> str | None:
    """把被调用者解析成**规范名**；不是导入 callee 则返回 ``None``。

    覆盖 2026-10-22 批次二十二实测漏掉的四种间接化：

    - ``from importlib import import_module``（⇒ ``callables``）
    - ``import importlib as il``（⇒ ``modules``）
    - ``functools.partial(importlib.import_module)``（⇒ 解 ``partial`` 的第一实参）
    - ``getattr(importlib, "import_module")``（⇒ 解 ``getattr`` 的第二实参）
    """
    if isinstance(func, ast.Name):
        if func.id in callables and callables[func.id] in IMPORT_CALLEES:
            return callables[func.id]
        if func.id in ("__import__", "exec", "eval"):
            return func.id
        return None
    if isinstance(func, ast.Attribute):
        base = func.value
        if (
            isinstance(base, ast.Name)
            and modules.get(base.id) == "importlib"
            and func.attr in ("import_module", "__import__")
        ):
            return f"importlib.{func.attr}"
        return None
    if isinstance(func, ast.Call):
        inner = func.func
        if (
            isinstance(inner, ast.Attribute)
            and inner.attr == "partial"
            and isinstance(inner.value, ast.Name)
            and modules.get(inner.value.id) == "functools"
            and func.args
        ):
            return _resolve_import_callee(func.args[0], modules, callables)
        if (
            isinstance(inner, ast.Name)
            and inner.id == "getattr"
            and len(func.args) == 2
            and isinstance(func.args[1], ast.Constant)
            and isinstance(func.args[1].value, str)
            and func.args[1].value in ("import_module", "__import__")
        ):
            base = func.args[0]
            if isinstance(base, ast.Name) and modules.get(base.id) == "importlib":
                return f"importlib.{func.args[1].value}"
        return None
    return None


def _call_args(call: ast.Call) -> list[ast.AST]:
    """调用的全部实参（位置 + 关键字值）。"""
    args = list(call.args)
    args.extend(kw.value for kw in call.keywords)
    return args


def scan_dynamic_workflow_imports(
    root_rel: str = "packages/core",
) -> tuple[list[str], list[str]]:
    """扫 ``root_rel`` 下的动态 import，返回 ``(违规列表, 须登记但未登记的不可判定列表)``。

    两条判据：

    ① **规则 R（callee 无关）**：任何 ``ast.Call`` 的任一实参若可常量折叠为**包含**
       ``packages.workflows`` 的字符串 ⇒ 违规（除非命中白名单）。
       ⇒ ``getattr`` / 别名 / from-import / partial / ``exec`` **一次堵死**。
    ② **不可判定**：仅当被调用者可解析为**已知 import callee** 且实参折不出时
       ⇒ 须登记在 ``NON_FOLDABLE_DYNAMIC_IMPORTS``（**调用级**：(rel, callee)）。
       ⇒ 不会把 ``log.warning(<不可折叠>)`` 这类普通调用误判。
    """
    violations: list[str] = []
    unfingerprintable: list[str] = []
    for path in _iter_py_files(REPO_ROOT / root_rel):
        tree = _parse(path)
        if tree is None:
            continue
        rel = _rel(path)
        env = _module_string_env(tree)
        modules, callables = _import_bindings(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            line = getattr(node, "lineno", 0)
            args = _call_args(node)
            # ① 规则 R
            for arg in args:
                folded = _fold_str(arg, env)
                if folded is not None and _mentions_workflows(folded):
                    if (rel, folded) not in ALLOWED_DYNAMIC_WORKFLOW_IMPORTS:
                        violations.append(
                            f"{rel}:{line}: 调用实参折叠出 {folded!r}（含 packages.workflows）"
                            "——库层禁止依赖业务流程包；如属装配入口请登记白名单"
                        )
                    break
            # ② 不可判定（**仅**已知 import callee）
            callee = _resolve_import_callee(node.func, modules, callables)
            if callee is None or not args:
                continue
            if all(_fold_str(arg, env) is None for arg in args):
                if (rel, callee) not in NON_FOLDABLE_DYNAMIC_IMPORTS:
                    unfingerprintable.append(
                        f"{rel}:{line}: {callee} 的实参**不可常量折叠**"
                        "（无法证明目标不是 packages.workflows）；"
                        "要么改成可折叠写法，要么登记进 "
                        "NON_FOLDABLE_DYNAMIC_IMPORTS（(rel, callee) 粒度）"
                    )
    return violations, unfingerprintable


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


def test_no_business_import_of_workflows_in_core() -> None:
    """① core 静态 import 业务流程包 = 0。"""
    hits: list[str] = []
    for path in _iter_py_files(CORE_ROOT):
        tree = _parse(path)
        if tree is None:
            continue
        for lineno, desc in _static_violations(tree, _TARGET_PKG_TOP):
            hits.append(f"{_rel(path)}:{lineno}: {desc}")
    assert not hits, "core 不得 import 业务流程包：\n" + "\n".join(hits)


def test_core_has_no_escaping_relative_imports() -> None:
    """② core 内禁止逃出本包的相对 import（封住静态断言的盲区）。"""
    hits = scan_escaping_relative_imports("packages/core")
    assert not hits, (
        "packages/core/ 下出现逃出本包的相对 import；命中:\n"
        + "\n".join(f"{rel}:{ln}: {desc}" for rel, ln, desc in hits)
    )


def test_no_dynamic_workflow_import_outside_whitelist() -> None:
    """③ **动态 import 也要守**（core 与 domain 各一遍）：拆字符串不再能绕过。

    判别（撤销本修复的写法会让本用例红）：在 ``packages/core/quality/engine.py`` 里加
    ``importlib.import_module("packages" + "." + "workflows")`` ⇒ 折叠出
    ``packages.workflows`` ⇒ 不在白名单 ⇒ 违规。
    """
    for root_rel in GUARDED_ROOTS:
        violations, unfingerprintable = scan_dynamic_workflow_imports(root_rel)
        assert not violations, (
            f"{root_rel} 出现指向业务流程包的动态 import（常量折叠后可判定）；命中:\n"
            + "\n".join(violations)
        )
        assert not unfingerprintable, (
            f"{root_rel} 存在**不可判定**的动态 import —— "
            "请改成可折叠写法或显式登记；命中:\n" + "\n".join(unfingerprintable)
        )


def test_dynamic_import_allowlists_are_pinned() -> None:
    """④ 两个白名单**精确集合相等** ⇒ 换掉一条（条数不变）也必红。

    ⚠️ 旧版只钉条数 ⇒ 把唯一那条**换成另一个文件**（条数仍是 1）测试照绿
    ⇒ 白名单可以被悄悄换掉而不被发现（2026-10-22 批次二十二实测的 minor）。
    """
    assert ALLOWED_DYNAMIC_WORKFLOW_IMPORTS == frozenset(
        {("packages/core/api/main.py", "packages.workflows")}
    ), (
        "ALLOWED_DYNAMIC_WORKFLOW_IMPORTS 必须逐条登记（加一条 / 换一条都要改这里）；"
        f"实得 {sorted(ALLOWED_DYNAMIC_WORKFLOW_IMPORTS)}"
    )
    assert NON_FOLDABLE_DYNAMIC_IMPORTS == frozenset(
        {("packages/core/api/routers/__init__.py", "importlib.import_module")}
    ), (
        "NON_FOLDABLE_DYNAMIC_IMPORTS 必须逐条登记（加一条 / 换一条都要改这里）；"
        f"实得 {sorted(NON_FOLDABLE_DYNAMIC_IMPORTS)}"
    )


def test_entry_main_uses_dynamic_import_for_workflow_discovery() -> None:
    """⑤ 装配入口仍必须用动态 import 触发业务流程注册（V1.5 约定，防被误删）。

    ⚠️ 折叠必须看得见**函数内**的局部常量：``main.py`` 的
    ``_workflows_pkg = "packages" + "." + "workflows"`` 在 ``create_app()`` 内
    ⇒ 只收模块级绑定时它折不出来 ⇒ 本用例会误红（2026-10-22 实测的 critical）。
    """
    tree = _parse(ENTRY_FILE)
    assert tree is not None
    env = _module_string_env(tree)
    modules, callables = _import_bindings(tree)
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _resolve_import_callee(node.func, modules, callables)
        if callee not in ("importlib.import_module", "importlib.__import__", "__import__"):
            continue
        for arg in _call_args(node):
            target = _fold_str(arg, env)
            if target is not None and _matches_prefix(target, _TARGET_PKG_TOP):
                found = True
    assert found, (
        "packages/core/api/main.py 必须通过 importlib.import_module(...) "
        "触发业务流程顶层 import（V1.5 架构约定）"
    )


def test_grep_packages_workflows_in_library_layer_is_zero() -> None:
    """⑥ 兜底字面量断言：core 与 domain 下 ``packages.workflows`` 字面值命中数为 0。

    ⚠️ **旧版的 ``main.py`` 豁免已删除**（2026-10-22 实测：``main.py`` 含该字面值
    **0 次** ⇒ 豁免用不上）。旧版「全局禁止字面值」**反而逼**任何动态 import 只能用
    **拆字符串**形态 —— 那正是盲区成因；现在由 ①②③ 精确看守，字面量禁令恢复全局生效。
    """
    hits: list[str] = []
    for root_rel in GUARDED_ROOTS:
        for path in _iter_py_files(REPO_ROOT / root_rel):
            rel = _rel(path)
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if _TARGET_PKG_TOP in line:
                    hits.append(f"{rel}:{i}: {line.strip()}")
    assert not hits, (
        "core/domain 下出现 'packages.workflows' 字面值；命中:\n" + "\n".join(hits)
    )


def test_layering_directions_are_clean() -> None:
    """⑦ 另外两个方向也不许破（此前只有 core↛workflows 一个看守）。

    - `packages/domain` ↔ `packages/workflows`：领域层不得依赖流程组装层；
    - `packages/**` ↔ `apps`：库层不得依赖应用层。

    实测基线（2026-10-22）：三条方向边均为 0。
    """
    hits: list[str] = []
    for root_rel, forbidden, desc in LAYERING_DIRECTIONS:
        root = REPO_ROOT / root_rel
        for path in _iter_py_files(root):
            tree = _parse(path)
            if tree is None:
                continue
            for lineno, what in _static_violations(tree, forbidden):
                hits.append(f"{_rel(path)}:{lineno}: {what} ⇒ {desc}")
    assert not hits, "分层方向被破坏：\n" + "\n".join(hits)


# ===========================================================================
# 批次二十二（dev-a 独立验证 + 修复）：绕过形态逐条喂判据
# ===========================================================================
# 手法：**合成源码字符串** + ``ast.parse``，**不往仓库注入任何真文件**。
# 对文件系统扫描型判据，替换的只是**两个入口**（「文件来源」与「解析器」），
# **判据逻辑（折叠 / 白名单 / 前缀匹配）一行不动**。

_PROBE_REL = "packages/core/quality/_probe.py"
_PROBE_REL_DOMAIN = "packages/domain/_probe.py"
_PROBE_REL_DEEP = "packages/core/x/y.py"


def _with_synthetic_source(monkeypatch, source: str, rel: str):
    """把合成源码装进本模块的扫描入口（不落盘）。返回 ``(tree, fake_path)``。"""
    tree = ast.parse(source)
    fake_path = REPO_ROOT / rel
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_iter_py_files", lambda root: [fake_path])
    monkeypatch.setattr(module, "_parse", lambda path: tree)
    return tree, fake_path


#: 8 种动态绕过形态 ⇒ 每一种都登记它**应当走哪条通道**被拦：
#: ``violation`` = 规则 R 命中；``unfingerprintable`` = 折不出 ⇒ 须登记。
#: （两种都是「拦」，但通道不同 ⇒ 断言必须分别钉住，否则测的就不是同一个机制。）
_DYNAMIC_BYPASS_FORMS: tuple[tuple[str, str, str], ...] = (
    (
        "getattr-attr",
        'import importlib\n'
        'getattr(importlib, "import_module")("packages" + "." + "workflows")\n',
        "violation",
    ),
    (
        "from-import-alias",
        "from importlib import import_module\n"
        'import_module("packages" + "." + "workflows")\n',
        "violation",
    ),
    (
        "module-alias",
        "import importlib as il\n"
        'il.import_module("packages" + "." + "workflows")\n',
        "violation",
    ),
    (
        "functools-partial",
        "import functools\n"
        "import importlib\n"
        'functools.partial(importlib.import_module)("packages" + "." + "workflows")\n',
        "violation",
    ),
    (
        "dict-subscript",
        'import importlib\n'
        '_PKG = {"wf": "packages.workflows"}\n'
        'importlib.import_module(_PKG["wf"])\n',
        "unfingerprintable",
    ),
    (
        "exec-split",
        'exec("import packages" + ".workflows")\n',
        "violation",
    ),
    (
        "star-args",
        "from importlib import import_module\n"
        'import_module(*["packages" + "." + "workflows"])\n',
        "unfingerprintable",
    ),
    (
        "baseline-split",
        "import importlib\n"
        'importlib.import_module("packages" + "." + "workflows")\n',
        "violation",
    ),
)


@pytest.mark.parametrize(
    ("source", "channel"),
    [(src, ch) for _n, src, ch in _DYNAMIC_BYPASS_FORMS],
    ids=[name for name, _s, _c in _DYNAMIC_BYPASS_FORMS],
)
def test_dynamic_bypass_forms_are_detected(monkeypatch, source, channel):
    """① **8 种动态绕过形态** ⇒ 每一种都必须被拦（通道按登记）。

    这 8 种是 2026-10-22 审查员实测的清单（其中 6 种旧实现漏过）。
    """
    _with_synthetic_source(monkeypatch, source, _PROBE_REL)
    violations, unfingerprintable = scan_dynamic_workflow_imports("packages/core")
    if channel == "violation":
        assert violations, f"漏过（未判违规）：{source!r}"
    else:
        assert unfingerprintable, f"漏过（未判不可判定）：{source!r}"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "from packages.workflows.chapter_write import pipeline\n",
            id="static-from",
        ),
        pytest.param(
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from packages.workflows.x import Y\n",
            id="static-type-checking",
        ),
        pytest.param(
            "def f():\n    from packages.workflows.x import Y\n",
            id="static-in-function",
        ),
        pytest.param(
            "from packages import workflows\n",
            id="static-parent-package",
        ),
    ],
)
def test_static_bypass_forms_are_detected(source):
    """② 静态四种形态：普通 / ``TYPE_CHECKING`` 块内 / 函数体内 / **经父包取子包**。

    最后一种（``from packages import workflows``）会真的执行
    ``packages/workflows/__init__.py``（该文件存在）⇒ 是货真价实的分层破口。
    """
    tree = ast.parse(source)
    assert _static_violations(tree, _TARGET_PKG_TOP), f"漏过：{source!r}"


def test_relative_escape_is_detected(monkeypatch):
    """③ 相对逃逸：深度 ≥3 的包里 ``from ...workflows import Y`` ⇒ 必须被拦。"""
    _with_synthetic_source(monkeypatch, "from ...workflows import Y\n", _PROBE_REL_DEEP)
    hits = scan_escaping_relative_imports("packages/core")
    assert hits, "漏过：相对逃逸的 from ...workflows import Y"


def test_domain_root_is_guarded_too(monkeypatch):
    """④ **domain 也被守**（旧实现只扫 core ⇒ 动态方向是「半边看守」）。

    把基准形态放进 ``packages/domain/thing.py`` ⇒ 必须被判违规。
    """
    source = "import importlib\n" 'importlib.import_module("packages" + "." + "workflows")\n'
    _with_synthetic_source(monkeypatch, source, _PROBE_REL_DOMAIN)
    violations, _unfingerprintable = scan_dynamic_workflow_imports("packages/domain")
    assert violations, "漏过：domain 下的动态 import 未被判违规"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            'import importlib\nimportlib.import_module("packages.core.quality")\n',
            id="importlib-core",
        ),
        pytest.param(
            '__import__("json")\n',
            id="dunder-stdlib",
        ),
    ],
)
def test_legitimate_dynamic_forms_are_not_flagged(monkeypatch, source):
    """⑤ 反向用例：库层内部的**合法**动态 import ⇒ 不得误报（否则守看会变成噪声源）。"""
    _with_synthetic_source(monkeypatch, source, _PROBE_REL)
    violations, unfingerprintable = scan_dynamic_workflow_imports("packages/core")
    assert not violations, f"误报（判违规）：{source!r}"
    assert not unfingerprintable, f"误报（判不可判定）：{source!r}"


def test_whitelisted_entry_form_is_not_flagged(monkeypatch):
    """⑥ 反向用例：白名单登记的那一处（``main.py`` 指向 ``packages.workflows``）
    ⇒ 不得判违规。

    喂的是**模块级**常量形态（与 ``ALLOWED_DYNAMIC_WORKFLOW_IMPORTS`` 的登记语义
    一致：折叠后的目标必须**恰好**是 ``packages.workflows``，精确匹配、不许前缀通配）。
    """
    source = (
        "import importlib\n"
        '_PKG = "packages" + "." + "workflows"\n'
        "importlib.import_module(_PKG)\n"
    )
    _with_synthetic_source(monkeypatch, source, "packages/core/api/main.py")
    violations, unfingerprintable = scan_dynamic_workflow_imports("packages/core")
    assert not violations, f"白名单登记的形态被判违规：{violations}"
    assert not unfingerprintable, f"白名单登记的形态被判不可判定：{unfingerprintable}"


def test_real_entry_main_function_local_binding_is_clean(monkeypatch):
    """⑦ **真实** ``main.py`` 的那条动态 import（**函数内**局部常量形态）⇒ 应当干净。

    ⚠️ 这条是 2026-10-22 批次二十二实测的 **critical**：
    ``_workflows_pkg = "packages" + "." + "workflows"`` 在 ``create_app()`` **函数体内**
    ⇒ 只收模块级绑定时折不出来 ⇒ 既判不出违规、也命中不了白名单。
    ⇒ 折叠器改为**全树**后本用例才绿（⇒ 它是「折叠器作用域」这条判据的判别点）。
    """
    tree = _parse(ENTRY_FILE)
    assert tree is not None
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_iter_py_files", lambda root: [ENTRY_FILE])
    monkeypatch.setattr(module, "_parse", lambda path: tree)
    violations, unfingerprintable = scan_dynamic_workflow_imports("packages/core")
    assert not violations, violations
    assert not unfingerprintable, unfingerprintable
