# workflows.chapter_review（章节评审）

> 职责：对本章草稿做最小基本检查（字数偏离 ±15%、禁用词扫描）+ LLM 评审员建议（V1.3，advisory only）+ Human 审批。完成后 chapters.status DRAFTED→REVIEWED。
> 状态：Sprint 4-A 已实现（MVP 简化版）；Sprint 5 补全「驳回并改稿（revise）」闭环（PRD §59/§87）；V1.3 新增 LLM 评审员节点（仅建议、不拦截）。

## 节点列表

| node_id | kind | 说明 |
|---|---|---|
| `basic_checks` | Transform | 校验草稿存在性；字数偏离 target ±15% 记 warning；扫描 forbidden_words（默认 `["仿佛", "如同", "本章目标"]`）。输出 `review_report` 进 ctx |
| `critic_review` | AI | V1.3 LLM 评审员。调 `critic` agent 对草稿生成**建议性**结构化报告；写入 `critic_report`，合并进 author_review 的 pause_payload。**仅建议、不拦截**：任何失败（prompt 缺失 / provider 异常 / 输出不合规）→ `critic_status='failed'` + `critic_report=null`，**不**阻断人工审批 / run 终态 |
| `author_review` | Human | 抛 `PauseRequested(payload=review_report, critic_status, critic_report)` 等 author 决议；human_input 三态（见下） |
| `mark_reviewed` | State | chapters.status DRAFTED→REVIEWED（仅当 author_review 通过）；revise 分支写 revision_note 并收尾 |

注册名：`chapter-review`

## 输入 / 输出

- 输入：`chapter_id`（必须有 draft）。
- 输出：`run_id` + status。PAUSED 时返回 `pause_payload`（含 review_report + critic_status + critic_report）；resume 三态：

| human_input | run 终态 | chapters.status | 附带效果 |
|---|---|---|---|
| `{"approved": true}` | COMPLETED | REVIEWED | — |
| `{"approved": false}` | FAILED（error=author_review 未通过…） | 保持 DRAFTED | — |
| `{"approved": false, "revise": true, "note": str?}` | FAILED（error=`rejected-for-revision`） | 保持 DRAFTED | `note` 追加进 `plan_json.revision_note`（无 note 时移除该键），供下次 chapter-write 的 load_plan 参考 |

## critic 节点（V1.3）— 失败降级语义

| 触发场景 | critic_status | critic_report | 人工审批 |
|---|---|---|---|
| mock 合规 JSON / provider 正常 | `ok` | dict（见 prompt §7 schema） | 正常 |
| LLM 输出非 JSON（连续 2 次重试仍失败） | `failed` | `null` | 正常（按钮可用，UI 显示「AI 审稿不可用」） |
| critic prompt 未注册（`PromptNotFoundError`） | `failed` | `null` | 正常 |
| 无 model_config + 无 mock（`ModelNotConfiguredError`） | `failed` | `null` | 正常 |
| LLM 抛契约校验失败（category/severity 越界等） | `failed` | `null` | 正常 |
| 输出 JSON 中某 issue.quote 不可溯源到 draft_text | `ok` | dict（该 issue 被丢弃，其余保留） | 正常 |

**核心约束**：

- `critic_status != 'ok'` 时 `critic_report` 必为 `null`，UI 仅显示弱提示「AI 审稿不可用（不影响审批）」，**不**显示批准/驳回按钮禁用态。
- 任何 critic 节点的异常（输入收集失败、provider 异常、JSON 解析失败、契约校验失败）都**不**抛错 —— 节点以 `critic_status='failed'` 收尾，让 author_review 照常 PAUSED。
- critic 报告**不**进入 State Delta、**不**触发自动驳回或重跑；仅供人工审批界面渲染参考。

## 失败语义

- 无 draft → basic_checks 抛错 → run FAILED。
- resume approved=false（非 revise）→ mark_reviewed 抛错 → run FAILED，chapters.status 保持 DRAFTED。
- resume revise:true → mark_reviewed 写 revision_note 后抛 `_RejectForRevision` → run FAILED（error=`rejected-for-revision`），chapters.status 保持 DRAFTED。
- chapters.status 非 DRAFTED → mark_reviewed 抛错 → run FAILED。
- critic 节点任何失败 → 不抛错；`critic_status='failed'`，pause_payload 仍正常生成。

## 依赖

- `packages/core/workflow_runtime/`
- `packages/core/agent_runtime/`（V1.3 新增 critic agent）
- `packages/core/db.py`
- `docs/agents/prompts/critic-v1.md`

## 使用 / 入口

API：

```http
POST /api/projects/{project_id}/chapters/{chapter_id}/review
Content-Type: application/json

{ "mock_providers": { "critic": [<json>] } }   # 可选；缺省走 ModelRouter + 失败转移

# 审查报告返回后：
POST /api/runs/{run_id}/resume
Content-Type: application/json

{ "human_input": { "approved": true } }

# 驳回并改稿：
{ "human_input": { "approved": false, "revise": true, "note": "禁用词命中，请改写后重审" } }
```

## 维护注意点

- `review_report.warnings` 是信息性提示，不阻断 run（FATAL 仅在无 draft / author 拒绝 / status 不合法时）。
- 字数检查基于字符数 `len(prose)`；中文按字符计算，不做分词。
- **critic 报告**是**advisory**信息：UI 必须容忍 `critic_report=null` / `critic_status='failed'` 状态；不得因 critic 缺失而禁用批准/驳回按钮。
- critic 输入构造：当前节点直接查 `chapters.plan_json` + `drafts.content` + `hooks` 表（status ∈ OPEN/ACTIVE/ESCALATED，按 importance 降序截断 10 条）；不接 context_engine 完整产物。
- Quality 评分管线属 Sprint 6；V1.3 critic 仅做建议性报告，**不**参与 quality_reports 评分。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4/6、`docs/agents/agent-contracts-v0.md` §7.4、`docs/agents/prompts/critic-v1.md`。

## revise 闭环（Sprint 5，关闭 deviation #1）

「驳回并改稿」闭环已实现：resume `{approved:false, revise:true, note?}` → run 以 FAILED 收尾（
error 字段写 `rejected-for-revision` 与普通拒绝区分）、chapter 保持 DRAFTED、note 落
`plan_json.revision_note`。随后前端可「人工改稿」（POST drafts，已有）或重跑 chapter-write
（DRAFTED 允许，S4 已支持）产生新 draft 版本，再发起 review —— 闭环成立。

实现口径（deviation 登记 §4.2 #1 关闭说明）：引擎 `workflow_runs.status` CHECK 虽含
`CANCELLED` 且 `_finalize_run` 支持该终态，但 `engine._run_nodes` 无产生 CANCELLED 的触发路径
（节点成功必 COMPLETED、异常必 FAILED，节点内改 ctx 的 status 会被 `_finalize_run` 无条件覆盖），
且 DoD 约束 engine/DDL 不动。故采用改动最小的合法方案：沿用 FAILED 终态 + error 字段
`rejected-for-revision` 标识。若后续引擎支持显式取消语义（如节点抛 CancelRequested），
可平滑迁移为 CANCELLED，本工作流语义不变。