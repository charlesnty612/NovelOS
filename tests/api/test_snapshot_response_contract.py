"""`GET /state` 的 response_model 契约测试（2026-10-15）。

背景
----
`GET /projects/{pid}/state` 与 `GET /projects/{pid}/state/versions/{n}` 此前返回裸
``dict``、**没有** ``response_model``，于是 OpenAPI 里没有它们的 schema。前端只好在
``apps/web/src/api/types.ts`` 手写一个欠定义类型（全 optional + ``[key: string]: unknown``
+ 并列着后端从不返回的 ``version``），并配一层手写归一化；那层归一化把 ``characters``
（list）拍成 ``{}``、把 ``events``（dict）拍成 ``[]``，导致项目概览页「角色」与
「事件·累计」**恒为 0** 且无告警。

本组用例钉住契约本身，判别点是**形状与 OpenAPI 声明**：

1. OpenAPI 里存在 ``SnapshotResponse`` schema，且被上述两个端点引用；
2. 端点实际返回的形状与 schema 一致（characters 是 **list**、events 是 **dict**）；
3. ``extra="allow"``——故事状态是开放 JSON 文档，多余集合键不得被静默裁掉。

注意第 3 条：``response_model`` 默认会**过滤**未声明字段。若哪天把 ``extra`` 去掉，
``/state`` 会静默丢掉新增的集合键——比「类型欠定义」更难发现。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from packages.core.api.main import create_app
from packages.core.config import Settings


def _create_app(tmp_path: Path):
    return create_app(Settings(data_dir=tmp_path, log_level="WARNING"))


@asynccontextmanager
async def _client(app):
    """进 lifespan 再发请求。

    ⚠️ **迁移只在 lifespan 里跑**（`api/main.py::lifespan` → `apply_migrations`），
    而 `httpx.ASGITransport` **不触发 lifespan**——不显式进就会撞
    `no such table: projects`。本仓既有 api 测试统一用
    `app.router.lifespan_context(app)`（见 `tests/api/test_arc_api.py`）。
    """
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _client(app) as c:
        return await c.request(method, path, **kwargs)


async def _make_project(app, name: str = "snap_proj") -> str:
    r = await _request(app, "POST", "/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


async def _make_chapter(app, pid: str, number: int = 1) -> str:
    r = await _request(
        app,
        "POST",
        f"/api/projects/{pid}/chapters",
        json={"number": number, "title": f"第{number}章"},
    )
    assert r.status_code == 201, r.text
    return r.json()["chapter_id"]


# --------------------------------------------------------------------------- OpenAPI


@pytest.mark.anyio
async def test_openapi_declares_snapshot_response_for_both_state_endpoints(tmp_path: Path):
    app = _create_app(tmp_path)
    r = await _request(app, "GET", "/openapi.json")
    assert r.status_code == 200, r.text
    spec = r.json()

    assert "SnapshotResponse" in spec["components"]["schemas"], (
        "SnapshotResponse schema 缺失——前端会退回手写欠定义类型，"
        "而那正是本次两个统计项恒 0 的根因"
    )

    for path in (
        "/api/projects/{project_id}/state",
        "/api/projects/{project_id}/state/versions/{version}",
    ):
        op = spec["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]
        assert op["schema"]["$ref"].endswith("/SnapshotResponse"), (
            f"{path} 的 200 响应未引用 SnapshotResponse"
        )


#: ``SnapshotResponse`` 应有的**全部**键。
#:
#: ⚠️ 基准取自 **第三个独立位置**——``story_state/snapshot.py::build_initial_state``
#: 第 301~307 行实际构造的 7 个键，**不是**从 ``SnapshotResponse`` 模型抄的
#: （抄模型 = 自指断言，见 AGENTS 坑区「自指断言恒真」），也不是「跑一下看返回什么」
#: （那正是「夹具从被测代码的实际输出里抄形状」那条）。构造器与模型分处两地，
#: 两者对不上正是本断言要抓的东西。
_EXPECTED_SNAPSHOT_PROPERTIES = frozenset(
    {
        "state_version",
        "characters",
        "world",
        "hooks",
        "debts",
        "recent_events",
        "events",
    }
)

#: 只有 ``state_version`` 没有默认值（其余五段都有 ``default_factory``）⇒ 仅它 required。
#: 这条同样从**模型定义本身可推导**（无 default ⇒ required），不是跑出来的。
_EXPECTED_SNAPSHOT_REQUIRED = frozenset({"state_version"})


@pytest.mark.anyio
async def test_openapi_snapshot_schema_declares_every_segment(tmp_path: Path):
    """**schema 的 properties 必须列全 7 段**——这条的存在理由是 ``extra="allow"``。

    ⚠️ 没有这条，「从 ``SnapshotResponse`` 删掉一个字段声明」是**测不到**的：
    ``model_config = ConfigDict(extra="allow")`` 让未声明的键**照样出现在响应里**
    ⇒ 既有的形状断言（characters 是 list / events 是 dict…）**照样全绿**，
    而 OpenAPI 已经不再声明它 ⇒ 前端 codegen 出的 TS 类型少一个字段
    ⇒ **静默退回批次（三）修掉的那个 bug**（前端欠定义类型把 list 拍成 {}，
    两个统计项恒 0）。这与批次（五）的 M20 是同一条纪律：**``extra="allow"``
    让「删字段声明」没有可见症状，必须断言 schema 的 properties/required**。
    """
    app = _create_app(tmp_path)
    r = await _request(app, "GET", "/openapi.json")
    assert r.status_code == 200, r.text
    schema = r.json()["components"]["schemas"]["SnapshotResponse"]

    props = frozenset(schema.get("properties", {}))
    missing = _EXPECTED_SNAPSHOT_PROPERTIES - props
    assert not missing, (
        f"SnapshotResponse 的 schema 未声明 {sorted(missing)}——"
        f"extra=allow 会让它们照样进响应，所以响应形状断言抓不到；"
        f"但前端 codegen 出的 TS 类型会缺这些字段（实际 properties={sorted(props)}）"
    )

    required = frozenset(schema.get("required", []))
    assert required == _EXPECTED_SNAPSHOT_REQUIRED, (
        f"required 应恰为 {sorted(_EXPECTED_SNAPSHOT_REQUIRED)}"
        f"（其余五段都有 default_factory）；实际 {sorted(required)}"
    )


# --------------------------------------------------------------------------- 实际形状


@pytest.mark.anyio
async def test_state_endpoint_returns_contract_shapes(tmp_path: Path):
    """无快照时也要给出契约形状：`state_version=0`，其余为空容器而非缺字段。"""
    app = _create_app(tmp_path)
    pid = await _make_project(app)
    await _make_chapter(app, pid)

    r = await _request(app, "GET", f"/api/projects/{pid}/state")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["state_version"] == 0
    # 形状是本用例的全部要点（前端曾把这两项拍反成 {} / []）
    assert isinstance(body["characters"], list), "characters 必须是 list（前端曾拍成 {}）"
    assert isinstance(body["hooks"], list)
    assert isinstance(body["debts"], list)
    assert isinstance(body["recent_events"], list)
    assert isinstance(body["events"], dict), "events 必须是 dict（前端曾拍成 []）"
    assert isinstance(body["world"], dict)


@pytest.mark.anyio
async def test_state_endpoint_keeps_unknown_collection_keys(tmp_path: Path):
    """`extra="allow"`：多写的集合键不得被 response_model 裁掉。

    这是回归看守：``response_model`` 默认**过滤**未声明字段。故事状态是开放 JSON
    文档（delta 可带新集合），一旦某天有人去掉 ``extra="allow"``，新增的集合会
    静默消失——比「类型欠定义」更难被发现，因为它不报错。
    """
    import sqlite3

    app = _create_app(tmp_path)
    pid = await _make_project(app)
    cid = await _make_chapter(app, pid)

    # 走正规 init 路径落 genesis（story_states.commit_id 是 NOT NULL，
    # 直接 INSERT 会撞约束——这里不用「造一行假快照」的捷径）。
    init = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": cid})
    assert init.status_code in (201, 409), init.text

    # 再把 snapshot_json 改写成带自定义集合键的形状（模拟「将来新增集合」）。
    conn = sqlite3.connect(tmp_path / "novelos.db")
    try:
        conn.execute(
            "UPDATE story_states SET snapshot_json = ? WHERE project_id = ?",
            (
                '{"state_version": 1, "characters": [], "world": {}, "hooks": [], '
                '"debts": [], "recent_events": [], "events": {}, '
                '"future_collection": [{"x": 1}]}',
                pid,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    r = await _request(app, "GET", f"/api/projects/{pid}/state")
    assert r.status_code == 200, r.text
    assert r.json().get("future_collection") == [{"x": 1}], (
        "未声明的集合键被 response_model 裁掉了——extra='allow' 失效？"
    )


@pytest.mark.anyio
async def test_state_versions_endpoint_shares_the_contract(tmp_path: Path):
    app = _create_app(tmp_path)
    pid = await _make_project(app)
    cid = await _make_chapter(app, pid)

    r = await _request(app, "POST", f"/api/projects/{pid}/state/init", json={"chapter_id": cid})
    assert r.status_code in (201, 409), r.text

    r = await _request(app, "GET", f"/api/projects/{pid}/state/versions/1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state_version"] == 1
    assert isinstance(body["characters"], list)
    assert isinstance(body["events"], dict)


@pytest.mark.anyio
async def test_unknown_version_still_404(tmp_path: Path):
    """加 response_model 不得把 404 变成 500（FastAPI 会在校验前抛 HTTPException）。"""
    app = _create_app(tmp_path)
    pid = await _make_project(app)
    r = await _request(app, "GET", f"/api/projects/{pid}/state/versions/999")
    assert r.status_code == 404, r.text
