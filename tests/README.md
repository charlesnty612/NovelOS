# tests（测试套件）

> 职责：NovelOS 后端的 pytest 套件，按 PRD §70 四级目录组织（unit / integration / workflow / evals），另有 `api/` 集成层与 `unit/` 子域（quality / simulation）。
> 状态：现行规模——`python -m pytest -n 4 -q` 全量 **3578 passed / 1 skipped / 1 xfail**（3580 collected；2026-10-19 批次十二实测，指纹 `5663ac14…`/612 文件跑测前后逐位一致；**`--ignore` 已撤销**——它曾挂的理由「`test_m1_lock.py` 单跑会硬杀整个进程树」经实测证伪：该现象**从未成立**（原记录把「我复现不出来」当成了「我还没找到真因」）。顺此查出并修掉一个**真缺陷**（`os.kill(pid,0)` 在 Windows 非稳定探测 ⇒ M1 单实例锁静默失效），并重写了两条本来都是空壳的 cmd_run 用例。该文件现已进入全量并行验证。V3.9.0 基线为 2059 / 2，52f639f）；前端 vitest **755** 用例 / 56 文件（`apps/web`，`npm run test`）。

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

### 现行规模与关键用例（2026-09-27 口径，替代 Sprint 0 的 12 用例清单）

| 项 | 说明 |
|---|---|
| 全量命令 | `python -m pytest -n 4 -q`（xdist 并行，约 8~10 分钟；**不再带任何 `--ignore`**，含 `test_m1_lock.py`——其曾挂的「硬杀进程树」理由已于 2026-10-16 证伪并撤销，详见 `scripts/tree_fingerprint.py` docstring 与 AGENTS.md 坑区。依赖 dev extra，裸 `uv sync` 会缺 ruff/pytest-xdist） |
| 基线 | 3578 passed / 1 skipped / 1 xfail（3580 collected；2026-10-19 批次十二实测，指纹 `5663ac14…`/612 文件跑测前后逐位一致） |
| 迁移表数 | `test_apply_migrations_creates_40_tables_total`（迁移后总表 40 = 39 业务表 + `_migrations`）；`test_business_table_count_is_39`（业务表 39，排除 `chapter_fts%` 影子表） |
| 健康检查 | `test_health_endpoint_returns_ok`：`GET /api/health` → `{status:"ok", version:<包版本>, tables:39}` |
| 端到端 | 工作流全链由 `scripts/smoke_e2e.py` 覆盖（独立临时库 + 独立端口，自动清理） |

> ⚠️ 名实不符已了结（2026-09-30）：上表这两条**测试名**过去含历史数字 `34`，而断言实为迁移后**总表** 40 与**业务表** 39。现已按断言语义改名（旧名见 `git log -p tests/unit/test_migrations.py`，全仓零残留）。

### 子目录归属

- `tests/unit/`：单元测试，不依赖 FastAPI 与外部 IO（测试用 `tmp_path`）；含 `quality/`、`simulation/` 子域
- `tests/integration/`：集成测试，使用 httpx + ASGITransport，不依赖外部服务
- `tests/api/`：API 契约与路由行为（OpenAPI 契约、SPA 托管、门禁 / 取消闭环等）
- `tests/workflow/`：工作流无头跑（引擎、四工作流、checkpoint / resume / crash 恢复）
- `tests/evals/`：评估 Harness（golden 数据集、回归基线读写与判定）
- `tests/fixtures/`：跨用例共享的 golden fixture（如 P1 规划合并的 A/B 实跑产出
  `p1_director_planner_merged_ch{3,4}.json`，供 `test_director_planner_contract.py`
  做双契约 + 白名单回归）

## 依赖

- pytest + pytest-xdist + httpx（dev extra，`pyproject.toml`）
- 业务代码：`packages/*`

## 使用 / 入口

```bash
# 全量（推荐；xdist 需在 venv 内）
# 复现上表基线：**不带任何 --ignore**（该基线就是不带 flag 测出来的）
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
