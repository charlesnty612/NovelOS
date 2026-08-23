# `prompts/external/bishu-reference/` — AGPL-3.0 隔离目录

> 用途：**纯私有使用**场景下，借参考考自 bishu-novel（DeterminFlow-Plugins，AGPL-3.0）的 Prompt 参考文本存放处。
> 对齐：PRD §103「许可证策略」+ 调研综合文档 D7 裁决 + `docs/reference-canon/reference-canon-v0.md` §1.3 AGPL 与外部资产策略。
> 创建日期：2026-08-23
> 状态：**空目录**（尚无文件）；本目录必须在引入任何借鉴文件前先理解 §2 四条规则。

---

## 1. 目录目的

NovelOS 借鉴 bishu-novel（DeterminFlow-Plugins，AGPL-3.0 已实地确认）的**架构思想**——如观察-裁决分离（Observer / Arbite / post-hoc 模式）、六维世界观、节奏方法论——通过**自行重新实现**落地，**禁止**直接复制其 `prompts.json` / `definition.json` / `world.json` 等资产文本。

但 PRD §103 明确允许"纯私有使用（不分发、不提供服务）时**可以**参考或临时借用其 prompt 文本"——copyleft 义务**不**在"参考"环节触发，只在"分发软件"或"对外提供网络服务"环节（含 AGPL-3.0 §13 网络交互条款）触发。

**本目录即承载这种"纯私有、临时参考"用途**：把 bishu-novel 的 prompt 原文**隔离存放**，作为人工阅读 / 重写时的对照参考；任何 Canonical Prompt 一律自写（落地于 `docs/agents/prompts/*.md`），**绝不**直接搬用。

---

## 2. 四条规则（强制）

### 规则 ① — 来源标注

每个放入本目录的文件**头部必须**包含如下元信息块（注释形式，文件类型不限）：

```text
---
source_project: bishu-novel（DeterminFlow-Plugins）
source_repo_url: <上游仓库地址，访问/下载时填入>
source_license: AGPL-3.0
obtained_date: <YYYY-MM-DD>
obtained_by: <获取人/Agent>
intended_use: 仅作人工参考对照（私有使用），不进入 Canonical Prompt
novelos_action: 引入后必须改写为自研版本，Canonical Prompt 一律放 docs/agents/prompts/
---
```

**未标注来源的文件 = 视为未授权引入，必须立即移除**。

### 规则 ② — 运行时隔离

本目录内容**禁止**被以下任何运行时 prompt 加载链路直接引用：

- Workflow Runtime / State Committer 在 commit 阶段注入的字段；
- Deconstructor / Director / Planner / Writer / Observer 等任何 Agent 的 Prompt 拼接；
- 测试 / mock 数据加载路径；
- 构建产物 / 打包脚本。

本目录**仅供人工阅读、改写、自研对照**。**Canonical Prompt 一律自写**，落地于 `docs/agents/prompts/` 与 `docs/agents/agent-contracts-v0.md` 等 Canonical 路径。

### 规则 ③ — 分发前清除

AGPL-3.0 的 copyleft 触发点是**分发软件**或**对外提供网络服务**，**非**商用（PRD §103 已明确）；NovelOS 一旦进入以下任一动作前，**必须整目录清除或重写**：

- 发布 NovelOS 源代码 / 二进制 / Docker 镜像 / 任意包（含公司内部分发、GitHub 公开、npm/pip 发布等）；
- 部署 NovelOS 作为对外 SaaS / 网络服务（含公司内网多人共用、demo 服务、API 公开等）；
- 把 NovelOS 整合进任何衍生作品 / 二次发行版；
- 任何形式对外展示（含截屏、博客贴图、教程引用时引用了原文 prompt 段落）。

清除要求：

1. 删除本目录**全部**文件（含 README 本身可保留作审计痕迹，但 §4 登记表清空）；
2. 在 `docs/reference-canon/reference-canon-v0.md` 附录 A 版本记录追加"AGPL 清除"事件；
3. 通过 grep / find 验证项目内已不存在借鉴文本的连续 ≥8 字重合（G-sim 同口径）。

### 规则 ④ — 登记表维护

任何文件放入本目录**必须**在 §4 登记表中追加一行（文件名 / 来源 / 日期 / 用途）。登记表与文件系统**双向对账**：每次提交时检查"文件 ↔ 登记行"一一对应；任一侧缺失立即修复或移除文件。

---

## 3. 借什么 / 不借什么

| 类别 | 是否允许放入本目录 | 说明 |
|---|---|---|
| bishu-novel prompt 原文（仅人工参考） | ✅（必须隔离） | 必须人工阅读后改写为自研版本；Canonical Prompt 一律自写 |
| bishu-novel definition / world json | ✅（必须隔离） | 同上，仅参考其结构 |
| bishu-novel 代码（Python / TS） | ❌（推荐不放） | 代码复制判定更严，建议直接重新实现 |
| oh-story-claudecode 任何资产 | ⚠️（须先查 LICENSE，未查实前不放） | PRD §103 + D7：许可证未核实，不引入其任何字段命名 |
| 第三方拆书工具 prompt 原文 | ⚠️（先查许可证 + 放本目录） | 任何 AGPL 系 / 强 copyleft 系必须按本规则隔离 |
| NovelOS 自研 prompt | ❌（不放） | Canonical Prompt 走 `docs/agents/prompts/`，不混入本目录 |

---

## 4. 文件登记表

> 双向往返账：每放入一个文件必须在此追加一行；移除时同步删除该行。每次提交前由 main-agent / 审查者交叉核对。

| # | 文件名 | 来源项目 | 许可证 | 获取日期 | 用途 | 状态 |
|---|---|---|---|---|---|---|
| — | （暂无） | — | — | — | — | — |

---

## 5. 自检命令（放入文件后人工或审查脚本执行）

```bash
# 1) 列目录全部文件，逐个头部必须有来源标注
ls prompts/external/bishu-reference/

# 2) 校验 Canonical Prompt 路径未引入本目录
grep -rE "prompts/external/bishu-reference" docs/agents/prompts/ docs/agents/agent-contracts-v0.md
# 期望：零命中（命中则违规，必须移除引用）

# 3) 校验 Canonical Prompt 不含 AGPL 借鉴原文（连续 ≥8 字重合扫描）
# （与 reference-canon-v0.md §5 G-sim 轨道 A 同口径）
```

---

## 6. 相关引用

- PRD §103「许可证策略」—— 使用策略 v1.2 主会话裁决三条
- `docs/reference-canon/reference-canon-v0.md` §1.3「AGPL 与外部资产策略」
- `docs/v1.2-调研综合与设计决策-2026-08-23.md` D7「许可证与知识产权边界重申」
- `docs/agents/prompts/deconstructor-v0.md` —— Canonical 拆书 Prompt 自写落地（不引用本目录）