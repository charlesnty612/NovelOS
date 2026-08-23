# domain.world（世界设定领域）

> 职责：地点 / 阵营 / 世界规则（`locations` / `factions` / `world_rules` 表）的 CRUD 与一致性，对应 PRD §18。
> 状态：**已实现（Sprint 1）**。

## 职责与边界
- 做：三类世界实体的 create / get / list / update / delete；引用完整性校验（project 必须存在；location 被 plot_events 引用时拒绝删除）；visibility / who_knows 字段校验。
- 不做：地理拓扑计算、规则冲突推理（属后续 AI 增强）；写入权限字段的字段级权限由 Service 在 data_json 内部做过滤（S2+ 再细化）。

## 对外接口

### Python（`packages.domain.world.service.WorldService`）
构造函数：`WorldService(conn: sqlite3.Connection)`。

locations：
- `create_location(project_id, name, statement='', data=None, visibility=None, who_knows=None) -> WorldEntity`
- `get_location(location_id) -> WorldEntity | None`
- `list_locations(project_id) -> list[WorldEntity]`
- `update_location(location_id, *, name=None, statement=None, data=None, visibility=None, who_knows=None) -> WorldEntity`
- `delete_location(location_id) -> None`（被 plot_events 引用时抛 `ReferencedError`）

factions：`create_faction / get_faction / list_factions / update_faction / delete_faction`，签名同 locations；ID 前缀 `fac_`，默认 visibility=VISIBLE。

world_rules：`create_world_rule / get_world_rule / list_world_rules / update_world_rule / delete_world_rule`，签名同 locations；ID 前缀 `wrule_`，默认 visibility=PUBLIC。

ID 前缀：`loc_` / `fac_` / `wrule_`（12 位 hex）。

### HTTP（FastAPI，自动发现至 `/api` 前缀）
- `POST   /api/projects/{pid}/locations`
- `GET    /api/projects/{pid}/locations`
- `GET    /api/locations/{location_id}`
- `PATCH  /api/locations/{location_id}`
- `DELETE /api/locations/{location_id}`
- `POST   /api/projects/{pid}/factions` / `GET .../factions` / `GET /api/factions/{id}` / `PATCH ...` / `DELETE ...`
- `POST   /api/projects/{pid}/world-rules` / `GET .../world-rules` / `GET /api/world-rules/{id}` / `PATCH ...` / `DELETE ...`

错误码：
- 404：实体不存在
- 409：location 被 plot_events 引用，无法删除
- 422：project 不存在 / visibility 非法 / 字段类型错误

## 依赖
- 上游：`packages/core/db.py`（sqlite 连接）
- 不依赖其他领域包

## 使用 / 入口
```python
from packages.core.db import get_connection
from packages.domain.world import WorldService

conn = get_connection("data/novelos.db")
svc = WorldService(conn)
loc = svc.create_location(project_id="prj_x", name="云海城", statement="...")
```

REST 入口：`POST /api/projects/{pid}/locations` 等（由 `packages/core/api/main.py` 通过 `discover_routers()` 自动挂载）。

## 维护注意点
- `*_json` 列：写入用 `json.dumps(ensure_ascii=False)`，读出用 `json.loads`。
- `created_at` / `updated_at`：使用本包自建的 `now_iso()`（UTC ISO-8601），不依赖 `packages.core.ids`（并行代理 A 正在创建）。
- 删除 location 前会检查 `plot_events.location_id`，避免悬空 FK。
- DDL 权威在 `database/migrations/0001_init.sql`（line 86-134），本服务不修改 DDL。
- WorldEntity.id_field / id_prefix 字段便于通用方法按实体类型构造 SQL 列名。

## 测试
- `tests/integration/test_world_api.py`：覆盖三类实体的 CRUD 主链路 + 404 / 422 / 409 负例。