"""卷与章节隶属关系的契约测试（2026-10-15，评估 G6）。

背景
----
「卷与章节的隶属关系在 UI 上完全不存在」——后端 ``volumes.py`` 六条端点全在
（含 seal / assign），dev 库也有卷数据，但作者看不到任何一章属于哪一卷。

补上 UI 面板时撞上一件必须先查清的事：**章节列表到底带不带 ``volume_id``**。
`chapters` 表有该列（迁移 0015），service 走 ``SELECT *``，而 ``Chapter`` pydantic
模型**没有声明它**——靠 ``model_config = ConfigDict(extra="allow")`` 透出来。

这正是本仓反复吃过的形状：**依赖一个不显式声明的字段**。一旦有人把该模型换成
严格 ``response_model``（FastAPI 会**过滤**未声明字段），``volume_id`` 会**静默消失**，
UI 的「包含章节」列与「未挂卷」计数同时退化成「全部未挂卷」——而这两种退化看起来
都很合理，没有任何报错。

所以这里把「透出来」钉成契约：assign 前后各读一次，两次都必须带该键，且值要真的变。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from packages.core.api.main import create_app
from packages.core.config import Settings


@asynccontextmanager
async def _client(app):
    """进 lifespan 再发请求。

    ⚠️ 迁移只在 lifespan 里跑（`api/main.py::lifespan`），而 `httpx.ASGITransport`
    不触发 lifespan——不显式进就会撞 `no such table: projects`。
    """
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _client(app) as c:
        return await c.request(method, path, **kwargs)


@pytest.mark.anyio
async def test_chapter_list_exposes_volume_id_and_assign_backfills_it(tmp_path: Path):
    app = create_app(Settings(data_dir=tmp_path, log_level="WARNING"))

    pid = (await _request(app, "POST", "/api/projects", json={"name": "vol_proj"})).json()[
        "project_id"
    ]
    cid = (
        await _request(
            app, "POST", f"/api/projects/{pid}/chapters", json={"number": 1, "title": "第一章"}
        )
    ).json()["chapter_id"]

    # assign 之前：键必须在，且值为 null（显式「未挂卷」，不是「键不存在」）
    before = (await _request(app, "GET", f"/api/projects/{pid}/chapters")).json()[0]
    assert "volume_id" in before, (
        "章节列表没有 volume_id 键——'包含章节'列与'未挂卷'计数将恒为'全部未挂卷'，"
        "而那看起来完全合理、零告警"
    )
    assert before["volume_id"] is None

    vol = (
        await _request(
            app, "POST", f"/api/projects/{pid}/volumes", json={"number": 1, "title": "第一卷"}
        )
    ).json()

    assigned = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/volumes/{vol['volume_id']}/assign",
        json={"chapter_id": cid},
    )
    assert assigned.status_code in (200, 201), assigned.text

    after = (await _request(app, "GET", f"/api/projects/{pid}/chapters")).json()[0]
    assert after["volume_id"] == vol["volume_id"], (
        "assign 之后 volume_id 没有回填——UI 会把已挂卷的章显示成未挂卷"
    )

    # 卷列表的 chapter_count 聚合与之一致（两个数字必须同源，不得各算各的）
    volumes = (await _request(app, "GET", f"/api/projects/{pid}/volumes")).json()
    assert volumes[0]["chapter_count"] == 1


@pytest.mark.anyio
async def test_volume_list_response_is_declared_in_openapi(tmp_path: Path):
    """列表响应有 ``response_model``；缺了它卷形状就没有 schema 兜底。"""
    app = create_app(Settings(data_dir=tmp_path, log_level="WARNING"))
    spec = (await _request(app, "GET", "/openapi.json")).json()
    op = spec["paths"]["/api/projects/{project_id}/volumes"]["get"]["responses"]["200"]
    schema = op["content"]["application/json"]["schema"]
    # list[...] 在 OpenAPI 里是 {type: array, items: {$ref}}（FastAPI ≥0.9x 的写法）
    assert schema.get("type") == "array", schema
    assert schema["items"]["$ref"].endswith("/VolumeListItem"), schema["items"]
    assert "VolumeListItem" in spec["components"]["schemas"]
