# domain.ledger（伏笔台账与叙事债务，Sprint 9）

> 职责：Hook（PRD §21，五态机）与 Narrative Debt（PRD §22，v1.1 扩展）的 CRUD + 状态机迁移约束 + 章节外键存在性校验。对应 ``hooks`` 与 ``narrative_debts`` 两张表（DDL 权威见 ``database/migrations/0001_init.sql`` line 197-235）。
> 状态：Sprint 9 实现，本期仅「管理面」（人工维护入口）。

## 职责与边界

**做**：

- 创建 / 查询 / 列表 / 部分更新 / 删除 Hook 与 Debt。
- 状态机迁移约束（任务书给死）：
  - Hook 五态机（OPEN→ACTIVE→ESCALATED→RESOLVED + 任意→ABANDONED）；
  - Debt 四态机（open→acknowledged→paid/forgiven 前进制）。
- 章节外键存在性校验（hook: introduced/expected_payoff/payoff；debt: created/deadline）：
  不存在 → ``ChapterNotFound``，router 转 404。
- visibility 取值校验（PUBLIC / VISIBLE / RESTRICTED / HIDDEN）。
- who_knows（NULL=沿用默认；``[]``=显式置空，对齐 knowledge-permission-v0 §3.1）。

**不做**：

- **不**自动从 ``story_state`` 快照同步（属 Observer 写透职责，本 Sprint 不接入）。
- **不**驱动状态机迁移（Sprint 9 仅暴露 PATCH 端点给人工修正）。
- **不**做逾期判定（属前端 UI 职责，调用 ``GET /projects/{pid}/chapters`` 比对 ``number``）。

## 与 State Delta 的关系

本 CRUD 是**管理面**（人工维护入口）；Observer 的写透路径
（``packages/core/story_state/service.py``）**不动**。两侧潜在的口径冲突：

| 来源 | 含义 |
| --- | --- |
| ``hooks`` / ``narrative_debts`` 表 | 人工维护台账的权威来源；本期 CRUD 直接读写 |
| ``story_states.snapshot_json``（Reader 侧） | Observer 写透的快照展示，里面也有 ``hooks`` / ``debts`` 数组 |

**主会话拍板（2026-08-23）**：以 **state 快照为权威展示**（UI 概览面板仍读快照）；
CRUD 用于人工修正台账。Observer 的写透路径在 Sprint 9 不动；后续若需双向同步，
由主会话重新拍板同步口径（增量 event-sourcing 方案或定时 reconcile）。

## 对外接口

| 名称 | 来源 | 说明 |
|---|---|---|
| `Hook` / `HookCreate` / `HookUpdate` / `HookStatus` / `HOOK_ALLOWED_NEXT` / `HOOK_STATUS_VALUES` | `packages/domain/ledger/models.py` | Hook Pydantic 模型与状态机白名单 |
| `Debt` / `DebtCreate` / `DebtUpdate` / `DebtStatus` / `DEBT_ALLOWED_NEXT` / `DEBT_STATUS_VALUES` | `packages/domain/ledger/models.py` | Debt Pydantic 模型与状态机白名单 |
| `LedgerService` | `packages/domain/ledger/service.py` | 构造需 ``db_path`` |
| `LedgerService.create_hook / get_hook / list_hooks_by_project / update_hook / delete_hook` | `packages/domain/ledger/service.py` | Hook CRUD；章节外键缺失 → ChapterNotFound（404） |
| `LedgerService.create_debt / get_debt / list_debts_by_project / update_debt / delete_debt` | `packages/domain/ledger/service.py` | Debt CRUD |
| `LedgerTransitionError` | `packages/domain/ledger/service.py` | 状态机非法跳变（409，含 ``kind/current/target``） |
| `ChapterNotFound` | `packages/domain/ledger/service.py` | 章节外键缺失（404） |
| `NotFoundError` / `ValidationError` / `LedgerError` | `packages/domain/ledger/service.py` | 通用异常基类 |
| `POST/GET /api/projects/{pid}/hooks` | `packages/core/api/routers/ledger.py` | 201/200；列表可带 ``?status=`` |
| `GET/PATCH/DELETE /api/hooks/{id}` | `packages/core/api/routers/ledger.py` | 200/200/204 |
| `POST/GET /api/projects/{pid}/debts` | `packages/core/api/routers/ledger.py` | 同上 |
| `GET/PATCH/DELETE /api/debts/{id}` | `packages/core/api/routers/ledger.py` | 同上 |

## 依赖

- 上游：`packages/core/db.py`、`packages/core/ids.py`
- 下游：`packages/core/api/routers/ledger.py`、前端 `apps/web/src/pages/bible/LedgerTab.tsx`

## 使用 / 入口

```python
from packages.domain.ledger.service import LedgerService
from packages.domain.ledger.models import (
    HookCreate, HookUpdate, DebtCreate, DebtUpdate,
    LedgerTransitionError, ChapterNotFound,
)

svc = LedgerService(db_path)

# Hook 主链路
hk = svc.create_hook("prj_x", HookCreate(name="黑玉佩秘密", importance=0.9))
svc.update_hook(hk["hook_id"], HookUpdate(status="ACTIVE"))   # OPEN → ACTIVE 合法

try:
    svc.update_hook(hk["hook_id"], HookUpdate(status="ESCALATED"))  # OPEN → ESCALATED 非法
except LedgerTransitionError as e:
    print(f"rejected: hook {e.current!r} -> {e.target!r}")

try:
    svc.update_hook(hk["hook_id"], HookUpdate(payoff_chapter_id="ch_unknown"))
except ChapterNotFound as e:
    print(f"rejected: {e.chapter_id!r} not found")

# Debt 主链路
db = svc.create_debt("prj_x", DebtCreate(description="男主答应调查父亲死亡"))
svc.update_debt(db["debt_id"], DebtUpdate(status="acknowledged"))
svc.update_debt(db["debt_id"], DebtUpdate(status="paid"))
svc.update_debt(db["debt_id"], DebtUpdate(status="acknowledged"))  # 终态 → 409
```

HTTP：
```bash
curl -X POST http://127.0.0.1:18081/api/projects/prj_xxx/hooks \
  -H 'Content-Type: application/json' \
  -d '{"name":"黑玉佩秘密","importance":0.9,"expected_payoff_chapter_id":"ch_yyy"}'
# 201 / 404（chapter 不存在） / 422（name 空 / importance 超界 / visibility 非法）

curl -X PATCH http://127.0.0.1:18081/api/hooks/hook_xxx \
  -H 'Content-Type: application/json' -d '{"status":"RESOLVED"}'
# OPEN → RESOLVED：不在白名单 → 409
```

## 维护注意点

- **状态机白名单**（``HOOK_ALLOWED_NEXT`` / ``DEBT_ALLOWED_NEXT``）：任务书给死，禁止扩展。
- **章节外键**：跨 project 引用允许（不强制同 project）；业务上一般同 project。
- **RESOLVED 时 payoff_chapter_id NULL**：任务书给死——直接允许（不阻断、不 warning），
  对应「先标记 RESOLVED、后补兑现章节」工作流；前端表单允许用户事后补录。
- **逾期判定**：后端**不**判定「payoff chapter 已过且未 RESOLVED」——前端 UI 调
  ``GET /projects/{pid}/chapters`` 拿到最大 number 后与 ``expected_payoff_chapter_id``
  对应章节 number 比对；属展示层职责，避免双写。
- **state 同步**：本期**不**接入 Observer 写透路径；以 state 为权威展示，
  CRUD 用于人工修正。
- **JSON 列**：``who_knows`` 默认 NULL，写入前 ``json.dumps(ensure_ascii=False)``；
  读出 ``json.loads``；解析失败回退 None（不阻断）。其他列均为标量。
- **权威文档**：`database/migrations/0001_init.sql` line 197-235、PRD §21/§22、
  knowledge-permission-v0 §3.1、Sprint 9 任务书。