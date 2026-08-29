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
    "summarizer": "light",      # V3 P0-2：结构化提取走轻量模型
    "critic": "light",          # V3 P0-2：LLM 评审员走轻量模型
    "scene_planner": "reasoning",  # P0：Director plan → Scene plan 结构翻译
    # V3.7 project-init 四个 agent 显式映射：避免 ``capability_for`` 默认回退
    # reasoning——把「题材定位 / 世界观 / 角色设计 / 卷纲」与正文写作分离，
    # 前端可独立分配更便宜的小模型。
    "premise_designer": "premise_design",
    "world_builder": "world_building",
    "character_designer": "character_design",
    "volume_outliner": "volume_outline",
}
"""Agent 名 → capability 名（对齐 agent-contracts §7）。

不在此映射的 agent（critic / planner / integrator 等）
默认走 ``reasoning``——Sprint 3 MVP 仅在 README 中声明，不强约束。

V3 P0-2 新增 ``light`` capability：用于结构化提取 / 评审类任务（critic / summarizer），
可配更便宜更快的模型（DeepSeek-chat / GLM-flash 级）。observer 保持 ``reasoning``
——observer 输出准确性直接影响 story_state，downgrade 风险高；writer 保持
``creative_writing`` 不变（长文本生成任务）。``light`` capability 在未配置任何
enabled 行时自动回退到 ``reasoning`` 链（见 :meth:`ModelRouter.call_with_fallback`），
保证零破坏。

V3.7 project-init 四 agent（premise_designer / world_builder / character_designer /
volume_outliner）映射到独立 capability（premise_design / world_building /
character_design / volume_outline），与正文创作（creative_writing）/ 推理规划
（reasoning）/ 轻量评审（light）解耦。
"""


def capability_for(agent_name: str) -> str:
    """按 agent 名取 capability；未知 agent 默认 ``reasoning``。"""
    return AGENT_CAPABILITY.get(agent_name, "reasoning")


# ---------------------------------------------------------------------------
# V3.7：Capability 标签 + agents 归属（前端 / GET bindings 展示）
# ---------------------------------------------------------------------------

CAPABILITY_LABELS: dict[str, dict[str, object]] = {
    # 顺序即前端「环节」展示顺序；先 project-init（项目初始化四步），
    # 再正文写作，再推理规划与轻量评审。
    "premise_design":   {"label": "题材定位",   "agents": ["premise_designer"]},
    "world_building":   {"label": "世界观",     "agents": ["world_builder"]},
    "character_design": {"label": "角色设计",   "agents": ["character_designer"]},
    "volume_outline":   {"label": "卷纲",       "agents": ["volume_outliner"]},
    "creative_writing": {"label": "正文写作",   "agents": ["writer"]},
    "reasoning":        {"label": "推理规划",   "agents": [
        "director", "observer", "arbiter",
        "deconstructor_chapter", "deconstructor_aggregate", "scene_planner",
    ]},
    "light":            {"label": "轻量评审",   "agents": ["summarizer", "critic"]},
}
"""环节元信息（有序 dict，前端 / GET bindings 用）。

- ``label``：人类可读中文名（前端展示）；
- ``agents``：归属该 capability 的 agent 名列表（从 :data:`AGENT_CAPABILITY` 反推）。
"""


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class ModelRouter:
    """按 capability 解析启用的 ``model_configs`` 行并构造 Provider。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # -------------------------------------------------------------- _candidates
    def _candidates(self, capability: str) -> list[dict[str, Any]]:
        """统一候选来源（V3.7）：优先 ``capability_bindings + model_profiles``，无 binding 回落 ``model_configs``。

        返回字典键名与 model_configs 行完全一致（``config_id / capability / provider /
        model / params_json / enabled``），其中 ``config_id`` 取 ``profile_id``、
        ``capability`` 取入参 ``capability``，其它字段从 model_profiles 行映射。
        保证 :meth:`get_provider` / ``runner`` 写 ai_call_logs 不需要 schema 改动。

        优先级：
        1. ``capability_bindings`` 有该 capability → 取 ``profile_ids`` JSON 数组，
           顺序即 fallback 序；逐个解析 profile_id，在 ``model_profiles`` 里取
           ``enabled=1`` 的行；缺失或 ``enabled=0`` 跳过。
        2. 无 binding → 直接查 ``model_configs`` 中 ``capability`` 匹配 ``enabled=1``
           的全部行（按 rowid ASC），与 V3.6 旧行为等价。
        """
        if not capability:
            return []

        conn = get_connection(self.db_path)
        try:
            binding_row = conn.execute(
                "SELECT profile_ids FROM capability_bindings WHERE capability = ?",
                (capability,),
            ).fetchone()
        finally:
            conn.close()

        if binding_row is not None:
            try:
                raw = json.loads(binding_row["profile_ids"] or "[]")
            except (TypeError, ValueError):
                raw = []
            profile_ids = [str(x) for x in raw if x]
            if not profile_ids:
                return []
            # 保留 binding 顺序：逐个查；missing/enabled=0 跳过
            conn = get_connection(self.db_path)
            try:
                out: list[dict[str, Any]] = []
                for pid in profile_ids:
                    row = conn.execute(
                        """
                        SELECT profile_id, provider, model, params_json, enabled
                        FROM model_profiles
                        WHERE profile_id = ? AND enabled = 1
                        """,
                        (pid,),
                    ).fetchone()
                    if row is None:
                        continue
                    out.append({
                        "config_id": row["profile_id"],
                        "capability": capability,
                        "provider": row["provider"],
                        "model": row["model"],
                        "params_json": row["params_json"],
                        "enabled": row["enabled"],
                    })
            finally:
                conn.close()
            return out

        # 无 binding → 回落 model_configs（旧行为）
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

    # -------------------------------------------------------------- resolve
    def resolve(self, capability: str) -> dict[str, Any]:
        """返回该 capability 候选链的第一个（bindings 命中走 bindings，否则走 model_configs）。

        无命中 → 抛 :class:`ModelNotConfiguredError`（capability 字段保持原始字符串；
        消息体补充「环节 X 未绑定档案且无历史配置」便于运维定位）。
        返回 dict 字段对齐 :meth:`_candidates` 的契约。
        """
        if not capability:
            raise ModelNotConfiguredError(capability or "")
        rows = self._candidates(capability)
        if not rows:
            err = ModelNotConfiguredError(capability)
            # 覆盖 message：保留 capability 字段契约（test_model_router 强校验 exc.value.capability），
            # 但在 message 里补全「未绑定档案且无历史配置」的诊断信息，便于运维定位。
            err.args = (f"环节 {capability!r} 未绑定档案且无历史配置",)
            raise err
        return rows[0]

    # -------------------------------------------------------------- list_enabled
    def list_enabled(self, capability: str) -> list[dict[str, Any]]:
        """返回该 capability 的全部启用候选（按 binding 顺序或 rowid ASC）。

        无命中 → 返回空列表（不抛）。用于 :meth:`call_with_fallback` 的候选链。
        """
        if not capability:
            return []
        return self._candidates(capability)

    # -------------------------------------------------------------- _has_binding
    def _has_binding(self, capability: str) -> bool:
        """仅判断 ``capability_bindings`` 表里是否存在该 capability 行。

        用于 B1 修复：``call_with_fallback`` 在 light 零候选时，需要区分
        「该 capability 没 binding」（旧行为：回退 reasoning）与「有 binding
        但 _candidates 全 disabled / 缺失」（新行为：不回退，直接抛异常）。
        本方法只查 binding 主键存在性，不读 profile_ids 内容，单次 query 极轻。
        """
        if not capability:
            return False
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM capability_bindings WHERE capability = ? LIMIT 1",
                (capability,),
            ).fetchone()
        finally:
            conn.close()
        return row is not None

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
        profile_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """失败转移：按 rowid 顺序逐个尝试该 capability 的 enabled configs。

        返回 ``(completion, used_config_row)``；任一 ProviderError 或网络异常则记 warn 并试下一个。
        全部失败抛 :class:`AggregateProviderError`，携带各次错误的 ``(config_id, error_repr)``。
        无任何候选 → 抛 :class:`ModelNotConfiguredError`（与 ``resolve`` 行为一致）。

        V3 P0-2：``light`` capability 零配置时自动回退到 ``reasoning`` 候选链。
        回退成功时，返回 ``used_config_row['capability']`` 改写为 ``'reasoning'``，
        便于下游日志/审计识别实际命中配置。

        配置行 ``params_json`` 的非构造键（除 ``base_url`` / ``timeout_s`` /
        ``api_key`` / ``api_key_env`` 由 :meth:`get_provider` 消费外）会被合并进
        ``provider.complete(..., params=...)``——从而透传到上游请求体。调用方显式
        ``params`` 同名键覆盖配置行同名键。

        单次 run 级覆盖：``profile_id`` 非 None 时，候选列表收缩为
        ``model_profiles.profile_id == profile_id AND enabled=1`` 的唯一一行；
        行为与 :meth:`_candidates` 的 binding 分支一致——行形状（``config_id`` /
        ``capability`` / ``provider`` / ``model`` / ``params_json`` / ``enabled``）
        保持 ``config_id ← profile_id``、``capability ← 本 capability``，保证
        ``get_provider`` / ``ai_call_logs`` 无 schema 改动。``profile_id`` 缺失 /
        disabled → 抛 :class:`ModelNotConfiguredError`（detail 含 profile_id 便于排查），
        跳过 ``light → reasoning`` 回退逻辑（覆盖语义优先）。

        注：单配置时与 :meth:`resolve` + :meth:`get_provider` 的旧路径行为等价；
        ``resolve`` 仅返回行字典、不构造 Provider，因此不在本透传路径上。
        """
        if profile_id is not None:
            # 单次 run 级覆盖：直接按 profile_id 锁定唯一候选，跳过 binding 与回退。
            conn = get_connection(self.db_path)
            try:
                row = conn.execute(
                    """
                    SELECT profile_id, provider, model, params_json, enabled
                    FROM model_profiles
                    WHERE profile_id = ? AND enabled = 1
                    """,
                    (profile_id,),
                ).fetchone()
            finally:
                conn.close()
            if row is None:
                err = ModelNotConfiguredError(capability)
                err.args = (
                    f"profile_id={profile_id!r} 未在 model_profiles 命中或已 disabled "
                    f"(capability={capability!r})",
                )
                raise err
            # 行形状对齐 _candidates binding 分支：config_id←profile_id、
            # capability←本 capability，其它键从 model_profiles 直接映射。
            candidates: list[dict[str, Any]] = [{
                "config_id": row["profile_id"],
                "capability": capability,
                "provider": row["provider"],
                "model": row["model"],
                "params_json": row["params_json"],
                "enabled": row["enabled"],
            }]
            used_capability = capability
        else:
            candidates = self.list_enabled(capability)
            used_capability = capability
            if not candidates and capability == "light":
                # V3.7 修复（B1）：显式 binding 为准不回退。仅当 capability_bindings
                # 里压根没有 light 这一行时（旧行为）才回退 reasoning；显式 binding
                # 但 _candidates 全 disabled / 缺失则抛 ModelNotConfiguredError，
                # 不再静默落到 reasoning 链（避免绑定语义被绕过）。
                if not self._has_binding("light"):
                    candidates = self.list_enabled("reasoning")
                    used_capability = "reasoning"
                    if candidates:
                        log.info(
                            "model_router.light_fallback",
                            extra={
                                "requested": "light",
                                "fallback_to": "reasoning",
                                "candidates": len(candidates),
                            },
                        )
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
            # 解析该行 params_json：构造键（base_url/timeout_s/api_key/api_key_env）
            # 已被 get_provider 消费，不进请求体；其余键作为 extras 透传。
            # 解析失败 → {}（与 get_provider 同风格）。
            _raw = row.get("params_json") or "{}"
            if isinstance(_raw, dict):
                _parsed = _raw
            else:
                try:
                    _parsed = json.loads(_raw)
                except (TypeError, ValueError):
                    _parsed = {}
            _extras = {
                k: v for k, v in _parsed.items()
                if k not in ("base_url", "timeout_s", "api_key", "api_key_env")
            }
            merged: dict[str, Any] | None = {**_extras, **(params or {})}
            try:
                completion = provider.complete(messages, params=merged or None)
                # V3 P0-2：回退发生时把实际命中的 capability 暴露给下游
                if used_capability != capability:
                    row = dict(row)
                    row["capability"] = used_capability
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


__all__ = ["ModelRouter", "AGENT_CAPABILITY", "CAPABILITY_LABELS", "capability_for"]
