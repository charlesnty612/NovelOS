# tests（测试套件）

> 职责：NovelOS 后端的 pytest 套件，按 PRD §70 四级目录组织（unit / integration / workflow / evals），另有 `api/` 集成层与 `unit/` 子域（quality / simulation）。
> 状态：现行规模——`python -m pytest -n 4 -q` 全量 **2059 passed / 2 skipped**（2026-09-13 V3.9.0 基线，52f639f 实测）；前端 vitest 422 用例（`apps/web`，`npm run test`）。

## 职责与边界

**做**：
- 单元测试核心层与领域服务（配置 / 迁移 runner / State Delta / 质量评分 / 上下文装配 / 模型路由 / 内容同步 …）
- 集成测试 FastAPI 应用与领域 API（health / projects / chapters / story-state / 导出 / 题材库 …）
- 工作流无头测试（chapter plan→write→review→commit、拆书、引擎 resume / cancel）
- 评估回归（golden 数据集 + 结构签名基线，`tests/evals/`）

**不做**：
- 不覆盖前端代码（前端验证走 `npm run build`（`tsc -b && vite build`）与 `npm run test`（vitest），见 `apps/web/package.json`）
- 不替代 `scripts/migrate.py` / `scripts/smoke_e2e.py` 的手工与端到端验证

## 对外接口

### 现行规模与关键用例（2026-09-13 口径，替代 Sprint 0 的 12 用例清单）

| 项 | 说明 |
|---|---|
| 全量命令 | `python -m pytest -n 4 -q`（xdist 并行，约 4 分钟；依赖 dev extra，裸 `uv sync` 会缺 ruff/pytest-xdist） |
| 基线 | 2059 passed / 2 skipped（52f639f 实测） |
| 迁移表数 | `test_apply_migrations_creates_34_business_tables`（迁移后总表 39 = 38 业务表 + `_migrations`）；`test_business_table_count_is_34`（业务表 38，排除 `chapter_fts%` 影子表） |
| 健康检查 | `test_health_endpoint_returns_ok`：`GET /api/health` → `{status:"ok", version:<包版本>, tables:38}` |
| 端到端 | 工作流全链由 `scripts/smoke_e2e.py` 覆盖（独立临时库 + 独立端口，自动清理） |

### 子目录归属

- `tests/unit/`：单元测试，不依赖 FastAPI 与外部 IO（测试用 `tmp_path`）；含 `quality/`、`simulation/` 子域
- `tests/integration/`：集成测试，使用 httpx + ASGITransport，不依赖外部服务
- `tests/api/`：API 契约与路由行为（OpenAPI 契约、SPA 托管、门禁 / 取消闭环等）
- `tests/workflow/`：工作流无头跑（引擎、四工作流、checkpoint / resume / crash 恢复）
- `tests/evals/`：评估 Harness（golden 数据集、回归基线读写与判定）

## 依赖

- pytest + pytest-xdist + httpx（dev extra，`pyproject.toml`）
- 业务代码：`packages/*`

## 使用 / 入口

```bash
# 全量（推荐；xdist 需在 venv 内）
python -m pytest -n 4 -q

# 单目录
python -m pytest tests/unit -q

# 单文件
python -m pytest tests/unit/test_migrations.py -v
```

## 维护注意点

- **不引入 pytest-asyncio**：集成测试用 `httpx.ASGITransport` + `asyncio.run` 自管事件循环（参见 `tests/integration/test_health.py:5` 注释）。
- **数据库隔离**：所有 `tmp_path: Path` fixture 自动隔离，无外部副作用。
- **包导入**：pytest 通过根目录 `pyproject.toml` 的包发现解析 `packages.*`。
- **DoD 校验**：改动后全量 `python -m pytest -n 4 -q` 绿 + ruff 0；新增迁移 / 表必须同步 `tests/unit/test_migrations.py` 的表数断言。
- **权威文档**：`docs/impl/IMPLEMENTATION-PLAN-v0.md`（历史计划）§1 D-I5（验证策略）、§2 Sprint 0 / 4 / 6。
