# domain.world（世界设定领域）

> 职责：世界 / 地点 / 阵营 / 世界规则（`worlds` / `locations` / `factions` / `world_rules` 表）的 CRUD 与一致性，对应 PRD §18。
> 状态：空骨架（规划 Sprint 1）。

## 职责与边界
做：世界观四张表的增删改查与跨表引用校验（地点所属世界、阵营归属等）。
不做：地理拓扑计算、规则冲突推理（属后续 AI 增强）。

## 对外接口（规划中）
- `Service.create_world / add_location / add_faction / add_rule`
- `Service.get_world_with_descendants(world_id)`

## 依赖
- 上游：`packages/core/db.py`

## 使用 / 入口
待实现（Sprint 1）。

## 维护注意点
- 物理外键约束需通过 Service 层保证（`worlds` 删除前必须先清理地点 / 阵营）。
- 权威文档：`database/migrations/0001_init.sql`、PRD §18。