# workflows.chapter_review（章节评审）

> 职责：对本章草稿做最小基本检查（字数偏离 ±15%、禁用词扫描）+ Human 审批。完成后 chapters.status DRAFTED→REVIEWED。
> 状态：Sprint 4-A 已实现（MVP 简化版）；Sprint 5 补全「驳回并改稿（revise）」闭环（PRD §59/§87）。

## 节点列表

| node_id | kind | 说明 |
|---|---|---|
| `basic_checks` | Transform | 校验草稿存在性；字数偏离 target ±15% 记 warning；扫描 forbidden_words（默认 `["仿佛", "如同", "本章目标"]`）。输出 `review_report` 进 ctx |
| `author_review` | Human | 抛 `PauseRequested(payload=review_report)` 等 author 决议；human_input 三态（见下） |
| `mark_reviewed` | State | chapters.status DRAFTED→REVIEWED（仅当 author_review 通过）；revise 分支写 revision_note 并收尾 |

注册名：`chapter-review`

## 输入 / 输出

- 输入：`chapter_id`（必须有 draft）。
- 输出：`run_id` + status。PAUSED 时返回 `pause_payload`（含 review_report）；resume 三态：

| human_input | run 终态 | chapters.status | 附带效果 |
|---|---|---|---|
| `{"approved": true}` | COMPLETED | REVIEWED | — |
| `{"approved": false}` | FAILED（error=author_review 未通过…） | 保持 DRAFTED | — |
| `{"approved": false, "revise": true, "note": str?}` | FAILED（error=`rejected-for-revision`） | 保持 DRAFTED | `note` 追加进 `plan_json.revision_note`（无 note 时移除该键），供下次 chapter-write 的 load_plan 参考 |

## 失败语义

- 无 draft → basic_checks 抛错 → run FAILED。
- resume approved=false（非 revise）→ mark_reviewed 抛错 → run FAILED，chapters.status 保持 DRAFTED。
- resume revise:true → mark_reviewed 写 revision_note 后抛 `_RejectForRevision` → run FAILED（error=`rejected-for-revision`），chapters.status 保持 DRAFTED。
- chapters.status 非 DRAFTED → mark_reviewed 抛错 → run FAILED。

## 依赖

- `packages/core/workflow_runtime/`
- `packages/core/db.py`

## 使用 / 入口

API：

```http
POST /api/projects/{project_id}/chapters/{chapter_id}/review
Content-Type: application/json

{ "mock_providers": {} }   # 本工作流无需 agent

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
- Critic / Evaluation 评分管线属 Sprint 6；本 Sprint 仅 MVP 基本检查。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4/6、`docs/agents/agent-contracts-v0.md` §7.4。

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