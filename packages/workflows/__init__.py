"""业务流程包入口（Sprint V1.5 架构债务项）。

Sprint V1.5 起，工作流注册中心下沉到 core
（:mod:`packages.core.workflow_registry`）；本包仅保留**对外兼容 façade**：

- :func:`get_workflow` → 委托 core 注册表查询。
- :func:`all_workflows` → 委托 core 注册表快照。
- :func:`register_workflow` → 委托 core 注册 API（保留 workflows 旧调用形态）。

本包顶层副作用：import 各业务 pipeline 子包，触发其 :func:`register_workflow` 调用
core 注册中心（仅放置 builder，不构造节点）。装配入口
:mod:`packages.core.api.main` 通过 ``importlib.import_module("packages.workflows")``
触发本包加载——这是允许的「插件发现」模式，core 业务模块零依赖 workflows。
"""

from __future__ import annotations

from typing import Any, Callable

from packages.core.workflow_registry import (
    all_workflows as _all_workflows,
)
from packages.core.workflow_registry import (
    get_workflow as _get_workflow,
)
from packages.core.workflow_registry import (
    register_workflow as _register_workflow,
)

# 触发各子包 __init__：每个子包在本步骤内调 core 注册表写入 builder。
from packages.workflows.chapter_commit import pipeline as _commit  # noqa: E402, F401
from packages.workflows.chapter_plan import pipeline as _plan  # noqa: E402, F401
from packages.workflows.chapter_review import pipeline as _review  # noqa: E402, F401
from packages.workflows.chapter_write import pipeline as _write  # noqa: E402, F401
from packages.workflows.deconstruct_book import pipeline as _deconstruct_book  # noqa: E402, F401
from packages.workflows.project_init import pipeline as _project_init  # noqa: E402, F401


def register_workflow(workflow_or_name: Any, builder: Callable[[], dict[str, Any]] | None = None) -> None:
    """兼容 façade：支持两种调用形态。

    1. ``register_workflow(workflow_dict)`` —— 旧中心调用形态（直接传 dict）；
       等价于 ``register_workflow(workflow_dict["name"], lambda: workflow_dict)``。
    2. ``register_workflow(name, builder)`` —— 新 core 注册表调用形态。
    """
    if builder is None:
        # 形态 1：旧 dict 入参
        if not isinstance(workflow_or_name, dict):
            raise TypeError(
                "register_workflow expects a dict (legacy) or (name, builder) (new)"
            )
        wf = workflow_or_name
        name = wf.get("name")
        if not name:
            raise ValueError("workflow dict must have 'name'")
        _register_workflow(name, lambda: wf)
    else:
        # 形态 2：新 core 形态
        _register_workflow(workflow_or_name, builder)


def get_workflow(name: str) -> dict[str, Any] | None:
    """委托 :func:`packages.core.workflow_registry.get_workflow` 查询。"""
    return _get_workflow(name)


def all_workflows() -> dict[str, dict[str, Any]]:
    """委托 :func:`packages.core.workflow_registry.all_workflows` 取快照。"""
    return _all_workflows()


__all__ = ["register_workflow", "get_workflow", "all_workflows"]
