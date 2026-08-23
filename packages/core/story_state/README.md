# Story State 引擎（Sprint 2）

> 状态：**已实现并通过测试**（Sprint 2 修复后：76 既有 + 新增回归 + 新增 spec 用例）。核心入口 ``StoryStateService``。

本目录是 NovelOS State Delta 引擎——Observer / Validator / State Committer 三步之间的
完整数据契约与落库实现。对齐 ``docs/state-model/state-delta-v0.md`` 的全部语义。

---

## 1. 职责

- 把 Observer 输出的 State Delta（结构化 JSON）做 **Schema 校验 → 业务校验**。
- 把通过校验的 Delta 应用到 **Canonical Story State** 快照（``story_states`` 表）。
- 同一事务内 **写透领域表**（characters / locations / factions / world_rules / relationships /
  plot_events / hooks / narrative_debts）。
- 乐观锁（state_version 单调 +1）保证并发提交不污染前一状态。
- HIGH 风险 change 必须经 author_approval.approved=True 才允许 Commit。
- 支持「逆 Delta + 新 Commit」模式回滚；``commits`` 表不可变，「已回滚」由
  ``commits.rollback_of`` 字段表达。回滚在 ``commit_delta`` 单一事务内完成：写透领域表 +
  撤销领域表行（plot_events / hooks / timeline_events）+ mutate 快照 JSON +
  落 ``commits.rollback_of`` 一次性 commit，无 post-facto 分离连接（修复后口径）。

---

## 2. 模块结构

```
packages/core/story_state/
├── __init__.py            # 公共 API（service / applier / validator / snapshot / exceptions）
├── exceptions.py          # StateConflictError / OptimisticLockError / ApprovalRequiredError / StateNotFoundError
├── validator.py           # validate_delta(delta) -> list[str]（jsonschema + 业务规则）
├── snapshot.py            # build_initial_state / materialize_snapshot
├── applier.py             # apply_delta(state, delta) -> state（纯函数）
└── service.py             # StoryStateService（DB 主入口）
```

API 路由：``packages/core/api/routers/story_state.py``（自动发现挂载到 ``/api``）。

---

## 3. 公共 API

### 3.1 ``StoryStateService(db_path)``

| 方法 | 输入 | 输出 | 失败语义 |
|------|------|------|---------|
| ``get_current_state(project_id)`` | project_id | dict（state_version + 全量 JSON） | 无 |
| ``get_snapshot(project_id, version)`` | project_id, version | dict / None | 不存在 → None |
| ``init_genesis(project_id, chapter_id)`` | project_id, chapter_id | dict（v1 state） | 已存在 → 直接返回当前 |
| ``submit_delta(delta)`` | 完整 delta dict | ``{delta_id, status, errors}`` | 校验失败 → ``status='rejected'`` 仍落表 |
| ``commit_delta(delta_id, author_approval, workflow_run_id)`` | delta_id, dict, str | ``{commit_id, delta_id, state_version, snapshot_ref}`` | ``StateConflictError`` / ``OptimisticLockError`` / ``ApprovalRequiredError`` |
| ``rollback_commit(commit_id, author_approval)`` | commit_id, dict | ``{commit_id, state_version, rollback_of}`` | 同 commit 失败语义 |
| ``list_commits(project_id)`` | project_id | list[dict] | 无 |
| ``list_deltas(chapter_id)`` | chapter_id | list[dict] | 无 |

### 3.2 ``validate_delta(delta) -> list[str]``

纯函数。空列表 = 通过；非空 = 错误列表（``[schema] <path>: <msg>`` 或
``[business] <path>: <reason>`` 形式）。

### 3.3 ``apply_delta(state, delta) -> state``

纯函数。深拷贝 state 后按 7 数组依次修改；不读不写 DB；不修改入参。

### 3.4 ``build_initial_state(conn, project_id) -> dict``

从领域表组装初始 Canonical State JSON（``state_version=0``，调用方可覆盖）。

### 3.5 ``materialize_snapshot(conn, *, project_id, state_version, snapshot_json, commit_id, created_at)``

把 state JSON 写入 ``story_states``，返回 ``(snapshot_ref, sha256)``。

---

## 4. REST 端点

| 方法 | 路径 | 行为 |
|------|------|------|
| POST | ``/projects/{pid}/state/init`` | 创建 genesis（201） |
| GET | ``/projects/{pid}/state`` | 当前 Canonical State |
| GET | ``/projects/{pid}/state/versions/{n}`` | 指定版本快照（404 不存在） |
| POST | ``/projects/{pid}/deltas`` | submit（201 通过 / 422 校验失败） |
| POST | ``/projects/{pid}/commits`` | commit（201 通过 / 409 状态机/乐观锁/HIGH / 404 delta 不存在） |
| POST | ``/commits/{cid}/rollback`` | 回滚（201；同 commit 失败语义） |
| GET | ``/projects/{pid}/commits`` | 列 commits |
| GET | ``/projects/{pid}/chapters/{cid}/deltas`` | 列 deltas |

错误码：
- **422**：Delta 校验失败（``detail.errors`` 含 schema + 业务错误字符串列表）。
- **404**：project / chapter / delta / commit / snapshot 不存在。
- **409**：状态机非法（``StateConflictError``）/ 乐观锁失败（``OptimisticLockError``）/
  HIGH 风险无审批（``ApprovalRequiredError``）。

---

## 5. 数据模型依赖

- ``state_deltas`` 表——Delta 提案行（``proposed → validated → applied`` 终态；``rejected`` /
  ``superseded`` 终态）。
- ``story_states`` 表——快照行（``state_version`` 单调 +1/commit）。
- ``commits`` 表——不可变提交记录（``validation_json`` / ``author_approval_json`` /
  ``rollback_of``）。
- ``branches`` 表——分支（默认 ``main``）。``init_genesis`` 与 ``commit_delta`` 都会
  ``INSERT OR IGNORE`` 创建 ``(project_id, name='main')`` 行，``base_state_version=0``。
- 领域表（``characters`` / ``character_states`` / ``locations`` / ``factions`` /
  ``world_rules`` / ``relationships`` / ``plot_events`` / ``hooks`` /
  ``narrative_debts``）——commit 阶段写透。

DDL 权威定义在 ``database/migrations/0001_init.sql``；本模块不修改 schema。

---

## 6. 关键约束与维护注意点

1. **乐观锁语义**：每次 Commit 严格 ``state_version + 1``（``state-delta-v0.md §6.2``，
   2026-08-23 主会话拍板）。不允许按变更条目数累加。
2. **HIGH 风险审批门**：任何 change ``risk_level == "HIGH"`` 必须有
   ``author_approval.approved == True`` 才能 commit。``service.commit_delta`` 第 3 步预检。
3. **写透领域表的事务性**：commit 阶段所有 DB 写入（领域表 + ``story_states`` + ``commits`` +
   ``state_deltas.status``）必须同一事务，任一步失败整体 rollback。修改 commit 流程时务必
   保证 ``conn.commit()`` 只在所有 ``conn.execute(...)`` 成功后调用一次。
4. **Rollback 不可变性**：``commits`` 表不允许被回写；「已回滚」通过「存在新 commit.rollback_of
   = 原 commit_id」表达。新 commit 的 ``rollback_of`` 由 ``rollback_commit`` 在 commit 完成后
   用单条 UPDATE 补齐。
5. **world_changes 中 ``world_kind in {politics, economy, event, time}``**：本 Sprint **只进
   ``story_states`` 快照**，不写领域表（领域表无对应表）。``service._write_through`` 中有
   注释说明；v1+ 应视 PRD 决策补建领域表。
6. **resolved_hooks 不可回滚边界**：若原 commit 的 ``resolved_hooks`` 条目 ``from_status``
   缺失（schema 允许 null），生成逆 Delta 时无法确定回退目标状态——``rollback_commit`` 直接
   抛 ``StateConflictError``，拒绝回滚。这是任务书给死的硬性限制。
7. **rollback 单一事务保证**：``rollback_commit`` 走「逆 Delta + 单 commit」路径——
   - 逆 Delta 只承载 schema 合法 change（character / world / relationship / hook / debt）。
   - ``new_events`` / ``new_hooks`` 的逆无法走 schema（``op`` 强制 add），由调用方从
     原 delta 收集 event_id / hook_id，作为 ``_inverse_cleanup`` 私有参数传给
     ``commit_delta``，在 **同事务** 内：
     1. 先 DELETE ``timeline_events`` 解除 FK，再 DELETE ``plot_events`` / ``hooks``。
     2. 通过 ``_apply_inverse_cleanup_to_state`` 同步 mutate ``new_state``
        （剔除 recent_events / events / hooks[] 中的对应项）。
     3. ``materialize_snapshot`` 把 mutate 后的 state 落盘。
     4. 落 ``commits.rollback_of = 原 commit_id``（同一事务）。
   - 因此 rollback 后无残留领域表行（plot_events / hooks 行被 DELETE），快照层
     recent_events / events / hooks 也已剔除，无 post-facto 兜底连接（修复后口径）。
8. **逆 debt_changes 合法化**（修复后）：逆条目禁用 ``before`` / ``after`` 键，改用
   schema 合法字段：
   - ``op=remove``（逆 add）：``status_after`` 必填（兜底 ``forgiven``），``reason`` 必填。
   - ``op=update``（逆 update）：``status_before`` / ``status_after`` 互换，
     ``severity_before`` / ``severity_after`` 互换。
   - ``op=add``（逆 remove）：用原 ``description`` / ``severity_after`` / ``status_after``
     重建（兜底 ``"open"`` / ``0.5``）。
9. **逆 resolved_hooks payoff_summary**：必填（schema ``minLength=1``），
   填 ``"reverted by rollback of <commit_id>"``。同时逆条目 ``notes`` 携带哨兵
   ``__CLEAR_PAYOFF_CHAPTER__``，``_write_through`` 检测到哨兵即把
   ``hooks.payoff_chapter_id`` 显式置 NULL（修复后口径）。
10. **回滚可逆性边界**（对齐逆操作语义）：
    - 严格可逆：character_changes / world_changes / relationship_changes / debt_changes
      （add/update/remove 三态互换）。
    - 「软」可逆（领域表行被物理删除）：new_events / new_hooks —— 撤销后
      ``plot_events`` / ``hooks`` 表对应行不再存在（业务上等同于「该 event/hook
      未发生 / 未被引入」）。
    - 半可逆：resolved_hooks —— status 还原、payoff_chapter_id 清 NULL；hook 行本身保留。
11. **Validator 路径**：「Schema 不通过时跳过业务校验」——避免对缺失字段二次报错。修改
    ``validator._business_errors`` 时确保它对 schema 不报错的 delta 才执行。
12. **JSON 列读写**：所有 JSON 列写入前 ``json.dumps(ensure_ascii=False)``；读出后
    ``json.loads``（空字符串兜底为 ``{}`` / ``None``）。
13. **who_knows 三态语义**（修复后）：对齐 ``knowledge-permission-v0.md §6``——
    - ``None``（缺失）→ DB 列置 ``NULL``（沿用实体现状，不参与合并）。
    - ``[]`` → DB 列置 ``'[]'``（显式清空）。
    - 非空 list → JSON 字符串（``ensure_ascii=False``）。
    ``_write_through`` 内所有 INSERT/UPDATE 走 ``_encode_who_knows`` helper。
14. **HIGH 风险门扩展**（修复后）：除 ``risk_level=HIGH`` 外，``character_changes.facet
    =definition`` 与 ``world_changes.world_kind=rule`` 也强制 author_approval.approved=True
    （对齐 ``state-delta-v0.md §2.5.1/2.5.2`` 末段）。
15. **applier update after=None → delete key**（修复后）：``op=update`` 且 ``after=None``
    时调用 ``bucket.pop(key, None)``，使回滚后快照与原状态严格等价。
16. **测试断言**：
    - 单测 ``tests/unit/test_applier.py`` 覆盖 7 数组每类至少 1 条 apply 断言（纯函数，
      无 DB 依赖）。
    - 单测 ``tests/unit/test_validator.py`` 覆盖合法通过 + 缺 required / 非法枚举 /
      业务不匹配四类断言。
    - 集成测试 ``tests/integration/test_story_state_api.py`` 覆盖主链路 + 乐观锁 / HIGH /
      schema 失败 / 持久化五条负例。

---

## 7. 已知 Open Questions（继承自 ``state-delta-v0.md §10``）

- 五条 guardrail（``timeline_consistency`` / ``character_contradiction`` /
  ``world_rule_contradiction`` / ``knowledge_leakage``）的具体检测尚未实现；当前
  ``validation_json.guardrail_results`` 全部 ``status='pass'`` 占位。属后续 Sprint。
- ``Branch 隔离不严``（``state-delta-v0.md §11``）：本 Sprint 所有 Commit 走 ``main`` 分支，
  Branch 隔离（``branch_id`` 校验）属 v1+ 工作。
- ``Rollback 链式复杂度``（同上）：本 Sprint 不实现级联回退（PRD §5.5 已声明未规定）。

---

## 8. 相关文档指针

- ``docs/state-model/state-delta-v0.md`` —— State Delta / Commit 字段语义权威定义。
- ``docs/state-model/schemas/state-delta.schema.json`` —— Delta JSON Schema（Draft 2020-12）。
- ``docs/state-model/schemas/state-commit.schema.json`` —— Commit JSON Schema。
- ``database/migrations/0001_init.sql`` —— 数据库表结构（含 ``state_deltas`` /
  ``story_states`` / ``commits`` / ``branches`` 四张核心表）。
- ``tests/integration/test_story_state_api.py`` —— 完整链路 + 负例参考测试。