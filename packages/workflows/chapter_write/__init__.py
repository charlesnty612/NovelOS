"""chapter_write 包入口。

- :data:`WORKFLOW` dict 由 :mod:`pipeline` 定义。
- Sprint V1.5：注册到 :mod:`packages.core.workflow_registry`（core 侧注册中心）。
"""

from __future__ import annotations

from packages.core.workflow_registry import register_workflow

from .pipeline import WORKFLOW

register_workflow(WORKFLOW["name"], lambda: WORKFLOW)

__all__ = ["WORKFLOW"]
