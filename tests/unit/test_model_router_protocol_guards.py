"""model_router 的**协议层守卫**单测（2026-10-25 批）。

两条守卫（此前都是**静默**的）：

1. **协议池未命中 ⇒ 协议-地址错配必须可见**（M2）
   绑定态取址单点 = ``resolve_effective``：``pool[档案协议] or 连接.base_url``。
   回退发生时，回退到的地址服务的是**连接的主协议**平面；主协议 ≠ 档案协议 ⇒
   请求打到了**另一个协议的端点**（opencode Go 上 anthropic 档案打到
   ``/openai/v1`` 平面）⇒ 上游 404/400，而**仓内零线索**。
   ⇒ 回退 **且** 协议不一致 ⇒ ``log.warning``（带 profile_id / 档案协议 /
   连接主协议 / 池键集合 / 回退地址）；协议一致 ⇒ 回退是安全的 ⇒ **静默**。
   覆盖三条「未命中」形态：**池非空但没该协议** / **池为空（NULL）** /
   **池非空且协议一致（静默对照）**。

2. **环节强度覆盖是 per-candidate 的**（M5）
   候选链允许**异协议后备**（首选 Kimi(openai_compatible) + 后备 Claude(anthropic)
   是常见配法）。Anthropic Messages API 对**未知请求体字段直接 400** ⇒
   后备候选收到 ``reasoning_effort`` ⇒ **整条链全灭**（首选成功也会被后备
   的 400 拖成失败）。
   ⇒ 不相容的候选**跳过覆盖**（保留档案自身参数）+ ``log.warning``；
   相容的候选照常应用。

判别力：断言全部打在**真实值**上（解析出的 base_url / 候选 params_json /
上游请求体 / 日志文本），无恒真断言。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

from packages.core.db import apply_migrations, get_connection
from packages.core.model_router import ModelRouter
from packages.core.model_router.effective import (
    RESERVED_PARAM_KEYS,
    resolve_effective,
    thinking_override_compatible,
)
from packages.core.model_router.providers import AnthropicProvider

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "database" / "migrations"

LOGGER_NAME = "novelos.model_router"

_MAIN = "https://gw.example.test/openai/v1"
_POOL_ANTHROPIC = "https://gw.example.test/anthropic"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> str:
    db_path = tmp_path / "guards.db"
    apply_migrations(db_path, MIGRATIONS_DIR)
    return str(db_path)


def _insert_connection(
    db_path: str,
    *,
    connection_id: str = "mconn_gw",
    provider: str = "openai_compatible",
    base_url: str = _MAIN,
    protocol_base_urls: dict | None = None,
) -> str:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO model_connections (connection_id, name, provider, base_url, "
            "params_json, enabled, created_at, updated_at, protocol_base_urls) "
            "VALUES (?, ?, ?, ?, '{}', 1, datetime('now'), datetime('now'), ?)",
            (
                connection_id,
                connection_id,
                provider,
                base_url,
                json.dumps(protocol_base_urls, ensure_ascii=False)
                if protocol_base_urls
                else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return connection_id


def _insert_profile(
    db_path: str,
    *,
    profile_id: str,
    provider: str,
    model: str = "m",
    params_json: str = "{}",
    connection_id: str | None = None,
) -> str:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO model_profiles (profile_id, name, provider, model, params_json, "
            "enabled, created_at, updated_at, connection_id) "
            "VALUES (?, ?, ?, ?, ?, 1, datetime('now'), datetime('now'), ?)",
            (profile_id, profile_id, provider, model, params_json, connection_id),
        )
        conn.commit()
    finally:
        conn.close()
    return profile_id


def _row(db_path: str, table: str, key_col: str, key: str) -> dict:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE {key_col} = ?", (key,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"{table}/{key} 不存在"
    return {k: row[k] for k in row.keys()}


def _upsert_binding(
    db_path: str,
    capability: str,
    profile_ids: list[str],
    thinking_override: str | None = None,
) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO capability_bindings (capability, profile_ids, thinking_override, updated_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(capability) DO UPDATE SET "
            "profile_ids = excluded.profile_ids, "
            "thinking_override = excluded.thinking_override, "
            "updated_at = excluded.updated_at",
            (capability, json.dumps(list(profile_ids)), thinking_override),
        )
        conn.commit()
    finally:
        conn.close()


def _warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and r.name == LOGGER_NAME
    ]


# ---------------------------------------------------------------------------
# 1. M2：协议池未命中的协议-地址错配 ⇒ 必须有日志
# ---------------------------------------------------------------------------


def test_pool_miss_with_protocol_mismatch_logs_warning(tmp_path: Path, caplog):
    """池未命中 **且** 连接主协议 ≠ 档案协议 ⇒ warning（带完整上下文）。"""
    db = _fresh_db(tmp_path)
    _insert_connection(
        db,
        provider="openai_compatible",
        base_url=_MAIN,
        protocol_base_urls={"anthropic": _POOL_ANTHROPIC},
    )
    _insert_profile(db, profile_id="mprof_miss", provider="ollama", connection_id="mconn_gw")

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        eff = resolve_effective(
            _row(db, "model_profiles", "profile_id", "mprof_miss"),
            _row(db, "model_connections", "connection_id", "mconn_gw"),
        )

    # 回退确实发生了（生效地址 = 连接主地址）
    assert eff.base_url == _MAIN
    # 且必须**有**一条能定位的 warning
    warns = _warnings(caplog)
    assert warns, "协议-地址错配必须留下 warning（此前完全静默）"
    msg = warns[0]
    assert "protocol_pool_miss" in msg
    assert "mprof_miss" in msg  # profile_id
    assert "ollama" in msg  # 档案协议
    assert "openai_compatible" in msg  # 连接主协议
    assert "anthropic" in msg  # 池键集合
    assert _MAIN in msg  # 回退地址


def test_pool_hit_logs_no_warning(tmp_path: Path, caplog):
    """池命中 ⇒ 取池地址 ⇒ **零 warning**（命中路径不许被噪声淹没）。"""
    db = _fresh_db(tmp_path)
    _insert_connection(
        db,
        provider="openai_compatible",
        base_url=_MAIN,
        protocol_base_urls={"anthropic": _POOL_ANTHROPIC},
    )
    _insert_profile(
        db, profile_id="mprof_hit", provider="anthropic", connection_id="mconn_gw"
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        eff = resolve_effective(
            _row(db, "model_profiles", "profile_id", "mprof_hit"),
            _row(db, "model_connections", "connection_id", "mconn_gw"),
        )

    assert eff.base_url == _POOL_ANTHROPIC
    assert _warnings(caplog) == [], f"池命中不该有 warning：{_warnings(caplog)}"


def test_pool_miss_with_same_protocol_is_silent(tmp_path: Path, caplog):
    """池未命中 **但** 连接主协议 == 档案协议 ⇒ 回退安全 ⇒ **静默**。"""
    db = _fresh_db(tmp_path)
    _insert_connection(
        db,
        provider="anthropic",
        base_url=_MAIN,
        protocol_base_urls={"openai_compatible": "https://gw.example.test/openai/v1"},
    )
    _insert_profile(
        db, profile_id="mprof_same", provider="anthropic", connection_id="mconn_gw"
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        eff = resolve_effective(
            _row(db, "model_profiles", "profile_id", "mprof_same"),
            _row(db, "model_connections", "connection_id", "mconn_gw"),
        )

    assert eff.base_url == _MAIN
    assert _warnings(caplog) == [], f"协议一致时的回退是安全的，不该告警：{_warnings(caplog)}"


def test_empty_pool_with_protocol_mismatch_logs_warning(tmp_path: Path, caplog):
    """★连接**没有协议地址池**（NULL）**且** 档案协议 ≠ 连接主协议 ⇒ 必须告警。

    覆盖「池为 NULL」这条路径：既有守卫只测了「池非空但没命中该协议」，
    「池压根没建」（存量连接升级前的常态）**从未被覆盖** ⇒ 该路径若静默，
    协议-地址错配同样零线索。

    行为已实测正确（``_parse_protocol_pool(None)`` → ``{}`` ⇒ 走回退分支），
    本用例补的是**覆盖**（防后续改动把这条路径改静默）。
    """
    db = _fresh_db(tmp_path)
    # protocol_base_urls=None ⇒ 该连接**没有**协议地址池
    _insert_connection(
        db,
        provider="openai_compatible",
        base_url=_MAIN,
        protocol_base_urls=None,
    )
    _insert_profile(
        db, profile_id="mprof_nopool", provider="anthropic", connection_id="mconn_gw"
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        eff = resolve_effective(
            _row(db, "model_profiles", "profile_id", "mprof_nopool"),
            _row(db, "model_connections", "connection_id", "mconn_gw"),
        )

    # 回退到连接主地址（池为空 ⇒ 没有可选地址）
    assert eff.base_url == _MAIN
    # 且必须留下可定位的 warning（含档案协议 / 连接主协议 / 空池键集合）
    warns = _warnings(caplog)
    assert warns, "池为空且协议不一致时必须告警（否则协议-地址错配零线索）"
    msg = warns[0]
    assert "protocol_pool_miss" in msg
    assert "mprof_nopool" in msg  # profile_id
    assert "anthropic" in msg  # 档案协议
    assert "openai_compatible" in msg  # 连接主协议
    assert _MAIN in msg  # 回退地址


# ---------------------------------------------------------------------------
# 2. M5：强度覆盖 per-candidate 相容性
# ---------------------------------------------------------------------------


def test_compat_matrix_semantics():
    """相容矩阵：anthropic 不可表达 reasoning_effort 族；default 恒相容。"""
    # anthropic：effort 族不可表达，thinking.type 族可表达
    assert thinking_override_compatible("max", "anthropic") is False
    assert thinking_override_compatible("low", "anthropic") is False
    assert thinking_override_compatible("off", "anthropic") is True
    assert thinking_override_compatible("adaptive", "anthropic") is True
    # openai_compatible：两族都可表达
    assert thinking_override_compatible("max", "openai_compatible") is True
    assert thinking_override_compatible("off", "openai_compatible") is True
    # default 恒相容（跟随端点，不写任何参数）
    assert thinking_override_compatible("default", "anthropic") is True
    assert thinking_override_compatible("default", "openai_compatible") is True
    # 未知协议 ⇒ 不判定 ⇒ 不拦截（宁可少拦，不静默吞掉用户的覆盖）
    assert thinking_override_compatible("max", "some_unknown_protocol") is True
    assert thinking_override_compatible("max", None) is True


def test_compat_matrix_rejects_invalid_mode():
    """非法档位 ⇒ ValueError（坏配置显式失败，不静默当「跟随档案」）。"""
    with pytest.raises(ValueError, match="8 档枚举"):
        thinking_override_compatible("turbo", "anthropic")


def test_incompatible_fallback_candidate_skips_override(tmp_path: Path, caplog):
    """★异协议后备链：首选（openai_compatible）应用覆盖；后备（anthropic）跳过。

    撤掉修复（无差别应用）⇒ 后备候选会带上 ``reasoning_effort`` ⇒ 本用例红。
    """
    db = _fresh_db(tmp_path)
    kimi = _insert_profile(
        db,
        profile_id="mprof_kimi",
        provider="openai_compatible",
        params_json=json.dumps({"temperature": 0.3}),
    )
    claude = _insert_profile(
        db,
        profile_id="mprof_claude",
        provider="anthropic",
        # 后备档案**自带的**思考参数：跳过覆盖时必须原样保留
        params_json=json.dumps({"thinking": {"type": "enabled"}, "temperature": 0.7}),
    )
    _upsert_binding(db, "reasoning", [kimi, claude], thinking_override="max")

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        rows = ModelRouter(db).list_enabled("reasoning")

    assert [r["config_id"] for r in rows] == [kimi, claude]
    first = json.loads(rows[0]["params_json"])
    second = json.loads(rows[1]["params_json"])

    # 首选：openai_compatible ⇒ 相容 ⇒ 覆盖生效
    assert first["reasoning_effort"] == "max"
    assert "thinking" not in first
    assert first["temperature"] == 0.3

    # 后备：anthropic ⇒ reasoning_effort 族不可表达 ⇒ **跳过覆盖**（保留档案默认）
    assert "reasoning_effort" not in second, "anthropic 候选收到了无法表达的参数族"
    assert second["thinking"] == {"type": "enabled"}, "跳过覆盖必须保留档案自身的思考参数"
    assert second["temperature"] == 0.7

    # 跳过必须有日志（可定位：环节 / 候选 / 协议 / 被跳过的档）
    warns = _warnings(caplog)
    assert warns, "跳过覆盖必须留日志（否则是静默失效）"
    msg = warns[0]
    assert "thinking_override" in msg
    assert "reasoning" in msg  # capability
    assert claude in msg  # 候选 profile_id
    assert "anthropic" in msg  # 协议
    assert "max" in msg  # 被跳过的档


def test_incompatible_candidate_never_reaches_upstream_body(tmp_path: Path):
    """★上游请求体里不得出现 anthropic 无法表达的字段（400 的真实来源）。"""
    db = _fresh_db(tmp_path)
    _insert_profile(
        db, profile_id="mprof_kimi2", provider="openai_compatible", params_json="{}"
    )
    claude = _insert_profile(
        db,
        profile_id="mprof_claude2",
        provider="anthropic",
        params_json=json.dumps({"thinking": {"type": "enabled"}}),
    )
    _upsert_binding(db, "reasoning", ["mprof_kimi2", claude], thinking_override="max")

    rows = ModelRouter(db).list_enabled("reasoning")
    candidate = next(r for r in rows if r["config_id"] == claude)
    extras = {
        k: v
        for k, v in json.loads(candidate["params_json"]).items()
        if k not in RESERVED_PARAM_KEYS
    }

    captured: dict[str, object] = {}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content.decode("utf-8"))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    provider = AnthropicProvider(
        base_url="https://api.example.test",
        api_key="sk-test",
        model="claude",
        client=httpx.Client(transport=httpx.MockTransport(_handler)),
    )
    provider.complete([{"role": "user", "content": "hi"}], params=extras)

    body = captured["body"]
    assert isinstance(body, dict)
    assert "reasoning_effort" not in body, "anthropic 请求体带上了未知字段 ⇒ 上游 400"
    assert body.get("thinking") == {"type": "enabled"}


def test_same_protocol_chain_applies_override_to_every_candidate(tmp_path: Path):
    """同协议链：覆盖**照常**应用到每个候选（不因相容性修复而缩水）。"""
    db = _fresh_db(tmp_path)
    p1 = _insert_profile(db, profile_id="mprof_p1", provider="openai_compatible")
    p2 = _insert_profile(db, profile_id="mprof_p2", provider="openai_compatible")
    _upsert_binding(db, "reasoning", [p1, p2], thinking_override="high")

    rows = ModelRouter(db).list_enabled("reasoning")
    assert len(rows) == 2
    for row in rows:
        params = json.loads(row["params_json"])
        assert params["reasoning_effort"] == "high"
        assert "thinking" not in params


def test_thinking_type_override_still_applies_to_anthropic_candidate(tmp_path: Path):
    """thinking.type 族（off/on/adaptive）在 anthropic 上**可表达** ⇒ 覆盖照常应用。"""
    db = _fresh_db(tmp_path)
    p = _insert_profile(
        db,
        profile_id="mprof_anthropic_off",
        provider="anthropic",
        params_json=json.dumps({"reasoning_effort": "high"}),
    )
    _upsert_binding(db, "reasoning", [p], thinking_override="off")

    rows = ModelRouter(db).list_enabled("reasoning")
    params = json.loads(rows[0]["params_json"])
    assert params["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in params
