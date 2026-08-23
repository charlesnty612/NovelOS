# domain.character（角色领域）

> 职责：角色定义侧 + 弧线（`characters` / `character_arcs` 表）的 CRUD 与业务规则，对应 PRD §16-17。
> 状态：空骨架（规划 Sprint 1）。

## 职责与边界
做：角色创建 / 查询 / 更新 / 列表；弧线记录；`visibility` 四级与 `who_knows` 字段语义（PRD §4 原则 7）。
不做：知识权限字段级过滤（属 `packages/core/context_engine/`）。

## 对外接口（规划中）
- `Service.create / get / list / update / delete`
- `Service.add_arc(character_id, arc)`

## 依赖
- 上游：`packages/core/db.py`

## 使用 / 入口
待实现（Sprint 1）。

## 维护注意点
- `core_json` / `arc_json` 字段级权限（core/arc）由 Service 层在 JSON 内部做过滤；不依赖 DB 列权限。
- 权威文档：`database/migrations/0001_init.sql`（characters / character_arcs）、PRD §16-17。