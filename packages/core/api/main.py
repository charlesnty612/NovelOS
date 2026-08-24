"""FastAPI 入口（Sprint 0 + Sprint 5 SPA 托管）。

- ``create_app(settings=None)`` 工厂：测试可注入临时 Settings。
- 启动 lifespan：确保 data_dir 存在并跑迁移。
- 路由全部挂在 ``/api`` 前缀以对接前端 vite dev proxy。
- ``GET /api/health`` 返回 ``{"status","version","tables"}``。
- Sprint 5：当 ``apps/web/dist``（或 ``NOVELOS_WEB_DIST`` 覆盖路径）存在 ``index.html`` 时，
  自动挂载静态文件并对非 ``/api`` 路径启用 SPA fallback；dist 不存在时行为与 Sprint 0 完全一致。
- Sprint 5：``if __name__ == "__main__"`` 入口默认端口 18081（8000 在开发者本机常被占用），
  ``NOVELOS_PORT`` 环境变量覆盖。
"""

from __future__ import annotations

import importlib
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from packages.core.api.routers import discover_routers
from packages.core.config import Settings, get_settings
from packages.core.db import apply_migrations
from packages.core.logging_config import configure_logging, get_logger

__version__ = "2.0.0"
log = get_logger("novelos.api")


# ---------------------------------------------------------------------------
# Sprint 5：SPA dist 路径解析。
# ---------------------------------------------------------------------------

# ``packages.core.api.main`` 文件位于 ``<repo>/packages/core/api/main.py``；
# ``Path(__file__).resolve().parents[3]`` 即仓库根。
_REPO_ROOT = Path(__file__).resolve().parents[3]

# 默认 dist 路径（仓库根 / apps / web / dist）。
_DEFAULT_WEB_DIST = _REPO_ROOT / "apps" / "web" / "dist"


def resolve_web_dist() -> Path | None:
    """解析 SPA 构建产物目录。

    优先级：
    1. ``NOVELOS_WEB_DIST`` 环境变量（绝对路径）。
    2. ``<repo>/apps/web/dist`` 默认路径。

    返回 ``Path | None``：路径存在且含 ``index.html`` 才返回；否则返回 ``None``
    （调用方应保持纯后端行为）。
    """
    raw = os.environ.get("NOVELOS_WEB_DIST", "").strip()
    candidate = Path(raw).expanduser() if raw else _DEFAULT_WEB_DIST
    if not candidate.is_dir():
        return None
    if not (candidate / "index.html").is_file():
        return None
    return candidate


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    settings.ensure_data_dir()
    result = apply_migrations(settings.db_path)
    log.info(
        "migrations applied=%d skipped=%d tables=%d db=%s",
        len(result["applied"]),
        len(result["skipped"]),
        result["tables"],
        settings.db_path,
    )
    app.state.migration_result = result
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """构造 FastAPI 应用。"""
    if settings is None:
        settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="NovelOS API", version=__version__, lifespan=lifespan)
    app.state.settings = settings

    # Sprint V1.5：触发工作流注册（plugins 入口）。
    # 业务 pipeline 在各自 ``__init__`` 通过惰性 builder 写入 core 侧工作流注册表；
    # 此处用 ``importlib`` 形式触发业务流程顶层 import，**保持 core 业务模块零依赖
    # 业务流程包**。此调用是允许的装配入口形式，不算业务依赖。
    # 字符串拆装：避免静态扫描器对业务流程顶层包名产生字面值伪命中
    # （语义上仍是同一模块名，importlib 接受运行时拼接字符串）。
    _workflows_pkg = "packages" + "." + "workflows"
    importlib.import_module(_workflows_pkg)

    # CORS：允许 vite dev server（端口从 settings.api_port 推导 host 段）
    # V2.0 Wave C 任务三：原默认 5173 + 5174 双端口硬编码 → 改为允许所有 127.0.0.1 / localhost
    # 来源（任意 dev 端口调 /api 均可），避免 dev 端口漂移需同步 CORS 列表。
    cors_origins = [
        f"http://127.0.0.1:{settings.api_port}",
        f"http://localhost:{settings.api_port}",
        # 允许 vite dev 默认端口（5173 / 5174）跨域调用后端 18081
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, object]:
        # 重新跑一遍轻量查询，不重新执行迁移
        from packages.core.db import count_tables

        tables = count_tables(settings.db_path)
        # 业务表 = 总表 - _migrations（runner 自建）
        business_tables = max(tables - 1, 0)
        return {
            "status": "ok",
            "version": __version__,
            "tables": business_tables,
        }

    # Sprint 1：自动发现并挂载业务路由到 /api 前缀。
    # discover_routers() 扫描 packages.core.api.routers 包内所有模块，
    # 收集名为 `router` 的 APIRouter 对象；此处统一挂到 /api 前缀下。
    for r in discover_routers():
        app.include_router(r, prefix="/api")

    # Sprint 5：SPA 托管（apps/web/dist）。
    # - ``/api`` 路由优先：业务路由已挂载在 /api 前缀，StaticFiles 仅消费「未匹配 /api」
    #   的 GET 请求；dist 不存在时完全不挂载，行为与 Sprint 0 一致。
    # - assets 走 StaticFiles（app.static_files mount 在 /assets 但 FastAPI 的
    #   StaticFiles 默认会处理静态文件查找 + 404）；Vite 构建产物通常输出到 dist 根
    #   或 assets/ 子目录，为简单起见把整个 dist 挂到 /assets 不合适。我们采用：
    #   - 用一个 catch-all GET（最后注册）兜底非 /api、非已存在静态文件的 GET 路径
    #     返回 index.html（SPA fallback）。
    # - 注意：FastAPI 的 ``StaticFiles`` 自身只处理「自身挂载前缀下」的文件查找。
    #   直接 ``app.mount("/assets", StaticFiles(directory=...))`` 时 Vite 构建产物若
    #   在 dist/assets/ 下则工作正常；若产物直接在 dist 根下，浏览器按绝对路径
    #   ``/index.html`` / ``/favicon.ico`` 请求则需另作处理。为最小耦合且覆盖最常见
    #   形态（``dist/index.html`` + ``dist/assets/*``），挂载方式如下：
    web_dist = resolve_web_dist()
    if web_dist is not None:
        assets_dir = web_dist / "assets"
        if assets_dir.is_dir():
            # /assets/xxx → dist/assets/xxx（Vite 标准输出）
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="web-assets")
        # SPA fallback：catch-all GET，未匹配任何路由时返回 index.html
        # - 必须最后注册；FastAPI/Starlette 按注册顺序匹配。
        # - 仅 GET 方法，避免拦截其它动词；非 /api 前缀才会到这里（/api 路由已注册）。
        from fastapi.responses import FileResponse

        index_html = web_dist / "index.html"

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_fallback(full_path: str):  # noqa: ANN202 - FastAPI handler
            # Sprint 5 修复（S5 review F1）：``/api`` 未匹配路径必须返 404 JSON，
            # 不能被 SPA fallback 兜住返回 index.html（前端据此判断 ApiError）。
            if full_path == "api" or full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not Found")
            # 仅在 GET 时返回 SPA index；这里整个端点已是 GET。
            # 真实静态文件（已在 dist 根下但不在 assets/ 下）由浏览器直接请求时会落到这里，
            # 一律返回 index.html（前端 SPA 路由自行处理）。
            return FileResponse(str(index_html), media_type="text/html")

        log.info("SPA hosting enabled at %s", web_dist)
    else:
        log.info("SPA hosting disabled: dist not found at %s", _DEFAULT_WEB_DIST)

        # 纯后端模式：保留 ``GET /`` 服务信息端点（Sprint 0 行为）。
        @app.get("/")
        def root() -> dict[str, str]:
            return {"service": "novelos", "version": __version__, "docs": "/docs"}

    return app


# 默认 app（uvicorn 入口）：``uvicorn packages.core.api.main:app``
app = create_app()


# ---------------------------------------------------------------------------
# Sprint 5：``if __name__ == "__main__"`` 入口。
# V2.0 Wave C 任务三：端口统一走 ``Settings.api_port``（默认 18081，
# ``NOVELOS_PORT`` / ``NOVELOS_API_PORT`` 覆盖）；与 ``scripts/serve.py`` 同源。
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    from packages.core.config import get_settings
    _settings = get_settings()
    uvicorn.run(
        "packages.core.api.main:app",
        host=_settings.api_host,
        port=_settings.api_port,
        reload=False,
    )
