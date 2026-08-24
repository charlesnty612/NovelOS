"""deconstruct_book 包入口（Sprint 11 上半）。

- :data:`WORKFLOW` dict（含 name/nodes/version）由 :mod:`pipeline` 定义。
- 节点列表 = [T1 Transform] → [T2 Transform] → [T3 AI] → [G-sim Transform] → [T4 State]。
- Sprint V1.5：注册到 :mod:`packages.core.workflow_registry`（core 侧注册中心），
  替代原先的中心注册 :mod:`packages.workflows.__init__`。
"""

from __future__ import annotations

from packages.core.workflow_registry import register_workflow

from .pipeline import WORKFLOW, _validate_canon_schema

register_workflow(WORKFLOW["name"], lambda: WORKFLOW)

__all__ = ["WORKFLOW", "_validate_canon_schema"]