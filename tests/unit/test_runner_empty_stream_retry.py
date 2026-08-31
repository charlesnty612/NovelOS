"""agent runner「空流自动重试」测试。

吸收上游 Provider（MiniMax / DeepSeek 等）间歇性零 content 块抖动：
- ``ProviderError`` 消息含 ``"empty stream: no content chunks received"`` 时自动重试 1 次；
- messages 不变（不属于输出解析问题，不追加 ``_RETRY_HINT``）；
- 第二次仍空流 → 按 Provider 失败路径抛错，错误消息注明已重试；
- 其它 ProviderError（HTTP 500 等）→ 不触发空流重试，首次即失败。

与既有 ``retry_count`` 语义正交：retry_count 仍只计 output-invalid 重试，
空流重试成功 → ``ai_call_logs.error`` 追加 ``warn: empty stream retried``，
``retry_count == 0``，不破坏既有断言与指标。

依据：packages/core/agent_runtime/runner.py 顶部 docstring + 主循环。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from packages.core.agent_runtime.runner import create_adhoc_run, run_agent
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.model_router.exceptions import ProviderError
from packages.core.model_router.providers import MockProvider


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prepare_db(tmp_path: Path) -> Settings:
    """最小可用 DB：注册 agent + ACTIVE prompt；不消费 model_configs（mock 路径）。"""
    settings = Settings(db_path=str(tmp_path / "novelos.db"))
    apply_migrations(settings.db_path)
    return settings


def _seed_observer(tmp_path: Path, db_path: str) -> None:
    """注册一个最小可用的 observer agent + ACTIVE prompt。"""
    conn = get_connection(db_path)
    try:
        agent_id = "agn_observer_empty_stream"
        conn.execute(
            """
            INSERT OR IGNORE INTO agents (agent_id, name, role, config_json, created_at, updated_at)
            VALUES (?, 'observer', 'reasoning', '{}', ?, ?)
            """,
            (agent_id, _now(), _now()),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO prompts (
                prompt_id, agent_id, version, status, content, created_at, updated_at
            ) VALUES (
                'prm_obs_empty_stream_v1', ?, 'observer:v1', 'ACTIVE',
                'You are observer. Output JSON only.', ?, ?
            )
            """,
            (agent_id, _now(), _now()),
        )
        conn.commit()
    finally:
        conn.close()


_VALID_OBSERVER_JSON = {
    "character_changes": [],
    "world_changes": [],
    "relationship_changes": [],
    "new_events": [],
    "resolved_hooks": [],
    "new_hooks": [],
    "debt_changes": [],
}


def _build_valid_completion(_self, _messages, _params=None) -> dict:
    return {
        "text": json.dumps(_VALID_OBSERVER_JSON, ensure_ascii=False),
        "usage": {"prompt": 10, "completion": 20, "total": 30},
    }


def _read_ai_call_log(db_path: str, run_id: str) -> dict | None:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT error, retry_count FROM ai_call_logs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _read_run_status(db_path: str, run_id: str) -> tuple[str, str | None]:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT status, error FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    return (row["status"], row["error"]) if row else ("?", None)


# ---------------------------------------------------------------------------
# 核心场景
# ---------------------------------------------------------------------------


def test_empty_stream_first_then_success(
    tmp_path: Path, monkeypatch,
):
    """case (a): mock provider 首次抛空流 ProviderError、第二次返回合法 JSON
    → 整体成功、ai_call_logs.error 含 "warn: empty stream retried"、retry_count=0。
    """
    settings = _prepare_db(tmp_path)
    db_path = settings.db_path
    _seed_observer(tmp_path, db_path)

    call_count = {"n": 0}

    def _patched_complete(self, messages, params=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 首次：抛空流 ProviderError（与 providers.py L407-430 同款消息）
            raise ProviderError("minimax", "empty stream: no content chunks received")
        # 第二次：合法 observer JSON
        return _build_valid_completion(self, messages, params)

    monkeypatch.setattr(
        "packages.core.model_router.providers.MockProvider.complete", _patched_complete
    )

    run_id = create_adhoc_run(db_path)
    out = run_agent(
        db_path,
        "observer",
        {"chapter_id": "ch_xxx"},
        run_id,
        expected="observer",
        mock_script=lambda i: "{}",
    )
    # 解析后结构正确
    assert out["character_changes"] == []
    assert out["world_changes"] == []

    # 调了两次 provider（首次空流 + 重试成功）
    assert call_count["n"] == 2

    # ai_call_logs.error 含 warn: empty stream retried，retry_count == 0
    row = _read_ai_call_log(db_path, run_id)
    assert row is not None
    assert row["retry_count"] == 0
    assert row["error"] is not None
    assert "warn: empty stream retried" in row["error"]

    # workflow_runs 收尾为 COMPLETED（成功路径不应残留 FAILED）
    status, err = _read_run_status(db_path, run_id)
    assert status == "COMPLETED"
    assert err is None


def test_empty_stream_twice_raises_with_retry_annnotation(
    tmp_path: Path, monkeypatch,
):
    """case (b): 两次都空流 → 抛 ProviderError、run 终态 FAILED、错误消息含「已重试过」标注。"""
    settings = _prepare_db(tmp_path)
    db_path = settings.db_path
    _seed_observer(tmp_path, db_path)

    call_count = {"n": 0}

    def _patched_complete(self, messages, params=None):
        call_count["n"] += 1
        raise ProviderError("minimax", "empty stream: no content chunks received")

    monkeypatch.setattr(
        "packages.core.model_router.providers.MockProvider.complete", _patched_complete
    )

    run_id = create_adhoc_run(db_path)
    with pytest.raises(ProviderError) as exc_info:
        run_agent(
            db_path,
            "observer",
            {"chapter_id": "ch_xxx"},
            run_id,
            expected="observer",
            mock_script=lambda i: "{}",
        )
    msg = str(exc_info.value)
    # 原始 marker 仍存在 + 已重试过标注
    assert "empty stream: no content chunks received" in msg
    assert "retried once" in msg or "已重试过" in msg or "still empty" in msg

    # provider 调用恰好 2 次（首次空流 + 重试仍是空流 → 走失败路径）
    assert call_count["n"] == 2

    # ai_call_logs.error 记录 ProviderError 失败
    row = _read_ai_call_log(db_path, run_id)
    assert row is not None
    assert row["retry_count"] == 0
    assert "provider error:" in (row["error"] or "")
    assert "empty stream" in (row["error"] or "")

    # run 收尾 FAILED，错误消息含 marker + 已重试标注
    status, err = _read_run_status(db_path, run_id)
    assert status == "FAILED"
    assert err is not None
    assert "empty stream" in err


def test_non_empty_stream_provider_error_no_retry(
    tmp_path: Path, monkeypatch,
):
    """case (c): 非空流 ProviderError（如 HTTP 500）→ 不触发空流重试、首次即失败。"""
    settings = _prepare_db(tmp_path)
    db_path = settings.db_path
    _seed_observer(tmp_path, db_path)

    call_count = {"n": 0}

    def _patched_complete(self, messages, params=None):
        call_count["n"] += 1
        raise ProviderError("openai", "HTTP 500: internal server error", status_code=500)

    monkeypatch.setattr(
        "packages.core.model_router.providers.MockProvider.complete", _patched_complete
    )

    run_id = create_adhoc_run(db_path)
    with pytest.raises(ProviderError) as exc_info:
        run_agent(
            db_path,
            "observer",
            {"chapter_id": "ch_xxx"},
            run_id,
            expected="observer",
            mock_script=lambda i: "{}",
        )
    # 非空流 ProviderError：消息不含「empty stream」marker，且不含「已重试过」标注
    msg = str(exc_info.value)
    assert "HTTP 500" in msg
    assert "empty stream" not in msg
    assert "retried once" not in msg and "still empty" not in msg

    # provider 调用只调 1 次（首次即失败，不进空流重试）
    assert call_count["n"] == 1

    # run 收尾 FAILED
    status, _err = _read_run_status(db_path, run_id)
    assert status == "FAILED"


def test_empty_stream_retry_success_but_output_invalid_still_triggers_attempt_retry(
    tmp_path: Path, monkeypatch,
):
    """case (d): 空流重试成功后 output 仍须过契约校验。空流重试成功 → 第二次 attempt
    拿到文本但非法 JSON → 照常走 output-invalid 重试路径（_RETRY_HINT 追加到 user
    末尾），第二次 attempt 再次空流 → 失败。验证两机制独立不互相挤占。

    等价路径：attempt 0 内 provider 调用顺序为「空流 → 重试返回合法 JSON」；
    模拟「空流重试成功但文本非法」需 mock 在 attempt=1 路径上也走空流（attempt
    循环外层 + 空流小循环内层）。
    """
    settings = _prepare_db(tmp_path)
    db_path = settings.db_path
    _seed_observer(tmp_path, db_path)

    call_count = {"n": 0}

    def _patched_complete(self, messages, params=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # attempt 0 首次：空流
            raise ProviderError("minimax", "empty stream: no content chunks received")
        if call_count["n"] == 2:
            # attempt 0 空流重试成功 → 返回非法 JSON（不过契约 → 触发外层 attempt 重试）
            return {
                "text": "not a json {{{ broken",
                "usage": {"prompt": 10, "completion": 5, "total": 15},
            }
        # attempt 1 首次：空流
        raise ProviderError("minimax", "empty stream: no content chunks received")

    monkeypatch.setattr(
        "packages.core.model_router.providers.MockProvider.complete", _patched_complete
    )

    run_id = create_adhoc_run(db_path)
    # 调用顺序：
    #   1. attempt 0 首次 → 空流
    #   2. attempt 0 空流小循环重试 → 返回非法 JSON（output-invalid）
    #   3. attempt 1 首次 → 空流
    #   4. attempt 1 空流小循环重试 → 仍空流 → 失败路径
    with pytest.raises(ProviderError) as exc_info:
        run_agent(
            db_path,
            "observer",
            {"chapter_id": "ch_xxx"},
            run_id,
            expected="observer",
            mock_script=lambda i: "{}",
        )
    assert call_count["n"] == 4

    msg = str(exc_info.value)
    # 第二次空流（attempt=1 内）→ 错误标注「已重试过」
    assert "empty stream" in msg
    assert "retried once" in msg or "still empty" in msg

    # ai_call_logs.error: output invalid 失败（不是 provider error，
    # 因为最后一次失败是外层 attempt 抛 ProviderError 后被认定为 provider 错误）
    # —— 实际路径：attempt=1 内空流小循环第二次空流 → 走 provider 失败路径
    row = _read_ai_call_log(db_path, run_id)
    assert row is not None
    assert "provider error:" in (row["error"] or "")
    assert "empty stream" in (row["error"] or "")
