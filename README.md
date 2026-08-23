# NovelOS

> 本地优先的小说写作操作系统（Local-first novel writing OS）。
> Sprint 0：项目基础设施（git 仓库、§70 目录、迁移 runner、FastAPI 健康端点、Vite+React 骨架）。

## 仓库结构

- `packages/core/` — 后端核心（config / logging / db 迁移 / api / story-state / workflow-runtime / agent-runtime / model-router / evaluation / versioning）
- `packages/domain/` — 领域服务（world / character / plot / timeline / relationship / hooks）
- `packages/agents/` — 智能体（director / planner / writer / observer / critic / integrator）
- `packages/workflows/` — 工作流（project_init / chapter_plan / chapter_write / chapter_review / chapter_commit / simulation）
- `apps/desktop/` — React + TypeScript 前端（Vite）
- `database/migrations/` — 唯一 DDL 来源
- `tests/{unit,integration,workflow,evals}/` — pytest
- `scripts/` — `migrate.py`、`serve.py`
- `docs/` — 设计文档、PRD
- `prompts/` — Agent prompt 文件

## 快速开始

```bash
# 1) 安装依赖（Python>=3.11；前端另装）
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    fastapi 'uvicorn[standard]' pydantic jsonschema httpx pytest ruff

# 2) 执行数据库迁移（生成 data/novelos.db，业务表 28 + 1 张 _migrations）
python scripts/migrate.py

# 3) 启动后端（默认 127.0.0.1:8000）
python scripts/serve.py

# 4) 前端开发 / 构建
cd apps/desktop
npm install
npm run dev      # http://127.0.0.1:5173
npm run build    # 生成 dist/
```

健康检查：`curl http://127.0.0.1:8000/api/health`

## 测试与静态检查

```bash
python -m pytest tests/ -q
ruff check packages tests scripts
```

## 配置（环境变量，前缀 `NOVELOS_`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `NOVELOS_DATA_DIR` | `./data` | 数据目录（含 SQLite 文件） |
| `NOVELOS_DB_PATH` | `{data_dir}/novelos.db` | 覆盖默认 db 路径 |
| `NOVELOS_LOG_LEVEL` | `INFO` | 日志级别 |
| `NOVELOS_API_HOST` | `127.0.0.1` | 后端监听地址 |
| `NOVELOS_API_PORT` | `8000` | 后端监听端口 |

## Sprint 状态

- [x] S0 基础设施（当前）

更多上下文见 `docs/impl/IMPLEMENTATION-PLAN-v0.md`、`#NovelOS.md`。