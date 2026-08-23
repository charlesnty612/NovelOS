"""Model Router 包（Sprint 3）。

公共 API：
- :class:`ModelRouter` ——按 ``capability`` 解析 ``model_configs`` + 构造 Provider。
- :class:`MockProvider` / :class:`OpenAICompatibleProvider` ——Provider 实现。
- :func:`resolve_api_key` ——从 params_json 或环境变量取 key。
- :data:`AGENT_CAPABILITY` / :func:`capability_for` ——Agent → capability 映射。
- 异常：:class:`ModelRouterError` / :class:`ModelNotConfiguredError` / :class:`ProviderError`。

设计要点：
- 测试不依赖外网；OpenAI 兼容 Provider 接受 ``httpx.MockTransport`` 注入。
- Sprint 8 计划补 :class:`AnthropicProvider` 与本地 :class:`OllamaProvider`，
  本 Sprint 不实现。
"""

from __future__ import annotations

from .exceptions import (
    ModelNotConfiguredError,
    ModelRouterError,
    ProviderError,
)
from .providers import (
    MockProvider,
    OpenAICompatibleProvider,
    resolve_api_key,
)
from .router import AGENT_CAPABILITY, ModelRouter, capability_for

__all__ = [
    "ModelRouter",
    "MockProvider",
    "OpenAICompatibleProvider",
    "resolve_api_key",
    "AGENT_CAPABILITY",
    "capability_for",
    "ModelRouterError",
    "ModelNotConfiguredError",
    "ProviderError",
]
