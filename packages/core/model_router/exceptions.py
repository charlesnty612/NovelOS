"""Model Router 异常类型（Sprint 3）。

设计要点：
- 全部继承自 :class:`ModelRouterError`，便于上层统一捕获并翻译为 HTTP 状态码。
- :class:`ModelNotConfiguredError` 对应 404 / 422——无任何启用配置时告知调用方「该能力未配置模型」。
- :class:`ProviderError` 透传上游 HTTP / 解析错误，附 status_code 与 provider 名便于定位。
"""

from __future__ import annotations


class ModelRouterError(Exception):
    """Model Router 基类异常。"""


class ModelNotConfiguredError(ModelRouterError):
    """指定 capability 在 model_configs 表中无任何启用行。"""

    def __init__(self, capability: str) -> None:
        super().__init__(f"no enabled model configured for capability={capability!r}")
        self.capability = capability


class ProviderError(ModelRouterError):
    """Provider 调用失败（HTTP 非 2xx、网络异常、解析失败等）。"""

    def __init__(self, provider: str, message: str, *, status_code: int | None = None) -> None:
        super().__init__(f"provider {provider!r} failed: {message}")
        self.provider = provider
        self.status_code = status_code


__all__ = ["ModelRouterError", "ModelNotConfiguredError", "ProviderError"]
