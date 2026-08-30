"""OpenAI 兼容 provider 空流报错文案的可观测性修复测试。

目标：当 SSE 流式调用拿到零 content chunk 时，ProviderError 消息必须带
``finish_reason`` 线索。``finish_reason='length'`` 时更要点名根因
（输出预算被思考 token 耗尽）与修法（调大档案 params 的 max_tokens）。

依据：生产事故 deepseek 档案缺 max_tokens → 自适应思考 token 吃光预算 →
流式只吐思考块、零 content、``finish_reason='length'``，原报错
"empty stream: no content chunks received" 不含线索，运维难以直观看出根因。
"""

from __future__ import annotations

import json

import httpx
import pytest

from packages.core.model_router.exceptions import ProviderError
from packages.core.model_router.providers import OpenAICompatibleProvider


def _sse_response(*chunks: str) -> httpx.Response:
    """拼一段 SSE 响应体，结尾附 ``data: [DONE]``。"""
    body = "\n\n".join(f"data: {c}" for c in chunks) + "\n\ndata: [DONE]\n\n"
    return httpx.Response(
        200, text=body, headers={"Content-Type": "text/event-stream"},
    )


def _build_provider(handler) -> OpenAICompatibleProvider:
    """用 httpx.MockTransport 构造一个 OpenAI 兼容 provider 实例。"""
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return OpenAICompatibleProvider(
        base_url="http://example.com", api_key="k", model="m1", client=client,
    )


def test_empty_stream_with_finish_reason_length_message_mentions_max_tokens():
    """零 content + finish_reason='length' → ProviderError 消息含 ``length``、
    ``max_tokens`` 调大提示，与 ``structured_output.py`` 既有文案风格对齐。
    """
    def _handler(request: httpx.Request) -> httpx.Response:
        # 模拟 deepseek 事故签名：推理 token 占满预算，最后一个 chunk 给 length
        return _sse_response(
            json.dumps({"choices": [{"delta": {"reasoning_content": "thinking..."}}]}),
            json.dumps({"choices": [{"delta": {"reasoning_content": "more..."}, "finish_reason": "length"}]}),
            json.dumps({
                "choices": [{"delta": {}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 8192, "total_tokens": 8292},
            }),
        )

    p = _build_provider(_handler)
    with pytest.raises(ProviderError) as exc:
        p.complete([{"role": "user", "content": "hi"}])
    msg = str(exc.value)
    assert "finish_reason=length" in msg
    assert "max_tokens" in msg


def test_empty_stream_with_finish_reason_stop_message_includes_finish_reason():
    """零 content + finish_reason='stop' → 消息含 ``finish_reason=stop``，
    但**不**含 length/max_tokens 误诊提示。
    """
    def _handler(request: httpx.Request) -> httpx.Response:
        return _sse_response(
            json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        )

    p = _build_provider(_handler)
    with pytest.raises(ProviderError) as exc:
        p.complete([{"role": "user", "content": "hi"}])
    msg = str(exc.value)
    assert "finish_reason=stop" in msg
    # 不要因为 stop 误报 max_tokens 提示
    assert "调大" not in msg and "被思考" not in msg


def test_empty_stream_without_finish_reason_message_unchanged():
    """零 content + finish_reason 未出现 → 报错消息保持原样（不臆造线索）。"""
    def _handler(request: httpx.Request) -> httpx.Response:
        # delta 空对象、choices 不带 finish_reason 键 → finish_reason 变量保持 None
        return _sse_response(
            json.dumps({"choices": [{"delta": {}}]}),
        )

    p = _build_provider(_handler)
    with pytest.raises(ProviderError) as exc:
        p.complete([{"role": "user", "content": "hi"}])
    # ProviderError.str() 形如 "provider 'xxx' failed: <message>"；断言 message 部分不变
    assert "empty stream: no content chunks received" in str(exc.value)
    assert "(finish_reason=" not in str(exc.value)