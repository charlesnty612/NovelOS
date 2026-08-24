"""Model Router 路由层（Sprint 3 + Sprint 8）。

职责：
- 按 capability 查询 ``model_configs`` 表，命中第一条 ``enabled=1`` 行。
- 按 ``provider`` 字段分发到 :class:`MockProvider` / :class:`OpenAICompatibleProvider` /
  :class:`AnthropicProvider` / :class:`OllamaProvider`。
- 失败转移链：:meth:`ModelRouter.call_with_fallback` 按 rowid 顺序逐个尝试，全失败抛
  :class:`AggregateProviderError`。

设计要点：
- 单一入口 :class:`ModelRouter`；构造接收 ``db_path``，每个方法内部开连接、try/finally 关闭。
- 能力映射（capability）与 agent 名口径固定，对齐 ``docs/agents/agent-contracts-v0.md`` §7 十问：
  - director / observer → ``reasoning``
  - writer → ``creative_writing``
- ``resolve(capability)`` 无命中 → :class:`ModelNotConfiguredError`，detail 注明 capability。
- ``get_provider(config_row)`` 按 provider 字段工厂化；mock 不传脚本（每次回显空 JSON），
  实际测试 / 手工触发时由 ``runner`` 注入 ``mock_script`` 直接构造 MockProvider，
  此处的 mock 仅作为「最小兜底」。
- Anthropic / Ollama 的 ``base_url`` 可省略（分别用官方默认 / 本地默认），其余 provider
  仍要求 ``params_json.base_url`` 必填（沿用 Sprint 3 行为）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.core.db import get_connection
from packages.core.logging_config import get_logger

from .exceptions import (
    AggregateProviderError,
    ModelNotConfiguredError,
    ProviderError,
)
from .providers import (
    AnthropicProvider,
    MockProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    resolve_api_key,
)

log = get_logger("novelos.model_router")

# ---------------------------------------------------------------------------
# Agent → Capability 映射
# ---------------------------------------------------------------------------

AGENT_CAPABILITY: dict[str, str] = {
    "director": "reasoning",
    "observer": "reasoning",
    "writer": "creative_writing",
    "arbiter": "reasoning",
    "deconstructor_chapter": "reasoning",
    "deconstructor_aggregate": "reasoning",
    "summarizer": "reasoning",  # Sprint 14-A 章节摘要链
    "critic": "reasoning",      # V1.3 LLM 评审员
}
"""Agent 名 → capability 名（对齐 agent-contracts §7）。

不在此映射的 agent（critic / planner / integrator 等）
默认走 ``reasoning``——Sprint 3 MVP 仅在 README 中声明，不强约束。
"""


def capability_for(agent_name: str) -> str:
    """按 agent 名取 capability；未知 agent 默认 ``reasoning``。"""
    return AGENT_CAPABILITY.get(agent_name, "reasoning")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class ModelRouter:
    """按 capability 解析启用的 ``model_configs`` 行并构造 Provider。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # -------------------------------------------------------------- resolve
    def resolve(self, capability: str) -> dict[str, Any]:
        """返回 ``model_configs`` 中 capability 匹配且 enabled=1 的第一行（按 rowid 升序）。

        无命中 → 抛 :class:`ModelNotConfiguredError`。
        返回 dict 与数据库行字段一致：``config_id / capability / provider / model /
        params_json / enabled``（params_json 字段以**字符串原样**返回，调用方按需 json.loads）。
        """
        if not capability:
            raise ModelNotConfiguredError(capability or "")
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT config_id, capability, provider, model, params_json, enabled
                FROM model_configs
                WHERE capability = ? AND enabled = 1
                ORDER BY rowid ASC
                LIMIT 1
                """,
                (capability,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ModelNotConfiguredError(capability)
        return dict(row)

    # -------------------------------------------------------------- list_enabled
    def list_enabled(self, capability: str) -> list[dict[str, Any]]:
        """返回 ``model_configs`` 中 capability 匹配且 enabled=1 的全部行（按 rowid 升序）。

        无命中 → 返回空列表（不抛）。用于 ``call_with_fallback`` 的候选链。
        """
        if not capability:
            return []
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT config_id, capability, provider, model, params_json, enabled
                FROM model_configs
                WHERE capability = ? AND enabled = 1
                ORDER BY rowid ASC
                """,
                (capability,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- get_provider
    def get_provider(
        self,
        config_row: dict[str, Any],
        *,
        scripted: Any | None = None,
    ):
        """按 ``config_row['provider']`` 字段分发构造 Provider。

        - ``"mock"`` → :class:`MockProvider`（``scripted`` 透传；默认 None，回显空 JSON）。
        - ``"anthropic"`` → :class:`AnthropicProvider`（``base_url`` 可省，默认官方）。
        - ``"ollama"`` → :class:`OllamaProvider`（``base_url`` 可省，默认本地）。
        - 其它（``openai`` / ``openai_compatible`` / ``deepseek`` 等）→
          :class:`OpenAICompatibleProvider`，``base_url`` 来自 ``params_json.base_url``，
          ``api_key`` 通过 :func:`resolve_api_key` 解析。
        """
        provider_name = (config_row.get("provider") or "").strip().lower()
        if provider_name == "mock":
            return MockProvider(scripted=scripted)

        # params_json 可能是字符串（DB 原始）或 dict（get_provider 调用方预解析）
        params_raw = config_row.get("params_json") or "{}"
        if isinstance(params_raw, dict):
            params = params_raw
        else:
            try:
                params = json.loads(params_raw)
            except (TypeError, ValueError):
                params = {}

        if provider_name == "anthropic":
            api_key = resolve_api_key(provider_name, params)
            return AnthropicProvider(
                base_url=params.get("base_url") or None,
                api_key=api_key,
                model=config_row["model"],
            )

        if provider_name == "ollama":
            return OllamaProvider(
                base_url=params.get("base_url") or None,
                model=config_row["model"],
            )

        # 其它 → OpenAI 兼容（OpenAI / DeepSeek / 通义 等）
        base_url = params.get("base_url")
        if not base_url:
            raise ValueError(
                f"provider {provider_name!r} requires params_json.base_url "
                f"(config_id={config_row.get('config_id')})"
            )
        api_key = resolve_api_key(provider_name, params)
        timeout = params.get("timeout_s", 240.0)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("params_json.timeout_s must be a positive number of seconds")
        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=api_key,
            model=config_row["model"],
            timeout=timeout,
        )

    # -------------------------------------------------------------- call_with_fallback
    def call_with_fallback(
        self,
        capability: str,
        messages: list[dict[str, Any]],
        *,
        params: dict[str, Any] | None = None,
        scripted: Any | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """失败转移：按 rowid 顺序逐个尝试该 capability 的 enabled configs。

        返回 ``(completion, used_config_row)``；任一 ProviderError 或网络异常则记 warn 并试下一个。
        全部失败抛 :class:`AggregateProviderError`，携带各次错误的 ``(config_id, error_repr)``。
        无任何候选 → 抛 :class:`ModelNotConfiguredError`（与 ``resolve`` 行为一致）。

        注：单配置时与 :meth:`resolve` + :meth:`get_provider` 的旧路径行为等价。
        """
        candidates = self.list_enabled(capability)
        if not candidates:
            raise ModelNotConfiguredError(capability)

        attempts: list[tuple[str, str]] = []
        last_exc: Exception | None = None
        for row in candidates:
            cid = row.get("config_id") or "<unknown>"
            try:
                provider = self.get_provider(row, scripted=scripted)
            except Exception as exc:  # noqa: BLE001 —— 构造失败也视为一次尝试失败
                msg = f"construct failed: {exc}"
                log.warning("model_router.fallback.construct_failed", extra={
                    "capability": capability, "config_id": cid, "error": msg,
                })
                attempts.append((cid, msg))
                last_exc = exc
                continue
            try:
                completion = provider.complete(messages, params=params)
                return completion, row
            except ProviderError as exc:
                msg = f"{exc} (status_code={exc.status_code})"
                log.warning("model_router.fallback.provider_error", extra={
                    "capability": capability, "config_id": cid, "error": str(exc),
                    "status_code": exc.status_code,
                })
                attempts.append((cid, msg))
                last_exc = exc
                continue
            except Exception as exc:  # noqa: BLE001 —— 网络/超时等非 ProviderError 也吞
                msg = f"unexpected error: {exc}"
                log.warning("model_router.fallback.unexpected", extra={
                    "capability": capability, "config_id": cid, "error": msg,
                })
                attempts.append((cid, msg))
                last_exc = exc
                continue
        # 全部失败 → 聚合异常
        raise AggregateProviderError(capability, attempts) from last_exc


__all__ = ["ModelRouter", "AGENT_CAPABILITY", "capability_for"]
