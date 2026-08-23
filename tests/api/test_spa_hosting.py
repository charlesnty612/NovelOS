"""FastAPI SPA 托管集成测试（Sprint 5，A2 任务）。

覆盖（任务书给死）：
- 用 tmp_path 造一个假 dist（index.html + assets/app.js）
- monkeypatch 环境变量 NOVELOS_WEB_DIST 指向它
- 断言：
  - GET / → 200 含 index 内容
  - GET /some/spa/route → 200 含 index 内容（SPA fallback）
  - GET /assets/app.js → 200 含 js 内容
  - GET /api/health → 200 JSON 不被 SPA 拦截
  - dist 不存在时 GET / → 404（业务路由未挂载 / 时返回 404）
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx

from packages.core.api.main import create_app
from packages.core.config import Settings


def _make_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _make_fake_dist(root: Path) -> Path:
    """创建假 dist 目录（index.html + assets/app.js），返回 dist 路径。"""
    dist = root / "fake_dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><body><div id='root'>SPA INDEX</div></body></html>",
        encoding="utf-8",
    )
    (dist / "assets" / "app.js").write_text(
        "console.log('SPA APP JS');",
        encoding="utf-8",
    )
    return dist


async def _request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async with _make_client(app) as client:
        return await client.request(method, path, **kwargs)


def test_spa_root_returns_index(tmp_path: Path, monkeypatch):
    """dist 存在时 GET / 返回 index.html。"""
    dist = _make_fake_dist(tmp_path)
    monkeypatch.setenv("NOVELOS_WEB_DIST", str(dist))
    # 清掉 settings 单例（环境变量已变，但 main.py 直接读 os.environ，无单例干扰；保留保险）
    from packages.core import config as config_mod

    config_mod.reset_settings()

    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    app = create_app(settings)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/")
            return r

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    assert "SPA INDEX" in r.text
    assert r.headers["content-type"].startswith("text/html")


def test_spa_fallback_returns_index_for_unknown_route(tmp_path: Path, monkeypatch):
    """dist 存在时 GET /some/spa/route 返回 index.html（SPA fallback）。"""
    dist = _make_fake_dist(tmp_path)
    monkeypatch.setenv("NOVELOS_WEB_DIST", str(dist))
    from packages.core import config as config_mod

    config_mod.reset_settings()

    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    app = create_app(settings)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/some/spa/route")
            return r

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    assert "SPA INDEX" in r.text


def test_spa_assets_served_via_static(tmp_path: Path, monkeypatch):
    """dist 存在时 GET /assets/app.js 走 StaticFiles 返回 js 内容。"""
    dist = _make_fake_dist(tmp_path)
    monkeypatch.setenv("NOVELOS_WEB_DIST", str(dist))
    from packages.core import config as config_mod

    config_mod.reset_settings()

    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    app = create_app(settings)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/assets/app.js")
            return r

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    assert "SPA APP JS" in r.text


def test_spa_does_not_intercept_api(tmp_path: Path, monkeypatch):
    """dist 存在时 GET /api/health 仍走业务路由，返回 JSON 不被 SPA 拦截。"""
    dist = _make_fake_dist(tmp_path)
    monkeypatch.setenv("NOVELOS_WEB_DIST", str(dist))
    from packages.core import config as config_mod

    config_mod.reset_settings()

    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    app = create_app(settings)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/health")
            return r

    r = asyncio.run(run())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"


def test_spa_disabled_when_dist_missing(tmp_path: Path, monkeypatch):
    """dist 不存在时（NOVELOS_WEB_DIST 指向不存在路径）行为与 Sprint 0 一致：
    GET / 返回业务路由 / 服务信息端点。"""
    # 指向不存在的路径
    monkeypatch.setenv("NOVELOS_WEB_DIST", str(tmp_path / "no_such_dist"))
    from packages.core import config as config_mod

    config_mod.reset_settings()

    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    app = create_app(settings)

    async def run():
        async with app.router.lifespan_context(app):
            r_root = await _request(app, "GET", "/")
            r_random = await _request(app, "GET", "/random/path/no/spa")
            return r_root, r_random

    r_root, r_random = asyncio.run(run())
    # Sprint 0 行为：/ 返回服务信息 JSON；未注册路径返回 404
    assert r_root.status_code == 200
    assert r_root.json()["service"] == "novelos"
    # 未注册路径返回 404（不被 SPA fallback 兜住）
    assert r_random.status_code == 404


def test_resolve_web_dist_env_override(tmp_path: Path, monkeypatch):
    """``resolve_web_dist`` 在 NOVELOS_WEB_DIST 指向有效 dist 时返回该路径。"""
    from packages.core.api.main import resolve_web_dist

    dist = _make_fake_dist(tmp_path)
    monkeypatch.setenv("NOVELOS_WEB_DIST", str(dist))
    resolved = resolve_web_dist()
    assert resolved is not None
    assert resolved.resolve() == dist.resolve()


def test_resolve_web_dist_missing_index_returns_none(tmp_path: Path, monkeypatch):
    """目录存在但缺 index.html → 返回 None（视为未托管）。"""
    empty_dir = tmp_path / "no_index"
    empty_dir.mkdir()
    monkeypatch.setenv("NOVELOS_WEB_DIST", str(empty_dir))

    from packages.core.api.main import resolve_web_dist

    assert resolve_web_dist() is None


def test_resolve_web_dist_unset_uses_default(tmp_path: Path, monkeypatch):
    """未设置 NOVELOS_WEB_DIST 时，默认查找 <repo>/apps/web/dist。
    若该目录存在 ``index.html`` → 返回路径（启用 SPA）；否则返回 None。
    测试本身不假设 dist 是否存在：仅断言返回值是 ``<repo>/apps/web/dist`` 或 None 之一，
    且当 ``index.html`` 缺失时一定返回 None。"""
    monkeypatch.delenv("NOVELOS_WEB_DIST", raising=False)

    from packages.core.api.main import _DEFAULT_WEB_DIST, resolve_web_dist

    resolved = resolve_web_dist()
    if (_DEFAULT_WEB_DIST / "index.html").is_file():
        assert resolved is not None
        assert resolved.resolve() == _DEFAULT_WEB_DIST.resolve()
    else:
        assert resolved is None