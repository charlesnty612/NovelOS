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
)
```

返回 dict，可直接 `json.dumps` 后作为 user message 传给 `run_agent`。

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