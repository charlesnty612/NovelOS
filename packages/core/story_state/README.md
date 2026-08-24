# Story State 引擎（Sprint 2 + Sprint 7 分支能力）

> 状态：**已实现并通过测试**（Sprint 2 主链路 + Sprint 4 / 4-B / 6 修复 + Sprint 7 分支能力）。
> 核心入口 ``StoryStateService``。

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
├── exceptions.py          # StateConflictError / OptimisticLockError / ApprovalRequiredError / StateNotFoundError / BranchNotFound / BranchClosed
├── validator.py           # validate_delta(delta) -> list[str]（jsonschema + 业务规则）
├── snapshot.py            # build_initial_state / materialize_snapshot
├── applier.py             # apply_delta(state, delta) -> state（纯函数）
└── service.py             # StoryStateService（DB 主入口；Sprint 7 增 create_branch / promote_branch / diff_versions / 分支推导 helper）；公共 helper diff_snapshots / strip_state_version
```

API 路由：``packages/core/api/routers/story_state.py``（自动发现挂载到 ``/api``）。

---

## 3. 公共 API

### 3.1 ``StoryStateService(db_path)``

| 方法 | 输入 | 输出 | 失败语义 |
|------|------|------|---------|
| ``get_current_state(project_id, *, branch_id=None)`` | project_id, 可选 branch_id | dict（state_version + 全量 JSON） | ``branch_id`` 不存在 → ``BranchNotFound`` |
| ``get_snapshot(project_id, version)`` | project_id, version | dict / None | 不存在 → None |
| ``init_genesis(project_id, chapter_id)`` | project_id, chapter_id | dict（v1 state） | 已存在 → 直接返回当前 |
| ``submit_delta(delta, *, branch_id=None)`` | 完整 delta dict, 可选 branch_id | ``{delta_id, status, errors}`` | 校验失败 → ``status='rejected'`` 仍落表；``branch_id`` 非 ACTIVE → ``BranchNotFound`` / ``BranchClosed`` |
| ``commit_delta(delta_id, author_approval, workflow_run_id, *, branch_id=None)`` | delta_id, dict, str, 可选 branch_id | ``{commit_id, delta_id, state_version, snapshot_ref}`` | ``StateConflictError`` / ``OptimisticLockError`` / ``ApprovalRequiredError`` / ``BranchNotFound`` / ``BranchClosed`` |
| ``rollback_commit(commit_id, author_approval, *, branch_id=None)`` | commit_id, dict, 可选 branch_id | ``{commit_id, state_version, rollback_of}`` | 同 commit 失败语义 |
| ``list_commits(project_id, *, branch_id=None)`` | project_id, 可选 branch_id | list[dict] | 无 |
| ``list_deltas(chapter_id)`` | chapter_id | list[dict] | 无 |
| ``create_branch(project_id, name, *, base_state_version=None)`` | project_id, str, 可选 int | ``{branch_id, name, parent_branch_id, base_state_version, status, created_at}`` | 重名 → ``StateConflictError``（409）；name='main' 拒绝 |
| ``list_branches(project_id)`` | project_id | list[dict]（main 优先，其余按 created_at ASC） | 无 |
| ``promote_branch(project_id, branch_id, *, chapter_id=None)`` | project_id, str, 可选 chapter_id | ``{commit_id, delta_id, state_version, promoted_from, promoted_commits, branch_id, replayed_delta_ids, snapshot_ref}`` | branch 非 ACTIVE → ``BranchClosed``；按序重放时单个 delta 校验失败 → ``StateConflictError``（详见 §6.5 deviation） |
| ``diff_versions(project_id, version_a, version_b, *, branch_id=None)`` | project_id, int, int, 可选 branch_id | 结构化 diff dict | snapshot 缺失 → ``StateNotFoundError``；``branch_id`` 非 None → ``StateConflictError``（MVP 不支持分支 diff） |

### 3.2 ``validate_delta(delta) -> list[str]``

纯函数。空列表 = 通过；非空 = 错误列表（``[schema] <path>: <msg>`` 或
``[business] <path>: <reason>`` 形式）。

### 3.3 ``apply_delta(state, delta) -> state``

纯函数。深拷贝 state 后按 7 数组依次修改；不读不写 DB；不修改入参。

### 3.4 ``build_initial_state(conn, project_id) -> dict``

从领域表组装初始 Canonical State JSON（``state_version=0``，调用方可覆盖）。

### 3.5 ``materialize_snapshot(conn, *, project_id, state_version, snapshot_json, commit_id, created_at)``

把 state JSON 写入 ``story_states``，返回 ``(snapshot_ref, sha256)``。

### 3.6 ``diff_snapshots(a, b, *, version_a, version_b, branch_id) -> dict``

公共 helper（模块级纯函数），``diff_versions`` 的底层实现；外部模块（如
``packages.core.simulation``）需要结构化 diff 时可直接 import。``a`` / ``b`` 应为已
``strip_state_version`` 处理过的 dict（避免 ``state_version`` 字段被视为差异）。

### 3.7 ``strip_state_version(snap: dict) -> dict``

公共 helper（模块级纯函数）；去除 ``snap["state_version"]`` 字段以便 diff；非 dict
或不含该键时直接返回原值。供 ``diff_snapshots`` 及外部调用方在 diff 前统一预处理。

---

## 4. REST 端点

| 方法 | 路径 | 行为 |
|------|------|------|
| POST | ``/projects/{pid}/state/init`` | 创建 genesis（201） |
| GET | ``/projects/{pid}/state`` | 当前 Canonical State（main）；可选 ``?branch_id=<id>`` 取分支视角 |
| GET | ``/projects/{pid}/state/versions/{n}`` | 指定版本快照（404 不存在） |
| POST | ``/projects/{pid}/deltas`` | submit（201 通过 / 422 校验失败）；body 可选 ``branch_id`` |
| POST | ``/projects/{pid}/commits`` | commit（201 通过 / 409 状态机/乐观锁/HIGH/branch_closed / 404 delta 不存在 / 404 branch 不存在）；body 可选 ``branch_id`` |
| POST | ``/commits/{cid}/rollback`` | 回滚（201；同 commit 失败语义） |
| GET | ``/projects/{pid}/commits`` | 列 commits |
| GET | ``/projects/{pid}/chapters/{cid}/deltas`` | 列 deltas |
| POST | ``/projects/{pid}/branches`` | 创建分支（201；重名 / 'main' → 409） |
| GET | ``/projects/{pid}/branches`` | 列分支（main 优先） |
| POST | ``/projects/{pid}/branches/{bid}/promote`` | 分支 promote 到 main（201；非 ACTIVE → 409 branch_closed） |
| GET | ``/projects/{pid}/state/diff?a=&b=&branch_id=`` | 两版本 diff（200；snapshot 缺失 → 404；``branch_id`` 非空 → 409 diff_conflict） |

错误码：
- **422**：Delta 校验失败（``detail.errors`` 含 schema + 业务错误字符串列表）。
- **404**：project / chapter / delta / commit / snapshot / branch 不存在。
- **409**：状态机非法（``StateConflictError``）/ 乐观锁失败（``OptimisticLockError``）/
  HIGH 风险无审批（``ApprovalRequiredError``）/ 分支已关闭（``BranchClosed``，
  ``detail.error = "branch_closed"``）/ 分支重名（``StateConflictError``，
  ``detail.error = "branch_name_conflict"``）/ 分支视角 diff 不支持（``StateConflictError``，
  ``detail.error = "diff_conflict"``）。

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
- ``Rollback 链式复杂度``（同上）：本 Sprint 不实现级联回退（PRD §5.5 已声明未规定）。

---

## 6.5 分支能力（Sprint 7）

对齐 ``docs/state-model/state-delta-v0.md §6.3`` 与 PRD §48 的 Story Branch 语义。
**核心原则**：

1. **分支 commit 不写新快照**。分支的当前状态由读路径推导：
   - 取 ``branches.base_state_version`` 处的 main 快照作为 base；
   - 按 ``commits.branch_id == branch_id`` 顺序（``resulting_state_version ASC``）重放
     本分支 delta，复用 ``apply_delta``；
   - ``state_version`` 取分支内独立递增（base + 1, + 2, ...），与 main 序列正交。

2. **Promote 按序重放分支全部 commits 到 main**（Sprint 7 审查 P0 修订）：
   不再 concat 成单个 merged delta。对分支每个 commit 按 ``resulting_state_version ASC``
   依次在 main 上走 ``submit_delta + commit_delta(branch_id=None)``，每次产生一个
   main commit（state_version 逐次 +1），``commits.validation_json`` 注入
   ``{"promoted_from": "<branch_id>", "source_commit_id": "<branch_commit_id>"}``。
   全部成功后 ``branches.status = 'MERGED'``；中途失败已重放的 main commits 保留
   （与正常 commit 一致的事务语义），branch 保持 ACTIVE，错误向上抛。
   详细 deviation 见下方 §6.5 deviation 段。

3. **领域表写透**：分支 commit 路径**整体跳过**领域表写透（含 character /
   world / relationship / debt / new_events / new_hooks / resolved_hooks）——
   避免分支未 promote 即污染 main 领域表。**仅 main commit 路径**执行领域表
   写透（含 promote 重放时 main 上的 commit）。原 ``_write_through`` 的
   ``skip_new_events_hooks`` 语义已被 ``skip_all`` 取代（兼容参数保留）。

4. **乐观锁**：main 用 ``story_states`` 全局最新 version 作锚点；分支用本分支 commits
   最新 ``resulting_state_version`` 作锚点。

5. **分支状态机**：``status='ACTIVE'`` → 接受写入；``'MERGED'`` / ``'DISCARDED'`` →
   拒绝（``BranchClosed``，HTTP 409 ``error='branch_closed'``）。

### 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | ``/projects/{pid}/branches`` | body ``{name, base_state_version?}``；重名 / ``name='main'`` → 409 |
| GET  | ``/projects/{pid}/branches`` | 列分支（main 优先） |
| POST | ``/projects/{pid}/branches/{bid}/promote`` | body ``{chapter_id?}``；非 ACTIVE → 409 |
| GET  | ``/projects/{pid}/state/diff?a=&b=&branch_id=`` | ``branch_id`` 非空 → 409（MVP 限制） |

### 已知 MVP 限制

- **不支持跨分支三章以上合并冲突检测**（``state-delta-v0.md §10 Q6``）：promote 时
  按 7 数组 concat 合并 delta，不做字段级冲突检查。后 commit 在 promote 时**覆盖**
  先 commit 涉及的同字段（如 character state.location），但**不报错**——这是任务书
  给死的 MVP 口径。v1 应升级为「冲突字段级提示 + 作者决策」流程。
- **分支 commit 不支持跨 project**（``BranchNotFound``，HTTP 404）。
- **分支视角的 diff 不在 ``diff_versions`` 范围**：调用方需在外部用
  ``get_current_state`` 两次取分支快照后自行 diff（详见 ``diff_versions`` docstring）。
- **新分支的 commits.resulting_state_version 与 main 主键 (project_id, state_version)
  共享空间**：main version=N 时，分支首个 commit 的 resulting_state_version=N+1，
  下个 main commit 写入 story_states 时若取 N+2 会撞——本 Sprint 实现里**分支不写
  story_states 行**（按任务书口径），因此两序列不冲突。后续 v1 若需分支也写快照，
  需在 branches 上引入独立 ``branch_state_version`` 字段。
- **rollback 默认在原 commit 所在分支**（main commit 在 main 回滚，分支 commit
  在分支回滚）；``rollback_commit(branch_id=...)`` 参数当前保留为接口占位，未启用
  跨分支回滚语义。

### Promote 按序重放（deviation from ``state-delta-v0.md §6.3``）

``state-delta-v0.md §6.3`` 字面口径是「单一合并 commit（7 数组 concat）」，
Sprint 7 审查复现该方案存在致命缺陷（见下方根因）后被否决，本 Sprint 改用
**按序重放**：

- **根因（applier 固定顺序破坏）**：``applier.apply_delta`` 固定顺序为
  ``character_changes → world_changes → relationship_changes → new_events
  → resolved_hooks → new_hooks → debt_changes``（``applier.py:60-66``）。
  对同一 ``hook_id``，若分支内某 commit 顺序为「先 new_hook 后
  resolved_hooks」，concat 合并后会变成「resolved_hooks 先于 new_hooks」——
  此时 resolved UPDATE 找不到行（hook 尚未 INSERT），被静默丢弃，hook 以
  OPEN 落 main。
- **新语义（按序重放）**：
  1. 对分支每个 commit 读 ``payload_json``（7 数组），
  2. 构造 replay delta（新 ``delta_id``，``chapter_id`` 沿用该 commit 原
     chapter_id 或 ``chapter_id`` 参数覆盖，``previous_state_version``
     重写为 main 当前最新 version；注意 ``state_deltas`` 表无 notes 列，
     溯源标记落于 commit 侧，见下一条），
  3. ``submit_delta`` 校验 + ``commit_delta(branch_id=None)`` 走 main 路径，
     ``validation_json.promoted_from = <branch_id>``，且
     ``commits.author_approval_json.notes`` 标记
     ``"replay from branch <bid> (source_commit=<src_cmt_id>)"``
     （含 ``source_commit_id`` 溯源），
  4. 全部成功后 ``branches.status='MERGED'``；中途失败保留已重放 commits。
- **新 delta 行不复用原 delta_id**：原 state_deltas 行（status=applied、
  归属分支 commit）只读不动；重放生成**新 delta 行**（新 delta_id、status=
  applied、归属新 main commit）。``state_deltas.delta_id`` 是 PK 必须唯一。
- **影响**：
  - ``promote_branch`` 返回结构多 ``replayed_delta_ids``（本次重放在 main 上
    产生的新 delta_id 列表，按序）与 ``delta_id``（最后一个新 delta_id）；
    ``promoted_commits`` 与 ``state_version`` 语义不变。
  - 单次 promote 会在 main 上产生 N 个 commits（N = 分支 commits 数），
    main 的 ``state_version`` 单次跳 N+1，与 Sprint 2/4 单 commit 跳 1 不
    一致——后续 v1 可考虑把 promote 视作「批量 commit」返回 commit_id 列表。
  - 字段级冲突检测仍不做：若分支内后 commit 改了同一字段，后值覆盖前值，
    与 concat 方案语义等价。

---

## 8. 相关文档指针

- ``docs/state-model/state-delta-v0.md`` —— State Delta / Commit 字段语义权威定义。
- ``docs/state-model/schemas/state-delta.schema.json`` —— Delta JSON Schema（Draft 2020-12）。
- ``docs/state-model/schemas/state-commit.schema.json`` —— Commit JSON Schema。
- ``database/migrations/0001_init.sql`` —— 数据库表结构（含 ``state_deltas`` /
  ``story_states`` / ``commits`` / ``branches`` 四张核心表）。
- ``tests/integration/test_story_state_api.py`` —— 完整链路 + 负例参考测试。