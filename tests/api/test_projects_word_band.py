"""/api/projects PATCH word_band 覆盖字段测试（V3.7）。

覆盖：
- PATCH 设置 dict → 200 + GET 回读一致；
- PATCH 显式 null → 200 + GET 回读 word_band = None（DB NULL）；
- PATCH 非法值（low_ratio 越界 / floor 负数 / high_ratio < low_ratio）→ 422 + 携带中文 detail；
- 项目列表读路径暴露 word_band 字段（与未设覆盖项目共存）。

测试模式参考 ``tests/integration/test_projects_api.py``：httpx.ASGITransport + tmp_path db。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _create_app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    return create_app(settings)


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


async def _create_project(app, name: str = "字数带项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


# ---------------------------------------------------------------------------
# PATCH word_band 设置/读取
# ---------------------------------------------------------------------------


def test_patch_word_band_set_and_get_roundtrip(tmp_path: Path):
    """PATCH 设置 dict → 200 + GET 回读 word_band 一致。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _create_project(app, "roundtrip")

            # PATCH 设置
            r = await _request(
                app, "PATCH", f"/api/projects/{pid}",
                json={"word_band": {"low_ratio": 0.9, "high_ratio": 1.1, "floor": 1500}},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["word_band"] == {"low_ratio": 0.9, "high_ratio": 1.1, "floor": 1500}

            # GET 回读一致
            r = await _request(app, "GET", f"/api/projects/{pid}")
            assert r.status_code == 200
            assert r.json()["word_band"] == {"low_ratio": 0.9, "high_ratio": 1.1, "floor": 1500}

    asyncio.run(run())


def test_patch_word_band_partial_keys_fill_defaults(tmp_path: Path):
    """PATCH 只给 floor → 后端按 resolve_band_config 折叠（其余键走默认）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _create_project(app, "partial")

            r = await _request(
                app, "PATCH", f"/api/projects/{pid}",
                json={"word_band": {"floor": 800}},
            )
            assert r.status_code == 200, r.text
            # dict 在响应里保持客户端传入形态（不全展开）——service 只读自己落库的
            # JSON 字符串，json.loads 还原原始 dict。
            assert r.json()["word_band"] == {"floor": 800}

    asyncio.run(run())


def test_patch_word_band_null_clears_overload(tmp_path: Path):
    """PATCH 显式 null → DB NULL + GET 回读 word_band=None（与覆盖清）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _create_project(app, "clear")

            # 1) 先设置覆盖
            r = await _request(
                app, "PATCH", f"/api/projects/{pid}",
                json={"word_band": {"floor": 1500}},
            )
            assert r.status_code == 200
            assert r.json()["word_band"] is not None

            # 2) 显式 null 清除
            r = await _request(
                app, "PATCH", f"/api/projects/{pid}",
                json={"word_band": None},
            )
            assert r.status_code == 200, r.text
            assert r.json()["word_band"] is None

            # 3) GET 回读为 None
            r = await _request(app, "GET", f"/api/projects/{pid}")
            assert r.status_code == 200
            assert r.json()["word_band"] is None

    asyncio.run(run())


def test_patch_word_band_omitted_preserves_existing(tmp_path: Path):
    """PATCH 不带 word_band 键 → 保留原值（与 target_words 等字段同口径）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _create_project(app, "preserve")

            # 设置覆盖
            await _request(
                app, "PATCH", f"/api/projects/{pid}",
                json={"word_band": {"low_ratio": 0.8, "high_ratio": 1.2, "floor": 1000}},
            )

            # 不带 word_band 键，只改 name → 覆盖应保留
            r = await _request(
                app, "PATCH", f"/api/projects/{pid}",
                json={"name": "改名"},
            )
            assert r.status_code == 200
            assert r.json()["word_band"] == {"low_ratio": 0.8, "high_ratio": 1.2, "floor": 1000}
            assert r.json()["name"] == "改名"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 非法值 → 422
# ---------------------------------------------------------------------------


def test_patch_word_band_low_ratio_out_of_range_returns_422(tmp_path: Path):
    """low_ratio=0 或 1.5 → 422（resolve_band_config 拒绝）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _create_project(app, "bad-low")

            for bad in [0, -0.1, 1.5]:
                r = await _request(
                    app, "PATCH", f"/api/projects/{pid}",
                    json={"word_band": {"low_ratio": bad}},
                )
                assert r.status_code == 422, f"low_ratio={bad} should 422, got {r.status_code}"
                detail = r.json().get("detail", "")
                assert "low_ratio" in detail, f"detail should mention low_ratio, got {detail!r}"

    asyncio.run(run())


def test_patch_word_band_floor_negative_returns_422(tmp_path: Path):
    """floor=-1 → 422。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _create_project(app, "bad-floor")

            r = await _request(
                app, "PATCH", f"/api/projects/{pid}",
                json={"word_band": {"floor": -1}},
            )
            assert r.status_code == 422
            detail = r.json().get("detail", "")
            assert "floor" in detail

    asyncio.run(run())


def test_patch_word_band_high_ratio_below_low_returns_422(tmp_path: Path):
    """high_ratio < low_ratio → 422（带单调性违规）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _create_project(app, "bad-monotonic")

            r = await _request(
                app, "PATCH", f"/api/projects/{pid}",
                json={"word_band": {"low_ratio": 0.95, "high_ratio": 0.9}},
            )
            assert r.status_code == 422

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 列表读路径
# ---------------------------------------------------------------------------


def test_list_projects_exposes_word_band_per_project(tmp_path: Path):
    """GET /projects 列表：每个项目的 word_band 字段都暴露（有覆盖 dict / 无覆盖 None）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid_a = await _create_project(app, "A-有覆盖")
            pid_b = await _create_project(app, "B-无覆盖")

            await _request(
                app, "PATCH", f"/api/projects/{pid_a}",
                json={"word_band": {"floor": 1500}},
            )

            r = await _request(app, "GET", "/api/projects")
            assert r.status_code == 200
            rows = {p["project_id"]: p for p in r.json()}
            assert rows[pid_a]["word_band"] == {"floor": 1500}
            assert rows[pid_b]["word_band"] is None

    asyncio.run(run())


def test_create_project_default_word_band_is_none(tmp_path: Path):
    """POST /projects 新建项目 → word_band 默认 None（无覆盖）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "POST", "/api/projects", json={"name": "新建无覆盖"})
            assert r.status_code == 201
            assert r.json()["word_band"] is None

    asyncio.run(run())


def test_create_project_with_word_band_roundtrip(tmp_path: Path):
    """POST /projects 携带 word_band → 201 + 落库，GET 回读一致（创建路径不再静默丢弃）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/projects",
                json={"name": "新建带覆盖", "word_band": {"low_ratio": 0.9, "floor": 1500}},
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["word_band"] == {"low_ratio": 0.9, "floor": 1500}

            r = await _request(app, "GET", f"/api/projects/{body['project_id']}")
            assert r.status_code == 200
            assert r.json()["word_band"] == {"low_ratio": 0.9, "floor": 1500}

    asyncio.run(run())


def test_create_project_invalid_word_band_returns_422(tmp_path: Path):
    """POST /projects 携带非法 word_band（low_ratio 越界）→ 422，与 PATCH 同口径。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(
                app, "POST", "/api/projects",
                json={"name": "新建非法覆盖", "word_band": {"low_ratio": 1.5}},
            )
            assert r.status_code == 422
            assert "word_band" in r.text

    asyncio.run(run())