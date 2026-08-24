# core.backup — 项目级 JSON 备份 / 恢复

> 职责：V1.4 Sprint 16 / MVP 落地的「整项目导出 / 导入」能力——单 JSON 包，
> 22 张业务表行级备份；导入为新项目（不覆盖源项目），主键与外键 id 重映射，
> 单事务回环。

## 1. 模块结构

```
packages/core/backup/
├── __init__.py     # 公共 API 导出
├── README.md       # 本文件
├── schema.py       # 包格式契约（BACKUP_FORMAT / BACKUP_VERSION / EXPORTED_TABLES / validate_backup）
├── ids.py          # 表元信息（TABLE_META）+ 主键生成 helper
└── service.py      # BackupService 主类（export_project / import_project）
```

## 2. 公共 API

```python
from packages.core.backup import (
    BackupService,             # 主入口
    BACKUP_FORMAT,             # "novelos-backup"
    BACKUP_VERSION,            # 1
    EXPORTED_TABLES,           # 22 张表的导出白名单（tuple）
    TABLE_META,                # 表级元信息：主键列 / 主键前缀
    validate_backup,           # 导入前格式校验
)
```

`BackupService.export_project(project_id) -> dict`：

- 校验项目存在 → 不存在抛 `ValueError(f"project {pid!r} not found")`；
- 逐表 `SELECT *` 收集该项目的行（含通过 `JOIN` 间接过滤的二级表）；
- 组装顶层包（含 metadata 标记）。

`BackupService.import_project(data: dict) -> dict`：

- 调 `validate_backup` 做格式校验；
- 单连接 + 单事务写入；
- **不覆盖源项目**：永远创建新 `project_id`（`new_id("prj")`），新项目名 = `f"{原名}（导入）"`，`status='ACTIVE'`；
- 22 张表按 `EXPORTED_TABLES` 顺序重映射主键与外键；
- 失败任意步 → `conn.rollback()` + 抛 `ValueError`（不残留半导入项目）；
- 成功返回新 `projects` 行 dict（结构与 `ProjectService.create` 一致）。

## 3. 备份包格式（顶层契约）

```json
{
  "format": "novelos-backup",
  "version": 1,
  "exported_at": "<iso8601 UTC>",
  "exported_from_project_id": "<原项目 id>",
  "metadata": {
    "schema_migrations": ["0001_init.sql", ..., "0008_author_style_samples_and_overdue.sql"],
    "table_count_exported": 22,
    "exported_table_names": ["characters", "locations", ...],
    "exported_at_iso": "<iso8601 UTC>",
    "api_keys_stripped": true,
    "ai_call_logs_excluded": true,
    "evaluations_excluded": true,
    "workflow_runs_excluded": true,
    "model_configs_excluded": true,
    "reference_canons_excluded": true
  },
  "project": { ...projects 行原文... },
  "tables": {
    "<表名>": [ ...行... ]
  }
}
```

### 3.1 关键约束

- `format` 必须等于 `BACKUP_FORMAT = "novelos-backup"`；
- `version` 必须等于 `BACKUP_VERSION = 1`（不接受 0/2 等其它版本）；
- `tables` 的 key 必须在 `EXPORTED_TABLES` 内（白名单闭合）——**多出任何表直接 422**，
  防御误传 `model_configs`（含 API key）等敏感表；
- 每张表的 value 必须是 JSON 数组（允许空 `[]`）；
- `project` 必须含 `project_id / name / created_at` 三个必填字段。

### 3.2 metadata 自证字段（强制写入）

| 字段 | 值 | 含义 |
|---|---|---|
| `api_keys_stripped` | `true` | 备份包不包含任何 API key（model_configs 未被导出） |
| `ai_call_logs_excluded` | `true` | LLM 调用日志未导出（运行时数据，体积+隐私） |
| `evaluations_excluded` | `true` | 早期评分表未导出（与 quality_reports 冗余） |
| `workflow_runs_excluded` | `true` | 工作流实例未导出（运行时） |
| `model_configs_excluded` | `true` | 模型路由配置未导出（全局 + 含 API key） |
| `reference_canons_excluded` | `true` | 拆书参照系未导出（v1.4 MVP defer） |

## 4. 导出表清单（共 22 张）

### 4.1 第一波（仅依赖 `projects.project_id`，13 张）

按 `EXPORTED_TABLES` 顺序：

1. `characters`（角色定义）
2. `locations`（地点）
3. `factions`（势力）
4. `world_rules`（世界规则）
5. `hooks`（伏笔）
6. `narrative_debts`（叙事债务）
7. `chapters`（章节）
8. `branches`（分支；含 `parent_branch_id` 自引用，第二轮改写）
9. `memories`（记忆）
10. `reveal_policies`（释放策略）
11. `author_style_samples`（作者文风样例，Sprint 15）
12. `chapter_summaries`（章节摘要，Sprint 14）
13. `story_states`（Canonical 快照；复合主键 `(project_id, state_version)`）

### 4.2 第二波（依赖 `characters` 或 `chapters`，3 张）

14. `character_states`（角色状态；复合主键 `(character_id, state_version)`）
15. `scenes`（场景）
16. `drafts`（草稿）

### 4.3 第三波（依赖 `plot_events` / `characters` / `chapters`，6 张）

17. `plot_events`（剧情事件）
18. `timeline_events`（时间线事件，通过 `event_id` 间接过滤）
19. `relationships`（关系）
20. `state_deltas`（状态变更提案；含 `supersedes` 自引用，第二轮改写）
21. `commits`（State Commit）
22. `quality_reports`（质量评估报告）

### 4.4 显式排除（不在白名单）

- `model_configs`（全局表 + 含 API key，红线）
- `agents` / `prompts` / `workflows`（全局）
- `workflow_runs` / `workflow_run_nodes` / `ai_call_logs`（运行时）
- `evaluations`（与 quality_reports 冗余）
- `reference_canons` / `canon_extracts`（v1.4 MVP defer，避免引入拆书工作流相关数据）

## 5. ID 重映射

### 5.1 策略

- **单列主键表**：用 `TABLE_META[table]["pk_prefix"]` 调 `packages.core.ids.new_id(prefix)`
  生成新主键；旧主键入全局映射 `project_id_map[old_pk] = new_pk`。
- **复合主键表**：
  - `character_states (character_id, state_version)` —— `character_id` 替换，
    `state_version` 保留；
  - `story_states (project_id, state_version)` —— `project_id` 与 `commit_id`
    替换，`state_version` 保留。

### 5.2 外键列改写

按"全局映射表"统一处理：遍历行内所有字符串字段，若值存在于
`project_id_map`，则替换；`project_id` 列特殊处理（任何表都从映射表查）；
自引用外键（`branches.parent_branch_id` / `state_deltas.supersedes`）在第一轮
INSERT 时置 `NULL` 落库，同时把 `(table, pk_col, new_pk_value, old_target)`
记入 `BackupService._self_ref_rewrites` 内存清单；第二轮
`_rewrite_self_references` 直接按该清单 + 全局 `project_id_map` UPDATE 新行
的对应列为映射后的新 id（**不**依赖库内 `WHERE col IS NOT NULL` 扫描，
因为第一轮已统一置 NULL）。旧目标值若不在 `project_id_map`（如源行本身
就是 NULL，或指向非本次导入批次内的行），保持 NULL。

### 5.3 FK 校验时序

导入时先 `PRAGMA foreign_keys = OFF`（避免跨表 INSERT 时悬挂），所有行落库后再
`PRAGMA foreign_keys = ON` + 触发一次空查询让 SQLite 重检 FK，任意 FK 违反 → 抛
`IntegrityError` → rollback。事务原子性由 `BEGIN IMMEDIATE` + `commit` 保证
（`get_connection` 默认行为）。

## 6. 导入事务与回滚

- 单 `sqlite3.Connection` + 单事务包裹整次导入；
- 任意步骤抛异常 → `conn.rollback()` → 不残留半导入项目；
- 事务内禁用 FK 检查（导入末开启），避免中间态被 FK 校验阻截；
- 提交前做一次完整 FK 校验（通过 `PRAGMA foreign_keys = ON` + SELECT 触发）；
- 返回值：新 `projects` 行 dict（含 `project_id / name / status / created_at` 等）。

## 7. 安全红线

- **API key 隔离**：导出模块完全不接触 `model_configs` 表；包内**不会**出现
  任何 `api_key` 字段值；`metadata.api_keys_stripped = true` 显式声明。
- **路径安全**：本模块不接触文件系统，导入通过 JSON body 走 FastAPI；不构造
  任何文件路径，无路径穿越面。
- **只读源项目**：导入永远创建**新**项目（`new_id("prj")`），绝不会修改源项目；
  源项目所有数据保持不变。

## 8. 关联文档

- 权威规范：`PRD § 100 / § 101`（备份 / 恢复能力口径）
- 备份包 schema：`packages/core/backup/schema.py`
- ID 与时间戳：`packages/core/ids.py`
- 测试：`tests/unit/test_backup.py`（9 例）

## 9. 测试覆盖

`tests/unit/test_backup.py`（9 例）：

- `test_export_contains_expected_tables` —— 导出包含全部 22 张表，行数正确。
- `test_export_metadata_has_api_keys_stripped` —— metadata 标记 + 递归扫描包内无 `api_key`。
- `test_export_strips_ai_call_logs_and_evaluations` —— 不含运行时 / 敏感表。
- `test_export_404_like_for_missing_project` —— 不存在项目 → ValueError。
- `test_roundtrip_consistency` —— export → import → 比对内容一致。
- `test_import_remaps_all_ids` —— 导入后无残留旧 project_id。
- `test_import_rejects_bad_format` —— 坏 format → ValueError。
- `test_import_rejects_bad_version` —— 坏 version → ValueError。
- `test_import_rolls_back_on_failure` —— 中途失败 → 整体回滚，无残留。

## 10. 已知边界 / V1.x 升级路径

- **`reference_canons` / `canon_extracts`**：V1.4 MVP 不导出；V1.5 若需支持
  参照系备份，需在 `EXPORTED_TABLES` 末尾追加并补全 `TABLE_META` + `id 重映射`。
- **`workflow_runs` + `nodes` + `ai_call_logs`**：刻意排除；这是运行时数据，
  重新跑工作流即可恢复。后续若想「冻结工作流历史一并备份」，需独立设计版本
  （建议 v2 协议，分包存储）。
- **`memory.embedding_ref`**：导出的是引用字符串而非向量本体；向量数据库
  （LanceDB/Chroma）需另行导入导出策略，本 MVP 不涉及。
- **跨大版本兼容**：`BACKUP_VERSION=1`；若字段或表结构破坏性变更，必须升级
  到 v2，并保留 v1 兼容路径（接受 `version=1` 不接受 v2，或反之）。
- **大项目体积**：单 JSON 包可能较大；当前无 size 上限；V1.5 可加
  `MAX_BACKUP_BYTES` 与流式下载（Content-Disposition + gzip）。
- **快照内嵌 id 不重映射（行级 MVP 口径）**：`story_states.snapshot_json` 等
  `*_json` 列内嵌的实体 id（`chapter_id` / `commit_id` / `character_id` 等）
  不做深度重映射——导入新项目后，快照 JSON 内嵌的 id 仍指向**源项目**命名空间。
  行级数据完整、可回溯展示，但按内嵌 id 跨库反查实体不可用。后续若需深度
  重映射，可利用稳定 id 前缀（`ch_/cmt_/char_/event_/tle_/rel_/hook_/debt_/...`）
  做浅层替换（先解析 JSON、再对每个字符串值做前缀感知替换）。当前 MVP 范围内
  不主动处理，避免无谓的实现复杂度与误改风险。