# core.context_engine（上下文引擎）

> 职责：按角色（Director/Writer/Observer/Critic）组装 LLM 上下文——知识权限过滤、相关剧情检索、记忆压缩、token 预算分配。
> 状态：空骨架（规划 Sprint 3）。

## 职责与边界
做：依据 `knowledge-permission-v0.md` 过滤可见字段，组装结构化 prompt context，控制 token 上限。
不做：直接调用 LLM；不持久化生成结果（属 workflow）。

## 对外接口（规划中）
- `ContextBuilder.build(role, project_id, scope) -> Context`
- `Context.window_budget(role) -> int`

## 依赖
- 上游：`packages/domain/*`、`packages/core/story_state/`
- 外部：MVP 用 SQLite + numpy 余弦（`docs/impl/IMPLEMENTATION-PLAN-v0.md` 风险登记）

## 使用 / 入口
待实现（Sprint 3）。

## 维护注意点
- 严格遵循 PRD §24-27 与 `docs/state-model/knowledge-permission-v0.md`。
- 检索兜底 LanceDB/Chroma 留待 Sprint 3 决策。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 3、PRD §24-27。