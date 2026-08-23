# core.api（FastAPI 应用）

> 职责：NovelOS 后端的 HTTP 入口——FastAPI 应用工厂、lifespan 启动迁移、健康检查与服务信息端点；为前端 vite dev proxy 提供 `/api` 前缀挂载点。
> 状态：已实现 Sprint 0。

## 职责与边界

**做**：
- 构造 FastAPI 应用并配置 CORS（允许 `http://127.0.0.1:5173` 与 `http://localhost:5173`）
- lifespan 启动：确保 `data_dir` 存在、执行迁移、记录迁移结果到 `app.state.migration_result`
- 提供 `GET /api/health`（状态 / 版本 / 业务表数）与 `GET /`（服务信息）端点
- 暴露 `create_app(settings)` 工厂函数供测试注入临时 `Settings`

**不做**：
- 不挂载业务领域路由（属于后续 Sprint，`packages/domain/*`）
- 不实现 WebSocket 长连接（属于 Sprint 5 Workbench UI）

## 对外接口

| 名称 | 来源 | 说明 |
|---|---|---|
| `create_app(settings: Settings | None) -> FastAPI` | `packages/core/api/main.py:40` | 工厂函数，测试可注入 settings |
| `app` | `packages/core/api/main.py:83` | 默认 FastAPI 实例（uvicorn 入口） |
| `__version__ = "0.1.0"` | `packages/core/api/main.py:20` | API 版本号 |
| `lifespan(app)` | `packages/core/api/main.py:24` | 异步上下文管理器，启动时执行迁移 |
| `GET /api/health` | `packages/core/api/main.py:61` | 返回 `{status:"ok", version, tables:28}` |
| `GET /` | `packages/core/api/main.py:75` | 返回 `{service, version, docs}` |
| `create_app`（包级 re-export） | `packages/core/api/__init__.py:5` | 从 `.main` 导入 |

## 依赖

- 上游：`packages.core.config`（`Settings`, `get_settings`）、`packages.core.db`（`apply_migrations`, `count_tables`）、`packages.core.logging_config`（`configure_logging`, `get_logger`）
- 外部库：fastapi、uvicorn（运行）

## 使用 / 入口

```bash
# 开发：vite proxy /api → 8000
python scripts/serve.py

# 直接 uvicorn
uvicorn packages.core.api.main:app --reload

# 浏览器
curl http://127.0.0.1:8000/api/health
# → {"status":"ok","version":"0.1.0","tables":28}

# 测试（httpx ASGI transport）
pytest tests/integration/test_health.py -q
```

## 维护注意点

- **CORS 白名单**：当前仅 `127.0.0.1:5173` 与 `localhost:5173`；后续若新增前端端口必须同步更新。
- **迁移触发**：lifespan 启动时无条件执行迁移；幂等由 `apply_migrations` 内部保证。
- **`tables` 计算**：`count_tables` 包含 `_migrations` 表（runner 自建），端点输出 `max(tables-1, 0)`，业务表恒为 28。
- **路由前缀**：业务路由应挂在 `/api` 前缀下，与 vite dev proxy 配合。
- **权威文档**：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §1 D-I1（本地 Web 形态）、§2 Sprint 0/5。