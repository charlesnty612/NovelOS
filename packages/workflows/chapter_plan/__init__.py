"""chapter_plan 包入口。

- :data:`WORKFLOW` dict（含 name/nodes/version）由 :mod:`pipeline` 定义。
- 节点列表 = [Transform:build_ctx] → [AI:director] → [State:save_plan]。
- Sprint V1.5：注册到 :mod:`packages.core.workflow_registry`（core 侧注册中心），
  装配入口 :mod:`packages.core.api.main` 通过 ``importlib.import_module`` 触发本包
  import 完成注册（业务模块不再依赖 packages.workflows 中心）。
"""

from __future__ import annotations

from packages.core.workflow_registry import register_workflow

from .pipeline import WORKFLOW

# 惰性 builder：第一次 ``get_workflow`` 时构建 dict，避免 import 阶段构造节点。
register_workflow(WORKFLOW["name"], lambda: WORKFLOW)

__all__ = ["WORKFLOW"]