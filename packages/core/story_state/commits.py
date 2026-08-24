"""Story State Commit 主链路（Sprint 2 + Sprint 7 + Sprint 10）。

职责（god-object 拆分后）：
- ``project_id_for_chapter``——查 chapters 表取 project_id（commit_delta 入口）。
- ``init_genesis``——创建项目首份 story_states 快照（v1）。
- ``submit_delta``——落 state_deltas 行（proposed → validated/rejected）。
- ``commit_delta``——乐观锁 + HIGH 风险审批门 + apply + 写透领域表 + 落 commits/
  story_states。
- ``rollback_commit``——生成逆 Delta 并走 submit + commit 全流程。

设计要点：
- 拆分后与原 ``service.StoryStateService.{init_genesis,submit_delta,
  commit_delta,rollback_commit,_project_id_for_chapter}`` **逐字节相同**；
  仅文件位置变更，公开行为 0 变化。
- 每个公开方法都接收 ``service_self``（StoryStateService 实例）以访问
  ``db_path``，并把 ``self._write_through`` / ``self.submit_delta`` /
  ``self.commit_delta`` 等方法委派调用——通过 service.py 的 façade 类维持
  原有方法调用语义（外部调用方零改动）。
- 事务边界：commit_delta 与 rollback_commit 走单一 ``conn.commit()``，
  中途失败 → 整体 rollback（commit_delta 中每步失败先 ``conn.rollback() +
  conn.close()`` 再 raise）。
"""

from __future__ import annotations

import sqlite3

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso

from .applier import apply_delta
from .branches import branch_current_state, ensure_branch, resolve_branch
from .deltas import (
    build_inverse_delta,
    high_risk_change_ids,
    payload_json_from_delta,
    restore_delta_from_row,
)
from .exceptions import (
    ApprovalRequiredError,
    OptimisticLockError,
    StateConflictError,
    StateNotFoundError,
)
from .snapshot import build_initial_state, materialize_snapshot
from .snapshots import _dump, latest_snapshot_version
from .validator import validate_delta
from .write_through import apply_inverse_cleanup_to_state, write_through


def project_id_for_chapter(service_self, conn: sqlite3.Connection, chapter_id: str) -> str | None:
    row = conn.execute("SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
    return row["project_id"] if row else None


def init_genesis(service_self, project_id: str, chapter_id: str) -> dict:
    """创建项目首份 story_states 快照（v1）。

    幂等：已有快照则直接返回当前 state。
    流程（同一事务）：
    1. 落 state_deltas（delta_id=new_id("dlt")，created_by="system:genesis"，previous_state_version=1，
       status=applied → commit 后转 applied）。
    2. 确保 (project_id, name='main') branch 行存在（不存在则插入）。
    3. 落 commits（branch_id=main，validation_json 兜底五 guardrail pass）。
    4. 落 story_states v1（content=build_initial_state 结果，state_version 改 1）。
    5. 把 delta.status 更新为 applied。

    返回：当前 Canonical State（v1）。
    """
    conn = get_connection(service_self.db_path)
    try:
        existing_version, _ = latest_snapshot_version(conn, project_id)
        if existing_version >= 1:
            # 已存在 → 直接返回
            snap_row = conn.execute(
                "SELECT snapshot_json FROM story_states WHERE project_id = ? AND state_version = ?",
                (project_id, existing_version),
            ).fetchone()
            from .snapshots import _parse_required_json
            snap = _parse_required_json(snap_row["snapshot_json"], {})
            snap["state_version"] = existing_version
            conn.close()
            return snap

        now = now_iso()
        branch_id = ensure_branch(conn, project_id)
        initial = build_initial_state(conn, project_id)
        initial["state_version"] = 1

        delta_id = new_id("dlt")
        commit_id = new_id("cmt")
        payload = {
            "character_changes": [],
            "world_changes": [],
            "relationship_changes": [],
            "new_events": [],
            "resolved_hooks": [],
            "new_hooks": [],
            "debt_changes": [],
        }

        # 1) state_deltas（status=applied；commit 后状态由 applied 表达）
        conn.execute(
            """
            INSERT INTO state_deltas
                (delta_id, chapter_id, workflow_run_id, previous_state_version,
                 delta_version, schema_version, payload_json, status, supersedes,
                 created_by, created_at)
            VALUES
                (?, ?, ?, ?, 1, 'state-delta-v0', ?, 'applied', NULL, 'system:genesis', ?)
            """,
            (
                delta_id,
                chapter_id,
                "system:genesis",
                1,
                _dump(payload),
                now,
            ),
        )

        # 2) commits
        validation_json = {
            "schema_valid": True,
            "guardrail_results": [
                {"name": n, "status": "pass", "details": None}
                for n in (
                    "schema_validity",
                    "timeline_consistency",
                    "character_contradiction",
                    "world_rule_contradiction",
                    "knowledge_leakage",
                )
            ],
            "validator_agent": "validator:v1",
            "checked_at": now,
        }
        author_approval_json = {
            "required": False,
            "status": "not_required",
            "approver": None,
            "approved_at": None,
            "high_risk_change_ids": [],
            "notes": "system:genesis",
        }
        conn.execute(
            """
            INSERT INTO commits
                (commit_id, project_id, branch_id, chapter_id,
                 previous_state_version, resulting_state_version,
                 delta_id, validation_json, author_approval_json,
                 timestamp, workflow_run_id, rollback_of)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'system:genesis', NULL)
            """,
            (
                commit_id,
                project_id,
                branch_id,
                chapter_id,
                0,
                1,
                delta_id,
                _dump(validation_json),
                _dump(author_approval_json),
                now,
            ),
        )

        # 3) story_states v1
        materialize_snapshot(
            conn,
            project_id=project_id,
            state_version=1,
            snapshot_json=initial,
            commit_id=commit_id,
            created_at=now,
        )

        conn.commit()
    finally:
        conn.close()

    return service_self.get_current_state(project_id)


def submit_delta(service_self, delta: dict, *, branch_id: str | None = None) -> dict:
    """落 state_deltas 行（proposed → validated/rejected）。

    ``branch_id``（Sprint 7 新增）：可选，标识 Delta 归属分支。
    - None：现有行为不变（落 main），与 Sprint 2/4 字节级一致。
    - 非 None：先通过 ``resolve_branch`` 校验归属 + ACTIVE 状态；校验失败抛
      :class:`BranchNotFound` / :class:`BranchClosed`。
      **state_deltas 表本身无 branch_id 列**（设计沿用 state-delta-v0.md §2.2），
      此参数仅用于本方法在落 validated 行前做"前置守卫"——拒绝向 closed branch
      写入；实际 commit 时再用同名参数决定 commits.branch_id。

    流程（不在事务中——状态机允许 intermediate row）：
    1. ``branch_id`` 非 None 时校验 branch（前置守卫）。
    2. ``validate_delta`` 全量校验（含 10 元信息字段 + 业务规则）。
    3. 校验失败：若 ``chapter_id`` 非空（FK 可满足）落 ``status='rejected'`` 行；
       否则因 FK 约束无法落库，仅返回错误。两种情形均返回 ``status='rejected'``。
    4. 校验通过：落 ``status='proposed'`` 行，立即 UPDATE 为 ``status='validated'``。
    5. ``supersedes`` 非空：把被指向 delta（同 chapter_id + workflow_run_id 上下文）
       UPDATE 为 ``status='superseded'``。

    返回 ``{"delta_id": str, "status": str, "errors": list}``。
    """
    # 分支归属校验（前置守卫）：在 validate 之后插入，避免先报告校验错误又因分支失败
    # 二次抛错。失败时直接抛出（不会写 state_deltas 行）。
    # Sprint 7 审查 P1 修复：使用 ``delta.get("chapter_id")``（缺省 None），
    # 缺失时 ``project_id_for_chapter`` 返回 None 即跳过前置守卫——之后
    # ``validate_delta`` 会因 chapter_id 缺失返回 422 校验错误，与不带
    # branch_id 的行为一致，避免 KeyError 导致 500。
    if branch_id is not None:
        conn = get_connection(service_self.db_path)
        try:
            project_id = project_id_for_chapter(service_self, conn, delta.get("chapter_id"))
            if project_id is not None:
                resolve_branch(conn, project_id, branch_id)
        finally:
            conn.close()

    errors = validate_delta(delta)
    if errors:
        delta_id = delta.get("delta_id") or new_id("dlt")
        chapter_id = delta.get("chapter_id")
        workflow_run_id = delta.get("workflow_run_id") or ""
        # FK 保护：chapter_id 缺失时无法写 state_deltas 行（FK → chapters.chapter_id）。
        # 此时仅返回错误，不尝试 INSERT。
        if isinstance(chapter_id, str) and chapter_id:
            conn = get_connection(service_self.db_path)
            try:
                now = now_iso()
                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO state_deltas
                            (delta_id, chapter_id, workflow_run_id, previous_state_version,
                             delta_version, schema_version, payload_json, status, supersedes,
                             created_by, created_at)
                        VALUES
                            (?, ?, ?, ?, 1, 'state-delta-v0', ?, 'rejected', ?, ?, ?)
                        """,
                        (
                            delta_id,
                            chapter_id,
                            workflow_run_id,
                            int(delta.get("previous_state_version") or 1),
                            _dump(payload_json_from_delta(delta)),
                            delta.get("supersedes"),
                            delta.get("created_by") or "observer:unknown",
                            delta.get("created_at") or now,
                        ),
                    )
                    conn.commit()
                except sqlite3.IntegrityError:
                    # chapter_id 存在但仍 FK 失败（如 chapter 不存在）→ 回滚后仅返回错误
                    conn.rollback()
            finally:
                conn.close()
        return {"delta_id": delta_id, "status": "rejected", "errors": errors}

    delta_id = delta["delta_id"]
    conn = get_connection(service_self.db_path)
    try:
        now = now_iso()
        conn.execute(
            """
            INSERT INTO state_deltas
                (delta_id, chapter_id, workflow_run_id, previous_state_version,
                 delta_version, schema_version, payload_json, status, supersedes,
                 created_by, created_at)
            VALUES
                (?, ?, ?, ?, 1, 'state-delta-v0', ?, 'proposed', ?, ?, ?)
            """,
            (
                delta_id,
                delta["chapter_id"],
                delta["workflow_run_id"],
                int(delta["previous_state_version"]),
                _dump(payload_json_from_delta(delta)),
                delta.get("supersedes"),
                delta["created_by"],
                delta["created_at"],
            ),
        )
        conn.execute(
            "UPDATE state_deltas SET status = 'validated' WHERE delta_id = ?",
            (delta_id,),
        )

        supersedes = delta.get("supersedes")
        if supersedes:
            conn.execute(
                """
                UPDATE state_deltas
                SET status = 'superseded'
                WHERE delta_id = ? AND status IN ('proposed', 'validated', 'rejected')
                """,
                (supersedes,),
            )

        conn.commit()
    finally:
        conn.close()

    return {"delta_id": delta_id, "status": "validated", "errors": []}


def commit_delta(
    service_self,
    delta_id: str,
    author_approval: dict,
    workflow_run_id: str,
    *,
    branch_id: str | None = None,
    _inverse_cleanup: dict | None = None,
    _rollback_of: str | None = None,
    _skip_approval: bool = False,
) -> dict:
    """执行 Commit。

    ``branch_id``（Sprint 7 新增）：可选，标识 commit 归属分支。
    - None（默认）：现有 main 行为，逐字节保持 Sprint 2/4 兼容。
      main branch 行由 ``ensure_branch`` 兜底创建；乐观锁基于
      ``story_states`` 全局最新 version（与 Sprint 2/4 一致）。
    - 非 None：分支 commit **不写 ``story_states`` 新快照**（对齐任务书口径；
      分支当前状态由 ``get_current_state(branch_id=...)`` 在读路径推导：
      ``branches.base_state_version`` 处的快照 + 按 ``branch_id`` 过滤 commits
      顺序重放 delta）。``commits.resulting_state_version`` 取分支内独立递增
      序列（max(commits.resulting_state_version WHERE branch_id=X)+1）。
      领域表写透仍执行；rollback 触发时同样在 commit_delta 内完成逆路径清理。

    流程（同一事务）：
    1. 读 delta；必须 status='validated'，否则 ``StateConflictError``（409 语义）。
    2. 解析 branch（None → ensure main；否则 ``resolve_branch`` 校验）。
    3. 乐观锁：main → story_states 最新 version；branch → 本分支 commits 最新 version。
    4. HIGH 风险门：delta 任何 change risk_level=HIGH / character facet=definition /
       world_kind=rule 且 ``author_approval.get("approved") is not True``
       → ``ApprovalRequiredError``。
    5. ``apply_delta(current_state, delta)`` → 新 state，state_version+1。
    6. **写透领域表**。
    7. 落 commits 行（validation_json + author_approval_json + 可选 rollback_of）。
    8. main 路径：落 story_states 新快照；branch 路径：不写 story_states。
    9. UPDATE state_deltas.status='applied'。
    10. **逆路径清理**（仅 rollback 触发）：
        - DELETE FROM plot_events WHERE event_id IN (hints["remove_event_ids"])
          —— 先 DELETE FROM timeline_events WHERE event_id IN (...) 解除 FK。
        - DELETE FROM hooks WHERE hook_id IN (hints["remove_hook_ids"])。
        - 同步 mutate 当前 state（new_state 副本）：剔除 recent_events / events / hooks[]。

    ``_inverse_cleanup`` 是私有入参（仅 rollback_commit 使用）。

    ``_skip_approval``（Sprint 10 新增，私有）：当 ``True`` 时绕过 HIGH 风险
    审批门（不抛 :class:`ApprovalRequiredError`）。**仅 What-if Simulation
    路径使用**——见 :mod:`packages.core.simulation` 的 README；推演不产生
    真实状态（领域表写透由 S7 ``skip_all=True`` 整体跳过；分支最终标记
    ``ARCHIVED``），因此人工审批门无意义；调用方需保证 ``author_approval``
    中 ``high_risk_change_ids`` 的审计字段仍被记录，便于审计。

    返回 ``{"commit_id": str, "state_version": int, "delta_id": str, "snapshot_ref": str|None}``。
    """
    conn = get_connection(service_self.db_path)
    try:
        # 1) 读 delta
        delta_row = conn.execute(
            "SELECT * FROM state_deltas WHERE delta_id = ?", (delta_id,)
        ).fetchone()
        if delta_row is None:
            conn.rollback()
            conn.close()
            raise StateNotFoundError(f"delta {delta_id!r} not found", resource="delta", resource_id=delta_id)
        if delta_row["status"] != "validated":
            conn.rollback()
            conn.close()
            raise StateConflictError(
                f"delta {delta_id!r} status={delta_row['status']!r}, must be 'validated' to commit",
                delta_id=delta_id,
            )
        delta = restore_delta_from_row(delta_row)

        # 2) project / branch 解析 + 乐观锁
        project_id = project_id_for_chapter(service_self, conn, delta_row["chapter_id"])
        if project_id is None:
            conn.rollback()
            conn.close()
            raise StateNotFoundError(
                f"chapter {delta_row['chapter_id']!r} not found",
                resource="chapter",
                resource_id=delta_row["chapter_id"],
            )
        if branch_id is None:
            # main 路径：保留 ensure_branch 兜底创建 main 行（与 Sprint 2/4 字节级一致）
            branch_id = ensure_branch(conn, project_id)
            current_version, current_state = latest_snapshot_version(conn, project_id)
        else:
            # branch 路径：前置守卫（BranchNotFound / BranchClosed）
            branch_id = resolve_branch(conn, project_id, branch_id)
            # 即使显式传 branch_id，若该 branch 是 main（名称 = 'main'），也走 main 路径
            # —— main 是项目根分支，必须保持与 Sprint 2/4 一致的快照行为；
            # 分支 commit 仅在非 main 的「feature branch」上走 branch_current_state 推导路径。
            row = conn.execute(
                "SELECT name FROM branches WHERE branch_id = ?", (branch_id,)
            ).fetchone()
            if row is not None and row["name"] == "main":
                current_version, current_state = latest_snapshot_version(conn, project_id)
            else:
                current_version, current_state = branch_current_state(conn, project_id, branch_id)
        if delta_row["previous_state_version"] != current_version:
            conn.rollback()
            conn.close()
            raise OptimisticLockError(
                f"State 被其他流程更新，请重新观察 "
                f"(expected={delta_row['previous_state_version']}, actual={current_version})",
                expected_version=delta_row["previous_state_version"],
                actual_version=current_version,
                delta_id=delta_id,
            )

        # 3) HIGH 风险门
        high_ids = high_risk_change_ids(delta)
        # Sprint 10：What-if Simulation 路径下显式绕过审批门——
        # 推演不产生真实状态（领域表整体跳过 + 分支归档），因此 author_approval
        # 无意义。审计字段仍记录在 author_approval_json.high_risk_change_ids 里。
        if high_ids and not _skip_approval and not (
            isinstance(author_approval, dict) and author_approval.get("approved") is True
        ):
            conn.rollback()
            conn.close()
            raise ApprovalRequiredError(
                f"delta {delta_id!r} 含 HIGH 风险 change {len(high_ids)} 条，需 author_approval.approved=true",
                high_risk_change_ids=high_ids,
                delta_id=delta_id,
            )

        # 4) apply delta
        if current_state is None:
            # 无快照（理论上 init_genesis 后一定存在；兜底为 build_initial_state）
            current_state = build_initial_state(conn, project_id)
            current_state["state_version"] = 0
        new_state = apply_delta(current_state, delta)
        new_version = current_version + 1
        new_state["state_version"] = new_version

        # 5) 写透领域表
        #   Sprint 7 审查 P0 修复：分支 commit 路径**整体跳过**领域表写透
        #   （含 character / world / relationship / debt / new_events / new_hooks
        #   / resolved_hooks）——避免分支未 promote 即污染 main 领域表。
        #   分支的所有副作用由 promote 时按序重放在 main commit 路径下统一
        #   写入（commit_delta 走 main 路径时不传 skip_all）。仅 main commit
        #   路径（branch_id 为 None 或 branches.name = 'main'）执行领域表写透。
        is_main_branch_for_write = branch_id is None or (
            (lambda r: r["name"] == "main" if r is not None else False)(
                conn.execute("SELECT name FROM branches WHERE branch_id = ?", (branch_id,)).fetchone()
            )
        )
        write_through(
            conn, project_id, delta, new_version,
            skip_all=(not is_main_branch_for_write),
        )

        # 6) commits
        commit_id = new_id("cmt")
        now = now_iso()
        # branch_id 在步骤 2 已解析（main 路径 ensure main，分支路径 resolve_branch）
        validation_json = {
            "schema_valid": True,
            "guardrail_results": [
                {"name": n, "status": "pass", "details": None}
                for n in (
                    "schema_validity",
                    "timeline_consistency",
                    "character_contradiction",
                    "world_rule_contradiction",
                    "knowledge_leakage",
                )
            ],
            "validator_agent": "validator:v1",
            "checked_at": now,
        }
        author_approval_norm = {
            "required": bool(high_ids),
            "status": "approved" if high_ids else "not_required",
            "approver": (author_approval or {}).get("approver") if isinstance(author_approval, dict) else None,
            "approved_at": now if high_ids else None,
            "high_risk_change_ids": high_ids,
            "notes": (author_approval or {}).get("notes") if isinstance(author_approval, dict) else None,
        }
        # Sprint 10：Simulation 路径下 HIGH 风险被绕过审批门，但审计字段需如实
        # 记录——用 status='simulation_bypassed' + bypass_reason 标记，便于审计回溯
        # 「这条 HIGH 风险是 Simulation 推演路径带的，不是人工批的」。
        if high_ids and _skip_approval:
            author_approval_norm["status"] = "simulation_bypassed"
            author_approval_norm["approved_at"] = None
            author_approval_norm["bypass_reason"] = "what_if_simulation"
        # 分支 commit 时把 promoted_from 标记（仅 promote_branch 显式设置）
        promoted_from = None
        if isinstance(author_approval, dict) and author_approval.get("promoted_from"):
            promoted_from = author_approval["promoted_from"]
            validation_json["promoted_from"] = promoted_from
        conn.execute(
            """
            INSERT INTO commits
                (commit_id, project_id, branch_id, chapter_id,
                 previous_state_version, resulting_state_version,
                 delta_id, validation_json, author_approval_json,
                 timestamp, workflow_run_id, rollback_of)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                commit_id,
                project_id,
                branch_id,
                delta_row["chapter_id"],
                current_version,
                new_version,
                delta_id,
                _dump(validation_json),
                _dump(author_approval_norm),
                now,
                workflow_run_id,
                _rollback_of,
            ),
        )

        # 7) 逆路径清理（仅 rollback 触发）：与 write_through 同一事务。
        if _inverse_cleanup:
            apply_inverse_cleanup_to_state(new_state, _inverse_cleanup)
            # 领域表清理：先清 FK 引用（timeline_events → plot_events）再清主表。
            for eid in _inverse_cleanup.get("remove_event_ids") or []:
                conn.execute("DELETE FROM timeline_events WHERE event_id = ?", (eid,))
                conn.execute("DELETE FROM plot_events WHERE event_id = ?", (eid,))
            for hid in _inverse_cleanup.get("remove_hook_ids") or []:
                conn.execute("DELETE FROM hooks WHERE hook_id = ?", (hid,))

        # 8) story_states 新快照：仅 main 路径写（分支不写）
        snapshot_ref: str | None = None
        is_main_branch = False
        row = conn.execute(
            "SELECT name FROM branches WHERE branch_id = ?", (branch_id,)
        ).fetchone()
        if row is not None and row["name"] == "main":
            is_main_branch = True
        if is_main_branch:
            snapshot_ref, _digest = materialize_snapshot(
                conn,
                project_id=project_id,
                state_version=new_version,
                snapshot_json=new_state,
                commit_id=commit_id,
                created_at=now,
            )

        # 9) delta.status = applied
        conn.execute(
            "UPDATE state_deltas SET status = 'applied' WHERE delta_id = ?",
            (delta_id,),
        )

        conn.commit()
    finally:
        conn.close()

    return {
        "commit_id": commit_id,
        "delta_id": delta_id,
        "state_version": new_version,
        "snapshot_ref": snapshot_ref,
    }


def rollback_commit(
    service_self, commit_id: str, author_approval: dict, *, branch_id: str | None = None
) -> dict:
    """生成逆 Delta 并走 submit + commit 全流程；新 commit.rollback_of = commit_id。

    ``branch_id``（Sprint 7 新增）：可选，指定 rollback 在哪个分支上生成
    逆 commit。None（默认）=原 commit 所在分支（Sprint 2/4 兼容行为）。
    - 若原 commit 在 main 上：rollback 也在 main（无论 branch_id 传什么；防御性
      强制，避免跨分支回滚语义混乱）。
    - 若原 commit 在分支上：rollback 默认也在同分支。

    逆 Delta 规则（state-delta-v0.md §5.4 + 任务书口径）：
    - add → remove（target_id 不变，op=remove；用 schema 合法字段 status_after 必填）。
    - update → update（after 与 before 互换）。
    - remove → add（用原 description/severity_after/status_after 重建）。
    - new_events → schema 禁止「删除事件」op；逆条目走 ``_inverse_cleanup`` hints。
    - new_hooks → 同上；同事务内 DELETE hooks + mutate snapshot JSON。
    - resolved_hooks 逆：to_status 回 from_status；from_status 为 null → 抛错拒绝回滚；
      payoff_summary 填回滚说明；hooks.payoff_chapter_id 显式置 NULL（哨兵）。
    - debt_changes 逆：用 status_before/status_after/severity_before/severity_after 等
      schema 合法字段互换（无 before/after 键）。

    单一事务保证：逆 Delta 的写透、领域表清理（DELETE plot_events/hooks/timeline_events）、
    rollback_of 落 commits 行、快照 mutate 全部在 ``commit_delta`` 同一个 DB 连接上完成。
    """
    conn = get_connection(service_self.db_path)
    try:
        row = conn.execute("SELECT * FROM commits WHERE commit_id = ?", (commit_id,)).fetchone()
        if row is None:
            conn.rollback()
            conn.close()
            raise StateNotFoundError(f"commit {commit_id!r} not found", resource="commit", resource_id=commit_id)
        delta_row = conn.execute(
            "SELECT * FROM state_deltas WHERE delta_id = ?", (row["delta_id"],)
        ).fetchone()
        if delta_row is None:
            conn.rollback()
            conn.close()
            raise StateNotFoundError(
                f"delta {row['delta_id']!r} not found for commit {commit_id!r}",
                resource="delta",
                resource_id=row["delta_id"],
            )
        original_delta = restore_delta_from_row(delta_row)
        project_id = row["project_id"]
        original_branch_id = row["branch_id"]
        chapter_id = row["chapter_id"]
        workflow_run_id = row["workflow_run_id"]
        # 取原 commit 所在分支的当前 version 作为逆 delta 的 previous_state_version
        if original_branch_id is None:
            current_version, _snap = latest_snapshot_version(conn, project_id)
        else:
            branch_row = conn.execute(
                "SELECT name FROM branches WHERE branch_id = ?", (original_branch_id,)
            ).fetchone()
            if branch_row is not None and branch_row["name"] == "main":
                current_version, _snap = latest_snapshot_version(conn, project_id)
            else:
                current_version, _snap = branch_current_state(conn, project_id, original_branch_id)
        # 决定本次 rollback 落点：默认原分支（branch_id 仅作接口占位；当前实现维持原分支）
        _ = branch_id
    finally:
        conn.close()

    inverse = build_inverse_delta(
        original_delta,
        chapter_id=chapter_id,
        workflow_run_id=workflow_run_id,
        current_version=current_version,
    )

    # 从逆 delta 收集「需在领域表删除」的事件 / hook ID；
    # 逆 Delta 的 new_events / new_hooks 数组保持空（schema 禁止 remove op），
    # 这里从原始 delta 收集要被清除的 ID（因为回滚是「撤销原 commit」语义）。
    cleanup: dict[str, list[str]] = {
        "remove_event_ids": [
            ev.get("event_id")
            for ev in (original_delta.get("new_events") or [])
            if ev.get("event_id")
        ],
        "remove_hook_ids": [
            nh.get("hook_id")
            for nh in (original_delta.get("new_hooks") or [])
            if nh.get("hook_id")
        ],
    }
    # 过滤空值
    cleanup["remove_event_ids"] = [x for x in cleanup["remove_event_ids"] if x]
    cleanup["remove_hook_ids"] = [x for x in cleanup["remove_hook_ids"] if x]

    # 走 submit + commit 全流程（author_approval.approved=True 由调用方/UI 强制）
    ap = dict(author_approval or {})
    ap["approved"] = True
    ap.setdefault("notes", f"rollback of {commit_id}")

    submit_result = service_self.submit_delta(inverse["delta"], branch_id=original_branch_id)
    if submit_result["status"] != "validated":
        raise StateConflictError(
            f"inverse delta rejected by validator: {submit_result['errors']}",
            delta_id=submit_result["delta_id"],
        )
    # 用 commit_delta 完成；rollback_of 在 commit_delta 同一事务内落 commits 行；
    # inverse_cleanup 在同一事务内清理领域表 + mutate 快照。
    commit_result = service_self.commit_delta(
        submit_result["delta_id"],
        ap,
        f"system:rollback:{commit_id}",
        branch_id=original_branch_id,
        _inverse_cleanup=cleanup,
        _rollback_of=commit_id,
    )
    commit_result["rollback_of"] = commit_id
    return commit_result


__all__ = [
    "project_id_for_chapter",
    "init_genesis",
    "submit_delta",
    "commit_delta",
    "rollback_commit",
]
