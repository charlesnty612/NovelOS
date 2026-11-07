# 业务链审计 · 修复方案（2026-11-08）

> 依据：`docs/impl/reviews/business-chain-audit-2026-11-08.md`。
> 原则：**只修「事实清楚、改动面小、可加判别用例」的条目**；结构性重复与半成品面登记待裁决。
> 约束：零真实 LLM 调用、零内容仓接触、活库业务数据零改动；每条修复配「撤掉必红」的判别用例。

---

## 一、本轮执行（7 条）

### F1 · HIGH 审批卡只带 2/7 数组（major）
- **文件**：`packages/workflows/chapter_commit/gate.py::_high_risk_approval_node`
- **改法**：新增 `_high_risk_items(observer_payload)`，按**触发判据**过滤 7 个数组
  （`character`：risk_level=HIGH 或 facet=definition；`world`：risk_level=HIGH 或 world_kind=rule；
  其余 5 个：risk_level=HIGH），payload 的 `changes` 改为这 7 个过滤后的数组。
- **语义**：审批卡展示的集合 == 触发审批的集合。**不改变**是否暂停的判定。
- **判别**：unit test —— 仅 `new_events` 含 HIGH 时，pause payload 的 `changes["new_events"]` 非空、
  `character_changes` 为空；撤掉过滤（改回只装 2 数组）必红。

### F2 · 按项目过滤 AI 调用日志丢项目级 run（major）
- **文件**：`packages/core/api/routers/ai_call_logs.py`
- **改法**：`JOIN chapters` 改 `LEFT JOIN chapters`，谓词改为
  `(r.project_id = ? OR ch.project_id = ?)` —— 0037 的 `project_id` 为主，`chapters` 反查兜底历史 NULL 行；
  同步订正过期注释。
- **判别**：api test —— 项目级 run（`chapter_id IS NULL`、`project_id` 已填）+ 一条 `ai_call_logs`，
  按 project 过滤必须返回该行；撤掉 `r.project_id` 分支必红。

### F3 · init-status 判 outline 不认 outline_json（major）
- **文件**：`packages/core/api/routers/workflows/runs.py:118`
- **改法**：`has_real_chapters` 改为 `plan_json.chapter_goal **or** outline_json.chapter_goal`。
- **判别**：api test —— 只有 `outline_json.chapter_goal` 的章 ⇒ `outline.done == True`；撤掉或分支必红。

### F4 · 回滚不清章节摘要（major）
- **文件**：`packages/core/story_state/commits.py`（`_inverse_cleanup` 段）
- **改法**：同事务 `DELETE FROM chapter_summaries WHERE chapter_id = ?`。
- **理由**：摘要是「已发生剧情」的派生面，回滚后原剧情不再成立；不删会让撤销内容继续注入后续章。
- **判别**：unit test —— commit 后存在摘要 ⇒ rollback ⇒ 该章摘要为 0 行；撤掉 DELETE 必红。

### F5 · continuation 采纳记为 human（major）
- **文件**：`packages/domain/chapter/service.py::create_draft` + `packages/core/api/routers/continuation.py`
- **改法**：`create_draft` 增加**可选** `created_by="human"` / `prompt_version=None` / `model_id=None`
  （缺省逐字不变）；continuation adopt 传 `created_by="agent:continuation"` 与本次调用的
  `model_id` / `prompt_version`（拿不到则留 None）。
- **判别**：unit test —— adopt 后 `drafts.created_by` 以 `agent:` 开头且 Q8 归桶不为 human；
  撤掉传参必红。

### F6 · project-init 缺项目级原子并发守卫（minor）
- **文件**：`packages/core/api/routers/workflows/control.py::_start_project_init`
- **改法**：`start_with_nodes(..., project_level_guard=body.project_id is not None)`，
  并把 `WorkflowRunConflict` 映射 409（与 outline-batch 同款）。
- **不改异步**：project-init 目前同步起跑，前端依赖其返回语义；只补守卫，零行为突变。
- **判别**：api test —— 同一 project 连续两次 start（第一次挂在 Human 前的 mock 流或直接构造活跃 run）
  ⇒ 第二次 409；撤掉 `project_level_guard` 必红。

### F7 · README 三处过期/自相矛盾的数字
- **文件**：`README.md`
- **改法**：迁移数 34 → 38；pytest 基线 3800/3802 → 4749/4746；vitest 781 → 942；
  并删掉同一文件里两处互相矛盾的旧基线。
- **判别**：无机器看守（本轮只订正数字）；在报告 §四登记「README 无守卫」这一事实。

---

## 二、本轮登记不修（附理由）

| 条目 | 理由 |
|---|---|
| M6 被审版本未统一到 observer/summarizer | 修法跨 3 个装配点 + 缓存键语义，改动面大于本轮其余全部之和；**下轮单独立项**（建议与「删掉审校页版本选择器」二选一，需产品裁决） |
| M7 HIGH 风险每章必点（32.4%） | 改判据会改变审批语义，属产品级裁决；需用户拍板「哪些变更必须人工签字」 |
| M8 timeline 非单调误报 450 次 | 改评分口径 ⇒ `formula_hash` 变 + 三处文档同步 + 语料复校；本轮不动 |
| m1 乐观锁把 state_version 当内容版本 | 需引入内容哈希或独立计数器，跨 story_state 全链 |
| m2 章状态 UPDATE 补谓词 | 3 处并发路径，改动虽小但需逐处设计冲突语义，下轮与 m1 合并做 |
| m4/m5/m7/m8 | 分别为重复实现、前端功能缺口、预览双轨、分支半成品 |
| 结构性重复（建章 3 份、最新稿 6 处、状态机 6 处） | 重构面大，需单独立项 + 全量回归 |
| 死表/死字段（evaluations/memories/scenes 等） | 删除需迁移（`只加不改` 纪律下属破坏性动作），本轮只登记 |
| 备份/恢复、交付面、FTS 召回盲区 | 本轮未测绘，先补测绘再谈修复 |

---

## 三、验收

1. 每条修复配判别用例；新增用例做「撤掉修复 ⇒ 必红」手工确认。
2. `python -m pytest -n 20 -q --basetemp=<仓外>` 全量绿，测试数按增量对账。
3. `ruff check packages scripts tests`。
4. `cd apps/web && npx tsc -b && npx vitest run && npm run build`（本轮若零前端改动则只做回归确认）。
5. `python scripts/smoke_e2e.py` + `check_state_sync.py`。
6. 树指纹跑测前后逐位一致。
7. CHANGELOG + AGENTS 留痕。
