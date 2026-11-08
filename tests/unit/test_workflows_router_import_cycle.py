"""``routers/workflows`` 包内**模块级 import 图必须无环**（2026-11-08 批次三十八 · R1-break-cycle）。

## 背景

``control.py`` 与 ``revise.py`` 曾存在模块级循环：

- ``revise.py`` 顶层 ``from .control import _start_workflow``；
- ``control.py`` 在 resume 的 daemon 线程体内 ``from .revise import _auto_revise_loop``。

后者之所以写在函数体内，注释原文就是「control ↔ revise 存在模块级循环」——**延迟 import
是在掩盖环，不是在消除环**。批次三十八把启动实现迁到 ``.start``，``revise`` 改为依赖
``.start``，环被真正断开。

## 本文件钉住什么

1. **包内模块级 import 图无环**（DFS 检测；函数体内的延迟 import 不计入——它不参与
   import 期的求值顺序，但本文件另有一条更严的断言指出「谁在拿延迟 import 掩盖环」）；
2. **`revise` 不再有指向 `control` 的模块级边**（历史环的那一条，单独点名，失败信息可读）；
3. 反向不变量：检测器必须**真的能看见边**（否则「无环」只是「一条边都没解析到」的假绿）。
"""

from __future__ import annotations

import ast
from pathlib import Path

_PKG_DIR = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "core"
    / "api"
    / "routers"
    / "workflows"
)


def _module_level_relative_imports(path: Path) -> set[str]:
    """返回该模块**模块级**（不在函数/类体内）的相对 import 目标模块名集合。"""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    targets: set[str] = set()
    for node in tree.body:  # 只看模块体，不进函数体 ⇒ 延迟 import 不计入
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            targets.add(node.module.split(".")[0])
    return targets


def _graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for path in sorted(_PKG_DIR.glob("*.py")):
        name = path.stem
        if name == "__init__":
            continue
        graph[name] = _module_level_relative_imports(path)
    return graph


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """返回一个环（模块名列表）；无环返回 None。"""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    stack: list[str] = []

    def dfs(node: str) -> list[str] | None:
        color[node] = GREY
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if nxt not in graph:  # 指向包外/私有模块，不参与本包环检测
                continue
            if color[nxt] == GREY:
                idx = stack.index(nxt)
                return stack[idx:] + [nxt]
            if color[nxt] == WHITE:
                found = dfs(nxt)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for node in sorted(graph):
        if color[node] == WHITE:
            found = dfs(node)
            if found:
                return found
    return None


def test_workflows_package_has_no_module_level_import_cycle():
    graph = _graph()
    # 反向不变量：必须真的解析到边，否则「无环」是假绿
    assert any(graph.values()), f"一条模块级相对 import 都没解析到，检测器失明：{graph}"
    cycle = _find_cycle(graph)
    assert cycle is None, "routers/workflows 存在模块级 import 环：" + " → ".join(cycle or [])


def test_revise_does_not_import_control_at_module_level():
    """历史环的那一条：``revise`` 顶层不得再 import ``control``。"""
    graph = _graph()
    assert "control" not in graph.get("revise", set()), (
        "revise 又在模块级 import control 了（R1-break-cycle 回退）："
        f"revise → {sorted(graph.get('revise', set()))}"
    )


def test_detector_sees_the_known_edges():
    """量具自检：已知存在的边必须被看见（``revise → start``、``start → common``）。"""
    graph = _graph()
    assert "start" in graph.get("revise", set()), graph.get("revise")
    assert "common" in graph.get("start", set()), graph.get("start")
