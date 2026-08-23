# core（核心层）

> 职责：NovelOS 后端的核心基础能力——配置、日志、数据库迁移、FastAPI 应用工厂，以及后续 Sprint 中的 Story State、Context Engine、Agent Runtime、Workflow Runtime、Model Router、Evaluation、Versioning 等核心子模块的容器。
> 状态：已实现 Sprint 0 基础设施（config / logging_config / db / api）；其余子包为占位骨架，详见各自 README。

## 职责与边界

**做**：
- 运行时配置加载（环境变量 `NOVELOS_*` 前缀）
- 结构化日志（stdlib logging，控制台 stderr 输出）
- SQLite 连接与迁移 runner（幂等执行 `database/migrations/*.sql`）
- FastAPI 应用工厂与健康检查端点
- 为后续 Sprint 提供 Story State、Context Engine、Agent Runtime、Workflow Runtime、Model Router、Evaluation、Versioning 子模块的命名空间

**不做**：
- 不实现任何领域业务逻辑（属于 `packages/domain`）
- 不实现任何具体工作流定义（属于 `packages/workflows`）
- 不直接调用任何 LLM（属于 `packages/core/model_router` 子包规划）

## 对外接口

| 名称 | 来源 | 说明 |
|---|---|---|
| `Settings` | `packages/core/config.py:21` | 配置 dataclass，含 `data_dir / db_path / log_level / api_host / api_port` |
| `Settings.load()` | `packages/core/config.py:49` | 从环境变量加载，`NOVELOS_*` 前缀 |
| `Settings.ensure_data_dir()` | `packages/core/config.py:65` | 确保 `data_dir` 存在 |
| `Settings.to_dict()` | `packages/core/config.py:69` | 序列化为 dict |
| `get_settings()` | `packages/core/config.py:82` | 全局单例 |
| `reset_settings()` | `packages/core/config.py:90` | 测试辅助，清除单例 |
| `configure_logging(level)` | `packages/core/logging_config.py:17` | 配置根 logger（幂等） |
| `get_logger(name)` | `packages/core/logging_config.py:35` | 取命名 logger（首次自动配置） |
| `get_connection(db_path)` | `packages/core/db.py:22` | 开启外键 PRAGMA 的 sqlite3 连接 |
| `apply_migrations(db_path, migrations_dir=None)` | `packages/core/db.py:57` | 幂等执行迁移，返回 `{"applied","skipped","tables"}` |
| `count_tables(db_path)` | `packages/core/db.py:102` | 查询业务表数量 |
| `create_app(settings=None)` | `packages/core/api/main.py:40` | FastAPI 应用工厂 |
| `app` | `packages/core/api/main.py:83` | 默认 app（uvicorn 入口） |
| `GET /api/health` | `packages/core/api/main.py:61` | 返回 `{"status","version","tables"}` |
| `GET /` | `packages/core/api/main.py:75` | 服务信息 |

## 依赖

**Python 依赖**（`pyproject.toml`）：fastapi、uvicorn、pydantic（v1 兼容）、pytest、httpx。

**上游模块**：无（核心层是底层）。

**下游消费者**：`packages/domain`、`packages/agents`、`packages/workflows`、`scripts/migrate.py`、`scripts/serve.py`、`tests/integration/test_health.py`、`tests/unit/test_config.py`、`tests/unit/test_migrations.py`。

## 使用 / 入口

```bash
# 启动 FastAPI 服务
python scripts/serve.py
# 或
uvicorn packages.core.api.main:app --host 127.0.0.1 --port 8000

# 仅跑迁移
python scripts/migrate.py

# 测试
pytest tests/unit tests/integration -q
```

健康检查：`curl http://127.0.0.1:8000/api/health`

## 维护注意点

- **设计约束**：Sprint 0 不引入 `pydantic-settings` 依赖，`Settings` 用显式字段 + `load()` 类方法实现，避免 v1/v2 兼容问题。
- **迁移幂等**：`_migrations` 表追踪已执行脚本；重复运行 `apply_migrations` 不会重复执行 DDL。
- **表数口径**：迁移后 `count_tables` 返回 29（含 `_migrations`），业务表 = 28；`/api/health` 端点已减去 1。
- **包导入**：脚本入口通过 `sys.path.insert(0, REPO_ROOT)` 让 `python scripts/xxx.py` 可直接解析 `packages.*`。
- **权威文档**：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §1 决策 §2 Sprint 表；`docs/state-model/state-delta-v0.md`（后续 Sprint 参考）。