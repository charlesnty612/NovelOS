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

from packages.core.api.main import HealthResponse, create_app
from packages.core.config import Settings

# 硬编码：临时库实测得到的三个键，不从模型反推
EXPECTED_HEALTH_KEYS = {"status", "version", "tables"}


def test_health_response_keys_match_hardcoded_expectation(tmp_path: Path):
    """响应键集逐字等于硬编码期望。"""
    import asyncio

    import httpx

    app = create_app(Settings(data_dir=tmp_path, log_level="WARNING"))

    async def go() -> dict:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                r = await c.get("/api/health")
                assert r.status_code == 200, r.text
                return r.json()

    body = asyncio.run(go())
    assert set(body) == EXPECTED_HEALTH_KEYS, (
        f"多={set(body) - EXPECTED_HEALTH_KEYS} 少={EXPECTED_HEALTH_KEYS - set(body)}"
    )
    # 三项都恒非 null（模型不给默认值正是为了钉住这一点）
    for k in EXPECTED_HEALTH_KEYS:
        assert body[k] is not None, f"{k} 不该为 null"


def test_health_schema_is_declared_and_referenced(tmp_path: Path):
    """OpenAPI 里有 ``HealthResponse``，且该端点的 200 引用它。"""
    app = create_app(Settings(data_dir=tmp_path, log_level="WARNING"))
    schema = app.openapi()
    assert "HealthResponse" in schema["components"]["schemas"], (
        f"OpenAPI 缺 HealthResponse，现有：{sorted(schema['components']['schemas'])}"
    )
    ref = schema["paths"]["/api/health"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    assert ref.endswith("/HealthResponse"), ref
    props = schema["components"]["schemas"]["HealthResponse"]
    assert set(props["properties"]) == EXPECTED_HEALTH_KEYS, sorted(props["properties"])
    # 三项都必填：模型不给默认值 ⇒ required 覆盖全部
    assert set(props.get("required", [])) == EXPECTED_HEALTH_KEYS, props.get("required")


def test_health_model_matches_hardcoded_expectation():
    """模型字段集 == 硬编码期望（与上面的响应断言构成双保险）。"""
    assert set(HealthResponse.model_fields) == EXPECTED_HEALTH_KEYS
