# domain.reference（参照系 canons / extracts）

> 职责：拆书参照系（reference canon + extracts）的查询与级联删除 service；V1.5 起把 router
> 中的直接 SQL 收敛到本 service，路由层仅做参数校验 + 错误映射。
> 状态：V1.5 越层整改完成。

## 职责与边界

**做**：
- `reference_canons` 表（主表）+ `canon_extracts` 表（逐章拆解落库）的读 / 级联删。
- 摘要组装：从 `canon_json` 列解析 `logline` / `spine` 长度 / `emotion_curve` 长度三个
  摘要字段，避免路由层重复解析。
- 级联删除：单事务删 `canon_extracts` + `reference_canons`，事务失败整体回滚（无残留半成品）。

**不做**：
- 创建 / 更新 canon（由 `deconstruct-book` 工作流落库，路由层不直接做）。
- 工作流编排（属于 `packages/workflows/deconstruct_book`）。
- 模型路由 / LLM 调用（属于 `packages/core/model_router`）。

## 对外接口

```python
from packages.domain.reference import ReferenceService, summary_of

service = ReferenceService(db_path)

# 1) 列出项目下全部 active canon 摘要
summaries: list[dict] = service.list_active_summaries(project_id)
# → [{canon_id, project_id, title, reader_profile, status, created_at,
#     logline, spine_count, rhythm_chapter_count}, ...]
#   按 created_at DESC, canon_id DESC 排序

# 2) 取单条 canon 全文 + extracts
detail: dict | None = service.get_canon_detail(canon_id)
# → {canon_id, project_id, title, reader_profile, status,
#    canon_json: 已解析为 dict, report_md, created_at,
#    extracts: [{extract_id, chapter_index, extract_json, created_at}, ...]}
# 不存在 → None

# 3) 级联删除 canon + 关联 extracts（单事务）
ok: bool = service.delete_canon_cascade(canon_id)
# → True 成功 / False canon 不存在

# 4) 摘要字段独立函数（从 sqlite3.Row 或 dict 解析）
summary: dict = summary_of(canon_row)
```

| 接口 | 输入 | 输出 | 失败行为 |
|---|---|---|---|
| `ReferenceService.list_active_summaries(project_id)` | `str` | `list[dict]` 摘要列表 | — |
| `ReferenceService.get_canon_detail(canon_id)` | `str` | `dict \| None` | 不存在返回 `None`（router 转 404） |
| `ReferenceService.delete_canon_cascade(canon_id)` | `str` | `bool` | 不存在返回 `False`（router 转 404）；事务失败整体回滚无残留 |
| `summary_of(canon_row)` | `sqlite3.Row \| dict` | `dict` 含 `logline / spine_count / rhythm_chapter_count` | `canon_json` 解析失败保留原字段，计数=0/logline=空 |

## 依赖

- 上游：`packages.core.db.get_connection`（连接管理 + `PRAGMA busy_timeout = 5000`）。
- 表 schema：迁移 `database/migrations/0004_reference_canon.sql`（V1.0 Sprint 11 引入）。
  - `reference_canons(canon_id PK, project_id, title, reader_profile, status, canon_json, report_md, created_at)`
  - `canon_extracts(extract_id PK, canon_id FK→reference_canons.canon_id, chapter_index, extract_json, created_at)`
  - 索引：`idx_reference_canons_project_id` / `idx_reference_canons_project_created` /
    `idx_canon_extracts_canon_id`。
- 下游：**仅**被 `packages/core/api/routers/reference.py` 调用；路由层不直接写 SQL。

## 使用入口

- REST 路由：`packages/core/api/routers/reference.py`
  - `GET /api/projects/{project_id}/canons` —— 调 `list_active_summaries`
  - `GET /api/canons/{canon_id}` —— 调 `get_canon_detail`
  - `DELETE /api/canons/{canon_id}` —— 调 `delete_canon_cascade`
  - `POST /api/projects/{project_id}/deconstruct` —— 走 `packages.workflows.deconstruct_book`，不经过本 service
- service 入口：`packages/domain/reference/service.py`（类 `ReferenceService` + 函数 `summary_of`）
- 包 re-export：`packages/domain/reference/__init__.py` 同时导出 `ReferenceService` 与 `summary_of`

## 维护注意点

- **删除语义是双表事务级联**：`delete_canon_cascade` 必须先 `DELETE FROM canon_extracts WHERE canon_id=?`
  再 `DELETE FROM reference_canons WHERE canon_id=?`，并 `commit()` 包在同一事务里。
  当前实现也写了显式 SQL（即便 `canon_extracts.canon_id` 上有 `ON DELETE CASCADE` FK），
  便于审计与排错日志，**不要**改成单条 `DELETE FROM reference_canons` 依赖 FK 级联。
- **连接关闭统一由 `finally` 处理**：`get_canon_detail` / `delete_canon_cascade` / `list_active_summaries`
  全部走 `conn = get_connection(...)` + `try/finally conn.close()`，提前 `return` 路径也走 finally；
  改这块时不要在 `try` 块里手动 `conn.close()`（V1.5 健壮性整理已统一）。
- **`canon_json` 不在 service 解析**：list 路径由 `summary_of` 就地抽取 3 个摘要字段（logline +
  spine/rhythm 计数），完整 JSON 不展开；detail 路径返回 `dict`（已 `json.loads`）。调用方若需
  完整原文，可自行再 `json.loads(detail["canon_json"])`（已是 dict）。
- **`summary_of` 解析失败兜底**：`canon_json` 非法 JSON 时保留原 row 字段、`logline=""` +
  计数=0，不抛异常——调用方拿到的是"空摘要 + 完整 row"，符合"展示优先于崩溃"的契约。
- **service 不脱敏**：本 service 没有敏感字段（canon_json 是用户自己存的拆书结果），无脱敏
  边界问题。
- **只被路由层调用**：领域层不应被 `packages.core.api.routers` 之外的模块直接消费；新增调用方
  前请评估是否要走 service 而非直接 SQL（保持 router 越层 SQL 零回归）。
- **作者铁律**：本 README 是项目铁律"每模块自带 README"的一部分；新增 / 修改 service 时同步
  维护本文件对外接口与维护注意点。
