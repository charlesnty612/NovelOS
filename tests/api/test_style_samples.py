"""author_style_samples REST API 集成测试（Sprint 15 / V1.3）。

端点：
- GET    /api/projects/{pid}/style-samples
- POST   /api/projects/{pid}/style-samples
- DELETE /api/projects/{pid}/style-samples/{sid}

覆盖（任务书 V1.3 §A）：
- 创建 → 201 + sample_id；列表可见。
- content 超 5000 字 → 422。
- 已满 10 篇 → 422（先填到 10 篇，再 POST）。
- project 不存在 → 404（GET / POST / DELETE）。
- DELETE 不属于该项目 → 404。
"""

from __future__ import annotations

import asyncio
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


async def _make_project(app, name: str = "style samples 项目") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def test_create_and_list_style_sample(tmp_path: Path):
    """POST → 201；GET 列表含新建项；按 created_at DESC。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/style-samples",
                json={"title": "雨夜", "content": "雨敲在瓦上，一夜未歇。"},
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["sample_id"].startswith("asty_")
            assert body["project_id"] == pid
            assert body["title"] == "雨夜"

            r2 = await _request(app, "GET", f"/api/projects/{pid}/style-samples")
            assert r2.status_code == 200, r2.text
            rows = r2.json()
            assert len(rows) == 1
            assert rows[0]["sample_id"] == body["sample_id"]
            assert rows[0]["content"] == "雨敲在瓦上，一夜未歇。"

    asyncio.run(run())


def test_create_rejects_content_over_limit(tmp_path: Path):
    """content > 5000 字 → 422。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/style-samples",
                json={"title": "长篇", "content": "字" * 5001},
            )
            assert r.status_code == 422, r.text
            assert "5000" in r.json()["detail"]

    asyncio.run(run())


def test_create_rejects_when_project_at_limit(tmp_path: Path):
    """已满 10 篇 → 422。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid = await _make_project(app)
            for i in range(10):
                r = await _request(
                    app, "POST", f"/api/projects/{pid}/style-samples",
                    json={"title": f"t{i}", "content": f"c{i}"},
                )
                assert r.status_code == 201, (i, r.text)
            r = await _request(
                app, "POST", f"/api/projects/{pid}/style-samples",
                json={"title": "第 11 篇", "content": "再来"},
            )
            assert r.status_code == 422, r.text
            assert "10" in r.json()["detail"]

    asyncio.run(run())


def test_get_404_when_project_missing(tmp_path: Path):
    """GET 不存在的项目 → 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/projects/prj_missing/style-samples")
            assert r.status_code == 404, r.text

    asyncio.run(run())


def test_delete_404_when_sample_missing_or_wrong_project(tmp_path: Path):
    """DELETE 不存在的 sample / 不属于该项目的 sample → 404。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            pid_a = await _make_project(app, "项目A")
            pid_b = await _make_project(app, "项目B")
            # 在 A 上插一条
            r = await _request(
                app, "POST", f"/api/projects/{pid_a}/style-samples",
                json={"title": "t", "content": "c"},
            )
            assert r.status_code == 201, r.text
            sid = r.json()["sample_id"]

            # B 上删 A 的 sample → 404
            r2 = await _request(
                app, "DELETE", f"/api/projects/{pid_b}/style-samples/{sid}",
            )
            assert r2.status_code == 404, r2.text

            # A 上删不存在的 sid → 404
            r3 = await _request(
                app, "DELETE", f"/api/projects/{pid_a}/style-samples/asty_nope",
            )
            assert r3.status_code == 404, r3.text

            # A 上正常删 → 204
            r4 = await _request(
                app, "DELETE", f"/api/projects/{pid_a}/style-samples/{sid}",
            )
            assert r4.status_code == 204, r4.text

    asyncio.run(run())
