# domain.plot（情节图领域）

> 职责：情节事件 / 时间线索引 / 关系当前态（`plot_events` / `timeline_events` / `relationships` 表）的 CRUD 与一致性，对应 PRD §19 Plot Graph、§20 Timeline。
> 状态：**已实现（Sprint 1）**。

## 职责与边界
- 做：plot_events 的 CRUD；cause / effects / participants 引用完整性校验；时间线自动同步（创建 event 时若 `time.timeline_day` 存在 → 自动插入对应 timeline_events）；timeline_events 显式增删；relationships 只读列表。
- 不做：情节推演（图遍历推理属 Sprint 10 Simulation）；relationships 的写入（S2 State Delta 驱动）。

## 对外接口

### Python（`packages.domain.plot.service.PlotService`）
构造函数：`PlotService(conn: sqlite3.Connection)`。

plot_events：
- `create_event(project_id, type, *, cause=None, effects=None, participants=None, location_id=None, time=None, status=None, introduced_chapter_id=None, visibility=None, who_knows=None) -> PlotEvent`
  - `type` ∈ {revelation, conflict, decision, encounter, transition, other}
  - `time` 默认 `{"timeline_day": 1, "in_story_date": None}`
  - `cause` / `effects` 中每个 event_id 必须已存在（否则 422）
  - `participants` 中每个 character_id 必须已存在（否则 422）
  - 创建后若 `time.timeline_day` 存在 → 自动插入 `timeline_events` 同事务索引行
- `get_event(event_id) -> PlotEvent | None`
- `list_events(project_id, *, type=None, status=None) -> list[PlotEvent]`
- `update_event(event_id, *, type=None, cause=None, effects=None, participants=None, location_id=None, time=None, status=None, introduced_chapter_id=None, visibility=None, who_knows=None) -> PlotEvent`
- `delete_event(event_id) -> None`（被其他事件的 cause/effects 引用 → 409；级联删除对应 timeline_events）

timeline_events：
- `create_timeline_event(project_id, event_id, day_index, *, time_ref=None, description=None) -> TimelineEvent`
- `list_timeline_events(project_id) -> list[TimelineEvent]`（按 day_index 排序）
- `delete_timeline_event(timeline_event_id) -> None`

relationships：
- `list_relationships(project_id) -> list[Relationship]`（S1 只读）

ID 前缀：`event_` / `tle_` / `rel_`（12 位 hex）。

### HTTP（FastAPI，自动发现至 `/api` 前缀）
- `POST   /api/projects/{pid}/events`                body: `{type, cause?, effects?, participants?, location_id?, time?, status?, introduced_chapter_id?, visibility?, who_knows?}`
- `GET    /api/projects/{pid}/events?type=&status=`
- `GET    /api/events/{event_id}`
- `PATCH  /api/events/{event_id}`
- `DELETE /api/events/{event_id}`
- `GET    /api/projects/{pid}/timeline`              按 day_index 排序
- `POST   /api/projects/{pid}/timeline`              body: `{event_id, day_index, time_ref?, description?}`
- `DELETE /api/timeline/{timeline_event_id}`
- `GET    /api/projects/{pid}/relationships`         S1 只读

错误码：
- 404：event / timeline_event 不存在
- 409：event 被其他 event 引用或被 timeline 引用
- 422：type/status 非法；cause / participants 引用了不存在的 id；time 缺 timeline_day；project 不存在

## 依赖
- 上游：`packages/core/db.py`（sqlite 连接）
- 不依赖其他领域包（character 引用通过 SQL 字符串校验，不 import character 包，避免循环）

## 使用 / 入口
```python
from packages.core.db import get_connection
from packages.domain.plot import PlotService

conn = get_connection("data/novelos.db")
svc = PlotService(conn)
ev = svc.create_event(project_id="prj_x", type="revelation",
                       time={"timeline_day": 3})
# 此时 timeline_events 自动出现一条 day_index=3 的索引行
```

REST 入口：`POST /api/projects/{pid}/events` 等（由 `packages/core/api/main.py` 通过 `discover_routers()` 自动挂载）。

## 维护注意点
- `*_json` 列：写入用 `json.dumps(ensure_ascii=False)`，读出用 `json.loads`。
- `time.timeline_day` 是触发自动 timeline_events 同步的唯一信号（PRD §20）。
- cause / effects / participants 删除引用检查用 `json_each`（DDL 中 json_* 列已在 SQLite 内置）。
- relationships 的写入由 S2 State Delta 驱动；S1 仅暴露只读端点。
- DDL 权威在 `database/migrations/0001_init.sql`（line 142-192），本服务不修改 DDL。
- 本包自建 `_util.new_id` / `now_iso`，不依赖 `packages.core.ids`（并行代理 A 正在创建）。

## 测试
- `tests/integration/test_plot_api.py`：覆盖 event 主链路、引用完整性（cause/participants 422）、自动 timeline 同步、过滤参数、删除引用 409、timeline 显式增删、relationships 只读。