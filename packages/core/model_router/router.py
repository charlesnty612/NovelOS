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
  - director → ``reasoning``，observer → ``observer``（V3.9.3 起独立环节）
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

from .effective import (
    RESERVED_PARAM_KEYS,
    apply_thinking_override,
    parse_params,
    resolve_effective,
)
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
)

log = get_logger("novelos.model_router")

# ---------------------------------------------------------------------------
# Agent → Capability 映射
# ---------------------------------------------------------------------------

AGENT_CAPABILITY: dict[str, str] = {
    "director": "reasoning",
    "observer": "observer",  # V3.9.3：observer 独立环节（从 reasoning 拆出）
    "writer": "creative_writing",
    "polisher": "creative_writing",  # P1：润色走 creative_writing，与 writer 同 capability，可单次覆盖
    "arbiter": "reasoning",
    "deconstructor_chapter": "reasoning",
    "deconstructor_aggregate": "reasoning",
    "summarizer": "light",      # V3 P0-2：结构化提取走轻量模型
    "critic": "light",          # V3 P0-2：LLM 评审员走轻量模型
    "deep_reviewer": "reasoning",  # V1.3 二审 AI：三层清单核销（设定一致性→节拍核销→行为链连续性），推理型
    "scene_planner": "creative_writing",  # V3.9.2+：运行于 chapter-write 管线，归入正文写作（V3.9.2 前归 reasoning）
    # P1 合并调用（2026-09-14）：chapter-plan 的单 AI 节点（一次产出导演计划 + 场景计划；
    # scene_planner 保留为 chapter-write 的降级路径），与 writer 同属创作档。
    "director_planner": "creative_writing",
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
可配更便宜更快的模型（DeepSeek-chat / GLM-flash 级）。writer 保持
``creative_writing`` 不变（长文本生成任务）。``light`` capability 在未配置任何
enabled 行时自动回退到 ``reasoning`` 链（见 :meth:`ModelRouter.call_with_fallback`），
保证零破坏。

V3.7 project-init 四 agent（premise_designer / world_builder / character_designer /
volume_outliner）映射到独立 capability（premise_design / world_building /
character_design / volume_outline），与正文创作（creative_writing）/ 推理规划
（reasoning）/ 轻量评审（light）解耦。

V3.9.3 把 observer 从 reasoning 拆成独立 ``observer`` 环节：chapter_commit 的
observer 双腿此前以 ``capability_override="light"`` 硬编码跑 light 绑定的模型，
但页面把 observer 展示在 reasoning 组——展示与实际失真。独立环节后，observer
按 ``AGENT_CAPABILITY`` 默认走 ``observer`` capability，前端 AI 设置页能单独
分配模型；不依赖 capability_override 硬编码。迁移 0018 负责把 reasoning 的当前
绑定同步给 observer 行（已有行不动，幂等）。
"""


def capability_for(agent_name: str) -> str:
    """按 agent 名取 capability；未知 agent 默认 ``reasoning``。"""
    return AGENT_CAPABILITY.get(agent_name, "reasoning")


# ---------------------------------------------------------------------------
# V3.7：Capability 标签 + agents 归属（前端 / GET bindings 展示）
# ---------------------------------------------------------------------------

CAPABILITY_LABELS: dict[str, dict[str, object]] = {
    # 顺序即前端「环节」展示顺序；先 project-init（项目初始化四步），
    # 再正文写作，再推理规划、状态提取与轻量评审。
    "premise_design":   {"label": "题材定位",   "agents": ["premise_designer"]},
    "world_building":   {"label": "世界观",     "agents": ["world_builder"]},
    "character_design": {"label": "角色设计",   "agents": ["character_designer"]},
    "volume_outline":   {"label": "卷纲",       "agents": ["volume_outliner"]},
    "creative_writing": {"label": "正文写作",   "agents": [
        "writer", "polisher", "scene_planner", "director_planner",
    ]},
    "reasoning":        {"label": "推理规划",   "agents": [
        "director", "arbiter",
        "deconstructor_chapter", "deconstructor_aggregate",
        "deep_reviewer",
    ]},
    # V3.9.3：observer 拆为独立环节；不再列在 reasoning 组。原因：chapter_commit
    # observer 双腿此前硬编码 capability_override="light"，让 observer 实际跑
    # light 模型——前端却把它展示在 reasoning 组，分配与展示失真。独立环节后
    # observer 默认走 observer capability（见 AGENT_CAPABILITY），不依赖
    # capability_override；前端能单独分配模型。
    "observer":         {"label": "状态提取",   "agents": ["observer"]},
    # writer 在 revise 模式（按建议修改/驳回并改稿的局部修改）也走 light——
    # 标注为 writer(revise·改稿) 以便环节分配页如实展示；write/fresh_write 走 creative_writing。
    "light":            {"label": "轻量评审",   "agents": [
        "summarizer", "critic", "writer(revise·改稿)",
    ]},
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

        **环节级思考档覆盖**（2026-10-24，迁移 0036）：binding 行的
        ``thinking_override`` 非空时，对该环节候选链**每个**候选的 ``params_json``
        应用 :func:`effective.apply_thinking_override` 翻译（按候选自己的 params
        深拷贝后覆盖，**不改库**）——即「环节覆盖优先于档案值」；fallback 链上
        每个候选都被同一覆盖（后备档案也按环节强度跑）。
        ``thinking_override`` 为 NULL（存量行）⇒ params 原样逐字，零行为突变。

        ⚠️ 边界：:meth:`call_with_fallback` 的 ``profile_id=`` 单次 run 级覆盖路径
        **不走本方法**（调用方显式锁定档案，绕过 binding）⇒ 该路径不应用环节覆盖
        ——显式指定档案语义优先于环节配置，属预期。
        """
        if not capability:
            return []

        conn = get_connection(self.db_path)
        try:
            binding_row = conn.execute(
                "SELECT profile_ids, thinking_override FROM capability_bindings "
                "WHERE capability = ?",
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
            override = binding_row["thinking_override"] or None
            # 保留 binding 顺序：逐个查；missing/enabled=0 跳过
            conn = get_connection(self.db_path)
            try:
                out: list[dict[str, Any]] = []
                for pid in profile_ids:
                    row = conn.execute(
                        """
                        SELECT profile_id, provider, model, params_json, enabled, connection_id
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
                        "connection_id": row["connection_id"],
                    })
            finally:
                conn.close()
            if override:
                # 环节覆盖：候选链每个候选的 params 深拷贝后翻译再放回（不改库）。
                # 脏 override（不在 8 档枚举）→ ValueError 显式失败（写时已拦，
                # 这里只兜 DB 直改的脏行——坏配置不该被静默当「跟随档案」）。
                out = [
                    {
                        **row,
                        "params_json": json.dumps(
                            apply_thinking_override(
                                parse_params(row.get("params_json")), override
                            ),
                            ensure_ascii=False,
                        ),
                    }
                    for row in out
                ]
            return self._filter_disabled_connections(out)

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
        run_id: str | None = None,
        extra_headers: dict[str, str] | None = None,
        connection_row: dict[str, Any] | None = None,
    ):
        """按**生效配置**构造 Provider（连接优先）。

        - 档案**未绑定连接** ⇒ 与改造前逐字相同（provider / base_url / api_key
          全部取自档案的 ``params_json``）。
        - 档案**已绑定连接** ⇒ provider / base_url / api_key / headers /
          models_url **全部取自连接**（档案的同名键被忽略）。
        - ``"mock"`` → :class:`MockProvider`（``scripted`` 透传；默认 None，回显空 JSON）。
        - ``"anthropic"`` → :class:`AnthropicProvider`（``base_url`` 可省，默认官方）。
        - ``"ollama"`` → :class:`OllamaProvider`（``base_url`` 可省，默认本地）。
        - 其它（``openai`` / ``openai_compatible`` / ``deepseek`` 等）→
          :class:`OpenAICompatibleProvider`。

        ``run_id`` 供请求头模板渲染（``{{run_id}}`` / ``{{session_id}}``）；
        ``extra_headers`` 是**运行级**覆盖层（合并顺序：provider 默认 < 连接级 < 运行级）。
        """
        connection_id = config_row.get("connection_id") or None
        if connection_row is None and connection_id:
            connection_row = self._load_connection(connection_id)

        eff = resolve_effective(config_row, connection_row, run_id=run_id)
        provider_name = (eff.provider or "").strip().lower()
        if provider_name == "mock":
            return MockProvider(scripted=scripted)

        headers = dict(eff.headers)
        if extra_headers:
            headers.update(extra_headers)

        if provider_name == "anthropic":
            return AnthropicProvider(
                base_url=eff.base_url,
                api_key=eff.api_key,
                model=eff.model,
                extra_headers=headers,
            )

        if provider_name == "ollama":
            return OllamaProvider(
                base_url=eff.base_url,
                model=eff.model,
                extra_headers=headers,
            )

        # 其它 → OpenAI 兼容（OpenAI / DeepSeek / 通义 等）
        if not eff.base_url:
            raise ValueError(
                f"provider {provider_name!r} requires a base_url "
                f"(config_id={config_row.get('config_id')})"
            )
        timeout = eff.timeout_s if eff.timeout_s is not None else 240.0
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("params_json.timeout_s must be a positive number of seconds")
        return OpenAICompatibleProvider(
            base_url=eff.base_url,
            api_key=eff.api_key,
            model=eff.model,
            timeout=timeout,
            extra_headers=headers,
            models_url=eff.models_url,
        )

    def _load_connection(self, connection_id: str) -> dict[str, Any] | None:
        """按主键取一条连接；不存在 → None。

        ⚠️ 返回 None 时，:func:`resolve_effective` 会**抛配置错误**（悬空引用），
        **不静默回落**到档案自身的 base_url（那会掩盖配置错误，让用户以为连上了）。
        """
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM model_connections WHERE connection_id = ?", (connection_id,)
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def _filter_disabled_connections(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """剔除「绑定了**已停用连接**」的档案。

        语义对齐既有的「enabled=0 的 profile 自动从 binding 候选链跳过」——
        连接被停用 ⇒ 引用它的档案同样不可用。

        ⚠️ 悬空 ``connection_id``（指向不存在的连接）**不在此剔除**：
        它要走到 :meth:`get_provider` 才抛配置错误（**显式失败好过静默跳过**——
        静默跳过会让候选链空掉，用户看到的是「没配模型」而不是「连接坏了」）。
        """
        conn_ids = {r.get("connection_id") for r in rows if r.get("connection_id")}
        if not conn_ids:
            return rows
        conn = get_connection(self.db_path)
        try:
            placeholders = ",".join("?" * len(conn_ids))
            disabled = {
                r["connection_id"]
                for r in conn.execute(
                    f"SELECT connection_id FROM model_connections "
                    f"WHERE connection_id IN ({placeholders}) AND enabled = 0",
                    list(conn_ids),
                ).fetchall()
            }
        finally:
            conn.close()
        if not disabled:
            return rows
        return [r for r in rows if r.get("connection_id") not in disabled]

    # -------------------------------------------------------------- call_with_fallback
    def call_with_fallback(
        self,
        capability: str,
        messages: list[dict[str, Any]],
        *,
        params: dict[str, Any] | None = None,
        scripted: Any | None = None,
        profile_id: str | None = None,
        run_id: str | None = None,
        extra_headers: dict[str, str] | None = None,
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
                    SELECT profile_id, provider, model, params_json, enabled, connection_id
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
                "connection_id": row["connection_id"],
            }]
            candidates = self._filter_disabled_connections(candidates)
            if not candidates:
                err = ModelNotConfiguredError(capability)
                err.args = (
                    f"profile_id={profile_id!r} 绑定的连接已停用 "
                    f"(capability={capability!r})",
                )
                raise err
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
                        # V3.9 检修：extra= 字段不会被 Formatter 渲染进 message，
                        # 排障字段全丢——拼进 message 文本。
                        log.info(
                            "model_router.light_fallback requested=light "
                            "fallback_to=reasoning candidates=%d",
                            len(candidates),
                        )
        if not candidates:
            raise ModelNotConfiguredError(capability)

        attempts: list[tuple[str, str]] = []
        last_exc: Exception | None = None
        for row in candidates:
            cid = row.get("config_id") or "<unknown>"
            try:
                provider = self.get_provider(
                    row, scripted=scripted, run_id=run_id, extra_headers=extra_headers
                )
            except Exception as exc:  # noqa: BLE001 —— 构造失败也视为一次尝试失败
                msg = f"construct failed: {exc}"
                log.warning(
                    "model_router.fallback.construct_failed capability=%s "
                    "config_id=%s error=%s",
                    capability, cid, msg,
                )
                attempts.append((cid, msg))
                last_exc = exc
                continue
            # 解析该行 params_json：构造键（base_url/timeout_s/api_key/api_key_env/
            # headers/models_url）已被 get_provider 消费，不进请求体；其余键作为 extras 透传。
            # 解析失败 → {}（与 get_provider 同风格）。
            # ⚠️ 保留键集合来自 effective.RESERVED_PARAM_KEYS（**唯一权威**）——
            #    此前本处与 providers.py 各写一份副本，漏一处就把该键当请求体
            #    发给上游（OpenAI 兼容 API 直接 400）。
            _raw = row.get("params_json") or "{}"
            if isinstance(_raw, dict):
                _parsed = _raw
            else:
                try:
                    _parsed = json.loads(_raw)
                except (TypeError, ValueError):
                    _parsed = {}
            _extras = {k: v for k, v in _parsed.items() if k not in RESERVED_PARAM_KEYS}
            merged: dict[str, Any] | None = {**_extras, **(params or {})}
            try:
                completion = provider.complete(messages, params=merged or None)
                # V3 P0-2：回退发生时把实际命中的 capability 暴露给下游
                if used_capability != capability:
                    row = dict(row)
                    row["capability"] = used_capability
                # ⚠️ 2026-10-21 批次十五：**生效 provider 覆写档案列**。
                #    绑定连接的档案，其 provider 列只是派生镜像（连接的 provider
                #    才是权威）⇒ 下游（ai_call_logs.model_id）必须记**实际用的**那个，
                #    否则日志会把「连到 opencode 却用了 anthropic」记成 anthropic。
                if row.get("provider") != provider.name:
                    row = dict(row)
                    row["provider"] = provider.name
                return completion, row
            except ProviderError as exc:
                msg = f"{exc} (status_code={exc.status_code})"
                log.warning(
                    "model_router.fallback.provider_error capability=%s "
                    "config_id=%s status_code=%s error=%s",
                    capability, cid, exc.status_code, exc,
                )
                attempts.append((cid, msg))
                last_exc = exc
                continue
            except Exception as exc:  # noqa: BLE001 —— 网络/超时等非 ProviderError 也吞
                msg = f"unexpected error: {exc}"
                log.warning(
                    "model_router.fallback.unexpected capability=%s "
                    "config_id=%s error=%s",
                    capability, cid, msg,
                )
                attempts.append((cid, msg))
                last_exc = exc
                continue
        # 全部失败 → 聚合异常
        raise AggregateProviderError(capability, attempts) from last_exc


__all__ = ["ModelRouter", "AGENT_CAPABILITY", "CAPABILITY_LABELS", "capability_for"]
