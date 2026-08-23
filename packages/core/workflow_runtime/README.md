# core.workflow_runtime（工作流 Runtime）

> 职责：自研最小工作流执行器（节点五类：AI / State / Transform / Human / Simulation），按 `docs/agents/agent-contracts-v0.md` + `PRD §40-65` 设计。
> 状态：Sprint 4-A 已实现（MVP）。

## 职责与边界

做：
- 工作流定义 = 有序 `WorkflowNode` 列表，每节点 `{"node_id", "kind", "fn", "agent_name"}`。
- `WorkflowEngine(db_path).start_with_nodes(name, nodes, chapter_id, initial_ctx, mock_providers)`：插 `workflow_runs` 行，顺序执行每节点（插 `workflow_run_nodes`），每节点完成后写 checkpoint_json + 更新 `current_node`。
- `WorkflowEngine.resume(run_id, nodes, human_input)`：从 checkpoint 恢复 ctx，合并 human_input，从 current_node 下一节点继续；旧的 PENDING 节点行收尾为 SKIPPED。
- Human 节点：fn 抛 `PauseRequested(payload)` → 节点行 PENDING、run PAUSED、checkpoint 落盘。
- AI 节点 fn 内部调 `packages.core.agent_runtime.runner.run_agent(...)`（run_id/node_run_id 传入），`mock_providers` 透传 run_agent 的 mock_script。
- 查询辅助：`runs.list_runs(db_path, project_id)` / `runs.get_run(db_path, run_id)`。

不做：
- 不集成外部工作流引擎（DeterminFlow 评估后不集成，见 `docs/impl/IMPLEMENTATION-PLAN-v0.md` D-I3）。
- 不做具体业务工作流（属 `packages/workflows/*`）。

## 对外接口

- `WorkflowNode(node_id, kind, fn, agent_name=None)` — 节点定义。
- `WorkflowEngine(db_path)` — 构造。
  - `ensure_workflow(name) -> workflow_id`
  - `start_with_nodes(workflow_name, nodes, chapter_id=None, initial_ctx=None, mock_providers=None) -> run_id`
  - `resume(run_id, nodes, human_input=None) -> run_id`
- `PauseRequested(payload)` — Human 节点抛出用。
- `runs.list_runs(db_path, project_id) -> list[dict]`
- `runs.get_run(db_path, run_id) -> dict | None`（含 nodes 数组）

## 依赖

- 上游：`packages/core/db.py`、`packages/core/ids.py`、`packages/core/agent_runtime/runner.py`
- 下游：`packages/workflows/*`、`packages/core/api/routers/workflows.py`

## 使用 / 入口

业务流程注册中心 `packages/workflows/__init__.py` 暴露 `all_workflows()` / `get_workflow(name)`；API router 用 `WorkflowEngine.start_with_nodes()` 启动。

## 维护注意点

- 节点 fn 输出 dict 会被 merge 进 ctx（顶层 key 直接覆盖）；fn 返回 None 视为空 dict。
- ctx 必须保持 JSON 可序列化（路径转 str、datetime 转 ISO 等）；非 JSON 字段会触发 checkpoint 写盘失败。
- 重试逻辑在 `run_agent` 内部，Engine 不重试节点 fn 异常。
- `additionalProperties: false` 的 schema 由 Service 层保证；Engine 只负责流程编排与持久化。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 4、PRD §55-65、§72、`docs/agents/agent-contracts-v0.md`。