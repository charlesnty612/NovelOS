# workflows.chapter_review（章节评审）

> 职责：对本章草稿做最小基本检查（字数偏离 ±15%、禁用词扫描）+ Human 审批。完成后 chapters.status DRAFTED→REVIEWED。
> 状态：Sprint 4-A 已实现（MVP 简化版）。

## 节点列表

| node_id | kind | 说明 |
|---|---|---|
| `basic_checks` | Transform | 校验草稿存在性；字数偏离 target ±15% 记 warning；扫描 forbidden_words（默认 `["仿佛", "如同", "本章目标"]`）。输出 `review_report` 进 ctx |
| `author_review` | Human | 抛 `PauseRequested(payload=review_report)` 等 author 决议；`human_input={"approved": true}` 通过 |
| `mark_reviewed` | State | chapters.status DRAFTED→REVIEWED（仅当 author_review 通过） |

注册名：`chapter-review`

## 输入 / 输出

- 输入：`chapter_id`（必须有 draft）。
- 输出：`run_id` + status。PAUSED 时返回 `pause_payload`（含 review_report）；resume approved=true → COMPLETED + status=REVIEWED。

## 失败语义

- 无 draft → basic_checks 抛错 → run FAILED。
- resume approved=false → mark_reviewed 抛错 → run FAILED，chapters.status 保持 DRAFTED。
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
```

## 维护注意点

- `review_report.warnings` 是信息性提示，不阻断 run（FATAL 仅在无 draft / author 拒绝 / status 不合法时）。
- 字数检查基于字符数 `len(prose)`；中文按字符计算，不做分词。
- Critic / Evaluation 评分管线属 Sprint 6；本 Sprint 仅 MVP 基本检查。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4/6、`docs/agents/agent-contracts-v0.md` §7.4。

## 已知 deviation

- **review reject 即终局**：当前 MVP 实现中，author 拒绝（`approved=false`）会让 `mark_reviewed` 抛错 → run FAILED，chapter 保持 `DRAFTED`；新 draft 版本可在 `chapter-write` 流程内追加（参见 `packages/workflows/chapter_write/README.md` 维护注意点）。PRD §59 / §87 描述的「人工修改后重审」闭环（`revise` 语义、自动回到 `author_review` 节点）本 Sprint **未实现**，将于后续版本补齐。