"""L3 组：配置与日志**列表**端点的 response_model 契约测试。

背景
----
上一批补的是**单体**端点；列表端点（``list[dict]``）在 OpenAPI 里 items 是匿名
object，前端拿到的仍是 ``Record<string, unknown>``。本组给 6 个列表端点补
``response_model``，判别口径是：**``items`` 上有 ``$ref``**（或响应 schema 本身是
``$ref``）才算已建模。

测法：先量再建
--------------
``response_model`` 会**过滤**未声明字段——少声明一个，响应就静默少一个而测试可能全绿
（AGENTS 坑区「断言了结果 ≠ 判别了机制」的同族）。故本文件的键集基准是
2026-10-15 在临时库上**实测**出来的：

- ``create_app(Settings(data_dir=tmp))`` + ASGITransport；
- 全程 ``provider="mock"``（**不发起任何真实模型调用**）；
- **空数组量不到任何东西** ⇒ 每个用例都先造出行（造不出来的直接失败，不留空断言）。

⚠️ 造行手段（逐个可复现）：

===========================  ==========================================
端点                        播种方式
===========================  ==========================================
``/model-configs``          ``POST /api/model-configs``
``/model-profiles``         ``POST /api/model-profiles``
``/capability-bindings``    迁移自带 8 个环节（**天然非空**），另 ``PUT`` 绑定 1 个
``/agents``                 ``POST /api/agents/sync``（扫 ``docs/agents/prompts``）
``/agents/{name}/prompts``  同上；另测未知 agent → ``[]``
``/ai-call-logs``           跑一次 ``POST /agents/observer/run``（``mock_script``）
                            —— runner 每次调用都写 ``ai_call_logs``，见 ``runner.py``
                            ``_write_log``
===========================  ==========================================

三条互相独立的基准（**互不替代**）
--------------------------------
1. **实测硬编码基准** ``EXPECTED_*``：写死的是量到的键集，**不是**从
   ``Model.model_fields`` 抄来的；断言写成 ``set(row) == EXPECTED_*``。
2. **DB 实表列基准**：``AgentListRowOut`` / ``AgentPromptRowOut`` /
   ``AiCallLogSummary`` 的字段集直接与 ``PRAGMA table_info`` 的真实列名比对
   （后者要做「去掉两列 + 改一列名」的换算）——基准来自迁移文件，与端点、与模型都
   无关，从根上排除「夹具抄实现」（坑区「夹具从被测代码的实际输出里抄形状」）。
3. **模型 vs 硬编码** 双保险：``M.model_fields == EXPECTED_*``，与第 1 条互补
   （一条抓「端点少出/多出键」，一条抓「模型少声明/多声明键」）。
   ⚠️ 2026-10-24 **收敛**：这条比对现在走 runner 的单点
   ``assert_model_fields_match(M, EXPECTED_*)`` ⇒ 本文件不再自己抄一遍
   ``set(M.model_fields) == set(EXPECTED_*)``（复制粘贴的对比对逻辑迟早分叉）。

⚠️ **自指断言禁令的落实**（坑区「自指断言恒真」，M14/M15/M20 三条同族）：本文件**没有
一处**「响应键集 == 模型字段集」的断言。``model_fields`` 在本文件里只剩**两类**用途，
都是**独立**的：第四基准（模型 vs **DDL 实表列**，第三方基准 ⇒ 保留直比）与
``extra="allow"`` 容忍度用例的**占位值生成**。静态自查：

    grep -n "model_fields" tests/api/test_config_list_response_contract.py

🔑 密钥面：``params_json.api_key`` 在**读路径**被替换为 ``"***"`` 并另加
``has_api_key``（:func:`packages.core.model_router.security._mask_response`）。**建模不能
把掩码逻辑写漏**——``test_list_rows_never_leak_the_plaintext_api_key`` 同时钉住
「明文不出现在响应里」「DB 里确实存着明文（掩码是读路径的事）」两个方向。

⚠️ **extra="allow" 的代价必须显式承认**：三个行模型（``ModelConfigOut`` /
``ModelProfileOut`` / ``CapabilityBindingOut`` / 两个 agent 行模型）是
``extra="allow"`` 的 ⇒「少声明一个字段」在**响应侧看不出来**（键以 extra 身份透出），
只有 OpenAPI ``properties`` 断言能抓到。反过来 ``AiCallLogSummary`` 是**严格**模型，
少声明字段在响应侧立刻可见。两条断言都在，缺一条就会出现盲区。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from packages.core.api.routers.ai_call_logs import AiCallLogSummary
from packages.core.db import get_connection
from packages.core.model_router.models import (
    AgentListRowOut,
    AgentPromptRowOut,
    CapabilityBindingOut,
    ModelConfigOut,
    ModelProfileOut,
)

# ============================================================ 实测基准（第三方来源）
# ⚠️ 写死自 2026-10-15 临时库实测，**不是**从 model_fields 抄的。
from ._contract_runner import (
    app_table_columns,
    assert_list_items_named_ref,
    assert_model_fields_match,
    assert_openapi_properties_and_required,
    create_test_app,
    one_shot,
)

EXPECTED_CONFIG_KEYS = frozenset(
    {"config_id", "capability", "provider", "model", "params_json", "enabled", "has_api_key"}
)
EXPECTED_PROFILE_KEYS = frozenset(
    {
        "profile_id",
        "name",
        "provider",
        "model",
        "params_json",
        "enabled",
        "created_at",
        "updated_at",
        "has_api_key",
        # 2026-10-21 批次十五（连接层）：档案可引用一条「连接」；null = 独立配置
        "connection_id",
    }
)
EXPECTED_BINDING_KEYS = frozenset(
    {
        "capability",
        "label",
        "agents",
        "profile_ids",
        "profiles",
        "legacy_available",
        "updated_at",
    }
)
EXPECTED_AGENT_KEYS = frozenset({"agent_id", "name", "role", "config_json", "created_at", "updated_at"})
EXPECTED_PROMPT_KEYS = frozenset(
    {"prompt_id", "agent_id", "version", "content", "status", "created_at", "updated_at"}
)
EXPECTED_AI_CALL_LOG_KEYS = frozenset(
    {
        "call_id",
        "run_id",
        "node_run_id",
        "agent_id",
        "model_id",
        "prompt_version",
        "latency_ms",
        "retry_count",
        "error",
        "token_usage",
        "cost",
        "created_at",
    }
)

# 6 个端点 → items 上应当出现的 $ref 名。撤掉任一 response_model ⇒ 该 $ref 消失。
EXPECTED_ITEM_REFS: dict[tuple[str, str], str] = {
    ("/api/model-configs", "get"): "ModelConfigOut",
    ("/api/model-profiles", "get"): "ModelProfileOut",
    ("/api/capability-bindings", "get"): "CapabilityBindingOut",
    ("/api/agents", "get"): "AgentListRowOut",
    ("/api/agents/{name}/prompts", "get"): "AgentPromptRowOut",
    ("/api/ai-call-logs", "get"): "AiCallLogSummary",
}

# OpenAPI 里必须出现在 properties 里的键（＝实测键集）。
EXPECTED_DECLARED_PROPERTIES = {
    "ModelConfigOut": EXPECTED_CONFIG_KEYS,
    "ModelProfileOut": EXPECTED_PROFILE_KEYS,
    "CapabilityBindingOut": EXPECTED_BINDING_KEYS,
    "AgentListRowOut": EXPECTED_AGENT_KEYS,
    "AgentPromptRowOut": EXPECTED_PROMPT_KEYS,
    "AiCallLogSummary": EXPECTED_AI_CALL_LOG_KEYS,
}
# 必须出现在 required 里的键。三个模型的 6 个键**可空但恒在**（无数据时值为 null），
# 给了默认值就会退出 required，前端据此会误以为它可省略 ⇒ 必须钉住。
EXPECTED_REQUIRED = dict(EXPECTED_DECLARED_PROPERTIES)
# ⚠️ 2026-10-21 批次十五（连接层）：``connection_id`` 有默认值 None
# （独立配置的档案）⇒ 进 properties、**不进 required**。
EXPECTED_REQUIRED["ModelProfileOut"] = EXPECTED_REQUIRED["ModelProfileOut"] - {
    "connection_id"
}

#: AiCallLogSummary 里「值可为 null 但键恒在」的键——**schema 层**口径（8 个都声明成
#: ``X | None`` 且都进 required；实测它们确实都可为空，只是 mock 路径下只有前 3 个真的空）。
NULLABLE_BUT_ALWAYS_PRESENT = (
    "node_run_id",
    "agent_id",
    "model_id",
    "prompt_version",
    "latency_ms",
    "error",
    "token_usage",
    "cost",
)
#: 实测（mock 路径下真的为 null）的子集。别把这张表当「全部可空键」用——上次正是把
#: agent_id 误当 null 写进断言，用例当场红了（这正是「断言失败先读实际值」的用法）。
NULL_AT_RUNTIME = ("node_run_id", "error", "cost")
#: 实测有值的键：钉住「可空 ≠ 恒空」（模型把它们声明成 | None 是**允许**为空，不是断言为空）。
VALUED_AT_RUNTIME = (
    "call_id",
    "run_id",
    "agent_id",
    "model_id",
    "prompt_version",
    "latency_ms",
    "retry_count",
    "token_usage",
    "created_at",
)

SECRET = "sk-do-not-leak-list-abc123"

# ============================================================ 夹具


def _create_app(tmp_path: Path):
    """共享 runner 的 ``create_test_app``（本文件原用 ``log_level="ERROR"`` ⇒ 保留）。"""
    return create_test_app(tmp_path, log_level="ERROR")


def _req(app, method: str, path: str, **kwargs) -> httpx.Response:
    """每请求一个客户端、**进 lifespan**（迁移在 app 启动时跑 ⇒ 见 runner 说明）。"""
    return asyncio.run(one_shot(app, method, path, enter_lifespan=True, **kwargs))


def _seed_config(app, **overrides) -> dict:
    body = {
        "capability": "creative_writing",
        "provider": "mock",
        "model": "mock-model-0",
        "params_json": {"api_key": SECRET, "temperature": 0.7},
    }
    body.update(overrides)
    r = _req(app, "POST", "/api/model-configs", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _seed_profile(app, **overrides) -> dict:
    body = {
        "name": "列表契约档案",
        "provider": "mock",
        "model": "mock-model",
        "params_json": {"api_key": SECRET, "top_p": 0.9},
    }
    body.update(overrides)
    r = _req(app, "POST", "/api/model-profiles", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _seed_agents(app) -> None:
    """播种 agents + prompts（``sync`` 是唯一的正常写入口）。"""
    r = _req(app, "POST", "/api/agents/sync")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["registered"], "sync 一个 agent 都没注册 ⇒ 播种失败，量具失效"


def _seed_one_ai_call_log(app) -> None:
    """跑一次 mock agent 调用，让 runner 落一条 ``ai_call_logs`` 行。

    ⚠️ ``mock_script`` 非空 ⇒ 走 MockProvider，**不发起任何真实模型调用**
    （AGENTS §一 硬约束 2）。
    """
    r = _req(
        app,
        "POST",
        "/api/agents/observer/run",
        json={"input_payload": {}, "expected": "observer", "mock_script": ["{}"]},
    )
    assert r.status_code == 201, r.text


def _rows(app, path: str) -> list[dict]:
    r = _req(app, "GET", path)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list), f"{path} 应返回数组，实得 {type(body).__name__}"
    assert body, f"{path} 返回空数组 ⇒ **量不到任何东西**，本用例是空断言"
    return body


# ============================================================ ① 响应键集（实测基准）


def test_model_configs_list_returns_the_measured_seven_keys(tmp_path: Path):
    app = create_test_app(tmp_path, log_level="ERROR")
    _seed_config(app)
    _seed_config(app, capability="reasoning", model="mock-model-1")
    # 无 params 的行：params_json 是 {}（不是 null、也不是省略）
    _seed_config(app, capability="light", model="mock-model-2", params_json=None)

    rows = _rows(app, "/api/model-configs")
    for row in rows:
        assert set(row) == set(EXPECTED_CONFIG_KEYS), (
            f"多={sorted(set(row) - EXPECTED_CONFIG_KEYS)} "
            f"少={sorted(EXPECTED_CONFIG_KEYS - set(row))}"
        )
    bare = next(r for r in rows if r["capability"] == "light")
    assert bare["params_json"] == {}
    assert bare["has_api_key"] is False

    # 过滤分支（capability / provider）不得让形状分叉
    filtered = _rows(app, "/api/model-configs?capability=reasoning")
    assert len(filtered) == 1 and set(filtered[0]) == set(EXPECTED_CONFIG_KEYS)
    by_provider = _rows(app, "/api/model-configs?provider=mock")
    assert len(by_provider) == 3
    assert _req(app, "GET", "/api/model-configs?provider=nope").json() == []


def test_model_profiles_list_returns_the_measured_ten_keys(tmp_path: Path):
    app = create_test_app(tmp_path, log_level="ERROR")
    _seed_profile(app)
    _seed_profile(app, name="第二档案", params_json=None, enabled=0)

    rows = _rows(app, "/api/model-profiles")
    for row in rows:
        assert set(row) == set(EXPECTED_PROFILE_KEYS), (
            f"多={sorted(set(row) - EXPECTED_PROFILE_KEYS)} "
            f"少={sorted(EXPECTED_PROFILE_KEYS - set(row))}"
        )
    disabled = next(r for r in rows if r["enabled"] == 0)
    assert disabled["params_json"] == {}

    # include_enabled_only 分支：只剩 enabled 行，形状不变
    only_enabled = _rows(app, "/api/model-profiles?include_enabled_only=true")
    assert {r["name"] for r in only_enabled} == {"列表契约档案"}


def test_capability_bindings_list_rows_are_key_for_key_identical_to_the_put_response(
    tmp_path: Path,
):
    """GET 列表与 PUT 响应**逐字同形**（同一个 ``list_with_profiles`` 的产物）。

    这条是「能不能复用 ``CapabilityBindingOut``」的判据：两种形态都验——
    **已绑定**行（``updated_at`` 有值、``profiles`` 有 4 键子项）与
    **未绑定**行（``updated_at`` 为 ``null`` 而非省略、两个数组为空）都是 7 键。
    """
    app = create_test_app(tmp_path, log_level="ERROR")
    pid = _seed_profile(app)["profile_id"]
    put = _req(app, "PUT", "/api/capability-bindings/reasoning", json={"profile_ids": [pid]})
    assert put.status_code == 200, put.text
    put_body = put.json()
    assert set(put_body) == set(EXPECTED_BINDING_KEYS), put_body

    rows = _rows(app, "/api/capability-bindings")
    assert len(rows) == 8, f"环节数变了（实测 8）——本用例的空断言前提需重估：{rows}"
    for row in rows:
        assert set(row) == set(EXPECTED_BINDING_KEYS), (
            f"多={sorted(set(row) - EXPECTED_BINDING_KEYS)} "
            f"少={sorted(EXPECTED_BINDING_KEYS - set(row))}"
        )

    bound = next(r for r in rows if r["capability"] == "reasoning")
    assert bound == put_body, "GET 的绑定行与 PUT 响应必须是同一份数据（逐字相等）"
    assert bound["updated_at"] is not None

    unbound = next(r for r in rows if r["capability"] == "observer")
    assert unbound["updated_at"] is None, "未绑定时 updated_at 必须**存在**（值 null），不得省略"
    assert unbound["profile_ids"] == [] and unbound["profiles"] == []
    assert unbound["legacy_available"] is False


def test_agents_list_returns_the_measured_six_keys(tmp_path: Path):
    app = create_test_app(tmp_path, log_level="ERROR")
    _seed_agents(app)

    rows = _rows(app, "/api/agents")
    assert len(rows) == 17, f"实测 17 个 agent（2026-10-24 增 outline_batch_planner），实得 {len(rows)}——播种口径变了需重估"
    for row in rows:
        assert set(row) == set(EXPECTED_AGENT_KEYS), (
            f"{row.get('name')} 多={sorted(set(row) - EXPECTED_AGENT_KEYS)} "
            f"少={sorted(EXPECTED_AGENT_KEYS - set(row))}"
        )


def test_agent_prompts_list_returns_the_measured_seven_keys(tmp_path: Path):
    app = create_test_app(tmp_path, log_level="ERROR")
    _seed_agents(app)

    rows = _rows(app, "/api/agents/writer/prompts")
    # ⚠️ 2026-10-16 批次十一新增 ``writer-v4.md``（角色圣经到达 writer）⇒ 3 → 4 版；
    # ⚠️ 2026-10-19 内部消费外部 skill 新增 ``writer-v5.md``（skill 段到达 writer）⇒ 4 → 5 版。
    # 本条守的是「**响应形状**逐字不变」与「版本按数字降序」，不是某一版的个数；
    # 但仍保留个数断言（多一版就多一行，没被静默丢弃/合并）。
    assert len(rows) == 5, f"writer 有 v1/v2/v3/v4/v5 五版，实得 {len(rows)}"
    for row in rows:
        assert set(row) == set(EXPECTED_PROMPT_KEYS), (
            f"多={sorted(set(row) - EXPECTED_PROMPT_KEYS)} "
            f"少={sorted(EXPECTED_PROMPT_KEYS - set(row))}"
        )
    # version 列存的是 "v"+数字，不含 agent 前缀（与 ai_call_logs.prompt_version 不同形态）
    assert [r["version"] for r in rows] == ["v5", "v4", "v3", "v2", "v1"]
    assert rows[0]["agent_id"] == rows[1]["agent_id"], "同一 agent 的多版 prompt 共用 agent_id"
    assert rows[0]["status"] == "ACTIVE"

    # 未知 agent：返回 []（200，不是 404）——既有行为，本批未改
    missing = _req(app, "GET", "/api/agents/no-such-agent/prompts")
    assert missing.status_code == 200, missing.text
    assert missing.json() == []


def test_ai_call_logs_list_returns_the_measured_twelve_keys(tmp_path: Path):
    app = create_test_app(tmp_path, log_level="ERROR")
    _seed_agents(app)
    _seed_one_ai_call_log(app)

    rows = _rows(app, "/api/ai-call-logs")
    for row in rows:
        assert set(row) == set(EXPECTED_AI_CALL_LOG_KEYS), (
            f"多={sorted(set(row) - EXPECTED_AI_CALL_LOG_KEYS)} "
            f"少={sorted(EXPECTED_AI_CALL_LOG_KEYS - set(row))}"
        )
    row = rows[0]
    assert row["model_id"] == "mock/mock", "mock 路径下 model_id 恒为 'mock/mock'"
    assert row["prompt_version"] == "observer:v1"
    assert isinstance(row["token_usage"], dict), "token_usage 是**解析后**的对象，不是 JSON 字符串"
    # 摘要行**不含**详情端点那两个键（那是要读全文的）
    assert "input_context_ids" not in row and "output" not in row
    # 实测：3 个键是 null、其余有值——「可空」是**允许**为空，不是断言恒为空
    for key in NULL_AT_RUNTIME:
        assert key in row, f"{key} 键被省略了（实测应在、值为 null）"
        assert row[key] is None, f"{key} 实测应为 null，实得 {row[key]!r}"
    for key in VALUED_AT_RUNTIME:
        assert row[key] is not None, f"{key} 实测有值，不该是 null"

    # 分页分支
    assert len(_rows(app, "/api/ai-call-logs?limit=1")) == 1
    assert _req(app, "GET", "/api/ai-call-logs?offset=1").json() == []


def test_list_rows_match_the_detail_row_shape_where_they_share_a_source(tmp_path: Path):
    """同一个 ``_summary()`` 的两个消费者：12 键（列表） ⊂ 14 键（详情）。

    反向护栏：若将来有人把 ``input_context_ids`` / ``output`` 塞进列表摘要，
    列表端点会把 prompt 全文推给前端 ⇒ 本用例的「少两键」断言立刻红。
    """
    app = create_test_app(tmp_path, log_level="ERROR")
    _seed_agents(app)
    _seed_one_ai_call_log(app)
    row = _rows(app, "/api/ai-call-logs")[0]

    detail = _req(app, "GET", f"/api/ai-call-logs/{row['call_id']}")
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert set(detail_body) - set(row) == {"input_context_ids", "output"}
    assert set(row) < set(detail_body)


# ============================================================ ② 模型字段 vs 硬编码（双保险）


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (ModelConfigOut, EXPECTED_CONFIG_KEYS),
        (ModelProfileOut, EXPECTED_PROFILE_KEYS),
        (CapabilityBindingOut, EXPECTED_BINDING_KEYS),
        (AgentListRowOut, EXPECTED_AGENT_KEYS),
        (AgentPromptRowOut, EXPECTED_PROMPT_KEYS),
        (AiCallLogSummary, EXPECTED_AI_CALL_LOG_KEYS),
    ],
)
def test_model_field_set_matches_the_hardcoded_measured_baseline(model, expected):
    """第三基准：模型声明的字段集 == 实测硬编码键集。

    与第 ① 节的响应键集断言互补：那几条抓「端点少出/多出键」，这条抓「模型少声明/
    多声明键」。任一侧单独改都会红。

    ⚠️ **走 runner 的单点**（:func:`assert_model_fields_match`，2026-10-24 收敛）：
    「模型 vs 硬编码」的比对逻辑**只允许有一份实现** ⇒ 各域文件不再各写一遍
    ``declared == set(expected)``（复制粘贴的对比对逻辑迟早分叉）。
    """
    assert_model_fields_match(model, expected)


def test_new_row_models_declare_exactly_the_real_database_columns(tmp_path: Path):
    """第四基准：新行模型的字段集 == **DB 实表列**（摘要模型做一次显式换算）。

    基准取自 ``PRAGMA table_info``，**既不抄端点输出也不抄模型**，故不可能与缺陷同源。
    三个新模型都不是 ``SELECT *`` 而是**显式列清单**——但凡少声明一列，
    ``response_model`` 都会把它**静默过滤掉**（症状：后端返回了、API 取不到、零报错）。

    ⚠️ **这里保留直比**（不走 runner 的 ``assert_model_fields_match``）：DDL 实表列是
    **第三方基准**（不是硬编码期望），两边的**形状不同**（一侧是模型字段集、一侧是
    实表列，可能差在列名换算上）⇒ 用直比 + 显式换算表达，比套一个「与硬编码期望比」
    的 helper 更贴合语义；runner 的单点只覆盖「与硬编码期望比」这一种形态。
    """
    app = create_test_app(tmp_path, log_level="ERROR")
    assert set(AgentListRowOut.model_fields) == app_table_columns(app, "agents")
    assert set(AgentPromptRowOut.model_fields) == app_table_columns(app, "prompts")

    # 摘要模型 = 全表 − 两列（只有详情端点读全文）+ token_usage_json 改名为 token_usage
    expected_summary = (
        app_table_columns(app, "ai_call_logs")
        - {"input_context_ids_json", "output_json"}
        - {"token_usage_json"}
    ) | {"token_usage"}
    assert set(AiCallLogSummary.model_fields) == expected_summary
    # 反向护栏：那两列**不该**出现在摘要里（否则列表端点会把全文推给前端）
    assert "input_context_ids" not in set(AiCallLogSummary.model_fields)
    assert "output" not in set(AiCallLogSummary.model_fields)


# ============================================================ ③ response_model 确已声明


def test_every_list_endpoint_declares_a_typed_items_ref(tmp_path: Path):
    """6 个端点逐一断言 OpenAPI 的 200 响应是「数组 + items.$ref」。

    这条是本组的核心判别：撤掉任一 ``response_model=`` ⇒ items 退回匿名
    ``{"type": "object"}`` ⇒ 必红。⚠️ 断言**不是**「有没有 $ref」这种宽判——
    还要求顶层必须是 array（防有人把列表包成 ``{"items": [...]}`` 的对象）。
    """
    app = create_test_app(tmp_path, log_level="ERROR")
    schema = app.openapi()
    for (path, method), expected in EXPECTED_ITEM_REFS.items():
        # 口径单点：顶层必须是 array + items 是**具名** $ref（防「把列表包成
        # {"items": [...]} 的对象」）；裸 list[dict] ⇒ 前端拿到 Record<string, unknown>。
        assert_list_items_named_ref(schema, path, method, expected)


@pytest.mark.parametrize("model_name", sorted(EXPECTED_DECLARED_PROPERTIES))
def test_openapi_declares_every_measured_key(model_name: str, tmp_path: Path):
    """⚠️ extra="allow" 的模型：**响应里有该键 ≠ schema 里声明了该键**。

    把字段声明从模型里删掉，``extra="allow"`` 会让它以 extra 身份**照样透出**，
    响应侧键集断言恒绿（2026-10-15 M20 实测存活）。本节 ① 的响应断言抓不到这一类，
    必须另断言 OpenAPI 的 ``properties`` / ``required``。两个方向都用**相等**：
    多声明一个键同样是契约漂移（前端会以为后端保证它）。
    """
    app = create_test_app(tmp_path, log_level="ERROR")
    components = app.openapi()["components"]["schemas"]
    assert model_name in components, f"schema 缺 {model_name}"
    spec = components[model_name]
    assert_openapi_properties_and_required(
        spec,
        EXPECTED_DECLARED_PROPERTIES[model_name],
        EXPECTED_REQUIRED[model_name],
        label=model_name,
    )


def test_nullable_but_always_present_log_keys_are_declared_nullable_and_required(tmp_path: Path):
    """⚠️「可空但键恒在」在响应侧**抓不到**——它们是同一个键的两种取值。

    - 把 ``str | None`` 改成 ``str``：``required`` 还在、键集不变，只有类型变了；
    - 加个 ``= None`` 默认值：类型不变，但 ``required`` 掉了（前端会当它可省略）。
    ⇒ 只能从 OpenAPI 的 ``anyOf`` 含 ``null`` + ``required`` 两处判。

    这八项的实测值全是 ``null``（mock 路径不写 token_usage_json 之外的列），
    所以「键恒在」是真的——见 test_ai_call_logs_list_returns_the_measured_twelve_keys。
    """
    spec = create_test_app(tmp_path, log_level="ERROR").openapi()["components"]["schemas"]["AiCallLogSummary"]
    for key in NULLABLE_BUT_ALWAYS_PRESENT:
        prop = spec["properties"][key]
        assert {"type": "null"} in prop.get("anyOf", []), (
            f"AiCallLogSummary.{key} 在 schema 里不可空，但实测会返回 null：{prop}"
        )
        assert key in spec.get("required", []), (
            f"AiCallLogSummary.{key} 键恒在（无数据时值 null），不该被声明成可省略"
        )


def test_agents_config_json_is_declared_as_a_string_not_an_object(tmp_path: Path):
    """``config_json`` 在库里是 TEXT、端点原样透传 ⇒ 模型必须声明成 **string**。

    声明成 ``object`` 的后果实测过（throwaway 模型，非推断）：Pydantic **不会**把 JSON
    字符串解析成对象，而是**直接校验失败**（``Input should be a valid dictionary``）
    ⇒ 端点 500。所以这条断言守的不是「形状漂移」，是「一条会 500 的契约」。
    """
    props = create_test_app(tmp_path, log_level="ERROR").openapi()["components"]["schemas"]["AgentListRowOut"]["properties"]
    assert props["config_json"].get("type") == "string", (
        f"config_json 应声明为 string（端点未解析）：{props['config_json']}"
    )


def test_agents_rows_keep_config_json_as_a_json_string_at_runtime(tmp_path: Path):
    """运行时侧：行里的 ``config_json`` 是字符串，要用得自己 ``json.loads``。

    与上面那条（schema 侧）构成一对：schema 说 string、运行时也得真是 string。
    """
    app = create_test_app(tmp_path, log_level="ERROR")
    _seed_agents(app)
    row = _rows(app, "/api/agents")[0]
    assert isinstance(row["config_json"], str), (
        f"config_json 被解析成对象了（端点从未解析它）：{type(row['config_json']).__name__}"
    )
    parsed = json.loads(row["config_json"])
    assert isinstance(parsed, dict) and "capability" in parsed, parsed
    assert row["role"] == parsed["capability"], "role 与 config_json['capability'] 同源"


# ============================================================ ④ 密钥面（🔑）


def test_list_rows_never_leak_the_plaintext_api_key(tmp_path: Path):
    """🔑 列表路径的密钥面：明文零泄漏 + 掩码位恒在 + DB 里确实存着明文。

    第三个方向（DB 里有明文）是关键：否则「响应里找不到明文」可能是因为**写路径就没
    存**，那样这条用例就成了空断言（与实现同源，见坑区「夹具从被测代码的实际输出里
    抄形状」）。掩码是**读路径**的职责，必须证明读的那一端确实有东西可掩。

    突变对应：把列表端点里的 ``_mask_response(...)`` 摘掉 ⇒ 本用例红。
    """
    app = create_test_app(tmp_path, log_level="ERROR")
    profile = _seed_profile(app)
    config = _seed_config(app)

    # 先证明 DB 里存的是明文（否则下面的「找不到明文」没有意义）
    async def _read_raw_params() -> tuple[str, str]:
        async with app.router.lifespan_context(app):
            conn = get_connection(app.state.settings.db_path)
            try:
                p = conn.execute(
                    "SELECT params_json FROM model_profiles WHERE profile_id = ?",
                    (profile["profile_id"],),
                ).fetchone()[0]
                c = conn.execute(
                    "SELECT params_json FROM model_configs WHERE config_id = ?",
                    (config["config_id"],),
                ).fetchone()[0]
            finally:
                conn.close()
            return p, c

    raw_profile, raw_config = asyncio.run(_read_raw_params())
    assert SECRET in raw_profile and SECRET in raw_config, (
        "DB 里没有明文密钥 ⇒ 本用例的前提（掩码有东西可掩）不成立"
    )

    for path, expected_id in (
        ("/api/model-profiles", profile["profile_id"]),
        ("/api/model-configs", config["config_id"]),
    ):
        r = _req(app, "GET", path)
        assert r.status_code == 200, r.text
        assert SECRET not in r.text, f"{path} 响应里出现了明文密钥"
        row = next(x for x in r.json() if x.get("profile_id", x.get("config_id")) == expected_id)
        assert row["params_json"]["api_key"] == "***", row["params_json"]
        assert row["has_api_key"] is True
        # 顶层不得平铺出任何 key/secret 字段
        assert not ({"api_key", "key", "secret"} & set(row)), sorted(row)


# ============================================================ ⑤ extra="allow" 的政策


@pytest.mark.parametrize("model", [AgentListRowOut, AgentPromptRowOut])
def test_agent_row_models_tolerate_a_column_added_by_a_later_migration(model):
    """agent 行模型必须 ``extra="allow"``：SELECT 是显式列清单，将来加列不该被静默丢。

    端点侧抓不到这件事（当前列恰好都在声明里），只能在模型层注入未知键来判别。
    撤掉 ``extra="allow"`` ⇒ 该键被裁掉 ⇒ 必红。

    ⚠️ 占位值按**字段注解**生成而非照抄实现：抄一份「恰好合法」的样本，字段一改类型
    就会变成「夹具与实现同源」。
    """
    base = {name: _placeholder_for(name, f) for name, f in model.model_fields.items()}
    base["a_column_added_by_a_later_migration"] = "future-value"
    dumped = model(**base).model_dump()
    assert "a_column_added_by_a_later_migration" in dumped, (
        f"{model.__name__} 把未知键裁掉了 ⇒ extra='allow' 没了 ⇒ "
        "将来加的列会静默从响应消失（数据在库里、API 读不到、零报错）"
    )


def test_ai_call_log_summary_drops_any_key_it_does_not_declare(tmp_path: Path):
    """摘要模型**刻意不加** ``extra="allow"``：未声明的键在出口就被丢弃。

    这是密钥面的第二道闸：即便将来有人往 ``_SUMMARY_FIELDS`` 里加了一个 ``api_key``
    列（``_summary`` 的黑名单若同时被改掉），声明外的键也**到不了客户端**。

    ⚠️ 说准机制：pydantic v2 默认 ``extra="ignore"`` ⇒ 是**静默丢弃**，**不是** 500。
    代价是「加了一列却被模型吞掉」——本组把它记为已知取舍（宁可丢也不泄）。
    """
    # ⚠️ 字面量逐键写死，不从 model_fields 生成（否则该断言与被测物同源）
    payload = {
        "call_id": "aic_probe",
        "run_id": "wfr_probe",
        "node_run_id": None,
        "agent_id": None,
        "model_id": "mock/mock",
        "prompt_version": "observer:v1",
        "latency_ms": 3,
        "retry_count": 0,
        "error": None,
        "token_usage": {"total": 7},
        "cost": None,
        "created_at": "2026-10-15T00:00:00+00:00",
    }
    assert set(payload) == set(EXPECTED_AI_CALL_LOG_KEYS), "夹具本身与实测基准脱节了"
    dumped = AiCallLogSummary(**payload, api_key="sk-leak", a_future_column=1).model_dump()
    assert set(dumped) == set(EXPECTED_AI_CALL_LOG_KEYS), sorted(dumped)
    assert "api_key" not in dumped


def _placeholder_for(name: str, field) -> object:
    """按字段注解造一个合法占位值（不依赖任何端点输出或既有夹具）。"""
    annotation = str(field.annotation)
    if "dict" in annotation:
        return {}
    if "list" in annotation:
        return []
    if "bool" in annotation:
        return False
    if "int" in annotation and "str" not in annotation:
        return 0
    return name + "-placeholder"
