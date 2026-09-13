"""题材包 API 路由测试（题材库 P1a）。

端点：
- ``POST   /projects/{pid}/genre-packs``        —— 创建（payload 校验 → 422）
- ``GET    /genre-packs`` / ``GET /genre-packs/{pack_id}``
- ``PUT    /genre-packs/{pack_id}``             —— 更新（提供 payload → version 自增）
- ``DELETE /genre-packs/{pack_id}``             —— 204；被绑定 → 409
- ``GET    /projects/{pid}/genre-pack``         —— 当前绑定
- ``POST   /projects/{pid}/genre-pack/bind|unbind``
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations

_PAYLOAD: dict = {
    "schema_version": "genre-pack.v1.0.0",
    "payoff_types": [
        {"type_id": "face_slap", "name": "打脸", "strength": "S", "density_cap": "每卷 2~3 次"}
    ],
    "structure_templates": {"structure_model": "单元剧"},
    "pacing": {"chapter_words": {"target": 2500}},
}


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def _run(coro):
    return asyncio.run(coro)


async def _make_project(app, name: str = "题材项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _create_pack(
    app, pid: str, *, pack_id: str = "genre-male-quicktrans-v1", payload: dict | None = None,
) -> httpx.Response:
    return await _request(
        app,
        "POST",
        f"/api/projects/{pid}/genre-packs",
        json={
            "name": "男主快穿",
            "genre_tag": "快穿",
            "payload": _PAYLOAD if payload is None else payload,
            "pack_id": pack_id,
            "source_path": "genres/male-quicktrans",
        },
    )


# ---------------------------------------------------------------------------
# 创建
# ---------------------------------------------------------------------------


def test_create_genre_pack_returns_201(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        r = await _create_pack(app, pid)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["pack_id"] == "genre-male-quicktrans-v1"
        assert body["version"] == 1
        assert body["payload"]["payoff_types"][0]["type_id"] == "face_slap"
        assert body["source_path"] == "genres/male-quicktrans"
        assert body["bound_project_count"] == 0

    _run(run())


def test_create_genre_pack_422_on_invalid_payload(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        # 缺版本锚点 + 爽点 strength 枚举外
        r = await _create_pack(app, pid, payload={"payoff_types": [{"type_id": "x_y", "name": "n", "strength": "XL"}]})
        assert r.status_code == 422, r.text
        errors = r.json()["detail"]["errors"]
        assert any("schema_version" in e for e in errors)
        # 类型错误：pacing 不是对象
        r2 = await _create_pack(app, pid, payload={"schema_version": "genre-pack.v1.0.0", "pacing": []})
        assert r2.status_code == 422, r2.text
        assert r2.json()["detail"]["errors"]

    _run(run())


def test_create_genre_pack_404_unknown_project(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        r = await _create_pack(app, "prj_missing")
        assert r.status_code == 404, r.text

    _run(run())


def test_create_genre_pack_422_on_duplicate_pack_id(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        assert (await _create_pack(app, pid)).status_code == 201
        r = await _create_pack(app, pid)
        assert r.status_code == 422, r.text
        assert "integrity error" in r.json()["detail"]

    _run(run())


# ---------------------------------------------------------------------------
# 列表 / 详情
# ---------------------------------------------------------------------------


def test_list_and_get_genre_pack(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        await _create_pack(app, pid, pack_id="gp_kc")
        await _request(
            app, "POST", f"/api/projects/{pid}/genre-packs",
            json={"name": "都市", "genre_tag": "都市", "payload": {"schema_version": "genre-pack.v1.0.0"}, "pack_id": "gp_ds"},
        )

        r = await _request(app, "GET", "/api/genre-packs")
        assert r.status_code == 200, r.text
        assert {p["pack_id"] for p in r.json()} == {"gp_kc", "gp_ds"}

        r_tag = await _request(app, "GET", "/api/genre-packs?genre_tag=快穿")
        assert [p["pack_id"] for p in r_tag.json()] == ["gp_kc"]

        detail = await _request(app, "GET", "/api/genre-packs/gp_kc")
        assert detail.status_code == 200, detail.text
        assert detail.json()["payload"]["pacing"]["chapter_words"]["target"] == 2500

    _run(run())


def test_get_unknown_genre_pack_returns_404(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        r = await _request(app, "GET", "/api/genre-packs/gp_missing")
        assert r.status_code == 404, r.text

    _run(run())


# ---------------------------------------------------------------------------
# 更新
# ---------------------------------------------------------------------------


def test_put_updates_payload_and_bumps_version(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        await _create_pack(app, pid, pack_id="gp_kc")

        r = await _request(
            app, "PUT", "/api/genre-packs/gp_kc",
            json={"name": "男主快穿（v2）", "payload": {**_PAYLOAD, "pacing": {"chapter_words": {"target": 2400}}}},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["version"] == 2
        assert body["name"] == "男主快穿（v2）"
        assert body["payload"]["pacing"]["chapter_words"]["target"] == 2400
        assert body["genre_tag"] == "快穿", "未提供的字段保持原值"

    _run(run())


def test_put_422_on_invalid_payload_and_404_unknown(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        await _create_pack(app, pid, pack_id="gp_kc")

        bad = await _request(app, "PUT", "/api/genre-packs/gp_kc", json={"payload": {"schema_version": "nope"}})
        assert bad.status_code == 422, bad.text

        missing = await _request(app, "PUT", "/api/genre-packs/gp_missing", json={"name": "x"})
        assert missing.status_code == 404, missing.text

    _run(run())


# ---------------------------------------------------------------------------
# 删除（绑定校验 409）
# ---------------------------------------------------------------------------


def test_delete_unbound_pack_returns_204(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        await _create_pack(app, pid, pack_id="gp_kc")
        r = await _request(app, "DELETE", "/api/genre-packs/gp_kc")
        assert r.status_code == 204, r.text
        assert (await _request(app, "GET", "/api/genre-packs/gp_kc")).status_code == 404
        assert (await _request(app, "DELETE", "/api/genre-packs/gp_kc")).status_code == 404

    _run(run())


def test_delete_bound_pack_returns_409(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        await _create_pack(app, pid, pack_id="gp_kc")
        bind = await _request(app, "POST", f"/api/projects/{pid}/genre-pack/bind", json={"pack_id": "gp_kc"})
        assert bind.status_code == 200, bind.text

        r = await _request(app, "DELETE", "/api/genre-packs/gp_kc")
        assert r.status_code == 409, r.text
        assert "bound" in r.json()["detail"]

        # 先解绑再删 → 204
        await _request(app, "POST", f"/api/projects/{pid}/genre-pack/unbind")
        assert (await _request(app, "DELETE", "/api/genre-packs/gp_kc")).status_code == 204

    _run(run())


# ---------------------------------------------------------------------------
# 绑定 / 解绑 / 绑定读取
# ---------------------------------------------------------------------------


def test_bind_and_get_project_binding(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        await _create_pack(app, pid, pack_id="gp_kc")

        # 未绑定态
        r0 = await _request(app, "GET", f"/api/projects/{pid}/genre-pack")
        assert r0.status_code == 200, r0.text
        assert r0.json() == {"project_id": pid, "pack_id": None, "bound": False, "pack": None}

        r = await _request(app, "POST", f"/api/projects/{pid}/genre-pack/bind", json={"pack_id": "gp_kc"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["bound"] is True and body["pack_id"] == "gp_kc"
        assert body["pack"]["version"] == 1

        # 绑定出现在 pack 摘要的 bound_project_count（DELETE 409 判定同源）
        listed = await _request(app, "GET", "/api/genre-packs/gp_kc")
        assert listed.json()["bound_project_count"] == 1

        # 覆盖式绑定（单 slot）
        await _create_pack(app, pid, pack_id="gp_other")
        r2 = await _request(app, "POST", f"/api/projects/{pid}/genre-pack/bind", json={"pack_id": "gp_other"})
        assert r2.status_code == 200, r2.text
        assert r2.json()["pack_id"] == "gp_other"

    _run(run())


def test_bind_404_on_unknown_pack_or_project(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        await _create_pack(app, pid, pack_id="gp_kc")

        r = await _request(app, "POST", f"/api/projects/{pid}/genre-pack/bind", json={"pack_id": "gp_missing"})
        assert r.status_code == 404, r.text

        r2 = await _request(app, "POST", "/api/projects/prj_missing/genre-pack/bind", json={"pack_id": "gp_kc"})
        assert r2.status_code == 404, r2.text

        r3 = await _request(app, "POST", f"/api/projects/{pid}/genre-pack/bind", json={})
        assert r3.status_code == 422, r3.text

    _run(run())


def test_unbind_is_idempotent_and_404_on_unknown_project(tmp_path: Path):
    app = _create_app(tmp_path)

    async def run():
        pid = await _make_project(app)
        await _create_pack(app, pid, pack_id="gp_kc")
        await _request(app, "POST", f"/api/projects/{pid}/genre-pack/bind", json={"pack_id": "gp_kc"})

        r = await _request(app, "POST", f"/api/projects/{pid}/genre-pack/unbind")
        assert r.status_code == 200, r.text
        assert r.json()["bound"] is False

        r2 = await _request(app, "POST", f"/api/projects/{pid}/genre-pack/unbind")
        assert r2.status_code == 200, "未绑定时再解绑幂等返回 200"
        assert r2.json()["bound"] is False

        r3 = await _request(app, "POST", "/api/projects/prj_missing/genre-pack/unbind")
        assert r3.status_code == 404, r3.text

        r4 = await _request(app, "GET", "/api/projects/prj_missing/genre-pack")
        assert r4.status_code == 404, r4.text

    _run(run())


# ---------------------------------------------------------------------------
# OpenAPI 纳入（app 注册自动发现）
# ---------------------------------------------------------------------------


def test_openapi_includes_genre_endpoints(tmp_path: Path):
    app = _create_app(tmp_path)
    paths = app.openapi()["paths"]
    for path in (
        "/api/projects/{project_id}/genre-packs",
        "/api/genre-packs",
        "/api/genre-packs/{pack_id}",
        "/api/projects/{project_id}/genre-pack",
        "/api/projects/{project_id}/genre-pack/bind",
        "/api/projects/{project_id}/genre-pack/unbind",
    ):
        assert path in paths, f"openapi 缺 {path}"
