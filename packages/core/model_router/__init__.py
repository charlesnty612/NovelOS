"""Model Router 包（Sprint 3 + Sprint 8 + V1.5 架构整理）。

公共 API：
- :class:`ModelRouter` ——按 ``capability`` 解析 ``model_configs`` + 构造 Provider + 失败转移。
- :class:`MockProvider` / :class:`OpenAICompatibleProvider` / :class:`AnthropicProvider` /
  :class:`OllamaProvider` ——Provider 实现（Sprint 8 新增后两个）。
- :func:`resolve_api_key` ——从 params_json 或环境变量取 key。
- :data:`AGENT_CAPABILITY` / :func:`capability_for` ——Agent → capability 映射。
- :class:`ModelConfigService` ——V1.5 起的 ``model_configs`` 表 CRUD（被 router 调用），
  替代路由层直接 SQL。
- 异常：:class:`ModelRouterError` / :class:`ModelNotConfiguredError` / :class:`ProviderError` /
  :class:`AggregateProviderError`。

设计要点：
- 测试不依赖外网；所有 Provider 接受 ``httpx.MockTransport`` 注入。
- Sprint 8 补 :class:`AnthropicProvider`（原生 Messages API）与 :class:`OllamaProvider`
  （本地 ``/api/chat``）；同时新增失败转移 :meth:`ModelRouter.call_with_fallback` 与健康检查
  ``health_check()``。
"""

from __future__ import annotations

from .configs import ModelConfigService
from .exceptions import (
    AggregateProviderError,
    ModelNotConfiguredError,
    ModelRouterError,
    ProviderError,
)
from .providers import (
    AnthropicProvider,
    MockProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    resolve_api_key,
)
from .router import AGENT_CAPABILITY, ModelRouter, capability_for

__all__ = [
    "ModelRouter",
    "ModelConfigService",
    "MockProvider",
    "OpenAICompatibleProvider",
    "AnthropicProvider",
    "OllamaProvider",
    "resolve_api_key",
    "AGENT_CAPABILITY",
    "capability_for",
    "ModelRouterError",
    "ModelNotConfiguredError",
    "ProviderError",
    "AggregateProviderError",
]
