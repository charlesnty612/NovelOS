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
import shutil
import tempfile
from pathlib import Path

import httpx

from packages.core.api.main import __version__, create_app
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
    assert body["version"] == __version__


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


# ============================================================================
# Sprint 5 review F1：SPA catch-all 必须把 ``/api`` 未知路径放回 404 JSON，
# 而非用 index.html 兜住（前端 ApiError 期待 JSON detail）。
# ============================================================================


def test_spa_fallback_api_unknown_returns_404_json(tmp_path: Path, monkeypatch):
    """dist 存在时 GET /api/does-not-exist 不应返回 SPA index.html，
    必须 404 且响应是 JSON（content-type 非 HTML）。"""
    dist = _make_fake_dist(tmp_path)
    monkeypatch.setenv("NOVELOS_WEB_DIST", str(dist))
    from packages.core import config as config_mod

    config_mod.reset_settings()

    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    app = create_app(settings)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api/does-not-exist")
            return r

    r = asyncio.run(run())
    assert r.status_code == 404, r.text
    # 必须 JSON 形态（FastAPI HTTPException 默认 application/json）
    assert "text/html" not in r.headers["content-type"], r.headers.get("content-type")
    body = r.json()
    assert body["detail"] == "Not Found"


def test_spa_fallback_api_root_returns_404_json(tmp_path: Path, monkeypatch):
    """dist 存在时 GET /api（精确路径）也必须 404 JSON，不被 SPA fallback 兜住。"""
    dist = _make_fake_dist(tmp_path)
    monkeypatch.setenv("NOVELOS_WEB_DIST", str(dist))
    from packages.core import config as config_mod

    config_mod.reset_settings()

    settings = Settings(data_dir=tmp_path, log_level="WARNING")
    app = create_app(settings)

    async def run():
        async with app.router.lifespan_context(app):
            r = await _request(app, "GET", "/api")
            return r

    r = asyncio.run(run())
    assert r.status_code == 404, r.text
    assert "text/html" not in r.headers["content-type"], r.headers.get("content-type")
    body = r.json()
    assert body["detail"] == "Not Found"


# ============================================================================
# 2026-10-19：契约产物不得依赖构建产物（CI 红测根因的直接看守）
#
# 事故：``GET /`` 的行为随 ``apps/web/dist`` 在不在而变 ⇒ 活 OpenAPI 的路径集
# 也跟着变（本地 98 paths 无 ``/`` vs CI 99 paths 多 ``/``）⇒ 入库的
# ``apps/web/openapi.json`` 与 CI 侧活 schema 对不上 ⇒
# ``test_openapi_contract.py`` 自 2026-09-09 起**每次 push 恒红**，
# 而本地全量闸门全绿（本地有 dist）⇒ 红了 30+ 次无人看红。
#
# 本用例断言的是**不变式**（比「比对某个文件」更强、更一般）：
# **两种环境下的活 OpenAPI 路径集必须逐字相同。**
# 撤掉 ``main.py`` root 端点的 ``include_in_schema=False`` ⇒ 无 dist 侧多出
# ``/`` ⇒ 两集合不等 ⇒ 本用例必红。
# ============================================================================


def _openapi_paths(dist_env: str | None) -> set[str]:
    """按给定的 ``NOVELOS_WEB_DIST`` 构造 app，返回其活 OpenAPI 的路径集。

    ``dist_env`` 为 None ⇒ 指向一个**不存在**的路径（= 干净 clone / CI 的形态）。
    """
    import os

    from packages.core import config as config_mod

    os.environ["NOVELOS_WEB_DIST"] = dist_env or ""
    config_mod.reset_settings()
    # ⚠️ **用完即删**（2026-10-23 补）：旧实现建了这个临时 data 目录却**从不删**
    # ⇒ 每跑一次本文件就在系统 Temp 留一个 `novelos_openapi_env_*` ⇒ 逐期累积
    # ⇒ 与「几百个泄漏临时目录」是同一个形状（已修的 `export_openapi.py` 同款）。
    # 删不掉时**保留证据**（Windows 上 SQLite 文件可能仍被占用）⇒ 不静默吞。
    tmp_dir = Path(tempfile.mkdtemp(prefix="novelos_openapi_env_"))
    try:
        settings = Settings(data_dir=tmp_dir, log_level="ERROR")
        app = create_app(settings)
        return set(app.openapi()["paths"])
    finally:
        config_mod.reset_settings()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_openapi_paths_identical_with_and_without_dist(tmp_path: Path, monkeypatch):
    """**活 OpenAPI 的路径集不得随 ``apps/web/dist`` 在不在而变。**

    通用纪律：契约产物的正确性不得依赖构建产物是否存在——凡「同一份代码在两种
    环境下产出不同路由表」的分支，其环境差异必须被 ``include_in_schema`` 排除在
    契约之外。``apps/web/dist/`` **不在版本库**（``.gitignore``）⇒ 干净 clone 与
    CI 必然缺它 ⇒ 若不钉这条不变式，CI 必红而本地必绿，两边都不报错。
    """
    dist = _make_fake_dist(tmp_path)

    with_dist = _openapi_paths(str(dist))
    without_dist = _openapi_paths(str(tmp_path / "no_such_dist"))

    assert with_dist == without_dist, (
        "活 OpenAPI 的路径集随 apps/web/dist 存在与否而变——"
        f"仅 dist 存在时有: {sorted(with_dist - without_dist)}；"
        f"仅 dist 缺失时有: {sorted(without_dist - with_dist)}；"
        "契约产物不得依赖构建产物（检查 main.py root 端点的 include_in_schema）"
    )
    # 附带钉住两条不该出现的路径（dist 存在时走 SPA catch-all，本身就在 schema 外）
    assert "/" not in with_dist, "SPA 托管时 catch-all 不该进 OpenAPI"
    assert "/" not in without_dist, (
        "纯后端模式下 GET / 也不该进 OpenAPI（root 端点缺 include_in_schema=False）"
    )


def test_openapi_env_probe_leaves_no_temp_dir_behind(monkeypatch):
    """``_openapi_paths`` 建的临时 data 目录必须**用完即删**（不留垃圾）。

    断言打在**真实文件系统**上：把 ``tempfile.mkdtemp`` 换成间谍，记录实际建出来的
    目录 ⇒ 调用结束后它必须**不存在**。

    ⚠️ 为什么不扫全局 Temp 目录数「前后差」：``pytest -n 20`` 下别的 worker 会
    同时建自己的临时目录 ⇒ 差值法会**偶发假红** ⇒ 改成「只盯自己建的那一个」。
    """
    created: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def _spy(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(Path(path))
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", _spy)
    _openapi_paths(None)

    assert created, "间谍没抓到任何目录 ⇒ 断言会恒真（探针没被调用）"
    leaked = [p for p in created if p.exists()]
    assert not leaked, f"临时目录泄漏：{[str(p) for p in leaked]}"

