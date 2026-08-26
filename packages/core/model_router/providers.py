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
- OpenAI 兼容 Provider 走 SSE 流式（``httpx.client.stream`` + ``iter_lines`` + 解析
  ``data: {...}`` / ``data: [DONE]``），配合 ``time.monotonic()`` 总时长 deadline
  防止长生成在大量 keep-alive chunk 间无限挂起；失败抛 :class:`ProviderError`，
  status_code 透传便于上层映射。``health_check`` 仍走非流式 GET ``/models``。
- Anthropic / Ollama Provider 使用 ``httpx.post`` 同步客户端（``timeout=60s``）。
- 不做重试：重试与降级由 :class:`packages.core.model_router.ModelRouter`
  的 ``call_with_fallback`` 负责（agent-contracts §6 重试原则）。
- :meth:`Provider.health_check` 返回 ``{"ok": bool, "status_code": int|None,
  "detail": str, "latency_ms": int}``，用于 ``/model-configs/{id}/test`` 健康检查端点。
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from .exceptions import ProviderError

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# V3.7 诊断特性：OpenAI 兼容 Provider 原始响应采样落盘（env 门控，默认关）
# ---------------------------------------------------------------------------
# 生产排障专用：当 ``NOVELOS_DEBUG_PROVIDER_DUMP_DIR`` 被设置时，每次成功的 OpenAI
# 兼容 Provider 调用都会把 ``text`` 全文 + usage JSON 写到该目录下一个独立文件，
# 文件名带时间戳与 completion_tokens。默认关闭（env 不设 → 零行为变化）。
# 仅在 ``OpenAICompatibleProvider.complete()`` 成功 return 前调用；失败路径不采样。

_DEBUG_DUMP_ENV = "NOVELOS_DEBUG_PROVIDER_DUMP_DIR"
"""环境变量名：设置后开启原始响应采样落盘；未设置或为空字符串 → 完全 no-op。"""

_DEBUG_DUMP_KEEP = 10
"""同一目录下，仅保留最新 N 个 dump 文件；超出按 mtime ASC 排序删除旧文件。"""

_DEBUG_DUMP_MAX_BYTES = 2 * 1024 * 1024
"""单文件最大字节数；超过则只写入前 N 字节并在尾部追加 ``\\n\\n[TRUNCATED]``。"""


def _dump_debug_response(model: str, usage: dict, text: str) -> None:
    """OpenAI 兼容 Provider 原始响应落盘（诊断特性，仅 env 开启时执行）。

    参数：
    - ``model``：本次调用的模型名（用于文件名）。
    - ``usage``：解析后的 usage dict（OpenAI 标准 usage 字段）。
    - ``text``：累积的助手回复全文。

    文件命名：``{YYYYmmdd_HHMMSS}_{model}_comp{completion_tokens}.txt``。
    文件内容：``text`` 全文 + ``\\n\\n===== USAGE =====\\n`` + ``json.dumps(usage)``。

    异常一律吞掉（不影响主流程），并通过 :mod:`logging` 记录 warning 便于排查。
    """
    dump_dir = os.environ.get(_DEBUG_DUMP_ENV)
    if not dump_dir:
        # 默认路径：零开销，第一时间 return
        return
    try:
        os.makedirs(dump_dir, exist_ok=True)
        completion_tokens = 0
        try:
            completion_tokens = int((usage or {}).get("completion") or 0)
        except (TypeError, ValueError):
            completion_tokens = 0
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe_model = (model or "unknown").replace(os.sep, "_").replace("/", "_")
        filename = f"{ts}_{safe_model}_comp{completion_tokens}.txt"
        target = os.path.join(dump_dir, filename)
        # 单文件体积截断：超过 _DEBUG_DUMP_MAX_BYTES 只写前 N 字节并加尾部标注
        if isinstance(text, str) and len(text.encode("utf-8")) > _DEBUG_DUMP_MAX_BYTES:
            truncated = text.encode("utf-8")[:_DEBUG_DUMP_MAX_BYTES].decode(
                "utf-8", errors="ignore"
            )
            payload = truncated + "\n\n[TRUNCATED]"
        else:
            payload = text if isinstance(text, str) else (text or "")
        usage_json = json.dumps(usage or {}, ensure_ascii=False)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.write("\n\n===== USAGE =====\n")
            fh.write(usage_json)
        # 轮转：仅保留最新 _DEBUG_DUMP_KEEP 个文件（按 mtime ASC 删旧）
        try:
            entries = [
                (os.path.join(dump_dir, name), os.path.getmtime(os.path.join(dump_dir, name)))
                for name in os.listdir(dump_dir)
                if os.path.isfile(os.path.join(dump_dir, name))
            ]
            if len(entries) > _DEBUG_DUMP_KEEP:
                entries.sort(key=lambda item: item[1])  # 旧 → 新
                for old_path, _ in entries[: len(entries) - _DEBUG_DUMP_KEEP]:
                    try:
                        os.remove(old_path)
                    except OSError:
                        # 单文件删除失败不影响其他清理
                        pass
        except OSError:
            # listdir / getmtime 失败不致命，跳过轮转
            pass
    except Exception as exc:  # noqa: BLE001
        # 任何异常（权限/磁盘/编码）都吞掉；不影响主流程
        _LOG.warning("debug dump failed (env=%s): %s", _DEBUG_DUMP_ENV, exc)

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
    - ``MockProvider(scripted=..., usage={"prompt_tokens_details": {"cached_tokens": 123}})``：
      V3.5 cached_tokens 观测——让单测断言 usage 字段透传到 ai_call_logs.token_usage_json。
      ``usage`` 为 ``None`` 或缺省则保持 ``{prompt:0, completion:0, total:0}``。
    """

    name = "mock"

    def __init__(
        self,
        scripted: Scripted | None = None,
        *,
        usage: dict[str, Any] | None = None,
    ) -> None:
        self._scripted = scripted
        # V3.5：cached_tokens 注入槽——仅当显式传入时覆盖默认 usage；None/缺省保
        # 留 ``{prompt:0, completion:0, total:0}``，与既有测试零兼容影响。
        self._forced_usage = usage

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
        if self._forced_usage is not None:
            # V3.5：透传注入的 usage（典型场景：cached_tokens 命中观测）；
            # copy() 防止调用方对同一个 dict 的修改被下一次 complete 看到。
            usage_out = dict(self._forced_usage)
        else:
            usage_out = {"prompt": 0, "completion": 0, "total": 0}
        return {
            "text": text,
            "usage": usage_out,
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
        timeout: float = 240.0,
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
            # 连接池上限：限制 keep-alive 堆积（实测长进程 114 条死连接，新请求被路由到
            # 挂起连接上无限等待——read timeout 是字节间隔语义，对整连接挂起不生效）
            self._client = httpx.Client(
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def complete(self, messages: Messages, params: dict | None = None) -> CompletionResult:
        """OpenAI 兼容 Chat Completions 流式调用（SSE）+ 总时长 deadline。

        关键设计（Sprint V3.6+）：
        - 用 ``client.stream("POST", ...)`` 逐行读 SSE chunk，避免非流式下整连接挂起
          而 httpx read timeout（字节间隔语义）不生效导致客户端无限等待。
        - ``stream=True`` 写入请求体，让服务端持续吐 SSE chunk；任何「整条挂起」都会立即
          触发读超时暴露。
        - ``start = time.monotonic()`` + 循环内手动检查总时长作为「硬顶 deadline」，
          独立于 httpx 的 read timeout（因为 read timeout 在大量 chunk 持续到达时不会
          触发；典型场景：服务端开始吐 chunk 后每 30s 发一个 keep-alive 字节，
          永远不超过 read timeout）。
        - httpx 的 read timeout 仍设为 ``request_timeout``，作为字节间隔兜底。
        """
        body: dict[str, Any] = {"model": self.model, "messages": list(messages), "stream": True}
        # OpenAI 流式协议：不带 include_usage 时流式响应不下发 usage（实测 MiniMax 如此），
        # token 计量会全丢；显式要求服务端在末 chunk 回传 usage。
        body.setdefault("stream_options", {"include_usage": True})
        request_params = dict(params or {})
        request_timeout = request_params.pop("timeout_s", self.timeout)
        if not isinstance(request_timeout, (int, float)) or request_timeout <= 0:
            raise ValueError("params_json.timeout_s must be a positive number of seconds")
        # 剔除本地保留键，避免被透传到上游请求体（脏参数）。
        # 这些是 NovelOS 自身解析用的字段（如 base_url 用于构造 URL、api_key 已走 Authorization 头），
        # 上游 OpenAI 兼容 API 看到会直接报 400。
        for k in ("base_url", "timeout_s", "api_key", "api_key_env"):
            request_params.pop(k, None)
        # 调用方可能传了 stream=False（少数情况下游不支持流式）；强制打开。
        # 同理 stream_options 也可能由调用方误传，统一由本 Provider 注入并剔除。
        request_params.pop("stream", None)
        request_params.pop("stream_options", None)
        if request_params:
            body.update(request_params)
        url = f"{self.base_url}/chat/completions"
        client = self._ensure_client()
        # httpx 字节间隔超时（兜底；总时长硬顶由下面 monotonic 循环控制）
        stream_timeout = httpx.Timeout(request_timeout)

        start = time.monotonic()
        deadline_exceeded = False
        try:
            with client.stream(
                "POST", url, json=body, headers=self._headers(), timeout=stream_timeout
            ) as resp:
                # HTTP 错误要在流式里也早暴露：读出错误体再抛
                if resp.status_code >= 400:
                    # 流式下错误体通常较短；读整个 body（最多 ~200 字符截断）
                    try:
                        err_body = resp.read()
                        snippet = (err_body.decode("utf-8", errors="replace"))[:200]
                    except Exception:
                        snippet = ""
                    # 抛错时 with 块退出会正常关闭流
                    raise ProviderError(
                        self.name,
                        f"HTTP {resp.status_code}: {snippet}",
                        status_code=resp.status_code,
                    )

                text_parts: list[str] = []
                usage_raw: dict[str, Any] = {}
                saw_done = False
                # 逐行 SSE：data: {...}\n\n  /  data: [DONE]\n\n
                for line in resp.iter_lines():
                    # 总时长 deadline 检查：流式下服务端持续吐 chunk 也可能拉得过长
                    # （MiniMax 长生成实测可超 1800s）；read timeout 在 chunk 间隔短时不
                    # 触发，因此必须独立硬顶。
                    if time.monotonic() - start > request_timeout:
                        deadline_exceeded = True
                        raise ProviderError(
                            self.name,
                            f"deadline exceeded: total elapsed {time.monotonic() - start:.2f}s "
                            f"> timeout_s={request_timeout}",
                        )
                    if not line:
                        continue
                    # SSE 行通常以 "data: " 开头；strip 后只剩 payload
                    if line.startswith(":"):
                        # SSE 注释行；忽略
                        continue
                    if not line.startswith("data:"):
                        # event: / id: 等其他 SSE 字段；本 Provider 暂不关注
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        saw_done = True
                        break
                    try:
                        chunk = json.loads(payload)
                    except Exception:
                        # 单个 chunk 解析失败：跳过该 chunk 继续读（SSE 容错）；
                        # 但 [DONE] 前若整个流没拿到任何 content 会在循环后判 empty。
                        continue
                    # usage：OpenAI / MiniMax 流式在最后一个 chunk 给 usage
                    chunk_usage = chunk.get("usage")
                    if isinstance(chunk_usage, dict) and chunk_usage:
                        usage_raw = chunk_usage
                    # content 累加
                    try:
                        delta = chunk["choices"][0]["delta"]
                    except (KeyError, IndexError, TypeError):
                        continue
                    if isinstance(delta, dict):
                        piece = delta.get("content")
                        if isinstance(piece, str) and piece:
                            text_parts.append(piece)
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"network error: {exc}") from exc

        # deadline 超时：ProviderError 已抛；这里只是保险（避免 pylint 等告警）
        if deadline_exceeded:
            raise ProviderError(
                self.name,
                f"deadline exceeded: total elapsed {time.monotonic() - start:.2f}s "
                f"> timeout_s={request_timeout}",
            )

        # 零 content chunk → 空流（无论是否见到 [DONE]），抛错；
        # 防止下游把空串当合法产出。
        if not text_parts:
            raise ProviderError(self.name, "empty stream: no content chunks received")

        text = "".join(text_parts)

        # usage 提取（与原非流式语义一致；流式末 chunk 给 usage，缺省则 0）
        try:
            prompt_tokens = int(usage_raw.get("prompt_tokens") or 0) if usage_raw else 0
            completion_tokens = int(usage_raw.get("completion_tokens") or 0) if usage_raw else 0
            total_tokens = int(
                usage_raw.get("total_tokens") or (prompt_tokens + completion_tokens)
            ) if usage_raw else (prompt_tokens + completion_tokens)
        except (TypeError, ValueError):
            prompt_tokens = completion_tokens = total_tokens = 0

        # V3.5 cached_tokens 观测：从 OpenAI 标准 usage.prompt_tokens_details.cached_tokens
        # 抽取命中前缀缓存的 token 数。这是 MiniMax / OpenAI 自动前缀缓存开启后的
        # 命中率指标——观测目标，不影响计费（OpenAI 不对缓存命中 token 二次收费）。
        # 缺省时不下发该 key，避免后续读端误判 0 为「未观测」。runner 写 ai_call_logs
        # 时整段 usage 透传，token_usage_json 自动得到该字段。零 schema 变更。
        usage_out: dict[str, Any] = {
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "total": total_tokens,
        }
        if usage_raw:
            prompt_details = usage_raw.get("prompt_tokens_details") or {}
            if isinstance(prompt_details, dict):
                cached_tokens_raw = prompt_details.get("cached_tokens")
                try:
                    cached_tokens_int = int(cached_tokens_raw) if cached_tokens_raw is not None else None
                except (TypeError, ValueError):
                    cached_tokens_int = None
                if cached_tokens_int is not None and cached_tokens_int > 0:
                    usage_out["cached_tokens"] = cached_tokens_int

        # V3.7 诊断：env 开启时把原始 text + usage 落盘（仅成功路径；失败路径不采样）
        _dump_debug_response(self.model, usage_out, text)

        return {
            "text": text,
            "usage": usage_out,
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
            # 连接池上限：限制 keep-alive 堆积（实测长进程 114 条死连接，新请求被路由到
            # 挂起连接上无限等待——read timeout 是字节间隔语义，对整连接挂起不生效）
            self._client = httpx.Client(
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
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
            # 连接池上限：限制 keep-alive 堆积（实测长进程 114 条死连接，新请求被路由到
            # 挂起连接上无限等待——read timeout 是字节间隔语义，对整连接挂起不生效）
            self._client = httpx.Client(
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
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
