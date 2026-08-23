"""Agent Runtime 包（Sprint 3）。

公共 API：
- :func:`run_agent` ——执行一次 agent 调用（含 prompt 加载、provider 调用、JSON 提取、
  契约校验、重试 1 次、ai_call_logs 落库）。
- :func:`create_adhoc_run` ——为手工触发 / 测试准备一条 workflow_runs 行。
- :class:`PromptRegistry` ——``docs/agents/prompts`` → agents/prompts 表的同步与查询。
- :func:`extract_json` / :func:`validate_contract` ——结构化输出提取 + 契约校验。
- 异常：:class:`AgentRuntimeError` / :class:`AgentOutputError` / :class:`PromptNotFoundError`。

依赖：
- :mod:`packages.core.model_router` ——Provider 实现 + capability 路由。
- :mod:`packages.core.db` / :mod:`packages.core.ids` ——连接管理 + ID/时间工具。

不做：
- Workflow 编排（属 :mod:`packages.core.workflow_runtime`）。
- State Delta 提交（属 :mod:`packages.core.story_state`）；Runner 仅产出 agent 输出。
"""

from __future__ import annotations

from .exceptions import AgentOutputError, AgentRuntimeError, PromptNotFoundError
from .prompts import PromptRegistry
from .runner import create_adhoc_run, run_agent
from .structured_output import (
    OBSERVER_ALLOWED_KEYS,
    OBSERVER_FORBIDDEN_KEYS,
    extract_json,
    strip_code_fence,
    strip_observer_violations,
    validate_contract,
)

__all__ = [
    "run_agent",
    "create_adhoc_run",
    "PromptRegistry",
    "extract_json",
    "strip_code_fence",
    "validate_contract",
    "strip_observer_violations",
    "OBSERVER_ALLOWED_KEYS",
    "OBSERVER_FORBIDDEN_KEYS",
    "AgentRuntimeError",
    "AgentOutputError",
    "PromptNotFoundError",
]
