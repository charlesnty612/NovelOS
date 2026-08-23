# domain.plot（情节图领域）

> 职责：情节图节点 / 边（`plot_graph_nodes` / `plot_graph_edges` 表）+ 章节大纲（`chapter_outlines`）的 CRUD 与一致性，对应 PRD §19。
> 状态：空骨架（规划 Sprint 1）。

## 职责与边界
做：节点增删改查、边连接校验（避免自环 / 重复边）、大纲绑定到章节。
不做：情节推演（图遍历推理属 Sprint 10 Simulation）。

## 对外接口（规划中）
- `Service.add_node / add_edge / bind_outline`
- `Service.subgraph(root_node_id, depth)`

## 依赖
- 上游：`packages/core/db.py`、`packages/domain/chapter/`（大纲绑定）

## 使用 / 入口
待实现（Sprint 1）。

## 维护注意点
- 边类型枚举（`cause / contrast / callback / parallel`）由 Service 层校验，不允许自由字符串。
- 权威文档：`database/migrations/0001_init.sql`、PRD §19。