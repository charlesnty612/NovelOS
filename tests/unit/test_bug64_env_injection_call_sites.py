"""BUG-64②：启动路径**显式注入 env**（``ensure_env_defaults``）三处调用点的源码守卫。

背景
----
BUG-59（复查修③）要求：启动路径必须**显式注入** ``NOVELOS_DATA_DIR`` /
``NOVELOS_DB_PATH``（全仓零处 ``load_dotenv`` ⇒ ``_env`` 只读 ``os.environ``）。
注入点共**三处**：

1. ``packages/core/api/main.py`` 的 ``create_app()``（模块级 ``app = create_app()``
   ⇒ **import 本模块即注入**）；
2. ``packages/core/api/main.py`` 的 ``if __name__ == "__main__":`` 块；
3. ``scripts/serve.py`` 的 ``main()``。

⚠️ **为什么必须用源码守卫**：这三条**删掉任何一处**，``tests/unit/test_config.py``
仍然全绿（那条文件只验 ``ensure_env_defaults`` **函数自身**的行为，不验**调用点**
还在不在）⇒ 「函数是对的」不等于「启动路径真的调了它」。

判据形态（AST 级，**不**是子串匹配）
------------------------------------
* 用 ``ast`` 解析源文件 ⇒ 断言「**在指定作用域内**存在对 ``ensure_env_defaults``
  的调用」：删调用 ⇒ 找不到 ⇒ 必红；把调用挪出该作用域（如挪进模块级）⇒ 必红。
* **只读源码、不 import 生产模块** ⇒ 本文件不触发 ``import`` 期副作用
  （``packages.core.api.main`` 一 import 就会建 FastAPI 应用并写 ``os.environ``）。

BUG-62 口径（**注**：本条口径的**注释位置**在 ``packages/core/config.py`` 的
``ensure_env_defaults`` 附近（已落一行口径注释），行为由
``tests/unit/test_config.py::test_bug62_注入后全链只打一条WARNING`` 钉住）：

    全链**只打一条** WARNING。实测链路：``ensure_env_defaults`` 首调
    ``resolve_data_paths()`` 时 env 尚未注入 ⇒ 打一条兜底 WARNING ⇒ 随即把
    env 注入进去 ⇒ 之后 ``get_settings()`` 再调 ``resolve_data_paths()`` 时
    「两个 env 都缺」的条件**已不成立** ⇒ 不再打 ⇒ 全链恰好一条。
"""

from __future__ import annotations

import ast
from pathlib import Path

#: 仓根（本文件 ``tests/unit/test_bug64_env_injection_call_sites.py`` 上溯三级）。
REPO_ROOT = Path(__file__).resolve().parents[2]

MAIN_PY = REPO_ROOT / "packages" / "core" / "api" / "main.py"
SERVE_PY = REPO_ROOT / "scripts" / "serve.py"

#: 被守卫的注入函数名（真源：``packages/core/config.py::ensure_env_defaults``）。
INJECT_FUNC = "ensure_env_defaults"


def _parse(path: Path) -> ast.Module:
    """解析源文件为 AST（只读；不 import ⇒ 无副作用）。"""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _calls_named(node: ast.AST, name: str) -> list[ast.Call]:
    """节点树里所有对**裸名字** ``name`` 的调用（属性调用 ``x.name()`` 不算）。

    ⚠️ **不下钻嵌套函数/lambda**：藏在 ``def inner(): ...`` / ``lambda: ...`` 里的
    调用**永不执行**（除非被显式调用）⇒ 不算该作用域的调用点。
    ⇒ 把调用**挪进嵌套 def** 骗不过本守卫（此前用 ``ast.walk`` 会一并下钻
    ⇒ 藏进嵌套 def 仍被判绿）。

    作用域口径：**模块级只认顶层语句**；``create_app`` / ``main()`` 这类函数
    **只认它自己函数体那一层**（更深一层的嵌套 def 不算）。
    """
    found: list[ast.Call] = []
    stack: list[ast.AST] = [node]
    while stack:
        current = stack.pop()
        if (
            isinstance(current, ast.Call)
            and isinstance(current.func, ast.Name)
            and current.func.id == name
        ):
            found.append(current)
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # 不下钻嵌套函数/lambda（永不执行 ⇒ 不算调用点）
            stack.append(child)
    return found


def _function_def(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """取**模块级**函数定义（同名嵌套函数不算）。"""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"源码里找不到模块级函数 {name}")


def _is_main_guard(test: ast.expr) -> bool:
    """判断是不是 ``__name__ == "__main__"`` 这个守卫条件。"""
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def _main_guard_block(tree: ast.Module) -> ast.If:
    """取模块级 ``if __name__ == "__main__":`` 块。"""
    for node in tree.body:
        if isinstance(node, ast.If) and _is_main_guard(node.test):
            return node
    raise AssertionError('源码里找不到模块级 `if __name__ == "__main__":` 块')


def _module_level_app_assignment(tree: ast.Module) -> list[ast.Assign]:
    """取模块级 ``app = create_app()`` 赋值（import 期即建应用的路径）。"""
    return [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "app" for t in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "create_app"
    ]


def test_bug64_main_create_app_still_injects_env() -> None:
    """★ BUG-64② 判别①：``main.py`` 的 ``create_app()`` 里必须**调用** ``ensure_env_defaults``。

    删掉该调用 ⇒ 本条必红（``assert`` 失败，非 AttributeError）。
    """
    tree = _parse(MAIN_PY)
    fn = _function_def(tree, "create_app")
    calls = _calls_named(fn, INJECT_FUNC)
    assert calls, (
        f"{MAIN_PY.name} 的 create_app() 里不再调用 {INJECT_FUNC}() ⇒ "
        "启动路径不再显式注入 env（库落点不确定，BUG-59 复查修③失守）"
    )


def test_bug64_main_module_level_app_is_still_created() -> None:
    """★ BUG-64② 判别②：``main.py`` 必须保留模块级 ``app = create_app()``。

    这是**import 期就注入 env** 的那条路径（``uvicorn packages.core.api.main:app``
    依赖它）⇒ 删掉它 ⇒ ``import`` 本模块不再建应用、不再注入 env。
    """
    tree = _parse(MAIN_PY)
    assigns = _module_level_app_assignment(tree)
    assert assigns, (
        f"{MAIN_PY.name} 里没有模块级 `app = create_app()` ⇒ "
        "import 期路径（连同它的 env 注入）消失"
    )


def test_bug64_main_dunder_main_block_still_injects_env() -> None:
    """★ BUG-64② 判别③：``main.py`` 的 ``if __name__ == "__main__":`` 块里必须**调用**
    ``ensure_env_defaults``。

    删掉该调用 ⇒ 本条必红。
    """
    tree = _parse(MAIN_PY)
    block = _main_guard_block(tree)
    calls = _calls_named(block, INJECT_FUNC)
    assert calls, (
        f'{MAIN_PY.name} 的 `if __name__ == "__main__":` 块里不再调用 {INJECT_FUNC}() ⇒ '
        "`python -m packages.core.api.main` 这条启动路径不再显式注入 env"
    )


def test_bug64_serve_main_still_injects_env() -> None:
    """★ BUG-64② 判别④：``scripts/serve.py`` 的 ``main()`` 里必须**调用** ``ensure_env_defaults``。

    删掉该调用 ⇒ 本条必红。
    """
    tree = _parse(SERVE_PY)
    fn = _function_def(tree, "main")
    calls = _calls_named(fn, INJECT_FUNC)
    assert calls, (
        f"{SERVE_PY.name} 的 main() 里不再调用 {INJECT_FUNC}() ⇒ "
        "CLI 启动路径不再显式注入 env"
    )
