# packages/domain/volume

多卷组织域服务（V3.4）。

## 职责

- `VolumeService`：`volumes` 表 CRUD + 业务规则 + 章节归属（`chapters.volume_id`），
  对齐迁移 `database/migrations/0015_volumes.sql` 的 DDL。

## 关键业务规则

1. **同 project 下 `number` 唯一**：依赖 DB `UNIQUE(project_id, number)`；service
   层预检 + `IntegrityError` 兜底 → `VolumeConflictError`（router 转 409）。
2. **active 卷单例**：create 前查同 project 的 active 卷，存在 → 冲突（须先 seal）；
   list / assign 不强制（历史 sealed 卷不受影响）。
3. **`seal(volume_id)`**：同事务内 status='sealed' + `terminal_snapshot_json`
   写入该项目 `story_states` 最新快照（无快照存 `{}`）+ 刷新 `updated_at`；
   重复 seal → `VolumeConflictError`。
4. **sealed 卷禁止挂章**：`assign_chapter` 对 sealed 卷拒绝。

## 文件

- `models.py`：Pydantic 模型。
- `service.py`：`VolumeService`（每方法内 `get_connection` 开连接、
  `try/finally` 关闭；主键 `new_id("vol")`，时间戳 `now_iso()`）。

## REST 入口

见 `packages/core/api/routers/volumes.py`（本包不感知 HTTP）。
