# database（数据库迁移）

> 职责：NovelOS 的 SQLite 数据库 schema 与迁移文件仓库；当前含 `migrations/0001_init.sql`（28 张业务表），由 `packages/core/db.py` 的迁移 runner 顺序执行。
> 状态：已实现 Sprint 0（28 业务表 + runner 自建 `_migrations` 跟踪表）。

## 职责与边界

**做**：
- 提供权威 DDL（`migrations/*.sql`，按文件名升序执行）
- 通过 `database/migrations/0001_init.sql` 建立 MVP 完整 schema（PRD §67，25 表 + 3 表 v1.1 新增 = 28 表）
- 与 `packages/core/db.py` 的迁移 runner 配合实现幂等迁移

**不做**：
- 不存储数据本身（库文件落在 `Settings.db_path`，默认 `./data/novelos.db`）
- 不实现 ORM 模型（属于 Sprint 1 领域 Service）

## 对外接口

| 文件 | 说明 |
|---|---|
| `database/migrations/0001_init.sql` | Sprint 0 初始 schema，28 张业务表 |

### 表清单（28 业务表 + runner 自建 `_migrations` = 29）

按迁移文件中 `CREATE TABLE` 顺序：

1. `projects` — 项目根
2. `characters` — 角色定义侧（PRD §16 §17）
3. `character_arcs` — 角色弧线
4. `worlds` — 世界设定
5. `locations` — 地点
6. `factions` — 阵营
7. `world_rules` — 世界规则
8. `plot_graph_nodes` — 情节图节点
9. `plot_graph_edges` — 情节图边
10. `timeline_events` — 时间线事件
11. `chapters` — 章节
12. `scenes` — 场景
13. `chapter_outlines` — 章节大纲
14. `relationships` — 人物关系
15. `relationship_events` — 关系变更事件
16. `hooks` — 伏笔
17. `narrative_debts` — 叙事债务
18. `commits` — 状态提交（PRD §91）
19. `state_deltas` — 状态增量（`docs/state-model/state-delta-v0.md` §2.2）
20. `state_snapshots` — 状态快照（Sprint 7 Version 用）
21. `ai_call_logs` — AI 调用日志（PRD §93）
22. `reveal_policies` — 揭示策略（PRD v1.1 新增）
23. `workflow_runs` — 工作流运行实例
24. `workflow_run_nodes` — 工作流节点执行（v1.1 新增）
25. `quality_scores` — 质量评分（Sprint 6）
26. `guardrail_violations` — Guardrail 违规记录（Sprint 6）
27. `eval_golden_datasets` — 黄金数据集（Sprint 4 Eval Harness）
28. `eval_regression_runs` — 回归运行记录

## 依赖

- 上游消费者：`packages/core/db.py:apply_migrations`、`scripts/migrate.py`
- 外部库：SQLite（stdlib `sqlite3`）
- 不引入 Alembic 等迁移框架（保持 Sprint 0 极简，符合「单一 DDL 来源」原则）

## 使用 / 入口

```bash
# 方式一：通过 CLI
python scripts/migrate.py

# 方式二：通过 API lifespan（启动 uvicorn 时自动执行）
python scripts/serve.py

# 方式三：编程式
python -c "from packages.core.db import apply_migrations; print(apply_migrations('./data/novelos.db'))"
```

新增迁移：按 `0002_xxx.sql`、`0003_xxx.sql` ... 顺序命名，runner 自动按文件名升序执行；不要修改已应用的脚本（PRD §75「只允许加列/加表，不允许破坏性变更」）。

## 维护注意点

- **PRAGMA 头**：`0001_init.sql` 头部开启 `foreign_keys=ON` 与 `journal_mode=WAL`，与 `packages/core/db.py:get_connection` 一致。
- **TEXT 主键**：人类可读前缀（`prj_/char_/event_/hook_/debt_/commit_`...），便于日志追踪。
- **时间戳**：所有时间字段为 ISO-8601 TEXT，与 `state-delta.schema.json` / `state-commit.schema.json` 对齐。
- **可见性枚举**：`visibility` 四级 `PUBLIC / VISIBLE / RESTRICTED / HIDDEN`（PRD §4 原则 7）。
- **业务表数校验**：`tests/unit/test_migrations.py:test_business_table_count_is_28` 精确断言 28 张；新增 / 删除表必须同步更新该测试。
- **权威文档**：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §1 D-I5（Schema 权威）、§2 Sprint 0/1/2；`docs/state-model/state-delta-v0.md`；`docs/state-model/schemas/state-delta.schema.json` / `state-commit.schema.json`。