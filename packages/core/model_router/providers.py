"""LLM Provider 适配器（Sprint 3 + Sprint 8）。

提供 Provider：
- :class:`MockProvider`：测试 / 冒烟用，按 ``scripted`` 列表顺序返回响应；耗尽则重复最后一条。
- :class:`OpenAICompatibleProvider`：兼容 OpenAI Chat Completions 接口（OpenAI / DeepSeek /
  通义 / Ollama / 任何 OpenAI 兼容服务）的同步调用器，使用项目内已有的 httpx。
- :class:`AnthropicProvider`（Sprint 8）：Anthropic 原生 Messages API。
- :class:`OllamaProvider`（Sprint 8）：Ollama 本地 ``/api/chat`` 端点；无需 api_key。

设计要点：
- ``Provider.complete`` 接受 ``messages: list[dict]`` 与 ``params: dict``，返回
  ``{"text": str, "usage": {"prompt": int, "completion": int, "total": int}}``。
  与 ``docs/impl/IMPLEMENTATION-PLAN-v0.md`` D-I4 的 Completion 契约保持一致。
- OpenAI 兼容 / Anthropic / Ollama Provider 使用 ``httpx.post`` 同步客户端（``timeout=60s``），
  失败抛 :class:`ProviderError`；status_code 透传便于上层映射。
- 不做流式、不做重试：重试与降级由 :class:`packages.core.model_router.ModelRouter`
  的 ``call_with_fallback`` 负责（agent-contracts §6 重试原则）。
- :meth:`Provider.health_check` 返回 ``{"ok": bool, "status_code": int|None,
  "detail": str, "latency_ms": int}``，用于 ``/model-configs/{id}/test`` 健康检查端点。
"""

from __future__ import annotations

import os
import time
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

    def health_check(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Mock provider 永远 ``ok=True``（任务书口径：mock provider 保持现状语义）。"""
        _ = timeout  # noqa: F841 —— 统一签名，不使用
        return {
            "ok": True,
            "status_code": 200,
            "detail": "mock provider always healthy",
            "latency_ms": 0,
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

    def health_check(self, *, timeout: float | None = None) -> dict[str, Any]:
        """OpenAI 兼容健康检查：``GET {base_url}/models``（按 OpenAI 文档约定）。

        任何 2xx → ok=True；其它或网络异常 → ok=False。timeout 单位秒。
        """
        url = f"{self.base_url}/models"
        client = self._ensure_client()
        start = time.monotonic()
        try:
            resp = client.get(url, headers=self._headers(), timeout=timeout or self.timeout)
        except httpx.HTTPError as exc:
            latency = int((time.monotonic() - start) * 1000)
            return {
                "ok": False,
                "status_code": None,
                "detail": f"network error: {exc}",
                "latency_ms": latency,
            }
        latency = int((time.monotonic() - start) * 1000)
        if resp.status_code < 400:
            return {
                "ok": True,
                "status_code": resp.status_code,
                "detail": "reachable",
                "latency_ms": latency,
            }
        snippet = (resp.text or "")[:200]
        return {
            "ok": False,
            "status_code": resp.status_code,
            "detail": f"HTTP {resp.status_code}: {snippet}",
            "latency_ms": latency,
        }


# ---------------------------------------------------------------------------
# Anthropic Native Provider (Sprint 8)
# ---------------------------------------------------------------------------


class AnthropicProvider:
    """Anthropic 原生 Messages API（``POST /v1/messages``）。

    与 OpenAI 兼容的差异：
    - system 消息走顶层 ``system`` 字段，不放进 ``messages`` 数组；
    - 鉴权用 ``x-api-key`` + ``anthropic-version`` 头；
    - 必填 ``max_tokens``（缺省 4096，可在 params.max_tokens 覆盖）；
    - 响应 ``content`` 是 block 列表，文本取 ``content[0].text``；
    - usage 字段名为 ``input_tokens`` / ``output_tokens``。
    """

    name = "anthropic"
    DEFAULT_BASE_URL = "https://api.anthropic.com"
    DEFAULT_MAX_TOKENS = 4096
    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        *,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not model:
            raise ValueError("model must be a non-empty string")
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = client

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "anthropic-version": self.ANTHROPIC_VERSION,
        }
        if self.api_key:
            h["x-api-key"] = self.api_key
        return h

    @staticmethod
    def _split_system(messages: Messages) -> tuple[str | None, list[dict[str, Any]]]:
        """把 messages 里的 system 拼到顶层 system 字段；其它原样透传。"""
        system_parts: list[str] = []
        user_msgs: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "system":
                if content:
                    system_parts.append(str(content))
            else:
                user_msgs.append({"role": role or "user", "content": content})
        system_text = "\n\n".join(system_parts) if system_parts else None
        return system_text, user_msgs

    def complete(self, messages: Messages, params: dict | None = None) -> CompletionResult:
        system_text, user_msgs = self._split_system(messages)
        # Anthropic 要求至少一条 user 消息；空就补一个 " " 占位
        if not user_msgs:
            user_msgs = [{"role": "user", "content": " "}]

        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.DEFAULT_MAX_TOKENS,
            "messages": user_msgs,
        }
        if system_text:
            body["system"] = system_text
        if params:
            # params 透传 temperature / top_p / max_tokens 等；
            # max_tokens 优先用 params 里的（与 OpenAI 兼容 Provider 保持一致语义）。
            body.update({k: v for k, v in params.items() if v is not None})

        url = f"{self.base_url}/v1/messages"
        client = self._ensure_client()
        try:
            resp = client.post(url, json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"network error: {exc}") from exc

        if resp.status_code >= 400:
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
            text = data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(self.name, f"malformed response: {exc}") from exc

        usage_raw = data.get("usage") or {}
        try:
            prompt_tokens = int(usage_raw.get("input_tokens") or 0)
            completion_tokens = int(usage_raw.get("output_tokens") or 0)
            total_tokens = prompt_tokens + completion_tokens
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

    def health_check(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Anthropic 无公开 ping：发一条 ``max_tokens=1`` 的极小 messages 请求。

        200 / 401（鉴权失败但 endpoint 通） → ``ok=True``；其它 4xx/5xx 或网络异常 → ``ok=False``。
        """
        body = {
            "model": self.model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": " "}],
        }
        url = f"{self.base_url}/v1/messages"
        client = self._ensure_client()
        start = time.monotonic()
        try:
            resp = client.post(
                url, json=body, headers=self._headers(), timeout=timeout or self.timeout
            )
        except httpx.HTTPError as exc:
            latency = int((time.monotonic() - start) * 1000)
            return {
                "ok": False,
                "status_code": None,
                "detail": f"network error: {exc}",
                "latency_ms": latency,
            }
        latency = int((time.monotonic() - start) * 1000)
        if resp.status_code in (200, 401):
            detail = "reachable"
            if resp.status_code == 401:
                detail = "reachable (401 auth failed, but endpoint reachable)"
            return {"ok": True, "status_code": resp.status_code, "detail": detail, "latency_ms": latency}
        snippet = (resp.text or "")[:200]
        return {
            "ok": False,
            "status_code": resp.status_code,
            "detail": f"HTTP {resp.status_code}: {snippet}",
            "latency_ms": latency,
        }


# ---------------------------------------------------------------------------
# Ollama Provider (Sprint 8)
# ---------------------------------------------------------------------------


class OllamaProvider:
    """Ollama 本地 ``/api/chat`` 同步调用器（无需 api_key）。

    请求体透传 ``options``（temperature / top_p / seed 等），响应取 ``message.content``。
    """

    name = "ollama"
    DEFAULT_BASE_URL = "http://127.0.0.1:11434"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        *,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not model:
            raise ValueError("model must be a non-empty string")
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = client

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def complete(self, messages: Messages, params: dict | None = None) -> CompletionResult:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "stream": False,
        }
        if params:
            # Ollama 把 sampling 参数放在 options 子对象里；其它键（如 format）放顶层。
            opts: dict[str, Any] = {}
            passthrough_keys = ("format",)
            for k, v in params.items():
                if k in passthrough_keys:
                    body[k] = v
                else:
                    opts[k] = v
            if opts:
                body["options"] = opts
        url = f"{self.base_url}/api/chat"
        client = self._ensure_client()
        try:
            resp = client.post(url, json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"network error: {exc}") from exc

        if resp.status_code >= 400:
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
            text = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ProviderError(self.name, f"malformed response: {exc}") from exc

        # Ollama usage：prompt_eval_count / eval_count
        prompt_tokens = int(data.get("prompt_eval_count") or 0)
        completion_tokens = int(data.get("eval_count") or 0)
        return {
            "text": text or "",
            "usage": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": prompt_tokens + completion_tokens,
            },
        }

    def health_check(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Ollama ``GET /api/tags`` —— 列出本地模型，作为可达性探针。"""
        url = f"{self.base_url}/api/tags"
        client = self._ensure_client()
        start = time.monotonic()
        try:
            resp = client.get(url, headers=self._headers(), timeout=timeout or self.timeout)
        except httpx.HTTPError as exc:
            latency = int((time.monotonic() - start) * 1000)
            return {
                "ok": False,
                "status_code": None,
                "detail": f"network error: {exc}",
                "latency_ms": latency,
            }
        latency = int((time.monotonic() - start) * 1000)
        if resp.status_code < 400:
            return {
                "ok": True,
                "status_code": resp.status_code,
                "detail": "reachable",
                "latency_ms": latency,
            }
        snippet = (resp.text or "")[:200]
        return {
            "ok": False,
            "status_code": resp.status_code,
            "detail": f"HTTP {resp.status_code}: {snippet}",
            "latency_ms": latency,
        }


# ---------------------------------------------------------------------------
# OpenAI-Compatible Provider（继续保留；以下为 health_check 追加）
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
    "AnthropicProvider",
    "OllamaProvider",
    "resolve_api_key",
    "Messages",
    "CompletionResult",
    "Scripted",
]
