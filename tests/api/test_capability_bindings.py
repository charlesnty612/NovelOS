"""Capability Bindings 路由测试（V3.7「模型档案 + 环节绑定」）。

覆盖：
1. GET 列出全部 binding（含未绑定环节）：label / agents / profile_ids / profiles /
   legacy_available 等字段齐备。
2. PUT upsert 合法值：写入后 GET 单项命中、response 字段对齐 GET 列表单项。
3. PUT 未知 capability → 404。
4. PUT profile_ids 含不存在 / 已禁用的 → 422。
5. PUT 空 profile_ids → 422。
6. DELETE 后 GET 列表对应项 profile_ids 为空（fallback 到 model_configs）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations


def _make_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _create_profile(app, **fields) -> str:
    payload = {
        "name": fields.pop("name", "p"),
        "provider": fields.pop("provider", "mock"),
        "model": fields.pop("model", "m1"),
    }
    payload.update(fields)
    r = await _request(app, "POST", "/api/model-profiles", json=payload)
    assert r.status_code == 201, r.text
    return r.json()["profile_id"]


# ---------------------------------------------------------------------------
# 1. GET 列表
# ---------------------------------------------------------------------------


def test_get_returns_all_seven_capabilities_with_label_and_agents(tmp_path: Path):
    """GET 必须返回全部七项 capability（CAPABILITY_LABELS 口径），每项含 label/agents。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/capability-bindings")
            assert r.status_code == 200, r.text
            data = r.json()
            assert isinstance(data, list)
            caps = {item["capability"] for item in data}
            assert caps == {
                "premise_design", "world_building", "character_design",
                "volume_outline", "creative_writing", "reasoning", "light",
            }
            for item in data:
                assert item["label"], f"{item['capability']} label missing"
                assert isinstance(item["agents"], list) and item["agents"]
                assert item["profile_ids"] == []
                assert item["profiles"] == []
                assert item["legacy_available"] is False
                assert item["updated_at"] is None

    asyncio.run(run())


def test_get_legacy_available_true_when_model_configs_has_enabled_row(tmp_path: Path):
    """model_configs 有 enabled 行 → legacy_available=True。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            # 写一条 model_config 行（直接走 DB，绕过 service）
            settings = app.state.settings
            from packages.core.db import get_connection
            from packages.core.ids import new_id
            from datetime import datetime, timezone

            cid = new_id("mcf")
            ts = datetime.now(timezone.utc).isoformat()
            conn = get_connection(settings.db_path)
            try:
                conn.execute(
                    "INSERT INTO model_configs (config_id, capability, provider, model, params_json, enabled) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (cid, "reasoning", "openai", "gpt-4o", "{}", 1),
                )
                conn.commit()
            finally:
                conn.close()

            r = await _request(app, "GET", "/api/capability-bindings")
            assert r.status_code == 200, r.text
            by_cap = {i["capability"]: i for i in r.json()}
            assert by_cap["reasoning"]["legacy_available"] is True
            assert by_cap["creative_writing"]["legacy_available"] is False

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 2. PUT upsert
# ---------------------------------------------------------------------------


def test_put_upsert_returns_full_enriched_item(tmp_path: Path):
    """PUT upsert 后 GET 单项返回 enriched 形态（含 profiles 解析详情）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid1 = await _create_profile(app, name="alpha", provider="mock", model="m1")
            pid2 = await _create_profile(app, name="beta", provider="mock", model="m2")

            r = await _request(
                app, "PUT", "/api/capability-bindings/creative_writing",
                json={"profile_ids": [pid1, pid2]},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["capability"] == "creative_writing"
            assert data["label"] == "正文写作"
            assert data["agents"] == ["writer", "polisher", "scene_planner"]  # V3.9.2+：scene_planner 按 chapter-write 管线阶段归入 creative_writing
            assert data["profile_ids"] == [pid1, pid2]
            assert len(data["profiles"]) == 2
            assert data["profiles"][0]["profile_id"] == pid1
            assert data["profiles"][0]["model"] == "m1"
            assert data["legacy_available"] is False
            assert data["updated_at"]

    asyncio.run(run())


def test_put_unknown_capability_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _create_profile(app)
            r = await _request(
                app, "PUT", "/api/capability-bindings/no_such_capability",
                json={"profile_ids": [pid]},
            )
            assert r.status_code == 404, r.text
            assert "no_such_capability" in r.json()["detail"]

    asyncio.run(run())


def test_put_missing_profile_returns_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "PUT", "/api/capability-bindings/reasoning",
                json={"profile_ids": ["mprof_nonexistent"]},
            )
            assert r.status_code == 422, r.text

    asyncio.run(run())


def test_put_disabled_profile_returns_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _create_profile(app, enabled=0)
            r = await _request(
                app, "PUT", "/api/capability-bindings/reasoning",
                json={"profile_ids": [pid]},
            )
            assert r.status_code == 422, r.text

    asyncio.run(run())


def test_put_empty_profile_ids_returns_422(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "PUT", "/api/capability-bindings/reasoning",
                json={"profile_ids": []},
            )
            assert r.status_code == 422, r.text

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 3. DELETE
# ---------------------------------------------------------------------------


def test_delete_then_get_profile_ids_empty(tmp_path: Path):
    """DELETE 后 GET 该 capability 的 profile_ids 应回到空列表（回落旧行为）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _create_profile(app)
            r = await _request(
                app, "PUT", "/api/capability-bindings/light",
                json={"profile_ids": [pid]},
            )
            assert r.status_code == 200, r.text

            r = await _request(app, "DELETE", "/api/capability-bindings/light")
            assert r.status_code == 200, r.text
            assert r.json()["deleted"] is True

            r = await _request(app, "GET", "/api/capability-bindings")
            assert r.status_code == 200
            by_cap = {i["capability"]: i for i in r.json()}
            assert by_cap["light"]["profile_ids"] == []
            assert by_cap["light"]["profiles"] == []
            assert by_cap["light"]["updated_at"] is None

    asyncio.run(run())


def test_delete_unknown_capability_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "DELETE", "/api/capability-bindings/no_such_capability")
            assert r.status_code == 404, r.text

    asyncio.run(run())
