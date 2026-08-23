# domain.relationship（人物关系领域）

> 职责：人物关系（`relationships`）+ 关系变更事件（`relationship_events` 表）的 CRUD 与业务规则，对应 PRD §21。
> 状态：空骨架（规划 Sprint 1）。

## 职责与边界
做：关系定义 / 关系变更事件追加；为 Observer agent 提供「关系演进」事实来源。
不做：关系推理（属 LLM 任务）。

## 对外接口（规划中）
- `Service.upsert_relationship / add_event / history(character_id)`

## 依赖
- 上游：`packages/core/db.py`、`packages/domain/character/`

## 使用 / 入口
待实现（Sprint 1）。

## 维护注意点
- 关系类型枚举由 Service 层定义；不允许自由字符串。
- 关系事件必须可追溯到 commit_id（便于回滚时一并撤销）。
- 权威文档：`database/migrations/0001_init.sql`、PRD §21。