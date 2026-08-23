# agents.critic（Critic）

> 职责：对章节草稿做质量审计——quality-scoring-v0 六子分、Guardrail 五硬门槛（3 硬 + Q6/Q8）、爽感 H-1~H-5；为 chapter_review 工作流提供阻断/警告判定。
> 状态：空骨架（规划 Sprint 6）。

## 职责与边界
做：评审章节草稿，给出质量分与违规清单；为 Evaluation Harness 提供 LLM-as-judge 维度。
不做：评分管线实现（属 `packages/core/evaluation/`）；UI 展示（属 Sprint 5）。

## 对外接口（规划中）
- `run(inputs: CriticInputs) -> CriticVerdict`
- 提示词：`docs/agents/prompts/critic.md`

## 依赖
- 上游：`packages/core/agent_runtime/`、`packages/core/evaluation/`（评分规则）

## 使用 / 入口
待实现（Sprint 6）。

## 维护注意点
- 五硬门槛（PRD §86）必须可机检；LLM 判断仅作辅助，不替代规则。
- 权威文档：PRD §33、§82-86；`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 6。