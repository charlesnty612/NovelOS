# workflows（业务流程编排层）

> 职责：把 `packages/agents/*` 的角色与 `packages/core/*` 的基础设施组合成可执行的业务工作流；底层依赖 `packages/core/workflow_runtime/`（DAG 执行器）。
> 状态：空骨架（规划 Sprint 4 首批：chapter_plan / chapter_write / chapter_review / chapter_commit；Sprint 1 含 project_init；Sprint 10 含 simulation）。

## 职责与边界
做：声明 DAG 节点（AI / State / Transform / Human / Simulation 五类）、节点输入/输出契约、Human Node 挂起点。
不做：DAG 引擎实现（属 `packages/core/workflow_runtime/`）；节点内部逻辑（属 agents / core 子包）。

## 对外接口（规划中）
- 每条工作流：`Workflow.run(inputs) -> RunHandle`
- 子包：`project_init / chapter_plan / chapter_write / chapter_review / chapter_commit / simulation`

## 依赖
- 上游：`packages/core/workflow_runtime/`、`packages/core/agent_runtime/`、`packages/agents/*`、`packages/domain/*`
- 下游：`tests/workflow/`（无头跑测试）

## 使用 / 入口
待实现（Sprint 1 project_init；Sprint 4 章节四件套；Sprint 10 simulation）。

## 维护注意点
- 工作流节点不写业务规则，只编排；所有规则在对应模块实现。
- Human Node 必须显式挂起点，便于 Sprint 5 Workflow Panel 接管。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 1/4/10、PRD §40、§55-65、§109。

## checkpoint_exclude 机制（Sprint V1.5）

每个 workflow 的 ``WORKFLOW`` dict 可声明 ``checkpoint_exclude``（顶层 ctx 键列表），
在 PAUSE 落盘前由 :class:`packages.core.workflow_runtime.engine.WorkflowEngine` 剔除。
目的：避免大块 payload（observer 7 数组 / critic 报告 / previous_state 快照）写放大
``workflow_runs.checkpoint_json``。

已配置的 workflow（V1.0 性能审计整改）：

| Workflow | 排除字段 | 理由 |
|---|---|---|
| chapter-plan | （无） | 无 Human 节点，不会 PAUSE；无需 exclude |
| chapter-write | （无） | 同上 |
| chapter-review | `review_report` / `critic_report` / `critic_status` / `critic_error` | PAUSE 在 author_review；mark_reviewed 不读这些字段；author_review 的 `__pause_payload__`（含 review_report / critic_report）单独存于 ctx['author_review']，前端 reviewer UI 不受影响 |
| chapter-commit | `observer_input` / `observer_payload` / `delta` / `submit_result` / `snapshot_pre` | PAUSE 在 high_risk_approval；commit 节点仅依赖 `delta_id`（按 id 从 state_deltas 表读 delta 行）+ `needs_high_risk_approval` + `human_input` + `_high_risk_approved`；service 不依赖 ctx['delta'] 内容 |

新增 workflow 的判断标准：
1. 是否有 Human 节点（PAUSE 点）？无 → 无需 exclude。
2. PAUSE 后下一节点（resume 起点）依赖哪些 ctx 顶层键？这些键必须保留。
3. 其余节点已产出但下游不再读的 ctx 字段，可加入 ``checkpoint_exclude``。

## 注册机制（Sprint V1.5）

每条 workflow 在自身包内通过 :mod:`packages.core.workflow_registry` 注册，**不依赖本包顶层**：

- 业务流程包 ``__init__.py`` 在 import 时调用
  ``register_workflow(WORKFLOW["name"], lambda: WORKFLOW)`` 写入注册表；
- 装配入口 ``packages/core/api/main.py:create_app`` 通过
  ``importlib.import_module`` 形式触发本包顶层 import 完成注册。
- 本包顶层仍保留 :func:`get_workflow` / :func:`all_workflows` /
  :func:`register_workflow` 作为对外兼容 façade，全部委托 core 注册表；
  旧测试与脚本可继续使用。

### 如何注册新 workflow

```python
# packages/workflows/<name>/__init__.py
from packages.core.workflow_registry import register_workflow
from .pipeline import WORKFLOW

register_workflow(WORKFLOW["name"], lambda: WORKFLOW)
```

`pipeline.py` 仅暴露 :data:`WORKFLOW` dict（含 ``name`` / ``nodes`` / ``version``），
不再定义 ``register_workflow`` 函数——注册动作统一上移到 ``__init__.py``。