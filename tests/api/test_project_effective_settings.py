"""快通道「已解析有效值」只读出口（2026-10-23 批次三十二 · 对抗审查 B 的 T3）。

## 为什么要这个端点

``projects.fast_mode`` 列是**三态**（NULL 未设置 / 1 开 / 0 显式关），而 NULL 会被
后端解析成「回落 ``NOVELOS_FAST_MODE``，再回落默认关」
（:func:`packages.core.api.routers.workflows.fast_mode.resolve_fast_mode`）。
前端若只拿到**列值**，就只能把 NULL 显示成「关」——**在 env 开启时那是在撒谎**：
后端实际在快通道（跳过审校闸门），作者却以为闸门还在。

⇒ 本端点返回**解析后的有效值 + 来源**，让前端按真实生效状态显示与判定。

## 本文件钉住的机制

1. **项目列优先于 env**：列有值 ⇒ ``source="project"``（即使 env 相反）；
2. **列为 NULL ⇒ 回落 env** ⇒ ``source="env"``；
3. **列 NULL 且 env 未设置** ⇒ ``source="off"``、有效值为关；
4. **项目不存在 ⇒ 404**（不是「安静返回关」——那又是一次谎报）；
5. **解析口径与运行时唯一权威同源**：本端点不得另写一份 env 词表
   （反向：改 env 词表时本端点必须跟着变 ⇒ 断言打在本端点的**真实响应**上）。

零真实 LLM 调用、零内容仓接触：全程临时库 + 合成夹具。
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
    """⚠️ 迁移只在 lifespan 里跑（``httpx.ASGITransport`` 不触发 lifespan）。"""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c


async def _new_project(app) -> str:
    async with _client(app) as c:
        r = await c.post("/api/projects", json={"name": "有效值出口"})
        assert r.status_code == 201, r.text
        return r.json()["project_id"]


async def _get_settings(app, pid: str) -> httpx.Response:
    async with _client(app) as c:
        return await c.get(f"/api/projects/{pid}/effective-settings")


async def _set_project_fast_mode(app, pid: str, value: bool) -> None:
    async with _client(app) as c:
        r = await c.patch(f"/api/projects/{pid}", json={"fast_mode": value})
        assert r.status_code == 200, r.text


@pytest.mark.anyio
async def test_null_column_and_no_env_is_off(tmp_path: Path, monkeypatch):
    """列为 NULL + env 未设置 ⇒ 有效值关、来源 ``off``。"""
    monkeypatch.delenv("NOVELOS_FAST_MODE", raising=False)
    app = _create_app(tmp_path)
    pid = await _new_project(app)

    r = await _get_settings(app, pid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fast_mode_effective"] is False, body
    assert body["fast_mode_source"] == "off", body


@pytest.mark.anyio
async def test_null_column_falls_back_to_env(tmp_path: Path, monkeypatch):
    """列为 NULL + env=1 ⇒ 有效值**开**、来源 ``env``（前端据此不再显示「关」）。"""
    monkeypatch.setenv("NOVELOS_FAST_MODE", "1")
    app = _create_app(tmp_path)
    pid = await _new_project(app)

    r = await _get_settings(app, pid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fast_mode_effective"] is True, body
    assert body["fast_mode_source"] == "env", body


@pytest.mark.anyio
async def test_null_column_with_env_off_is_explicitly_off(tmp_path: Path, monkeypatch):
    """列为 NULL + env=0 ⇒ 有效值关、来源 ``env``（**来源是 env 不是 off**）。"""
    monkeypatch.setenv("NOVELOS_FAST_MODE", "0")
    app = _create_app(tmp_path)
    pid = await _new_project(app)

    r = await _get_settings(app, pid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fast_mode_effective"] is False, body
    assert body["fast_mode_source"] == "env", body


@pytest.mark.anyio
async def test_project_column_wins_over_env(tmp_path: Path, monkeypatch):
    """列有值 ⇒ 来源恒为 ``project``，**无视 env**（运行时优先级：列 > env）。"""
    monkeypatch.setenv("NOVELOS_FAST_MODE", "1")
    app = _create_app(tmp_path)
    pid = await _new_project(app)
    await _set_project_fast_mode(app, pid, False)

    r = await _get_settings(app, pid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fast_mode_effective"] is False, body
    assert body["fast_mode_source"] == "project", body


@pytest.mark.anyio
async def test_project_column_on_wins_over_env_off(tmp_path: Path, monkeypatch):
    """列=开 + env=0 ⇒ 仍为开（列优先的另一侧）。"""
    monkeypatch.setenv("NOVELOS_FAST_MODE", "0")
    app = _create_app(tmp_path)
    pid = await _new_project(app)
    await _set_project_fast_mode(app, pid, True)

    r = await _get_settings(app, pid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fast_mode_effective"] is True, body
    assert body["fast_mode_source"] == "project", body


@pytest.mark.anyio
async def test_unknown_project_is_404(tmp_path: Path, monkeypatch):
    """项目不存在 ⇒ **404**（不是「安静返回关」——那又是一次谎报）。"""
    monkeypatch.delenv("NOVELOS_FAST_MODE", raising=False)
    app = _create_app(tmp_path)

    r = await _get_settings(app, "prj_does_not_exist")
    assert r.status_code == 404, r.text
