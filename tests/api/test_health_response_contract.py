"""`GET /api/health` 的 response_model 契约（2026-10-15 批次（六））。

`/api/health` 此前也是裸 `dict` ⇒ OpenAPI 无 schema ⇒ 前端手写类型。
本批把它补上；这是全仓**唯一一个**定义在 ``core/api/main.py`` 里的出参模型
（其余 router 模型都在各领域包里），所以它的契约也一并钉住。

三条断言，三道不同的网：

1. 响应键集 == **硬编码**期望（不是 == 模型字段集——那是自指，见 AGENTS 坑区）；
2. OpenAPI 里该端点引用了 ``HealthResponse`` 且 schema 有这 3 个 property；
3. 模型字段集 == 硬编码期望（双保险，防止 1 里的基准被「照抄模型」反向污染）。
"""

from __future__ import annotations

from pathlib import Path

from packages.core.api.main import HealthResponse

from ._contract_runner import (
    assert_keys_match,
    assert_model_fields_match,
    assert_openapi_properties_and_required,
    assert_openapi_ref,
    client_ctx,
    create_test_app,
)

# 硬编码：临时库实测得到的三个键，不从模型反推
EXPECTED_HEALTH_KEYS = {"status", "version", "tables"}


def test_health_response_keys_match_hardcoded_expectation(tmp_path: Path):
    """响应键集逐字等于硬编码期望。"""
    import asyncio

    app = create_test_app(tmp_path)

    async def go() -> dict:
        async with client_ctx(app, base_url="http://t") as c:
            r = await c.get("/api/health")
            assert r.status_code == 200, r.text
            return r.json()

    body = asyncio.run(go())
    assert_keys_match(body, EXPECTED_HEALTH_KEYS, "GET /api/health")
    # 三项都恒非 null（模型不给默认值正是为了钉住这一点）
    for k in EXPECTED_HEALTH_KEYS:
        assert body[k] is not None, f"{k} 不该为 null"


def test_health_schema_is_declared_and_referenced(tmp_path: Path):
    """OpenAPI 里有 ``HealthResponse``，且该端点的 200 引用它。"""
    app = create_test_app(tmp_path)
    schema = app.openapi()
    assert "HealthResponse" in schema["components"]["schemas"], (
        f"OpenAPI 缺 HealthResponse，现有：{sorted(schema['components']['schemas'])}"
    )
    assert_openapi_ref(schema, "/api/health", "get", "200", "HealthResponse")
    props = schema["components"]["schemas"]["HealthResponse"]
    # 三项都必填：模型不给默认值 ⇒ required 覆盖全部
    assert_openapi_properties_and_required(
        props, EXPECTED_HEALTH_KEYS, EXPECTED_HEALTH_KEYS, label="HealthResponse"
    )


def test_health_model_matches_hardcoded_expectation():
    """模型字段集 == 硬编码期望（与上面的响应断言构成双保险）。"""
    assert_model_fields_match(HealthResponse, EXPECTED_HEALTH_KEYS)
