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
| `open_foreshadow_list` | director_input 顶层 | `[{hook_id, name, status, introduced_chapter_no, importance, overdue, chapters_since_introduced}]` | planted 状态伏笔清单（status ∈ {OPEN, ACTIVE, ESCALATED}）。排序：overdue 优先 → importance DESC → introduced 早的优先；最多 20 条。overdue 计算：(current_chapter_no − introduced_chapter_no) > 30 即视为逾期。 |

token 预算与截断策略：

- 三组新内容源在 builder 内 **预截断**：摘要链单独按 800 token 上限截断；前章尾段固定 300 字；开放伏笔清单按重要性+ overdue 排序后取 20 条。
- preview（`preview_context`）的 L1 `token_estimate` 把三组新内容源计入；每条以 `kind=chapter_summary / previous_chapter_tail / open_foreshadow` 显示在 items 数组中。
- 缺字段（无 chapter_summaries 行 / 无 planted 状态伏笔 / 无前章）→ 给空列表 / 空 dict；调用方（Director / Writer / Observer Prompt）按空态处理。

数据来源：

- `chapter_summaries` 表（迁移 `0007_chapter_summaries.sql`）——由 `chapter_commit` 工作流的 `summarize` 节点在 commit 成功后写入；commit 失败 / 摘要生成失败时该章可能无摘要行（详见 `packages/workflows/chapter_commit/README.md`）。
- `hooks` 表（已有，PRD §21）——open_foreshadow_list 直接查询。

伏笔 overdue 阈值为常量 `_FORESHADOW_OVERDUE_CHAPTERS = 30`；后续若需项目可配，由 `project_settings` 表 + 读取 fallback 至该常量（MVP 暂用常量）。

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