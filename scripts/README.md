# scripts（运维脚本）

> 职责：NovelOS 后端的本地运维 / 工具 CLI（现行 25 个脚本：迁移、开发服务器、内容同步、备份维护、评估回归、schema 导出、长跑与 smoke、卷纲体检与开卷、章节量产等），均可通过 `python scripts/<name>.py` 直接运行（脚本内部自动 `sys.path.insert(0, REPO_ROOT)`）。
> 状态：现行入口 25 个（`ls scripts/*.py`）；migrate.py / serve.py 为最小起点，其余见本文件后续分节。

## 职责与边界

**做**：
- 提供「仅跑迁移、不启服务」的开发流程入口
- 提供「启动 uvicorn 服务」的开发流程入口
- 统一从 `packages.core.config.get_settings()` 读取配置，保证 CLI 与 API 行为一致

**不做**：
- 不实现生产部署脚本（Phase B Tauri 壳已于 2026-08-23 由用户裁决关闭，Web 版交付即目标达成）
- 不实现数据库备份 / 恢复（已实现于 `packages/core/backup/` + `/api/projects/{pid}/backup` 端点，不在 `scripts/` 重复）

## 对外接口

| 脚本 | 来源 | 入口函数 | 用途 |
|---|---|---|---|
| `scripts/migrate.py` | `scripts/migrate.py:16` | `main() -> int` | 执行 SQLite 迁移并打印 `applied / skipped / tables / db` |
| `scripts/serve.py` | `scripts/serve.py:31` | `main() -> int` | 启动 uvicorn，host/port 取自 `Settings` |

### 脚本总览（现行 25 个）

| 类别 | 脚本 |
|---|---|
| 迁移 / 服务 | `migrate.py`、`serve.py` |
| 内容仓同步 | `content_sync.py`（见下文专节） |
| 库维护 / 一次性迁移 | `db_maintenance.py`（workflow_runs 清理 / prune-logs / vacuum）、`migrate_m1_run_to_main.py` |
| 评估 / 长跑探针 | `eval_regression.py`、`m1_long_run.py`、`m1_report.py`、`m2_judge.py`、`real_llm_e2e.py` |
| Schema / 前端类型 | `export_openapi.py`、`gen_frontend_types.py` |
| 端到端 / 冒烟 | `smoke_e2e.py`、`smoke_ch063_split.py` |
| 一致性核查 | `check_state_sync.py` |
| 卡死 run 巡检 | `inspect_stalled_runs.py`（PAUSED/RUNNING/PENDING 逐条 + 审批实质内容摘要，只读；`--apply` 故意不实现，见下文专节） |
| 卷纲 / 开卷 / 世界切换 | `outline_check.py`（卷纲体检，见其模块 docstring）、`open_volume.py`（见下文专节）、`world_switch.py`（快穿换位面时按既有机制封存上一世作用域，默认 dry-run） |
| 正文规整 / 算子校准 / 可读性回放 | `normalize_chapters.py`（引号漂移 + 内部标识体检，默认 dry-run）、`ai_tone_calibrate.py`（AI 味算子校准 + 精确率抽样）、`readability_audit.py`（长段 / 对话占比 / 接词回声三指标只读回放 + 锚点书人类基线对照） |
| delta 源锚定阈值校准 | `delta_anchoring_calibrate.py`（只读 `mode=ro` 打开；实测 `DELTA_BIGRAM_COVERAGE_MIN` 的 bigram 覆盖率分布 + ≤20 条低覆盖样本供人工判别「真捏造」还是「正常概括」；不写库） |
| revise 保真阈值校准 | `revise_fidelity_calibrate.py`（只读 `mode=ro` 打开；从 `workflow_runs.checkpoint_json` 还原历史 revise 轮次实测 `REVISE_MIN_PRESERVED_RATIO` 的 `preserved_ratio` 分布，另出相邻 drafts 版本对照与 ≤20 条低比值抽样；不写库） |
| 章节量产 | `produce_chapters.py`（见下文专节） |

### `scripts/migrate.py` 输出格式

```
applied=<N> skipped=<N> tables=<int> db=<path>
newly applied:
  - 0001_init.sql
```

### `scripts/serve.py` 行为

- 调用 `uvicorn.run("packages.core.api.main:app", host=settings.api_host, port=settings.api_port, reload=False, log_level=settings.log_level.lower())`
- 默认绑定 `127.0.0.1:18081`（`Settings.api_port` 默认值；优先级 `NOVELOS_PORT` > `NOVELOS_API_PORT` > 18081），host 可用 `NOVELOS_API_HOST` 覆盖

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
- **权威文档**：`docs/impl/IMPLEMENTATION-PLAN-v0.md`（历史计划）§2 Sprint 0 DoD（`uvicorn` 启动 `/health` 200、迁移后 38 业务表）。

---

## `scripts/content_sync.py` —— 内容仓同步器 CLI

把运行库（SQLite `data/novelos.db`）中某本书（project）的产出，按「每本书一个独立子目录」同步到内容仓（默认 `D:/zcodeproject/NovelOS-Content`），实现软件 / 内容两仓分离下的内容版本管理闭环。

> 仅写软件仓代码与脚本；不修改内容仓已有历史文件，仅新建 `<content-dir>/<book-slug>/` 子目录。

### 用法

```bash
# 列出内容仓下所有 book 子目录（slug / project_id / synced_at / name）
python scripts/content_sync.py --content-dir D:/zcodeproject/NovelOS-Content list

# 仅建书目录骨架 + manifest，不写其它产物（用于先验位置 / slug）
python scripts/content_sync.py \
    --db data/novelos.db \
    --content-dir D:/zcodeproject/NovelOS-Content \
    init --project prj_xxxxxxxx

# 全量同步一本书到内容仓；幂等可重跑
python scripts/content_sync.py \
    --db data/novelos.db \
    --content-dir D:/zcodeproject/NovelOS-Content \
    push --project prj_xxxxxxxx
```

### 目录约定

```
<content-dir>/<book-slug>/
    novel/
        全书-<slug>.txt                # 整书正文（复用 packages.core.exporter.build_txt kind=book）
        第<N>卷-<title>.txt            # 按卷合并（若该书有 volumes）
        第<N>章-<title>.txt            # 每章正文（复用 build_txt kind=chapter）
    canon/
        characters.json
        world_rules.json
        plot_events.json
        timeline_events.json
        volumes.json
    story_state/
        <state_version>.json           # 每版本 story_states 快照（按版本号去重）
    backup/
        <slug>-backup-<YYYYmmdd-HHMMSS>.json  # 复用 packages.core.backup.BackupService.export_project
    manifest.json
```

### `book-slug` 派生规则（确定性）

1. `projects.name` 做 NFKD 归一化 + lowercase + 仅保留 `[a-z0-9-]`；
2. 连续 `-` 折叠、首尾 `-` 删除；
3. 结果为空（纯中文 / 纯特殊字符）→ fallback 到 `book-<project_id 末 8 字符>`；
4. 同一 project_id 的 manifest 已存在 → 直接复用其所在目录（保证 push 重跑路径稳定）；
5. 与内容仓其它 book 子目录冲突 → 追加 `-2` / `-3` 后缀。

### 幂等性

| 产物 | 行为 |
|---|---|
| `novel/*.txt` | 每次 push 覆盖（章节正文可能变化） |
| `canon/*.json` | 每次 push 覆盖 |
| `story_state/<version>.json` | 同版本已存在则跳过（按 state_version 命名天然去重） |
| `backup/<slug>-backup-<ts>.json` | 每次 push 新建（带时间戳，保留历史快照） |
| `manifest.json` | 每次 push 覆盖 |

### 退出码

- `0`：全部产物写入成功，`errors` 为空；
- `1`：参数错误 / project 不存在 / 数据库读不开 / 全失败；
- `2`：部分产物写入失败（`errors` 非空但已写入部分产物）。

### 依赖

- 上游：`packages.core.content_sync`、`packages.core.exporter.builder.build_txt`、`packages.core.backup.BackupService.export_project`、`packages.core.ids.new_id`；
- 外部库：无新增；
- 标准库：`argparse` / `json` / `pathlib` / `datetime`。
---

## `scripts/open_volume.py` —— 开新卷（卷 N）策展入口

把一份**卷 brief**（卷头 + 该卷各章的策展大纲）校验后落库，用于「第一卷已写完、
要开第二卷」的场景。**只有 `project-init` 工作流能开卷，而它的卷节点把卷号写死为 1
（重跑 init 会覆盖卷 1）**——本脚本是不经工作流的独立入口：不调用任何工作流、
不写 `workflow_runs`，只写 `volumes` / `chapters` 两张表。

### 用法

```bash
# 体检（dry-run，默认；不写库）
python scripts/open_volume.py --project prj_xxxx --brief vol2.json

# 落库
python scripts/open_volume.py --project prj_xxxx --brief vol2.json --apply

# 机器可读摘要（stdout 仅 JSON，供主会话消费）
python scripts/open_volume.py --project prj_xxxx --brief vol2.json --apply --json

# 指定数据库 / 允许本工具封存卷号更小的既有 active 卷
python scripts/open_volume.py --db data/novelos.db --project prj_xxxx \
    --brief vol2.json --apply --seal-active
```

### brief JSON 契约

```json
{
  "volume": {"number": 2, "title": "…", "arc_summary": "…"},
  "chapters": [
    {"number": 25, "title": "…",
     "outline": {"chapter_goal": "…", "core_conflict": "…", "turning_point": "…",
                 "expected_role": "setup|escalation|transition|climax|resolution",
                 "key_beats": ["…", "…"],
                 "character_changes_planned": ["…"],
                 "information_releases": ["…"],
                 "expected_word_count": 2500}}
  ]
}
```

- `volume.number` 必填（int ≥ 1）；`title` / `arc_summary` 可选（空串 = 不写）。
- `chapters` 非空，按**升序连续**给号（25, 26, 27 …，不允许跳号 / 乱序 / 重复）。
- `outline` 的 8 个字段**全部必填**：三个叙述字段非空字符串、`expected_role` 取枚举
  （`setup / escalation / transition / climax / resolution / other`，另收 `turn`）、
  `key_beats` 非空字符串数组、两个列表字段为字符串数组（可空数组）、
  `expected_word_count` 为正整数。额外字段（`hook_handling` 等）原样写入、不丢。
- 字段名与 `outline_check` 的判据**同名**（判据读 `chapter_goal / key_beats /
  expected_role / hook_handling`），无改名映射。

### 校验（dry-run 与 `--apply` 都跑；有 error 则 `--apply` 拒写）

| 码 | 判据 |
|---|---|
| `BRIEF-*` | 顶层结构：`volume` 为对象、`chapters` 非空数组、字段类型合法 |
| `VOLUME-NUMBER` | 卷号为 int ≥ 1 |
| `VOLUME-SEALED` | 同号卷已存在且 `sealed`（终态归档）→ 拒写 |
| `VOLUME-ACTIVE` | 项目已有**别的** active 卷 → 拒写；`--seal-active` 可让本工具封存卷号更小的那个（不可逆） |
| `VOLUME-NUMBER-SKIP`（warning） | 卷号跳跃 / 补洞 |
| `CHAPTER-DUP` / `CHAPTER-ORDER` / `CHAPTER-GAP` | brief 内章号重复 / 未升序 / 不连续 |
| `CHAPTER-CONFLICT` | 该章号已存在且**属于别的卷** → 拒写 |
| `CHAPTER-TITLE-DIFF`（warning） | 既有章标题与 brief 不一致——只提示不改写 |
| `OUTLINE-*` | 8 个必填字段齐全 / `expected_role` 合法 / `key_beats` 非空 |

另外落库前用 `outline_check` 的**同源判据**（`packages/core/quality/outline_check.py`
的 `check_outline`，直接 import）对 brief 做质量体检并打印命中证据——
**报告器不是闸门：alert 只提示，不阻断落库**（命中项按位置编号，报告里给出
`ch1=#25 …` 的映射行）。

### 写入路径（全部走既有 domain service）

| 动作 | 路径 |
|---|---|
| 封存旧 active 卷（仅 `--seal-active`） | `VolumeService.seal()` |
| 建卷 / 改卷标题 | `VolumeService.create()` / `VolumeService.update()` |
| 建章 | `ChapterService.create()` |
| 写策展大纲 `chapters.outline_json` | `ChapterService.update(ChapterUpdate(outline_json=…))` |
| 挂章到卷 | `VolumeService.assign_chapter()`（= `chapters.volume_id`） |
| 写 `volumes.arc_summary` | **唯一一处定点直写 SQL**：该列无任何 service / 路由入口（`VolumeCreate` / `VolumeUpdate` 都不含），不直写就等于「收下即忘」 |

`chapters.plan_json`（生成面）本脚本**不写**——它由 chapter-plan 工作流整体覆盖，
塞半成品只会复发「一列两用」（见 AGENTS.md 硬规则 9 与迁移 0028）。

### 幂等

- 同 number 的既有卷 → 复用（不重复建卷）；标题不一致时经 service 改标题并打印 diff。
- 同 number 的既有章 → 复用（不重复建章）：已挂本卷、或 `volume_id` 为 NULL
  （上次运行半途中断的残留）时补挂。
- 既有章**只在 `outline_json` 与 brief 不一致时**才 UPDATE，并打印逐字段 diff 摘要。
- 标题不一致只告警不改写（改标题走 `PATCH /api/chapters/{id}`）。

### 退出码

- `0`：校验通过（dry-run）/ 写入完成；
- `1`：校验不通过 → **拒写**（含 VOLUME-SEALED / VOLUME-ACTIVE / CHAPTER-CONFLICT / OUTLINE-* 等）；
- `2`：参数错误 / brief 读不出或不是合法 JSON / 数据库打不开 / 写入中断。

### 依赖

- 上游：`packages.domain.volume`（VolumeService）、`packages.domain.chapter`（ChapterService）、
  `packages.core.quality.outline_check.check_outline`、`packages.core.db.get_connection`；
- 外部库：无新增；标准库：`argparse` / `json` / `sqlite3` / `pathlib`；
- 测试：`tests/unit/test_open_volume.py`（校验拒写 / 落库逐字段 / 幂等 / 体检不阻断）。

---

## `scripts/produce_chapters.py` —— 章节量产入口（plan → write → review → commit）

把一批章节推到 `COMMITTED` 的一等入口，取代此前每本书手写的一次性 HTTP 驱动
（软件仓外的 `_refs/arc1_driver.py` … `_refs/arc6_driver.py` 那批）。
**只走 HTTP，不直连 SQLite**（事实源在服务端库；客户端另开连接只会制造第二写者），
因此没有 `--db` 参数。

### 用法

```bash
# 体检（默认模式，不发起任何 run）
python scripts/produce_chapters.py --project prj_xxxx --chapters 1-3,7

# 生产（已 COMMITTED 的章自动跳过）
python scripts/produce_chapters.py --project prj_xxxx --chapters 1-3,7 --apply

# 机器可读摘要（stdout 仅 JSON，人类可读日志走 stderr）
python scripts/produce_chapters.py --project prj_xxxx --apply --json

# 显式给作者意图 / 目标字数 / 门禁模式
python scripts/produce_chapters.py --project prj_xxxx --apply \
    --author-intent "单章可见字数 2200~2800" --target-word-count 2500 \
    --quality-gate-mode enforce
```

选项：`--project`（必填）、`--chapters 1-3,7`（`--only` 为同语法旧名，二者同给报错）、
`--base-url`（缺省由 `get_settings()` 的 `api_host`/`api_port` 拼出）、`--dry-run`（默认）/
`--apply`、`--json`、`--author-intent`、`--target-word-count`、`--max-revise-rounds`（默认 2）、
`--max-auto-approve`（默认 3）、`--repetition-threshold`（默认 0.08）、`--stage-timeout`、
`--start-timeout`、`--poll-interval`、`--http-timeout`、`--quality-gate-mode`、
`--mock-providers`（离线 / 测试用）。

### 逐章流程与按状态续跑

| 章节当前状态 | 本次执行 |
|---|---|
| `PLANNED` | plan → write → review → commit |
| `DRAFTED` | review → commit（已有草稿，不重复烧 token 重写） |
| `REVIEWED` | commit |
| `COMMITTED` / `RELEASED` | 跳过（`skipped` 字段留痕） |

### 四道守卫（每条都有实机事故来源）

1. **409 容忍启动**：章节行在 workflow 内部先翻状态、run 行后落终态，窗口期内 start 会被
   `already has an active workflow run` 拒收 → 有界退避重试；**只有这条已知竞态**可重试，
   其它 409（如「最新草稿晚于最近一次审校完成时间」的时序守卫）与 4xx 硬错**立即硬停**。
2. **commit 前清场**：commit 之前先轮询到该章没有 `RUNNING`/`PENDING` run（`--start-timeout` 上限），
   超时即失败——不带着竞态发 commit。
3. **报告驱动决议**（`decide_review`）：无 error ⇒ 批准；errors 全部落在可定向改稿规则集合
   （`REVISABLE_RULE_IDS`：字数带 + 确定性算子）⇒ 驳回并附**定向**改稿意见（字数意见双向）；
   未知 rule_id / 缺 rule_id / 不可定向改的规则 ⇒ **硬停报告**，不猜、不盲目重写。
   **占比 / 频率类指标（如 `AI-DIALOGUE-LOW`）一律不在集合内**（F-19：可优化指标不作处方）。
4. **有界风控门自动批准**：commit 阶段 PAUSE 在 `chapter-commit.high_risk_approval` 时，
   最多自动批准 `--max-auto-approve` 轮，每轮把 pause_payload 摘要（stage / delta_id /
   changes 计数 / HTTP 码）记进摘要的 `auto_approvals`（可审计）；轮次用尽交回人工。
   **未知人工节点与作者决议节点（`chapter-review`）绝不自动批准**。

### 不静默批准坏章（本脚本存在的理由）

批准之前，脚本直接从**最新草稿正文**算章内重复率（13 字 shingle，重复 shingle 数 / 总数，
去空白归一化——与 `packages/core/quality/ai_trace.py:intra_chapter_repetition` 同口径，
但**本地实现**，不被该包的并发改动牵着走），超过 `--repetition-threshold`（默认 8%）
**拒绝批准**并把比值写进失败报告（`failure.reason = "repetition_exceeds_threshold"`）。

### 改稿回路（子 run 的发现方式）

驳回改稿后，服务端 daemon 回路自己跑 write → review，**子 run 不在 resume 响应里**：
驱动轮询 `GET /projects/{pid}/runs`，找**更新的、PAUSED 的 chapter-review run**（不看
chapter.status——改稿轮里章状态停在 `DRAFTED` 不翻转，盯它只会傻等超时），读它的报告续判。
轮次上限 `--max-revise-rounds`。

### confirm 档门禁（前向兼容）

质量门禁的 `confirm` 档（`quality.issues.CONFIRM_RULES` 的 `AI-BEAT-REPEAT`，以及
`scoring` 按量级单条上修的 `RULE_STYLE_REPETITION_TRIGRAM` > 0.25 一档——「不许静默通过、
必须显式接受并写明理由」）命中时，commit 会
以 `quality gate blocked: [...] | gate=confirm | ...` 失败。驱动按硬停处理
（`is_confirm_tier_failure`）：**不重试、不代填 `gate_override`**，把 rule_id 与证据原样
报回摘要（`failure.reason = "gate_confirm_tier"`、`needs_gate_override = true`）。
「接受」是作者的判断，不是驱动的判断。

### `--json` 摘要（每章）

`status`（initial/final）、`stages_run`、`word_count` / `target_word_count`、
`review_error_count` / `review_errors` / `review_error_rule_ids` / `review_warning_count`、
`repetition_ratio`、`draft_version`、`revise_rounds`、`auto_approve_rounds` / `auto_approvals`、
`commit_retries`、`elapsed_s`、`failure{reason, stage, run_id, detail, rule_ids}`；
顶层含 `mode` / `base_url` / `project_id` / `counts{selected,produced,skipped,failed,not_attempted}`
与 `elapsed_s`。

### 退出码

- `0`：全部完成（含已 `COMMITTED` 被跳过）；
- `1`：参数 / 配置错误（选择表达式非法、`--chapters` 与 `--only` 同给、阈值越界、
  mock 文件读不出、API 不可达、project 不存在、选中的章号不在项目里）；
- `2`：某章失败、整轮停止（失败章之后的章**不再尝试**，`counts.not_attempted` 记账）。

### 已知边界（不做的部分）

- 不自动填 `gate_override`（见上）；不重产已 `COMMITTED` 的章（需先回退章状态，另属一件事）；
- 不建章、不写策展大纲、不改标题（分别走 `open_volume.py` / `PATCH /api/chapters/{id}`）；
- 不清理孤儿 run（停驱动留下的 `RUNNING` 行会让本脚本在「等清场」处超时失败并如实报出）。

### 依赖

- 上游：`packages.core.config.get_settings`（端口 / host 单一来源）；
- 外部库：httpx（仓库既有依赖）；标准库：`argparse` / `json` / `re` / `time` / `dataclasses` / `pathlib`；
- 测试：`tests/unit/test_produce_chapters.py`（选择解析 / 重复率口径 / 评审决议 / confirm 前向兼容 /
  落定循环的假 API）+ `tests/integration/test_produce_chapters_e2e.py`（临时库 + 独立端口 + mock
  providers 三场景：干净章推到 `COMMITTED`；重复文本章被拒绝批准 exit 2；窄带章触发驳回改稿 →
  轮询发现改稿子 run → 轮次耗尽即停 exit 2）。

---

## `scripts/inspect_stalled_runs.py` —— 卡死 run 只读巡检

列全部 `PAUSED` / `RUNNING` / `PENDING` 的 workflow run，并对每个 run 解析
`checkpoint_json`，报告它在人工审批节点上**压着什么实质内容**——这是主会话判断
「能不能安全清理」的唯一依据。**默认只读，不实现任何取消 / 删除。**

### 用法

```bash
# Markdown 报告（默认库 data/novelos.db）
python scripts/inspect_stalled_runs.py

# 指定库 / 机器可读
python scripts/inspect_stalled_runs.py --db data/novelos.db --json

# 完整留档（含完整 checkpoint_json）——落盘位置由调用方定
python scripts/inspect_stalled_runs.py --export <path>/stalled_runs_<date>.json
```

### 三个容易搞错的结论（工具按代码核对，不按口头说法）

| 说法 | 实测真相 | 依据 |
|---|---|---|
| 「同章有活动 run ⇒ 重新发起写章 409」 | **不成立**。守卫只判 `RUNNING`/`PENDING`，**PAUSED 不拦新 start** | `routers/workflows/control.py:52` `_check_active_run_for_chapter`（SQL 谓词见 line 78 / 89）；DB 侧同谓词部分唯一索引 `database/migrations/0017_unique_constraints.sql:46-48`（其 line 42-45 注释写明「不覆盖 PAUSED」是刻意设计） |
| 「PAUSED run 可以直接 cancel 掉」 | **不成立**。cancel 端点只接受 `RUNNING`，PAUSED 返 **409** | `routers/workflows/control.py:780-781`；`workflow_runtime/engine.py:644-646` 抛 `ValueError(... "must be RUNNING to cancel")` |
| 「PAUSED run 就是空的」 | **不成立**。dev 库 16 条 PAUSED **全部**带实质内容（review_report 非空 + critic issues） | 工具第 2 节「逐 run 待审批实质内容」逐条给出字数 / error rule_id / 评审意见摘录 |

### 报告三节

1. **概览**——run_id / 章 / 状态 / 节点 / **停留时长** / retry / error / 有无实质内容 / 是否阻止新 start；
2. **逐 run 待审批实质内容**——`__pause_payload__` 解析结果：被审字数 vs 目标与字数带、
   error / warning 的 rule_id 清单、AI 模式命中、题材核销、critic issues 条数、
   **评审意见摘录原文**；空报告与未识别 payload 分别显式标出（不静默归成「无内容」）；
3. **对「重新发起写章 409」的影响**——按上面第 1 行那条真实谓词算，逐章给出是否真的会被拦。

### 已知边界

- `workflow_runs` **无 `project_id` / `created_at` 列**；`chapter_id IS NULL` 的
  project-init run JOIN 不出项目名，工具回落到 `checkpoint_json.brief.title` 还原，
  并在报告里打 `*` 标记（该依据未经 `projects` 表核对，与 JOIN 出来的不是一回事）；
- 停留时长以 `started_at` 为准，`ended_at IS NULL` 是活动 run 的常态；
- **`--apply` 故意不实现**：`apply_cleanup()` 显式抛 `NotImplementedError`、CLI 返 **3**。
  取消是终态操作，由主会话依据本报告另行决策并留痕——留一个空实现或 `pass` 只会让
  将来加的 `UPDATE` 静默生效。

### 退出码

- `0`：巡检完成（无论是否发现活动 run）；`2`：参数错误 / DB 不存在 / 非法 SQLite；
- `3`：带了 `--apply`（显式拒绝，stderr 打 `REFUSED:`）。

### 依赖

- 上游：无（纯标准库，不 import `packages.*`，故不随业务代码漂移）；
- 标准库：`argparse` / `json` / `sqlite3` / `pathlib` / `dataclasses` / `datetime`；
- 测试：`tests/unit/test_inspect_stalled_runs.py`（59 例：只读拒写 / 审批内容三形态 /
  守卫口径形状 / 导出留档 / apply 闸；全部夹具在 `tmp_path`，不碰 `data/novelos.db`）。
