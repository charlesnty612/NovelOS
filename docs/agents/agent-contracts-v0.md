# Agent I/O 接口契约 — `agent-contracts:v0`

> 版本：`agent-contracts:v0`
> 范围：Director / Writer / Observer 三个核心 Agent 的输入契约、输出契约、失败语义、对 PRD §113「Agent 十问」的逐条回答；Arbiter Agent 占位契约（V1 生效，MVP 不启用，对齐 ADR-0001）。
> 对齐：PRD §28（Agent 名录）、§29-33（Agent 职责）、§40（主 Workflow）、§62（Prompt 九段）、§63（Prompt/Agent 分离）、§87（Human-in-the-loop）、§89（风险等级）、§92（可恢复性）、§94（Prompt Version）、§113（Agent 十问）、§116（结构化输出）。
> 关联 Prompt 文件：
> - `docs/agents/prompts/director-v1.md`
> - `docs/agents/prompts/writer-v1.md`
> - `docs/agents/prompts/observer-v1.md`
> - `docs/agents/prompts/arbiter-v0.md`（V1 生效，MVP 不启用）
> 关联 Schema：
> - `docs/state-model/schemas/state-delta.schema.json`（权威，唯一）
> - `docs/architecture/context-engine-v0.md`
> 状态：本文件由 Prompt 编写子代理产出，**只**定义 I/O 契约与十问回答，不复制 Context Engine 内部层定义。

---

## 1. 文档目的

Prompt 定义行为，Agent 定义能力，Workflow 决定何时调用。本契约文档定义三个核心 Agent 的：

- **能力边界**（做什么 / 不做什么）
- **输入字段**（含来源、必填性、约束）
- **输出字段**（含类型、Schema 引用）
- **失败语义**（不合法输出、超时、依赖缺失时 Workflow 如何处置）
- **可重放性要求**（Prompt Version、Model Version、State Snapshot）
- **PRD §113「Agent 十问」逐条回答**

供 Workflow Runtime、Context Engine、State Validator、UI Workbench 使用。

---

## 2. 总览表

| Agent | 职责（一句话） | 输入契约文件 | 输出契约文件 | Prompt 版本 | 输出 Schema |
|---|---|---|---|---|---|
| Director | 决定 WHAT 发生 / WHY 发生 | §3.1 | §3.2 | `director:v1` | `director-plan.v1` |
| Writer | 把 Narrative Slot 写成中文小说正文 | §4.1 | §4.2 | `writer:v1` | `writer-output.v1` |
| Observer | 从 Draft 中提取 State Delta 业务载荷 | §5.1 | §5.2 | `observer:v1` | `state-delta-v0`（由 Committer 注入） |
| Arbiter（V1 生效，MVP 不启用） | 裁决 Observer 观察清单并产出正式 Delta 业务载荷 | §5A.1 | §5A.2 | `arbiter:v0` | `arbiter-report.v0` + `state-delta-v0`（由 Committer 注入） |

---

## 3. Director Agent 契约

### 3.1 输入契约

| 字段 | 类型 | 必填 | 来源 | 约束 |
|---|---|---|---|---|
| `agent` | string | Y | Workflow Runtime | 固定 `"director"` |
| `prompt_version` | string | Y | Agent Registry | 当前固定 `"director:v1"` |
| `chapter.chapter_id` | string | Y | Project / Chapter Service | 必须存在于 projects.chapters 表 |
| `chapter.title` | string\|null | N | Chapter Service | — |
| `chapter.target_word_count` | integer | Y | Workflow Runtime | 与项目 style_constraints 一致 |
| `chapter.expected_role` | enum | Y | Director 上游（Outline） | `setup\|escalation\|turn\|payoff\|denouement` |
| `project.*` | object | Y | Project Service | — |
| `author_intent.raw` | string | Y | Intent Parser | 自然语言 |
| `author_intent.structured` | object\|null | N | Intent Parser | 若 Intent Parser 已运行则填 |
| `story_state_snapshot.*` | object | Y | Story State Service | 来自上一章节 commit 后的 snapshot |
| `character_state_excerpts[]` | array | Y | Character Service | 只含 `knowledge_scope` 在 `your_visibility` 内的角色 |
| `world_state_excerpts.*` | object | Y | World State Service | — |
| `plot_graph_excerpt.*` | object | Y | Plot Service | — |
| `hook_ledger_excerpt[]` | array | Y | Hook Service | 仅 ACTIVE/ESCALATED 钩子 |
| `narrative_debt_excerpt[]` | array | Y | Debt Service | 仅 status=open 的债务 |
| `knowledge_permissions.*` | object | Y | Context Engine（按层过滤后） | 必须含 `your_visibility` |
| `constraints.*` | object | Y | Project Config | 含 style_constraints_id |

> **层引用**：以上字段对应 `docs/architecture/context-engine-v0.md` 的层（L1 Project / L2 Story State / L3 Character State / L4 Plot Context / L5 Chapter Context / L9 Agent Private Context）。Context Engine 负责按 §23 知识权限过滤后填入；本契约只声明消费哪些字段，不规定 Context Engine 内部实现。

### 3.2 输出契约

完整 Schema 见 `docs/agents/prompts/director-v1.md` §7。简要要点：

- 顶层字段：`schema_version`, `prompt_version`, `chapter_id`, `chapter_goal`, `core_conflict`, `turning_point`, `expected_role`, `key_beats[]`, `character_changes_planned[]`, `information_releases[]`, `hook_handling[]`, `debt_handling[]`, `proposed_new_entities[]`, `deviations[]`, `knowledge_leakage_check`, `open_questions[]`, `notes_for_planner`。
- `schema_version = "director-plan.v1"`。
- `prompt_version = "director:v1"`。
- 必填字段由 §7 列出，详见 Prompt。
- **禁止输出 Markdown 包裹**；只允许 JSON。

### 3.3 失败语义

| 失败情形 | 检测点 | Workflow Runtime 处置 |
|---|---|---|
| 输出不匹配 Schema | JSON Schema 校验（下游） | 自动重试 1 次；仍失败 → 进入 Human Node（人工修订） |
| `chapter_goal` 为空 / 多目标 | 正则 + 语义规则 | 重试并加强 prompt hint；仍失败 → Human Node |
| ID 引用了不存在的 entity | 引用完整性检查 | 重试 1 次；仍失败 → Human Node（需人工补 entity） |
| 包含正文片段 | 正则扫描（中文对话标记、叙述视角片段） | 拒绝并立即进入 Human Node，不重试（避免 LLM 写正文循环） |
| `risk_level = HIGH` 但未列 Human Approval 点 | 规则校验 | 补全后再走下游；不允许直接通过 |
| Timeout | Workflow Runtime | 重试 1 次（不同 temperature）；仍超时 → Human Node |
| 输出包含 `proposed_new_entities` | 引用完整性 | 视为需要在 Human Node 中确认的新实体；不阻塞，但 §89 规则要求 HIGH 项人工审批 |

### 3.4 可重放性要求

- 每次调用必须记录 `prompt_version`（`director:v1`）。
- 必须记录 `model.provider` + `model.model` + `model.model_version` + `model.parameters` + 可选 `seed`（PRD §94、§95、§96）。
- 必须记录 `chapter_id`、`previous_state_version`、`Input Context IDs`（PRD §93）。
- 必须 snapshot 输入上下文（便于 replay）。

---

## 4. Writer Agent 契约

### 4.1 输入契约

| 字段 | 类型 | 必填 | 来源 | 约束 |
|---|---|---|---|---|
| `agent` | string | Y | Workflow Runtime | 固定 `"writer"` |
| `prompt_version` | string | Y | Agent Registry | 当前固定 `"writer:v1"` |
| `chapter.*` | object | Y | Chapter Service | — |
| `director_plan.*` | object | Y | Director 输出（上一节点） | 必含 `chapter_goal`、`key_beats[]`、`notes_for_planner` |
| `scene_plan.scenes[]` | array | Y | Scene Planner（下一节点） | 每 Scene 含 `slots[]`；slot `type` ∈ {dialogue, action, description, emotion, suspense, humor, romance} |
| `character_state_excerpts[]` | array | Y | Character Service | 仅 `knowledge_scope` 在 `your_visibility` 内的角色 |
| `world_state_excerpts.*` | object | Y | World State Service | 必须含 `sensory_anchors[]` |
| `recent_prose.*` | object | Y | Chapter Service | 上一章/上一场结尾摘录 |
| `retrieved_memory[]` | array | N | Memory Service | 由 Context Engine 注入 |
| `knowledge_permissions.*` | object | Y | Context Engine | `forbidden_kinds` 至少含 `HIDDEN` |
| `style_constraints.*` | object | Y | Project Style Service | 含 `forbidden_words[]`、`pov`、`dialogue_ratio` 等 |

> **层引用**：上述字段对应 `docs/architecture/context-engine-v0.md` 的层（L2-L7）。

### 4.2 输出契约

完整 Schema 见 `docs/agents/prompts/writer-v1.md` §7。要点：

- 顶层字段：`schema_version`, `prompt_version`, `chapter_id`, `prose`, `self_report`。
- `schema_version = "writer-output.v1"`。
- `prompt_version = "writer:v1"`。
- `prose` 为字符串（Markdown 文本），不含 Scene 标题，不含 `#` / `##`。
- `self_report` 必填，含 `slots_filled[]`、`word_count`、`scene_count`、`deviations[]`、`forbidden_word_hits[]`、`self_check_notes`。

### 4.3 失败语义

| 失败情形 | 检测点 | Workflow Runtime 处置 |
|---|---|---|
| Schema 不合规 | JSON Schema 校验 | 自动重试 1 次；仍失败 → Human Node |
| `slots_filled` 与上游 `slots[].slot_id` 不一致 | 集合相等校验 | 重试 1 次（加强 slot 覆盖提示）；仍失败 → Human Node |
| 字数偏离 ±15% | `word_count` vs `target_word_count` | 扣分但允许通过；若 author 设为 hard limit 则拒绝 |
| 命中 `forbidden_words` | 字符串扫描 | 拒绝并重试（不同 temperature / 重新采样）；最多 2 次 |
| 命中 HIDDEN 知识 | Context Engine 注入的禁用 token 列表 | 拒绝并立即标记 `risk_level = HIGH`，强制 Human Approval |
| 出现 Scene 顺序错乱 | 顺序对比 | 拒绝并重试；最多 2 次 |
| 出现元叙述/AI 味道 | 正则（"本章目标"、"仿佛…一般"、"如同…一般"） | 重试 1 次；仍失败 → Critic 节点 |
| Timeout | Workflow Runtime | 重试 1 次；超时 → Human Node |

### 4.4 可重放性要求

- 同 §3.4。

---

## 5. Observer Agent 契约

### 5.1 输入契约

| 字段 | 类型 | 必填 | 来源 | 约束 |
|---|---|---|---|---|
| `agent` | string | Y | Workflow Runtime | 固定 `"observer"` |
| `prompt_version` | string | Y | Agent Registry | 当前固定 `"observer:v1"` |
| `chapter.chapter_id` | string | Y | Chapter Service | 必须存在 |
| `chapter.title` | string\|null | N | Chapter Service | — |
| `chapter.draft_text` | string | Y | Drafts Service | 章节完整正文 |
| `chapter.scene_ids` | array | N | Chapter Service | 本章 Scene ID 列表，便于 evidence.scene_id 填写 |
| `previous_state_version` | integer | Y | Story State Service | 正整数（如 `2`），与 `previous_state.state_version` 同步；Observer 不直接消费此字段，仅用于 Workflow Runtime 在注入顶层 `previous_state_version` 时取源 |
| `previous_state.*` | object | Y | Story State Service | 必须含 `state_version`（正整数）、`characters[]`、`world.*`、`hooks[]`、`debts[]`、`recent_events[]` |
| `director_plan_summary.*` | object | Y | Director 输出（本章） | 用于计划一致性比对；用于 Prompt 辅助字段 `deviations` 自检 |
| `knowledge_permissions.*` | object | Y | Context Engine | `forbidden_kinds` 至少含 `HIDDEN` |
| `config.min_excerpt_chars_low_confidence` | integer | N | Workflow Runtime | 默认 80；`confidence < 0.5` 时 excerpt 最少字符数 |
| `config.max_changes_per_array` | integer | N | Workflow Runtime | 默认 50 |

### 5.2 输出契约

权威 Schema：`docs/state-model/schemas/state-delta.schema.json`。本节只描述 Observer 视角的输出分工与边界。

**输出分工（与主会话口径对齐）**：Observer **只输出业务载荷**——即 §5.2.1 列出的 7 个 change 数组。**Observer 不输出任何顶层元信息字段**。

#### 5.2.1 Observer 输出（业务载荷）

JSON 顶层**仅**含以下 7 个 change 数组（即便为空也必须存在为 `[]`）：

| 数组 | required per-item 字段 | 固定 op | 关键枚举 |
|---|---|---|---|
| `character_changes` | change_id, op, target_id, character_id, facet, field, before*, after*, confidence, evidence, risk_level | add/update/remove | facet ∈ {definition, state} |
| `world_changes` | change_id, op, target_id, world_kind, world_id, field, before*, after*, confidence, evidence, risk_level | add/update/remove | world_kind ∈ {location, faction, rule, politics, economy, event, time} |
| `relationship_changes` | change_id, op, target_id, from_character_id, to_character_id, relation_type, before*, after*, confidence, evidence, risk_level | add/update/remove | before/after 为 object |
| `new_events` | change_id, op, target_id, event_id, type, participants, time, confidence, evidence, risk_level | const `add` | type ∈ {revelation, conflict, decision, encounter, transition, other} |
| `resolved_hooks` | change_id, op, target_id, hook_id, to_status, payoff_summary, confidence, evidence, risk_level | const `update` | to_status 五态之一（对齐 PRD §21） |
| `new_hooks` | change_id, op, target_id, hook_id, name, importance, description, confidence, evidence, risk_level | const `add` | importance ∈ [0, 1] |
| `debt_changes` | change_id, op, target_id, debt_id, status_after, confidence, evidence, risk_level | add/update/remove | status_after ∈ {open, acknowledged, paid, forgiven} |

`before*` / `after*` 的具体 required 状态依赖 `op`：update 时 before/after 均必填；add 时 after 必填；remove 时 before/after 可为 null（remove 时 `reason` 必填）。

#### 5.2.2 元信息字段（Observer 不输出，由 Committer 注入）

以下 10 个顶层字段**不由 Observer 编写**，由 **Workflow Runtime / State Committer 在 Schema 校验与提交前注入**（依据 state-delta-v0.md §2.2「Observer 或其上游 Agent 生成」）：

| 字段 | 类型 | 注入时机 | 注入者 |
|---|---|---|---|
| `delta_id` | string（ULID/UUIDv7） | Committer 注入 | State Committer |
| `delta_version` | integer（固定 1） | Committer 注入 | State Committer |
| `schema_version` | string（固定 `"state-delta-v0"`） | Committer 注入 | State Committer |
| `chapter_id` | string | Committer 注入 | State Committer |
| `workflow_run_id` | string | Committer 注入 | State Committer |
| `previous_state_version` | integer | Committer 注入 | State Committer |
| `created_by` | string（固定 `"observer:v1"`） | Committer 注入 | State Committer |
| `created_at` | string（ISO-8601 date-time） | Committer 注入 | State Committer |
| `supersedes` | string\|null | 重试场景由 Workflow Runtime 注入 | Workflow Runtime |
| `notes` | string\|null | Workflow 决定是否填入 run-level notes | Workflow Runtime |

**禁止 Observer 输出**：LLM 不应尝试编造上述字段。Observer 输出若包含这 10 个字段中的任何一个，Validator 拒绝并报告越权（见 §5.3）。

#### 5.2.3 Prompt 辅助字段（Observer 不输出，仅用于自检）

`deviations[]` / `self_check{}` 是 Observer Prompt 内部自检字段，**不得写入最终 JSON 输出**。这些字段的存在仅为辅助 LLM 在 §3 步骤中做计划一致性自检；Schema 的 `additionalProperties: false` 会拒绝它们。

### 5.3 失败语义

| 失败情形 | 检测点 | Workflow Runtime 处置 |
|---|---|---|
| Schema 不合规 | JSON Schema 校验（`state-delta.schema.json`） | 自动重试 1 次；仍失败 → Human Node |
| Observer 输出含元信息字段（`delta_id` / `delta_version` / `schema_version` / `chapter_id` / `workflow_run_id` / `previous_state_version` / `created_by` / `created_at` / `supersedes` / `notes`） | 顶层白名单字段校验（必须不存在） | 视为越权；剥离后继续（**不重试**，避免 LLM 循环编造） |
| Observer 输出含 Prompt 辅助字段（`deviations` / `self_check` / `unresolved_plan_intents`） | 顶层白名单字段校验 | 视为越权；剥离后继续（不重试） |
| change 缺 required 字段 | Schema 校验 | 拒绝 + 重试 1 次 |
| `confidence` ∉ [0, 1] | 范围校验 | 拒绝 + 重试 |
| `confidence < 0.5` 且 `evidence.excerpt` 字符数 < `min_excerpt_chars_low_confidence` | 字数校验 | 拒绝 + 重试 1 次；仍失败 → Human Node |
| `evidence.excerpt` 不在 `draft_text` 中 | 子串校验 | 拒绝 + 重试 1 次；仍失败 → Human Node |
| `evidence.chapter_id` 缺失或非 string | evidence 校验 | 拒绝 + 重试 1 次 |
| `evidence.span` 存在但 `start >= end` | span 校验 | 拒绝 + 重试 1 次 |
| `risk_level = HIGH` change 进入下一节点 | 路由规则 | 必须经 Human Approval 后才能进入 Validator 提交（PRD §89） |
| `target_id` 在 `previous_state` 中不存在（`add` 类 new_events / new_hooks / debt_changes 除外） | 引用完整性 | 标记 warning，必须人工复核 |
| `new_events[].op != "add"` / `resolved_hooks[].op != "update"` / `new_hooks[].op != "add"` | const 校验 | 拒绝 + 重试 1 次 |
| `character_changes[].facet ∉ {definition, state}` | 枚举校验 | 拒绝 + 重试 1 次 |
| `world_changes[].world_kind ∉ 7 枚举` | 枚举校验 | 拒绝 + 重试 1 次 |
| `relationship_changes[].from_character_id / to_character_id / relation_type` 缺失 | required 校验 | 拒绝 + 重试 1 次 |
| `new_events[].time.timeline_day` 缺失或 < 1 / `participants` 为空 | required 校验 | 拒绝 + 重试 1 次 |
| `resolved_hooks[].to_status` 缺失或非法 / `payoff_summary` 缺失 | required 校验 | 拒绝 + 重试 1 次 |
| `new_hooks[].name / description / importance` 缺失或 `importance` 越界 | required 校验 | 拒绝 + 重试 1 次 |
| `debt_changes[].status_after` 缺失或非法 | required 校验 | 拒绝 + 重试 1 次 |
| `update` 操作 `before` 或 `after` 缺失 | op-dependency 校验 | 拒绝 + 重试 1 次 |
| `remove` 操作 `reason` 缺失 | op-dependency 校验 | 拒绝 + 重试 1 次 |
| `visibility` 非 null 但非法值 | 枚举校验 | 拒绝 + 重试 1 次 |
| `who_knows` 存在但非字符串数组 | 类型校验 | 拒绝 + 重试 1 次 |
| 七个 change 数组缺失 | 字段存在性 | 拒绝并重写整个 Delta |
| Timeout | Workflow Runtime | 重试 1 次（不同 temperature）；仍超时 → Human Node |
| Delta 中出现对文笔/风格的评价 | 正则扫描 | 拒绝（不属于 Observer 职责，PRD §28 Critic 才评价） |

### 5.4 可重放性要求

- 同 §3.4。
- 额外要求：`evidence.span` 采用统一偏移定义（utf-16 code unit），便于跨语言 / 跨工具对齐。
- 必须 snapshot 输入 draft_text 与 previous_state（不可只存 ID 引用，因为 draft 后续可能被修改）。
- Workflow Runtime 在注入元信息时记录 `delta_id`（由 Committer 生成 ULID/UUIDv7），便于后续 Retry / Rollback 通过 `supersedes` 指针追溯。

---

## 5A. Arbiter Agent 契约（V1 生效，MVP 不启用；ADR-0001）

> 本节登记 Arbiter Agent（裁决者）的 I/O 契约占位。MVP 阶段不调用 Arbiter，Observer 直产 Delta（沿用 §5）；V1 切换时（ADR-0001 状态翻转为 Active）以本节 + `arbiter-v0.md` 启用。本 v0 仅登记契约占位，不修改 Observer 流程。

### 5A.1 输入契约

| 字段 | 类型 | 必填 | 来源 | 约束 |
|---|---|---|---|---|
| `agent` | string | Y | Workflow Runtime | 固定 `"arbiter"` |
| `prompt_version` | string | Y | Agent Registry | 当前固定 `"arbiter:v0"` |
| `chapter.chapter_id` | string | Y | Chapter Service | 必须存在；与 Observer 输入同源 |
| `chapter.title` | string\|null | N | Chapter Service | — |
| `chapter.draft_text` | string | Y | Drafts Service | 章节完整正文（仅用于复核 evidence 可溯源，不重读全量） |
| `chapter.scene_ids` | array | N | Chapter Service | 本章 Scene ID 列表 |
| `observer_output.*` | object | Y | Observer 输出（上一节点） | 必须含 7 数组（character_changes / world_changes / relationship_changes / new_events / resolved_hooks / new_hooks / debt_changes），结构与 §5.2.1 一致 |
| `previous_state_version` | integer | Y | Story State Service | 正整数；与 `previous_state.state_version` 同步 |
| `previous_state.*` | object | Y | Story State Service | 结构同 §5.1 `previous_state` |
| `director_plan_summary.*` | object | Y | Director 输出（本章） | 用于 R6 计划一致性补救 |
| `knowledge_permissions.*` | object | Y | Context Engine | `forbidden_kinds` 至少含 `HIDDEN` |
| `config.adopt_confidence_threshold` | number | N | Workflow Runtime | 默认 `0.5` |
| `config.max_changes_per_array` | integer | N | Workflow Runtime | 默认 `50` |

> **层引用**：同 §5.1，对应 `docs/architecture/context-engine-v0.md` 的层（L1-L5、L9）。Context Engine 负责按 §23 知识权限过滤后填入。

### 5A.2 输出契约

Arbiter 产出的 `arbiter-report.v0` 包含两部分：

1. **`adjudications[]`** — 每条裁决记录（Arbiter 内部字段，不进入 State Delta）。
2. **`delta_payload{7 数组}`** — 仅含 `disposition = adopt` 的 change，**原样透传** Observer 输出（不得改写字段值）。

#### 5A.2.1 arbiter-report.v0 顶层结构

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | string | Y | 固定 `"arbiter-report.v0"` |
| `prompt_version` | string | Y | 固定 `"arbiter:v0"` |
| `chapter_id` | string | Y | 等于输入 `chapter.chapter_id` |
| `adjudications` | array | Y | 裁决记录数组，详见 §5A.2.2 |
| `delta_payload` | object | Y | 仅含 adopt 项的 7 数组，详见 §5A.2.3 |

#### 5A.2.2 裁决记录（adjudications[] 每项）

| 字段 | 类型 | 必填 | 枚举 / 约束 |
|---|---|---|---|
| `change_id` | string | Y | Observer 输入原样引用（如 `cc:01HCHAR001`）；R6 计划补救项以 `plan:` 前缀标识 |
| `disposition` | string | Y | `adopt` \| `pending` \| `conflict` \| `discard` |
| `category` | string | Y | `state_change` \| `hook` \| `debt` \| `discard` |
| `reason` | string | Y | 陈述句，不修辞 |
| `original_risk_level` | string | Y | `LOW` \| `MEDIUM` \| `HIGH`（从 Observer change 读取） |
| `final_risk_level` | string | Y | `LOW` \| `MEDIUM` \| `HIGH`（默认等于 original_risk_level，Arbiter 可升级） |

#### 5A.2.3 delta_payload 业务载荷

`delta_payload` 必须含以下 7 个数组（即便为空也必须存在为 `[]`）；每个 change 对象复用 `state-delta.schema.json` 字段定义（沿用 §5.2.1 表格）：

- `character_changes` / `world_changes` / `relationship_changes` / `new_events` / `resolved_hooks` / `new_hooks` / `debt_changes`

**仅含 `disposition = adopt` 的 change**；其他三态（pending / conflict / discard）一律不得出现；每条 change 字段值与 Observer 输出**逐字段相等**（不改 confidence、不改 evidence、不改 before/after、不改 risk_level、不改 notes）。

#### 5A.2.4 元信息字段（Arbiter 不输出，由 Committer 注入）

沿用 §5.2.2 清单——Arbiter 不输出 `delta_id` / `delta_version` / `schema_version`（`arbiter-report.v0` 顶层 schema_version 与 `state-delta-v0` 同名但语义不同，提交时由 Committer 分别处理）/ `delta_id` 等 10 个字段；`delta_payload` 顶层亦不得包含这些字段（除 `chapter_id` / `previous_state_version` 顶层允许但必须等于输入值，且不得出现在 `delta_payload` 内）。

### 5A.3 失败语义

| 失败情形 | 检测点 | Workflow Runtime 处置 |
|---|---|---|
| `schema_version != "arbiter-report.v0"` | 顶层白名单字段校验 | 拒绝 + 重试 1 次；仍失败 → Human Node |
| `prompt_version != "arbiter:v0"` | 顶层白名单字段校验 | 拒绝 + 重试 1 次；仍失败 → Human Node |
| `adjudications` 缺失或非数组 | 字段存在性 | 拒绝 + 重试 1 次 |
| `adjudications.length != Observer 七数组 change 总数` | 集合相等校验 | 拒绝 + 重试 1 次；仍失败 → Human Node（可能 LLM 漏判） |
| 任意 adjudication 的 `change_id` 在 Observer 输出中找不到对应 change（`plan:` 前缀的 R6 补救项除外） | 引用完整性 | 拒绝 + 重试 1 次 |
| `disposition` / `category` / `original_risk_level` / `final_risk_level` 含非法枚举值 | 枚举校验 | 拒绝 + 重试 1 次 |
| `disposition=discard` 但 `category != "discard"` | 枚举配对校验 | 拒绝 + 重试 1 次 |
| `delta_payload` 中存在 `disposition != adopt` 的 change（即 adjudications 未标 adopt 但出现在 delta_payload 中） | 集合差校验 | 拒绝 + 强制重新裁决（不重试 LLM 避免循环） |
| `delta_payload` 中 change 字段值与 Observer 输出不一致（confidence / evidence / before / after / risk_level / notes 等任一字段被改写） | 逐字段相等校验 | 拒绝 + 强制重新裁决（不重试 LLM） |
| `delta_payload` 含元信息字段（`delta_id` / `delta_version` / `schema_version` / `workflow_run_id` / `created_by` / `created_at` / `supersedes` / `notes`） | 顶层白名单字段校验 | 视为越权；剥离后继续（不重试） |
| `delta_payload` 通过 state-delta 业务载荷校验失败（7 数组 required 字段 / op 约束 / 枚举 / evidence 结构等） | JSON Schema 校验 | 拒绝 + 重试 1 次；仍失败 → Human Node |
| `delta_payload` 七数组任一缺失 | 字段存在性 | 拒绝 + 重写整个 report |
| `delta_payload` 数组长度超过 `config.max_changes_per_array` | 长度校验 | 拒绝 + Human Node（可能 Observer 误收大量 change） |
| `adjudications` 含至少一条 `disposition=conflict` | 路由规则 | 整组挂起 → Human Node（E-ARB-20）；冲突项落入 Run Log 冲突队列 |
| `adjudications` 含至少一条 `disposition=pending` | 路由规则 | pending 项落入 Run Log 待审队列；**不阻塞** adopt 项提交 |
| `reason` 为空字符串或缺失 | 字段存在性 | 拒绝 + 重试 1 次 |
| Arbiter 输出引入 HIDDEN 知识作为裁决依据 | 知识权限扫描 | 拒绝 + Human Node（PRD §23） |
| Timeout | Workflow Runtime | 重试 1 次（不同 temperature）；仍超时 → Human Node |

### 5A.4 可重放性要求

- 同 §3.4。
- 每次调用必须记录：
  - `prompt_version`（`arbiter:v0`）；
  - `model.provider` + `model.model` + `model.model_version` + `model.parameters` + 可选 `seed`（PRD §94、§95、§96）；
  - `chapter_id` + `previous_state_version` + `Input Context IDs`（PRD §93）；
  - **Observer 输出快照**（完整 7 数组，不可只存 ID 引用，因为 Observer 输出后续可能被重做或回滚）；
  - **完整 `adjudications[]`**（含 change_id、disposition、category、reason、original_risk_level、final_risk_level），便于 replay / audit / 调试；
  - 提交后的 `delta_id`（由 Committer 注入后回填到 Run Log）。
- 必须 snapshot 输入 `previous_state` 与 `director_plan_summary`（不可只存 ID 引用）。

---

## 6. 跨 Agent 错误处理与降级策略

### 6.1 重试原则

| 错误类型 | 自动重试次数 | 重试策略 |
|---|---|---|
| Schema / 类型错误 | 1 | 同一 prompt，重 prompt hint 强提示 |
| 字数 / 顺序 / 覆盖 | 1 | 重试 + 明确缺陷提示 |
| 命中禁用词 / 禁用知识 | 1-2 | 重试 + 调高 temperature / 重新采样 |
| 超时 | 1 | 重试 + 调整 max_tokens |
| 内容越权（Writer 写正文外的剧情改动） | 0 | 立即 Human Node |
| 高风险但未声明审批点 | 0 | 补全后再走 |

### 6.2 降级

| 情形 | 降级路径 |
|---|---|
| Director 输出包含 `proposed_new_entities` | 转入 Human Node 确认是否新增 entity；不阻塞但延迟到 commit 前 |
| Writer 字数不足 | 重新调用 Writer（同一 Scene Plan）；最多 2 次；仍不足 → Critic + Human |
| Observer 大量 low_confidence | 转入 Critic → Human 复核（可能是 Draft 表述不清） |
| State Validator 拒绝 | 进入 Rollback → Human Node（PRD §65） |

### 6.3 与 State Transaction 的关系（PRD §65）

Observer 的输出 State Delta 业务载荷由 Workflow Runtime 注入元信息（`delta_id` / `created_by` / `created_at` / `previous_state_version` / `chapter_id` / `workflow_run_id` / `schema_version` 等）→ 经 State Validator 校验 → State Committer 落库 → 新 State Snapshot（`state_version = n+1`）。失败时回滚至 `state_version = n`，**不污染**当前 State。`state_version` 为单调递增正整数（state-delta-v0.md §6.2），由 Schema `previous_state_version`（integer, ≥ 1）携带乐观锁令牌。

---

## 7. PRD §113「Agent 十问」逐条回答

> PRD §113：任何新 Agent 都必须说明：1) 为什么需要它？2) 它负责什么？3) 它不能负责什么？4) 输入是什么？5) 输出是什么？6) 读什么？7) 写什么？8) 使用什么模型？9) 失败怎么办？10) 如何评价？

### 7.1 Director Agent

| # | 问题 | 回答 |
|---|---|---|
| 1 | 为什么需要它？ | 长篇小说需要在写正文前先确定「本章要发生什么、为什么发生」（PRD §29 WHAT/WHY）。若把规划与写作合一，Writer 会同时承担剧情决策与正文表达，造成规划被表达侵蚀、人物行为前后不一致。 |
| 2 | 它负责什么？ | 单章节的 WHAT/WHY：核心目标、核心冲突、转折点、人物变化声明、信息释放、伏笔处理、债务推进。 |
| 3 | 它不能负责什么？ | 不能写正文；不能指定具体句子、对话、描写；不能直接修改 Canonical Story State；不能改变人物核心设定或世界规则；不能创造新的 hook / debt / character / location / faction（除非 proposal）。 |
| 4 | 输入是什么？ | chapter 元信息 + project 元信息 + author_intent + story_state_snapshot + character_state_excerpts + world_state_excerpts + plot_graph_excerpt + hook_ledger_excerpt + narrative_debt_excerpt + knowledge_permissions + constraints（详见 §3.1）。 |
| 5 | 输出是什么？ | 一份 `director-plan.v1` JSON：chapter_goal、core_conflict、turning_point、key_beats、character_changes_planned、information_releases、hook_handling、debt_handling、proposed_new_entities、deviations、knowledge_leakage_check、open_questions、notes_for_planner。 |
| 6 | 读什么？ | Canonical Story State（按 §23 知识权限过滤到 DIRECTOR/AUTHOR 可见层）+ author_intent + 上一章节摘要 + Hook Ledger + Narrative Debt。 |
| 7 | 写什么？ | 只写 Director Plan JSON；不写 State Delta、不写正文。 |
| 8 | 使用什么模型？ | capability = `reasoning`（PRD §51）。Model Router 按 Project Config 选择；典型为高推理能力的模型（带 reasoning / long-thinking），不强制本地。 |
| 9 | 失败怎么办？ | Schema 失败重试 1 次 → Human Node；ID 不合法 → Human Node；越权写正文 → 立即 Human Node；HIGH 风险项缺审批点 → 补全后再走。详见 §3.3。 |
| 10 | 如何评价？ | 8 条 Evaluation 规则（详见 `director-v1.md` §9），覆盖 Schema 合规、单一目标、ID 合法性、风险标注、三问可答性、无正文污染、知识隔离、偏差显式。配合 §84 Regression Eval 在 golden chapters 上对比。 |

### 7.2 Writer Agent

| # | 问题 | 回答 |
|---|---|---|
| 1 | 为什么需要它？ | Director / Planner 给出结构性规划后，需要一个专门的「笔」把它们翻译成可读中文小说正文。Writer 与 Director 分离（PRD §3、§31），才能保证剧情决策不污染文笔表达，文笔微调不污染剧情结构。 |
| 2 | 它负责什么？ | 把 Scene Plan 中的 Narrative Slot 翻译为中文小说正文片段，按 Scene 顺序逐 Scene 写作，每 Scene 内覆盖所有 slot，遵守 style_constraints，产出 prose + self_report。 |
| 3 | 它不能负责什么？ | 不能改变剧情、不能创造新事件、不能修改世界规则、不能修改人物核心设定、不能使用 HIDDEN 知识、不能跨 Scene 改写、不能修改 Canonical State、不能输出 State Delta、不能跳过 slot、不能输出元叙述（PRD §31）。 |
| 4 | 输入是什么？ | chapter 元信息 + director_plan + scene_plan（含 slots[]）+ character_state_excerpts + world_state_excerpts + recent_prose + retrieved_memory + knowledge_permissions + style_constraints（详见 §4.1）。 |
| 5 | 输出是什么？ | 一份 `writer-output.v1` JSON：prose（Markdown 文本）+ self_report（slots_filled、word_count、scene_count、deviations、forbidden_word_hits、self_check_notes）。 |
| 6 | 读什么？ | Director Plan + Scene Plan + Character State（按 WRITER 可见层）+ World State（sensory_anchors）+ Recent Prose + Retrieved Memory。 |
| 7 | 写什么？ | 仅写 prose（章节正文片段）与 self_report。**绝不**写 State Delta、**绝不**修改 Canonical State。 |
| 8 | 使用什么模型？ | capability = `creative_writing`（PRD §51）。倾向擅长中文文学创作的模型；可在 Project Config 中按 Style Profile 切换。 |
| 9 | 失败怎么办？ | Schema 失败重试 1 次 → Human Node；slot 缺失重试 1 次 → Human Node；字数偏离超 15% 扣分；禁用词命中重试 1-2 次；命中 HIDDEN 知识立即标 HIGH 并走 Human Approval。详见 §4.3。 |
| 10 | 如何评价？ | 10 条 Evaluation 规则（详见 `writer-v1.md` §9），覆盖 Schema、Slot 覆盖、顺序、字数、禁用词、知识隔离、元叙述、偏离显式、视角一致、自洽衔接。 |

### 7.3 Observer Agent

| # | 问题 | 回答 |
|---|---|---|
| 1 | 为什么需要它？ | 章节正文完成后，必须把「正文里发生了什么事实变化」结构化抽离出来，由 State Validator 校验后由 State Committer 写入 Canonical State（PRD §35）。若直接把 LLM 自由文本当作 State 修改，会违反 §116「AI 输出必须结构化」。 |
| 2 | 它负责什么？ | 从 Draft 中观察事实变化，**输出 State Delta 的业务载荷**（七个 change 数组），并对照 Director Plan 标记未完成的计划意图（用 Prompt 辅助字段 `deviations` 自检，不写入最终 JSON）。 |
| 3 | 它不能负责什么？ | 不能写正文、不能修改 Draft、不能修改 Canonical State、不能凭空捏造 change、不能跳过 evidence、不能对文笔/风格做评价（那是 Critic 的工作）、不能使用 HIDDEN 知识作为判定依据、**不能输出顶层元信息字段**（`delta_id` / `delta_version` / `schema_version` / `chapter_id` / `workflow_run_id` / `previous_state_version` / `created_by` / `created_at` / `supersedes` / `notes` 由 Committer 注入）。 |
| 4 | 输入是什么？ | chapter（含 chapter_id / title / draft_text / scene_ids）+ previous_state_version（正整数）+ previous_state（含 state_version、characters、world、hooks、debts、recent_events）+ director_plan_summary + knowledge_permissions + config（min_excerpt_chars_low_confidence、max_changes_per_array）。详见 §5.1。 |
| 5 | 输出是什么？ | **仅 7 个 change 数组**（character_changes / world_changes / relationship_changes / new_events / resolved_hooks / new_hooks / debt_changes），每个数组即便为空也必须存在为 `[]`。**不输出任何顶层元信息字段与 Prompt 辅助字段**。Schema 权威定义在 `docs/state-model/schemas/state-delta.schema.json`；Observer Prompt §7.2.1 与 Schema `properties` 逐字对齐。 |
| 6 | 读什么？ | Draft 全文 + previous_state 全量 + Director Plan 摘要。 |
| 7 | 写什么？ | 仅写 7 个 change 数组的业务载荷。**绝不**写正文、**绝不**提交 Canonical State（提交由 Validator → Committer 完成）、**绝不**输出顶层元信息（由 Committer 注入）、**绝不**输出 Prompt 辅助字段（仅用于自检）。 |
| 8 | 使用什么模型？ | capability = `reasoning`（同 Director），但偏重事实抽取与对比，而非文学推理。可用较小模型。 |
| 9 | 失败怎么办？ | Schema 失败重试 1 次 → Human Node；Observer 越权输出元信息/辅助字段 → 剥离后继续（不重试）；confidence 不合规拒绝重试；evidence 不足重试 1 次 → Human Node；HIGH 项必走 Human Approval；op/枚举/required 缺失拒绝 + 重试 1 次；超 `max_changes_per_array` 报错 → Human。详见 §5.3。 |
| 10 | 如何评价？ | 24 条 Evaluation 规则（详见 `observer-v1.md` §9），覆盖 Schema 合规、元信息缺席、辅助字段缺席、change required 字段、confidence 范围、低 confidence 证据长度、op 合法性（含 const）、risk_level 合法性、evidence 必填字段与可溯源、target_id 引用、HIGH 必审批、before/after 依赖 op、计划一致性、notes 修辞扫描、守恒性、visibility/who_knows 语义、各数组枚举必填字段。 |

### 7.4 Arbiter Agent（V1 生效，MVP 不启用；ADR-0001）

> 本节为 Arbiter Agent 十问回答占位，V1 切换时与 `arbiter-v0.md` §9 Evaluation 配套启用。

| # | 问题 | 回答 |
|---|---|---|
| 1 | 为什么需要它？ | PRD §33.2 决议：V1 起 Observer 与 Arbiter 职责分离。Observer 单点可靠性上限即抽取精度，存在「自产自审」盲区（§33.1 已识别）。Arbiter 作为独立裁决者，对 Observer 观察清单做取舍与定纷，承担裁决责任而非抽取责任（ADR-0001 §1）。 |
| 2 | 它负责什么？ | 对 Observer 7 数组输出逐条裁决（adopt / pending / conflict / discard 四态）；归类（state_change / hook / debt / discard 四类）；产出 `arbiter-report.v0`（含 `adjudications[]` 与 `delta_payload`）；R6 计划一致性补救（在 `adjudications` 中登记漏抓计划意图）。 |
| 3 | 它不能负责什么？ | 不能写正文、不能修改 Draft、不能修改 Canonical State、不能凭空新增 `delta_payload` 中的 change（仅 `disposition=adopt` 项可透传）、不能改写 Observer 字段值（含 confidence / evidence / before / after / risk_level）、不能输出元信息 10 字段（由 Committer 注入）、不能对文笔做评价、不能使用 HIDDEN 知识作为裁决依据、不能做 JSON Schema 校验（由 Validator 兜底）。 |
| 4 | 输入是什么？ | chapter（含 chapter_id / title / draft_text / scene_ids）+ observer_output（7 数组）+ previous_state_version + previous_state + director_plan_summary + knowledge_permissions + config（adopt_confidence_threshold / max_changes_per_array）。详见 §5A.1。 |
| 5 | 输出是什么？ | 一份 `arbiter-report.v0` JSON：`schema_version` / `prompt_version` / `chapter_id` / `adjudications[]` / `delta_payload{7 数组}`。`delta_payload` 仅含 adopt 项且字段值与 Observer 输出逐字段相等；元信息 10 字段由 Committer 注入。 |
| 6 | 读什么？ | Observer 完整 7 数组输出 + previous_state 全量 + director_plan_summary + draft_text（仅用于复核 evidence 可溯源）。 |
| 7 | 写什么？ | 仅写 `arbiter-report.v0` JSON（adjudications + delta_payload）。**绝不**写正文、**绝不**修改 Canonical State、**绝不**改写 Observer 字段值、**绝不**输出元信息 10 字段（由 Committer 注入）。 |
| 8 | 使用什么模型？ | capability = `reasoning`（同 Observer），但偏重裁决语义（证据完整判定、冲突识别、计划一致性比对），而非事实抽取。可用较小模型或与 Observer 同型。 |
| 9 | 失败怎么办？ | Schema 版本错误 / 裁决不全覆盖 / change_id 对不上 / 枚举非法 → 拒绝 + 重试 1 次 → Human Node；delta_payload 透传违规（字段改写 / 含非 adopt 项 / 元信息越权）→ 剥离 / 强制重新裁决（不重试 LLM）；conflict 整组挂起（E-ARB-20）；pending 不阻塞 adopt；超 max_changes_per_array → Human Node。详见 §5A.3。 |
| 10 | 如何评价？ | 22 条 Evaluation 规则（详见 `arbiter-v0.md` §9 E-ARB-01 至 E-ARB-22），覆盖 schema/prompt 版本固定、adjudications 全覆盖与 change_id 一致性、disposition/category 枚举、delta_payload 严格 adopt-only 与字段透传、元信息缺席、七数组存在、delta_payload 通过 state-delta 业务载荷校验、R6 计划补救、conflict 整组挂起、HIDDEN 知识扫描等。 |

---

## 8. 与 Prompt 文件的映射

| 契约条款 | Director | Writer | Observer | Arbiter（V1） |
|---|---|---|---|---|
| 输入契约 | `director-v1.md` §5 Context | `writer-v1.md` §5 Context | `observer-v1.md` §5 Context | `arbiter-v0.md` §5 Context |
| 输出 Schema | `director-v1.md` §7 | `writer-v1.md` §7 | `observer-v1.md` §7 + `state-delta.schema.json` | `arbiter-v0.md` §7（arbiter-report.v0 + state-delta 业务载荷） |
| Forbidden | `director-v1.md` §4 | `writer-v1.md` §4 | `observer-v1.md` §4 | `arbiter-v0.md` §4 |
| Evaluation | `director-v1.md` §9 | `writer-v1.md` §9 | `observer-v1.md` §9 | `arbiter-v0.md` §9（E-ARB-01 至 E-ARB-22） |
| 失败语义 | 本文档 §3.3 | 本文档 §4.3 | 本文档 §5.3 | 本文档 §5A.3 |

---

## 与 PRD 的映射

| PRD 章节 | 本文档对应 |
|---|---|
| §28 Agent 名录 | §2 总览 |
| §29 Director | §3 + §7.1 |
| §30 Planner | 留待后续 v0.1；本版本不包含 Planner I/O 契约（按主会话范围锁定） |
| §31 Writer | §4 + §7.2 |
| §33 Observer | §5 + §7.3 |
| §33.2 观察-裁决分离（V1 引入） | §5A + §7.4 + ADR-0001（V1 生效，MVP 不启用） |
| §40 主 Workflow | §2 体现 Agent 在流程中的位置 |
| §62 Prompt 九段结构 | 四个 Prompt 文件均按九段组织（含 `arbiter-v0.md`，V1 生效） |
| §63 Prompt/Agent 分离 | 本文档（契约）= Agent 能力定义；四个 Prompt = 行为定义 |
| §87 Human-in-the-loop | §3.3、§4.3、§5.3、§5A.3 的 Human Node 处置 |
| §89 风险等级 | §3.2、§4.2、§5.2、§5A.2 的 `risk_level` 与 §89 三档对应 |
| §92 可恢复性 | §3.4、§4.4、§5.4、§5A.4 的可重放性要求 |
| §94 Prompt Version | §2 总览表 + 各 Prompt `prompt_version` 字段（含 `arbiter:v0`） |
| §113 Agent 十问 | §7 全部十问（含 §7.4 Arbiter） |
| §116 AI 输出结构化 | §3.2 / §4.2 / §5.2 / §5A.2 均强制 JSON Schema |

---

## Open Questions

1. **PRD 未规定**：Planner Agent（§30）何时补齐 I/O 契约。本设计决策：本版本 v0 不含 Planner；Scene Plan 输入视为已由 Workflow / 后续 Planner 提供。下一版 v0.1 补 Planner 契约。
2. **PRD 未规定**：Critic / Validator / Integrator / State Committer / Polisher 等 Agent 的 I/O 契约。本设计决策：本版本 v0 仅覆盖 PRD §113 明确点名「写正文」和「Commit 前必须经过」的三个核心 Agent；其他 Agent 在后续版本补齐。
3. **PRD 未规定**：失败后是否对输入做 mutation（如自动删除冗余上下文）。本设计决策：失败仅重试，不修改输入；输入修改由 Human Node 决策。
4. **已执行**：`docs/state-model/schemas/state-delta.schema.json` 已发布为权威，本契约文档 §5 与 Observer Prompt v1 §7 已与 Schema 逐字对齐（含七个 change 数组的 required 字段、枚举、op 约束、evidence 结构、权限字段语义、元信息字段分工）。原 Open Question 闭环。
5. **待验证**：Human Approval 节点对 `risk_level = HIGH` 的处理是否要保留「整章 Approve」与「单项 Approve」两种粒度。本 v0 采用整章 Approve，后续 v1 可拆为细粒度。
6. **待验证**：本契约是否需要在 v1 中纳入 Integrator（§36）以保证多 Scene Writer 输出的合并语义。当前 v0 仅声明 Writer 自报 `self_report` 供 Integrator 使用，Integrator 自身契约留待后续。
7. **已执行**：**Arbiter（裁决者）Agent**——PRD v1.2 §33.2 已登记观察-裁决分离为 V1 能力。**已产出**：
   - `docs/adr/ADR-0001-arbiter-observer-separation.md`（V1 引入决议）
   - `docs/agents/prompts/arbiter-v0.md`（V1 生效设计稿，MVP 不启用）
   - 本文档 §5A（Arbiter 契约）+ §7.4（Arbiter 十问）+ §8（映射表 Arbiter 列）
   - Open Question 收敛：`observer:v2` 保守原则放宽边界、`conflict` 整组挂起粒度、`pending` 与 `conflict` 枚举细分、Arbiter 是否承担 Schema 校验等已转入 ADR-0001 §5 与 `arbiter-v0.md` Open Questions，待 V1 切换前以子代理调研 + 主控拍板。V1 切换以 ADR-0001 状态翻转触发（`Accepted` → `Active`）；MVP 期间 feature flag 关闭，不上线。
