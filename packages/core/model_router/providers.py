"""LLM Provider 适配器（Sprint 3）。

提供两个 Provider：
- :class:`MockProvider`：测试 / 冒烟用，按 ``scripted`` 列表顺序返回响应；耗尽则重复最后一条。
- :class:`OpenAICompatibleProvider`：兼容 OpenAI Chat Completions 接口（OpenAI / DeepSeek /
  通义 / Ollama / 任何 OpenAI 兼容服务）的同步调用器，使用项目内已有的 httpx。

设计要点：
- ``Provider.complete`` 接受 ``messages: list[dict]`` 与 ``params: dict``，返回
  ``{"text": str, "usage": {"prompt": int, "completion": int, "total": int}}``。
  与 ``docs/impl/IMPLEMENTATION-PLAN-v0.md`` D-I4 的 Completion 契约保持一致。
- OpenAI 兼容 Provider 使用 ``httpx.post`` 同步客户端（``timeout=60s``），失败抛
  :class:`ProviderError`；status_code 透传便于上层映射。
- 不做流式、不做重试：重试与降级由 ``agent_runtime.runner`` 负责（agent-contracts §6 重试原则）。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from .exceptions import ProviderError

# ---------------------------------------------------------------------------
# 类型别名
# ---------------------------------------------------------------------------

Messages = list[dict[str, Any]]
"""Chat messages 格式：每条 ``{"role": "system"|"user"|"assistant", "content": str}``。"""

CompletionResult = dict[str, Any]
"""Provider 输出：``{"text": str, "usage": {"prompt": int, "completion": int, "total": int}}``。"""

Scripted = Sequence[str] | Callable[[int], str]
"""MockProvider 脚本来源：固定列表（耗尽则重复末条）或按调用次数返回响应的 callable。"""


# ---------------------------------------------------------------------------
# Mock Provider
# ---------------------------------------------------------------------------


class MockProvider:
    """测试用 Provider：按脚本顺序或 callable 返回响应。

    使用方式：
    - ``MockProvider(scripted=["hello"])``：第一次返回 ``"hello"``，后续永远返回 ``"hello"``。
    - ``MockProvider(scripted=lambda i: f"reply {i}")``：第 i 次调用返回对应响应。
    - ``MockProvider()``：无脚本，回显空 JSON（``"{}"``），便于断言「未提供脚本时不影响 runner」。
    """

    name = "mock"

    def __init__(self, scripted: Scripted | None = None) -> None:
        self._scripted = scripted

    def complete(self, messages: Messages, params: dict | None = None) -> CompletionResult:
        """返回脚本响应。``messages`` / ``params`` 仅用于可观测性，不做解析。"""
        # 调用次数计数（callable 模式需要）；列表模式不计数（永远重复末条）。
        if callable(self._scripted):
            # callable 需要单调递增的 i；用对象属性维护
            idx = getattr(self, "_call_count", 0)
            text = self._scripted(idx)
            self._call_count = idx + 1
        elif self._scripted is None or len(self._scripted) == 0:
            # 无脚本：回显空 JSON（任务书口径），runner 会因此次次抛 AgentOutputError
            text = "{}"
        else:
            # 列表：耗尽后重复最后一条
            idx = getattr(self, "_call_count", 0)
            if idx < len(self._scripted):
                text = self._scripted[idx]
            else:
                text = self._scripted[-1]
            self._call_count = idx + 1
        return {
            "text": text,
            "usage": {"prompt": 0, "completion": 0, "total": 0},
        }


# ---------------------------------------------------------------------------
# OpenAI-Compatible Provider
# ---------------------------------------------------------------------------


class OpenAICompatibleProvider:
    """OpenAI 兼容 Chat Completions 适配器（OpenAI / DeepSeek / 通义 / Ollama 等）。"""

    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        *,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url must be a non-empty string")
        if not model:
            raise ValueError("model must be a non-empty string")
        # 去掉尾部斜杠，避免拼成 base_url//chat/completions
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        # 允许测试注入 httpx.MockTransport（task书口径）；None 时构造新 client
        self._client = client

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def complete(self, messages: Messages, params: dict | None = None) -> CompletionResult:
        body: dict[str, Any] = {"model": self.model, "messages": list(messages)}
        if params:
            # params 透传 temperature / top_p / seed / max_tokens 等
            body.update(params)
        url = f"{self.base_url}/chat/completions"
        client = self._ensure_client()
        try:
            resp = client.post(url, json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"network error: {exc}") from exc

        if resp.status_code >= 400:
            # 截断 body 避免日志爆炸
            snippet = (resp.text or "")[:200]
            raise ProviderError(
                self.name,
                f"HTTP {resp.status_code}: {snippet}",
                status_code=resp.status_code,
            )

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self.name, f"invalid JSON response: {exc}") from exc

        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(self.name, f"malformed response: {exc}") from exc

        # usage 可选；存在则取，否则默认 0
        usage_raw = data.get("usage") or {}
        try:
            prompt_tokens = int(usage_raw.get("prompt_tokens") or 0)
            completion_tokens = int(usage_raw.get("completion_tokens") or 0)
            total_tokens = int(usage_raw.get("total_tokens") or (prompt_tokens + completion_tokens))
        except (TypeError, ValueError):
            prompt_tokens = completion_tokens = total_tokens = 0

        return {
            "text": text or "",
            "usage": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": total_tokens,
            },
        }


# ---------------------------------------------------------------------------
# 便捷工具：env 解析
# ---------------------------------------------------------------------------


def resolve_api_key(provider: str, params_json: dict | None) -> str | None:
    """按任务书口径解析 API Key：

    1. 优先 ``params_json["api_key"]``（明文存于 DB，仅 MVP 演示用，不推荐生产）。
    2. 否则读环境变量 ``NOVELOS_API_KEY_<PROVIDER大写>``。
    3. 都没有 → 返回 None（Mock Provider 不需要 key；OpenAI 兼容 Provider 若无 key
       通常仍可访问本地 / Ollama 服务，由 Provider 自行处理是否 401）。
    """
    if params_json:
        key = params_json.get("api_key")
        if isinstance(key, str) and key:
            return key
    env_name = f"NOVELOS_API_KEY_{provider.upper()}"
    val = os.environ.get(env_name)
    if val:
        return val
    return None


__all__ = [
    "MockProvider",
    "OpenAICompatibleProvider",
    "resolve_api_key",
    "Messages",
    "CompletionResult",
    "Scripted",
]
