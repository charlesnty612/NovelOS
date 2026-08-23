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