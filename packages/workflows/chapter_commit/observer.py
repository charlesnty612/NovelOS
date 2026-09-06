"""Observer 节点——双腿拆分（A 数组/B 数组）、并行执行与聚合
（拆分自 chapter_commit/pipeline.py，2026-09-06 审查批次三）。"""

from __future__ import annotations

import concurrent.futures
import json
from typing import Any

from packages.core.agent_runtime.runner import run_agent
from packages.core.context_engine import build_observer_input
from packages.core.db import get_connection
from packages.core.ids import now_iso
from packages.core.model_router.router import capability_for

from .pipeline_common import (
    _OBSERVER_ALL_ARRAYS,
    _OBSERVER_LEG_A_SET,
    _OBSERVER_LEG_B_SET,
    _filter_mock_for_leg,
    _inject_resolvable_ids_into_config,
    _observer_parallel_enabled,
    _observer_split_enabled,
    _summary_parallel_enabled,
)
from .summary import (
    _prepare_summarizer_call,
)

# Observer delta 校验失败重试提示模板（注入 payload._retry_hint 引导 LLM 修正）。
# 真实 LLM（如 MiniMax-M3）曾出现 ``character_changes[0].op='update' 但 before 为 None``
# 这类业务校验失败：让 observer 修正后重新完整输出 7 个 change 数组 JSON。
_OBSERVER_RETRY_HINT_TEMPLATE = (
    "\n\n[Validation note] 上一次输出的 delta 未通过业务校验：{errors}。"
    "请按反馈修正后重新完整输出 7 个 change 数组的合法 JSON（保持 schema_version="
    "state-delta-v0 外的其它元信息字段由后续节点注入，无需在本次输出中包含）。"
    "特别注意：凡 snapshot 中不存在前值的实体（本章首次出现的人物/地点/设定），"
    "必须用 add 而非 update；update 必须给出与 snapshot 一致的 before。"
)


def _merge_observer_legs(leg_a: dict[str, Any], leg_b: dict[str, Any]) -> dict[str, Any]:
    """合并两条腿的 7 数组输出。

    规则：
    1. 7 数组齐全；任一腿缺某数组 → 视为空列表。
    2. 同数组内：leg_a 优先；leg_b 中与 leg_a change_id 冲突的条目丢弃。
    3. change_id 缺失或非字符串 → 按数组内出现顺序追加（保留双方全部）。
    """
    merged: dict[str, Any] = {}
    for arr_name in _OBSERVER_ALL_ARRAYS:
        a_list = leg_a.get(arr_name) if isinstance(leg_a, dict) else None
        b_list = leg_b.get(arr_name) if isinstance(leg_b, dict) else None
        if not isinstance(a_list, list):
            a_list = []
        if not isinstance(b_list, list):
            b_list = []
        if arr_name in _OBSERVER_LEG_A_SET:
            # leg_a 负责：直接取 a_list，b_list 丢弃（按 scope 不会出现冲突）
            merged[arr_name] = list(a_list)
        else:
            # leg_b 负责：a_list 应为空；防御性兜底时仍按 leg_b 优先
            merged[arr_name] = list(b_list) if not a_list else (list(b_list) + list(a_list))
    return merged


def _build_observer_ctx_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """装配 observer 输入。

    改用 ``snapshot_mode="trimmed"``（M1/M2 实证：全量快照 >110KB 导致 LLM 超时；
    trimmed 仅保留最近 ``keep_recent_commits`` 个 commit 中 touch 过的实体全量字段 +
    open/active/escalated/acknowledged 状态 hook/debt 全量 + 其余仅摘要），让
    observer 在大快照场景下也能稳定完成。trim 口径与 stats 写入由
    :func:`build_observer_input` 负责；本节点仅做模式选择。
    """
    db_path = ctx["db_path"]
    chapter_id = ctx["chapter_id"]
    payload = build_observer_input(db_path, chapter_id, snapshot_mode="trimmed")
    return {"observer_input": payload}


def _observer_node(ctx: dict[str, Any]) -> dict[str, Any]:
    """Observer 节点（V3.1.1 O-2：双 leg 拆分）。

    V3.1.1 O-2 之前：本节点单次调 ``run_agent("observer", payload)`` 输出 7 数组。
    V3.1.1 O-2 之后：
    - 默认（``NOVELOS_OBSERVER_SPLIT=on``）：分两条轻量 leg 调 observer：
      - leg_a (entities)：character_changes + relationship_changes + world_changes
      - leg_b (narrative)：new_events + new_hooks + resolved_hooks + debt_changes
      两腿各自走 ``observer`` capability（V3.9.3 拆出独立环节，迁移 0018 同步绑定）；
      payload 注入 ``extraction_scope`` 字段让 observer 只输出对应 scope 的数组（见
      ``docs/agents/prompts/observer-v1.md`` §11）。两腿响应合并后走既有
      ``_inject_validate_node`` 校验链不变。
    - off（``NOVELOS_OBSERVER_SPLIT=off``）：走旧单次大调用路径——保留原代码
      路径分支，保证可一键回退。

    输出：
    - ``observer_payload``：合并后的 7 数组（dict）。
    - ``observer_split_meta``：可观测性元数据——
      ``{"enabled": bool, "leg_a": {...}, "leg_b": {...}, "merged_at": iso}``；
      每条 leg 含 ``{"tokens": int, "latency_ms": int, "retry_count": int}``，
      数值从 ai_call_logs 聚合（chapter-commit run + node_run_id + agent='observer'）。
    """
    db_path = ctx["db_path"]
    run_id = ctx["run_id"]
    node_run_id = ctx.get("_current_node_run_id")
    base_payload = ctx["observer_input"]
    mock_script = (ctx.get("mock_providers") or {}).get("observer")

    # V3.10 O-4：可核销白名单注入。数据源 = observer_input.previous_state，
    # 与 validator 校验快照同源；narrative 腿与单腿旧路径都需要（entities 腿不需要）。
    snapshot_for_resolvable = (
        base_payload.get("previous_state") if isinstance(base_payload, dict) else None
    )
    base_payload = _inject_resolvable_ids_into_config(
        base_payload, snapshot_for_resolvable or {},
    )

    # env 开关 + ctx 显式覆盖
    if ctx.get("observer_split") is False or not _observer_split_enabled():
        # 旧单次路径（V3.1.1 O-2 之前；保证回退兼容）
        out = run_agent(
            db_path,
            "observer",
            base_payload,
            run_id,
            node_run_id=node_run_id,
            expected="observer",
            mock_script=mock_script,
            # V3.9.4：observer 单次 run 级 model_overrides 透传（off 路径未显式
            # capability_override，沿用既有 capability_for('observer') 解析行为）。
            profile_id=(ctx.get("model_overrides") or {}).get("observer"),
        )
        return {
            "observer_payload": out,
            "observer_split_meta": {
                "enabled": False,
                "leg_a": None,
                "leg_b": None,
                "merged_at": now_iso(),
            },
        }

    # 新拆分路径：两腿分别调 observer agent，按 scope 注入 payload 指令
    # V3.1.1 O-3：先按 leg 裁剪 previous_state，让两腿各自只看到本 leg 需要的集合；
    # 同时 base_payload["config"]["recent_event_ids"] 已由 build_observer_input 自动注入。
    leg_a_payload, leg_a_trim_stats = _trim_observer_input_for_leg(base_payload, "entities")
    leg_a_payload["extraction_scope"] = "entities"
    leg_b_payload, leg_b_trim_stats = _trim_observer_input_for_leg(base_payload, "narrative")
    leg_b_payload["extraction_scope"] = "narrative"

    # V3.10 O-4：narrative 腿再次注入可核销白名单（trim_observer_input_for_leg
    # 不动 config 子树，重新注入确保 trimmed payload 也带白名单；entities 腿
    # 不涉及 resolved_hooks / debt_changes，不需要白名单）。
    leg_b_payload = _inject_resolvable_ids_into_config(
        leg_b_payload, snapshot_for_resolvable or {},
    )

    # Mock 兼容：golden regression 的 mock_script 是完整 7 数组；按 leg 过滤，
    # 让两腿各自看到「只含本 leg 范围」mock——merge 后等价于单次大调用。
    leg_a_mock = _filter_mock_for_leg(mock_script, "entities")
    leg_b_mock = _filter_mock_for_leg(mock_script, "narrative")

    # 量化 per-leg 字符数（便于 O-3 收益对比）；量的是 trim 后的 JSON 序列化字节数。
    try:
        leg_a_chars = len(json.dumps(leg_a_payload, ensure_ascii=False))
    except (TypeError, ValueError):
        leg_a_chars = 0
    try:
        leg_b_chars = len(json.dumps(leg_b_payload, ensure_ascii=False))
    except (TypeError, ValueError):
        leg_b_chars = 0

    parallel_enabled = _observer_parallel_enabled(ctx)
    summary_parallel = _summary_parallel_enabled(ctx)
    # wall_time：仅并发路径记录（便于测试断言「真并发」）；off 回退串行时不统计。
    parallel_wall_ms: int | None = None
    summary_early: dict[str, Any] | None = None
    if parallel_enabled and summary_parallel:
        # V3.7 P0：observer 双腿 + summarizer 三路同池并发（ThreadPoolExecutor,
        # max_workers=3）。summary 与双腿互不依赖（不读 observer_payload），可同跑。
        # summary_early 走 observer 返回 dict 顶层 key，引擎在 _run_nodes 里
        # ``ctx.update(output)`` 自动合入下游 ctx，summarize 节点直接 ``ctx.get('summary_early')``
        # 短路消费（参考 packages/core/workflow_runtime/engine.py 行 336-337）。
        leg_a_out, leg_b_out, parallel_wall_ms, summary_early = (
            _run_observer_with_summary_in_parallel(
                db_path=db_path,
                run_id=run_id,
                node_run_id=node_run_id,
                leg_a_payload=leg_a_payload,
                leg_b_payload=leg_b_payload,
                leg_a_mock=leg_a_mock,
                leg_b_mock=leg_b_mock,
                ctx=ctx,
            )
        )
    elif parallel_enabled:
        # V3.5：只开 observer_parallel，未开 summary_parallel → 保持现双腿并发形态。
        leg_a_out, leg_b_out, parallel_wall_ms = _run_observer_legs_in_parallel(
            db_path=db_path,
            run_id=run_id,
            node_run_id=node_run_id,
            leg_a_payload=leg_a_payload,
            leg_b_payload=leg_b_payload,
            leg_a_mock=leg_a_mock,
            leg_b_mock=leg_b_mock,
            # V3.9.4：observer 单次 run 级 model_overrides 透传
            profile_id=(ctx.get("model_overrides") or {}).get("observer"),
        )
    else:
        # off / 兼容回退：原串行提交，保持既有行为
        # 单次 run 级 model_overrides：observer 键 → profile_id 透传
        _observer_profile_id = (ctx.get("model_overrides") or {}).get("observer")
        leg_a_out = run_agent(
            db_path,
            "observer",
            leg_a_payload,
            run_id,
            node_run_id=node_run_id,
            expected="observer",
            mock_script=leg_a_mock,
            capability_override="observer",  # V3.9.3：observer 拆为独立环节（不再走 light）
            profile_id=_observer_profile_id,
        )
        leg_b_out = run_agent(
            db_path,
            "observer",
            leg_b_payload,
            run_id,
            node_run_id=node_run_id,
            expected="observer",
            mock_script=leg_b_mock,
            capability_override="observer",  # V3.9.3：observer 拆为独立环节（不再走 light）
            profile_id=_observer_profile_id,
        )

    merged = _merge_observer_legs(leg_a_out, leg_b_out)
    meta = _aggregate_observer_split_meta(
        db_path,
        run_id=run_id,
        node_run_id=node_run_id,
        # 两腿各 1 次成功调用（首次即通过；如失败将由 _inject_validate_node 重试，
        # 该节点会消费同一 ai_call_logs 行做聚合，本节点只关心成功首调）。
        expected_calls=2,
        merged_at=now_iso(),
    )
    # V3.1.1 O-3：把 per-leg payload 字符数 + 按腿 trim stats 挂到 observer_split_meta，
    # 便于量化 O-3 收益（与 O-2 时的 ~120k tokens / 全量 payload 对比）。
    if isinstance(meta, dict):
        meta["leg_a_chars"] = leg_a_chars
        meta["leg_b_chars"] = leg_b_chars
        meta["leg_a_total_chars"] = leg_a_chars + leg_b_chars
        meta["leg_a_trim_stats"] = leg_a_trim_stats
        meta["leg_b_trim_stats"] = leg_b_trim_stats
        # V3.5：并发模式标记 + wall_time（仅并发路径有值；off 路径为 None）。
        meta["parallel"] = bool(parallel_enabled)
        # V3.7：summarizer 并入并发池标记（与 observer_parallel 双开关独立）。
        meta["summary_parallel"] = bool(summary_parallel)
        if parallel_wall_ms is not None:
            meta["parallel_wall_ms"] = int(parallel_wall_ms)
            # V3.7：summary 早产成功时复用同 wall，便于观测同池收益；早产失败/未触发时省略字段。
            if summary_early is not None and not summary_early.get("skipped"):
                meta["summary_parallel_wall_ms"] = int(parallel_wall_ms)
            # SQLite rowid 在并发 commit 下不保证 leg_a 先 leg_b 后——记录此点
            # 让观测者明确 leg_a / leg_b 字段可能交换（不影响业务正确性）。
            meta["ordering_note"] = (
                "concurrent_legs_rowid_order_unstable"
                if parallel_enabled
                else "serial"
            )
        # recent_event_ids 注入条数（base_payload.config 已在 build_observer_input 完成）
        base_config = base_payload.get("config") or {}
        if isinstance(base_config, dict):
            reid = base_config.get("recent_event_ids")
            if isinstance(reid, list):
                meta["recent_event_ids_count"] = len(reid)
    result: dict[str, Any] = {
        "observer_payload": merged,
        "observer_split_meta": meta,
    }
    # V3.7：summary 早产结果以顶层 key 暴露；引擎 ``ctx.update(output)`` 自动合入下游 ctx。
    # 仅当 third-leg 跑过（即使是 skipped）才返回该 key，让下游明确语义。
    if summary_early is not None:
        result["summary_early"] = summary_early
    return result


def _run_observer_legs_in_parallel(
    *,
    db_path: Any,
    run_id: str,
    node_run_id: str | None,
    leg_a_payload: dict[str, Any],
    leg_b_payload: dict[str, Any],
    leg_a_mock: Any,
    leg_b_mock: Any,
    profile_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """V3.5：双腿并发提交到 run_agent（ThreadPoolExecutor，max_workers=2）。

    并发语义：
    - 两条腿同时入队；任一腿抛异常会让该 future 携带异常被 ``future.result()`` 重抛——
      ``_observer_node`` 调用方未捕获，将直接冒泡（与原串行路径异常语义一致）。
    - 两腿各自 ``run_agent`` 内部独立 get_connection / insert ai_call_logs；
      SQLite WAL + busy_timeout 5s 保证并发 INSERT 不锁死（先到先 commit，rowid 顺序
      不可预测但业务正确性不依赖 leg_a/leg_b 提交顺序，merge 按 key 区分）。
    - 收集后返回 ``(leg_a_out, leg_b_out, wall_ms)``。wall_ms 用 ``time.monotonic()``
      度量，仅供测试 / 观测断言「真并发」。

    设计动机（V3.5 提速调研结论）：见 ``docs/roadmap/v3-plan.md`` 已知问题——双腿
    并发让 wall_time ≈ max(t_leg_a, t_leg_b)，单次路径 ≈ sum(t_leg_a, t_leg_b)。
    实测 m1_run（service_tier=priority 启用）：单腿 60-180s ⇒ 并发收益 30-50%。

    V3.9.4：新增 ``profile_id`` 参数透传 observer 单次 run 级 model_overrides 覆盖
    （由 :func:`_observer_node` 从 ctx['model_overrides']['observer'] 解析后传入）。
    mock 路径不消费 profile_id，与既有契约一致。
    """
    import time as _time

    def _run_leg_a() -> dict[str, Any]:
        return run_agent(
            db_path,
            "observer",
            leg_a_payload,
            run_id,
            node_run_id=node_run_id,
            expected="observer",
            mock_script=leg_a_mock,
            capability_override="observer",  # V3.9.3：observer 拆为独立环节（不再走 light）
            profile_id=profile_id,  # V3.9.4：observer 覆盖透传
        )

    def _run_leg_b() -> dict[str, Any]:
        return run_agent(
            db_path,
            "observer",
            leg_b_payload,
            run_id,
            node_run_id=node_run_id,
            expected="observer",
            mock_script=leg_b_mock,
            capability_override="observer",  # V3.9.3：observer 拆为独立环节（不再走 light）
            profile_id=profile_id,  # V3.9.4：observer 覆盖透传
        )

    # max_workers=2：恰好容纳两条腿；不再扩张，避免 provider 侧被并发请求压垮。
    # ThreadPoolExecutor 默认 shutdown 语义是 with-block 退出时 wait 全部完成——
    # 即使 future.result() 抛异常，两腿都会被 join 后才退出。
    start = _time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=2, thread_name_prefix="observer-leg",
    ) as pool:
        future_a = pool.submit(_run_leg_a)
        future_b = pool.submit(_run_leg_b)
        # as_completed：任一腿完成就返回，但需为每条腿 .result() 检查异常——这里直接
        # 按「submit 顺序」取结果：leg_a / leg_b 的归属由调用方按变量绑定恢复，不依赖
        # 完成时间。
        leg_a_out = future_a.result()
        leg_b_out = future_b.result()
    wall_ms = int((_time.monotonic() - start) * 1000)
    return leg_a_out, leg_b_out, wall_ms


def _run_observer_with_summary_in_parallel(
    *,
    db_path: Any,
    run_id: str,
    node_run_id: str | None,
    leg_a_payload: dict[str, Any],
    leg_b_payload: dict[str, Any],
    leg_a_mock: Any,
    leg_b_mock: Any,
    ctx: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], int, dict[str, Any] | None]:
    """V3.7 P0：observer 双腿 + summarizer 三路同池并发（ThreadPoolExecutor, max_workers=3）。

    与 :func:`_run_observer_legs_in_parallel` 的关系：
    - 本函数保留原双腿函数不动；调用方按需选择（summary_parallel 开关）。
    - 第三 future ``_run_summary`` 内部 try/except 捕获一切异常，绝不让 summary
      失败炸掉 observer 节点；失败时返回 ``{"skipped": True}`` 让下游走兜底。

    返回 ``(leg_a_out, leg_b_out, wall_ms, summary_early)``：
    - summary_early 形态：
      - ``{"skipped": True}`` ——prepare 阶段判定为可跳过（章节/draft 缺失）。
      - ``{"skipped": False, "output": out, "project_id": ..., "chapter_no": ..., "tail_text": ...}``
        —— LLM 调成功，供下游 summarize 节点短路消费。
      - ``None`` ——summary 异常被吞掉，让下游 summarize 节点自愈重跑。
    """
    import logging as _logging
    import time as _time

    # V3.9.4：observer 单次 run 级 model_overrides 透传（summary 走 light 键，互不串）。
    _observer_profile_id = (ctx.get("model_overrides") or {}).get("observer")
    _summarizer_profile_id = (ctx.get("model_overrides") or {}).get(
        capability_for("summarizer")
    )

    def _run_leg_a() -> dict[str, Any]:
        return run_agent(
            db_path,
            "observer",
            leg_a_payload,
            run_id,
            node_run_id=node_run_id,
            expected="observer",
            mock_script=leg_a_mock,
            capability_override="observer",  # V3.9.3：observer 拆为独立环节（不再走 light）
            profile_id=_observer_profile_id,
        )

    def _run_leg_b() -> dict[str, Any]:
        return run_agent(
            db_path,
            "observer",
            leg_b_payload,
            run_id,
            node_run_id=node_run_id,
            expected="observer",
            mock_script=leg_b_mock,
            capability_override="observer",  # V3.9.3：observer 拆为独立环节（不再走 light）
            profile_id=_observer_profile_id,
        )

    def _recover_run_status() -> None:
        """早产失败→恢复 run 状态防污染（已退化为 no-op）。

        历史行为：runner 异常路径会把 run 行盖成 FAILED，本函数改回 COMPLETED
        兜底。2026-08 异步化改造后（1）runner 对引擎托管调用（node_run_id 非空）
        一律不再盖戳（见 runner._finalize_agent_run_status）；（2）本函数自己盖
        的 COMPLETED 反而成为污染源——异步轮询方（前端 2s 轮询 / 集成测试等待环）
        会在 observer 节点仍在执行时读到假 COMPLETED 终态。run 终态完全由
        ``engine._run_nodes`` 的 ``_finalize_run`` 收口，这里保留 no-op 维持
        三条失败路径调用对称。
        """
        return None

    def _run_summary() -> dict[str, Any] | None:
        """第三路：summarizer LLM 早产。

        任一异常（prepare 缺失 / run_agent 失败 / 其它）→ log warning +
        ``_recover_run_status()`` 防 runner 内部 FAILED 污染 +
        返回 None，让下游 summarize 节点按原 prepare+run_agent 路径自愈完成。
        """
        try:
            prepared = _prepare_summarizer_call(ctx)
        except Exception as exc:  # noqa: BLE001 —— prepare 阶段容错
            _logging.getLogger(__name__).warning(
                "chapter_commit.observer early summarize prepare failed: "
                "chapter_id=%s err=%s", ctx.get("chapter_id"), exc,
            )
            # 注意：prepare 阶段不调 run_agent，不会有 runner 兜底的 FAILED 状态。
            # 仍调一次恢复函数做幂等的 noop，保证两条路径走向完全对称。
            _recover_run_status()
            return None
        if prepared is None:
            return {"skipped": True}
        try:
            out = run_agent(
                db_path,
                "summarizer",
                prepared["payload"],
                run_id,
                node_run_id=node_run_id,
                expected="summarizer",
                mock_script=prepared["mock"],
                # V3.9.4：summarizer 单次 run 级覆盖走 light 键透传
                profile_id=_summarizer_profile_id,
            )
        except Exception as exc:  # noqa: BLE001 —— LLM 失败兜底，不炸 observer
            _logging.getLogger(__name__).warning(
                "chapter_commit.observer early summarize run_agent failed: "
                "chapter_id=%s err=%s", ctx.get("chapter_id"), exc,
            )
            # runner 内部异常路径已 _update_workflow_run(FAILED)（runner.py:321/364/396）。
            # 在本吞异常分支里强制恢复为 COMPLETED，避免 run 状态被污染。
            _recover_run_status()
            return None
        return {
            "skipped": False,
            "output": out,
            "project_id": prepared["project_id"],
            "chapter_no": prepared["chapter_no"],
            "tail_text": prepared["tail_text"],
        }

    # max_workers=3：恰好容纳两腿 + summary；不再扩张，避免 provider 侧并发请求过多。
    start = _time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=3, thread_name_prefix="observer-leg+sum",
    ) as pool:
        future_a = pool.submit(_run_leg_a)
        future_b = pool.submit(_run_leg_b)
        future_s = pool.submit(_run_summary)
        leg_a_out = future_a.result()
        leg_b_out = future_b.result()
        # summary future 的异常已由 _run_summary 内部吞掉；此处再兜一层确保异常
        # 不会以 future.result() 形式冒泡炸掉 observer 节点。
        try:
            summary_early: dict[str, Any] | None = future_s.result()
        except Exception as exc:  # noqa: BLE001
            _logging.getLogger(__name__).warning(
                "chapter_commit.observer summary future unexpected exception: "
                "chapter_id=%s err=%s", ctx.get("chapter_id"), exc,
            )
            # future.result() 自身冒泡的异常：通常是 _run_summary 之外的代码 bug。
            # 同样恢复 run 状态防污染（幂等）。
            _recover_run_status()
            summary_early = None
    wall_ms = int((_time.monotonic() - start) * 1000)
    return leg_a_out, leg_b_out, wall_ms, summary_early


def _aggregate_observer_split_meta(
    db_path: Any,
    *,
    run_id: str,
    node_run_id: str | None,
    expected_calls: int,
    merged_at: str,
) -> dict[str, Any]:
    """聚合 observer 双 leg 的 tokens / latency_ms / retry_count。

    路径：``ai_call_logs`` WHERE ``run_id=? AND node_run_id=? AND agent='observer'``
    取最近 ``expected_calls`` 条（按 created_at DESC 倒序后回正为 leg_a 先 leg_b 后）；
    单次大调用时（off 路径）不会调用本函数，故此处的「按 created_at 排序 +
    假定 leg_a 先 leg_b 后」足以区分两腿。极端情况下两腿几乎同时落库（毫秒级
    差异），仍可按 ``rowid`` 倒序稳定回放顺序。
    """
    try:
        conn = get_connection(db_path)
    except Exception:  # noqa: BLE001 —— 观测失败不阻断 observer 节点
        return {
            "enabled": True,
            "leg_a": None,
            "leg_b": None,
            "merged_at": merged_at,
        }
    try:
        # 取本节点 observer 全部调用（按 rowid ASC；同一 run_id+node_run_id 下
        # 两腿调用按代码顺序落库，rowid 顺序 = 调用顺序）
        rows = conn.execute(
            """
            SELECT a.call_id, a.token_usage_json, a.latency_ms, a.retry_count, a.created_at
            FROM ai_call_logs a
            JOIN agents ag ON ag.agent_id = a.agent_id
            WHERE a.run_id = ? AND ag.name = 'observer'
              AND (? IS NULL OR a.node_run_id = ?)
            ORDER BY a.rowid ASC
            """,
            (run_id, node_run_id, node_run_id),
        ).fetchall()
    finally:
        conn.close()

    legs: list[dict[str, Any]] = []
    for r in rows[-2:] if len(rows) >= 2 else rows:
        try:
            usage = json.loads(r["token_usage_json"]) if r["token_usage_json"] else {}
        except (TypeError, ValueError):
            usage = {}
        legs.append({
            "tokens": int(usage.get("total") or 0),
            "latency_ms": int(r["latency_ms"] or 0),
            "retry_count": int(r["retry_count"] or 0),
            "call_id": r["call_id"],
        })

    leg_a = legs[0] if len(legs) >= 1 else None
    leg_b = legs[1] if len(legs) >= 2 else None
    return {
        "enabled": True,
        "leg_a": leg_a,
        "leg_b": leg_b,
        "merged_at": merged_at,
    }


def _extract_leg_payload(observer_payload: dict[str, Any], leg: str) -> dict[str, Any]:
    """从合并后的 observer_payload 中按 leg 抽取对应数组（用于 per-leg 重试缓存）。"""
    keep = _OBSERVER_LEG_A_SET if leg == "entities" else _OBSERVER_LEG_B_SET
    out: dict[str, Any] = {}
    if not isinstance(observer_payload, dict):
        return out
    for arr_name in _OBSERVER_ALL_ARRAYS:
        if arr_name in keep:
            arr = observer_payload.get(arr_name)
            out[arr_name] = list(arr) if isinstance(arr, list) else []
    return out


def _summarize_character_for_leg_b(char: dict[str, Any]) -> dict[str, Any]:
    """leg_b narrative 用：实体性 character 降级为标识性摘要（仅 id/name/role/status）。"""
    if not isinstance(char, dict):
        return {}
    state = char.get("current_state")
    status = None
    if isinstance(state, dict):
        status = state.get("status")
    return {
        "character_id": char.get("character_id"),
        "name": char.get("name"),
        "role": char.get("role"),
        "status": status,
        "summary_marker": "leg_b_narrative",
    }


def _summarize_location_for_leg_b(loc: Any, lid: str | None = None) -> dict[str, Any]:
    """leg_b narrative 用：location 降级为 ``{location_id, name}``。"""
    if isinstance(loc, dict):
        return {
            "location_id": loc.get("location_id") or lid,
            "name": loc.get("name"),
            "summary_marker": "leg_b_narrative",
        }
    return {"location_id": lid, "name": None, "summary_marker": "leg_b_narrative"}


def _summarize_faction_for_leg_b(fac: Any, fid: str | None = None) -> dict[str, Any]:
    if isinstance(fac, dict):
        return {
            "faction_id": fac.get("faction_id") or fid,
            "name": fac.get("name"),
            "summary_marker": "leg_b_narrative",
        }
    return {"faction_id": fid, "name": None, "summary_marker": "leg_b_narrative"}


def _summarize_world_rule_for_leg_b(rule: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(rule, dict):
        return {}
    return {
        "world_rule_id": rule.get("world_rule_id"),
        "name": rule.get("name"),
        "statement": rule.get("statement"),
        "summary_marker": "leg_b_narrative",
    }


def _trim_observer_input_for_leg(
    base_payload: dict[str, Any], leg: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """V3.1.1 O-3：按 leg 裁剪 observer_input.previous_state。

    输入 ``base_payload`` 是 :func:`build_observer_input` 输出的完整 payload（已含
    ``snapshot_mode='trimmed'`` 的 previous_state）；输出 ``(trimmed_payload, stats)``。

    公共部分（两腿都保留）：``chapter`` / ``director_plan_summary`` / ``config``
    （含 recent_event_ids 白名单）/ ``knowledge_permissions`` / ``previous_state_version`` /
    ``previous_state.snapshot_mode`` / ``previous_state.state_version`` /
    ``previous_state.recent_events`` / ``previous_state.world.current_time_in_story`` /
    ``previous_state.world.active_resources``。

    leg_a (entities) previous_state 保留：
    - ``characters``（已是 O-1 trimmed：touched 全量 / 其他仅 {character_id, name, facet}）
    - ``characters[].relationships``（touched 全量 / 其他仅摘要）
    - ``world.locations`` / ``world.factions``（touched value 全量 / 其他仅 {name}）
    - ``world.world_rules``（touched 全量 / 其他仅 {world_rule_id, name}）
    leg_a 移除：``events`` / ``hooks`` / ``debts``（leg_a 不读）。

    leg_b (narrative) previous_state 保留：
    - ``events``（O-1 窗口口径，原样）
    - ``hooks``（open 压缩口径，resolved 仅摘要，与 O-1 对齐）
    - ``debts``（open 压缩口径，resolved 仅摘要，与 O-1 对齐）
    leg_b 实体集合降级：
    - ``characters`` → ``{character_id, name, role, status}`` 摘要
    - ``world.locations`` → ``{location_id, name}`` 摘要
    - ``world.active_factions`` 或 ``world.factions`` → ``{faction_id, name}`` 摘要
    - ``world.world_rules`` → ``{world_rule_id, name, statement}`` 摘要（事件可能引用规则变化）

    stats 记录裁剪前后体积（按 leg 统计）：
    - ``leg`` / ``previous_state_bytes_before`` / ``previous_state_bytes_after``
    - ``characters_kept`` / ``locations_kept`` / ``factions_kept`` / ``world_rules_kept``
    - ``events_kept`` / ``hooks_kept`` / ``debts_kept``
    """
    if leg not in ("entities", "narrative"):
        raise ValueError(f"leg must be 'entities' or 'narrative', got {leg!r}")

    prev_state = base_payload.get("previous_state") or {}
    if not isinstance(prev_state, dict):
        prev_state = {}

    try:
        bytes_before = len(json.dumps(prev_state, ensure_ascii=False))
    except (TypeError, ValueError):
        bytes_before = 0

    stats: dict[str, Any] = {
        "leg": leg,
        "previous_state_bytes_before": bytes_before,
        "characters_kept": 0,
        "locations_kept": 0,
        "factions_kept": 0,
        "world_rules_kept": 0,
        "events_kept": 0,
        "hooks_kept": 0,
        "debts_kept": 0,
    }

    # 顶层元信息（两腿都保留）
    new_state: dict[str, Any] = {}
    for k in ("snapshot_mode", "state_version", "recent_events"):
        if k in prev_state:
            new_state[k] = prev_state[k]
    # 如果原 snapshot 没有 snapshot_mode 但 snapshot_mode='trimmed'，补一个标识
    if "snapshot_mode" not in new_state:
        new_state["snapshot_mode"] = (
            prev_state.get("snapshot_mode") or "trimmed"
        )

    if leg == "entities":
        # ---- characters（保留 O-1 裁剪后的形态）----
        chars_in = prev_state.get("characters") or []
        chars_out: list[dict[str, Any]] = []
        if isinstance(chars_in, list):
            for c in chars_in:
                if isinstance(c, dict):
                    chars_out.append(c)
        stats["characters_kept"] = len(chars_out)
        new_state["characters"] = chars_out

        # ---- world（保留 locations / factions / world_rules；current_time 等不动）----
        world_in = prev_state.get("world") or {}
        world_out: dict[str, Any] = {}
        if isinstance(world_in, dict):
            for k in ("current_time_in_story", "active_resources"):
                if k in world_in:
                    world_out[k] = world_in[k]

            locs_in = world_in.get("locations") or {}
            if isinstance(locs_in, dict):
                world_out["locations"] = dict(locs_in)
                stats["locations_kept"] = len(locs_in)
            elif isinstance(locs_in, list):
                # list 形态：每条带 location_id 的项原样保留（与 O-1 兼容）
                world_out["locations"] = [
                    x for x in locs_in if isinstance(x, dict)
                ]
                stats["locations_kept"] = len(world_out["locations"])

            facs_in = world_in.get("factions") or {}
            if isinstance(facs_in, dict):
                world_out["factions"] = dict(facs_in)
                stats["factions_kept"] = len(facs_in)
            elif isinstance(facs_in, list):
                world_out["factions"] = [
                    x for x in facs_in if isinstance(x, dict)
                ]
                stats["factions_kept"] = len(world_out["factions"])
            # 兼容：有的 snapshot 把 factions 放在 active_factions
            active_facs_in = world_in.get("active_factions")
            if active_facs_in is not None and "factions" not in world_out:
                if isinstance(active_facs_in, list):
                    world_out["factions"] = [
                        x for x in active_facs_in if isinstance(x, dict)
                    ]
                    stats["factions_kept"] = len(world_out["factions"])

            rules_in = world_in.get("world_rules") or []
            if isinstance(rules_in, list):
                world_out["world_rules"] = list(rules_in)
                stats["world_rules_kept"] = len(rules_in)

        new_state["world"] = world_out
        # 明确移除 leg_a 不读的集合（即便原 snapshot_mode=trimmed 也移除——保证 payload 字节级一致）
        # events / hooks / debts 全部置空 list，避免 observer 误读
        new_state["events"] = {}
        new_state["hooks"] = []
        new_state["debts"] = []

    else:  # leg == "narrative"
        # ---- 实体集合降级为标识性摘要 ----
        chars_in = prev_state.get("characters") or []
        chars_out: list[dict[str, Any]] = []
        if isinstance(chars_in, list):
            for c in chars_in:
                chars_out.append(_summarize_character_for_leg_b(c))
        stats["characters_kept"] = len(chars_out)
        new_state["characters"] = chars_out

        world_in = prev_state.get("world") or {}
        world_out: dict[str, Any] = {}
        if isinstance(world_in, dict):
            for k in ("current_time_in_story", "active_resources"):
                if k in world_in:
                    world_out[k] = world_in[k]

            locs_in = world_in.get("locations") or {}
            if isinstance(locs_in, dict):
                world_out["locations"] = {
                    lid: _summarize_location_for_leg_b(lval, lid)
                    for lid, lval in locs_in.items()
                }
                stats["locations_kept"] = len(locs_in)
            elif isinstance(locs_in, list):
                world_out["locations"] = [
                    _summarize_location_for_leg_b(
                        x, x.get("location_id") if isinstance(x, dict) else None
                    )
                    for x in locs_in if isinstance(x, dict)
                ]
                stats["locations_kept"] = len(world_out["locations"])

            facs_in = world_in.get("factions") or {}
            if isinstance(facs_in, dict):
                world_out["factions"] = {
                    fid: _summarize_faction_for_leg_b(fval, fid)
                    for fid, fval in facs_in.items()
                }
                stats["factions_kept"] = len(facs_in)
            elif isinstance(facs_in, list):
                world_out["factions"] = [
                    _summarize_faction_for_leg_b(
                        x, x.get("faction_id") if isinstance(x, dict) else None
                    )
                    for x in facs_in if isinstance(x, dict)
                ]
                stats["factions_kept"] = len(world_out["factions"])
            active_facs_in = world_in.get("active_factions")
            if active_facs_in is not None and "factions" not in world_out:
                if isinstance(active_facs_in, list):
                    world_out["factions"] = [
                        _summarize_faction_for_leg_b(
                            x, x.get("faction_id") if isinstance(x, dict) else None
                        )
                        for x in active_facs_in if isinstance(x, dict)
                    ]
                    stats["factions_kept"] = len(world_out["factions"])

            rules_in = world_in.get("world_rules") or []
            if isinstance(rules_in, list):
                world_out["world_rules"] = [
                    _summarize_world_rule_for_leg_b(r)
                    for r in rules_in if isinstance(r, dict)
                ]
                stats["world_rules_kept"] = len(world_out["world_rules"])

        new_state["world"] = world_out

        # ---- events / hooks / debts 原样保留（O-1 口径）----
        events_in = prev_state.get("events")
        if isinstance(events_in, dict):
            new_state["events"] = events_in
            stats["events_kept"] = len(events_in)
        else:
            new_state["events"] = {}

        hooks_in = prev_state.get("hooks") or []
        if isinstance(hooks_in, list):
            new_state["hooks"] = list(hooks_in)
            stats["hooks_kept"] = len(hooks_in)
        else:
            new_state["hooks"] = []

        debts_in = prev_state.get("debts") or []
        if isinstance(debts_in, list):
            new_state["debts"] = list(debts_in)
            stats["debts_kept"] = len(debts_in)
        else:
            new_state["debts"] = []

    try:
        bytes_after = len(json.dumps(new_state, ensure_ascii=False))
    except (TypeError, ValueError):
        bytes_after = 0
    stats["previous_state_bytes_after"] = bytes_after
    stats["previous_state_bytes_delta"] = bytes_before - bytes_after

    trimmed = dict(base_payload)
    trimmed["previous_state"] = new_state
    # 按腿的 stats 写到 payload 顶层，命名 leg_<x>_snapshot_trim_stats
    # 让两腿各自读各自的 stats，便于后续观测与测试断言。
    trimmed[f"snapshot_trim_stats_leg_{'a' if leg == 'entities' else 'b'}"] = stats
    return trimmed, stats


def _pick_retry_mock(mock_script: Any, leg: str) -> Any:
    """按 leg 选取「下一条」mock 响应；非 list 模式保持原样。

    与 _observer_node 首次调用的口径对齐：list[str] 模式按 leg 过滤（取下一条
    元素再按 scope 过滤），让 mock 测试可以分别控制双腿的首次 / 重试响应。
    """
    if mock_script is None or callable(mock_script) or isinstance(mock_script, str):
        # 字符串 / callable / None：透传（重试仅靠 _retry_hint 修正）
        return mock_script
    if not isinstance(mock_script, list) or len(mock_script) <= 1:
        return mock_script
    # list 模式 + 多条：弹下一条以让 MockProvider 返回不同响应
    picked = mock_script[1]
    retry_list = [picked] if isinstance(picked, str) else picked
    return _filter_mock_for_leg(retry_list, leg)
