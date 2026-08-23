# tests（测试套件）

> 职责：NovelOS 后端的 pytest 套件，按 PRD §70 四级目录组织（unit / integration / workflow / evals）；Sprint 0 仅有 12 个测试全绿（unit: 10 + integration: 2），其余子目录为占位骨架。
> 状态：已实现 Sprint 0（unit/test_config、unit/test_migrations、integration/test_health）；workflow / evals 子目录为空骨架。

## 职责与边界

**做**：
- 单元测试核心层（config 默认值 / 环境变量覆盖 / 单例重置；迁移 runner 幂等 / 表数 / 外键）
- 集成测试 FastAPI 应用（健康检查 / 根端点）
- 为后续 Sprint 提供 workflow（工作流无头跑）与 evals（黄金数据集回归）子目录

**不做**：
- 不覆盖前端代码（前端 lint 由 `npm run lint` oxlint 负责）
- 不替代 `scripts/migrate.py` 的手工验证

## 对外接口

### 已实现测试用例

| 测试 | 来源 | 验证点 |
|---|---|---|
| `test_defaults` | `tests/unit/test_config.py:13` | `Settings()` 默认值（data_dir=./data, log_level=INFO, api_port=8000） |
| `test_explicit_db_path_overrides_default` | `tests/unit/test_config.py:24` | 显式 `db_path` 覆盖默认推导 |
| `test_env_var_override` | `tests/unit/test_config.py:29` | `NOVELOS_*` 环境变量注入 |
| `test_ensure_data_dir_creates` | `tests/unit/test_config.py:43` | `ensure_data_dir()` 创建目录 |
| `test_reset_clears_singleton` | `tests/unit/test_config.py:52` | `reset_settings()` 清空 `_settings_singleton` |
| `test_apply_migrations_creates_28_business_tables` | `tests/unit/test_migrations.py:17` | 迁移后总表数=29（28 业务+_migrations） |
| `test_apply_migrations_is_idempotent` | `tests/unit/test_migrations.py:26` | 二次运行 `applied=[]`、原脚本进 `skipped` |
| `test_migrations_table_records_filename` | `tests/unit/test_migrations.py:37` | `_migrations.filename / applied_at` 落库 |
| `test_get_connection_enables_foreign_keys` | `tests/unit/test_migrations.py:50` | `PRAGMA foreign_keys = ON` 生效 |
| `test_business_table_count_is_28` | `tests/unit/test_migrations.py:60` | 业务表精确 28，含 `projects/characters/chapters/commits/state_deltas/ai_call_logs` |
| `test_health_endpoint_returns_ok` | `tests/integration/test_health.py:22` | `GET /api/health` 返回 `{status:"ok", version:"0.1.0", tables:28}` |
| `test_root_endpoint` | `tests/integration/test_health.py:41` | `GET /` 返回 `{service:"novelos"}` |

### 子目录归属

- `tests/unit/`：单元测试，不依赖 FastAPI 与数据库以外部 IO（测试用 `tmp_path`）
- `tests/integration/`：集成测试，使用 httpx + ASGITransport，不依赖外部服务
- `tests/workflow/`：规划中（PRD §55-65 工作流无头跑测试，Sprint 4 起填充）
- `tests/evals/`：规划中（PRD §82-86 评估 Harness 黄金数据集回归，Sprint 4 起填充）

## 依赖

- pytest（`pyproject.toml`）
- httpx（仅 integration）
- 业务代码：`packages/core/*`

## 使用 / 入口

```bash
# 全量
pytest -q

# 仅 Sprint 0 已实现
pytest tests/unit tests/integration -q

# 单文件
pytest tests/unit/test_migrations.py -v
```

## 维护注意点

- **不引入 pytest-asyncio**：集成测试用 `httpx.ASGITransport` + `asyncio.run` 自管事件循环（参见 `tests/integration/test_health.py:5` 注释）。
- **数据库隔离**：所有 `tmp_path: Path` fixture 自动隔离，无外部副作用。
- **包导入**：pytest 通过根目录 `pyproject.toml` 的包发现解析 `packages.*`。
- **DoD 校验**：Sprint 0 DoD 要求 `pytest` 全绿；新增测试须确保不破坏 12 个已有用例。
- **权威文档**：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §1 D-I5（验证策略）、§2 Sprint 0 / 4 / 6。