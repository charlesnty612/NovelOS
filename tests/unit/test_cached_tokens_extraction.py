"""V3.5 cached_tokens 观测测试。

覆盖点（providers.py + runner.py）：
1. MockProvider 接受 ``usage`` 注入：``complete()`` 透传 usage 到 runner。
2. runner.py 无改动即把整段 usage 落到 ``ai_call_logs.token_usage_json``——
   含 ``cached_tokens`` 时 JSON 列含此 key；不含时此 key 不出现。
3. provider 抽象：OpenAI 兼容路径从 ``usage.prompt_tokens_details.cached_tokens``
   提取；缺省 / None / 非 int → 不下发 key。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from packages.core.agent_runtime.runner import create_adhoc_run, run_agent
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection
from packages.core.model_router.providers import (
    MockProvider,
    OpenAICompatibleProvider,
)

# ---------------------------------------------------------------------------
# MockProvider usage 注入（纯函数）
# ---------------------------------------------------------------------------


def test_mock_provider_default_usage_is_zero():
    """缺省 ``usage``：MockProvider.complete 返回 ``{prompt:0, completion:0, total:0}``，
    不含 ``cached_tokens``。"""
    p = MockProvider(scripted=["hello"])
    out = p.complete([{"role": "user", "content": "x"}])
    assert out["text"] == "hello"
    assert out["usage"] == {"prompt": 0, "completion": 0, "total": 0}
    assert "cached_tokens" not in out["usage"]


def test_mock_provider_inject_usage_with_cached_tokens():
    """显式 ``usage=...``：MockProvider.complete 透传（深 copy），cached_tokens 出现。"""
    usage = {"prompt": 100, "completion": 50, "total": 150, "cached_tokens": 80}
    p = MockProvider(scripted=["ok"], usage=usage)
    out = p.complete([{"role": "user", "content": "x"}])
    assert out["usage"] == usage
    # 修改返回 dict 不影响下次调用（防御性 copy）
    out["usage"]["cached_tokens"] = 0
    out2 = p.complete([{"role": "user", "content": "x"}])
    assert out2["usage"]["cached_tokens"] == 80


def test_mock_provider_usage_is_none_treated_as_default():
    """``usage=None``/缺省等价 default usage。"""
    p = MockProvider(scripted=["ok"], usage=None)
    out = p.complete([{"role": "user", "content": "x"}])
    assert out["usage"] == {"prompt": 0, "completion": 0, "total": 0}


# ---------------------------------------------------------------------------
# OpenAI 兼容 provider 抽 cached_tokens 单元（注入 httpx.MockTransport）
# ---------------------------------------------------------------------------


def _sse_response(*chunks: str) -> httpx.Response:
    """把若干 ``data:`` 行拼成 SSE 响应体（含末尾 ``data: [DONE]``）。"""
    body = "\n\n".join(f"data: {c}" for c in chunks) + "\n\ndata: [DONE]\n\n"
    return httpx.Response(
        200,
        text=body,
        headers={"Content-Type": "text/event-stream"},
    )


def test_openai_compatible_provider_extracts_cached_tokens():
    """OpenAI 标准响应 ``usage.prompt_tokens_details.cached_tokens`` → 透传到 usage dict。"""
    # 构造一个 httpx MockTransport，拦截 POST /chat/completions
    import httpx

    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return _sse_response(
            json.dumps({"choices": [{"delta": {"content": "hello"}}]}),
            json.dumps(
                {
                    "choices": [{"delta": {}}],
                    "usage": {
                        "prompt_tokens": 200,
                        "completion_tokens": 50,
                        "total_tokens": 250,
                        "prompt_tokens_details": {"cached_tokens": 173},
                    },
                }
            ),
        )

    transport = httpx.MockTransport(_handler)
    client = httpx.Client(transport=transport)
    p = OpenAICompatibleProvider(
        base_url="http://example.com", api_key="k", model="m1", client=client
    )
    out = p.complete(
        [{"role": "user", "content": "hi"}],
    )
    usage = out["usage"]
    assert usage["prompt"] == 200
    assert usage["completion"] == 50
    assert usage["total"] == 250
    assert usage["cached_tokens"] == 173


def test_openai_compatible_provider_no_prompt_details():
    """响应缺 prompt_tokens_details：usage dict 不下发 cached_tokens（与现有 schema 兼容）。"""
    import httpx

    def _handler(request: httpx.Request) -> httpx.Response:
        return _sse_response(
            json.dumps({"choices": [{"delta": {"content": "hi"}}]}),
            json.dumps(
                {
                    "choices": [{"delta": {}}],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 30,
                        "total_tokens": 130,
                    },
                }
            ),
        )

    transport = httpx.MockTransport(_handler)
    client = httpx.Client(transport=transport)
    p = OpenAICompatibleProvider(
        base_url="http://example.com", api_key="k", model="m1", client=client
    )
    out = p.complete([{"role": "user", "content": "hi"}])
    usage = out["usage"]
    assert usage == {"prompt": 100, "completion": 30, "total": 130}
    assert "cached_tokens" not in usage


def test_openai_compatible_provider_cached_tokens_zero_omitted():
    """cached_tokens=0：0 不下发（避免误判；既有 schema 不含此 key）。"""
    import httpx

    def _handler(request: httpx.Request) -> httpx.Response:
        return _sse_response(
            json.dumps({"choices": [{"delta": {"content": "hi"}}]}),
            json.dumps(
                {
                    "choices": [{"delta": {}}],
                    "usage": {
                        "prompt_tokens": 50,
                        "completion_tokens": 10,
                        "total_tokens": 60,
                        "prompt_tokens_details": {"cached_tokens": 0},
                    },
                }
            ),
        )

    transport = httpx.MockTransport(_handler)
    client = httpx.Client(transport=transport)
    p = OpenAICompatibleProvider(
        base_url="http://example.com", api_key="k", model="m1", client=client
    )
    out = p.complete([{"role": "user", "content": "hi"}])
    assert "cached_tokens" not in out["usage"]


def test_openai_compatible_provider_cached_tokens_invalid_omitted():
    """cached_tokens 非 int（如字符串"abc"）：兜底不写 key。"""
    import httpx

    def _handler(request: httpx.Request) -> httpx.Response:
        return _sse_response(
            json.dumps({"choices": [{"delta": {"content": "hi"}}]}),
            json.dumps(
                {
                    "choices": [{"delta": {}}],
                    "usage": {
                        "prompt_tokens": 50,
                        "completion_tokens": 10,
                        "total_tokens": 60,
                        "prompt_tokens_details": {"cached_tokens": "abc"},
                    },
                }
            ),
        )

    transport = httpx.MockTransport(_handler)
    client = httpx.Client(transport=transport)
    p = OpenAICompatibleProvider(
        base_url="http://example.com", api_key="k", model="m1", client=client
    )
    out = p.complete([{"role": "user", "content": "hi"}])
    assert "cached_tokens" not in out["usage"]


# ---------------------------------------------------------------------------
# runner 端到端：mock 注入 usage → ai_call_logs.token_usage_json 含 cached_tokens
# ---------------------------------------------------------------------------


def _prepare_db(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return settings


def test_runner_writes_cached_tokens_to_ai_call_logs(tmp_path: Path, monkeypatch):
    """runner 在 mock_script 路径下：MockProvider usage 注入后，写入 ai_call_logs.token_usage_json。"""

    # 注册 ACTIVE observer prompt（无 ACTIVE prompt 时 runner 抛错）

    settings = _prepare_db(tmp_path)
    db_path = settings.db_path
    # 注入一个 agent：observer 直接写一行（最低可用）
    conn = get_connection(db_path)
    try:
        agent_id = "agn_observer_cached"
        conn.execute(
            """
            INSERT OR IGNORE INTO agents (agent_id, name, role, config_json, created_at, updated_at)
            VALUES (?, 'observer', 'reasoning', '{}', ?, ?)
            """,
            (agent_id, _now(), _now()),
        )
        # 给一个 ACTIVE prompt：内容随意（mock 路径不消费）
        conn.execute(
            """
            INSERT OR REPLACE INTO prompts (
                prompt_id, agent_id, version, status, content, created_at, updated_at
            ) VALUES (
                'prm_obs_cached_v1', ?, 'observer:v1', 'ACTIVE',
                'You are observer. Output JSON only.', ?, ?
            )
            """,
            (agent_id, _now(), _now()),
        )
        conn.commit()
    finally:
        conn.close()

    # monkey-patch MockProvider 以注入 usage
    usage_inject = {
        "prompt": 200,
        "completion": 50,
        "total": 250,
        "cached_tokens": 175,
    }

    def _patched_complete(self, messages, params=None):
        # 复用原 MockProvider 的 scripted 文本生成逻辑；此处直接返回固定 JSON 文本
        return {
            "text": json.dumps(
                {
                    "character_changes": [],
                    "world_changes": [],
                    "relationship_changes": [],
                    "new_events": [],
                    "resolved_hooks": [],
                    "new_hooks": [],
                    "debt_changes": [],
                },
                ensure_ascii=False,
            ),
            "usage": dict(usage_inject),  # copy
        }

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
        # 提供 callable 触发 patched_complete，但此处 patched 已被 monkey-patch 接管
        mock_script=lambda i: "{}",
    )
    assert out["character_changes"] == []

    # 读 ai_call_logs 验 token_usage_json
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT token_usage_json FROM ai_call_logs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    usage = json.loads(row["token_usage_json"])
    assert usage["cached_tokens"] == 175
    assert usage["prompt"] == 200


def test_runner_no_cached_tokens_field_when_absent(tmp_path: Path, monkeypatch):
    """usage 不含 cached_tokens 时：ai_call_logs.token_usage_json 也不含此 key。"""


    settings = _prepare_db(tmp_path)
    db_path = settings.db_path
    conn = get_connection(db_path)
    try:
        agent_id = "agn_observer_no_cache"
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
                'prm_obs_nocache_v1', ?, 'observer:v1', 'ACTIVE',
                'You are observer.', ?, ?
            )
            """,
            (agent_id, _now(), _now()),
        )
        conn.commit()
    finally:
        conn.close()

    def _patched_complete(self, messages, params=None):
        return {
            "text": json.dumps(
                {
                    "character_changes": [],
                    "world_changes": [],
                    "relationship_changes": [],
                    "new_events": [],
                    "resolved_hooks": [],
                    "new_hooks": [],
                    "debt_changes": [],
                },
                ensure_ascii=False,
            ),
            "usage": {"prompt": 100, "completion": 20, "total": 120},  # 无 cached_tokens
        }

    monkeypatch.setattr(
        "packages.core.model_router.providers.MockProvider.complete", _patched_complete
    )

    run_id = create_adhoc_run(db_path)
    run_agent(
        db_path, "observer", {"chapter_id": "ch_xxx"}, run_id,
        expected="observer", mock_script=lambda i: "{}",
    )

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT token_usage_json FROM ai_call_logs WHERE run_id = ?", (run_id,),
        ).fetchone()
    finally:
        conn.close()
    usage = json.loads(row["token_usage_json"])
    assert "cached_tokens" not in usage
    assert usage == {"prompt": 100, "completion": 20, "total": 120}


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 前缀缓存稳定化：messages 顺序 & 无动态内容注入
# ---------------------------------------------------------------------------


def test_runner_messages_order_system_then_user_with_static_prompt(
    tmp_path: Path, monkeypatch,
):
    """V3.5 P0-2：messages[0] 是静态 system prompt 且字节级稳定（无 run_id/时间戳混入）。

    通过 env 开关观察 prompt_content 不被混改：mock_script 检查 messages 收到时
    的 system 内容在两次连续调用间是**字节级一致**。
    """
    settings = _prepare_db(tmp_path)
    db_path = settings.db_path
    conn = get_connection(db_path)
    try:
        agent_id = "agn_observer_order"
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
                'prm_obs_order_v1', ?, 'observer:v1', 'ACTIVE',
                'STATIC OBSERVER PROMPT. No dynamic content here.', ?, ?
            )
            """,
            (agent_id, _now(), _now()),
        )
        conn.commit()
    finally:
        conn.close()

    captured: list[list[dict]] = []

    def _patched_complete(self, messages, params=None):
        # 透传到外面，方便断言顺序与内容稳定性
        # 这里把 messages 深 copy 一份（避免下游修改）
        captured.append([{"role": m["role"], "content": m["content"]} for m in messages])
        return {
            "text": json.dumps(
                {
                    "character_changes": [],
                    "world_changes": [],
                    "relationship_changes": [],
                    "new_events": [],
                    "resolved_hooks": [],
                    "new_hooks": [],
                    "debt_changes": [],
                },
                ensure_ascii=False,
            ),
            "usage": {"prompt": 0, "completion": 0, "total": 0},
        }

    monkeypatch.setattr(
        "packages.core.model_router.providers.MockProvider.complete", _patched_complete
    )

    run_id = create_adhoc_run(db_path)
    # 调用两次：两次的 payload 故意不同（注入 dynamic content 到 payload），
    # 但 system prompt 必须保持完全相同。
    run_agent(
        db_path, "observer", {"chapter_id": "ch_a", "dynamic": "alpha"}, run_id,
        expected="observer", mock_script=lambda i: "{}",
    )
    # 二次调用走新的 run_id（同一 run_id 第二次会重新 reopen，但 prompt_content 不变）
    run_id_2 = create_adhoc_run(db_path)
    run_agent(
        db_path, "observer", {"chapter_id": "ch_b", "dynamic": "beta"}, run_id_2,
        expected="observer", mock_script=lambda i: "{}",
    )

    assert len(captured) == 2
    msgs_a, msgs_b = captured
    # 顺序：system 在前，user 在后
    assert msgs_a[0]["role"] == "system"
    assert msgs_a[1]["role"] == "user"
    assert msgs_b[0]["role"] == "system"
    assert msgs_b[1]["role"] == "user"
    # system 内容字节级稳定（与 prompt_content 一致，不混入 run_id / 时间戳）
    assert msgs_a[0]["content"] == msgs_b[0]["content"] == (
        "STATIC OBSERVER PROMPT. No dynamic content here."
    )
    # user 内容包含动态字段但属于「user 部分」——前缀缓存按 system 命中不受影响
    assert "alpha" in msgs_a[1]["content"]
    assert "beta" in msgs_b[1]["content"]
