# scripts（运维脚本）

> 职责：NovelOS 后端的本地运维 CLI——迁移执行与开发服务器启动；Sprint 0 仅含两个最小可执行入口，可通过 `python scripts/<name>.py` 直接运行（脚本内部自动 `sys.path.insert(0, REPO_ROOT)`）。
> 状态：已实现 Sprint 0（migrate.py / serve.py）。

## 职责与边界

**做**：
- 提供「仅跑迁移、不启服务」的开发流程入口
- 提供「启动 uvicorn 服务」的开发流程入口
- 统一从 `packages.core.config.get_settings()` 读取配置，保证 CLI 与 API 行为一致

**不做**：
- 不实现生产部署脚本（属于 Phase B Tauri 壳任务，Sprint 12）
- 不实现数据库备份/恢复（属于 Sprint 7 Version 之后）

## 对外接口

| 脚本 | 来源 | 入口函数 | 用途 |
|---|---|---|---|
| `scripts/migrate.py` | `scripts/migrate.py:16` | `main() -> int` | 执行 SQLite 迁移并打印 `applied / skipped / tables / db` |
| `scripts/serve.py` | `scripts/serve.py:15` | `main() -> int` | 启动 uvicorn，host/port 取自 `Settings` |

### `scripts/migrate.py` 输出格式

```
applied=<N> skipped=<N> tables=<int> db=<path>
newly applied:
  - 0001_init.sql
```

### `scripts/serve.py` 行为

- 调用 `uvicorn.run("packages.core.api.main:app", host=settings.api_host, port=settings.api_port, reload=False, log_level=settings.log_level.lower())`
- 默认绑定 `127.0.0.1:8000`，可通过 `NOVELOS_API_HOST` / `NOVELOS_API_PORT` 环境变量覆盖

## 依赖

- 上游：`packages.core.config.get_settings`、`packages.core.db.apply_migrations`、`packages.core.logging_config.configure_logging`
- 外部库：uvicorn（仅 `serve.py`）
- 标准库：sqlite3（迁移 runner 内部使用）

## 使用 / 入口

```bash
# 仅执行迁移
python scripts/migrate.py

# 启动开发服务器
python scripts/serve.py

# 自定义端口 / 日志级别
NOVELOS_API_PORT=9000 NOVELOS_LOG_LEVEL=DEBUG python scripts/serve.py
```

## 维护注意点

- **sys.path hack**：脚本首行 `sys.path.insert(0, REPO_ROOT)`，让 `python scripts/xxx.py` 可解析 `packages.*`；不要移除。
- **配置单一来源**：CLI 一律走 `get_settings()`，避免硬编码路径 / 端口。
- **`migrate.py` 退出码**：成功返回 0；不抛异常即视为成功（迁移幂等）。
- **`serve.py` 不启用 reload**：避免 Sprint 0 阶段文件变更导致的双进程问题；如需 reload 用 `uvicorn --reload` 显式调用。
- **权威文档**：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 0 DoD（`uvicorn` 启动 `/health` 200、迁移后 28 表）。