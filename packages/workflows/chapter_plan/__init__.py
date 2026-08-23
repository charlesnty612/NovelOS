"""chapter_plan 包入口。

- :data:`WORKFLOW` dict（含 name/nodes/version）由 :mod:`pipeline` 定义。
- 节点列表 = [Transform:build_ctx] → [AI:director] → [State:save_plan]。
"""

from .pipeline import WORKFLOW, register_workflow

# 注册到全局中心
register_workflow(WORKFLOW)

__all__ = ["WORKFLOW", "register_workflow"]
