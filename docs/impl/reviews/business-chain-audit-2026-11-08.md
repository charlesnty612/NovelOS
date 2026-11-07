# 业务链 / 业务逻辑审计报告（2026-11-08）

> 触发：用户指令「检查现有业务链/业务逻辑是否存在问题，比如设计过度等等」。
> 方法：6 条业务链并行测绘 × 8 个镜头发掘 × critical/major 三镜头对抗验证 × 完整性批评，
> 共 135 个只读审计 agent、6243 次工具调用。**零真实 LLM 调用、零内容仓接触、活库业务数据零改动。**
> 裁决纪律：主会话对全部 major 结论**逐条亲读代码复核**；agent 的实测数字若未经我复算，
> 一律标注「agent 实测，主会话未复算」。

---

## 一、口径与总量

| 项 | 数 |
|---|---|
| 去重后候选 | **103** |
| 进入对抗验证（critical/major） | **40** |
| 确认（≥2/3 镜头未驳回） | **17** |
| 驳回 | **23** |
| 未验证（minor/info，或超出验证上限） | **63** |

三镜头 = ①代码事实核验（逐字打开被引代码）②影响与可达性（单用户本机能否触发、有无既有守卫）
③反方 steelman（找设计者的理由与它保护的故障）。

**驳回 23 条不是「判错了」，而是量具在起作用**：其中多条的证据引用逐字属实，但影响被夸大、
或是有注释/测试/ADR 背书的刻意设计。驳回理由已逐条留档（见 §五），避免下一轮重复提出。

---

## 二、业务链地图（6 条）

### 2.1 章节生产主链 plan → write → review → commit
`chapter_plan`（build_ctx → director_planner → save_plan，写 `plan_json` + `chapter_scene_plans`）
→ `chapter_write`（load_plan → scene_planner → writer → polisher → length_check → condense → save_draft，
写 `drafts`，章推到 DRAFTED）→ `chapter_review`（basic_checks → critic_review → deep_review →
author_review(Human) → mark_reviewed，章推到 REVIEWED）→ `chapter_commit`（build_observer_ctx →
observer → inject_validate → quality_gate → high_risk_approval(Human) → commit → summarize，
写 `state_deltas` / `story_states` / `commits` / `chapter_summaries`）。

前端全部入口集中在 `ChapterDetailPage.tsx` 与 `ChapterPipelineHeader.tsx`，可用性判断在
`utils/chapterState.ts` 的纯函数里，刻意与后端 `ALLOWED_NEXT` 对齐。

### 2.2 Story State 与版本/分支链
observer 抽 7 类变更 → delta_repair（1230 行、20+ 条修补规则）→ validator 三层校验 →
`commit_delta`（单函数约 500 行、9 个顺序步骤，同时承载 main/branch/rollback/simulation 四类调用方）
→ `story_states` 快照 +1 → rollback 逆链 → 分支 create/promote → what-if 仿真。

### 2.3 上下文装配与提示词链
四条装配链（writer 24 维键 / director 24 维 / observer 无缓存 / director_planner 无自有缓存）
→ peek 单连接 JOIN → builders_common（excerpts / relevance / paged 三层裁剪）→ cache（256 条 FIFO）
→ prompts 表 + 运行期自愈 → 输出契约校验 → 预览双轨。

### 2.4 模型路由与配置链
能力名 → `AGENT_CAPABILITY` → capability_bindings 候选链 → 旧表 `model_configs` 回落 →
model_profiles → model_connections → `resolve_effective` → 5 级密钥解析 → 请求头模板渲染 →
协议 dispatch（openai_compatible / anthropic / ollama）→ `ai_call_logs`。

### 2.5 项目建模与内容入口链
3 条建项目入口（直建 / AI 初始化 / 备份导入，**只有 AI 初始化写 Story Bible**）→ 3 条自动化链
（project-init / deconstruct-book / outline-batch，**写三组互不相交的表，唯一交点在 `chapters`**）
→ 内容编辑 CRUD → 题材包 / skill 绑定。**`continuation` 是第四条正文生成路径，不在 workflow 框架内。**

### 2.6 治理链（质量 / 交付 / 元信息）
草稿/Delta → QualityEngine（7 子分）→ 46 个 rule_id → 聚合与公式指纹 → `quality_reports` →
提交门禁（唯一阻断消费方）→ 改稿处方 → delivery verdict → signing_check → arc / simulation →
ai_call_logs。

---

## 三、确认结论（17 条）

> 分级为三镜头投票后的**订正严重度**。`亲验` = 主会话已亲自读码确认。

### 3.1 major（9 条）

| # | 结论 | 位置 | 亲验 |
|---|---|---|---|
| M1 | **HIGH 风险审批卡只带 2/7 个 change 数组**：触发面扫 7 数组，展示面只装 `character_changes` + `world_changes` ⇒ 其余 5 个数组触发的 HIGH 会被**盲批** | `chapter_commit/gate.py:711` vs `pipeline_common.py:535-550` | ✅ |
| M2 | **按项目过滤 AI 调用日志时，项目级 run 的调用被内连接静默丢弃**：注释「workflow_runs 没有 project_id 列」在 0037 之后已过期，实现仍 `JOIN chapters`；project-init / outline-batch / deconstruct 的 `chapter_id` 为 NULL ⇒ 全部被滤掉 | `routers/ai_call_logs.py:222` | ✅ |
| M3 | **回滚不清理章节摘要**：`chapter_summaries` 只有写与读，rollback 既不删也不失效 ⇒ 被撤销剧情的摘要继续作为「近窗摘要」注入后续章 director 装配 | `story_state/commits.py:629` | ✅ |
| M4 | **「把章建出来」有 3 份实现，列集各不相同**（`volume_id` / `outline_json` / `plan_json`），章号靠 `UNIQUE(project_id, number)` 互相竞争 | `domain/chapter/service.py:237`、`project_init/pipeline.py:1317`、`outline_batch/pipeline.py:285` | ✅ |
| M5 | **continuation 采纳的 LLM 正文被记成 `created_by="human"`**：`create_draft` 契约写死 human ⇒ Q8 人机占比被污染，`model_id/prompt_version` 恒 NULL | `routers/continuation.py:372`、`domain/chapter/service.py:471` | ✅ |
| M6 | **被审版本只统一到门禁/导出，observer 与 summarizer 仍读「最新稿」**：审 v5 提交 v5，而 7 个 delta 与摘要描述的是 v6 ⇒ 下游写作依据与导出正文分叉，零告警。`summary.py:47` 的注释「提交门禁已保证最新稿=被审稿」在「审旧版」场景下**不成立** | `chapter_commit/summary.py:46`、`observer_input.py:722` | ✅ |
| M7 | **HIGH 风险审批在真实数据上等于每章必点**：判据只有 `risk_level == "HIGH"`，dev 库 2558 条 change 里 829 条 HIGH（32.4%），154/173 次提交需要批准 | `chapter_commit/gate.py:677` | 机制✅ / 数字为 agent 实测 |
| M8 | **最高频的质量 warning 是「事件发生在更早的时间」**——把倒叙当错误：任何新事件 day < 快照最大 day 即命中，161 份报告里出现 450 次（第二名 2.8 倍），使 continuity 只有 87/100 两种取值 | `quality/guardrails.py:479` | ✅ 机制 / 数字为 agent 实测 |
| M9 | **init-status 用 `plan_json.chapter_goal` 判 outline**，而 outline-batch 刻意只写 `outline_json` ⇒ 其产物被判「空壳卷纲」⇒ 前端默认勾选重跑 init ⇒ 进入删除预检，而预检放行的恰恰是「已生成计划但未写正文」的 PLANNED 章 | `routers/workflows/runs.py:118`、`project_init/pipeline.py:1219-1271` | ✅ |

### 3.2 minor（8 条）

| # | 结论 | 位置 | 亲验 |
|---|---|---|---|
| m1 | 乐观锁把「缓存失效版本号」当内容版本：审批暂停期间改标题/伏笔/项目 premise 都会 `state_version+1`，把在途 commit 判成内容冲突 | `story_state/commits.py:478` | 未亲验 |
| m2 | 章状态三处「先读后无条件 UPDATE」：并发保存草稿可把 COMMITTED 打回 DRAFTED | `domain/chapter/service.py:535` | 未亲验 |
| m3 | project-init 没有项目级原子并发守卫（outline-batch / deconstruct 都有），双开初始化会互删章节 | `routers/workflows/control.py:502` | ✅ |
| m4 | 「本章字数带/目标字数」review 与 write 各读一遍，且 critic/deep 拿到的是另一套目标字数 | `chapter_review/pipeline.py:330` | 未亲验 |
| m5 | 角色/世界实体的 `inject_mode` / `aliases` / `visibility` 后端全链消费，前端所有表单一个都不提交 | `bible/CharacterTab.tsx:424` | 未亲验 |
| m6 | 一个业务动作穿过约 16 个模块；最重的四个函数各 400~700 行，引擎单类 1130 行 | `workflow_runtime/engine.py:416` | ✅ |
| m7 | 「上下文预览」是第三套装配视图：空 scene + 默认 3000 字 + full 模式，与生产默认 paged 不一致，却共用同一个 256 条 FIFO 缓存池 | `context_engine/preview.py:501` | 未亲验 |
| m8 | 分支 + what-if 仿真是一套建了一半的 VCS：`state_deltas` 无 `branch_id`、`list_commits` 不按分支过滤、无放弃分支端点；dev 库 10 条分支全是 main、`branch_snapshots` 0 行 | `story_state/branches.py:1` | 结构✅ / 数据为 agent 实测 |

---

## 四、设计过度：总评

这不是「抽象层太多」这种泛评。本仓的过度设计有**四个可量化的形态**，每个都能落到行数或计数：

### 4.1 表达力远大于实际使用
质量体系 **8151 行 / 23 文件 / 约 46 个 rule_id**，而真正能改变提交结果的只有
**6 个 block id**（其中 `RULE_Q8_HUMAN_RATIO_LOW` 默认 warning，实际生效 5 个）
+ **1 个静态 confirm id**（`AI-BEAT-REPEAT`）+ 1 条按量级上修的 trigram。
其余 40 余条规则只进 issues 列表供人读；快通道（report 模式）下连这 7 条都不阻断。

`severity × gate × category` 四轴机制是为真实事故建的（P0-1：9 段逐字重复照常提交、overall 90），
**于是不能简单说"砍掉"**——但必须承认：机制的表达力是实际用量的 6 倍。

### 4.2 同一事实多份实现（维护成本的主因）
- 建章 **3 份**、最新稿 **6+ 处**（2 种排序口径）、章状态 UPDATE **6 处**、
  「最近一次有效审校」**4 份**、「未闭环 hook/debt 状态集」**5 份**（其中 2 份是 SQL 字面量）、
  候选行构造器 **3 份**、`_ensure_project` **6 份**。
- 权威口径只写在注释里：`draft_resolver.py:12` 自称「全仓唯一实现」，实际被 6 处绕过。
- 历史缺陷的成因高度集中在这一类：改一处漏一处。

### 4.3 半成品面（不是过度设计，是「建了没用完」）
| 子系统 | 规模 | 使用证据 |
|---|---|---|
| 分支 + what-if 仿真 | 781 + 503 行、0009 迁移、两个前端面板 | dev 库 10 条分支全 main、`branch_snapshots` 0 行 |
| Skill 子系统 | 8 端点 + 6 份提示词升版 | 15 个项目零绑定 |
| 知识权限（reveal_policies / HIDDEN / who_knows） | 一整层 | 装配侧有消费，产品面零入口 |
| judge 人工评分 | 面板 + 端点 | 161 份报告 0 次使用，且不影响任何判定 |
| `model_configs` 遗留表 | 264 行 CRUD + 前端 api 定义 | 前端 `.tsx` **零引用** |
| `evaluations` / `memories` / `scenes` | 3 张表 + 索引 + 备份白名单 | 生产代码**零 SQL 引用** |

成本已经在账上（行数、表、迁移、测试），收益没兑现。**这一类才该优先砍。**

### 4.4 开关换成分支
35 个 `NOVELOS_*` 环境变量。其中 observer 三开关（SPLIT / PARALLEL / SUMMARY_PARALLEL）
可组合出 8 种形态，只有默认档被日常跑到；另有若干「保留旧实现以便回退」的开关
（`WRITER_CONTEXT_MODE`、`CONTEXT_RELEVANCE`、`DISABLED_MODULES`）。

需要区分：**回退开关是项目刻意的风险策略**（AGENTS：不删闸门代码，回退=置 NULL），
不是无意识的过度设计。要砍的是「两个都活着且只有一个在跑」的那类。

### 4.5 一句话判断
> **这个仓的「大」，一半是事故驱动的防御（该留），一半是「先建起来再说」的半成品（该砍）。**
> 砍的顺序应该是：先砍 4.3 半成品面（成本已付、收益为零），再合并 4.2 的重复实现
> （降低下一次事故概率），最后才谈 4.1 的表达力。

---

## 五、被驳回的 23 条（附主要理由）

驳回 = 证据多属实，但「影响」或「定性」被夸大，或是有背书的设计。列出以免下一轮重复提出。

| # | 被驳回的结论 | 驳回要点 |
|---|---|---|
| 1 | prompts/agents 表是 .md 的只读镜像，却长出「自愈 + 忠实镜像守卫 + 每次调用两次取版」 | 自愈是为「长驻服务不重启 ⇒ 仓内新提示词到不了运行时」的真实事故；两次取版是自愈的代价 |
| 2 | checkpoint_json 存整份 ctx、每节点产出存两份 ⇒ 三套补偿 | 两份写入各有独立消费方：顶层 merge 是跨节点契约，镜像键是恢复期输入 |
| 3 | 三层模型配置每次调用全量重解析 | 计数属实，但因果链不成立；解析结果有缓存与短路径 |
| 4 | 思考档 8 档实际只有 3 种行为 | 把「参数族数」当「行为数」；8 档产出 8 份不同请求体 |
| 5 | 13 个手写校验器 vs 仓内已有 jsonschema | 行数与条数被夸大，且 jsonschema 在关键对象上套不上 |
| 6 | workflow_run_nodes 三列恒 NULL | 事实成立但 major 被夸大；列保留供审计扩展 |
| 7 | timeline_events 是 plot_events 的派生副本且不联动 | 影响场景在产品里不可达 |
| 8 | 取消提交 run 挡不住已落库 commit | 该行为被测试钉住，是既定设计 |
| 9 | 三份硬编码文风默认值、POV 越出枚举 | 推断断在两处；schema 对 pov 是自由字符串 |
| 10/12 | 「最新草稿」多处口径 | 大半是仓内已登记的已知低风险项；归因站点指错 |
| 11 | 装配缓存键 24 元、加一维要改 4 文件 | 结构属实，但「设计过度」定性被夸大，建议的修法治不到病 |
| 13 | 章节状态机只在 `update()` 生效，另 6 处裸 UPDATE | 有注释与测试背书的刻意设计（原子性/降级必须在同事务） |
| 14 | control ↔ revise 模块级循环依赖 | 概念环真实，但已用延迟 import 收口，静态检查可覆盖 |
| 15 | 35 个环境开关 | 多数是回退开关（项目刻意的风险策略） |
| 16 | 质量规则要跨 6 张注册表同步 | 把「有意收窄的后果面」误读成过度设计 |
| 17 | agent 名册四份且互不相等 | 计数属实，但四份各有消费方 |
| 18 | 前端类型双源 149 vs 146 | 已登记且**带机器看守**的债（`GHOST_SHAPE_DEBT` 只许递减） |
| 19 | 写一章 4 工作流 / 23 节点 / 12.5 次 LLM | 节点数不对（plan 实为 3 节点），且「记账调用」与写作不可比 |
| 20 | 7 维质量分几乎是常数 | 数字对，但「从未拦下提交」不等于缺陷（门禁本就只认 6 个 block） |
| 21 | Skill 子系统零使用 | 使用量为零属实，但这是「新功能未推广」不是「设计错误」 |
| 22 | 知识权限是一整层零使用的安全机制 | 把两套不相交的机制并成一层；who_knows 有真实消费 |
| 23 | 深度二审从未启用 | 启用率属实，但「过度设计」定性不成立（可选功能的默认关闭） |

---

## 六、未验证的 63 条（按主题归并）

> 这些是 minor/info，**没有经过对抗验证**，引用时须自行复核。完整清单见工作流输出。

- **死面 / 死字段（约 20 条）**：`evaluations` / `memories` / `scenes` 三张零 SQL 表；
  `chapters.visibility` / `who_knows`；`ai_call_logs.cost` 恒 NULL；`prompts.status` 单值；
  `workflow_run_nodes` 三列恒 NULL；`state_deltas.delta_version`；4 个永不参与谓词的索引；
  `WorkflowNode.kind` 五值零消费者。
- **重复实现（约 14 条）**：「最近一次 COMPLETED review」3 份 SQL；未结清状态集 5 份；
  genre/skill 两套逐字近似资源栈（约 430 行同构）；`plan_json` 三套 read-modify-write；
  `_ensure_project` 6 份；前端的活跃 run 谓词 8 处内联。
- **闭环缺口（约 14 条）**：`chapter_scene_plans` 无任何读写 API/UI；剧情事件只能建/删不能改；
  章节挂卷不可撤销；题材包写面 UI 不存在；提示词 ACTIVE/DEPRECATED 无写入口；
  4 个低层写端点产品面零消费者；连接归组两端点零消费者。
- **质量口径（约 8 条）**：`quality_reports.commit_id` 无人赋值；ai_trace 分量明细被丢弃；
  `recent_style_scores` 死路径；改稿处方 11/15 rule_id 永不命中；`RULE_TIMELINE_NON_MONOTONIC` 误报。
- **并发/状态（约 7 条）**：`_init_genesis_if_needed` 先查后建；PAUSED 是「出不去也停不掉」的态；
  regenerate 留旧 PENDING 行；`write_through` ABANDONED 守卫 check-then-act。

---

## 七、盲区（本轮未覆盖）

完整性批评指出三处**完全没有被测绘**的面：

1. **备份 / 恢复整链**：`core/backup/service.py:226/352`（导出/导入 25 表）+ `routers/backup.py`
   + `scripts/restore_db.py`——**全仓唯一不可逆覆盖写**。
   附带发现（未复核）：`backup/schema.py` 白名单不含 `chapter_fts`，`import_project` 不重建索引，
   而 `retrieval.rebuild_index` **全仓零生产调用方** ⇒ 导入的书 FTS 召回可能恒空。
2. **交付面**：`signing_check/checks.py` 824 行、`delivery/verdict.py` 740 行、`exporter/`、
   `content_sync/`（2450 行生产 + 2482 行测试，唯一消费者是一个 CLI）。
3. **四个 1 行空壳模块**：`domain/relationship`、`domain/hooks`、`domain/timeline`、
   `core/evaluation`——真实实现在 `domain/plot/service.py` 与 `domain/ledger/service.py`。

另有三条**无人问过**的方向（未复核，仅登记）：
- 回滚只有单 commit 粒度：回滚 ch2 后 ch3/ch4 的 delta 仍留在 state。
- 关系表写透（`applier.py:239`）但装配零注入（`writer_input.py:808` 注释明示不参与）。
- 导入即失去召回（同上 FTS）。

---

## 八、本轮处置

见 `docs/impl/reviews/business-chain-audit-fixplan-2026-11-08.md`（同批产出）。
原则：**本轮只修「事实清楚、改动面小、可加判别用例」的 8 条**；结构性重复与半成品面
登记待裁决，不在本轮做。
