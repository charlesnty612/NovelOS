# ADR-0001：Observer 与 Arbiter 职责分离（V1 引入）

> 状态：**Accepted**
> 日期：2026-08-23
> 生效：V1（Observer Prompt v2 切换时启用）；MVP 阶段不启用
> 关联：`#NovelOS.md` §33.1、§33.2；`docs/agents/agent-contracts-v0.md` §5A；`docs/agents/prompts/arbiter-v0.md`；`docs/agents/prompts/observer-v1.md`
> 关联 Schema：`docs/state-model/schemas/state-delta.schema.json`
> 起草：NovelOS 主控

---

## 1. Context

### 1.1 PRD §33.1 — Observer 单点可靠性问题

PRD §33.1 把 Observer 定位为「整个 Canonical State 管线的咽喉，系统可靠性上限 = Observer 提取精度」。该条款规定了三条核心约束：

1. Observer 只提取「文本中实际发生的事」，角色误判/传闻/梦境归入 `character.state.beliefs`，不得写入事实性 state。
2. 每条 change 必须携带 `confidence`（0-1）与 `evidence`；`confidence < 0.5` 必须经人工确认后才允许 Commit。
3. 下游模块按「State 可能含错」设计，必须可追溯 evidence 链、Rollback 能力始终可用。

§33.1 同时指出每 10 章运行一次 State-vs-Prose 对账 Workflow，发现漂移必须人工处置。该对账目前依赖 Observer 自查，存在「自产自审」盲区。

### 1.2 PRD §33.2 — V1 观察-裁决分离决议

§33.2 原文：

> V1 起 Observer 与 Arbiter 职责分离：Observer 只对照正文与状态、输出带证据的观察清单（宁可多记、不做取舍）；Arbiter 负责裁决（adopt / pending / conflict；hook / debt / discard）并产出正式 State Delta。MVP 阶段维持 Observer 直产 Delta + `confidence < 0.5` 人工确认（§33.1），Arbiter 在 V1 以 ADR 引入。

决议要点：
- **MVP 不切换**，维持现行 Observer 直产 Delta 流程；
- **V1 起切换**，Observer 改为输出「带证据的观察清单」（保守原则放宽、宁可多记），Arbiter 承担裁决与正式 Delta 生产；
- 裁决三态：`adopt` / `pending` / `conflict`；
- 归类：`hook` / `debt` / `discard`（以及 `state_change` 默认归类）；
- Arbiter 在 V1 以 ADR 引入。

### 1.3 外部参考（吸收思想，不复制实现）

§33.2 注明「吸收 bishu-novel post-hoc 观察-裁决分离思想」。本 ADR 不复用任何 bishu-novel 代码或提示词；仅借鉴其「先全面观察、再独立裁决」的责任分层思路，最终实现由 NovelOS 主控在本 ADR 与 `arbiter-v0.md` 中自行设计。

---

## 2. Decision

本 ADR 引入 Arbiter Agent 概念并锁定以下 V1 设计决策。MVP 流程不变。

### 2.1 Observer 端改造（V1 生效）

- **保守原则放宽**：Observer 由「拿不准宁可不写」改为「宁可多记、不做取舍」。低 confidence 项不再要求立即人工确认，而是全量进入 Arbiter 队列裁决。
- **输出形态**：Observer 输出仍是 7 数组业务载荷（与 Schema 兼容），但语义改为「观察清单」而非「提议性 Delta」。
- Prompt 版本升级至 `observer:v2`（本 ADR 不实现 prompt 升级，仅登记口径；observer:v1 在 MVP 期间保持不变）。

### 2.2 Arbiter 端引入（V1 生效）

- **Prompt 版本**：`arbiter:v0`（V1 首次启用，定义见 `docs/agents/prompts/arbiter-v0.md`）。
- **职责**：对照 `previous_state` 与 `director_plan_summary`，对 Observer 输出做逐条裁决并产出正式 Delta 业务载荷。
- **裁决三态**（`disposition` 枚举）：
  - `adopt`：证据完整、confidence 达标、无冲突，进入 `delta_payload`。
  - `pending`：confidence < 0.5 或 `risk_level = HIGH`，进人工队列；不阻塞其余 adopt 项。
  - `conflict`：与 `previous_state` 矛盾（更新不存在字段、belief 与 knowledge 互斥）或同字段多条 change 互斥；必须人工，整组挂起。
- **归类**（`category` 枚举）：
  - `state_change`：默认归类（adopt 项透传到对应 7 数组之一）。
  - `hook`：涉伏笔推进/回收，但需要 Hook Ledger 二次确认的项。
  - `debt`：涉及 Narrative Debt 的项。
  - `discard`：Observer 误收的非事实项（文笔评价、重复条目、非原子条目需拆分说明）。
- **输出包装结构**：`arbiter-report.v0`，顶层 `{schema_version, prompt_version, chapter_id, adjudications[], delta_payload{7 数组}}`。
- **元信息分工**：Arbiter 不输出 Schema 顶层元信息（`delta_id` / `delta_version` / `schema_version` / `chapter_id` / `workflow_run_id` / `previous_state_version` / `created_by` / `created_at` / `supersedes` / `notes`），由 Committer 注入（对齐 agent-contracts §5.2.2 清单）。

### 2.3 写入位置

- `adjudications` 写入 Workflow Run Log（对齐 REQ-P8：所有裁决记录可追溯），便于 replay / audit / 调试。
- `delta_payload` 中 adopt 项进入 State Validator → State Committer 管线，与 MVP 流程一致。

---

## 3. Consequences

### 3.1 MVP 阶段（立即生效）

- 流程不变：Observer 直产 Delta → Validator → Committer。
- 本 ADR 仅作为概念登记与 V1 设计稿，不引入任何新调用链。
- `agent-contracts-v0.md` 已在 §5A + §7.4 登记 Arbiter 契约占位；§5 不修改。

### 3.2 V1 切换时

- Observer Prompt 升级 `observer:v1` → `observer:v2`，保守原则由「拿不准宁可不写」放宽为「宁可多记、不做取舍」。
- Arbiter Prompt 启用 `arbiter:v0`，主 Workflow 在 Observer 节点之后插入 Arbiter 节点。
- State Validator 接收对象由「Observer 输出的 Delta 业务载荷」改为「Arbiter 输出的 `delta_payload`」。
- 切换动作以本 ADR 状态翻转触发（`Accepted` → `Active`）；切换前需补 `observer:v2` prompt 与回归测试。

### 3.3 工作量影响

- 新增 Arbiter 调用节点一处（Workflow 配置 + 路由规则）。
- `adjudications` 表新增（约 10 字段）。
- Workflow Run Log 写入逻辑扩展。
- 评估：Observer 评估基线复用现有 golden chapters；Arbiter 评估需新增 golden arbitrations（adopt / pending / conflict / discard 各若干样本）。

### 3.4 风险与缓解

- **风险 1**：Observer 保守原则放宽导致 false positive 上升 → 由 Arbiter `pending` 状态兜底，最终进人工队列。
- **风险 2**：Arbiter 与 Observer 重复抽取，token 成本上升 → Arbiter 输入复用 Observer 完整输出，不重抽 evidence。
- **风险 3**：`conflict` 整组挂起粒度过粗 → Open Question 待 V1 前回填（见 §5）。
- **风险 4**：MVP 期间 dual-track 维护成本 → MVP 期间 Arbiter 代码以 feature flag 关闭，不上线。

---

## 4. Alternatives Considered

### 4.1 Alternative A — 维持 Observer 单 Agent 直产 Delta

- **思路**：不引入 Arbiter，保留 Observer 直产 Delta 流程；低 confidence 项走人工确认。
- **否决理由**：自产自审盲区、Observer 抽取精度即系统上限与 §33.1 已识别问题一致；§33.2 决议已明确否决此路径。

### 4.2 Alternative B — 把裁决并入 State Validator

- **思路**：State Validator 在校验 Schema 同时承担裁决职责，发现冲突 / 低 confidence 时直接拒绝并改写 Delta。
- **否决理由**：State Validator 是「语法 + 引用完整性」校验器，承担裁决会污染其语义并使其成为新的单点；裁决需要 `previous_state` 全量语义比对（belief/knowledge 互斥、计划一致性），不是 Validator 的职责范围。

### 4.3 Alternative C — Observer / Arbiter 二合一（多步 prompt 链）

- **思路**：在同一 Agent 调用内先做观察、再做裁决，仅以 prompt 节段分隔。
- **否决理由**：无法解耦裁决责任、无法独立 replay 裁决结果、无法独立评估 Observer 抽取精度；与 §33.2「职责分离」原文精神不符。

---

## 5. Open Questions

1. **V1 切换时 `observer:v2` 的保守原则放宽边界**：哪些原本会被 Observer 直接丢弃的项应该被保留进入 Arbiter 队列？口径待 V1 前以子代理调研产出。
2. **`conflict` 整组挂起的粒度**：当前设计为「整组 conflict → 整章人工」，但部分 conflict 可能仅涉及单条 change。是否拆为「按 change 挂起」？待 V1 前以子代理调研 + 主控拍板。
3. **`pending` 与 `conflict` 是否需细分**：例如 `pending.low_confidence` vs `pending.high_risk`、`conflict.field_mismatch` vs `conflict.belief_knowledge_exclusive`。枚举细分待 V1 前定夺。
4. **Arbiter 是否承担 Schema 校验职责**：当前设计中 Arbiter 不做 Schema 校验（仍由 Validator 兜底）。若 Arbiter 内置轻量校验可减少 round-trip，但会与 Validator 职责重叠。

---

## 6. References

- `#NovelOS.md` §33.1（Observer 可靠性机制）、§33.2（观察-裁决分离决议）、§113（Agent 十问）
- `docs/agents/agent-contracts-v0.md` §5A、§7.4（Arbiter 契约）
- `docs/agents/prompts/arbiter-v0.md`（Arbiter Prompt v0 设计稿）
- `docs/state-model/schemas/state-delta.schema.json`（Schema 权威）
- REQ-P8（Workflow Run Log 全量可追溯）
