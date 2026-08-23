# Quality Scoring & Guardrails 规范 v0

> 版本：v0.1（v0 设计稿基础上增补 v1.2 爽感维度与合规 Guardrail 三件套）
> 适用范围：NovelOS Workflow（PRD §40 + §79 + §106）的 Quality Engine、State Commit 门禁、Regression Eval
> 主会话方案决策日期：2026-08-23
> 撰写日期：2026-08-23
> 状态：v0.1（v1.2 修订），随 PRD v1.2 生效；权重、阈值、基线值均为"建议值"，必须经首批 golden 章节评测后方可固化

---

## 0. 文档目的与范围

补齐 PRD §37 / §38 / §82 / §83 / §84 / §85 / §86 / §110 在"测量路径"上的空白，回答以下问题：

1. `Quality Score.overall / plot / character / continuity / style / pacing / foreshadowing / issues` 这八个字段**怎么算出来**？
2. §86 五条 Guardrail 的"严重失败"如何给出**可执行的判定规则**？
3. §84 的 `golden chapters / states / continuity cases` 如何**落地**（结构、标注、规模）？
4. §85 的 Regression 原则如何给出**可执行的"不能直接上线"判定**？

**不在本文档范围内**：

- 知识权限模型本身（参见已落地的 `docs/state-model/knowledge-permission-v0.md`，本文仅引用其字段与概念）；
- State Delta / Commit Record 的字段定义（参见已落地的 `docs/state-model/state-delta-v0.md`）；
- Prompt / Agent / Workflow 的实现细节（PRD §40-§80）；
- 数据模型其余表（见 `docs/data-model/data-model-v0.md`（待产出）等并行文档）。

---

## 1. Quality Score 输出结构

### 1.1 字段定义（沿用 PRD §38）

```jsonc
{
  "overall": 86,            // int [0, 100]，六子分加权聚合；§2 公式
  "plot": 91,               // int [0, 100]；§3.1
  "character": 89,          // int [0, 100]；§3.2
  "continuity": 94,         // int [0, 100]；§3.3
  "style": 78,              // int [0, 100]；§3.4
  "pacing": 82,             // int [0, 100]；§3.5
  "foreshadowing": 90,      // int [0, 100]；§3.6
  "issues": []              // 数组；§1.3 条目结构
}
```

**字段语义约束**：

- 所有子分与 `overall` 必须是 `int` 类型，**不允许只输出 `overall`**（PRD §38 铁律）。
- 任意子分缺失（null / undefined）必须以 `issues[].code = "scoring_missing_subscore"` 暴露，Commit Gate 视同失败（PRD §86 扩展解释）。
- `issues` 数组**永远存在**，空数组代表"无问题"——不要用 `null` 表示。

### 1.2 输入材料

一次 Quality 评估必须基于以下四个上下文快照（由 Commit Gate 提供同一 commit 的锁版本）：

| 上下文 | 来源 | 版本锁定点 |
|---|---|---|
| `chapter_draft` | Workflow 最后一次 Specialist Writer 输出 | State Commit 时的 `commit_id` |
| `chapter_plan` | Chapter Planner 输出 | 同上 |
| `state_snapshot_pre` | Commit 前一次的 Canonical State | 同上 |
| `state_snapshot_post` | Observer 给出的拟提交 Delta | 同上 |

**版本不一致**：任意输入缺失或版本不匹配 → `scoring_missing_input` issue，Commit Gate 阻断。

### 1.3 `issues[]` 条目结构

```jsonc
{
  "severity": "error" | "warning" | "info",
  // 对应 PRD §86 / §89 风险等级；error 必阻断 Commit
  "category": "schema_validity | timeline_consistency | character_contradiction | world_rule_contradiction | knowledge_leakage | plot | character | continuity | style | pacing | foreshadowing | payoff | compliance",
  // 前 5 类与 Guardrail 一一对应；后 6 类与子分一一对应；payoff 对应 §3.7 爽感体检 H-1~H-5；compliance 对应 §4.6/§4.7/§4.8 合规 Guardrail（REQ-Q6/Q7/Q8）
  "location": "<chapter_id>:<scene_id>:<span_id?>",
  // 没有 span 时退化为 "<chapter_id>:<scene_id>"
  "rule_id": "RULE_<short_name>",
  // 例：RULE_CHAR_LIVES_TWO_PLACES、SCHEMA_REQUIRED_FIELD
  "message": "<人类可读一句话>",
  "suggestion": "<可选修复建议或改写方向>",
  "evidence_refs": ["<chunk_id_or_state_field>"],
  // 用于人工复核时一键跳转
  "judge_trace": { /* 可选：LLM judge 时的 prompt hash + raw judge 文本（脱敏后），用于审计 */ }
}
```

约束：

- `severity` 三档与 PRD §89 风险等级对齐：`error` 等价 HIGH、`warning` 等价 MEDIUM、`info` 等价 LOW。
- `category` 前缀与 Guardrail / 子分对齐，方便路由到对应规则引擎或 LLM judge。
- `suggestion` 可省略；其余均为必填项。

---

## 2. Overall 聚合公式

### 2.1 公式 v0（建议值，需校准）

```
overall = round(
    0.20 * plot           // 剧情因果与三问必要性
  + 0.20 * character      // 人物一致性与弧线
  + 0.20 * continuity     // 时间/地点/世界规则/知识的连续性（含 Guardrail 软化项）
  + 0.15 * style          // 重复/AI味/句式/对白节奏
  + 0.15 * pacing         // 节奏与张力
  + 0.10 * foreshadowing  // 伏笔密度与兑现
)
```

**权重设定理由（PRD §82 十维目标 → 六子分的归并）**：

| PRD §82 维度 | 对应子分 | 备注 |
|---|---|---|
| Consistency | continuity（含 Guardrail 软化） | 不单独成子分，避免双计 |
| Causality | plot | 由 plot 子分承担 |
| Character | character | 同上 |
| Plot | plot | 已在 plot 子分内 |
| Pacing | pacing | 同上 |
| Style | style | 同上 |
| Foreshadowing | foreshadowing | 同上 |
| Payoff | foreshadowing | 与伏笔是一对耦合维度 |
| Originality | style（内嵌 0-10 子项） | 不再独立成子分 |
| Readability | style（内嵌 0-10 子项） | 同上 |

**Guardrail 触发的扣减规则（不破坏加权公式）**：

1. 任何 Guardrail 出现 `error` 级命中 → `overall = 0`，**绕过加权公式**（对齐 PRD §86 "严重失败不得 Commit"）。
2. 仅 `warning` 级命中 → `overall` 不变，但写入 `issues[]`，由人类 Author 决定是否放弃提交。
3. `info` 级命中 → 仅记录，不扣分、不阻断。

### 2.2 公式的版本化

- 公式版本号跟随 Quality Engine 版本号写入 Quality Score 输出的元数据侧：

```jsonc
{
  "overall": 86,
  "...": "...",
  "_meta": {
    "scoring_version": "quality-scoring-v0",
    "scoring_formula_hash": "<sha256 of this doc §2>",
    "judge_model_versions": {
      "style": "claude-...",
      "foreshadowing": "..."
    },
    "evaluated_at": "2026-08-23T12:00:00Z",
    "commit_id": "..."
  }
}
```

- 公式变更需更新 `scoring_version` 与 `_meta.scoring_formula_hash`，并触发一次完整 Regression（§5）。

---

## 3. 子分定义（6 维）

> 每个子分均给出：**测量来源 / 输入材料 / 计算流程 / 0-100 取值语义锚点 / 已知局限**。
> **MVP 收窄决策**（主会话 2026-08-23 已定，本节"硬门槛"对齐之）：MVP 阶段 timeline_consistency、knowledge_leakage 仅按 warning 级别运行，**不阻断 Commit**；V1 再升级为 error 级硬门槛。理由：MVP 阶段出于工程节奏收窄——`docs/state-model/knowledge-permission-v0.md` 与 TimeModel schema 的 enforcement pipeline 虽已具备承载位（`visibility` / `who_knows` 已落库），但端到端的检测通路（含 Context Engine 过滤执行顺序、Observer 校验流）仍在 Sprint 6 Quality 子模块中成型；若直接以 error 级阻断，会让 Sprint 4 全部 chapter 因检测通路未通而无法 Commit，违背 §112 硬性工程原则。

### 3.1 plot（剧情）

- **测量来源**：**混合**（rule-based + LLM judge）。
- **输入材料**：
  - `chapter_plan`（Outline、Scene 三问必要性是否可回答）；
  - `chapter_draft` 全文；
  - `state_snapshot_pre` + `state_snapshot_post`（剧情事件是否在因果链上）。
- **计算流程**：
  1. Rule-based 部分（权重 0.4）：
     - 计划事件数 vs 实际事件数（每缺失一个扣 5 分，每新增未授权事件扣 10 分）；
     - 每个 Scene 是否能用 PRD §39 三问必要性回答（每不可回答扣 8 分）；
     - Observer 给出的 `plot_event` Delta 是否在 Outline 因果链上。
  2. LLM judge 部分（权重 0.6）：
     - 在 rubric 上对**因果链完整性、冲突结构、反派逻辑、读者回响**分别按 0-10 打分；
     - 取四维平均，再乘 10 转 0-100；
     - 防刷分：rubric 每维给出"少扣/中/优"三档典型样例锚点（参考 §7）；**双评取低**（见 §7.3）。
- **0-100 锚点**：
  - `90+`：每 Scene 都能明确回答三问；因果链紧绷；冲突有递进；读后能复述主线。
  - `70`：主线清楚，但 1-2 个 Scene 三问勉强；至少一处因果跳步。
  - `50`：主线可见，但冲突平淡或反派动机薄弱；超过 3 个 Scene 无法回答三问。
- **已知局限**：LLM judge 对"读者回响"维度主观性强；MVP 阶段以双评取低对冲，V1 引入小规模人工一致性抽检（§4）。

### 3.2 character（人物）

- **测量来源**：**混合**。
- **输入材料**：
  - 所有出场人物的 Character Definition + 当前 State；
  - `chapter_draft` 中所有对人物（属性 / 关系 / 知识 / 情感）的引用切片。
- **计算流程**：
  1. Rule-based（权重 0.5）：每条引用切片与 Canonical State / Character Definition 做断言校验；命中率 × 100。
  2. LLM judge（权重 0.5）：rubric 维度 = 弧线推进、情感自洽、对白性格区分度、人物动机；每维 0-10 锚点打分，平均后 × 10；**双评取低**。
- **0-100 锚点**：
  - `90+`：每个出场的核心人物都有可观察的弧线推进；对白具备个性辨识度。
  - `70`：核心人物弧线可见，部分配角扁平。
  - `50`：主角行为基本一致，但至少 1 处显得"工具人"；或对白可互换。
- **已知局限**：配角覆盖完整度不可枚举；规则以主角 + 显式 mentions 的人物优先，配角缺失不扣分（不阻塞），仅 info 级 issue 记录。

### 3.3 continuity（连续性）

- **测量来源**：**rule-based 为主**（人/时/地/规则/知识 五类断言，PRD §37 Continuity 子项）。
- **输入材料**：
  - `state_snapshot_pre`（包含 TimeModel、LocationModel、CharacterState、WorldRule）；
  - `chapter_draft` 中所有事态变化切片；
  - Observer 给出的 Delta（含人物位置、时间戳、知识刷新）。
- **计算流程**：
  1. 基础分 100；
  2. 每条规则命中扣分：人物位置冲突 -25，生死冲突 -30，时间线先后冲突 -25，地点-动作匹配 -10，世界规则违反 -30，知识越界 -25；
  3. 扣分按"最差切片"取上限（一条规则多次命中按 1 次扣），不低于 0；
  4. warning 级命中扣半（×0.5），error 级命中按全额；
  5. MVP 收窄：timeline_consistency 与 knowledge_leakage 命中只走 warning 级扣半（详见 §4 收窄决策理由）。
- **0-100 锚点**：
  - `90+`：五类断言全部命中，无任何冲突被规则引擎报告。
  - `70`：1 条 warning，且未影响关键事件。
  - `50`：多条 warning，或 1 条非致命 error（如地点-动作不匹配）。
- **已知局限**：规则引擎依赖 State Model 的表达力；Sprint 4 落库前，continuity 子分只能对**已建模字段**生效（知识、时间跳变等不建模字段自然 100）。

### 3.4 style（文风）

- **测量来源**：**LLM judge 主导 + 少量 rule 辅佐**。
- **输入材料**：`chapter_draft` 全文 + 用户 StyleGuide（如有）。
- **计算流程**：
  1. Rule-based 辅佐（权重 0.2）：自动检测以下硬指标——
     - 句子平均长度（目标区间：用户 StyleGuide 设定；缺省 12-28 字）；
     - 高频 n-gram（trigram）重复率 > 8% 触发 `style_repetition` warning；
     - AI 味硬指标：过量排比、连续三个独立段落都以"然而/但是"开头等，明显模式触发 warning；
     - 对话占比（PRD 未规定，作者预设）。
  2. LLM judge 主体（权重 0.8）：rubric = 重复 / AI味 / 句式 / 对白 / 节奏（Sprint PRD §37 Style 五项）；每维 0-10，平均 × 10；**双评取低**。
- **0-100 锚点**：
  - `90+`：读起来像"作者的稿子"：句式有变化、对白利落、几乎没有可机械检测的 AI 模式。
  - `70`：个别段落略显模板化，但整体节奏稳。
  - `50`：2+ 段落有明显 AI 模式（"首先/其次/最后"、"不仅仅...更重要的是..."）。
- **已知局限**：AI 味检测易误报文学化排比；MVP 阶段以"双评 + 抽检"对冲；StyleGuide 缺失时退化为默认值。

### 3.5 pacing（节奏）

- **测量来源**：**LLM judge + rule-based 张力曲线**。
- **输入材料**：`chapter_draft` + `chapter_plan` 的 Scene 切分。
- **计算流程**：
  1. Rule-based 张力曲线（权重 0.4）：
     - 以 Scene 为粒度，提取"对话比例 / 动作密度 / 内心独白密度"三个数值；
     - 与 plan 中标注的张力等级序列做相关性（Pearson r）比对，r × 100 计入；
     - 缺张力等级标注时退化为均匀区间检查。
  2. LLM judge（权重 0.6）：rubric = 紧张-松弛交替、Scene 节拍、信息密度、收尾钩子；每维 0-10，平均 × 10；**双评取低**。
- **0-100 锚点**：
  - `90+`：每 Scene 都有可识别的张力变化；信息密度合理；结尾钩子明确。
  - `70`：整体节奏稳，但有 1-2 处连续松/紧。
  - `50`：节奏平铺或"高开低走"；钩子弱。
- **已知局限**：张力曲线与"文学意图张力"不完全等价；MVP 阶段以双评合一为锚。

### 3.6 foreshadowing（伏笔）

- **测量来源**：**混合**。
- **输入材料**：
  - `Hook Ledger`（PRD §21 / 并行文档 `docs/state-model/hook-ledger-v0.md`（待产出）；当前阶段回退到 `state_snapshot_pre.plot_event` 中的"伏笔类"事件）；
  - `chapter_draft` 中可识别的伏笔引用与兑现点。
- **计算流程**：
  1. Rule-based 兑现率（权重 0.5）：当期 chapter 涉及的钩子数 / 实际兑现或推进数 × 100；MVP 不兑现的长伏笔不扣分（info 级记录）。
  2. LLM judge 伏笔质量（权重 0.5）：rubric = 铺垫自然度、读者可回溯度、未兑现悬念密度；每维 0-10，平均 × 10；**双评取低**。
- **0-100 锚点**：
  - `90+`：每条已兑现的伏笔都能让读者回想"原来之前有提示"，铺垫不留痕。
  - `70`：兑现可见，但铺垫处 1-2 处过于刻意。
  - `50`：至少一条伏笔兑现突兀，或密度过低。
- **已知局限**：PRD §21 把伏笔作为关键；MVP 阶段 Hook Ledger（`docs/state-model/hook-ledger-v0.md` 待产出）使用降级路径（plot_event），其落地后即可切换至 Hook Ledger 主路径。

---

## 3.7 男频爽感维度（v0.1（v1.2 修订）增补，对齐 `docs/v1.2-调研综合与设计决策-2026-08-23.md` D4）

> 本节为 v1.2 调研综合文档 D4 的落地：把"好看"的手艺从玄学拆为可机检的爽感指标，纳入 Quality Engine 作为"爽感体检报告"的固定组成部分。
> **本节所有阈值均为"建议值，待校准"**：必须经首批番茄男频 golden + Top-10 爆款回放后方可固化。

| # | 指标 | 测量来源 | 建议阈值（待校准） | severity 默认 | 说明 |
|---|---|---|---|---|---|
| H-1 | **章末钩子检出率** | rule-based（末段 N 字内疑问/悬念/反转标记词 + LLM judge 双评核验） | ≥ 85% 的 Scene 结尾含钩子标记 | warning（< 70% 升 error） | 锚定参照书 chapter_end_hook_rate；未达标章节写入 issues 并建议改写末段。 |
| H-2 | **爽点密度**（三章小高潮/五章大高潮拟合） | 混合（rule-based 拟合曲线 + LLM judge 标注小/大高潮点） | 三章滑动窗口内至少 1 个小高潮（intensity ≥ 3）拟合度 ≥ 0.7；五章滑动窗口内至少 1 个大高潮（intensity ≥ 4）拟合度 ≥ 0.7 | warning | 与参照书 `rhythm.mini_climax_interval` / `major_climax_interval` 的中位数做比对；偏离过大则提示。 |
| H-3 | **连续水章预警** | rule-based（连续 2 章内无 payoff_list 命中且 valence 区间 [-2, +2]） | 连续 2 章无爽点 → warning；连续 3 章无爽点 → error | warning / error | 写入 issues 时附"建议插入爽点位置"。 |
| H-4 | **黄金三章专项**（新书期模式） | 混合（前 300 字文本扫描冲突词 + 章末钩子检出 + 三章内小高潮位置） | 三项全达标：前 300 字冲突 / 三章钩子 / 三章内首次小高潮 | warning（新书期）/ info（其他） | 仅当 chapter_index ∈ [1, 3] 时启用；不达标触发爽感体检报告专列。 |
| H-5 | **战力与境界递进一致性** | rule-based（境界名词一致 + 越级碾压检测） | 同一境界词跨章引用 100% 一致；不允许跨大境界越级碾压无代价 | warning（一致性问题）/ error（越级碾压） | 境界名词一致：抽取本章新引入境界词与 Canonical State 比对；越级碾压：比对`opponent_layer`与`protagonist_layer`差距，差距 ≥ 2 个大层且无代价标记 → error。 |

**集成方式**：

- H-1 至 H-5 的 issues 写入 `issues[]`，`category` 字段统一为新增枚举 `payoff`；
- 爽感体检报告作为 `chapter_review` 的固定 section（与 plot/character/continuity/style/pacing/foreshadowing 并列），由 Quality Engine 在 Commit 前渲染；
- 不进入 §2.1 加权公式（避免双计）；仅以 issues 形式暴露。

**MVP 收窄决策**：本节五项指标 MVP 阶段全部以 **warning** 级运行（仅 H-3 的"连续 3 章无爽点"与 H-5 的"越级碾压"按 error）；V1 与 PRD v1.2 §86 升级路径联动，再视情况调整阈值与 severity。

---

## 4. Guardrail 量化阈值（PRD §86）

每条 Guardrail 的判定分三档：**通过 / warning / 阻断（error）**。

> **MVP 收窄决策**（主会话 2026-08-23 已定，须在文档中固化）：MVP error 级阻断 = 三条硬门槛（§4.1/§4.3/§4.4）+ 合规阻断 §4.6(REQ-Q6)、§4.8(REQ-Q8)，Q6/Q8 自启用即为阻断级（与 PRD REQ-Q6~Q8 条目口径一致）；§4.7(REQ-Q7) MVP 为 warning。
> **理由**：本阶段 Quality 子模块在 Sprint 6 才落地，`docs/state-model/knowledge-permission-v0.md` 的 `visibility` / `who_knows` 承载位虽已落库，但端到端检测通路（含 Context Engine 过滤顺序、Observer 校验）仍在 Sprint 6 整合；若直接以 error 级阻断，会让 Sprint 4 的所有 chapter 因检测通路未通而无法 Commit，违背 §112 硬性工程原则。V1 升级时仅需把这一节的 status 行从 `mvp: warning` 改为 `mvp: error`，无须改动公式。

### 4.1 schema_validity

| status | 规则 |
|---|---|
| **error** | `chapter_plan` / `chapter_draft` / Observer Deltas 任一 JSON Schema 校验失败；缺失必填字段。 |
| **warning** | 全部字段合法，但含 `additionalProperties` 之外的未声明字段。 |
| **pass** | 严格通过 JSON Schema（参见已落地的 `docs/state-model/state-delta-v0.md` 中各 Delta / Commit Record 的 schema 章节）。 |

- **检测点**：JSON Schema 校验器（基于 AJV 或等价库）的零失败。
- **判定语义**：`error` ⇒ `overall = 0` 强制阻断 Commit（§2.1 规则 1）。

### 4.2 timeline_consistency（**MVP: warning**）

| status | 规则 |
|---|---|
| **error**（V1 起生效） | 同一 Story Time 上某人物同时处于两个互斥地点、同一人物两段时间标记交叉矛盾、跨度超出已声明规则。 |
| **warning**（MVP） | 同上规则命中，但仅写入 `issues[]`，不阻断。 |
| **pass** | TimeModel 中所有 timestamp 严格单调（人物级、世界级各自 check）。 |

- **检测点**：规则引擎（基于已落地的 `docs/state-model/state-delta-v0.md` 中 TimeModel 章节定义生效）。
- **MVP 收窄边界**：MVP 阶段即便 `docs/state-model/state-delta-v0.md` 的 TimeModel schema 已落地，本规则仍采用"白名单豁免"——只对已建模字段生效；缺字段不报告，避免过度阻断。

### 4.3 character_contradiction

| status | 规则 |
|---|---|
| **error** | 角色生死矛盾（已死亡角色说话/行动）、核心属性突变（无 Delta 解释）、年龄前后矛盾超 5 年（PRD 未约定阈值，本设计决策为 5 年，需首轮 golden 校准）。 |
| **warning** | 人物小属性弹性变化（如"累了"→"轻微疲倦"，允许模糊量级）、跨章内同一时长内的体力波动（同 PR 内 micro-loop）。 |
| **pass** | 角色所有引用全部命中 Canonical State。 |

- **检测点**：规则引擎以 `CharacterState` 当前快照为唯一参照。
- **MVP 直接进入 error 级**（同 §0 收窄决策）。

### 4.4 world_rule_contradiction

| status | 规则 |
|---|---|
| **error** | 违反 `WorldRule` 表中 `hard = true` 的规则（魔法体系约束、地理限制、不可逆事件）。 |
| **warning** | 违反 `soft = true` 的规则（如"普遍但不绝对"的世界惯例）。 |
| **pass** | 全部 WorldRule 一致。 |

- **检测点**：规则引擎 + `WorldRule` 表的 `severity` 字段（参见并行数据模型文档）。
- **MVP 直接进入 error 级**。

### 4.5 knowledge_leakage（**MVP: warning**）

| status | 规则 |
|---|---|
| **error**（V1 起生效） | 角色获知不应知道的信息且该信息影响剧情（PR/Plot Event 类剧情级钩子）。 |
| **warning**（MVP） | 同上命中；不阻断。 |
| **pass** | 所有 `who_knows` 引用通过 Knowledge Permission 模型（见 `docs/state-model/knowledge-permission-v0.md`）。 |

- **检测点**：Context Engine 的 visibility 过滤（参见 `docs/state-model/knowledge-permission-v0.md` 的 enforcement pipeline 概念） + Observer Delta 检查。
- **MVP 降级**：即便 `docs/state-model/knowledge-permission-v0.md` 的 `visibility` / `who_knows` 承载位已落库，本规则仍对未声明 visibility 的字段**默认 pass**，仅对已声明 `who_knows` 的字段做严格校验——避免检测通路未通时假阳性。
- **V1 升级触发条件**：端到端 Knowledge Permission 检测通路在 Sprint 6 Quality 子模块整合完成 + Hook Ledger（`docs/state-model/hook-ledger-v0.md` 待产出）落地。

### 4.6 REQ-Q6 参照书相似度（v0.1（v1.2 修订）增补，对齐 D2 G-sim）

| status | 规则 |
|---|---|
| **error** | `chapter_draft` 与项目挂载的全部参照书 `ReferenceCanon` 产出之间文本 n-gram 重叠率 > 2% 或单段 embedding 相似度 > 0.92；检测节点挂 Commit Gate 前。 |
| **warning** | 任意一处连续 13 字以上与参照书重合或单段相似度 > 0.85；写入 issues，不阻断。 |
| **pass** | 双轨检测均低于阈值（详见 `docs/reference-canon/reference-canon-v0.md` §5）。 |

- **检测点**：Quality Engine 内嵌相似度模块，对照 `docs/reference-canon/schemas/reference-canon.schema.json` 落地的参照系与当前 draft 文本。
- **MVP 收窄**：MVP 阶段阈值采用"建议值"（13 字 / 2% / 0.85 / 0.92），待首批 golden + Top-10 番茄男频回放校准；error 级硬门槛，但允许工程团队通过白名单豁免共用网文套话。
- 命中时写入 issues[]，category=compliance。

### 4.7 REQ-Q7 AI 痕迹自检（v0.1（v1.2 修订）增补，对齐 D2 G-ai，MVP: warning）

| status | 规则 |
|---|---|
| **error**（V1 起生效） | AI 痕迹综合得分超阈值且全章无任何人工修订字符。 |
| **warning**（MVP） | 困惑度 / 句式分布 / AI 标记词频三项任一超阈值；写入 issues 但不阻断，发布前必须人工确认。 |
| **pass** | 三项均低于阈值。 |

- **检测点**：对标番茄平台 AI 检测自称 93% 准确率的公开方法——困惑度（perplexity）/ 句式分布熵 / 高频 AI 标记词（如"首先/其次/最后""不仅…更重要的是"）频次。
- **触发动作**：warning 级命中时，Workbench 弹窗提示作者"建议人工复核章节 AI 占比"；error 级命中强制走 Human Node（PRD §87）。
- **MVP 定位**：v1.2 决策将 G-ai 在 MVP 阶段固化为 warning（不阻断），仅作提示与人工复核触发；error 级于 V1 启用。
- 命中时写入 issues[]，category=compliance。

### 4.8 REQ-Q8 人工加工占比（v0.1（v1.2 修订）增补，对齐 D2 G-human）

| status | 规则 |
|---|---|
| **error** | 章节人工加工占比 < 30%（按 AI 生成 / 人工修改字数比统计）。 |
| **warning** | 占比 30%-40%（接近红线）。 |
| **pass** | 占比 ≥ 40%（留足安全余量）。 |

- **统计口径**：按章节记录 `ai_generated_chars` / `human_modified_chars` / `total_chars`，统计 `human_modified_chars / total_chars`；人工修订包括作者手动修改 / 增删 / 重写 / 合并段落等任何状态从 `generated` 流转至 `human_edited` 的字符。
- **后台可查可导**：Workbench 提供章节级 / 项目级人工加工占比面板；支持导出 CSV 自证合规（应对番茄平台审核）。
- **红线条款**：番茄 2026 治理口径——AI 辅助内容须 ≥ 30% 人工加工；AI 创作占比超 30%-40% 判违规。本 v0 采用 30% 作为 error 红线、40% 作为建议目标值（高于红线 10pp 留缓冲），待首轮校准后调整。
- 命中时写入 issues[]，category=compliance。

### 4.9 Guardrail 与 Scoring 的衔接

- 任意 Guardrail `error` ⇒ `overall = 0` ⇒ 强制阻断 Commit（绕过 §2 加权）；
- 仅 Guardrail `warning` ⇒ 子分（plot / character / continuity）按 §3.3 流程扣半，并写入 `issues[]`；不阻断；
- Guardrail `info` ⇒ 无分；只写 issues。

---

## 5. Ground Truth 数据集规范（PRD §84）

### 5.1 Sprint 1 内置规模与目录布局

主会话已定：**Sprint 1 内置 5 章 golden**，覆盖 Story State First（PRD §4 原则 1（REQ-P1））原则的可重复验证最小集。

```
docs/evaluation/golden/
├── README.md                          # 数据集说明、版本、变更记录
├── chapter_01_to_05/
│   ├── chapter_01/
│   │   ├── chapter.golden.md          # 正文（golden）
│   │   ├── chapter.plan.json          # 计划（golden：scene 三问已回答）
│   │   ├── state.pre.json             # 提交前状态快照（golden）
│   │   ├── state.post.json            # 提交后状态快照（golden）
│   │   ├── state.delta.golden.json    # Observer 应给出的 Delta（golden）
│   │   └── expected_score.json        # 预期子分 + issues（golden）
│   ├── chapter_02/  …  (同上)
│   ├── chapter_03/  …
│   ├── chapter_04/  …
│   └── chapter_05/  …
├── continuity_cases/
│   ├── contradiction_character_lives.md
│   ├── contradiction_timeline_loop.md
│   ├── contradiction_world_rule_magic.md
│   ├── knowledge_leakage_hidden_identity.md
│   └── schema_missing_required_field.md
└── scoring_rubrics/
    ├── style_rubric.md                # LLM judge 时使用的样例锚点
    ├── pacing_rubric.md
    └── foreshadowing_rubric.md
```

字段说明：

- `chapter.golden.md`：在 Workflow 不可用时也能产出的纯人工撰写的"参考稿"，用于做写作风格对齐与 LLM judge ground truth。
- `expected_score.json`：

```jsonc
{
  "overall": 88,
  "plot": 90, "character": 89, "continuity": 95, "style": 80, "pacing": 84, "foreshadowing": 90,
  "issues": [
    {
      "severity": "info",
      "category": "style",
      "location": "chapter_03:scene_02",
      "rule_id": "RULE_STYLE_REPETITION_TRIGRAM",
      "message": "trigram X 出现 3 次",
      "suggestion": "考虑替换其中 1 处"
    }
  ]
}
```

### 5.2 标注规范

**谁标**：

- **主标注人**：项目内 Quality Lead（单人产出黄金值）；
- **校核人**：另一名资深 Author / Reviewer，对每章黄金值做 100% 二审；
- **仲裁**：标注差异 ≥ 10 分（任一子分绝对差 ≥ 10）时由 Quality Lead + Reviewer 联合二轮定锚，差异以最终标注为准，差异过程作为 `decisions.md` 条目留档。

**标注字段**（每章）：

| 字段 | 类型 | 来源 |
|---|---|---|
| `overall`, 6 个子分 | int [0,100] | 标注人按 §3 锚点给出 |
| `issues[]` | 数组 | 标注人按 §1.3 结构给出，可为空 |
| `_meta.annotated_by` | string | 主标注人标识 |
| `_meta.reviewed_by` | string | 校核人标识 |
| `_meta.commit_id_at_annotation` | string | 标注锚定的 commit |
| `_meta.scoring_version` | string | 标注时使用的规范版本（= "quality-scoring-v0"） |

**一致性抽检（每轮 golden 维护时执行）**：

- 抽 1 章做 Cross-Annotation（第三方盲标），计算 Krippendorff α（标注人间一致性）要求 ≥ 0.7（按子分维度），低于则触发 rubric 重写；
- 抽 `continuity_cases/` 的对抗样例至少 2 个跑一遍当前 Quality Engine，确认 Guardrail 命中且 severity 正确。

### 5.3 与 PRD §84 四类 golden 的对应关系

| PRD §84 类别 | 本规范落地 |
|---|---|
| golden chapters | `chapter.golden.md` + `expected_score.json` |
| golden character states | `state.pre.json` 与 `state.post.json` 中的人物 sub-object；另由 `golden/character_canon/` 文件维护人物圣经 |
| golden plot states | `state.pre.json` 与 `state.post.json` 中的 plot sub-object；`state.delta.golden.json` 表示过渡 |
| golden continuity cases | `continuity_cases/` 5 类对抗样例（每类至少 2 个变体），用于 Guardrail 单测 |

---

## 6. Regression 评测流程（PRD §85）

### 6.1 触发条件

变更满足以下任一项时必须跑 Regression：

- 任意 **Prompt** 文件改动（`docs/agents/prompts/*.md` 或在 Sprint 4 后变更的等价位置）；
- 任意 **Agent** 注册表变更（新增 / 删除 / 输入输出契约变更）；
- 任意 **Model** 路由调整（`docs/agents/model-router-*.md` 及 PRD §71 对应模块）；
- 任意 Quality / Scoring / Guardrail 规则变更；
- `_meta.scoring_formula_hash` 变更。

> PRD §84"每次 Prompt / Agent / Model 修改都运行 Regression Eval" —— 本节是该原则的可执行化。

### 6.2 流程（伪代码）

```text
function run_regression(change_event):
    # Step 1: 装载基线（最近一次通过且已上线的 overall/子分与 Guardrail 通过率）
    baseline = load_from("docs/evaluation/baseline/last_passing_run.json")
    cases = ["golden/chapter_01_to_05/*", "continuity_cases/*"]

    # Step 2: 对每个 case 跑 Workflow（端到端，可消费缓存 commit_id）
    results = []
    for case in cases:
        # 用 case 的 expected_score 作为 ground truth
        actual = eval_quality(case, current_prompts, current_model_router)
        diff = compare(actual, case.expected_score, deltas=current-baseline)
        results.append({case, actual, diff})

    # Step 3: 聚合与判定
    aggregate = aggregate_results(results)

    # Step 4: 上线判定（§6.3 显式条件）
    decision = decide_release(baseline, aggregate)

    # Step 5: 落盘报告
    write_report("docs/evaluation/runs/<run_id>.json", {baseline, results, aggregate, decision})
    return decision

function decide_release(baseline, aggregate):
    # PRD §85：Overall + Guardrails 双条件
    overall_ok  = (abs(aggregate.overall - baseline.overall) <= TOLERANCE_OVERALL)   # 默认 2 分
                 and (aggregate.overall >= MIN_OVERALL_FOR_RELEASE)                    # 默认 70（§7）
    guardrail_ok = (
        aggregate.guardrails.schema_validity          == "pass"
        and aggregate.guardrails.character_contradiction   == "pass" or "warning"
        and aggregate.guardrails.world_rule_contradiction == "pass" or "warning"
        and aggregate.guardrails.req_q6_similarity_check_ok     # §4.6 REQ-Q6 MVP 即为阻断级
        and aggregate.guardrails.req_q8_human_ratio_check_ok   # §4.8 REQ-Q8 MVP 即为阻断级
        # timeline / knowledge_leakage V1 阶段纳入；MVP 仅写入报告（§4 收窄）
        and aggregate.guardrails.timeline_consistency_v1_check_ok      # 即便 warning 也不阻断
        and aggregate.guardrails.knowledge_leakage_v1_check_ok
    )
    no_regression_flag = (
        aggregate.plot           >= baseline.plot - 3 and
        aggregate.character      >= baseline.character - 3 and
        aggregate.continuity     >= baseline.continuity - 3 and
        aggregate.foreshadowing  >= baseline.foreshadowing - 3
    )
    # §85 的核心反例：新版文笔评分（style）提高，但人物一致性（character）下降也不能上线
    # 所以"character / continuity / foreshadowing 任一跌破 baseline - 3"即阻断
    if not overall_ok:
        return BLOCK("overall 子分不达标或波动超过容差")
    if not guardrail_ok:
        return BLOCK("Guardrail 命中 error")
    if not no_regression_flag:
        return BLOCK("关键子分（character/continuity/foreshadowing）相对基线下降 > 3")
    return PASS()
```

### 6.3 "不能直接上线"的显式判定条件（PRD §85 落地）

满足以下**任一**条件即阻断（即使 `overall` 看上去更高）：

1. **Overall ≤ MIN_OVERALL_FOR_RELEASE**（默认 70，§7）；
2. **Overall 与 baseline 绝对差 > TOLERANCE_OVERALL**（默认 2 分）；
3. **任意 Guardrail 命中 error**（MVP 阶段 schema_validity / character_contradiction / world_rule_contradiction 三条 + 合规阻断 REQ-Q6 / REQ-Q8）；
4. **关键子分（character / continuity / foreshadowing）相对 baseline 下降 > 3**（对齐 §85 反例："文笔评分提高但人物一致性下降不能上线"——把 style 提升视为中性，把 character / continuity / foreshadowing 视为硬约束子分）；
5. **任意 Guardrail 的 hit_rate 较 baseline 上升 > 10%**（即使单次没命中 error，但频次恶化也阻断）。

### 6.4 报告落盘

- 每次 Regression 必须在 `docs/evaluation/runs/<run_id>.json` 落盘，含 baseline、各 case actual、aggregate、decision、变更 diff 引用、执行的 commit_id；
- `last_passing_run.json` 是上线流水线的输入：仅当上一步 `decision = PASS()` 时才更新为本次 run。

---

## 7. 基线与目标值（建议值，需校准）

> **本节所有数字均为"建议值"，必须由首批 golden 章节 + 实测校准后方可作为"默认值"写入 Quality Engine 启动配置**。PRD 未规定各项初始基线，本设计决策给出经验起点。

### 7.1 MVP 启动基线（建议）

| 指标 | 建议默认值 | 说明 |
|---|---|---|
| 整体 `overall` 起步 | ≥ 70 | 留给 LLM 写作质量自然波动 |
| 各子分起步 | ≥ 65 | 防止 style 波动掩盖关键子分 |
| Guardrail 阻断率 | 0%（MVP 强制 error 的 3 条） | 任何命中都要修 |
| Regression 容差 TOLERANCE_OVERALL | 2 | 单次整体波动容忍 |
| 关键子分回归阈值 | −3 | §6.3 条件 4 |
| Guardrail 频次恶化阈值 | +10% | §6.3 条件 5 |
| LLM judge 双评一致性 | Krippendorff α ≥ 0.6 | 起步，V1 提升到 0.7 |

### 7.2 V1 目标值（建议）

| 指标 | V1 目标 |
|---|---|
| 整体 `overall` | ≥ 80 |
| 关键子分 | character / continuity / foreshadowing ≥ 85 |
| LLM judge 一致性 | Krippendorff α ≥ 0.7 |
| Golden chapter 覆盖率 | ≥ 8 章（含至少 1 章纯人工撰写的 golden 用以校准 LLM judge） |
| Guardrail V1 全量 | schema/timeline/character/world/knowledge 五条全部 error 级 |

### 7.3 防刷分措施（针对 LLM judge 子分：style / pacing / foreshadowing / plot LLM 部分 / character LLM 部分）

- **Rubric 锚点化**：每维给出"少扣 / 中 / 优"三档各 1 个 50-120 字样例（典型 fragment），judge 模型按样例比对打 0-10，避免打分漂移；
- **双评取低**：每个 LLM judge 子分都用 2 个独立模型实例（model_a、model_b，MVP 可同模型不同 seed）独立打分，最终取 min，再平均为子分；差距 > 3 时触发人工仲裁；
- **盲化输入**：judge 看不到任何关于 model_router / prompt version 的元信息，仅看到 §1.2 四个输入快照；
- **历史锚定**：每个 judge prompt 在 v0 锁定后产出 baseline 报告，新 judge prompt 必须先回放 baseline 跑一遍，确认打分分布在 ±2 分内才允许接入主流程。

---

## 8. 与 PRD 的映射

| PRD 节 | 本文覆盖位置 | 备注 |
|---|---|---|
| §37 Quality Engine 四类检查 | §3.1-§3.6 六子分；§4 五 Guardrail | Structural → §4.1 schema；Narrative → §3.1 plot / §3.6 foreshadowing；Style → §3.4 style / §3.5 pacing；Continuity → §3.3 + §4.2-4.5 |
| §38 Quality Score 结构 | §1 字段；§2 公式 | 字段名、顺序、类型严格沿用 §38 |
| §82 十维质量目标 | §2.1 权重理由表 | 十维 → 六子分的归并解释 |
| §83 成功指标 | §7 基线 | 产品指标不在本文；AI 指标中的人物/剧情/世界观/伏笔/时间线/重复/AI 味 → 覆盖到子分与 Guardrail |
| §84 Evaluation Dataset | §5 Ground Truth 规范 | 目录布局、标注规范、一致性抽检、与四类 golden 的对应 |
| §85 Regression 原则 | §6 评测流程 | PRD §85 反例 → §6.3 条件 4 |
| §86 Guardrail 五硬门槛 | §4 量化阈值 | schema / timeline / character / world / knowledge 全部三档化 |
| §110 MVP 验收 | §4 收窄决策 | 仅 schema_validity / character_contradiction / world_rule_contradiction 在 MVP 阻断 |

---

## 9. Open Questions

> 以下问题在本规范 v0 内已采用"建议默认值"先行落地，但尚未闭环；任一项落实都会触动 §2 / §4 / §6 / §7 的数字与流程，必须在 v0 → v1 的版本升级中处理。

1. **OV-1**：`docs/state-model/knowledge-permission-v0.md` 的 `who_knows` / `visibility` 承载位虽已落库，但端到端 enforcement pipeline（含 Context Engine 过滤执行顺序、Observer 的越界校验流）何时在 Sprint 6 Quality 子模块整合完成？——这是 §4.5 在 MVP 后续升级为 error 的前提。
2. **OV-2**：Hook Ledger（`docs/state-model/hook-ledger-v0.md` 待产出）何时落地、并成为 Foreshadowing 子分（§3.6）的唯一数据源？当前 §3.6 使用降级路径（plot_event），待 Hook Ledger 落地后切换。
3. **OV-3**：PlotSimulation（PRD §90）是否需要单独的"模拟态 Quality Score"？当前 §3 算法仅针对生产态 draft；模拟态是否需要"分支质量分"暂未决。
4. **OV-4**：LLM judge 模型路由由谁配置？当前未指定 model_a / model_b。Proposal：在 `docs/agents/model-router-v0.md`（待产出）落定后回填。
5. **OV-5**：Rubric 的"少扣/中/优"三档样例（§7.3）由谁撰写？需 Quality Lead + 编辑团队联合产出，本 v0 不含样例正文。
6. **OV-6**：Golden 章节是否覆盖多种题材（玄幻/都市/科幻）以避免 rubric bias？v0 的 5 章建议以 1 部作品的连续 5 章实现，跨题材覆盖放在 v1 增补。
7. **OV-7**：Regression 是否纳入 Production 遥测指标（章节完成率、用户继续写作率，PRD §83 产品指标）？当前 §6.3 仅覆盖评估态；产品指标由 Sprint 5+ 的 Workbench 遥测模块负责，本文暂不混并。
8. **OV-8**：continuous integration / CD 流水线何时把 §6.4 报告落入 git history？现在 §6.4 仅要求落 `docs/evaluation/runs/`，是否要作为 commit 内部附件待定。
9. **OV-9**：v0 落地后的第一轮实际校准用什么数据回放？是用"人工 chapter.golden.md 本身 + 标注 backfill"，还是用历史生产数据脱敏回放？提案：用人工 golden 优先以校准 LLM judge；用生产数据回放以发现回归盲区。两批都必须做。

---

## 10. 风险

| # | 风险 | 等级 | 应对 |
|---|---|---|---|
| R1 | 权重 / 阈值"建议值"被当默认值使用、未校准即固化 | 重要 | §7 标红；上线前必须跑 OV-9 第一轮校准 |
| R2 | LLM judge 分数漂移导致 Regression 误报 | 重要 | §7.3 双评取低 + judge prompt hash；定期盲抽 |
| R3 | Knowledge Permission 落地延迟，§4.5 升级为 error 被迫推迟 | 中 | MVP 收窄已对齐；OV-1 闭环后即可升级 |
| R4 | Golden 5 章与生产态章节差异大，Regression 跑"过拟合" | 重要 | v1 增补跨题材；引入 continuity_cases 对抗样例 |
| R5 | Sprint 6 Quality 子模块实现延迟，导致本文档"暂时无主" | 致命 | Quality Lead 在 Sprint 6 启动前 1 周接管本文作为验收依据 |
| R6 | 已落地的 `state-delta-v0.md` / `knowledge-permission-v0.md` 在字段名上与本文 §1.2 / §4.5 的引用不一致 | 中 | v0 发布前做字段名一致性 diff review，主会话协调 |
| R7 | Continuity 规则引擎在 PRD §114"七问"中若干新 State 字段未到齐前误报或漏报 | 中 | §3.3 已知局限已说明；规则严格按"已建模字段"生效，未建模字段默认 pass |

---

## 附录 A：版本记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0 | 2026-08-23 | 首版；与 PRD §37/§38/§82/§83/§84/§85/§86/§110 + 评估报告 R7/R9 对齐；MVP 收窄决策固化 |
| v0.1 | 2026-08-23 | 增补 §3.7 男频爽感维度（H-1~H-5 五项指标：章末钩子检出率/爽点密度/连续水章预警/黄金三章专项/战力境界递进一致性）；§4.6/§4.7/§4.8 新增 REQ-Q6 参照书相似度 / REQ-Q7 AI 痕迹自检 / REQ-Q8 人工加工占比三条 Guardrail（对齐 `docs/v1.2-调研综合与设计决策-2026-08-23.md` D2 G-sim / G-ai / G-human）；§4.x 编号顺延。原 §4.6 Guardrail 与 Scoring 的衔接 → §4.9。本版所有阈值均为"建议值，待校准"。 |
