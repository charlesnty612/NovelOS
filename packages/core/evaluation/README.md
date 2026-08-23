# core.evaluation（评估 Harness）

> 职责：质量评估 Harness——黄金数据集（`eval_golden_datasets`）+ 回归 runner（`eval_regression_runs`）+ quality-scoring-v0 六子分；Sprint 4 起骨架，Sprint 6 评分管线完整化。
> 状态：空骨架（规划 Sprint 4 骨架，Sprint 6 评分管线）。

## 职责与边界
做：管理黄金样本、跑回归对比、计算 quality-scoring-v0 六子分、Guardrail 五硬门槛检查（3 硬 + Q6/Q8）。
不做：业务写作编排（属 workflow）；UI 展示（属 Sprint 5）。

## 对外接口（规划中）
- `EvalRunner.run(dataset_id) -> RegressionReport`
- `QualityScorer.score(chapter) -> {六子分}`
- `Guardrail.check(chapter) -> {pass, violations}`

## 依赖
- 上游：`packages/domain/chapter/`、`packages/workflows/chapter_review/`
- 下游：`tests/evals/`（黄金数据集测试）

## 使用 / 入口
待实现（Sprint 4 骨架，Sprint 6 完整）。

## 维护注意点
- 五硬门槛（PRD §86）+ 爽感 H-1~H-5 必须在 Sprint 6 完成；阻断/警告行为须有测试覆盖。
- 黄金数据集条目必须可追溯到具体章节与状态版本（state_version）。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4/6/11、PRD §82-86。