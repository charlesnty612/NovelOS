"""批次三十六 · 批 2：模型路由 + agent_runtime 域修复的定向看守。

覆盖：
- **C-6**：档案级 ``timeout_s`` 对 anthropic / ollama / openai_compatible **三协议**
  一致生效；未配置时回落各 provider 构造默认；非法值三协议一致显式失败。
- **C-32**：``call_with_fallback`` 只把「候选这次没成功」类异常（``ProviderError`` /
  ``OSError`` / ``httpx.HTTPError``）用于失败转移；**编程错误**
  （TypeError / ValueError / KeyError / AttributeError）直接向上抛出，不再拖成
  「N 候选全失败 + AggregateProviderError」。
- **C-12**：prompt 内容未变分支也必须提交 ``_upsert_agent`` 对 agents.role /
  config_json 的刷新（否则 ``conn.close()`` 隐式回滚）。
- **C-11**：``expected="summarizer"`` 有契约校验器（summary 必填非空字符串；
  key_facts / self_report 在场时校验形状）；未知 ``expected`` 不再静默。

零真实 LLM 调用、零内容仓接触：全部临时库 + fake provider。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

from packages.core.agent_runtime.exceptions import AgentOutputError
from packages.core.agent_runtime.prompts import PromptRegistry
from packages.core.agent_runtime.structured_output import validate_contract
from packages.core.db import apply_migrations, get_connection
from packages.core.ids import new_id
from packages.core.model_router.exceptions import ProviderError
from packages.core.model_router.router import ModelRouter


def _fresh_db(tmp_path: Path) -> str:
    db_path = tmp_path / "b36.db"
    apply_migrations(db_path)
    return str(db_path)


def _get_provider_row(db_path: str, provider: str, params: dict) -> dict:
    return {
        "config_id": f"cfg_{provider}",
        "provider": provider,
        "model": "test-model",
        "params_json": json.dumps(params, ensure_ascii=False),
    }


def _insert_model_config(db_path: str, capability: str, model: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO model_configs "
            "(config_id, capability, provider, model, params_json, enabled) "
            "VALUES (?, ?, 'openai', ?, '{}', 1)",
            (new_id("mcf"), capability, model),
        )
        conn.commit()
    finally:
        conn.close()


class _FakeProvider:
    """只实现 ``call_with_fallback`` 需要的两件事：``name`` + ``complete``。"""

    name = "openai"

    def __init__(self, model: str, exc: BaseException | None = None) -> None:
        self.model = model
        self.exc = exc
        self.calls = 0

    def complete(self, messages, params=None):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return {
            "text": f"ok:{self.model}",
            "usage": {"prompt": 0, "completion": 0, "total": 0},
        }


# ---------------------------------------------------------------------------
# C-6：timeout_s 三协议同口径
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider_name", ["anthropic", "ollama", "openai_compatible"])
def test_profile_timeout_reaches_every_protocol(tmp_path: Path, provider_name: str):
    """同一 ``timeout_s=123`` ⇒ 三个 provider 构造收到的 timeout 全为 123。"""
    db_path = _fresh_db(tmp_path)
    params: dict = {"timeout_s": 123}
    if provider_name == "openai_compatible":
        params["base_url"] = "https://gw.example.test/v1"

    provider = ModelRouter(db_path).get_provider(
        _get_provider_row(db_path, provider_name, params)
    )

    assert provider.timeout == 123


def test_bound_profile_timeout_reaches_anthropic_protocol(tmp_path: Path):
    """真实生产形态：opencode 网关连接 + 档案协议 anthropic + 档案级 timeout_s。

    timeout_s 是**档案级**配置（连接不提供）⇒ 绑定态也必须生效；
    同时验证协议地址池取址未被本次修改影响。
    """
    db_path = _fresh_db(tmp_path)
    row = {
        "config_id": "cfg_bound",
        "connection_id": "mconn_gw",
        "provider": "anthropic",
        "model": "claude-x",
        "params_json": json.dumps({"timeout_s": 123}),
    }
    connection_row = {
        "connection_id": "mconn_gw",
        "provider": "openai_compatible",
        "base_url": "https://gw.example.test/openai/v1",
        "params_json": "{}",
        "protocol_base_urls": json.dumps(
            {"anthropic": "https://gw.example.test/anthropic"}
        ),
    }

    provider = ModelRouter(db_path).get_provider(row, connection_row=connection_row)

    assert provider.timeout == 123
    assert provider.base_url == "https://gw.example.test/anthropic"


@pytest.mark.parametrize(
    ("provider_name", "expected"),
    [("anthropic", 60.0), ("ollama", 60.0), ("openai_compatible", 240.0)],
)
def test_profile_timeout_falls_back_to_provider_default(
    tmp_path: Path, provider_name: str, expected: float
):
    """未配置 ``timeout_s`` ⇒ 各自构造器默认（anthropic / ollama 60.0，openai 240.0）。

    判别点：anthropic / ollama 的默认值**不复制进 router**，而是「不传 timeout」
    交给构造器 ⇒ 若 router 误传 ``None`` 或误写常量，这里必红。
    """
    db_path = _fresh_db(tmp_path)
    params: dict = {}
    if provider_name == "openai_compatible":
        params["base_url"] = "https://gw.example.test/v1"

    provider = ModelRouter(db_path).get_provider(
        _get_provider_row(db_path, provider_name, params)
    )

    assert provider.timeout == expected


@pytest.mark.parametrize("provider_name", ["anthropic", "ollama", "openai_compatible"])
@pytest.mark.parametrize("bad_timeout", ["abc", 0, -3])
def test_invalid_profile_timeout_raises_for_every_protocol(
    tmp_path: Path, provider_name: str, bad_timeout
):
    """非法 ``timeout_s`` ⇒ 三协议一致抛 ValueError（不再静默用默认值）。"""
    db_path = _fresh_db(tmp_path)
    params: dict = {"timeout_s": bad_timeout}
    if provider_name == "openai_compatible":
        params["base_url"] = "https://gw.example.test/v1"

    with pytest.raises(ValueError, match="timeout_s"):
        ModelRouter(db_path).get_provider(
            _get_provider_row(db_path, provider_name, params)
        )


# ---------------------------------------------------------------------------
# C-32：fallback 只吞「这次没成功」类异常
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("exc_type", [TypeError, ValueError, KeyError, AttributeError])
def test_programming_error_propagates_without_trying_next_candidate(
    tmp_path: Path, exc_type: type[Exception]
):
    """编程错误直接抛出：断言第二个候选**一次都没被调用**，且不是聚合异常。"""
    db_path = _fresh_db(tmp_path)
    _insert_model_config(db_path, "reasoning", "first")
    _insert_model_config(db_path, "reasoning", "second")

    first = _FakeProvider("first", exc_type("programming bug"))
    second = _FakeProvider("second")
    router = ModelRouter(db_path)
    router.get_provider = lambda row, **kwargs: (  # type: ignore[assignment]
        first if row["model"] == "first" else second
    )

    with pytest.raises(exc_type):
        router.call_with_fallback("reasoning", [{"role": "user", "content": "hi"}])

    assert first.calls == 1
    assert second.calls == 0, "编程错误不该进入失败转移（第二个候选不得被尝试）"


@pytest.mark.parametrize(
    "exc",
    [
        ProviderError("openai", "upstream down", status_code=503),
        OSError("network down"),
        httpx.ConnectError("connect failed"),
    ],
)
def test_retryable_failures_still_fall_back(tmp_path: Path, exc: BaseException):
    """ProviderError / OSError / httpx.HTTPError ⇒ 照旧尝试下一个候选并成功返回。"""
    db_path = _fresh_db(tmp_path)
    _insert_model_config(db_path, "reasoning", "first")
    _insert_model_config(db_path, "reasoning", "second")

    first = _FakeProvider("first", exc)
    second = _FakeProvider("second")
    router = ModelRouter(db_path)
    router.get_provider = lambda row, **kwargs: (  # type: ignore[assignment]
        first if row["model"] == "first" else second
    )

    completion, used_row = router.call_with_fallback(
        "reasoning", [{"role": "user", "content": "hi"}]
    )

    assert completion["text"] == "ok:second"
    assert used_row["model"] == "second"
    assert first.calls == 1
    assert second.calls == 1


# ---------------------------------------------------------------------------
# C-12：prompt 内容未变分支必须提交 agent 刷新
# ---------------------------------------------------------------------------


def test_sync_commits_agent_refresh_when_prompt_content_unchanged(tmp_path: Path):
    """把 agents.role 改脏后重跑 sync（prompt 内容未变）⇒ DB role 必须被刷新。

    判别点：内容未变分支若直接 return（不 commit），``finally: conn.close()``
    会把 ``_upsert_agent`` 的 UPDATE 隐式回滚 ⇒ role 仍是 'dirty'。
    """
    db_path = Path(_fresh_db(tmp_path))
    docs = tmp_path / "prompts"
    docs.mkdir()
    (docs / "demo_agent-v1.md").write_text("# demo v1\n", encoding="utf-8")

    registry = PromptRegistry(db_path)
    first = registry.sync_from_docs(docs)
    assert first["registered"] == [("demo_agent", 1)]

    # 人为把 role / config_json 改脏（模拟历史行残留）
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE agents SET role = 'dirty-role', config_json = '{}' "
            "WHERE name = 'demo_agent'"
        )
        conn.commit()
    finally:
        conn.close()

    # 同一份内容再 sync ⇒ 走「内容未变」分支（updated 为空）
    second = registry.sync_from_docs(docs)
    assert second["updated"] == []

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT role, config_json FROM agents WHERE name = 'demo_agent'"
        ).fetchone()
    finally:
        conn.close()

    assert row["role"] == "reasoning", "内容未变分支必须提交 role 刷新，不能被 close 回滚"
    assert json.loads(row["config_json"]) == {"capability": "reasoning"}


# ---------------------------------------------------------------------------
# C-11：summarizer 契约 + 未知 expected 不再静默
# ---------------------------------------------------------------------------


def test_summarizer_minimal_shape_passes():
    """存量 mock / 消费方只产 ``{"summary": ...}`` ⇒ 必须放行（不收紧必填面）。"""
    validate_contract("summarizer", {"summary": "林渊夜入苏家旧宅。"})


def test_summarizer_full_shape_passes():
    validate_contract(
        "summarizer",
        {
            "summary": "林渊夜入苏家旧宅，取走半枚玉佩。",
            "key_facts": ["取得半枚玉佩", "旧信指向灭门案"],
            "self_report": {
                "chars": 16,
                "truncated": False,
                "sources_used": ["prose_excerpt", "tail_text"],
            },
        },
    )


def test_summarizer_missing_summary_raises():
    with pytest.raises(AgentOutputError, match="summary"):
        validate_contract("summarizer", {"key_facts": []})


@pytest.mark.parametrize("bad_summary", ["", "   ", 123, None])
def test_summarizer_invalid_summary_type_or_empty_raises(bad_summary):
    with pytest.raises(AgentOutputError, match="summary"):
        validate_contract("summarizer", {"summary": bad_summary})


@pytest.mark.parametrize(
    "payload",
    [
        {"summary": "x", "key_facts": "not-a-list"},
        {"summary": "x", "key_facts": ["ok", 123]},
        {"summary": "x", "self_report": "not-an-object"},
        {"summary": "x", "self_report": {"chars": 1, "truncated": False}},
        {"summary": "x", "self_report": {"chars": "1", "truncated": False, "sources_used": []}},
        {"summary": "x", "self_report": {"chars": 1, "truncated": "no", "sources_used": []}},
        {"summary": "x", "self_report": {"chars": 1, "truncated": False, "sources_used": [1]}},
    ],
)
def test_summarizer_malformed_optional_sections_raise(payload):
    with pytest.raises(AgentOutputError):
        validate_contract("summarizer", payload)


def test_unknown_expected_emits_warning_but_does_not_raise(caplog):
    """未知 ``expected`` 仍不抛（保持既有旁路语义），但必须留下可检索的 warning。"""
    with caplog.at_level(
        logging.WARNING, logger="packages.core.agent_runtime.structured_output"
    ):
        validate_contract("no_such_agent", {"anything": True})

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "unknown_expected" in m and "no_such_agent" in m for m in messages
    ), messages
