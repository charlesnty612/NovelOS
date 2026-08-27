# core.context_engine（上下文引擎）

> 职责：按角色（Director / Writer / Observer / Critic）组装 LLM 输入上下文——知识权限过滤、相关剧情检索、记忆压缩、token 预算分配。
> 状态：Sprint 4-A 已实现（MVP 简化版）。

## 职责与边界

做：
- `build_director_input(db_path, project_id, chapter_id, author_intent, target_word_count=2200)` — 组装 Director 输入（按 `agent-contracts-v0.md` §3.1）。
- `build_writer_input(db_path, chapter_id, scene_plan, target_word_count=2200)` — 组装 Writer 输入（按 §4.1）。
- `build_observer_input(db_path, chapter_id, min_excerpt_chars_low_confidence=80, max_changes_per_array=50)` — 组装 Observer 输入（按 §5.1）。
- 读取 chapters.plan_json 作为 `director_plan` / `director_plan_summary`。
- 读取 characters + character_states（最新 state_version）+ locations / factions / world_rules + hooks（OPEN/ACTIVE/ESCALATED）+ narrative_debts（open/acknowledged）。
- `recent_prose` 取上一章最新 draft 末尾 500 字。
- `draft_text` 取该章最新 draft 的 content 列。

不做：
- 不直接调用 LLM（属 workflow）。
- 不做 token 预算分配 / 层级压缩（**Deviation**：MVP 简化版按字段全量塞入，完整版留后续 Sprint）。

## 对外接口

```python
from packages.core.context_engine import (
    build_director_input,
    build_writer_input,
    build_observer_input,
    preview_context,  # Sprint 13 下半新增
)
```

返回 dict，可直接 `json.dumps` 后作为 user message 传给 `run_agent`。

## Sprint 13 下半：dry-run 预览（`preview_context`）

为补齐"上下文装配可见化"UX（对标 Sudowrite chiclet / NovelAI Context Viewer 的
"生成时注入了哪些文档+字数"标配能力），`packages.core.context_engine` 新增：

```python
preview_context(
    db_path,
    project_id,
    chapter_id,
    *,
    author_intent: str = "",
    scene_plan: dict | None = None,
    target_word_count: int = 2200,
) -> dict
```

设计要点：

- **只读 / 不调 LLM / 不写库** —— 内部复用三个 builder 的纯装配结果；不触发 `ModelRouter` / `MockProvider` / `run_agent`，因此 ai_call_logs 行数不变。
- **三层结构**（对齐 PRD/agent-contracts 的 L0/L1/L2 概念）：
  - L0 — 项目元数据（chapter / project / knowledge_permissions / constraints）
  - L1 — 业务摘要（character_state_excerpts / world_state_excerpts / plot_graph_excerpt / hook_ledger_excerpt / narrative_debt_excerpt / reference_canon）
  - L2 — 章节专属（author_intent / story_state_snapshot / director_plan / scene_plan / recent_prose / style_constraints / draft_text / director_plan_summary / previous_state）
- **token_estimate** 粗略估算：`max(1, len(json.dumps(value, ensure_ascii=False)) // 4)`（4 字节 ≈ 1 token；中文粗略近似）。MVP 不引入真实分词器；`token_budget` 硬编码 8000。
- **items** 数组：`[{kind, id, name, ...}]` —— kind 覆盖 character / location / faction / world_rule / plot_event / hook / debt / reference_canon / recent_prose / draft_text / author_intent / director_plan / style_constraints / previous_state 等。
- **异常透传**：`ValueError("chapter ... not found")` / `ValueError("project ... not found")`，由 router 转 404。

REST 端点：`GET /api/chapters/{chapter_id}/context-preview`（挂在 `routers/workflows.py`），用于 ChapterDetailPage "AI 上下文明细" 面板（Sprint 13 前端对应组件 `apps/web/src/components/ContextPreviewPanel.tsx`）。

## 默认参数

- `target_word_count=2200`（对齐 PRD §124 番茄单章 2000-2500）。
- `style_constraints` 默认：`{pov: third_limited, dialogue_ratio: 0.4, forbidden_words: ["仿佛", "如同", "本章目标"]}`。
- `knowledge_permissions` 默认：
  - Director / Observer：`your_visibility=["AUTHOR","DIRECTOR"], forbidden_kinds=["HIDDEN"]`
  - Writer：`your_visibility=["WRITER","PUBLIC","VISIBLE"], forbidden_kinds=["HIDDEN"]`

## 依赖

- 上游：`packages/domain/*`、`packages/core/story_state/`、`packages/core/db.py`
- 下游：`packages/workflows/*`

## 使用 / 入口

各 pipeline 在其 AI 节点的 fn 内部调对应 builder，例如 `chapter_plan.pipeline._build_ctx_node`。

## Sprint 14 下半扩展：摘要链 + 前章尾段 + 开放伏笔清单（Director L1）

为补齐长篇一致性装配需求（对标天命/Morpheus/Sudowrite Chapter Continuity 标配），
`build_director_input` 在原有 L1 装配（character / world / hook / debt / canon）之外新增
3 个键，均为 MVP 扩展键（不在 `docs/agents/agent-contracts-v0.md` §3.1 权威契约内；缺字段
容错跳过，无对应行时不加键——但当前实现选择显式给空值以便调用方按空态处理）：

| 键 | 注入位置 | 内容 | 说明 |
| --- | --- | --- | --- |
| `recent_chapter_summaries` | director_input 顶层 | `[{chapter_no, summary, chapter_id}]` | 按 `chapter_no` 倒序取最近 5 章摘要（来自 `chapter_summaries` 表，迁移 `0007_chapter_summaries.sql` 落地）。每条 ≤ 200 字。token 超预算时按"先砍最旧"截断。 |
| `previous_chapter_tail` | director_input 顶层 | `{chapter_no, chapter_id, tail_text}` | 前一章（chapter_no - 1）最新 draft 末尾 300 字原文；无前章 → `{}`。 |
| `open_foreshadow_list` | director_input 顶层 | `[{hook_id, name, status, introduced_chapter_no, importance, overdue, chapters_since_introduced}]` | planted 状态伏笔清单（status ∈ {OPEN, ACTIVE, ESCALATED}）。排序（Sprint 15 / V1.3 SQL 修复后全部下推 SQL）：`overdue_flag DESC → importance DESC → introduced 早的优先 → hook_id ASC`；`LIMIT 20`。**SQL 直接截断**——伏笔 > 20 条时 overdue 项不再被预取 + 内存排序截断丢失。overdue 阈值 **项目级可配**：从 `projects.foreshadow_overdue_chapters` 读取，缺失 / NULL 回退 30。 |

token 预算与截断策略：

- 三组新内容源在 builder 内 **预截断**：摘要链单独按 800 token 上限截断；前章尾段固定 300 字；开放伏笔清单按重要性+ overdue 排序后取 20 条。
- preview（`preview_context`）的 L1 `token_estimate` 把三组新内容源计入；每条以 `kind=chapter_summary / previous_chapter_tail / open_foreshadow` 显示在 items 数组中。
- 缺字段（无 chapter_summaries 行 / 无 planted 状态伏笔 / 无前章）→ 给空列表 / 空 dict；调用方（Director / Writer / Observer Prompt）按空态处理。

数据来源：

- `chapter_summaries` 表（迁移 `0007_chapter_summaries.sql`）——由 `chapter_commit` 工作流的 `summarize` 节点在 commit 成功后写入；commit 失败 / 摘要生成失败时该章可能无摘要行（详见 `packages/workflows/chapter_commit/README.md`）。
- `hooks` 表（已有，PRD §21）——open_foreshadow_list 直接查询。

伏笔 overdue 阈值 **Sprint 15 / V1.3 项目级可配**：从 `projects.foreshadow_overdue_chapters` 读取（迁移 `0008_author_style_samples_and_overdue.sql` 通过 `ALTER TABLE ... ADD COLUMN ... DEFAULT 30` 加列）；列缺失 / NULL → fallback 常量 `_FORESHADOW_OVERDUE_CHAPTERS = 30`。项目创建 / 更新时可通过 `POST/PATCH /api/projects` 设置。

## 维护注意点

- 字段缺失（无 character / 无 location）允许返回空列表 / None；调用方（Director / Writer / Observer Prompt）按空态处理。
- 知识权限过滤（`knowledge-permission-v0.md`）MVP 简化：仅按 visibility 透传；后续 Sprint 加 per-layer 过滤。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 3/4、`docs/agents/agent-contracts-v0.md` §3.1 / §4.1 / §5.1、`docs/architecture/context-engine-v0.md`。

## Sprint 11 下半扩展：Reference Canon 注入（Director）

`build_director_input` 在 `docs/agents/agent-contracts-v0.md` §3.1 之外额外加 2 个键（属于 MVP 扩展键；缺字段容错跳过，无 canon 时不加）：

| 键 | 注入位置 | 说明 |
| --- | --- | --- |
| `reference_canon` | director_input 顶层 | `{canon_id, logline, spine[:20], payoff_list[:30], rhythm}`；取该项目最新 active reference_canon（按 `created_at DESC` 取 1）。来源：`packages/core/api/routers/reference.py` GET 端点返回的 `canon_json`。 |
| `_reference_canon_consumed` | director_input 顶层 | 溯源审计 `{canon_id, consumed_fields: [...]}`；由 chapter_plan pipeline 把 ctx dict 整体写入 `workflow_runs.checkpoint_json`，对齐 `docs/reference-canon/reference-canon-v0.md` §6.3 溯源要求。 |

约束：

- 多参照系加权策略：MVP 暂只取最新 1 条 active canon；多书加权合并（OV-2）defer。
- `status='archived'` 的 canon 不注入（SQL WHERE 过滤）。
- 字段缺失：`logline` / `spine` / `payoff_list` / `rhythm` 任意一项缺失时跳过该项，且 `consumed_fields` 不计该字段名。

## Sprint 15 / V1.3 扩展：作者文风样例注入（Writer）

`build_writer_input` 在 `docs/agents/agent-contracts-v0.md` §4.1 之外额外加 1 个键（MVP 扩展键；无样例时给空 list，调用方按空态处理）：

| 键 | 注入位置 | 说明 |
| --- | --- | --- |
| `author_style_samples` | writer_input 顶层 | `{instruction, samples}`：`instruction` 为常量引导语「以下为作者本人散文样例，请模仿其句式、用词与节奏（非内容）。」；`samples` 为该项目最近创建的 ≤ 2 篇样例，每篇 `[{sample_id, title, excerpt}]`，`excerpt` 截断到 ≤ 1000 字（DB 原字段 ≤ 5000 字，路由器层强制）。 |

数据来源：`author_style_samples` 表（迁移 `0008_author_style_samples_and_overdue.sql`）；REST 端点见 `packages/core/api/routers/author_style_samples.py`（`GET /projects/{pid}/style-samples`、`POST`、`DELETE /projects/{pid}/style-samples/{sid}`）；前端管理面板 `apps/web/src/components/StyleSamplesPanel.tsx`（嵌入项目总览页）。

约束：

- 单篇 `content` ≤ 5000 字；单项目 ≤ 10 篇（均 422 拒绝，路由器层强制）。
- Writer 输入不做 token 预算分配（与现有 MVP 一致）：靠「最近 ≤ 2 篇 + 每篇 ≤ 1000 字」硬截断保证可控；后续若引入逐项预算可在此扩展。
- preview (`preview_context`) 的 L2 `token_estimate` 把 `author_style_samples` 计入；每条以 `kind=author_style_sample` 显示在 items 数组中（前端标签「作者文风样例」）。

## Sprint 15 / V1.3 修复：开放伏笔清单 SQL 截断（V1.2 审查 P2）

旧实现：`ORDER BY importance DESC, created_at ASC LIMIT 60` 预取 → Python 内存 `sort(overdue_first, ...)` → `[:20]`。当伏笔 > 60 条且 overdue 项排在后段时，会被预取 60 条截断边界丢掉（V1.2 审查遗留 P2-1）。

新实现（Sprint 15）：`ORDER BY overdue_flag DESC, importance DESC, introduced_chapter_no ASC, hook_id ASC LIMIT 20` 全部下推 SQL；直接返回 20 条最终结果，伏笔 > 60 条时 overdue 项不再丢失。V1.2 已发布的「预取 + 内存排序」两段式彻底退役。

测试：`tests/unit/test_sprint15_v13.py::test_open_foreshadow_keeps_overdue_when_total_exceeds_cap`（60 条非 overdue + 1 条 overdue → overdue 必现）与 `test_open_foreshadow_sql_order_strict_at_large_volume`（100 条伏笔 → overdue 必现且排第一）。

## Sprint V1.4 扩展：参照系消费可观测（reference_consumption）

V1.4 在 quality 评估链路上加观测能力：评估消费的项目级参照书（`<db 父目录>/references/<project_id>/*.txt`，由
:func:`packages.core.quality.service.load_reference_texts` 读取）的清单 + 字符数被显式记入报告与
checkpoint，便于作者判断"本章是否对齐了哪份爆款参照"。

新接口：

```python
from packages.core.quality.service import capture_reference_consumption
out = capture_reference_consumption(db_path, project_id)
# out == {"source": "project_refs_dir",
#         "files": [{"name": "ref_a.txt", "chars": 1200}, ...],
#         "total_chars": 1234, "files_count": 2}
```

落点（双路径）：

- ``chapter_commit`` 工作流 ``quality_gate`` 节点的 checkpoint_json / 节点返回 dict：
  ``checkpoint_json["quality_gate"].reference_consumption``（enforce 阻断时也随 ctx 落盘）。
- ``quality_reports._meta.reference_consumption``：与 ``chapter_commit`` pipeline 同源（chapter 详情
  页 QualityPanel 可直接从 ``/api/chapters/{cid}/quality`` 读取，无需再回查 runs.checkpoint）。

约束：

- ``project_id`` 不在 ``[A-Za-z0-9_-]`` 白名单内 → 按"无目录"返回 ``files=[]``，避免路径穿越。
- 目录不存在 → ``files=[] / files_count=0 / total_chars=0``；前端按空态处理（不渲染区块）。
- 文件读取异常 → 跳过该文件（log warning 但不阻断）。

与 ``reference_canon`` 的关系：

- ``reference_canon``（Director / chapter_plan 阶段）= ``reference_canons`` 表中的 active canon
  （结构化 logline / spine / payoff_list / rhythm），注入到 ``director_input`` 顶层，供 Director 规划。
- ``reference_consumption``（quality_gate / chapter_commit 阶段）= ``references/<pid>/*.txt`` 项目级
  参照文本清单，注入到 ``quality_reports._meta`` 与 ``checkpoint_json['quality_gate']``，供 REQ-Q6
  风格对齐评估。

两条链路完全独立、各自观测；不互相覆盖。

测试：`tests/integration/test_v1_4_reference_and_revision.py::test_v1_4_reference_consumption_persists_to_checkpoint_and_meta`
（happy path）+ `test_v1_4_reference_consumption_empty_when_no_refs_dir`（无目录空态）+ 
`test_v1_4_evaluate_endpoint_also_emits_reference_consumption`（API 端点同源）。

## V2.0 Wave B 任务二扩展：条件触发动态注入（Codex 3 态）

对标 NovelAI Lorebook 关键词触发 + NovelCrafter Codex 4 态，Context Engine 不再全量注入
canon 实体（characters / locations / factions），而是按"本章相关才注入"：

- 每实体新增两列（迁移 ``0010_trigger_keys.sql`` 落地）：
  - ``aliases``（TEXT，JSON 字符串数组，默认 ``'[]'``）—— 别名命中词面集合
  - ``inject_mode``（TEXT，默认 ``auto``；CHECK in ``auto|always|never``）—— 注入模式
- L0 ``world_rules`` **不参与触发**（PRD 视其为硬设定；常驻完整注入）
- 实体 API 同步扩展：``PATCH /characters/{cid}`` / ``PATCH /locations/{lid}`` /
  ``PATCH /factions/{fid}`` 接受 ``aliases`` / ``inject_mode`` 字段；
  ``GET`` 返回同样字段（向后兼容，旧库升级读不到列 → fallback ``[]`` / ``auto``）

### 三态语义（Codex 3 态）

| `inject_mode` | 命中 plan/前章尾段？ | 注入形态 |
| --- | --- | --- |
| `auto`（默认） | 命中 | 完整 excerpt（含 `core_json` / `current_state` / `data_json` 等） |
| `auto`（默认） | 未命中 | 一行摘要（name + role 或 statement），剥离冗余字段 |
| `always` | 任意 | 完整 excerpt（无视命中；适合主角 / 全局规则类） |
| `never` | 任意 | 不注入；preview 列表保留可见（kind=`suppressed_*`） |

注入策略对 Director (`build_director_input`) 与 Writer (`build_writer_input`) **同源生效**：
- Director 触发扫描面 = 当前章节 `plan_json`（`chapter_goal` / `core_conflict` /
  `turning_point` / `key_beats` / `character_changes_planned` / `notes_for_planner`）
  + 前一章尾段 300 字（`previous_chapter_tail`）
- Writer 触发扫描面 = `director_plan` + `scene_plan` + `recent_prose`（上一章末尾 500 字）

### 命中检测算法

- 对每个 canon 实体，候选词 = `name` ∪ `aliases`；任一词面在 corpus 中
  出现（大小写不敏感子串匹配）即"触发"。
- `aliases` 词面长度 < ``_MIN_ALIAS_LEN = 2`` 跳过（避免「的」「是」等常用词误命中）。
- 中文直接子串匹配；英文自动 lower()。

### 回退策略（保兼容）

- **章节 `plan_json` 为空** → auto 默认全部按 full 注入（README 「无计划文本场景」保兼容）。
- **always / never** 严格按配置执行（即使无 plan 文本也按模式生效；never 永远不注入）。
- **极老库（未跑 0010）**：SELECT 读不到 `aliases` / `inject_mode` 列 → fallback `[]` /
  `auto`；不抛错。

### Preview 标记

`preview_context` 在 L1 items 上展示注入状态（`injection` 字段）：

```jsonc
{
  "kind": "character",
  "id": "char_x",
  "name": "林昭",
  "injection": "summary",         //  full | summary | suppressed
  "summary_line": "林昭（protagonist）"  // 仅 summary 状态
}
```

never 模式另起独立条目 `kind=suppressed_character` / `suppressed_location` /
`suppressed_faction`，便于面板区分"已剔除"与"未命中降级"两类。前端
`apps/web/src/components/ContextPreviewPanel.tsx` 用徽标「摘要 / 已剔除」+ 颜色
（橙 / 灰）展示。

### 体积与相关性提升

- **相关性强**：always / auto+命中 实体全量注入，未相关实体降级为一行摘要或剔除，
  避免把不相关角色的全套背景拖入 prompt。
- **prompt 体积下降**：20 个 auto 实体 + 完整 `core_json` 场景下，全部未命中时
  `character_state_excerpts` 体积降至全注入的 < 80%（测试断言）。

### 维护注意点

- 字段缺失（无 character / 无 plan_json）允许返回空列表 / None；调用方（Director / Writer
  / Observer Prompt）按空态处理。
- 知识权限过滤（`knowledge-permission-v0.md`）MVP 简化：仅按 visibility 透传；后续 Sprint
  加 per-layer 过滤。
- 测试：`tests/unit/test_v2_wave_b_trigger_keys.py`（35 用例；覆盖 3 态矩阵 / 别名命中 /
  回退 / 体积下降 / 预览标记 / Service 读写）。

## V2.0 Wave C 任务一：召回混合层（FTS5）

对标社区"状态库定事实 + 检索召回供呼应"的混合方案：

- 状态库（story_state）定事实，避免 LLM 幻觉；
- 全文检索（FTS5）召回历史正文片段，让 writer/director 看到具体叙事语境。

详见 [`packages/core/retrieval/README.md`](../retrieval/README.md)。

装配集成：

- `build_director_input` / `build_writer_input` 顶层新增 `recalled_passages` 键：
  ```python
  [
    {"chapter_id": "ch_xxx", "chapter_no": 3, "snippet": "林轩握碎古镜外层封印...", "rank": -2.5},
    ...
  ]
  ```
  按当前章节计划文本（`chapter_goal` / `core_conflict` / `key_beats` /
  `character_changes_planned`）从 `chapter_fts` 召回 top-3 片段，每段截断
  ≤300 字。无索引 / 无命中 → 空 list（不阻断装配）。
- `preview_context` L1 增加 `recalled_passage` 新 kind；前端
  `ContextPreviewPanel` `KIND_LABEL` 已补"召回片段"。
- chapter_commit 流程：commit 成功后由 `_commit_node` 钩子调
  `packages.core.retrieval.upsert_chapter`（失败按 summarize 节点相同语义
  降级 log warning，不阻断 commit）。

## V2.0 Wave C 任务二：装配缓存（L0/L1）

`build_director_input` / `build_writer_input` 的 L0/L1 装配结果按
`(project_id, state_version, chapter_no, role, content_fp)` 做进程内缓存：

- 键必须含 `state_version` —— state 推进后自然失效；
- `role ∈ {"director", "writer"}` —— L2 (observer) 不缓存（commit 期间持续变化）；
- **V2.0 Wave C P1-1 修复**：第 5 元 `content_fp` = 内容指纹
  - `director`：`sha256(chapters.plan_json 原文)[:16]`（`NULL → 'none'`）
  - `writer`：`sha256(json.dumps(scene_plan, sort_keys=True))[:16]`（`None → 'none'`）
  - 不可序列化 → `'uncached'` → 跳过缓存（直接走 uncached），
    避免脏命中场景下缓存可用性反而下降。
  - **解决脏命中**：plan_json 被 UPDATE 但 state_version 不变时，键自然失效；
    writer 传不同 scene_plan 时，键也自然失效。
- 线程安全：`threading.Lock` 保护 dict 读写；
- 上限 256 条（dict 插入顺序淘汰最旧）；
- 仅缓存纯装配 dict，不缓存连接 / 副作用对象；
- 显式失效接口：`_invalidate_cache_for_chapter(project_id, chapter_no)` 用于
  commit 完成后兜底失效（state_version 推进通常已带走它；P1-1 修复后此兜底主要
  覆盖"plan_json 被 UPDATE 但 state_version 未变"的边界场景）。
- 失效调用点：`packages/workflows/chapter_commit/pipeline.py:_commit_node` 末尾
  commit 成功后显式调 `_invalidate_cache_for_chapter(project_id, chapter_no)`
  （任一异常吞掉——失效失败不阻断 commit）。

测试（`tests/unit/test_v2_wave_c_retrieval.py`）：
- 连续两次装配第二次命中缓存（`p1 is p2`）；
- state_version 变化后重装（`p1 is not p2`）；
- 多线程并发读同一键最终命中缓存；
- **P1-1 新增**：`test_director_cache_does_not_hit_stale_plan_json`（plan_json
  UPDATE 后必须返回新对象）、`test_writer_cache_distinguishes_scene_plan`
  （同 chapter 不同 scene_plan 返回不同对象）、
  `test_chapter_commit_invalidate_clears_cache` /
  `test_chapter_commit_node_invalidates_cache_in_pipeline`（commit 后显式失效）。

## P2 Context Engine 补全（Context Engine 缺口关闭）

对齐 `docs/impl/IMPLEMENTATION-PLAN-v0.md` §4.2 登记的 Context Engine MVP 缺口：

### 1. `plot_graph_excerpt.unresolved_branches` 不再恒空

`build_director_input` 现在从 `branches` 表查询 `status = 'ACTIVE'` 的分支作为未解决分支。

- ACTIVE = 尚未 MERGED / DISCARDED / ARCHIVED 的活跃分支；
- 注入字段：`{branch_id, name, parent_branch_id, base_state_version, status}`；
- `preview_context` L1 新增 `kind="unresolved_branch"` 展示。

### 2. `world_state_excerpts.sensory_anchors` 感官锚点

Writer 契约要求 `world_state_excerpts` 必须含 `sensory_anchors[]`。当前 schema
（`0001_init.sql`）`locations` 表无专用感官列，因此采用**零 DDL 兼容方案**：

- 优先读取 `locations.data_json.sensory_anchors`（标准扩展点，未来可在此字段维护
  结构化感官数据，无需改 schema）；
- 其次从 `data_json` 常见感官字段
  (`sensory_details / atmosphere / smell / sound / light / texture / temperature`)
  组合成 `anchor_text`；
- 最后回退到 `statement` 一句话陈述；
- 无数据 → 空 list，不抛错。

注入格式：

```json
[
  {"location_id": "loc_xxx", "location_name": "古庙", "anchor_text": "腐朽的檀香"},
  {"location_id": "loc_xxx", "location_name": "古庙", "sense": "smell", "anchor_text": "腐朽的檀香"}
]
```

`preview_context` L1 新增 `kind="sensory_anchor"` 展示。

### 3. 章节级相关性裁剪（Relevance Trim）

`build_writer_input` 新增 `relevance_trim` 参数：

```python
build_writer_input(
    db_path, chapter_id, scene_plan,
    relevance_trim=True,   # None 时读环境变量；默认开启
)
```

开关方式（优先级从高到低）：

1. 显式传参 `relevance_trim=True/False`；
2. 环境变量 `NOVELOS_CONTEXT_RELEVANCE=off` 关闭，其他值开启；
3. 默认开启。

裁剪逻辑（纯函数 `_apply_relevance_trim`）：

- 扫描面 = 当前章 `plan_json.key_beats[].involved_characters / involved_locations` +
  `plan_json.character_changes_planned[].name|character_id` +
  `scene_plan.characters / location / beats[].involved_*`；
- **始终完整保留**：主角（`role='protagonist'`）与 `inject_mode='always'` 的实体；
- **未涉及实体**（id 或 name 未命中 involved 集合且非核心）降级为
  `{id, name, relevance_summary: True}`，不直接剔除，保留可识别信息；
- 仅作用于 `build_writer_input`（Director 需全局视角规划，不裁剪）；
- 分页模式（`context_mode='paged'`）同样受 relevance_trim 影响：先 relevance_trim
  再分页裁剪，两者可叠加；
- 缓存键追加第 7 元 `relevance`（`"on"` / `"off"`），防止开关/环境变量变化导致脏命中。

观测字段：

- `_relevance_trim_enabled: bool`
- `_relevance_trim_stats: {characters_full, characters_summary, locations_full,
  locations_summary, factions_full, factions_summary, involved_characters[],
  involved_locations[]}`

### 测试

新增 `tests/unit/test_context_engine_p2.py`（13 用例）覆盖：

- `unresolved_branches` 查询与 preview 展示；
- `sensory_anchors` 解析、零 DDL 回退、preview 展示；
- `relevance_trim` 环境变量/显式参数开关、主角/always 保留、involved 命中保留、
  未涉及实体降级、纯函数行为；
- `test_writer_input_paged.py` 模块级设置 `NOVELOS_CONTEXT_RELEVANCE=off`，
  以保持对 paged 模式裁剪的独立观测。