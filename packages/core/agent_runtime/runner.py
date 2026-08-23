"""Agent Runner（Sprint 3）。

职责：
- ``run_agent(db_path, agent_name, input_payload, run_id, node_run_id, expected, mock_script)``
  执行一个 agent：取 prompt → 取 model config → 调用 provider → 解析 JSON → 契约校验 →
  落 ``ai_call_logs``。
- ``create_adhoc_run(db_path, name)``：确保 ``workflows`` 有 adhoc 行，插一行 ``workflow_runs``，
  返回 ``run_id``（用于测试与手工触发；S4 正式工作流复用同一 ``ai_call_logs`` FK 约束）。

设计要点（与任务书对齐）：
- 输入 payload 全部 ``json.dumps(ensure_ascii=False)`` 作为 user message。
- Provider 调用最多 2 次（首次 + 1 次重试）；重试机制：
  1. 解析失败 / 契约校验失败 → 拼一条「上次输出无法解析/不合规：<错误>。请只输出合法 JSON。」
     追加到 user 末尾，再次调用。
  2. 重试仍失败 → 抛 :class:`AgentOutputError`（由 router 转 502/500）。
- Mock 测试通道：``mock_script`` 非空时跳过 :class:`ModelRouter`，直接构造 :class:`MockProvider`
  并注入脚本；用于测试「先坏后好」「两次都坏」等场景。
- ``ai_call_logs`` 落库：调用次数写 ``retry_count``（0 / 1）；成功后写 ``output_json`` / ``token_usage`` /
  ``latency_ms``；失败时 ``error`` 字段写最后一次错误。
- ``input_context_ids_json``：从 input_payload 里挑 ``*_id`` 键值，去重、截断到 100 条。

契约校验三档（agent-contracts §3.2 / §4.2 / §5.2）：
- ``expected == "director"`` → 必须含 ``schema_version == "director-plan.v1"``。
- ``expected == "writer"`` → 必须含 ``schema_version == "writer-output.v1"`` + ``prose`` + ``self_report``。
- ``expected == "observer"`` → 顶层必须恰为 7 个 change 数组键，不得含 10 个元信息字段。
- ``expected is None`` → 仅要求合法 JSON。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso
from packages.core.model_router import MockProvider, ModelRouter, capability_for

from .exceptions import AgentOutputError
from .prompts import PromptRegistry
from .structured_output import extract_json, validate_contract

_ID_KEY_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*_id$")
_RETRY_HINT = "\n\n[System note] 上次输出无法解析/不合规：{err}。请只输出合法 JSON，不要附加解释。"
_MAX_CONTEXT_IDS = 100


def _collect_context_ids(payload: dict[str, Any]) -> list[str]:
    """收集 ``*_id`` 键的值，去重并截断到 100。"""
    ids: list[str] = []
    seen: set[str] = set()
    for k, v in payload.items():
        if not _ID_KEY_RE.match(k):
            continue
        if isinstance(v, str) and v and v not in seen:
            ids.append(v)
            seen.add(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str) and item and item not in seen:
                    ids.append(item)
                    seen.add(item)
        if len(ids) >= _MAX_CONTEXT_IDS:
            break
    return ids[:_MAX_CONTEXT_IDS]


def _ensure_workflow(conn: sqlite3.Connection, name: str) -> str:
    """确保 ``workflows`` 表有 ``name`` 行；返回 workflow_id。"""
    row = conn.execute(
        "SELECT workflow_id FROM workflows WHERE name = ?", (name,)
    ).fetchone()
    if row is not None:
        return row["workflow_id"]
    wf_id = new_id("wf")
    now = now_iso()
    conn.execute(
        """
        INSERT INTO workflows (workflow_id, name, version, definition_json, created_at, updated_at)
        VALUES (?, ?, 'v1', '{}', ?, ?)
        """,
        (wf_id, name, now, now),
    )
    return wf_id


def create_adhoc_run(db_path: Path | str, name: str = "adhoc") -> str:
    """确保 ``workflows.name='adhoc'`` 行存在，插入一行 ``workflow_runs``（status='RUNNING'）。

    返回 ``run_id``。Sprint 3 用于测试 / 手工触发；Sprint 4 正式工作流复用同一 FK 约束。
    """
    conn = get_connection(db_path)
    try:
        wf_id = _ensure_workflow(conn, name)
        run_id = new_id("wfr")
        now = now_iso()
        conn.execute(
            """
            INSERT INTO workflow_runs
                (run_id, workflow_id, chapter_id, status, current_node,
                 checkpoint_json, error, retry_count, started_at, ended_at)
            VALUES (?, ?, NULL, 'RUNNING', NULL, '{}', NULL, 0, ?, NULL)
            """,
            (run_id, wf_id, now),
        )
        conn.commit()
        return run_id
    finally:
        conn.close()


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _record_call(
    db_path: Path | str,
    *,
    call_id: str,
    run_id: str,
    node_run_id: str | None,
    agent_id: str | None,
    model_id: str | None,
    prompt_version: str | None,
    input_context_ids: list[str],
    output: dict[str, Any] | None,
    token_usage: dict[str, int] | None,
    latency_ms: int,
    error: str | None,
    retry_count: int,
) -> None:
    """写 ``ai_call_logs`` 行（不论成败；测试可断言行存在）。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO ai_call_logs (
                call_id, run_id, node_run_id, agent_id, model_id, prompt_version,
                input_context_ids_json, output_json, token_usage_json, latency_ms,
                cost, error, retry_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                call_id,
                run_id,
                node_run_id,
                agent_id,
                model_id,
                prompt_version,
                _dump_json(input_context_ids),
                _dump_json(output) if output is not None else None,
                _dump_json(token_usage) if token_usage is not None else None,
                latency_ms,
                error,
                retry_count,
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _update_workflow_run(
    db_path: Path | str,
    run_id: str,
    *,
    status: str = "COMPLETED",
    error: str | None = None,
) -> None:
    """把 ``workflow_runs`` 行收尾（COMPLETED / FAILED）。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            UPDATE workflow_runs
            SET status = ?, ended_at = ?, error = ?
            WHERE run_id = ?
            """,
            (status, now_iso(), error, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def _get_agent_id(conn: sqlite3.Connection, agent_name: str) -> str | None:
    row = conn.execute("SELECT agent_id FROM agents WHERE name = ?", (agent_name,)).fetchone()
    return row["agent_id"] if row else None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def run_agent(
    db_path: Path | str,
    agent_name: str,
    input_payload: dict[str, Any],
    run_id: str,
    *,
    node_run_id: str | None = None,
    expected: str | None = None,
    mock_script: Any = None,
) -> dict[str, Any]:
    """执行一次 agent 调用。

    返回解析后的 JSON payload（dict）。
    抛出：
    - :class:`PromptNotFoundError` ——无 ACTIVE prompt。
    - :class:`ModelNotConfiguredError`（来自 :class:`ModelRouter`）——无 model config。
    - :class:`ProviderError`（来自 provider）——HTTP / 解析失败（不重试，因属 Provider 错误）。
    - :class:`AgentOutputError` ——解析 / 契约重试 1 次仍失败。
    """
    db_path = str(db_path)
    registry = PromptRegistry(db_path)
    prompt_id, prompt_version, prompt_content = registry.get_active_prompt(agent_name)

    # 决定 provider
    if mock_script is not None:
        provider = MockProvider(scripted=mock_script)
        config_row: dict | None = None
        capability = capability_for(agent_name)
    else:
        capability = capability_for(agent_name)
        config_row = ModelRouter(db_path).resolve(capability)
        provider = ModelRouter(db_path).get_provider(config_row)

    model_id = (
        f"{config_row['provider']}/{config_row['model']}"
        if config_row
        else "mock/mock"
    )

    # agent_id（落 ai_call_logs 用）
    conn = get_connection(db_path)
    try:
        agent_id = _get_agent_id(conn, agent_name)
    finally:
        conn.close()

    context_ids = _collect_context_ids(input_payload)

    user_payload_text = _dump_json(input_payload)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": prompt_content},
        {"role": "user", "content": user_payload_text},
    ]

    # 调用 + 重试循环
    start = time.monotonic()
    retry_count = 0
    last_error: str | None = None
    parsed: dict[str, Any] | None = None
    token_usage: dict[str, int] | None = None
    raw_text: str | None = None
    output_log: dict[str, Any] | None = None

    for attempt in range(2):  # 0 = 首次，1 = 1 次重试
        if attempt > 0:
            retry_count = 1
            # 重试：在 user 末尾追加提示，再次调用
            messages[1]["content"] = user_payload_text + _RETRY_HINT.format(err=last_error or "无法解析")
        try:
            completion = provider.complete(messages)
        except Exception as exc:  # noqa: BLE001
            # Provider 失败：不重试，直接记日志并抛
            latency = int((time.monotonic() - start) * 1000)
            _record_call(
                db_path,
                call_id=new_id("aic"),
                run_id=run_id,
                node_run_id=node_run_id,
                agent_id=agent_id,
                model_id=model_id,
                prompt_version=prompt_version,
                input_context_ids=context_ids,
                output=None,
                token_usage=None,
                latency_ms=latency,
                error=f"provider error: {exc}",
                retry_count=0,
            )
            _update_workflow_run(db_path, run_id, status="FAILED", error=str(exc))
            raise

        raw_text = completion.get("text") or ""
        token_usage = completion.get("usage") or {"prompt": 0, "completion": 0, "total": 0}
        try:
            parsed = extract_json(raw_text)
        except AgentOutputError as exc:
            last_error = str(exc)
            continue  # 进入重试
        try:
            validate_contract(expected, parsed)
        except AgentOutputError as exc:
            last_error = str(exc)
            continue  # 进入重试
        # 成功
        output_log = parsed
        break
    else:
        # 两次都失败
        latency = int((time.monotonic() - start) * 1000)
        _record_call(
            db_path,
            call_id=new_id("aic"),
            run_id=run_id,
            node_run_id=node_run_id,
            agent_id=agent_id,
            model_id=model_id,
            prompt_version=prompt_version,
            input_context_ids=context_ids,
            output=None,
            token_usage=token_usage,
            latency_ms=latency,
            error=f"output invalid after retry: {last_error}",
            retry_count=retry_count,
        )
        _update_workflow_run(db_path, run_id, status="FAILED", error=last_error)
        raise AgentOutputError(
            f"agent {agent_name!r} output invalid after 1 retry: {last_error}",
            raw_output=raw_text,
        )

    # 成功落库
    latency = int((time.monotonic() - start) * 1000)
    _record_call(
        db_path,
        call_id=new_id("aic"),
        run_id=run_id,
        node_run_id=node_run_id,
        agent_id=agent_id,
        model_id=model_id,
        prompt_version=prompt_version,
        input_context_ids=context_ids,
        output=output_log,
        token_usage=token_usage,
        latency_ms=latency,
        error=None,
        retry_count=retry_count,
    )
    _update_workflow_run(db_path, run_id, status="COMPLETED")
    return output_log  # type: ignore[return-value]


__all__ = ["run_agent", "create_adhoc_run"]
