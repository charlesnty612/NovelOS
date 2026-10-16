"""E1 组 · 跨端接线判别：arc 路由的降级范围（2026-10-16 批次十）。

**为什么组件测试不够**（坑区 M8 / 「断言了结果 ≠ 判别了机制」）：
``tests/unit/test_db_error_scope_e1.py`` 测的是 ``build_arc_view`` 本身，
它证明不了「router 把上抛的故障变成了 500 而不是 200」——那是**另一段代码**。
这里专测接线：判别点是 **HTTP 状态码**，只有走完 router 才产生，
组件层用例无论怎么写都碰不到。

正反一对：

* 正 —— ``volumes`` 表缺失（0015 未跑）⇒ **200** + ``volumes: []``（降级仍是契约）；
* 反 —— 首次查询吃锁 ⇒ **500**（不得是 200 + 一份「只是没有卷信息」的正常响应）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from packages.core.api.main import create_app
from packages.core.config import Settings
from packages.core.db import apply_migrations, get_connection


def _app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    apply_migrations(settings.db_path)
    return create_app(settings)


async def _get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        return await c.get(path)


async def _create_project(app, name: str = "E1 接线") -> str:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.post("/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["project_id"]


def _seed_chapter(db_path: str, pid: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chapters (chapter_id, project_id, number, title, plan_json,"
            " status, visibility, created_at, updated_at)"
            " VALUES ('ch_e1_api', ?, 1, 'ch1', '{}', 'PLANNED', 'VISIBLE',"
            " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (pid,),
        )
        conn.commit()
    finally:
        conn.close()


class _FirstStatementFailsConn:
    """首次 ``execute`` 抛真实 ``OperationalError("database is locked")``，之后透明委派。

    （同一形态的说明见 ``tests/unit/test_db_error_scope_e1.py`` 的同名类：
    首发与回退查询读同一张 ``chapters``，真锁会同时打掉两条 ⇒ 真锁版本的断言恒真。）
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.calls = 0

    def execute(self, sql, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str):  # pragma: no cover
        return getattr(self._real, name)


@pytest.mark.anyio
async def test_e1_api_arc_volumes_table_missing_still_200(tmp_path: Path) -> None:
    """正：volumes 表缺失 ⇒ 200 + ``volumes: []``（降级契约未被收窄误伤）。"""
    app = _app(tmp_path)
    pid = await _create_project(app)
    _seed_chapter(str(app.state.settings.db_path), pid)
    conn = get_connection(app.state.settings.db_path)
    try:
        conn.execute("DROP TABLE volumes")
        conn.commit()
    finally:
        conn.close()

    r = await _get(app, f"/api/projects/{pid}/arc")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["volumes"] == []
    assert body["chapters"][0]["volume_id"] is None


@pytest.mark.anyio
async def test_e1_api_arc_lock_becomes_500_not_silent_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反：首次查询吃锁 ⇒ **500**。

    不收窄时 router 会拿到一份「volumes=[] 的正常响应」⇒ **200**，
    作者看到的是一次成功的查询，而卷信息其实没读到。判别点 = HTTP 状态码。
    """
    from packages.core.arc import service as arc_service

    app = _app(tmp_path)
    pid = await _create_project(app)
    _seed_chapter(str(app.state.settings.db_path), pid)

    # ⚠️ ``check_same_thread=False`` 是**必需**的，不是风格选择：anyio + ASGITransport
    # 下 app 在另一个线程里跑，而测试体在本线程建了连接 ⇒ ``build_arc_view`` 的
    # ``finally: conn.close()`` 抛 ``ProgrammingError``（跨线程）并**顶掉**我们要观察的
    # OperationalError ⇒ 用例仍会绿，但绿在「跨线程 close 会炸」这个无关机制上
    # （坑区「夹具自带噪声 ⇒ 失败点漂移」）。本文件首轮就踩到，已记录。
    real = sqlite3.connect(
        str(app.state.settings.db_path), check_same_thread=False
    )
    real.row_factory = sqlite3.Row
    proxy = _FirstStatementFailsConn(real)
    monkeypatch.setattr(arc_service, "get_connection", lambda *a, **k: proxy)
    try:
        r = await _get(app, f"/api/projects/{pid}/arc")
        assert r.status_code == 500, r.text
        assert proxy.calls == 1, "上抛后不得再发回退查询"
    finally:
        real.close()
