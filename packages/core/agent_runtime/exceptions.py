"""Agent Runtime 异常类型（Sprint 3）。

设计要点：
- :class:`AgentRuntimeError` 作为基类，便于上层（router）统一捕获并翻译 HTTP 状态码。
- :class:`AgentOutputError` ——LLM 输出无法解析 JSON 或不满足契约（schema / 元信息缺席 / 必填字段缺失）。
  runner 内部已自动重试 1 次；抛出此异常 = 已超过重试预算，调用方应映射为 502/500。
- :class:`PromptNotFoundError` ——PromptRegistry 找不到指定 agent 的 ACTIVE 版本。
"""

from __future__ import annotations


class AgentRuntimeError(Exception):
    """Agent Runtime 基类异常。"""


class AgentOutputError(AgentRuntimeError):
    """LLM 输出经 1 次重试后仍不合规（JSON 解析失败或契约校验失败）。"""

    def __init__(self, message: str, *, raw_output: str | None = None) -> None:
        super().__init__(message)
        self.raw_output = raw_output


class PromptNotFoundError(AgentRuntimeError):
    """PromptRegistry 找不到指定 agent 的 ACTIVE 版本。"""

    def __init__(self, agent_name: str) -> None:
        super().__init__(f"no ACTIVE prompt registered for agent {agent_name!r}")
        self.agent_name = agent_name


__all__ = ["AgentRuntimeError", "AgentOutputError", "PromptNotFoundError"]
