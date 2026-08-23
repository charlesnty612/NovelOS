# domain.project（项目领域）

> 职责：Project 聚合根的 CRUD 与业务规则，对应 ``projects`` 表（``database/migrations/0001_init.sql`` line 34-44）。
> 状态：已实现 Sprint 1。

## 职责与边界

**做**：
- 创建 / 查询 / 列表 / 部分更新 / 删除项目。
- 主键 ``project_id`` 由 Service 层用 ``new_id("prj")`` 生成，业务层不直接构造 ID。
- 删除策略：存在子记录（characters / chapters 等）时由 SQLite FK 约束拒绝，
  router 翻译为 409 Conflict。

**不做**：
- 不实现项目级业务编排（属后续 Sprint 的 workflow）。
- 不实现权限 / 协作能力（属后续 Sprint）。

## 对外接口

| 名称 | 来源 | 说明 |
|---|---|---|
| `Project` | `packages/domain/project/models.py:39` | 完整表示，含 8 个 DB 列 |
| `ProjectCreate` | `packages/domain/project/models.py:19` | 创建请求体；status 默认 ACTIVE，不暴露 |
| `ProjectUpdate` | `packages/domain/project/models.py:29` | 部分更新请求体，所有字段可选 |
| `ProjectStatus` | `packages/domain/project/models.py:14` | 字面量枚举 ``ACTIVE/PAUSED/ARCHIVED`` |
| `ProjectService` | `packages/domain/project/service.py:42` | 领域服务类，构造需 ``db_path`` |
| `ProjectService.create(payload)` | `packages/domain/project/service.py:49` | 返回完整行 dict |
| `ProjectService.get(project_id)` | `packages/domain/project/service.py:69` | 不存在返回 ``None`` |
| `ProjectService.list()` | `packages/domain/project/service.py:80` | 按 created_at 升序 |
| `ProjectService.update(project_id, payload)` | `packages/domain/project/service.py:91` | 仅更新 payload 中提供的字段 |
| `ProjectService.delete(project_id)` | `packages/domain/project/service.py:124` | 子记录存在时抛 ``sqlite3.IntegrityError`` |
| `ProjectService.has_children(project_id)` | `packages/domain/project/service.py:140` | 是否有 characters / chapters 子记录 |
| `POST /api/projects` | `packages/core/api/routers/projects.py:23` | 201 创建 |
| `GET /api/projects` | `packages/core/api/routers/projects.py:35` | 200 列表 |
| `GET /api/projects/{project_id}` | `packages/core/api/routers/projects.py:40` | 200/404 |
| `PATCH /api/projects/{project_id}` | `packages/core/api/routers/projects.py:48` | 200/404/422 |
| `DELETE /api/projects/{project_id}` | `packages/core/api/routers/projects.py:58` | 204/404/409 |

## 依赖

- 上游：`packages/core/db.py`（`get_connection`）、`packages/core/ids.py`（`new_id` / `now_iso`）
- 下游：`packages/core/api/routers/projects.py`（router 注册）、`packages/core/api/main.py`（自动发现）

## 使用 / 入口

```python
from packages.domain.project.service import ProjectService
from packages.domain.project.models import ProjectCreate

svc = ProjectService(db_path)
payload = ProjectCreate(name="末日孤舟", premise="AI 觉醒", genre="科幻", target_words=80000)
row = svc.create(payload)        # {"project_id": "prj_xxx", "status": "ACTIVE", ...}
fetched = svc.get(row["project_id"])
```

HTTP：
```bash
curl -X POST http://127.0.0.1:8000/api/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"末日孤舟","genre":"科幻","target_words":80000}'
```

## 维护注意点

- **DDL 权威**：表结构在 ``database/migrations/0001_init.sql``，本包不修改 DDL。
- **status 默认值**：DB DEFAULT 是 ``ACTIVE``，Service 创建时显式传 ``ACTIVE``，便于将来调整默认时只改一处。
- **JSON 列**：本表无 JSON 列；如后续加 JSON 列，参考 ``character`` 包的 ``_dump_json`` / 解析模式。
- **删除 409**：依赖 SQLite FK 约束抛 ``IntegrityError``；router 层翻译；不要在 Service 层吞此异常。
- **部分更新**：`update` 用 pydantic ``model_dump(exclude_unset=True)`` 区分「未提供」与「显式置 None」；
  目前没有必为 None 的字段，预留语义给未来扩展。
- **权威文档**：`docs/impl/IMPLEMENTATION-PLAN-v0.md` §2 Sprint 1、PRD §15-22。