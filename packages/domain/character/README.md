# domain.character（角色领域）

> 职责：角色定义侧（``characters`` 表）+ 角色状态侧（``character_states`` 表）的 CRUD 与业务规则，对应 PRD §16-17 Definition/State 分离。
> 状态：已实现 Sprint 1。

## 职责与边界

**做**：
- 创建角色（同一事务插入 ``characters`` 行 + ``character_states`` 首行 v1）。
- 查询 / 列表（JOIN 取 max(state_version) 的 state_json）。
- 部分更新定义侧字段（``name / role / core_json / visibility / who_knows``）。
- 删除角色（同一事务级联删除 ``character_states`` 行）。
- 列某角色的全部 state 历史版本（``GET /characters/{id}/states``，S2 起用于审计）。

**不做**：
- 不实现 State Delta 通道（属 Sprint 2，Sprint 1 直接覆盖 ``core_json``）。
- 不实现字段级权限过滤（属 ``packages/core/context_engine/``）。
- 不创建后续 state 版本（仅初始化 v1；S2 起由 Commit 工作流追加）。

## 对外接口

| 名称 | 来源 | 说明 |
|---|---|---|
| `Character` | `packages/domain/character/models.py` | 完整表示，含 ``latest_state_version / latest_state_json`` |
| `CharacterCreate` | `packages/domain/character/models.py` | 创建请求体；role 默认 supporting |
| `CharacterUpdate` | `packages/domain/character/models.py` | 部分更新请求体；只允许定义侧字段 |
| `CharacterState` | `packages/domain/character/models.py` | 单个 state 快照行 |
| `CharacterRole` | `packages/domain/character/models.py` | role 枚举（7 个） |
| `VisibilityLevel` | `packages/domain/character/models.py` | visibility 枚举（4 个） |
| `CharacterService` | `packages/domain/character/service.py` | 领域服务类，构造需 ``db_path`` |
| `CharacterService.create(project_id, payload)` | `packages/domain/character/service.py` | 一事务插两表，返回含 latest state |
| `CharacterService.get(character_id)` | `packages/domain/character/service.py` | JOIN latest state，不存在返回 None |
| `CharacterService.list_by_project(project_id)` | `packages/domain/character/service.py` | 按 created_at 升序，含 latest state |
| `CharacterService.update(character_id, payload)` | `packages/domain/character/service.py` | 仅改定义侧；core_json 直接覆盖（S2 应走 Delta） |
| `CharacterService.delete(character_id)` | `packages/domain/character/service.py` | 一事务级联删除 states + character |
| `CharacterService.list_states(character_id)` | `packages/domain/character/service.py` | 全 state 历史；角色不存在返回 None |
| `POST /api/projects/{pid}/characters` | `packages/core/api/routers/characters.py` | 201 创建（自动写 state v1） |
| `GET /api/projects/{pid}/characters` | `packages/core/api/routers/characters.py` | 200 列表 |
| `GET /api/characters/{id}` | `packages/core/api/routers/characters.py` | 200/404 |
| `PATCH /api/characters/{id}` | `packages/core/api/routers/characters.py` | 200/404/422 |
| `DELETE /api/characters/{id}` | `packages/core/api/routers/characters.py` | 204/404 |
| `GET /api/characters/{id}/states` | `packages/core/api/routers/characters.py` | 200/404 |

## 依赖

- 上游：`packages/core/db.py`、`packages/core/ids.py`
- 下游：`packages/core/api/routers/characters.py`、`packages/domain/project/service.py`（用于「project 不存在」404 早返回）

## 使用 / 入口

```python
from packages.domain.character.service import CharacterService
from packages.domain.character.models import CharacterCreate

svc = CharacterService(db_path)
payload = CharacterCreate(name="林夕", role="protagonist", core_json={"性格": "内敛"})
row = svc.create(project_id="prj_xxx", payload=payload)
# row 含 latest_state_version=1, latest_state_json={}

# 列全部 state 历史
states = svc.list_states(row["character_id"])  # [{"state_version": 1, "state_json": {}, ...}]
```

HTTP：
```bash
curl -X POST http://127.0.0.1:8000/api/projects/prj_xxx/characters \
  -H 'Content-Type: application/json' \
  -d '{"name":"林夕","role":"protagonist"}'

curl http://127.0.0.1:8000/api/characters/char_xxx/states
```

## 维护注意点

- **Definition/State 分离**（PRD §17）：``core_json`` 写性格/价值观/背景/核心创伤/基本能力；
  ``state_json`` 写当前地点/情绪/目标/认知/关系/伤势/资源，由 ``character_states`` 行快照追加。
- **创建时同步写 v1**：「角色存在但无任何 state」是非法状态。Service.create 在同一事务内插两行。
- **删除必须级联**：SQLite DDL 未声明 ``ON DELETE CASCADE``，Service.delete 显式级联。
- **JSON 列序列化**：``core_json`` / ``state_json`` / ``who_knows`` 三列统一走
  ``json.dumps(ensure_ascii=False)`` 写入，反序列化在 ``_row_to_character`` / ``_row_to_state``。
- **Definition 变更**：Sprint 1 直接覆盖 ``core_json``；S2 起应走 State Delta 通道（按 PRD §17），
  Service 方法注释保留说明。
- **权威文档**：`database/migrations/0001_init.sql`（characters / character_states）、PRD §16-17。