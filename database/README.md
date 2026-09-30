# database（数据库迁移）

> 职责：NovelOS 的 SQLite 数据库 schema 与迁移文件仓库；权威 DDL 见 `database/migrations/`（现行 0001~0030，共 30 个迁移；后续增量以目录清单为准），由 `packages/core/db.py` 的迁移 runner 顺序执行。
> 状态：现行口径 **39 张业务表**（+ runner 自建 `_migrations` 物理共 40 张；`chapter_fts%` FTS5 影子表不计入业务表口径）。

## 职责与边界

**做**：
- 提供权威 DDL（`migrations/*.sql`，按文件名升序执行）
- 通过 `database/migrations/` 全链迁移（现行 0001~0030，共 30 个迁移）建立并演进 schema（PRD §67 起逐版本增补；现行业务表 39 张）
- 与 `packages/core/db.py` 的迁移 runner 配合实现幂等迁移

**不做**：
- 不存储数据本身（库文件落在 `Settings.db_path`，默认 `./data/novelos.db`）
- 不实现 ORM 模型（属于领域 Service）

## 对外接口

| 文件 | 说明 |
|---|---|
| `database/migrations/*.sql`（现行 0001~0030，共 30 个迁移） | 唯一 DDL 来源；按文件名升序执行 |

### 表清单（39 业务表 + runner 自建 `_migrations` 物理共 40）

**权威清单以 `database/migrations/` 迁移文件为准**（2026-09-13 三仓检修：原按 `0001_init.sql` 列举的「28 张表」口径已过期，现行 39 张业务表）：

- 现场枚举：`grep -h "CREATE TABLE" database/migrations/*.sql`；
- 口径：排除 `_migrations` 与 `chapter_fts%`（FTS5 影子表），与 `tests/unit/test_migrations.py` 的表数断言、`/api/health` 的 `tables:39` 对齐；
- 演进：`0001_init` 28 张起，0004/0007/0008/0009/0015/0016/0025/0027 等迁移陆续加表；逐版本增量以迁移文件与 CHANGELOG 为准。

> 设计意图速览（v0 起沿用，现行命名以迁移文件为准）：`projects` 为项目根；故事状态三件套 `story_states`（快照）/ `commits` / `state_deltas`；工作流 `workflow_runs` / `workflow_run_nodes`；质量 `quality_reports`；参照系 `reference_canons` / `canon_extracts`；题材库 `genre_packs`。逐表字段与注释以各迁移文件为准。

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
- **业务表数校验**：`tests/unit/test_migrations.py` 的 `test_apply_migrations_creates_34_business_tables`（迁移后总表 40 = 39 业务表 + `_migrations`）与 `test_business_table_count_is_34`（业务表 39，排除 `chapter_fts%`）；新增 / 删除表必须同步更新该测试。
> ⚠️ 两条**测试名**仍含历史数字 `34`（名未跟断言走：`:48` 断言 40、`:434` 断言 39），已登记待 Lead 裁决，本次不改测试名。
- **权威文档**：`docs/impl/IMPLEMENTATION-PLAN-v0.md`（历史计划）§1 D-I5（Schema 权威）、§2 Sprint 0/1/2；`docs/state-model/state-delta-v0.md`（已迁内容仓 NovelOS-Content:docs/state-model/state-delta-v0.md）；`docs/state-model/schemas/state-delta.schema.json` / `state-commit.schema.json`。