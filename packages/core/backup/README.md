# core.backup — 项目级 JSON 备份 / 恢复

> 职责：V1.4 Sprint 16 / MVP 落地的「整项目导出 / 导入」能力——单 JSON 包，
> 23 张业务表行级备份；导入为新项目（不覆盖源项目），主键、外键与 JSON 列内嵌
> 实体 id 全部重映射，单事务回环。

## 1. 模块结构

```
packages/core/backup/
├── __init__.py     # 公共 API 导出
├── README.md       # 本文件
├── schema.py       # 包格式契约（BACKUP_FORMAT / BACKUP_VERSION / EXPORTED_TABLES / validate_backup）
├── ids.py          # 表元信息（TABLE_META）+ 主键生成 helper
├── json_ids.py     # JSON 列清单（JSON_ID_COLUMNS）+ 内嵌 id 递归重映射 walker
└── service.py      # BackupService 主类（export_project / import_project）
```

## 2. 公共 API

```python
from packages.core.backup import (
    BackupService,             # 主入口
    BACKUP_FORMAT,             # "novelos-backup"
    BACKUP_VERSION,            # 1
    EXPORTED_TABLES,           # 23 张表的导出白名单（tuple）
    TABLE_META,                # 表级元信息：主键列 / 主键前缀
    validate_backup,           # 导入前格式校验
)
from packages.core.backup.json_ids import (
    JSON_ID_COLUMNS,           # 表 → 含内嵌实体 id 的 JSON 列清单
    remap_json_column,         # TEXT JSON → 递归重映射 → TEXT
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
- 写库前先**一次性预建全局旧→新 id 映射**（跨表一份），再按 `EXPORTED_TABLES` 顺序逐表重映射主键 / 外键 / JSON 内嵌 id；
- 提交前 `PRAGMA foreign_key_check` 校验（与导入前基线做差集），悬挂引用 → rollback + `ValueError`；
- 失败任意步 → `conn.rollback()` + 抛 `ValueError`（不残留半导入项目）；
- 成功返回新 `projects` 行 dict（结构与 `ProjectService.create` 一致；列集按包内实际列动态拼装）。

## 3. 备份包格式（顶层契约）

```json
{
  "format": "novelos-backup",
  "version": 1,
  "exported_at": "<iso8601 UTC>",
  "exported_from_project_id": "<原项目 id>",
  "metadata": {
    "schema_migrations": ["0001_init.sql", ..., "0025_genre_packs.sql"],
    "table_count_exported": 23,
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

## 4. 导出表清单（共 23 张）

### 4.1 第一波（仅依赖 `projects.project_id`，14 张）

按 `EXPORTED_TABLES` 顺序：

1. `characters`（角色定义）
2. `locations`（地点）
3. `factions`（势力）
4. `world_rules`（世界规则）
5. `hooks`（伏笔）
6. `narrative_debts`（叙事债务）
7. `volumes`（卷；迁移 0015，V3.9 补入白名单）
8. `chapters`（章节；`volume_id` → volumes）
9. `branches`（分支；含 `parent_branch_id` 自引用，第二轮改写）
10. `memories`（记忆）
11. `reveal_policies`（释放策略）
12. `author_style_samples`（作者文风样例，Sprint 15）
13. `chapter_summaries`（章节摘要，Sprint 14）
14. `story_states`（Canonical 快照；复合主键 `(project_id, state_version)`）

### 4.2 第二波（依赖 `characters` 或 `chapters`，3 张）

15. `character_states`（角色状态；复合主键 `(character_id, state_version)`）
16. `scenes`（场景）
17. `drafts`（草稿）

### 4.3 第三波（依赖 `plot_events` / `characters` / `chapters`，6 张）

18. `plot_events`（剧情事件）
19. `timeline_events`（时间线事件，通过 `event_id` 间接过滤）
20. `relationships`（关系）
21. `state_deltas`（状态变更提案；含 `supersedes` 自引用，第二轮改写）
22. `commits`（State Commit）
23. `quality_reports`（质量评估报告）

### 4.4 显式排除（不在白名单）

- `model_configs`（全局表 + 含 API key，红线）
- `agents` / `prompts` / `workflows`（全局）
- `workflow_runs` / `workflow_run_nodes` / `ai_call_logs`（运行时）
- `evaluations`（与 quality_reports 冗余）
- `reference_canons` / `canon_extracts`（v1.4 MVP defer，避免引入拆书工作流相关数据）

## 5. ID 重映射

### 5.1 策略

- **全局映射预建**（V3.9 F1）：任何 INSERT 之前，`_build_id_map` 先扫一遍包内全部表，
  把「旧主键入 → 新主键」一次性写进 `project_id_map`（跨表一份）。后建表的前向引用
  因此能命中映射——典型两例：`story_states.commit_id` → `commits`（story_states 在
  EXPORTED_TABLES 里排在 commits 之前）、`chapters.volume_id` → `volumes`。
  逐表边插边建映射时，这两列会原样落旧 id。
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

### 5.3 JSON 列内嵌实体 id 重映射（V3.9 F1）

列清单见 `json_ids.JSON_ID_COLUMNS`（口径：导出表的所有 `*_json` 列 + `who_knows`；
`drafts.content` 这类散文字段不入清单，避免无谓的误改风险）：

| 表 | 列 |
|---|---|
| `characters` / `locations` / `factions` / `world_rules` | `data_json`（或 `core_json`）、`who_knows` |
| `plot_events` | `cause_json` / `effects_json` / `participants_json` / `time_json` / `who_knows` |
| `chapters` / `scenes` | `plan_json`、`who_knows`（scenes） |
| `hooks` / `narrative_debts` / `timeline_events` | `who_knows` |
| `character_states` | `state_json`、`who_knows` |
| `relationships` | `state_json`、`who_knows` |
| `story_states` | `snapshot_json`（全量快照） |
| `volumes` | `terminal_snapshot_json` |
| `state_deltas` | `payload_json`（7 个 change 数组） |
| `commits` | `validation_json`、`author_approval_json` |
| `quality_reports` | `scores_json`、`issues_json`、`judge_json` |
| `memories` | `source_ids_json` |

walk 规则（`remap_json_column` / `remap_json_value`）：

- **精确命中**：字符串与旧 id 逐字相等才替换（不做子串 / 前缀猜测——id 含随机片段，
  子串替换会误伤正文）；
- **dict 的 key 也替换**：快照里 `world.locations` / `world.factions` / `events` 是
  `{实体 id: {...}}` 形态，只改 value 会让 `scripts/check_state_sync.py` 报 DRIFT；
- **保持 TEXT 形态入库**：解析 → walk → `json.dumps(ensure_ascii=False)` 回写字符串；
  NULL 保持 NULL；空串 / 非法 JSON 原样透传（交给通用列逻辑）；
- 列清单由 `tests/unit/test_backup.py::test_json_id_columns_cover_schema` 对着迁移后的
  真实 schema 看守：新增 `*_json` / `who_knows` 列若忘记登记即红。

### 5.4 projects 行按包内实际列动态写（V3.9 F4）

新项目行原先硬编码 9 个字段，迁移 0023（`word_band_json`）/ 0025（`genre_pack_id`）
等后加列会被丢弃。现在列清单 = 「包内 `project` 行的键 ∩ 目标库
`PRAGMA table_info(projects)` 的真实列名」：目标库缺列（旧库导入新包）与包内多余键
一律丢弃，列名永远取自目标库 schema（包是外部输入，不参与 SQL 拼接）。

### 5.5 FK 校验时序（V3.9 F3）

导入时先 `PRAGMA foreign_keys = OFF`（跨表 INSERT 期间外键天然悬挂），提交前执行
`PRAGMA foreign_key_check`（逐表，覆盖 projects + 全部导出表）：

- **与导入前基线做差集**：只拦「本次导入引入」的违例——库内既有的历史违例不属于
  本次导入的责任，不应阻断恢复；
- 非空 → `ValueError`（消息含 `表.列 (rowid=N) -> 父表` 细节）→ rollback。

注意 SQLite 语义：`PRAGMA foreign_keys = ON` 只影响**其后**的写入，不会回扫已入库行；
「导入末开启 FK + `SELECT 1`」是空转——必须用 `foreign_key_check` 才能发现悬挂引用。

## 6. 导入事务与回滚

- 单 `sqlite3.Connection` + 单事务包裹整次导入；
- 任意步骤抛异常 → `conn.rollback()` → 不残留半导入项目；
- 事务内禁用 FK 检查（避免中间态被 FK 校验阻截），**提交前**用 `PRAGMA foreign_key_check`
  做一次显式校验（差集口径见 §5.5）；
- 返回值：新 `projects` 行 dict（含 `project_id / name / status / created_at` 与包内
  其余实际列）。

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
- 测试：`tests/unit/test_backup.py`

## 9. 测试覆盖

`tests/unit/test_backup.py`：

- `test_export_contains_expected_tables` —— 导出包含全部 23 张表，行数正确。
- `test_export_metadata_has_api_keys_stripped` —— metadata 标记 + 递归扫描包内无 `api_key`。
- `test_export_strips_ai_call_logs_and_evaluations` —— 不含运行时 / 敏感表。
- `test_export_404_like_for_missing_project` —— 不存在项目 → ValueError。
- `test_roundtrip_consistency` —— export → import → 比对内容一致。
- `test_import_remaps_all_ids` —— 导入后无残留旧 project_id。
- `test_import_rejects_bad_format` —— 坏 format → ValueError。
- `test_import_rejects_bad_version` —— 坏 version → ValueError。
- `test_import_rolls_back_on_failure` —— 中途失败 → 整体回滚，无残留。
- `test_import_remaps_branches_self_reference` / `test_import_remaps_state_deltas_self_reference`
  —— 自引用外键第二轮改写。
- `test_export_filters_timeline_events_by_plot_event_project` —— 间接过滤表不混入无关项目。

V3.9 检修新增（F1/F2/F3/F4）：

- `test_json_id_columns_cover_schema` —— 列清单 vs 迁移后真实 schema 的漂移看守。
- `test_roundtrip_remaps_json_embedded_ids` —— 跨引用 fixture（角色↔关系↔事件↔钩子↔
  卷↔两版本快照+delta）：副本 JSON 内嵌 id 全指向新 namespace、无旧 id 残留、
  `check_state_sync` 对副本项目 SYNC OK。
- `test_roundtrip_keeps_volumes_and_chapter_link` —— 卷行与 `chapters.volume_id` 完整。
- `test_import_preserves_projects_late_columns` / `test_import_drops_project_keys_outside_schema`
  —— projects 后加列保留 + 包内多余键丢弃。
- `test_import_rejects_dangling_fk_and_rolls_back` —— 悬空 FK 整体回滚 + 报错含列名。
- `test_import_ignores_pre_existing_fk_violations` —— 库内历史违例不阻断干净包导入。

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
- **JSON 内嵌 id 已重映射（V3.9 F1 起）**：`story_states.snapshot_json` /
  `state_deltas.payload_json` / `plot_events.*_json` / `who_knows` 等列内嵌的实体 id
  会递归替换为新 namespace（含 dict 键），副本项目的内嵌 id 可直接跨库反查。
  边界：只做**精确命中**替换，复合字符串（如 `quality_reports.issues_json` 里
  `<chapter_id>:<scene_id>` 形态的 `location`）只在整串等于 id 时才替换。
- **`branch_snapshots` 不入白名单（V3.9 复核维持）**：该表是分支读路径的**推导缓存**
  （迁移 0009 明确「旧分支无物化行 → 读路径自动回退全量重放」），不含项目原始数据；
  导入副本后按需重建即可，故不导出（导出它反而要额外处理 `branch_id` + 快照内嵌 id）。
- **跨库绑定到不存在的题材包会失败**：`projects.genre_pack_id` → `genre_packs`（全局表，
  不在包内）。导入到没有该 pack 的库时，提交前 FK 校验会拦下并报
  `projects.genre_pack_id -> genre_packs`——先导入题材包（内容仓 `genre-pull`）再导入
  项目，或先解绑。这是 F3「悬挂引用不静默入库」的直接后果，属刻意选择（宁可报错，
  不静默改写用户数据）。
- **V3.9 之前导出的旧包（缺 `volumes` 表）**：旧导出端不导出 `volumes`，但章节行里的
  `chapters.volume_id` 仍在包内；导入到源库之外（源卷行不存在）时会命中提交前 FK 校验
  并报 `chapters.volume_id -> volumes`——这类包本身就是「带悬挂引用的包」，与手工构造的
  坏包同形。可用的恢复路径：在仍有源数据的库上**重新导出**（新导出含 volumes）。
  若要支持「旧包照收、卷链接丢弃」，需在导入端对「引用的卷不在包内」的列显式置 NULL
  并留痕——当前不做（不静默改写用户数据）。