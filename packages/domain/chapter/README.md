# domain.chapter（章节领域）

> 职责：Chapter 聚合根的 CRUD + 状态机迁移约束，对应 ``chapters`` 表（DDL 权威见 ``database/migrations/0001_init.sql``）。
> 状态：已实现 Sprint 1。

## 职责与边界

**做**：
- 创建 / 查询 / 列表（按 ``number`` 升序）/ 部分更新 / 删除章节。
- 状态机迁移约束（任务书给死）：``PLANNED → DRAFTED → REVIEWED → COMMITTED → RELEASED``
  顺序推进，或任意阶段回退到 ``PLANNED``；非法跳变由 router 转 409。
- 同 project 内 ``number`` 重复由 Service 预检，重复时抛 ``ChapterNumberConflict``，router 转 409。

**不做**：
- 不实现 Scene / Draft 子表 CRUD（后续 Sprint 扩展）。
- 不驱动状态机迁移（Sprint 4 工作流会驱动，本 Sprint 只暴露 update 端点）。

## 对外接口

| 名称 | 来源 | 说明 |
|---|---|---|
| `Chapter` | `packages/domain/chapter/models.py` | 完整表示，含 ``plan_json / status / visibility / who_knows`` |
| `ChapterCreate` | `packages/domain/chapter/models.py` | 创建请求体；status 默认 PLANNED，不暴露 |
| `ChapterUpdate` | `packages/domain/chapter/models.py` | 部分更新请求体，所有字段可选 |
| `ChapterStatus` | `packages/domain/chapter/models.py` | 字面量枚举（5 个） |
| `ALLOWED_NEXT` | `packages/domain/chapter/models.py` | 状态机迁移白名单（dict） |
| `ChapterService` | `packages/domain/chapter/service.py` | 领域服务类，构造需 ``db_path`` |
| `ChapterService.create(project_id, payload)` | `packages/domain/chapter/service.py` | status=PLANNED；number 重复抛 ``ChapterNumberConflict`` |
| `ChapterService.get(chapter_id)` | `packages/domain/chapter/service.py` | 不存在返回 ``None`` |
| `ChapterService.list_by_project(project_id)` | `packages/domain/chapter/service.py` | 按 number ASC |
| `ChapterService.update(chapter_id, payload)` | `packages/domain/chapter/service.py` | status 非法跳变抛 ``ChapterTransitionError`` |
| `ChapterService.delete(chapter_id)` | `packages/domain/chapter/service.py` | 子记录存在时抛 ``sqlite3.IntegrityError`` |
| `ChapterNumberConflict` | `packages/domain/chapter/service.py` | number 重复异常（业务异常，409） |
| `ChapterTransitionError` | `packages/domain/chapter/service.py` | 状态机非法跳变异常（409） |
| `POST /api/projects/{pid}/chapters` | `packages/core/api/routers/chapters.py` | 201/404/409 |
| `GET /api/projects/{pid}/chapters` | `packages/core/api/routers/chapters.py` | 200/404 |
| `GET /api/chapters/{id}` | `packages/core/api/routers/chapters.py` | 200/404 |
| `PATCH /api/chapters/{id}` | `packages/core/api/routers/chapters.py` | 200/404/409/422 |
| `DELETE /api/chapters/{id}` | `packages/core/api/routers/chapters.py` | 204/404/409 |

## 依赖

- 上游：`packages/core/db.py`、`packages/core/ids.py`
- 下游：`packages/core/api/routers/chapters.py`、`packages/domain/project/service.py`（用于「project 不存在」404 早返回）

## 使用 / 入口

```python
from packages.domain.chapter.service import ChapterService
from packages.domain.chapter.models import ChapterCreate, ChapterUpdate

svc = ChapterService(db_path)
row = svc.create("prj_xxx", ChapterCreate(number=1, title="第一章"))
# {"chapter_id": "ch_xxx", "status": "PLANNED", "number": 1, ...}

# 合法推进
svc.update(row["chapter_id"], ChapterUpdate(status="DRAFTED"))

# 非法跳变 → 抛 ChapterTransitionError
from packages.domain.chapter.service import ChapterTransitionError
try:
    svc.update(row["chapter_id"], ChapterUpdate(status="COMMITTED"))  # PLANNED→COMMITTED 非法
except ChapterTransitionError as e:
    print(f"rejected: {e.current!r} -> {e.target!r}")
```

HTTP：
```bash
curl -X PATCH http://127.0.0.1:8000/api/chapters/ch_xxx \
  -H 'Content-Type: application/json' \
  -d '{"status":"DRAFTED"}'
```

## 维护注意点

- **状态机白名单**（``ALLOWED_NEXT``）：任务书给死，禁止扩展。如果需要新增合法迁移（如 ``REVIEWED → DRAFTED`` 回退），
  必须先在 PRD/IMPLEMENTATION-PLAN 中定夺。
- **number 唯一性**：DB 层无 UNIQUE 索引（DDL 未声明），由 Service 在 INSERT 前预检；并发竞态下可能
  产生重复行（极小概率），Sprint 4 工作流驱动时再考虑 DB 唯一索引升级。
- **JSON 列**：`plan_json` 默认 `{}`；写入前 `json.dumps(ensure_ascii=False)`，读出后 `json.loads`。
- **删除 409**：依赖 SQLite FK 约束（scenes / drafts），由 router 转 409；Service 不吞异常。
- **权威文档**：`database/migrations/0001_init.sql`、PRD §44 Chapter Planner。