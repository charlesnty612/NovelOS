"""业务流程注册中心（Sprint 4-A）。

- :func:`all_workflows` → 返回 ``{name: WORKFLOW}`` 字典，name 即注册名。
- :func:`get_workflow(name)` → 返回单条 ``WORKFLOW``（含 ``nodes`` 与 ``name``）。
- :func:`register(engine, name, ...)` → 在 ``WorkflowEngine`` 上启动指定工作流。

每条 pipeline 在自己包内暴露 :data:`WORKFLOW`（dict，含 name/nodes/version）。
"""

from __future__ import annotations

from typing import Any

_REGISTRY: dict[str, dict[str, Any]] = {}


def register_workflow(workflow: dict[str, Any]) -> None:
    """注册一条工作流（一般由各包 ``__init__.py`` 调用一次）。"""
    name = workflow.get("name")
    if not name:
        raise ValueError("workflow must have 'name'")
    _REGISTRY[name] = workflow


def all_workflows() -> dict[str, dict[str, Any]]:
    """返回 ``{name: WORKFLOW}`` 副本；不暴露内部状态。"""
    return dict(_REGISTRY)


def get_workflow(name: str) -> dict[str, Any] | None:
    """按名查 workflow dict。"""
    return _REGISTRY.get(name)


# 模块级副作用：import 各 pipeline 包，触发 register_workflow
from packages.workflows.chapter_commit import pipeline as _commit  # noqa: E402, F401
from packages.workflows.chapter_plan import pipeline as _plan  # noqa: E402, F401
from packages.workflows.chapter_review import pipeline as _review  # noqa: E402, F401
from packages.workflows.chapter_write import pipeline as _write  # noqa: E402, F401

__all__ = ["all_workflows", "get_workflow", "register_workflow"]
