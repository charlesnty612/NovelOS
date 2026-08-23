# domain.timeline（时间线领域）

> 职责：时间线事件（`timeline_events` 表）的 CRUD 与一致性校验，对应 PRD §20；为 Director Planner 提供「事件排序 / 时序冲突」基础。
> 状态：空骨架（规划 Sprint 1）。

## 职责与边界
做：事件增删改查、所属章节绑定、相对时序校验（同一 chapter 内事件 `event_order` 严格递增）。
不做：跨章节的全局时间线一致性（属 Sprint 4 workflow 内串联校验）。

## 对外接口（规划中）
- `Service.add_event / list_by_chapter / reorder`

## 依赖
- 上游：`packages/core/db.py`、`packages/domain/chapter/`

## 使用 / 入口
待实现（Sprint 1）。

## 维护注意点
- `event_time` 为 ISO-8601 TEXT；与 `state-delta.schema.json` `date-time` 对齐。
- 权威文档：`database/migrations/0001_init.sql`、PRD §20。