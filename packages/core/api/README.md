# core.api（FastAPI 应用）

> 职责：NovelOS 后端的 HTTP 入口——FastAPI 应用工厂、lifespan 启动迁移、健康检查与服务信息端点；为前端 vite dev proxy 提供 `/api` 前缀挂载点。
> 状态：已实现 Sprint 0。

## 职责与边界

**做**：
- 构造 FastAPI 应用并配置 CORS（允许 `http://127.0.0.1:5173` 与 `http://localhost:5173`）
- lifespan 启动：确保 `data_dir` 存在、执行迁移、记录迁移结果到 `app.state.migration_result`
- 提供 `GET /api/health`（状态 / 版本 / 业务表数）端点
- Sprint 5：检测 SPA 构建产物（`apps/web/dist` 或 `NOVELOS_WEB_DIST` 覆盖），存在则挂载静态资源与 SPA fallback；不存在时保留 `GET /`（服务信息 JSON）作为纯后端入口
- 暴露 `create_app(settings)` 工厂函数供测试注入临时 `Settings`
- 提供 `python -m packages.core.api.main` 直接启动入口（`__main__`），默认端口 18081

**不做**：
- 不挂载业务领域路由（属于 Sprint 1+ 的 `packages/domain/*`，由 `discover_routers()` 自动发现并挂到 `/api`）
- 不实现 WebSocket 长连接（属于 Sprint 5 Workbench UI）
- 不接管 `scripts/serve.py` 的端口配置（仍由 `Settings.api_port` + `NOVELOS_API_PORT` 控制）

## 对外接口

| 名称 | 来源 | 说明 |
|---|---|---|
| `create_app(settings: Settings | None) -> FastAPI` | `packages/core/api/main.py` | 工厂函数，测试可注入 settings |
| `app` | `packages/core/api/main.py` | 默认 FastAPI 实例（uvicorn 入口） |
| `__version__ = "0.1.0"` | `packages/core/api/main.py` | API 版本号 |
| `lifespan(app)` | `packages/core/api/main.py` | 异步上下文管理器，启动时执行迁移 |
| `GET /api/health` | `packages/core/api/main.py` | 返回 `{status:"ok", version, tables:28}` |
| `GET /`（SPA 关闭时） | `packages/core/api/main.py` | 返回 `{service, version, docs}` |
| `GET /`、`GET /{path}`（SPA 启用时） | `packages/core/api/main.py` | catch-all 返回 `dist/index.html`（SPA fallback） |
| `resolve_web_dist()` | `packages/core/api/main.py` | 解析 SPA dist 路径（`NOVELOS_WEB_DIST` > `<repo>/apps/web/dist`） |
| `python -m packages.core.api.main` | `packages/core/api/main.py` __main__ | 默认端口 18081，`NOVELOS_PORT` 覆盖 |
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

# Sprint 5：main.py 自带 __main__ 入口，默认端口 18081（8000 常被占用）
python -m packages.core.api.main            # → 127.0.0.1:18081
NOVELOS_PORT=19090 python -m packages.core.api.main

# 浏览器
curl http://127.0.0.1:18081/api/health
# → {"status":"ok","version":"0.1.0","tables":28}

# 测试（httpx ASGI transport）
pytest tests/integration/test_health.py -q
```

### Sprint 5：SPA 托管与端口配置

**SPA 托管**（`packages/core/api/main.py`）：
- 默认查找 `<repo>/apps/web/dist`，环境变量 `NOVELOS_WEB_DIST`（绝对路径）可覆盖。
- 检测条件：`dir.exists()` 且 `dir/index.html` 是文件；否则视为未托管，保持纯后端行为。
- 启用时：
  - `dist/assets/` 存在 → `app.mount("/assets", StaticFiles(...))`。
  - catch-all GET `/{full_path:path}` 返回 `dist/index.html`（SPA fallback）。
  - `/api/*` 路由始终由业务路由处理，不被 fallback 拦截。
- 未启用时（dist 缺失）：保留 `GET /` 返回服务信息 JSON（`{service, version, docs}`）。
- 路由顺序注意：`/api/health` 与业务路由必须先注册；SPA fallback catch-all 最后注册，避免吃掉 API 请求。

**端口配置**（`packages/core/api/main.py:__main__`）：
- `python -m packages.core.api.main` 默认端口 `18081`（8000 在开发者本机常被占用）。
- 环境变量 `NOVELOS_PORT` 覆盖（最高优先级）；缺省回退 18081。
- `scripts/serve.py` 仍由 `Settings.api_port`（`NOVELOS_API_PORT` 环境变量）控制，**未**改 Sprint 0 默认 8000（保基线 `test_defaults` 断言）。

## 维护注意点

- **CORS 白名单**：当前仅 `127.0.0.1:5173` 与 `localhost:5173`；后续若新增前端端口必须同步更新。
- **迁移触发**：lifespan 启动时无条件执行迁移；幂等由 `apply_migrations` 内部保证。
- **`tables` 计算**：`count_tables` 包含 `_migrations` 表（runner 自建），端点输出 `max(tables-1, 0)`，业务表恒为 28。
- **路由前缀**：业务路由应挂在 `/api` 前缀下，与 vite dev proxy 配合。
- **SPA 路由注册顺序**：SPA fallback catch-all `/{full_path:path}` 必须最后注册；`/api/health` / 业务路由 / `root()` 必须先注册，否则 SPA 会吃掉 API 请求或吃掉 `GET /` 服务信息。
- **NOVELOS_PORT vs NOVELOS_API_PORT**：两个端口变量语义不同——`NOVELOS_PORT` 只影响 `packages/core/api/main.py:__main__` 入口；`NOVELOS_API_PORT` 只影响 `Settings.api_port`（被 `scripts/serve.py` 消费）。不要混用。
- **权威文档**：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §1 D-I1（本地 Web 形态）、§2 Sprint 0/5。