"""FastAPI 入口（Sprint 0）。

- ``create_app(settings=None)`` 工厂：测试可注入临时 Settings。
- 启动 lifespan：确保 data_dir 存在并跑迁移。
- 路由全部挂在 ``/api`` 前缀以对接前端 vite dev proxy。
- ``GET /api/health`` 返回 ``{"status","version","tables"}``。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from packages.core.config import Settings, get_settings
from packages.core.db import apply_migrations
from packages.core.logging_config import configure_logging, get_logger

__version__ = "0.1.0"
log = get_logger("novelos.api")


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

    # CORS：允许 vite dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
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

    @app.get("/")
    def root() -> dict[str, str]:
        return {"service": "novelos", "version": __version__, "docs": "/docs"}

    return app


# 默认 app（uvicorn 入口）：``uvicorn packages.core.api.main:app``
app = create_app()
