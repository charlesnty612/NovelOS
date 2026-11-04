# B 档逐条实证报告 — 2026-11-05

> **任务**：把 `business-chain-audit-2026-11-04.md` §四 B 档约 60 条候选从「静态推断」变成
> 「实测 / 推翻 / 不可判」三档结论。**不修任何产品代码、不做 git 提交**。
> **方法**：每条按「读码定位 → 最小复现（临时库 + 合成夹具 / mock，零真实 LLM）→ 实跑 → 记结论」；
> 纯注释/死导出/类型层缺口用**可复跑的静态探针**取证（grep/AST，脚本见 §五）。
> **硬约束遵守**：零真实 LLM 调用；零内容仓接触（全部夹具为临时库合成数据）；
> 探针只写系统 Temp（`tempfile.mkdtemp`），仓内零写入；basement 全部落仓外。
> **口径**：结论按**当前工作树**（`4751528` = master 同点）成立；`file:line` 以该树为准。

---

## 一、结论摘要

| 档 | 条数 | 说明 |
|---|---|---|
| **确认** | **45** | 含 4 条**中危**（B6-4 / B8-3 / B3-1 / B3-3）、其余低危或 UX/卫生 |
| **推翻** | **12** | 其中 **B1-2 是「已修」**（批次三十五修的正是它）；3 条是「声明过的设计」；其余是审计读码偏差 |
| **不可判** | **0** | 全部条目拿到了决定性证据（运行探针或全树静态扫描） |

**与审计阶段判断的三个最有价值偏差**：

1. **B7-2（归档失败 ⇒ 分支进 director 装配）整体不成立**：分支**从不写 `story_states`**（`queries.py:56` 明示「分支不写 story_states 新行」），而 director 装配读的是 main 的 `story_states`——被吞的归档失败不可能污染装配，真实后果只剩 branches 表残留 + 名字占用。
2. **B5-5（outline-batch 新章触发语料为空 ⇒ 角色圣经丢失）危害方向反了**：空语料触发的是**「全量 full 注入」保兼容回退**（`excerpts.py:340/374`），不是注入丢失；实际影响是未规划章不裁剪、token 变多。
3. **B1-2（量空正文放行）在审计成文与本次实证之间已被批次三十五修掉**——本报告以探针证实现行为「不可检硬停」（`DRAFT_UNAVAILABLE` 哨兵 + `overall=0` + 阻断级 issue），审计描述的是修复前状态。

---

## 二、确认档（按严重性排序：不可逆写 > 门禁 > 数据损坏 > 质量降级 > UX/卫生）

> 证据标注：〔P-b1-1〕= 探针 `scripts/probe_b1_review_commit.py` 的 B1-1 项；〔S〕= 静态读码/全树扫描。
> 「修复面」= 涉及文件数 × 大致行数（供排期，不含测试则注明）。

### 中危（4 条）

**C-1　B6-4　project_init 重跑按卷重建：删除本卷全部章节且无状态过滤**
- 位置：`packages/workflows/project_init/pipeline.py:1219-1236`
- 机制〔S〕：挂载模式重跑 init 时 `DELETE FROM drafts WHERE chapter_id IN (本卷)` + `DELETE FROM chapters WHERE chapter_id IN (本卷)`，**无任何 status 过滤**。
- 实测边界（比原审计更精确）：2026-10-23 已改成**单事务**（删+建原子，中途失败回滚，注释明示）——「半写状态」面已封；**已提交**的章被 `commits.chapter_id` FK（0001，NO ACTION）挡住 ⇒ run FAILED、数据无损；**真正可丢的是「只有草稿未提交」的章**（drafts 被显式删除，连 `outline_json` 策展槽一起消失）。
- 失败场景：作者写了草稿没提交，误触发/重跑项目初始化 ⇒ 草稿静默清空。
- 修复建议：DELETE 前按状态过滤（至少保护非 PLANNED 章）或删除前把非空 drafts 移入备份表；改动面 ≈ 1 文件 15~25 行 + 测试。

**C-2　B8-3　分支含 HIGH 风险 delta ⇒ promote 永远 409（前后端无出路）**
- 位置：`packages/core/story_state/branches.py:594-600`（重放 main 时不带 `approved` 也不带 `_skip_approval`）× `commits.py:494`；前端 `BranchesPanel.tsx:150` 只把 409 原文贴出
- 实测〔P-b67〕：分支上人工 `approved=True` 提交 `world_kind=rule` 变更成功 ⇒ `promote_branch` 抛 `ApprovalRequiredError(high_risk=['wc_high'])`。`promote_branch` 签名**无审批参数**、无任何 API 能为 promote 预置批准 ⇒ 死路。
- 触发面：HIGH 风险 delta 可经人工分支提交进入（simulation 用 `_skip_approval=True`，但其分支终态归档不可 promote）。属于「门禁存在但没有配套出路」。
- 修复建议：`promote_branch` 加 `author_approval` 透传参数（复用 commits 既有判定）或对 promote 场景显式定义审批语义；前端收到 `high_risk_change_ids` 给出下一步文案。改动面 ≈ 2 后端文件 20~30 行 + 1 前端文件 + 测试。

**C-3　B3-1　resume 非原子：`_mark_run_running` 的 UPDATE 无状态条件（TOCTOU）**
- 位置：`packages/core/workflow_runtime/engine.py:1113-1116`
- 实测〔P-b3〕：`status=COMPLETED` 的 run 被直接翻成 `RUNNING`（UPDATE 只有 `WHERE run_id=?`）。0017 部分唯一索引只拦「同章**第二条**活跃 run」，拦不住对**同一条** run 的并发 resume：两个请求都读到 PAUSED → 都翻 RUNNING → 都进 `_run_nodes` ⇒ 双跑（重复 AI 调用 / 重复写）。
- 修复建议：UPDATE 加 `AND status='PAUSED'` 并检查 rowcount=0 ⇒ 抛 `WorkflowRunConflict`（与既有 409 语义同口径）。改动面 ≈ 1 文件 8~12 行 + 测试。

**C-4　B3-3　节点产出不可序列化 ⇒ run 永久停在 RUNNING（无终态）**
- 位置：`engine.py:934→1036`（`_update_node_row` 的 `_dump_json(output)`，成功路径在 try/except 之外）
- 实测〔P-b3〕：节点成功返回 `{'bad': object()}` ⇒ `TypeError` 穿出 `_run_nodes`；run 行实测 `status='RUNNING'`、无终态 ⇒ 该章被活跃 run 守卫卡死（后续 start 全 409），async 路径则 daemon 线程静默死亡。`_finalize_run` 内的 `_dump_json(capped)`（`:1143+`）同形状（终态收尾也会在同类输入上炸）。
- 修复建议：成功路径（`_update_node_row`/`_update_run_checkpoint`/`_finalize_run`）对序列化失败兜底（repr 截断 + log + 显式 FAILED 收尾）；改动面 ≈ 1 文件 25~40 行 + 测试。

### 低危 · 质量降级 / 配置失效（按序）

**C-5　B1-1　`_latest_review_report_draft_version` 不筛 `wr.status`**
- 位置：`packages/workflows/chapter_commit/gate.py:365-374`
- 实测〔P-b1-1〕：最新草稿 v2、COMPLETED 评审 v2、FAILED(驳回) 评审 v1 ⇒ `_reviewed_draft_version` 返回 **1**（采纳了 FAILED run 的版本）。同域 `_latest_completed_review_end`（control.py:133）与 commit 内层时序守卫（commit.py:388-397）都筛 `status='COMPLETED'`，唯此处不筛。
- 危害边界（实测推演）：非快通道下，commit 需 `REVIEWED` 状态 + 时序守卫双闸，纯「被驳回 run 污染」大多被拦在启动层；**真实可达的受害场景 = 快通道**：章节历史上有 PAUSED/FAILED review run（正常态：作者没回审批页）+ 快通道开 ⇒ commit 照跑，门禁量的是旧版本、`commits.draft_version` 记旧版本 ⇒ **导出物按 `commits.draft_version` 取旧稿**（`exporter/builder.py:143-158`）——作者提交的是新稿，定稿记录/导出指向旧稿。
- 修复建议：SQL 加 `AND wr.status = 'COMPLETED'`（与同域三处对齐）；改动面 ≈ 1 文件 3 行 + 测试。

**C-6　B2-2　档案级 `timeout_s` 对 anthropic / ollama 协议静默忽略**
- 位置：`packages/core/model_router/router.py:417-427`
- 实测〔P-b2〕：`timeout_s=123` ⇒ anthropic `provider.timeout=60.0`、ollama `=60.0`（构造器默认），对照 openai_compatible `=123`（生效）。零告警。
- 危害：按 AGENTS §二，opencode 网关的 Claude/MiniMax/Qwen 系**都走 anthropic 协议** ⇒ 这批档案的超时配置全部无效，长文生成统一在 60s 被掐断。
- 修复建议：两处构造补 `timeout=eff.timeout_s or 默认`（与 openai 分支同口径）；改动面 ≈ 1 文件 4 行 + 测试。

**C-7　B3-5　`_prepare_resume_ctx` 找不到 `current_node` ⇒ `start_index=0` 从头重跑**
- 位置：`engine.py:809-815`；实测〔P-b3〕：`current_node='ghost_node_renamed_away'` ⇒ `start_index=0`，零日志。
- 危害：节点改名/迁移后 resume 会重跑全部节点（AI 节点重复烧调用、写节点重复落库）。与「claim 不可判 ⇒ fail-closed」的仓纪律相反。
- 修复建议：找不到 ⇒ 显式报错拒 resume（或 log.error + 拒绝）；改动面 ≈ 1 文件 6~10 行 + 测试。

**C-8　B5-3　`start_plan` 服务端全程无章节状态闸门**
- 位置：`packages/core/api/routers/workflows/control.py:216-252`（`_start_workflow_core` 只有「章属于项目」+「无活跃 run」两查）+ chapter_plan pipeline 节点内亦无状态校验。
- 对照：write 有节点级（`save_draft.py:165-183` 拒 COMMITTED）、commit 有节点级（REVIEWED 校验）、review/commit 有 API 层时序守卫；plan 两头都没有。前端把「生成计划」限制在 PLANNED，服务端不设防 ⇒ API 直调可对 COMMITTED 章重写 `plan_json`（含覆盖 `revision_note`/`gate_blocked`，与 C-22 叠加）。
- 修复建议：`_start_workflow_core` 按 workflow 名加最小状态闸门（plan ⇒ PLANNED/REVIEWED 以下）；改动面 ≈ 1 文件 10 行 + 测试。

**C-9　B6-2　deconstruct 无项目级并发守卫**
- 位置：`packages/core/api/routers/reference.py:471-478`；实测〔P-b67〕：全文无 `project_level_guard`（对照 outline_batch 走 `control.py:554`），`chapter_id=None` 也不受 `idx_workflow_runs_active` 保护 ⇒ 同项目并发拆书互不拦截（重复 canon 写入）。
- 修复建议：`start_with_nodes(..., project_id=..., project_level_guard=True)` 一并解决 C-10；改动面 ≈ 1 文件 2 行 + 测试。

**C-10　B6-1　deconstruct run 归属列恒 NULL ⇒ 出现在每个项目的运行记录**
- 实测〔P-b67〕：`chapter_id=NULL` + 归属列 NULL 的 run ⇒ 项目 A、项目 B 的 `_run_visible_in_project` 均 True（「无从归属 ⇒ 保持全可见」分支）。0037 的归属列对 deconstruct 恒 NULL（调用不带 `project_id=`），每家项目的 run 列表都会看到别家的拆书 run。
- 修复建议：与 C-9 同一处改动（传 `project_id=`）；改动面 ≈ 1 行。

**C-11　B2-3　summarizer 产出无契约校验**
- 位置：`structured_output.py:835`（`_VALIDATORS` 无 summarizer）+ `:1048-1052`（未知 `expected` 静默 return）；消费点 `chapter_commit/observer.py:479`（`run_agent(..., expected="summarizer")`）。
- 实测〔P-b2〕：`validate_contract("summarizer", {任意形状})` 不抛 ⇒ summarizer 三路输出只保证「合法 JSON」，形状零校验，坏形状直接流向 summary 落库/消费方（其容错程度决定后果）。
- 修复建议：补 `_validate_summarizer`（按现行 summary 段契约）或在 `expected` 未知时 `log.warning`（消灭静默）；改动面 ≈ 1 文件 20~40 行 + 测试。

**C-12　B2-6　`_upsert_agent` 的 role/config_json 刷新在「提示词内容未变」分支被回滚**
- 位置：`prompts.py:351-366`（UPDATE 无 commit）× `:384-391`（未变分支直接 return ⇒ `finally: conn.close()` 隐式回滚）。
- 实测〔P-b2〕：人为把 role 改脏 ⇒ 内容未变分支后 DB role 仍脏；内容变化分支后 role 正常落库。⇒ **V3.9.4「启动 sync 自动对齐 role」只在 prompt 内容恰好变化时生效**——恰恰是对存量失真最无效的组合。
- 修复建议：未变分支也 `conn.commit()`（或 agent 刷新改用独立短连接）；改动面 ≈ 1 文件 3~6 行 + 测试。

**C-13　B1-3　delta 残留：门禁阻断 / run FAILED 前 delta 已落库，全仓无清理路径**
- 位置：`commit.py:309`（`submit_delta` ⇒ `status='validated'`）× 全树〔P-b1-3〕`DELETE FROM state_deltas` 命中 **0** 处。
- 实测边界：残留行**不能静默生效**——`commit_delta` 的乐观锁拒版本错配（实测 `OptimisticLockError`）；但版本恰好对齐时可被手工 API 提交。危害 = 存储增长 + 「validated 中间态」长期暴露。
- 修复建议：run 终态 FAILED/CANCELLED 且 delta 未 applied 时标记/清理（或加 TTL 巡检）；改动面 ≈ 1~2 文件 20~30 行。

**C-14　B2-5　skills 指纹缺 name 维度（原条目表述订正后确认）**
- 位置：`cache.py:897-916`（指纹 = `skill_id:version`）× `skill/service.py:164-176`（**只改 name 不带 payload ⇒ version 不自增**）× `consumers.py:196-210`（注入块含 `name`）。
- 原审计「同名不同 id 会互相命中缓存」**表述错误**（不同 id ⇒ 指纹必不同）；真问题是**改 `skills.name` 不 bump version ⇒ 指纹不变 ⇒ 脏命中旧装配**，注入块里的 skill 名过期（质量降级-低）。
- 修复建议：指纹输入加 name（peek SQL 与降级路径**同形**改——硬规则 2 纪律）；改动面 ≈ 2 文件 8~12 行 + 测试。

**C-15　B4-1　writer 测量路径只在 revise 模式剥尾块**
- 位置：`packages/workflows/chapter_write/writer.py:193-197`（`if mode == "revise"`）。write 模式若模型违约输出 `---REVISION-CHECKLIST---` 尾块，**测量**把它算进 `visible_chars`（保存路径按内容剥离是另一处、已修）⇒ 欠带误判为在带 / condense 误触发。最终门禁量落库稿，不受影响。
- 修复建议：测量路径与保存路径同口径（按内容剥，不看 mode）；改动面 ≈ 1 文件 3 行 + 测试。

**C-16　B4-3　`last_scene_excerpt` / `retrieved_memory` 是死字段（writer-v5 契约声明了它们）**
- 位置：`writer_input.py:285-297`（两键硬编码 `""` / `[]`；`planner_input.py:176` 同款）× `docs/agents/prompts/writer-v5.md:169-171`（§5 输入契约声明）× `:489`（规则 E-WRT-10 引用 `last_scene_excerpt` ⇒ **死规则**）。硬规则 9「契约承诺存在 ≠ 数据源存在」的反向族。
- 修复建议：要么实现（从 scene_plan/上一章取），要么升版提示词删除声明（升版而非原地改——仓铁律）；改动面 ≈ 1 代码文件或 1 提示词新版本。

**C-17　B4-4　`length.py` 两处无日志 `except Exception` ⇒ 静默回落默认字数带**
- 位置：`packages/workflows/chapter_write/length.py:61-64 / 79-82`。读库失败 ⇒ 项目级 `word_band_json` 覆盖静默失效（默认带），零告警。
- 修复建议：补 `log.warning`（不阻断）；改动面 ≈ 1 文件 4 行。

**C-18　B5-2　`_save_plan_node` 字数优先级不含 `ctx['target_word_count']`（UI 当前不可达）**
- 位置：`chapter_plan/pipeline.py:176-187`（director 输出 > plan 继承 > 题材包 > 全仓默认）。请求体显式传的 target 被收下（`control.py:261` 进 ctx）但 plan 落库不用——「收下即忘」族（硬规则 9 配套纪律）。
- 缓解事实：前端四按钮从不发送该字段（C-38/B8-9 实测）⇒ 当前 UI 无影响；仅 API 直调可见。
- 修复建议：决策「plan 的 expected_word_count 是否该被请求 target 覆盖」后再改（策略面）；或先在端点文档声明语义。改动面 ≈ 1 文件 5~10 行 + 决策。

**C-19　B4-2　`_condense_node` payload 的 `agent: "condense"` / `prompt_version: "condense:v1"` 是假标签**
- 位置：`pipeline.py:897-899` × 实际调用 `:938-946` `run_agent(db, "polisher", ...)`；仓内**无 condense 提示词文件**（`docs/agents/prompts/` 仅 polisher-v1/v2）。落库（ai_call_logs）记的是真值；错的是**发给模型的 payload 元数据**。与批次十三 POLD-1「payload 声明漂移」同族（那边无缓存键、这边是纯回声）。
- 修复建议：payload 两键改真值（或删掉）；改动面 ≈ 1 文件 2 行。

**C-20　B4-5　drafts 记录的 model/prompt 版本取「本节点最后一条 ai_call_logs」而采纳的可能不是最后一次**
- 位置：`pipeline.py:518-541`（`ORDER BY rowid DESC LIMIT 1`）× `:398-404`（采纳 `_best_writer_attempt`，可选中**较早**一次）。
- 危害边界（实测推演）：同一节点内各 attempt 的 model_id/prompt_version 相同 ⇒ 观测值一致；错位仅在「节点中途提示词自愈恰好推进版本」时可见（极窄）。形状属实、当下危害≈0。
- 修复建议：按采纳序号取对应行（或登记为已知窄窗口）；改动面 ≈ 1 文件 6~10 行。

**C-21　B5-6　`build_ctx` 阶段算的 `prompt_version` 可能落后于 runner 自愈后的 ACTIVE（一次性过渡态）**
- 位置：`planner_input.py:292-294` × runner 自愈在 `run_agent` 内更晚发生。单次 run 的 payload 标签可能陈旧（落库侧现算，不受影响）。低。
- 修复建议：payload 键改为可后填，或登记接受；改动面 ≈ 1 文件 5 行。

**C-22　B8-8　章节计划面板 PATCH `plan_json` 是整体替换 ⇒ 手工编辑计划会静默清掉 `revision_note` / `gate_blocked`**
- 位置：`apps/web/src/pages/chapter/ChapterPlanSection.tsx:37-40` × `domain/chapter/service.py:230-231`（「plan_json 提供时整体替换」）。`plan_json` 同时承载门禁写路径的 `revision_note` / `gate_blocked`（`gate.py:564+`）⇒ 作者在门禁拦截后直接编辑保存计划，改稿出路标记无痕消失。
- 修复建议：前端 PATCH 前合并既有 revision 键（或后端对这两个键做保留合并）；改动面 ≈ 1 文件 10 行 + 测试。

### 低危 · UX / 可观测 / 卫生（其余确认条目）

| # | 原条目 | 结论与证据 | 修复面 |
|---|---|---|---|
| C-23 | B6-3 deconstruct 同步起跑占 HTTP 线程 | 〔S〕`start_with_nodes`（同步）+ T2 对每章串行 `run_agent`；对照其余链已 async。设计代价：拆长书时请求易超时 | 1 文件 ~15 行（改 async + 轮询）|
| C-24 | B6-5 `_HOOK/_DEBT_OPEN_STATUSES` 副本 | 〔S〕`outline_batch/pipeline.py:77-78`（tuple）≡ `ledger.py:408-409`（frozenset），docstring 自认副本 ⇒ 漂移无看守 | 1 文件（改 import）~5 行 |
| C-25 | B6-6 `chapter_summaries` 追加-only + 两读者口径不同 | 〔S〕单点 INSERT（summary.py:147）、无 UPSERT/DELETE；读者 1 取「< 当前章的最近窗口」（summaries.py:197-203），读者 2 全量 dict 后写覆盖（outline_batch:106-110） | 视需求收敛口径，~10 行 |
| C-26 | B6-7 outline_batch payload 的 `prompt_version` 用常量、落库现算 | 〔S〕`pipeline.py:142`（`PLANNER_PROMPT_VERSION_FALLBACK`）vs `:223/:301`（`active_prompt_label`）；payload 是发给模型的陈旧回声，落库已对 | 1 文件 ~2 行 |
| C-27 | B6-8 outline_batch 建章 `volume_id` 恒 NULL | 〔S〕`pipeline.py:261-278`（`:volume_id` 绑 None）；对照 project_init 建章带卷 ⇒ 这批章不进卷视图/卷域装配 | 1 文件 ~5 行（含策略）|
| C-28 | B6-9 `chapters(project_id,number)` 无 UNIQUE | 〔P-b67〕同键两行插入成功（0001 仅普通索引）；顺序重跑走 `max+1` 安全，重号仅并发可达 | 新迁移 1 条 + 测试 |
| C-29 | B7-1 自定义 `name` 的推演在列表与重放双出口不可见 | 〔S〕`simulation/service.py:261`（`name or sim-<ts>`）× `:385/:410`（两处 `name LIKE 'sim-%'`）；router docstring 自己写了这个过滤 | 1 文件 ~4 行（或前端约束命名）|
| C-30 | B7-5 delivery evidence 逐章新建连接 | 〔S〕`evidence.py:238-252` 每章 `resolve_draft` 各开连接；批次二十三只收敛了装配路径 | 1 文件 ~15 行（性能）|
| C-31 | B7-8 脏 `issues_json`（形状合法但 Literal 表外值）⇒ 端点 500 | 〔S〕`routers/quality.py:162-166` —— **代码内已自登记**「残余边界」；触发面 = 手工改库 | 批次九单点 `decode_json_field` 不覆盖 Literal；~15 行 |
| C-32 | B2-4 `call_with_fallback` 的 `except Exception` 兜住编程错误 | 〔S〕`router.py:658-666`；**订正**：非静默——log.warning + `attempts` 列表 + `from last_exc` 保留因果链。危害=诊断可读性 | 收窄分类 ~10 行 |
| C-33 | B2-8 `resolve_effective` 的 `provider` 形参死参 / `upstream_params` 死字段 | 〔P-AST〕形参 Load 点 **0** 处；`upstream_params` 生产零消费（仅测试断言） | 删形参/字段 ~5 行（或登记）|
| C-34 | B3-4 `_resolve_checkpoint_exclude` 未注册 ⇒ 静默 `[]` | 〔P-b3〕实测返回 `[]` 零日志；resume 时 scrub 全失效（checkpoint 混入应排除键） | 补 `log.warning` ~2 行 |
| C-35 | B3-6 「活跃 run」与「新鲜稿」SQL 各有两份同形实现 | 〔S〕control.py:100-110 ≈ engine.py `_find_active_run_for_chapter`；commit.py:388-403 ≈ control.py:613-625——**后者是注释声明的刻意复制**（避免 workflow→API 反向依赖） | 收敛与否属架构裁决；0 行（登记）|
| C-36 | B1-4 注释称 `draft_version`「仅 chapter-review 读取」 | 〔S〕`control.py:279-281` × `gate.py:425` 确读；**行为本身有 2026-10-23 Lead 裁决登记**（B4：显式 > 人审 > 最新）⇒ 只需改注释 | 1 行 |
| C-37 | B1-5 注释称 `quality_gate_mode` 默认 report | 〔S〕`routers/workflows/common.py:46-48`「任务书拍板默认 report」× `gate.py:326` 默认 `enforce` | 1~2 行 |
| C-38 | B1-7 `save_report` 的「取最新」兜底与不可检路径叠加 | 〔S〕`service.py:145-147` 仍在；批次三十五后显式版本记显式值（service.py:893-903），叠加面只剩「不可检 + 无显式版本」且最新版读取恰好可用——极窄，报告本体已带阻断 issue | 可登记；~5 行 |
| C-39 | B8-1 「撤销发布标记」文案与目标状态不符 | 〔S〕`ChapterDeliveryActions.tsx:90-91` title 说「回到已提交待处理」，实际 `move('PLANNED')`；后端不允许 RELEASED→COMMITTED（组件注释 ：9 明示刻意挡 COMMITTED→REVIEWED） | 1 行文案 |
| C-40 | B8-2 前端 `ALLOWED_NEXT` 缺两条 | 〔P-b8〕REVIEWED 缺 DRAFTED、COMMITTED 缺 REVIEWED（后端 `models.py:28-34` 都有）；**生产零 import（死导出）⇒ 无实际影响**（与 CHANGELOG 批次三十五登记一致） | 补两条 ~2 行 或删除导出 |
| C-41 | B8-4 `chapters.outline_json` 前端零消费 | 〔P-b8〕全 src 命中 3 处全在 api/ 类型层 ⇒ 策展大纲槽在 UI 不可见不可编辑 | 功能缺口，排期裁决 |
| C-42 | B8-5 `qualityApi.evaluate` 无版本入参 | 〔P-b8〕前端 body={}、后端端点无 draft_version ⇒ 只能评最新；出参带 `draft_version` 但入参缺位 | 2 文件 ~10 行 |
| C-43 | B8-6 `ChapterRunBanner` hintText 四个专属分支恒不命中 | 〔P-b8〕判断全比对不透明 `workflow_id`（`new_id('wf')`）；应比对 `workflow_name`。actionLabel 有兜底不受影响；纯 cosmetic | 1 文件 ~4 行 |
| C-44 | B8-7 pause_payload 前后端两套实现且优先级不同 | 〔P-b8〕服务端 current_node 优先（common.py:261-272），前端只有顶层/插入序两级 ⇒ 多挂起 payload 共存时可能取到不同的；服务端 PAUSED 响应已附 ⇒ 前端是回退路径，分歧面窄 | 收敛单点 ~15 行 |
| C-45 | B8-9/10/11 按次字数与铁律无入口 / `foreshadow_overdue_chapters` 无入口 / skill 8 端点零消费 | 〔P-b8〕三组 grep 均 0 生产命中；与 CHANGELOG 批次三十五登记一致 | 功能缺口，排期裁决 |

---

## 三、推翻档（12 条，每条说明原推断错在哪一步）

| # | 原条目 | 推翻理由（实测/读码证据） |
|---|---|---|
| R-1 | **B1-2** 显式 `draft_version` 不存在 ⇒ 量空正文拿满分放行 | **已修**（批次三十五，审计成文之后）。〔P-b1-2〕`build_quality_context(draft_version=99)` 返回 `DRAFT_UNAVAILABLE` 哨兵；`gate.py:523/538-540` ⇒ `should_block` 恒真；分数面 `overall=0` + `scoring_missing_subscore` ×2（不是 100/空）。评审侧与提交侧「同一字段两种态度」的对照也已消除 |
| R-2 | **B1-6** 快通道 + 无 COMPLETED review ⇒ 两道「草稿被审过」守卫同时失效 | 设计如此，非缺陷：无 COMPLETED review ⇒ 「被审版本」无对象，守卫 docstring 明写「无 COMPLETED ⇒ 交由 REVIEWED 状态机兜底」；快通道语义本身就是「不跑 review」。真实风险在 C-5（旧 run 污染被量版本），不在守卫早退 |
| R-3 | **B2-1** 空流重试 `exc.__class__(msg)` 重建异常 ⇒ 生产 TypeError | 〔P-b2-1〕全仓 marker `"empty stream: no content chunks received"` 的 raise 点实测仅 `providers.py:443` 一处且包在 `ProviderError`；`runner.py:478-491` 对 `ProviderError` 有 isinstance 特判（带 provider/status_code 重建）⇒ else 分支对携带 marker 的异常**不可达**。残余边界：仅当第三方自定义 provider 抛非 ProviderError 且消息含该串 |
| R-4 | **B2-7** output-invalid 重试每 attempt 新建 ModelRouter ⇒ 两次输出可能来自不同模型 | 失败转移的**既有设计**：attempt 重试重新走 `call_with_fallback`，候选 1 成功则同模型；「不同模型」只在候选 1 二次失败时发生 = fallback 语义本身（且此时换模型是合理行为）。新建 `ModelRouter` 无状态、无成本 |
| R-5 | **B3-2** `_insert_node_row` 每节点泄漏一条连接 | 〔P-b3-2〕调用前后存活 `sqlite3.Connection` 对象数 0→0：内联临时连接在语句返回后由 CPython 引用计数**立即析构并释放句柄** ⇒ 非资源泄漏。成立的是「违反全仓显式 close 纪律」的卫生问题（同 2026-10-21 卫生批次修过的形状） |
| R-6 | **B3-7** `list_runs` 的 `OR chapter_id IS NULL` 返回所有项目 ⇒ 跨项目泄漏 | 契约已文档化且有单一消费方：`runs.py:41-52` docstring **明写**「调用方须自行过滤」；唯一调用方 `routers/workflows/runs.py:391` 已接 `_filter_runs_by_project`（M7，2026-10-24），判别测试在 `test_run_list_project_scope.py` |
| R-7 | **B5-4** `DIRECTOR_PLANNER_PROMPT_VERSION='director_planner:v2'` 落后于仓内 v4 | 该常量只是 `active_prompt_label(..., fallback=...)` 的**兜底**（`planner_input.py:293`），真值现算；DB 由启动 sync + 自愈保证有行 ⇒ fallback 几乎不可达。批次二十三修的「落库必须现算」正是这条链，已接线 |
| R-8 | **B5-5** outline-batch 新章（plan_json 空）触发语料为空 ⇒ 角色注入丢失 | 〔S〕危害方向反了：`excerpts.py:340/374` 空语料 ⇒ **auto 实体全部按 full 注入**（保兼容回退，docstring 明示「无章节计划文本 → 全注入」）。实际影响 = 未规划章不裁剪、注入变多（token 变多），不是角色圣经丢失 |
| R-9 | **B7-2** `_safe_archive` 吞异常 ⇒ 临时分支停留 ACTIVE 并进入 director 装配 | 结构性不可能：`queries.py:56` 明示「**分支不写 story_states 新行**」，分支状态只以 commits 重放形态存在；director 装配读 main 的 `story_states` ⇒ 残留 ACTIVE 分支**不可能**进装配。另订正：`_safe_archive` 有 `log.warning`（非零痕迹）。真实后果 = branches 表残留 + 同名占用 |
| R-10 | **B7-4** 交付判定 gate 由运行期 `issue_gate` 重推 ⇒ 历史分档随矩阵漂移 | docstring 声明的**刻意设计**（`verdict.py:394-397`：「落库报告可能早于 gate 字段——此时按规则表重新推出分档，因此旧报告也能被正确分档」）。漂移是声明的固有代价；冻结旧报告反而会让无 gate 字段的历史报告失去分档 |
| R-11 | **B7-6** content_sync 备份包可无鉴权导入运行库 | ① 包走项目白名单（`backup/service.py` `_FLAG_REQUIRED_ABSENT_TABLES` 派生声明 + 递归看守禁 `"api_key"`）⇒ **不含模型凭据**；② 「无鉴权」是本地单用户工具的既定设计（全 API 均无鉴权），备份/导入是配套功能而非新增攻击面。可登记的真实面只有「`backup/` 目录随 push 累积」的存储增长 |
| R-12 | **B7-7** 物理删除的归属凭证唯一来源是 manifest 本身 | 审计描述的是 **2026-10-23 返工前**的实现。现行（`content_sync/service.py:313-351` 注释明示）：物理删除需**双条件**（文件名在上一版 manifest `owned_files` 里 **且** 当前内容 sha256 仍一致），否则**移入隔离区**绝不删除 + manifest 留痕 |

> 另：审计 B-5 首条（plan 覆盖键）与 B-8 前两条已升 A 档并在批次三十五修复（A-2/A-3），本报告不重复。

---

## 四、不可判档

**无。** 全部 57 条（45 确认 + 12 推翻）拿到了决定性证据。原任务书里预期可能「不可判」的条目
（如 `ALLOWED_NEXT` 死导出的影响面）均通过「全树 grep + 消费方枚举」降为可判。

---

## 五、探针清单（可复跑）

| 脚本 | 覆盖 | 说明 |
|---|---|---|
| `scripts/probe_b_common.py` | 公共 | 仓根 sys.path 修正（防 editable 指回主仓）+ 临时库 + 结论记录器 + UTF-8 stdout |
| `scripts/probe_b1_review_commit.py` | B1-1 / B1-2 / B1-3 | 评审版本无 status 过滤；显式版本不存在 ⇒ 硬停；delta 残留 + 乐观锁防线 |
| `scripts/probe_b2_llm.py` | B2-1 / B2-2 / B2-3 / B2-6 | marker 产地全树扫；timeout 三协议对照；summarizer 契约；role 刷新回滚 |
| `scripts/probe_b3_engine.py` | B3-1 / B3-2 / B3-3 / B3-4 / B3-5 | 无状态 UPDATE；连接计数；不可序列化卡死；静默 `[]`；`start_index=0` |
| `scripts/probe_b67_misc.py` | B6-1 / B6-2 / B6-9 / B8-3 | run 全项目可见；无并发守卫；重号章；promote HIGH 死路 |
| `scripts/probe_b8_frontend.py` | B8-2/4/5/6/7/9/10/11 | 前端静态面（grep + 注释行过滤 + 生成类型排除），纯只读 |

复跑方式（必须用项目 .venv 解释器、从仓根跑；全部只写系统 Temp）：

```bash
.venv/Scripts/python.exe scripts/probe_b1_review_commit.py
.venv/Scripts/python.exe scripts/probe_b2_llm.py
.venv/Scripts/python.exe scripts/probe_b3_engine.py
.venv/Scripts/python.exe scripts/probe_b67_misc.py
.venv/Scripts/python.exe scripts/probe_b8_frontend.py
```

每个脚本末尾输出「确认 / 推翻 / 不可判」计数行；全部 `exit 0`（无不可判）。
本轮实测各脚本结论与上表一致（B-1：2/3；B-2：3/1；B-3：4/1；B-6/7/8 杂项：4/0；B-8 前端：8/0）。

---

## 六、与既有登记 / 批次三十五的关系

- **B1-2（量空正文放行）**：批次三十五 ⑤ 修的正是它——本报告以探针把「已修」钉成实测。
- **B8-2（ALLOWED_NEXT 死导出）/ B8-9（author_intent/target_word_count 无赋值点）**：与 CHANGELOG
  `## [Unreleased]` 批次三十五「本轮明确不做（登记，不修）」段的两条实测结论**逐字一致**，本轮独立复算无翻案。
- **C-5（gate 不筛 status）**：与 2026-10-23 对抗审查 A 的 B4 裁决（显式 > 人审 > 最新的优先级）
  **不冲突**——裁决定的是优先级顺序，本条定的是「候选 run 的状态过滤」，两个面。
- **C-36（draft_version 注释）**：同上，行为已登记、注释失准 ⇒ 只改注释。

## 七、建议的下一轮修复顺序（危害 ÷ 改动面）

1. **C-6 timeout_s**（4 行，用户可感知的配置失效，opencode/anthropic 用户全量受害）
2. **C-3 resume 无条件 UPDATE**（8~12 行，并发正确性）
3. **C-5 gate 筛 status**（3 行 + 快通道导出正确性）
4. **C-12 role 刷新回滚**（3~6 行，一处 commit）
5. **C-4 序列化兜底**（25~40 行，章节卡死的根）
6. **C-2 promote 审批出路**（需先做产品裁决：审批语义挂哪层）
7. **C-1 init 重跑删章**（需先做产品裁决：重建语义 vs 状态保护）
8. 其余低危/UX 按批顺带；纯注释/文档 4 条（C-36/37/39/40）可并入任一批。
