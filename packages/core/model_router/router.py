"""Model Router 路由层（Sprint 3）。

职责：
- 按 capability 查询 ``model_configs`` 表，命中第一条 ``enabled=1`` 行。
- 按 ``provider`` 字段分发到 :class:`MockProvider` / :class:`OpenAICompatibleProvider`。

设计要点：
- 单一入口 :class:`ModelRouter`；构造接收 ``db_path``，每个方法内部开连接、try/finally 关闭。
- 能力映射（capability）与 agent 名口径固定，对齐 ``docs/agents/agent-contracts-v0.md`` §7 十问：
  - director / observer → ``reasoning``
  - writer → ``creative_writing``
- ``resolve(capability)`` 无命中 → :class:`ModelNotConfiguredError`，detail 注明 capability。
- ``get_provider(config_row)`` 按 provider 字段工厂化；mock 不传脚本（每次回显空 JSON），
  实际测试 / 手工触发时由 ``runner`` 注入 ``mock_script`` 直接构造 MockProvider，
  此处的 mock 仅作为「最小兜底」。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.core.db import get_connection

from .exceptions import ModelNotConfiguredError
from .providers import (
    MockProvider,
    OpenAICompatibleProvider,
    resolve_api_key,
)

# ---------------------------------------------------------------------------
# Agent → Capability 映射
# ---------------------------------------------------------------------------

AGENT_CAPABILITY: dict[str, str] = {
    "director": "reasoning",
    "observer": "reasoning",
    "writer": "creative_writing",
}
"""Agent 名 → capability 名（对齐 agent-contracts §7）。

不在此映射的 agent（arbiter / deconstructor / critic / planner / integrator 等）
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

    # -------------------------------------------------------------- get_provider
    def get_provider(
        self,
        config_row: dict[str, Any],
        *,
        scripted: Any | None = None,
    ):
        """按 ``config_row['provider']`` 字段分发构造 Provider。

        - ``"mock"`` → :class:`MockProvider`（``scripted`` 透传；默认 None，回显空 JSON）。
        - 其它（``openai`` / ``openai_compatible`` / ``ollama`` / ``deepseek`` 等）→
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

        base_url = params.get("base_url")
        if not base_url:
            raise ValueError(
                f"provider {provider_name!r} requires params_json.base_url "
                f"(config_id={config_row.get('config_id')})"
            )
        api_key = resolve_api_key(provider_name, params)
        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=api_key,
            model=config_row["model"],
        )


__all__ = ["ModelRouter", "AGENT_CAPABILITY", "capability_for"]
