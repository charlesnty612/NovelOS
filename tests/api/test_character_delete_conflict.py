"""DELETE /characters/{id}：被 relationships 引用时必须 409，**不是 500**（D-12 路由侧）。

背景（Lead 裁决 Task 7 的协调项）：
迁移 0022 摘掉了 ``relationships.from_character_id`` / ``to_character_id`` 的外键
（关系端点可以是 faction，FK 语义不再成立）⇒ 删角色会留孤儿关系行，
而 ``PRAGMA foreign_key_check`` **报不出来**。
⇒ 域层已修（``packages/domain/character/service.py::delete`` 查两端点引用，
被引用 ⇒ 抛 ``ReferencedError`` 且**不删任何东西**）。

⚠️ 但**路由层没接**：``packages/core/api/routers/characters.py::delete_character``
原先直接调 ``svc.delete(...)`` ⇒ ``ReferencedError`` 直接冒泡成 **500**
⇒ 把「可解释的引用冲突」报成「服务器炸了」，客户端无法区分「重试有用」与「永久失败」。
⇒ 本文件钉住 **409**（与 world 域 ``worlds.py::_handle_world_error`` 同款口径）。

零真实 LLM 调用、零内容仓接触：只建临时库 + 真实 app + HTTP 请求。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from packages.core.db import get_connection
from packages.core.ids import new_id

from ._shared import _create_app, _request


def _seed_relationship(db_path, project_id: str, from_id: str, to_id: str) -> None:
    """插一条关系行（端点 FK 已被 0022 摘掉 ⇒ 孤儿行是真实可写的）。"""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO relationships (relationship_id, project_id, from_character_id, "
            "to_character_id, relation_type, state_json, last_state_version) "
            "VALUES (?, ?, ?, ?, 'ally', '{}', 1)",
            (new_id("rel"), project_id, from_id, to_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_delete_referenced_character_returns_409_and_keeps_character(tmp_path: Path):
    """被引用 ⇒ 409（不是 500），且角色**仍在**（抛错时什么都没删）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "POST", "/api/projects", json={"name": "删角色 409"})
            assert r.status_code == 201, r.text
            pid = r.json()["project_id"]

            r = await _request(
                app, "POST", f"/api/projects/{pid}/characters",
                json={"name": "林夕", "role": "protagonist"},
            )
            assert r.status_code == 201, r.text
            cid = r.json()["character_id"]

            r = await _request(
                app, "POST", f"/api/projects/{pid}/characters",
                json={"name": "沈砚", "role": "supporting"},
            )
            assert r.status_code == 201, r.text
            other = r.json()["character_id"]

            db_path = app.state.settings.db_path
            _seed_relationship(db_path, pid, cid, other)

            r = await _request(app, "DELETE", f"/api/characters/{cid}")
            assert r.status_code == 409, (
                f"被 relationships 引用的角色删除应返 409，实际 {r.status_code}：{r.text}"
            )
            assert "引用" in r.text, f"409 的 detail 应说明是引用冲突，实际：{r.text}"

            # 抛错时**什么也没删**：角色还在
            r = await _request(app, "GET", f"/api/characters/{cid}")
            assert r.status_code == 200, (
                f"409 之后角色应仍然存在，实际 {r.status_code}：{r.text}"
            )

    asyncio.run(run())


def test_delete_character_without_relationship_still_succeeds(tmp_path: Path):
    """反向对照（防过度收紧）：无引用 ⇒ 204 且角色真的被删。

    没有这条，「所有 DELETE 都返 409」也会让上面的用例变绿。
    """
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "POST", "/api/projects", json={"name": "删角色 204"})
            assert r.status_code == 201, r.text
            pid = r.json()["project_id"]

            r = await _request(
                app, "POST", f"/api/projects/{pid}/characters",
                json={"name": "林夕", "role": "protagonist"},
            )
            assert r.status_code == 201, r.text
            cid = r.json()["character_id"]

            r = await _request(app, "DELETE", f"/api/characters/{cid}")
            assert r.status_code == 204, (
                f"无引用的角色删除应返 204，实际 {r.status_code}：{r.text}"
            )

            r = await _request(app, "GET", f"/api/characters/{cid}")
            assert r.status_code == 404, (
                f"204 之后角色应已被删，实际 {r.status_code}：{r.text}"
            )

    asyncio.run(run())


def test_delete_missing_character_returns_404(tmp_path: Path):
    """不存在的角色 ⇒ 404（不是 409 也不是 500）。"""
    app = _create_app(tmp_path)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "DELETE", "/api/characters/ch_does_not_exist")
            assert r.status_code == 404, (
                f"删除不存在的角色应返 404，实际 {r.status_code}：{r.text}"
            )

    asyncio.run(run())
