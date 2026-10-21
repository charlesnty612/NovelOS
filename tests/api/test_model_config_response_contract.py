"""模型档案 / 配置 / 环节绑定 / agent 通道的 response_model 契约测试（G5 配置面）。

测法：先量再建
--------------
本组 13 个端点此前全部返回裸 dict、无 ``response_model``。字段形状**不是照源码读出来的**，
是 2026-10-15 用「临时库 + 合成夹具跑通端点、把实际键集 dump 出来」量的，
再建 :mod:`packages.core.model_router.models`。测量脚本的口径：
``create_app(Settings(data_dir=tmp))`` + ASGITransport，全程 ``provider="mock"``
（**不发起任何真实模型调用**），并额外用「不可达 base_url + monkeypatch provider」
补齐了真实 provider 分支的错误形状。

⚠️ 本文件刻意**不与被测物比对**（AGENTS 坑区「断言里只有被测物和它自己 ⇒ 自指断言恒真」
以及 2026-10-15 M14/M15/M20 三条同族实测）。三条基准各自独立：

1. **实测硬编码基准** ``EXPECTED_*``：写死的是 2026-10-15 量到的键集，**不是**从
   ``Model.model_fields`` 抄来的；断言写成 ``set(body) == EXPECTED_PROFILE_KEYS``。
2. **DB 实表列基准**：``ModelProfileOut`` / ``ModelConfigOut`` 的字段集直接与
   ``PRAGMA table_info`` 的**真实列名**比对（再加 ``has_api_key``）——基准来自
   迁移文件，与端点、与模型都无关，从根上排除「夹具抄实现」（坑区「夹具从被测代码的
   实际输出里抄形状」）。
3. **模型 vs 硬编码** 双保险：``set(M.model_fields) == EXPECTED_*``，与第 1 条互补
   （防「模型声明了但端点没出」与「端点出了但模型没声明」两个方向）。

🔑 密钥面（实测结论，见 ``test_api_key_is_masked_and_never_leaves_the_server``）：
``params_json.api_key`` 在**读路径**恒被替换为 ``"***"``，顶层另有 ``has_api_key``；
明文密钥不会经任何 GET/POST/PATCH 响应回吐。行为为既有，本批未改，只钉住它。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import get_connection
from packages.core.model_router import ModelRouter, ProviderError
from packages.core.model_router.models import (
    AgentPromptSyncResult,
    AgentRunResult,
    AvailableModelsOut,
    BindingProfileOut,
    CapabilityBindingDeleted,
    CapabilityBindingOut,
    ConfigTestResult,
    ModelConfigOut,
    ModelProfileOut,
    ProfileTestResult,
)

# ============================================================ 实测基准（第三方来源）
# ⚠️ 这套键集写死自 2026-10-15 的临时库实测，**不是**从 model_fields 抄的。
# 「模型字段集 == 硬编码」另立一条用例（双保险），两条互不替代。

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
        # ⚠️ 2026-10-21 批次十五（连接层）：档案可引用一条「连接」
        # （model_connections）；null = 独立配置（存量形态）。
        # ⚠️ 它**有默认值 None** ⇒ 进 properties、不进 required（见 EXPECTED_REQUIRED）。
        "connection_id",
    }
)
EXPECTED_CONFIG_KEYS = frozenset(
    {
        "config_id",
        "capability",
        "provider",
        "model",
        "params_json",
        "enabled",
        "has_api_key",
    }
)
EXPECTED_PROFILE_TEST_KEYS = frozenset({"profile_id", "ok", "latency_ms", "detail", "status_code"})
# ⚠️ 2026-10-21 批次十五：``probe``（探测走 models 还是退化的 chat 探测）是
# **条件键** —— 只有 provider 的 health_check 返回了它，响应里才带这个键
# （测试里的 stub provider 不返回 ⇒ 响应里没有）。
# ⇒ 「模型声明的字段集 / OpenAPI properties」含 probe，
#   「响应实测键集」不含 —— 两个基线必须分开，否则无法表达条件键。
EXPECTED_PROFILE_TEST_KEYS_WITH_HINT = EXPECTED_PROFILE_TEST_KEYS | {"hint"}
EXPECTED_PROFILE_TEST_KEYS_DECLARED = EXPECTED_PROFILE_TEST_KEYS | {"hint", "probe"}
EXPECTED_CONFIG_TEST_KEYS = frozenset({"config_id", "ok", "latency_ms", "detail", "status_code"})
EXPECTED_AVAILABLE_MODELS_KEYS = frozenset({"models"})
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
EXPECTED_BINDING_PROFILE_KEYS = frozenset({"profile_id", "name", "model", "provider"})
EXPECTED_BINDING_DELETED_KEYS = frozenset({"capability", "deleted"})
EXPECTED_SYNC_KEYS = frozenset({"scanned", "registered", "updated", "agents"})
EXPECTED_RUN_KEYS = frozenset({"run_id", "agent", "output"})

# (path, method) → (HTTP 状态码, OpenAPI schema 名)。13 个端点一个不少。
EXPECTED_RESPONSE_SCHEMA: dict[tuple[str, str], tuple[str, str]] = {
    ("/api/model-profiles", "post"): ("201", "ModelProfileOut"),
    ("/api/model-profiles/{profile_id}", "get"): ("200", "ModelProfileOut"),
    ("/api/model-profiles/{profile_id}", "patch"): ("200", "ModelProfileOut"),
    ("/api/model-profiles/{profile_id}/test", "post"): ("200", "ProfileTestResult"),
    ("/api/model-profiles/available-models", "post"): ("200", "AvailableModelsOut"),
    ("/api/model-configs", "post"): ("201", "ModelConfigOut"),
    ("/api/model-configs/{config_id}", "get"): ("200", "ModelConfigOut"),
    ("/api/model-configs/{config_id}", "patch"): ("200", "ModelConfigOut"),
    ("/api/model-configs/{config_id}/test", "post"): ("200", "ConfigTestResult"),
    ("/api/capability-bindings/{capability}", "put"): ("200", "CapabilityBindingOut"),
    ("/api/capability-bindings/{capability}", "delete"): ("200", "CapabilityBindingDeleted"),
    ("/api/agents/sync", "post"): ("200", "AgentPromptSyncResult"),
    ("/api/agents/{name}/run", "post"): ("201", "AgentRunResult"),
}

# OpenAPI 里必须出现在 properties 里的键（extra="allow" 的模型：删了声明响应照样有该键，
# 响应侧断言抓不到——见 test_openapi_declares_every_key）。
EXPECTED_DECLARED_PROPERTIES = {
    "ModelProfileOut": EXPECTED_PROFILE_KEYS,
    "ModelConfigOut": EXPECTED_CONFIG_KEYS,
    "CapabilityBindingOut": EXPECTED_BINDING_KEYS,
    "BindingProfileOut": EXPECTED_BINDING_PROFILE_KEYS,
    "ProfileTestResult": EXPECTED_PROFILE_TEST_KEYS_DECLARED,
    "ConfigTestResult": EXPECTED_CONFIG_TEST_KEYS,
    "AvailableModelsOut": EXPECTED_AVAILABLE_MODELS_KEYS,
    "CapabilityBindingDeleted": EXPECTED_BINDING_DELETED_KEYS,
    "AgentPromptSyncResult": EXPECTED_SYNC_KEYS,
    "AgentRunResult": EXPECTED_RUN_KEYS,
}
# 无默认值 ⇒ 进 required。hint / updated 之外的可空键同样必在（值可为 null，但键恒在）。
EXPECTED_REQUIRED: dict[str, frozenset[str]] = {
    "ModelProfileOut": EXPECTED_PROFILE_KEYS - {"connection_id"},
    "ModelConfigOut": EXPECTED_CONFIG_KEYS,
    "CapabilityBindingOut": EXPECTED_BINDING_KEYS,
    "BindingProfileOut": EXPECTED_BINDING_PROFILE_KEYS,
    # ProfileTestResult：hint / probe 有默认值 ⇒ 条件键，不进 required
    "ProfileTestResult": EXPECTED_PROFILE_TEST_KEYS,
    "ConfigTestResult": EXPECTED_CONFIG_TEST_KEYS,
    "AvailableModelsOut": EXPECTED_AVAILABLE_MODELS_KEYS,
    "CapabilityBindingDeleted": EXPECTED_BINDING_DELETED_KEYS,
    "AgentPromptSyncResult": EXPECTED_SYNC_KEYS,
    "AgentRunResult": EXPECTED_RUN_KEYS,
}

SECRET = "sk-do-not-leak-abc123"


# ============================================================ 夹具


def _create_app(tmp_path: Path):
    return create_app(Settings(data_dir=tmp_path, log_level="ERROR"))


@asynccontextmanager
async def _client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


def _req(app, method: str, path: str, **kwargs) -> httpx.Response:
    async def run():
        async with _client(app) as c:
            return await c.request(method, path, **kwargs)

    return asyncio.run(run())


def _db_columns(app, table: str) -> set[str]:
    """真实表列名（第三方基准）——不是端点输出，也不是模型字段。

    ⚠️ 必须先进 lifespan：迁移是 app 启动时跑的，不进 lifespan 的临时库是空表，
    ``PRAGMA table_info`` 会返回空集——**那正是「量具没量到目标却报告量过了」**
    （坑区「守卫自己被悄悄缩小了执行域」的同族）。故这里显式断言非空。
    """

    async def run() -> set[str]:
        async with app.router.lifespan_context(app):
            conn = get_connection(app.state.settings.db_path)
            try:
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            finally:
                conn.close()
            return {r["name"] for r in rows}

    cols = asyncio.run(run())
    assert cols, f"{table} 在临时库里没有列——迁移没跑？量具失效，本用例结论不可信"
    return cols


def _seed_profile(app, **overrides) -> dict:
    body = {
        "name": "契约档案",
        "provider": "mock",
        "model": "mock-model",
        "params_json": {"api_key": SECRET, "temperature": 0.7},
    }
    body.update(overrides)
    r = _req(app, "POST", "/api/model-profiles", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _seed_config(app, **overrides) -> dict:
    body = {
        "capability": "creative_writing",
        "provider": "mock",
        "model": "mock-model",
        "params_json": {"api_key": SECRET, "top_p": 0.9},
    }
    body.update(overrides)
    r = _req(app, "POST", "/api/model-configs", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _schema_of(schema: dict, path: str, method: str, code: str) -> str | None:
    op = schema["paths"][path][method]["responses"].get(code)
    if not op:
        return None
    ref = op["content"]["application/json"]["schema"].get("$ref")
    return ref.rsplit("/", 1)[-1] if ref else None


# ============================================================ ① 响应键集（实测基准）


def test_profile_write_and_read_paths_return_the_measured_ten_keys(tmp_path: Path):
    """POST / GET / PATCH 三条路径都恰好 10 键（含无参数时建的空 params 档案）。

    ⚠️ 2026-10-21 批次十五（连接层）：9 → 10 键，新增 ``connection_id``
    （档案引用的连接；null = 独立配置）。**基线是硬编码的第三方基准**，
    改它 = 承认一次契约变更，不能顺手改。
    """
    app = _create_app(tmp_path)
    created = _seed_profile(app)
    assert set(created) == set(EXPECTED_PROFILE_KEYS), (
        f"POST 多={sorted(set(created) - EXPECTED_PROFILE_KEYS)} "
        f"少={sorted(EXPECTED_PROFILE_KEYS - set(created))}"
    )

    # 空 params 的档案：params_json 是 {}（不是 null、也不是省略）
    bare = _seed_profile(app, name="无参", params_json=None)
    assert set(bare) == set(EXPECTED_PROFILE_KEYS)
    assert bare["params_json"] == {}
    assert bare["has_api_key"] is False

    got = _req(app, "GET", f"/api/model-profiles/{created['profile_id']}")
    assert got.status_code == 200, got.text
    assert set(got.json()) == set(EXPECTED_PROFILE_KEYS)

    patched = _req(
        app, "PATCH", f"/api/model-profiles/{created['profile_id']}", json={"name": "改名"}
    )
    assert patched.status_code == 200, patched.text
    assert set(patched.json()) == set(EXPECTED_PROFILE_KEYS)
    assert patched.json()["name"] == "改名"

    # 空 PATCH 体（走「无字段 → 直接返回当前行」分支），键集不得分叉
    noop = _req(app, "PATCH", f"/api/model-profiles/{created['profile_id']}", json={})
    assert noop.status_code == 200, noop.text
    assert set(noop.json()) == set(EXPECTED_PROFILE_KEYS)


def test_config_write_and_read_paths_return_the_measured_seven_keys(tmp_path: Path):
    """model_configs 恰好 7 键——比档案少 created_at/updated_at（表里就没这两列）。"""
    app = _create_app(tmp_path)
    created = _seed_config(app)
    assert set(created) == set(EXPECTED_CONFIG_KEYS), (
        f"POST 多={sorted(set(created) - EXPECTED_CONFIG_KEYS)} "
        f"少={sorted(EXPECTED_CONFIG_KEYS - set(created))}"
    )

    got = _req(app, "GET", f"/api/model-configs/{created['config_id']}")
    assert got.status_code == 200, got.text
    assert set(got.json()) == set(EXPECTED_CONFIG_KEYS)

    patched = _req(
        app, "PATCH", f"/api/model-configs/{created['config_id']}", json={"model": "mock-model-2"}
    )
    assert patched.status_code == 200, patched.text
    assert set(patched.json()) == set(EXPECTED_CONFIG_KEYS)
    assert patched.json()["model"] == "mock-model-2"

    noop = _req(app, "PATCH", f"/api/model-configs/{created['config_id']}", json={})
    assert set(noop.json()) == set(EXPECTED_CONFIG_KEYS)


def test_available_models_returns_only_the_models_key(tmp_path: Path):
    """mock provider 分支是唯一不发起外部请求的可用路径 ⇒ 端到端跑它。"""
    app = _create_app(tmp_path)
    r = _req(app, "POST", "/api/model-profiles/available-models", json={"provider": "mock"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == set(EXPECTED_AVAILABLE_MODELS_KEYS), body
    assert body["models"] == ["mock-model"]


# ============================================================ ② 模型字段 vs 硬编码（双保险）


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (ModelProfileOut, EXPECTED_PROFILE_KEYS),
        (ModelConfigOut, EXPECTED_CONFIG_KEYS),
        (ProfileTestResult, EXPECTED_PROFILE_TEST_KEYS_DECLARED),
        (ConfigTestResult, EXPECTED_CONFIG_TEST_KEYS),
        (AvailableModelsOut, EXPECTED_AVAILABLE_MODELS_KEYS),
        (CapabilityBindingOut, EXPECTED_BINDING_KEYS),
        (BindingProfileOut, EXPECTED_BINDING_PROFILE_KEYS),
        (CapabilityBindingDeleted, EXPECTED_BINDING_DELETED_KEYS),
        (AgentPromptSyncResult, EXPECTED_SYNC_KEYS),
        (AgentRunResult, EXPECTED_RUN_KEYS),
    ],
)
def test_model_field_set_matches_the_hardcoded_baseline(model, expected):
    """第二基准：模型声明的字段集 == 实测硬编码键集。

    与 test_*_return_*the_measured_*keys 互补——那几条抓「端点少出/多出键」，
    这条抓「模型少声明/多声明键」。任一侧单独改都会红。
    """
    declared = set(model.model_fields)
    assert declared == set(expected), (
        f"{model.__name__} 多={sorted(declared - set(expected))} "
        f"少={sorted(set(expected) - declared)}"
    )


def test_row_models_declare_exactly_the_real_database_columns(tmp_path: Path):
    """第三基准：行模型的字段集 == **DB 实表列** ∪ {has_api_key}。

    为什么要有这条：端点返回的是 service 的 ``SELECT *``。若模型少声明一列，
    ``response_model`` 会**静默把它过滤掉**——症状是「后端返回了、API 取不到、零报错」
    （坑区「数据源存在 ≠ 已进装配」的同族）。基准取自 ``PRAGMA table_info``，
    **既不抄端点输出也不抄模型**，故不可能与缺陷同源。
    """
    app = _create_app(tmp_path)
    assert set(ModelProfileOut.model_fields) == _db_columns(app, "model_profiles") | {
        "has_api_key"
    }
    assert set(ModelConfigOut.model_fields) == _db_columns(app, "model_configs") | {
        "has_api_key"
    }
    # 反向护栏：这两张表**没有**时间戳列——若将来加了，模型必须跟着加，
    # 否则新列会被静默丢弃（上面的断言届时会红，这里只是把原因写明）。
    assert "created_at" not in _db_columns(app, "model_configs")
    assert "updated_at" not in _db_columns(app, "model_configs")


# ============================================================ ③ response_model 确已声明


def test_every_modelled_path_declares_its_response_schema(tmp_path: Path):
    """13 个端点逐一断言 OpenAPI 里挂的是约定 schema。

    撤掉任一 ``response_model=`` ⇒ 该条 $ref 消失 ⇒ 必红。
    """
    app = _create_app(tmp_path)
    schema = app.openapi()
    for (path, method), (code, expected_name) in EXPECTED_RESPONSE_SCHEMA.items():
        actual = _schema_of(schema, path, method, code)
        assert actual == expected_name, (
            f"{method.upper()} {path} [{code}] 应声明 {expected_name}，实得 {actual!r}"
        )


@pytest.mark.parametrize("model_name", sorted(EXPECTED_DECLARED_PROPERTIES))
def test_openapi_declares_every_key(model_name: str, tmp_path: Path):
    """⚠️ extra="allow" 的模型：**响应里有该键 ≠ schema 里声明了该键**。

    把字段声明从模型里删掉，`extra="allow"` 会让它以 extra 身份**照样透出**，
    响应侧的键集断言恒绿（2026-10-15 M20 实测存活）。只断言响应抓不到这一条，
    必须另断言 OpenAPI 的 ``properties`` / ``required``。
    """
    app = _create_app(tmp_path)
    components = app.openapi()["components"]["schemas"]
    assert model_name in components, f"schema 缺 {model_name}"
    spec = components[model_name]
    props = set(spec.get("properties", {}))
    expected_props = set(EXPECTED_DECLARED_PROPERTIES[model_name])
    assert expected_props <= props, (
        f"{model_name} 未声明：{sorted(expected_props - props)}"
    )
    required = set(spec.get("required", []))
    expected_required = set(EXPECTED_REQUIRED[model_name])
    assert expected_required <= required, (
        f"{model_name} 该进 required 的没进：{sorted(expected_required - required)}"
    )
    # 反向：hint / probe 是条件键，**不该**出现在 required 里
    if model_name == "ProfileTestResult":
        for cond_key in ("hint", "probe"):
            assert cond_key not in required, (
                f"{cond_key} 是条件键（命中才出现），进 required 会让前端误以为它恒在"
            )
    if model_name == "ModelProfileOut":
        assert "connection_id" not in required, (
            "connection_id 有默认值 None（独立配置的档案）⇒ 不该进 required"
        )


# ============================================================ ④ /test 的条件键与可空键


def test_profile_test_omits_hint_unless_the_400_body_names_the_model(tmp_path: Path):
    """mock provider 恒 200 ⇒ hint 命中分支不触发 ⇒ 恰好 5 键，**没有** hint。

    这条是 ``response_model_exclude_unset=True`` 的守门人：去掉它，Pydantic 会把
    未提供的 ``hint`` 序列化成 ``null``，响应凭空多一个键 ⇒ 必红。
    """
    app = _create_app(tmp_path)
    pid = _seed_profile(app)["profile_id"]
    r = _req(app, "POST", f"/api/model-profiles/{pid}/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == set(EXPECTED_PROFILE_TEST_KEYS), body
    assert "hint" not in body
    assert body["ok"] is True and body["status_code"] == 200


class _StubProvider:
    """替换 ModelRouter.get_provider 的桩——**不发任何网络请求**。"""

    def __init__(self, result: dict | None = None, exc: Exception | None = None) -> None:
        self._result = result
        self._exc = exc

    def health_check(self) -> dict:
        if self._exc is not None:
            raise self._exc
        return self._result


def _patch_provider(monkeypatch, stub: _StubProvider) -> None:
    monkeypatch.setattr(ModelRouter, "get_provider", lambda self, row, **kw: stub)


@pytest.mark.parametrize(
    ("detail", "expect_hint"),
    [
        ("HTTP 400: unknown model gpt-x", True),
        ("HTTP 400: invalid api key", False),
        ("connection refused", False),
    ],
)
def test_profile_test_adds_hint_only_when_the_400_body_names_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, detail: str, expect_hint: bool
):
    """400 + 命中模型名标记 ⇒ 6 键（多 ``hint``）；其它 400 ⇒ 仍 5 键。

    反向断言与正例构成一对：单有正例，"hint 恒在"也能过；单有反例，"hint 从不出现"
    也能过。
    """
    app = _create_app(tmp_path)
    pid = _seed_profile(app)["profile_id"]
    _patch_provider(
        monkeypatch,
        _StubProvider({"ok": False, "status_code": 400, "detail": detail, "latency_ms": 7}),
    )
    r = _req(app, "POST", f"/api/model-profiles/{pid}/test")
    assert r.status_code == 200, r.text
    body = r.json()
    expected = EXPECTED_PROFILE_TEST_KEYS_WITH_HINT if expect_hint else EXPECTED_PROFILE_TEST_KEYS
    assert set(body) == set(expected), body
    assert body["latency_ms"] == 7 and body["status_code"] == 400 and body["ok"] is False
    if expect_hint:
        assert "模型名问题" in body["hint"]


def test_profile_test_hint_requires_its_own_declaration(tmp_path: Path, monkeypatch):
    """删掉 ``hint`` 的字段声明 ⇒ 严格模型会把它**过滤掉** ⇒ 必红。

    这条与 ``test_openapi_declares_every_key[ProfileTestResult]`` 互补：若哪天给
    ``ProfileTestResult`` 加上 ``extra="allow"``，本条会恒绿（键以 extra 透出），
    那时靠 OpenAPI 侧那条接着守。两条必须都在。
    """
    app = _create_app(tmp_path)
    pid = _seed_profile(app)["profile_id"]
    _patch_provider(
        monkeypatch,
        _StubProvider(
            {"ok": False, "status_code": 400, "detail": "unknown model x", "latency_ms": 1}
        ),
    )
    r = _req(app, "POST", f"/api/model-profiles/{pid}/test")
    assert r.status_code == 200, r.text
    # 模型层直接断言声明存在（端点侧那条只证明它出参了）
    assert "hint" in ProfileTestResult.model_fields
    assert r.json()["hint"]


def test_test_status_code_is_present_and_nullable_not_omitted(tmp_path: Path, monkeypatch):
    """网络异常时 provider 返回 ``status_code: None`` ⇒ 该键**恒在且为 null**。

    这是 ``exclude_unset`` 与 ``exclude_none`` 的分水岭：用 ``exclude_none``
    会把这一项删掉（改变实测行为），用默认值又会把它变成「可省略」。
    """
    app = _create_app(tmp_path)
    pid = _seed_profile(app)["profile_id"]
    cfg_id = _seed_config(app)["config_id"]

    _patch_provider(
        monkeypatch,
        _StubProvider({"ok": False, "status_code": None, "detail": "network error", "latency_ms": 3}),
    )
    for path, expected in (
        (f"/api/model-profiles/{pid}/test", EXPECTED_PROFILE_TEST_KEYS),
        (f"/api/model-configs/{cfg_id}/test", EXPECTED_CONFIG_TEST_KEYS),
    ):
        r = _req(app, "POST", path)
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == set(expected), body
        assert "status_code" in body, f"{path}: status_code 被省略了（应为 null）"
        assert body["status_code"] is None


def test_config_test_has_no_hint_key_even_on_a_model_name_400(tmp_path: Path, monkeypatch):
    """两个 ``/test`` 端点**不对称**：只有档案侧接了 ``_unknown_model_hint``。

    这是既有行为（task-28 只改了档案侧），本批不改动它——但把不对称钉住，
    免得将来有人只改一边、或以为两边本来就该一样。
    """
    app = _create_app(tmp_path)
    cfg_id = _seed_config(app)["config_id"]
    _patch_provider(
        monkeypatch,
        _StubProvider(
            {"ok": False, "status_code": 400, "detail": "unknown model x", "latency_ms": 2}
        ),
    )
    r = _req(app, "POST", f"/api/model-configs/{cfg_id}/test")
    assert r.status_code == 200, r.text
    assert set(r.json()) == set(EXPECTED_CONFIG_TEST_KEYS), r.json()


# ============================================================ ⑤ 环节绑定


def test_capability_binding_put_returns_measured_keys_and_null_when_unbound(tmp_path: Path):
    """未绑定时 ``updated_at`` 是 **null 而非省略**；``profiles`` / ``profile_ids`` 是空数组。"""
    app = _create_app(tmp_path)
    pid = _seed_profile(app)["profile_id"]

    r = _req(app, "PUT", "/api/capability-bindings/reasoning", json={"profile_ids": [pid]})
    assert r.status_code == 200, r.text
    bound = r.json()
    assert set(bound) == set(EXPECTED_BINDING_KEYS), bound
    assert bound["updated_at"] is not None, "绑定成功后 updated_at 恒为时间戳字符串"
    assert bound["profile_ids"] == [pid]
    assert len(bound["profiles"]) == 1
    assert set(bound["profiles"][0]) == set(EXPECTED_BINDING_PROFILE_KEYS), bound["profiles"][0]
    # 绑定列表刻意不暴露 params_json / has_api_key
    assert "params_json" not in bound["profiles"][0]
    assert "has_api_key" not in bound["profiles"][0]

    # 换一个未绑定的环节：走 GET 列表拿到「未绑定」形态（PUT 本身总会写入）
    listed = _req(app, "GET", "/api/capability-bindings")
    assert listed.status_code == 200, listed.text
    unbound = next(i for i in listed.json() if i["capability"] == "observer")
    assert set(unbound) == set(EXPECTED_BINDING_KEYS), unbound
    assert "updated_at" in unbound, "未绑定时 updated_at 必须**存在**（值 null），不得省略"
    assert unbound["updated_at"] is None
    assert unbound["profile_ids"] == [] and unbound["profiles"] == []
    assert unbound["legacy_available"] is False


def test_capability_binding_delete_returns_capability_and_deleted(tmp_path: Path):
    app = _create_app(tmp_path)
    pid = _seed_profile(app)["profile_id"]
    _req(app, "PUT", "/api/capability-bindings/light", json={"profile_ids": [pid]})

    r = _req(app, "DELETE", "/api/capability-bindings/light")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == set(EXPECTED_BINDING_DELETED_KEYS), body
    assert body == {"capability": "light", "deleted": True}

    again = _req(app, "DELETE", "/api/capability-bindings/light")
    assert again.status_code == 404, again.text


# ============================================================ ⑥ agent 通道


@pytest.mark.parametrize(
    ("model_name", "field"),
    [
        ("CapabilityBindingOut", "updated_at"),
        ("ProfileTestResult", "status_code"),
        ("ConfigTestResult", "status_code"),
    ],
)
def test_nullable_keys_are_declared_nullable_and_required(
    tmp_path: Path, model_name: str, field: str
):
    """⚠️「可空但键恒在」的三处，键集断言抓不到——它们是**同一个键在两种取值**。

    - 把 ``Optional[str]`` 改成 ``str``：``required`` 还在，键集不变，只有类型变了；
    - 加个 ``= None`` 默认值：类型不变，但 ``required`` 掉了（前端会把它当可省略）。
    ⇒ 只能从 OpenAPI 的 ``anyOf`` 含 ``null`` + ``required`` 两处判。
    """
    components = _create_app(tmp_path).openapi()["components"]["schemas"]
    spec = components[model_name]
    prop = spec["properties"][field]
    assert {"type": "null"} in prop.get("anyOf", []), (
        f"{model_name}.{field} 在 schema 里不可空，但后端会返回 null：{prop}"
    )
    assert field in spec.get("required", []), (
        f"{model_name}.{field} 键恒在（无数据时值 null），不该被声明成可省略"
    )


def test_agents_sync_returns_the_four_measured_keys(tmp_path: Path):
    app = _create_app(tmp_path)
    r = _req(app, "POST", "/api/agents/sync")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == set(EXPECTED_SYNC_KEYS), body
    for key in ("scanned", "registered", "updated", "agents"):
        assert isinstance(body[key], list), f"{key} 恒为数组（无数据时是 [] 而非省略）"
    # (agent, version) 二元组：JSON 侧是二元素数组
    for entry in body["scanned"]:
        assert isinstance(entry, list) and len(entry) == 2, entry


def test_agent_run_returns_run_id_agent_and_agent_specific_output(tmp_path: Path):
    """``output`` **随 agent 而异** ⇒ 只能声明成 dict[str, Any]，不得臆造统一字段集。

    走 mock_script + expected=observer，全程不发起真实模型调用。
    """
    app = _create_app(tmp_path)
    r = _req(
        app,
        "POST",
        "/api/agents/observer/run",
        json={"input_payload": {}, "expected": "observer", "mock_script": ["{}"]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == set(EXPECTED_RUN_KEYS), body
    assert body["agent"] == "observer"
    assert body["run_id"].startswith("wfr_")
    assert isinstance(body["output"], dict) and body["output"], body
    # observer 的 7 段 delta 是**该 agent 的契约**，不是端点的统一形状
    assert set(body["output"]) == {
        "new_hooks",
        "new_events",
        "world_changes",
        "resolved_hooks",
        "relationship_changes",
        "debt_changes",
        "character_changes",
    }, body["output"]


# ============================================================ ⑦ 错误体（实测形状）


def test_provider_error_envelope_shape_is_modeled(tmp_path: Path, monkeypatch):
    """ProviderError → 502，detail 是 {error, message, status_code} 三键。"""
    app = _create_app(tmp_path)
    pid = _seed_profile(app)["profile_id"]
    cfg_id = _seed_config(app)["config_id"]
    _patch_provider(
        monkeypatch, _StubProvider(exc=ProviderError("mock", "boom", status_code=503))
    )
    for path in (f"/api/model-profiles/{pid}/test", f"/api/model-configs/{cfg_id}/test"):
        r = _req(app, "POST", path)
        assert r.status_code == 502, r.text
        detail = r.json()["detail"]
        assert set(detail) == {"error", "message", "status_code"}, detail
        assert detail["error"] == "provider_error"
        assert detail["status_code"] == 503


def test_agent_output_invalid_envelope_shape_is_modeled(tmp_path: Path):
    """mock_script 输出不合契约 → 502，detail 是 {error, message} **两键**（无 status_code）。"""
    app = _create_app(tmp_path)
    r = _req(
        app,
        "POST",
        "/api/agents/writer/run",
        json={
            "input_payload": {},
            "expected": "writer",
            "mock_script": ['{"schema_version": "writer.v3"}'],
        },
    )
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert set(detail) == {"error", "message"}, detail
    assert detail["error"] == "agent_output_invalid"
    assert "writer-output.v1" in detail["message"]


# ============================================================ ⑧ 密钥面（🔑 实测结论）


def test_api_key_is_masked_and_never_leaves_the_server(tmp_path: Path):
    """🔑 密钥出参面：读路径恒脱敏为 ``"***"`` + 顶层 ``has_api_key``；明文零泄漏。

    这条把「读路径脱敏」这一既有契约钉成回归守卫。若哪天 ``_mask_response`` 被摘掉，
    响应里就会出现明文 ⇒ 必红。
    """
    app = _create_app(tmp_path)
    profile = _seed_profile(app)
    config = _seed_config(app)

    for path in (
        f"/api/model-profiles/{profile['profile_id']}",
        f"/api/model-configs/{config['config_id']}",
    ):
        r = _req(app, "GET", path)
        assert r.status_code == 200, r.text
        assert SECRET not in r.text, f"{path} 响应里出现了明文密钥"
        assert r.json()["params_json"]["api_key"] == "***"
        assert r.json()["has_api_key"] is True

    # PATCH 回传掩码 "***" ⇒ 保留 DB 原值（前端编辑表单的标准动作）
    patched = _req(
        app,
        "PATCH",
        f"/api/model-configs/{config['config_id']}",
        json={"params_json": {"api_key": "***", "top_p": 0.5}},
    )
    assert patched.status_code == 200, patched.text
    assert SECRET not in patched.text
    assert patched.json()["params_json"]["api_key"] == "***"
    assert patched.json()["has_api_key"] is True


# ============================================================ ⑨ extra="allow" 的政策


@pytest.mark.parametrize("model", [ModelProfileOut, ModelConfigOut, CapabilityBindingOut])
def test_row_models_tolerate_a_column_added_by_a_later_migration(model):
    """行模型必须 ``extra="allow"``：service 是 ``SELECT *``，将来加列不该被静默丢。

    端点侧抓不到这件事（当前表列恰好都在声明里），只能在模型层注入未知键来判别。
    撤掉 ``extra="allow"`` ⇒ 该键被裁掉 ⇒ 必红。

    ⚠️ 占位值按**字段注解**生成而非照抄实现：抄一份「恰好合法」的样本，
    字段一改类型就会变成「夹具与实现同源」（坑区「夹具从被测代码的实际输出里抄形状」）。
    """
    base = {name: _placeholder_for(name, f) for name, f in model.model_fields.items()}
    base["a_column_added_by_a_later_migration"] = "future-value"
    dumped = model(**base).model_dump()
    assert "a_column_added_by_a_later_migration" in dumped, (
        f"{model.__name__} 把未知键裁掉了 ⇒ extra='allow' 没了 ⇒ "
        "将来加的列会静默从响应消失（数据在库里、API 读不到、零报错）"
    )


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
