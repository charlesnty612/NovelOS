"""Workflow Runtime（Sprint 4-A）。

- :class:`WorkflowEngine` 自研最小 DAG 执行器（节点五类：AI / State / Transform / Human）；
  对齐 PRD §55-60。
- :class:`PauseRequested` — Human 节点用于请求挂起的异常。
- :func:`list_runs` / :func:`get_run` — workflow_runs + workflow_run_nodes 查询辅助。
- 对外只暴露 :class:`WorkflowEngine` 与查询函数；具体业务流通过
  :mod:`packages.core.workflow_registry` 查询并传入 ``start_with_nodes``。
  本包不依赖业务流程包（V1.5 架构债务项已消除反向依赖）。
"""

from .engine import PauseRequested, WorkflowEngine, WorkflowNode
from .runs import get_run, list_runs

__all__ = ["WorkflowEngine", "WorkflowNode", "PauseRequested", "list_runs", "get_run"]
