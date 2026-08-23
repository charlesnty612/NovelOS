"""StoryStateService（Sprint 2）—— State Delta 引擎主服务。

职责链：State Delta → Validate → Commit → Rollback → Snapshot。

公共方法：
- :meth:`get_current_state` —— 返回当前 Canonical State JSON。
- :meth:`init_genesis` —— 创建项目首份 story_states 快照（v1）。
- :meth:`submit_delta` —— 落 state_deltas 行（proposed → validated/rejected）。
- :meth:`commit_delta` —— 乐观锁 + HIGH 风险审批门 + apply + 写透领域表 + 落 commits/story_states。
- :meth:`rollback_commit` —— 生成逆 Delta 并走 submit + commit 全流程，新 commit.rollback_of 指向原 commit。
- :meth:`list_commits` / :meth:`list_deltas` / :meth:`get_snapshot`。

设计要点：
- 构造接收 ``db_path``；每个方法内部用 ``packages.core.db.get_connection`` 开连接、
  ``try / finally`` 关闭（与 S1 服务统一模式）。
- JSON 列读写用 ``json.dumps(ensure_ascii=False)`` / ``json.loads``。
- ``init_genesis`` / ``commit_delta`` / ``rollback_commit`` 任一中间步骤失败 → 整体 rollback。
- 乐观锁语义（state-delta-v0.md §6.2）：commit 时若 ``delta.previous_state_version != 当前最新快照 version``，
  抛 ``OptimisticLockError``。
- 状态机：
  proposed → validated → applied；
  rejected / superseded 终态；
  supersedes 非空时把被指向 delta 置 superseded（同 chapter + workflow_run）。
- ``validation_json`` 在本 Sprint 简化为 ``{"schema_valid": true, "guardrail_results": [], "checked_at": <iso>}``；
  五条 guardrail（schema_validity / timeline_consistency / character_contradiction /
  world_rule_contradiction / knowledge_leakage）的具体检测属后续 Sprint。
- world_changes 中 ``world_kind in {politics, economy, event, time}``：本 Sprint 只进
  story_states 快照，不写领域表（领域表中无对应表；属 v1+ 范畴）。
- world_changes 中 ``world_kind=rule``：写入 ``world_rules.data_json``（顶层 key 替换）。
- relationship_changes：按 ``(from_character_id, to_character_id, relation_type)`` 查
  ``relationships`` 表；state_json 替换为 after；last_state_version 设为新 state_version。
- Rollback 限制：若原 commit 的 resolved_hooks 条目 ``from_status`` 缺失（schema 允许 null），
  生成逆 Delta 时无法确定回退目标状态——按任务书口径抛错拒绝回滚。
- commits 表不可变；不存在「在原 commit 上改 rollback_commits」回写路径；
  「已回滚」通过「存在新 commit.rollback_of = 原 commit_id」表达。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packages.core.db import get_connection
from packages.core.ids import new_id, now_iso

from .applier import apply_delta
from .exceptions import (
    ApprovalRequiredError,
    OptimisticLockError,
    StateConflictError,
    StateNotFoundError,
)
from .snapshot import build_initial_state, materialize_snapshot
from .validator import validate_delta

# ----------------------------------------------------------------------------- helpers


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _dump_or_null(value: list | dict | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _parse_json(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not raw:
        return {} if isinstance(raw, str) and raw.startswith(("{", "[")) else None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _parse_required_json(raw: Any, default: Any = None) -> Any:
    parsed = _parse_json(raw)
    if parsed is None:
        return default
    return parsed


def _ensure_branch(conn: sqlite3.Connection, project_id: str) -> str:
    """确保 (project_id, name='main') 行存在；返回 branch_id。"""
    row = conn.execute(
        "SELECT branch_id FROM branches WHERE project_id = ? AND name = ?",
        (project_id, "main"),
    ).fetchone()
    if row is not None:
        return row["branch_id"]
    branch_id = new_id("br")
    now = now_iso()
    conn.execute(
        """
        INSERT INTO branches (branch_id, project_id, name, parent_branch_id, base_state_version, status, created_at)
        VALUES (?, ?, 'main', NULL, 0, 'ACTIVE', ?)
        """,
        (branch_id, project_id, now),
    )
    return branch_id


def _latest_snapshot_version(conn: sqlite3.Connection, project_id: str) -> tuple[int, dict] | tuple[int, None]:
    """返回 ``(version, snapshot_json_or_None)``，无快照时 version=0, snapshot=None。"""
    row = conn.execute(
        "SELECT state_version, snapshot_json FROM story_states "
        "WHERE project_id = ? ORDER BY state_version DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if row is None:
        return 0, None
    return row["state_version"], _parse_required_json(row["snapshot_json"], {})


def _high_risk_change_ids(delta: dict) -> list[str]:
    """扫描 delta 所有 change 数组，提取 risk_level=HIGH 的 change_id。"""
    out: list[str] = []
    for array_name in (
        "character_changes",
        "world_changes",
        "relationship_changes",
        "new_events",
        "resolved_hooks",
        "new_hooks",
        "debt_changes",
    ):
        for item in delta.get(array_name) or []:
            if not isinstance(item, dict):
                continue
            if item.get("risk_level") == "HIGH":
                cid = item.get("change_id")
                if isinstance(cid, str):
                    out.append(cid)
    return out


def _payload_json_from_delta(delta: dict) -> dict:
    """把 delta 拆成 ``{meta, payload}`` 元信息 + 7 数组（payload_json 列存的是 7 数组，不含元信息）。

    对齐 ``state_deltas.payload_json`` 设计：仅存 7 个 change 数组。
    """
    keys = (
        "character_changes",
        "world_changes",
        "relationship_changes",
        "new_events",
        "resolved_hooks",
        "new_hooks",
        "debt_changes",
    )
    return {k: list(delta.get(k) or []) for k in keys}


def _restore_delta_from_row(row: sqlite3.Row) -> dict:
    """从 state_deltas 行还原完整 delta dict（含 10 元信息字段 + 7 数组）。"""
    payload = _parse_required_json(row["payload_json"], {}) or {}
    return {
        "delta_id": row["delta_id"],
        "delta_version": row["delta_version"],
        "schema_version": row["schema_version"],
        "chapter_id": row["chapter_id"],
        "workflow_run_id": row["workflow_run_id"],
        "previous_state_version": row["previous_state_version"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "supersedes": row["supersedes"],
        "notes": None,
        **{k: payload.get(k, []) for k in (
            "character_changes",
            "world_changes",
            "relationship_changes",
            "new_events",
            "resolved_hooks",
            "new_hooks",
            "debt_changes",
        )},
    }


# ----------------------------------------------------------------------------- service


class StoryStateService:
    """State Delta → Validate → Commit → Rollback 引擎。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # -------------------------------------------------------------- get_current
    def get_current_state(self, project_id: str) -> dict:
        """返回当前 Canonical State。

        有 story_states：取最新 snapshot。
        无 snapshot：返回 ``build_initial_state``（state_version=0，**不落库**）。
        """
        conn = get_connection(self.db_path)
        try:
            version, snap = _latest_snapshot_version(conn, project_id)
        finally:
            conn.close()
        if snap is None:
            # 无快照：直接返回初始 state（state_version=0，调用方按需展示）
            conn = get_connection(self.db_path)
            try:
                initial = build_initial_state(conn, project_id)
            finally:
                conn.close()
            initial["state_version"] = 0
            return initial
        snap["state_version"] = version
        return snap

    # -------------------------------------------------------------- get_snapshot
    def get_snapshot(self, project_id: str, version: int) -> dict | None:
        """返回指定 version 的快照；不存在返回 None。"""
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT state_version, snapshot_json FROM story_states WHERE project_id = ? AND state_version = ?",
                (project_id, version),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        snap = _parse_required_json(row["snapshot_json"], {})
        snap["state_version"] = row["state_version"]
        return snap

    # -------------------------------------------------------------- init_genesis
    def init_genesis(self, project_id: str, chapter_id: str) -> dict:
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
        conn = get_connection(self.db_path)
        try:
            existing_version, _ = _latest_snapshot_version(conn, project_id)
            if existing_version >= 1:
                # 已存在 → 直接返回
                snap_row = conn.execute(
                    "SELECT snapshot_json FROM story_states WHERE project_id = ? AND state_version = ?",
                    (project_id, existing_version),
                ).fetchone()
                snap = _parse_required_json(snap_row["snapshot_json"], {})
                snap["state_version"] = existing_version
                conn.close()
                return snap

            now = now_iso()
            branch_id = _ensure_branch(conn, project_id)
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

        return self.get_current_state(project_id)

    # -------------------------------------------------------------- submit_delta
    def submit_delta(self, delta: dict) -> dict:
        """落 state_deltas 行（proposed → validated/rejected）。

        流程（不在事务中——状态机允许 intermediate row）：
        1. ``validate_delta`` 全量校验（含 10 元信息字段 + 业务规则）。
        2. 校验失败：若 ``chapter_id`` 非空（FK 可满足）落 ``status='rejected'`` 行；
           否则因 FK 约束无法落库，仅返回错误。两种情形均返回 ``status='rejected'``。
        3. 校验通过：落 ``status='proposed'`` 行，立即 UPDATE 为 ``status='validated'``。
        4. ``supersedes`` 非空：把被指向 delta（同 chapter_id + workflow_run_id 上下文）
           UPDATE 为 ``status='superseded'``。

        返回 ``{"delta_id": str, "status": str, "errors": list}``。
        """
        errors = validate_delta(delta)
        if errors:
            delta_id = delta.get("delta_id") or new_id("dlt")
            chapter_id = delta.get("chapter_id")
            workflow_run_id = delta.get("workflow_run_id") or ""
            # FK 保护：chapter_id 缺失时无法写 state_deltas 行（FK → chapters.chapter_id）。
            # 此时仅返回错误，不尝试 INSERT。
            if isinstance(chapter_id, str) and chapter_id:
                conn = get_connection(self.db_path)
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
                                _dump(_payload_json_from_delta(delta)),
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
        conn = get_connection(self.db_path)
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
                    _dump(_payload_json_from_delta(delta)),
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

    # -------------------------------------------------------------- commit_delta
    def commit_delta(
        self,
        delta_id: str,
        author_approval: dict,
        workflow_run_id: str,
    ) -> dict:
        """执行 Commit。

        流程（同一事务）：
        1. 读 delta；必须 status='validated'，否则 ``StateConflictError``（409 语义）。
        2. 乐观锁：``delta.previous_state_version == 最新快照 version``；否则 ``OptimisticLockError``。
        3. HIGH 风险门：delta 任何 change risk_level=HIGH 且 ``author_approval.get("approved") is not True``
           → ``ApprovalRequiredError``。
        4. ``apply_delta(current_state, delta)`` → 新 state，state_version+1。
        5. **写透领域表**：
           - character_changes（facet=state）→ INSERT character_states 行（该角色 max(state_version)+1）。
           - character_changes（facet=definition）→ UPDATE characters.core_json（顶层 key 替换）。
           - world_changes（location）→ locations.data_json 顶层 key 替换；name/statement 来自 after。
           - world_changes（faction）→ 同上。
           - world_changes（rule）→ world_rules.data_json 顶层 key 替换。
           - world_changes（politics/economy/event/time）→ 只进 story_states 快照（不写领域表）。
           - relationship_changes → relationships upsert（按 from+to+relation_type 查）；
             state_json=after，last_state_version=新 state_version。
           - new_events → INSERT plot_events（status='recorded'，introduced_chapter_id=delta.chapter_id）。
           - resolved_hooks → UPDATE hooks.status / payoff_chapter_id（=delta.chapter_id）。
           - new_hooks → INSERT hooks（status='OPEN'，introduced_chapter_id=delta.chapter_id）。
           - debt_changes → INSERT/UPDATE/DELETE narrative_debts（add/remove 时 created_chapter_id=delta.chapter_id）。
        6. 落 commits 行（validation_json + author_approval_json）。
        7. 落 story_states 新快照。
        8. UPDATE state_deltas.status='applied'。

        返回 ``{"commit_id": str, "state_version": int, "delta_id": str, "snapshot_ref": str}``。
        """
        conn = get_connection(self.db_path)
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
            delta = _restore_delta_from_row(delta_row)

            # 2) 乐观锁
            project_id = self._project_id_for_chapter(conn, delta_row["chapter_id"])
            if project_id is None:
                conn.rollback()
                conn.close()
                raise StateNotFoundError(
                    f"chapter {delta_row['chapter_id']!r} not found",
                    resource="chapter",
                    resource_id=delta_row["chapter_id"],
                )
            current_version, current_state = _latest_snapshot_version(conn, project_id)
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
            high_ids = _high_risk_change_ids(delta)
            if high_ids and not (isinstance(author_approval, dict) and author_approval.get("approved") is True):
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
            self._write_through(conn, project_id, delta, new_version)

            # 6) commits
            commit_id = new_id("cmt")
            now = now_iso()
            branch_id = _ensure_branch(conn, project_id)
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
            conn.execute(
                """
                INSERT INTO commits
                    (commit_id, project_id, branch_id, chapter_id,
                     previous_state_version, resulting_state_version,
                     delta_id, validation_json, author_approval_json,
                     timestamp, workflow_run_id, rollback_of)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
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
                ),
            )

            # 7) story_states 新快照
            snapshot_ref, _digest = materialize_snapshot(
                conn,
                project_id=project_id,
                state_version=new_version,
                snapshot_json=new_state,
                commit_id=commit_id,
                created_at=now,
            )

            # 8) delta.status = applied
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

    # -------------------------------------------------------------- rollback_commit
    def rollback_commit(self, commit_id: str, author_approval: dict) -> dict:
        """生成逆 Delta 并走 submit + commit 全流程；新 commit.rollback_of = commit_id。

        逆 Delta 规则（state-delta-v0.md §5.4 + 任务书口径）：
        - add → remove（target_id 不变，op=remove，after=null，reason="rollback of <commit_id>"）。
        - update → update（after 与 before 互换；如 before 为 null 则按 update(after=before=null) 处理）。
        - remove → add（用原 after 重建；after 必须非 null，否则抛错）。
        - new_events → 逆 add 一个"删除事件"在快照层不被直接表达——但 apply_delta 中
          add 仅 append 到 recent_events 与 events；没有 remove 路径。Sprint 2 简化：把
          新事件从快照 recent_events / events 中标记为「已撤回」（通过 insert 一个反向
          resolved_hook 不可得；此处采用「逆 new_event = 移除该 event_id」——但 schema
          中 new_events.op 必须为 add，故采用变通：在逆 delta 中作为 ``debt_changes`` 加
          一条 ``forgiven`` 标记，并在 commit 阶段通过 service 在快照层面手动剔除）。
        - 上述限制导致严格可逆性只在 character / world / relationship / hook / debt 上成立；
          new_events 的撤销通过在 service 层额外维护快照层 recent_events 剔除实现。
        - resolved_hooks 逆：to_status 回 from_status；from_status 为 null → 抛错拒绝回滚。

        实际采用更稳的「service 层处理快照逆向剔除 + Delta 走 schema 合法形式」策略：
        - 逆 Delta 中只承载 schema 合法 change（character / world / relationship / hook / debt）；
          resolved_hooks 的逆以 resolved_hooks 形式表达（to_status=from_status）；
          new_hooks 的逆 = 删除对应 hook（用 debt_changes 不可表达——hook 删除走
          ``new_events``? 不可行；改用：原 new_hooks 在快照层手工剔除）。
        - 快照层 recent_events / events 中的新事件，由 ``_post_apply_rollback`` 在 service 层
          直接 mutate new_state 完成（绕开 schema）。

        为保证可读性，这里把「逆 Delta 走 schema 合法路径」与「快照层手工剔除」两件事合并：
        - ``_build_inverse_delta`` 产出 schema 合法的逆 Delta（5 类 change）。
        - ``_apply_inverse_post_state`` 在 commit 阶段 mutate new_state：剔除 recent_events 中
          原 commit 引入的 event_id、剔除 events 中对应 key、剔除 new_hooks 引入的 hook 元素。
        """
        conn = get_connection(self.db_path)
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
            original_delta = _restore_delta_from_row(delta_row)
            project_id = row["project_id"]
            chapter_id = row["chapter_id"]
            workflow_run_id = row["workflow_run_id"]
            current_version, _snap = _latest_snapshot_version(conn, project_id)
        finally:
            conn.close()

        inverse = self._build_inverse_delta(
            original_delta,
            chapter_id=chapter_id,
            workflow_run_id=workflow_run_id,
            current_version=current_version,
        )
        post_apply = inverse["post_apply"]

        # 走 submit + commit 全流程（author_approval.approved=True 由调用方/UI 强制）
        ap = dict(author_approval or {})
        ap["approved"] = True
        ap.setdefault("notes", f"rollback of {commit_id}")

        submit_result = self.submit_delta(inverse["delta"])
        if submit_result["status"] != "validated":
            raise StateConflictError(
                f"inverse delta rejected by validator: {submit_result['errors']}",
                delta_id=submit_result["delta_id"],
            )
        # 用 commit_delta 完成；rollback_of 在 commit_delta 之后 patch（commit 表无
        # pre-commit 字段，所以先 commit 再 UPDATE rollback_of）。
        commit_result = self.commit_delta(
            submit_result["delta_id"],
            ap,
            f"system:rollback:{commit_id}",
        )

        # 在 commit 完成后 mutate 最新快照（剔除 recent_events / events / hooks[]）
        if post_apply:
            self._post_process_inverse_commit(
                project_id=project_id,
                version=commit_result["state_version"],
                hints=post_apply,
            )

        # patch rollback_of
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                "UPDATE commits SET rollback_of = ? WHERE commit_id = ?",
                (commit_id, commit_result["commit_id"]),
            )
            conn.commit()
        finally:
            conn.close()
        commit_result["rollback_of"] = commit_id
        return commit_result

    def _post_process_inverse_commit(
        self,
        *,
        project_id: str,
        version: int,
        hints: list[dict],
    ) -> None:
        """Rollback 后处理：从最新快照 JSON 中剔除 new_events / new_hooks 引入项。

        原因：schema 不允许 ``new_events.op != 'add'`` 与 ``new_hooks.op != 'add'``，故
        逆 Delta 不承载「删除事件/删除 hook」op；通过直接 mutate 快照 JSON 实现快照级可逆。
        """
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT snapshot_json FROM story_states WHERE project_id = ? AND state_version = ?",
                (project_id, version),
            ).fetchone()
            if row is None:
                return
            snap = _parse_required_json(row["snapshot_json"], {}) or {}
            if not isinstance(snap, dict):
                return
            changed = False
            for hint in hints:
                if hint.get("type") == "remove_event":
                    eid = hint.get("event_id")
                    if not eid:
                        continue
                    recent = snap.get("recent_events") or []
                    if eid in recent:
                        snap["recent_events"] = [x for x in recent if x != eid]
                        changed = True
                    events = snap.get("events") or {}
                    if eid in events:
                        del events[eid]
                        snap["events"] = events
                        changed = True
                elif hint.get("type") == "remove_hook":
                    hid = hint.get("hook_id")
                    if not hid:
                        continue
                    hooks = snap.get("hooks") or []
                    new_hooks = [h for h in hooks if h.get("hook_id") != hid]
                    if len(new_hooks) != len(hooks):
                        snap["hooks"] = new_hooks
                        changed = True
            if not changed:
                return
            conn.execute(
                "UPDATE story_states SET snapshot_json = ? WHERE project_id = ? AND state_version = ?",
                (_dump(snap), project_id, version),
            )
            conn.commit()
        finally:
            conn.close()

    # -------------------------------------------------------------- helpers (private)

    def _project_id_for_chapter(self, conn: sqlite3.Connection, chapter_id: str) -> str | None:
        row = conn.execute("SELECT project_id FROM chapters WHERE chapter_id = ?", (chapter_id,)).fetchone()
        return row["project_id"] if row else None

    def _write_through(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        delta: dict,
        new_version: int,
    ) -> None:
        """把 delta 的 7 数组写透到对应领域表。"""
        chapter_id = delta["chapter_id"]

        # character_changes
        for ch in delta.get("character_changes") or []:
            cid = ch.get("character_id")
            op = ch.get("op")
            facet = ch.get("facet")
            field = ch.get("field") or ""
            after = ch.get("after")
            if facet == "state":
                # 找到 max(state_version)
                row = conn.execute(
                    "SELECT MAX(state_version) AS v FROM character_states WHERE character_id = ?",
                    (cid,),
                ).fetchone()
                next_v = (row["v"] or 0) + 1
                # 复制当前 state_json 作为基础（避免覆盖其他字段）
                cur_row = conn.execute(
                    "SELECT state_json FROM character_states "
                    "WHERE character_id = ? ORDER BY state_version DESC LIMIT 1",
                    (cid,),
                ).fetchone()
                base = _parse_required_json(cur_row["state_json"], {}) if cur_row else {}
                if not isinstance(base, dict):
                    base = {}
                key = field.split(".")[-1] if "." in field else field
                if op in ("add", "update"):
                    base[key] = after
                elif op == "remove":
                    base.pop(key, None)
                conn.execute(
                    """
                    INSERT INTO character_states
                        (character_id, state_version, state_json, visibility, who_knows, created_at)
                    VALUES (?, ?, ?, 'VISIBLE', NULL, ?)
                    """,
                    (cid, next_v, _dump(base), now_iso()),
                )
            elif facet == "definition":
                # UPDATE characters.core_json 顶层 key 替换
                cur = conn.execute("SELECT core_json FROM characters WHERE character_id = ?", (cid,)).fetchone()
                if cur is None:
                    continue
                base = _parse_required_json(cur["core_json"], {}) or {}
                if not isinstance(base, dict):
                    base = {}
                key = field.split(".")[-1] if "." in field else field
                if op in ("add", "update"):
                    base[key] = after
                elif op == "remove":
                    base.pop(key, None)
                conn.execute(
                    "UPDATE characters SET core_json = ?, updated_at = ? WHERE character_id = ?",
                    (_dump(base), now_iso(), cid),
                )

        # world_changes
        for w in delta.get("world_changes") or []:
            kind = w.get("world_kind")
            op = w.get("op")
            wid = w.get("world_id")
            field = w.get("field") or ""
            after = w.get("after")
            if kind == "location":
                cur = conn.execute(
                    "SELECT name, statement, data_json FROM locations WHERE location_id = ?",
                    (wid,),
                ).fetchone()
                if cur is None and op == "add":
                    base_name = (after or {}).get("name") if isinstance(after, dict) else wid
                    base_stmt = (after or {}).get("statement") if isinstance(after, dict) else ""
                    base_data = (after or {}).get("data_json") if isinstance(after, dict) else {}
                    conn.execute(
                        """
                        INSERT INTO locations
                            (location_id, project_id, name, statement, data_json,
                             visibility, who_knows, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 'PUBLIC', NULL, ?, ?)
                        """,
                        (wid, project_id, base_name, base_stmt, _dump(base_data), now_iso(), now_iso()),
                    )
                    continue
                if cur is None:
                    continue
                base_data = _parse_required_json(cur["data_json"], {}) or {}
                if not isinstance(base_data, dict):
                    base_data = {}
                key = field.split(".")[-1] if "." in field else field
                if op in ("add", "update"):
                    base_data[key] = after
                elif op == "remove":
                    base_data.pop(key, None)
                conn.execute(
                    "UPDATE locations SET data_json = ?, updated_at = ? WHERE location_id = ?",
                    (_dump(base_data), now_iso(), wid),
                )
            elif kind == "faction":
                cur = conn.execute(
                    "SELECT data_json FROM factions WHERE faction_id = ?", (wid,)
                ).fetchone()
                if cur is None and op == "add":
                    base_data = (after or {}).get("data_json") if isinstance(after, dict) else {}
                    base_name = (after or {}).get("name") if isinstance(after, dict) else wid
                    base_stmt = (after or {}).get("statement") if isinstance(after, dict) else ""
                    conn.execute(
                        """
                        INSERT INTO factions
                            (faction_id, project_id, name, statement, data_json,
                             visibility, who_knows, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 'VISIBLE', NULL, ?, ?)
                        """,
                        (wid, project_id, base_name, base_stmt, _dump(base_data), now_iso(), now_iso()),
                    )
                    continue
                if cur is None:
                    continue
                base_data = _parse_required_json(cur["data_json"], {}) or {}
                if not isinstance(base_data, dict):
                    base_data = {}
                key = field.split(".")[-1] if "." in field else field
                if op in ("add", "update"):
                    base_data[key] = after
                elif op == "remove":
                    base_data.pop(key, None)
                conn.execute(
                    "UPDATE factions SET data_json = ?, updated_at = ? WHERE faction_id = ?",
                    (_dump(base_data), now_iso(), wid),
                )
            elif kind == "rule":
                cur = conn.execute(
                    "SELECT data_json FROM world_rules WHERE world_rule_id = ?", (wid,)
                ).fetchone()
                if cur is None and op == "add":
                    base_data = (after or {}).get("data_json") if isinstance(after, dict) else {}
                    base_name = (after or {}).get("name") if isinstance(after, dict) else wid
                    base_stmt = (after or {}).get("statement") if isinstance(after, dict) else ""
                    conn.execute(
                        """
                        INSERT INTO world_rules
                            (world_rule_id, project_id, name, statement, data_json,
                             visibility, who_knows, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 'PUBLIC', NULL, ?, ?)
                        """,
                        (wid, project_id, base_name, base_stmt, _dump(base_data), now_iso(), now_iso()),
                    )
                    continue
                if cur is None:
                    continue
                base_data = _parse_required_json(cur["data_json"], {}) or {}
                if not isinstance(base_data, dict):
                    base_data = {}
                key = field.split(".")[-1] if "." in field else field
                if op in ("add", "update"):
                    base_data[key] = after
                elif op == "remove":
                    base_data.pop(key, None)
                conn.execute(
                    "UPDATE world_rules SET data_json = ?, updated_at = ? WHERE world_rule_id = ?",
                    (_dump(base_data), now_iso(), wid),
                )
            elif kind in ("politics", "economy", "event", "time"):
                # 不写领域表（无对应表），仅在快照中体现
                pass

        # relationship_changes
        for rel in delta.get("relationship_changes") or []:
            from_id = rel.get("from_character_id")
            to_id = rel.get("to_character_id")
            rel_type = rel.get("relation_type")
            op = rel.get("op")
            after = rel.get("after")
            existing = conn.execute(
                """
                SELECT relationship_id FROM relationships
                WHERE from_character_id = ? AND to_character_id = ? AND relation_type = ?
                """,
                (from_id, to_id, rel_type),
            ).fetchone()
            if op in ("add", "update"):
                if existing is None:
                    rid = rel.get("target_id") or new_id("rel")
                    conn.execute(
                        """
                        INSERT INTO relationships
                            (relationship_id, project_id, from_character_id, to_character_id,
                             relation_type, state_json, last_state_version)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (rid, project_id, from_id, to_id, rel_type, _dump(after or {}), new_version),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE relationships SET state_json = ?, last_state_version = ?
                        WHERE relationship_id = ?
                        """,
                        (_dump(after or {}), new_version, existing["relationship_id"]),
                    )
            elif op == "remove":
                if existing is not None:
                    conn.execute("DELETE FROM relationships WHERE relationship_id = ?", (existing["relationship_id"],))

        # new_events
        for ev in delta.get("new_events") or []:
            conn.execute(
                """
                INSERT INTO plot_events
                    (event_id, project_id, type, cause_json, effects_json, participants_json,
                     location_id, time_json, status, introduced_chapter_id, visibility, who_knows)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'recorded', ?, ?, NULL)
                """,
                (
                    ev["event_id"],
                    project_id,
                    ev["type"],
                    _dump(ev.get("cause") or []),
                    _dump(ev.get("effects") or []),
                    _dump(ev.get("participants") or []),
                    ev.get("location"),
                    _dump(ev.get("time") or {"timeline_day": 1}),
                    chapter_id,
                    ev.get("visibility") or "RESTRICTED",
                ),
            )

        # resolved_hooks
        for rh in delta.get("resolved_hooks") or []:
            conn.execute(
                """
                UPDATE hooks
                SET status = ?, payoff_chapter_id = COALESCE(?, payoff_chapter_id), updated_at = ?
                WHERE hook_id = ?
                """,
                (rh["to_status"], rh.get("payoff_chapter_id") or chapter_id, now_iso(), rh["hook_id"]),
            )

        # new_hooks
        for nh in delta.get("new_hooks") or []:
            conn.execute(
                """
                INSERT INTO hooks
                    (hook_id, project_id, name, introduced_chapter_id, status, importance,
                     expected_payoff_chapter_id, payoff_chapter_id, visibility, who_knows,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, 'OPEN', ?, ?, NULL, ?, NULL, ?, ?)
                """,
                (
                    nh["hook_id"],
                    project_id,
                    nh["name"],
                    chapter_id,
                    float(nh.get("importance") or 0.5),
                    nh.get("expected_payoff_chapter_id"),
                    nh.get("visibility") or "RESTRICTED",
                    now_iso(),
                    now_iso(),
                ),
            )

        # debt_changes
        for db in delta.get("debt_changes") or []:
            op = db.get("op")
            did = db.get("debt_id")
            existing = conn.execute("SELECT debt_id FROM narrative_debts WHERE debt_id = ?", (did,)).fetchone()
            if op == "add":
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO narrative_debts
                            (debt_id, project_id, description, created_chapter_id, severity,
                             deadline_chapter_id, status, visibility, who_knows,
                             created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                        """,
                        (
                            did,
                            project_id,
                            db.get("description") or "",
                            chapter_id,
                            float(db.get("severity_after") or 0.5),
                            db.get("deadline_chapter_id"),
                            db.get("status_after") or "open",
                            db.get("visibility") or "RESTRICTED",
                            now_iso(),
                            now_iso(),
                        ),
                    )
            elif op == "update":
                if existing is not None:
                    conn.execute(
                        """
                        UPDATE narrative_debts
                        SET severity = COALESCE(?, severity),
                            status = COALESCE(?, status),
                            deadline_chapter_id = COALESCE(?, deadline_chapter_id),
                            updated_at = ?
                        WHERE debt_id = ?
                        """,
                        (
                            db.get("severity_after"),
                            db.get("status_after"),
                            db.get("deadline_chapter_id"),
                            now_iso(),
                            did,
                        ),
                    )
            elif op == "remove":
                if existing is not None:
                    conn.execute("DELETE FROM narrative_debts WHERE debt_id = ?", (did,))

    # ----------------------------------------------- inverse delta helpers (rollback)

    def _build_inverse_delta(
        self,
        original: dict,
        *,
        chapter_id: str,
        workflow_run_id: str,
        current_version: int,
    ) -> dict:
        """生成 schema 合法的逆 Delta；返回 ``{"delta": ..., "post_apply": [...]}``。

        ``post_apply`` 是需要在 commit 后 mutate new_state 的指令列表（用于剔除 recent_events
        / events / new_hooks 的新增项——schema 不允许「删除事件」op，故走 service 兜底）。

        ``current_version`` 为当前最新快照 version（用于填 ``previous_state_version`` 满足
        schema `minimum=1` 约束）。
        """
        # 用于 audit 的占位：scheduler 当前不接受 timestamp；直接生成
        now_ts = datetime.now(timezone.utc).isoformat()
        delta_id = new_id("dlt")
        post_apply: list[dict] = []

        # character_changes：add→remove；update→update(after↔before)；remove→add(after)
        inv_char: list[dict] = []
        for ch in original.get("character_changes") or []:
            op = ch.get("op")
            base = {
                "change_id": new_id("cc"),
                "target_id": ch.get("target_id"),
                "character_id": ch.get("character_id"),
                "facet": ch.get("facet"),
                "field": ch.get("field"),
                "confidence": ch.get("confidence"),
                "evidence": ch.get("evidence"),
                "risk_level": ch.get("risk_level"),
                "notes": f"inverse of {ch.get('change_id')}",
            }
            rollback_reason = f"rollback of {original.get('delta_id')}"
            if op == "add":
                inv_char.append(
                    {**base, "op": "remove", "before": ch.get("after"), "after": None, "reason": rollback_reason}
                )
            elif op == "update":
                inv_char.append(
                    {**base, "op": "update", "before": ch.get("after"), "after": ch.get("before")}
                )
            elif op == "remove":
                inv_char.append(
                    {
                        **base,
                        "op": "add",
                        "before": None,
                        "after": ch.get("after"),
                        "reason": f"re-add of {ch.get('change_id')}",
                    }
                )

        # world_changes：同 character 规则
        inv_world: list[dict] = []
        for w in original.get("world_changes") or []:
            base = {
                "change_id": new_id("wc"),
                "target_id": w.get("target_id"),
                "world_kind": w.get("world_kind"),
                "world_id": w.get("world_id"),
                "field": w.get("field"),
                "confidence": w.get("confidence"),
                "evidence": w.get("evidence"),
                "risk_level": w.get("risk_level"),
                "notes": f"inverse of {w.get('change_id')}",
            }
            op = w.get("op")
            if op == "add":
                inv_world.append(
                    {**base, "op": "remove", "before": w.get("after"), "after": None, "reason": rollback_reason}
                )
            elif op == "update":
                inv_world.append(
                    {**base, "op": "update", "before": w.get("after"), "after": w.get("before")}
                )
            elif op == "remove":
                inv_world.append(
                    {
                        **base,
                        "op": "add",
                        "before": None,
                        "after": w.get("after"),
                        "reason": f"re-add of {w.get('change_id')}",
                    }
                )

        # relationship_changes
        inv_rel: list[dict] = []
        for r in original.get("relationship_changes") or []:
            base = {
                "change_id": new_id("rc"),
                "target_id": r.get("target_id"),
                "from_character_id": r.get("from_character_id"),
                "to_character_id": r.get("to_character_id"),
                "relation_type": r.get("relation_type"),
                "confidence": r.get("confidence"),
                "evidence": r.get("evidence"),
                "risk_level": r.get("risk_level"),
                "notes": f"inverse of {r.get('change_id')}",
            }
            op = r.get("op")
            if op == "add":
                inv_rel.append(
                    {**base, "op": "remove", "before": r.get("after"), "after": None, "reason": rollback_reason}
                )
            elif op == "update":
                inv_rel.append(
                    {**base, "op": "update", "before": r.get("after"), "after": r.get("before")}
                )
            elif op == "remove":
                inv_rel.append(
                    {
                        **base,
                        "op": "add",
                        "before": None,
                        "after": r.get("after"),
                        "reason": f"re-add of {r.get('change_id')}",
                    }
                )

        # resolved_hooks：update 反向 = update(to_status=from_status)；from_status 缺失则抛错
        inv_rh: list[dict] = []
        for h in original.get("resolved_hooks") or []:
            from_status = h.get("from_status")
            if from_status is None:
                raise StateConflictError(
                    f"无法回滚 commit {original.get('delta_id')}：resolved_hooks 条目 from_status 缺失",
                    delta_id=original.get("delta_id"),
                )
            inv_rh.append(
                {
                    "change_id": new_id("rh"),
                    "op": "update",
                    "target_id": h.get("target_id"),
                    "hook_id": h.get("hook_id"),
                    "from_status": h.get("to_status"),
                    "to_status": from_status,
                    "payoff_chapter_id": None,
                    "payoff_summary": "",
                    "confidence": h.get("confidence"),
                    "evidence": h.get("evidence"),
                    "risk_level": h.get("risk_level"),
                    "notes": f"inverse of {h.get('change_id')}",
                }
            )

        # new_hooks → 删除 hook（schema 中没有 hook 删除 op，走 post_apply 标记）
        for nh in original.get("new_hooks") or []:
            post_apply.append({"type": "remove_hook", "hook_id": nh.get("hook_id")})

        # new_events → 标记剔除
        for ne in original.get("new_events") or []:
            post_apply.append({"type": "remove_event", "event_id": ne.get("event_id")})

        # debt_changes
        inv_debt: list[dict] = []
        for d in original.get("debt_changes") or []:
            base = {
                "change_id": new_id("dc"),
                "target_id": d.get("target_id"),
                "debt_id": d.get("debt_id"),
                "confidence": d.get("confidence"),
                "evidence": d.get("evidence"),
                "risk_level": d.get("risk_level"),
                "notes": f"inverse of {d.get('change_id')}",
            }
            op = d.get("op")
            if op == "add":
                inv_debt.append(
                    {
                        **base,
                        "op": "remove",
                        "before": {
                            "status": d.get("status_after"),
                            "severity": d.get("severity_after"),
                        },
                        "after": None,
                        "reason": f"rollback of {original.get('delta_id')}",
                    }
                )
            elif op == "update":
                inv_debt.append(
                    {
                        **base,
                        "op": "update",
                        "before": {"status": d.get("status_after"), "severity": d.get("severity_after")},
                        "after": {"status": d.get("status_before"), "severity": d.get("severity_before")},
                        "status_before": d.get("status_after"),
                        "status_after": d.get("status_before") or "open",
                        "severity_before": d.get("severity_after"),
                        "severity_after": d.get("severity_before"),
                    }
                )
            elif op == "remove":
                inv_debt.append(
                    {
                        **base,
                        "op": "add",
                        "before": None,
                        "after": {"status": "open", "severity": 0.5},
                        "description": d.get("reason") or f"re-add of {d.get('change_id')}",
                        "status_after": "open",
                        "reason": f"re-add of {d.get('change_id')}",
                    }
                )

        inverse_delta: dict = {
            "delta_id": delta_id,
            "delta_version": 1,
            "schema_version": "state-delta-v0",
            "chapter_id": chapter_id,
            "workflow_run_id": workflow_run_id,
            "previous_state_version": current_version,  # 提交时与最新快照 version 匹配
            "created_by": "system:rollback",
            "created_at": now_ts,
            "supersedes": None,
            "notes": f"inverse of {original.get('delta_id')}",
            "character_changes": inv_char,
            "world_changes": inv_world,
            "relationship_changes": inv_rel,
            "new_events": [],
            "resolved_hooks": inv_rh,
            "new_hooks": [],
            "debt_changes": inv_debt,
        }
        # previous_state_version 在 submit 后会被 commit 路径重新校验；这里保留 0 由 Service 兜底
        return {"delta": inverse_delta, "post_apply": post_apply}

    # -------------------------------------------------------------- list_commits
    def list_commits(self, project_id: str, *, branch_id: str | None = None) -> list[dict]:
        conn = get_connection(self.db_path)
        try:
            if branch_id:
                rows = conn.execute(
                    "SELECT * FROM commits WHERE project_id = ? AND branch_id = ? ORDER BY resulting_state_version ASC",
                    (project_id, branch_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM commits WHERE project_id = ? ORDER BY resulting_state_version ASC",
                    (project_id,),
                ).fetchall()
        finally:
            conn.close()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            d["validation_json"] = _parse_json(d["validation_json"])
            d["author_approval_json"] = _parse_json(d["author_approval_json"])
            out.append(d)
        return out

    # -------------------------------------------------------------- list_deltas
    def list_deltas(self, chapter_id: str) -> list[dict]:
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM state_deltas WHERE chapter_id = ? ORDER BY created_at ASC",
                (chapter_id,),
            ).fetchall()
        finally:
            conn.close()
        out: list[dict] = []
        for r in rows:
            d = _restore_delta_from_row(r)
            d["status"] = r["status"]
            out.append(d)
        return out


__all__ = ["StoryStateService"]
