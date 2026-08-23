"""deconstruct_book 包入口（Sprint 11 上半）。

- :data:`WORKFLOW` dict（含 name/nodes/version）由 :mod:`pipeline` 定义。
- 节点列表 = [T1 Transform] → [T2 Transform] → [T3 AI] → [G-sim Transform] → [T4 State]。
- 注册到全局中心 :mod:`packages.workflows.__init__`。
"""

from .pipeline import WORKFLOW, register_workflow, _validate_canon_schema

# 注册到全局中心
register_workflow(WORKFLOW)

__all__ = ["WORKFLOW", "register_workflow", "_validate_canon_schema"]