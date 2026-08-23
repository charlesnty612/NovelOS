# domain（领域服务层）

> 职责：NovelOS 的领域业务服务——围绕 Project / Character / World / Plot / Chapter / Timeline / Relationship / Hooks 八个聚合根，对应 PRD §15-22 与 `database/migrations/0001_init.sql` 中 28 张业务表的 CRUD 与业务规则。
> 状态：空骨架（规划 Sprint 1）。

## 职责与边界
做：每个子包暴露领域 Service（纯业务规则，不直接依赖 FastAPI）；读写 SQLite（`packages/core/db.py`）；保持「UI 不绕过 DB」的可测试性。
不做：HTTP 路由（属 FastAPI 后续接入）；LLM 调用（属 `packages/agents/`）。

## 对外接口（规划中）
- 各子包 Service：`character.Service` / `world.Service` / `plot.Service` / `timeline.Service` / `relationship.Service` / `hooks.Service`
- 入口契约：`create / get / list / update / delete` + 领域专有方法

## 依赖
- 上游：`packages/core/db.py`、`packages/core/logging_config.py`
- 下游：`packages/core/api` 路由（Sprint 1）、`packages/core/context_engine/`（Sprint 3）、各 workflow

## 使用 / 入口
待实现（Sprint 1）。

## 维护注意点
- Service 层不持有 SQL 模板字面量散落；统一走 `get_connection`。
- 文本主键（`prj_/char_/...`）由 Service 生成，业务层不直接构造 ID。
- 权威文档：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 1、PRD §15-22。