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
    BranchClosed,
    BranchNotFound,
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


def _encode_who_knows(value: list | None) -> str | None:
    """三态语义编码（对齐 knowledge-permission-v0.md §6 / state-delta-v0.md §2.6）：
    - ``None`` → ``NULL``（沿用实体现状，不参与合并）
    - ``[]`` → ``'[]'``（显式置空）
    - 非空 list → JSON（``ensure_ascii=False``）
    """
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return None


def _read_who_knows(change: dict) -> list | None:
    """从 change 条目读 who_knows；缺失视为 None（沿用）。"""
    return change.get("who_knows")


def _read_visibility(change: dict, default: str | None = None) -> str | None:
    """从 change 条目读 visibility；缺失 = 沿用（None）。"""
    return change.get("visibility") or default


def _apply_inverse_cleanup_to_state(state: dict, cleanup: dict) -> None:
    """Rollback 路径下 mutate ``state``（in place）：
    - 剔除 ``recent_events`` 中出现在 ``remove_event_ids`` 的 event_id；
    - 剔除 ``events`` 中相同 key；
    - 剔除 ``hooks`` 中 ``hook_id`` 在 ``remove_hook_ids`` 里的元素。

    必须在 ``commit_delta`` 同一事务内调用（在 materialize_snapshot 之前），这样落盘
    的 story_states 即「回滚后」语义；rollback 后 GET state 直接拿到该快照，无需 post-facto
    修改。
    """
    remove_event_ids = set(cleanup.get("remove_event_ids") or [])
    remove_hook_ids = set(cleanup.get("remove_hook_ids") or [])
    if remove_event_ids:
        recent = state.get("recent_events") or []
        if isinstance(recent, list):
            state["recent_events"] = [eid for eid in recent if eid not in remove_event_ids]
        events = state.get("events") or {}
        if isinstance(events, dict):
            for eid in list(events.keys()):
                if eid in remove_event_ids:
                    del events[eid]
            state["events"] = events
    if remove_hook_ids:
        hooks = state.get("hooks") or []
        if isinstance(hooks, list):
            state["hooks"] = [h for h in hooks if not (isinstance(h, dict) and h.get("hook_id") in remove_hook_ids)]


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


def _resolve_branch(
    conn: sqlite3.Connection, project_id: str, branch_id: str | None
) -> str:
    """解析并校验 branch_id；None → 返回 main branch_id（不创建）。

    - ``branch_id`` 为 None：返回 ``branches`` 表中 (project_id, name='main') 的
      ``branch_id``；不存在则抛 :class:`StateNotFoundError`（resource='branch'）。
    - ``branch_id`` 非 None：必须满足
      1. 该 branch 存在；
      2. ``branch.project_id == project_id``；
      3. ``branch.status == 'ACTIVE'``（分支已 MERGED / DISCARDED → BranchClosed）。
      任一失败抛对应异常。
    """
    if branch_id is None:
        row = conn.execute(
            "SELECT branch_id, status FROM branches WHERE project_id = ? AND name = ?",
            (project_id, "main"),
        ).fetchone()
        if row is None:
            raise StateNotFoundError(
                f"main branch for project {project_id!r} not found",
                resource="branch",
                resource_id=f"main:{project_id}",
            )
        return row["branch_id"]
    row = conn.execute(
        "SELECT branch_id, project_id, status FROM branches WHERE branch_id = ?",
        (branch_id,),
    ).fetchone()
    if row is None:
        raise BranchNotFound(
            f"branch {branch_id!r} not found",
            resource="branch",
            resource_id=branch_id,
        )
    if row["project_id"] != project_id:
        raise BranchNotFound(
            f"branch {branch_id!r} does not belong to project {project_id!r}",
            resource="branch",
            resource_id=branch_id,
        )
    if row["status"] != "ACTIVE":
        raise BranchClosed(
            f"branch {branch_id!r} is {row['status']!r}; only ACTIVE branches accept writes",
            branch_id=branch_id,
            status=row["status"],
        )
    return row["branch_id"]


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


def _branch_current_state(
    conn: sqlite3.Connection, project_id: str, branch_id: str
) -> tuple[int, dict | None]:
    """推导分支当前 state。

    语义（对齐任务书 Sprint 7）：
    - base：取 ``branches.base_state_version`` 处的 main 快照（即
      ``story_states`` 表中 ``state_version == base_state_version`` 的快照）。
      无该快照 → 兜底 ``build_initial_state``，version=base_state_version。
    - 在 base 之上按 ``commits.branch_id == branch_id`` 顺序（resulting_state_version ASC）
      重放每条 delta（delta_id 关联 state_deltas），复用 ``apply_delta``。
    - version 序列：base_state_version 起递增，分支内独立编号（与 main 主键空间
      不冲突，因为分支不写 story_states 行）。
    """
    branch_row = conn.execute(
        "SELECT base_state_version FROM branches WHERE branch_id = ?", (branch_id,)
    ).fetchone()
    if branch_row is None:
        raise StateNotFoundError(
            f"branch {branch_id!r} disappeared mid-transaction",
            resource="branch",
            resource_id=branch_id,
        )
    base_version = int(branch_row["base_state_version"] or 0)

    if base_version <= 0:
        base_snap = build_initial_state(conn, project_id)
        base_snap["state_version"] = base_version
    else:
        base_row = conn.execute(
            "SELECT state_version, snapshot_json FROM story_states "
            "WHERE project_id = ? AND state_version = ?",
            (project_id, base_version),
        ).fetchone()
        if base_row is None:
            base_snap = build_initial_state(conn, project_id)
            base_snap["state_version"] = base_version
        else:
            base_snap = _parse_required_json(base_row["snapshot_json"], {})
            base_snap["state_version"] = base_row["state_version"]

    rows = conn.execute(
        """
        SELECT c.commit_id, c.resulting_state_version, c.delta_id, d.payload_json
        FROM commits c
        JOIN state_deltas d ON c.delta_id = d.delta_id
        WHERE c.branch_id = ? AND c.project_id = ?
        ORDER BY c.resulting_state_version ASC
        """,
        (branch_id, project_id),
    ).fetchall()

    current_state = base_snap
    current_version = base_version
    for r in rows:
        payload = _parse_required_json(r["payload_json"], {}) or {}
        delta = _restore_delta_from_row_payload(r["delta_id"], payload)
        current_state = apply_delta(current_state, delta)
        current_version = int(r["resulting_state_version"])

    return current_version, current_state


def _high_risk_change_ids(delta: dict) -> list[str]:
    """扫描 delta 所有 change 数组，提取需要 author_approval 的 change_id。

    触发条件（state-delta-v0.md §2.5）：
    1. ``risk_level == "HIGH"``。
    2. ``character_changes[].facet == "definition"``（Character 长期定义变更，PRD §89 HIGH）。
    3. ``world_changes[].world_kind == "rule"``（World Rule 变更，PRD §89 HIGH）。
    """
    out: list[str] = []
    for ch in delta.get("character_changes") or []:
        if not isinstance(ch, dict):
            continue
        needs_approval = ch.get("risk_level") == "HIGH" or ch.get("facet") == "definition"
        if needs_approval:
            cid = ch.get("change_id")
            if isinstance(cid, str):
                out.append(cid)
    for w in delta.get("world_changes") or []:
        if not isinstance(w, dict):
            continue
        needs_approval = w.get("risk_level") == "HIGH" or w.get("world_kind") == "rule"
        if needs_approval:
            cid = w.get("change_id")
            if isinstance(cid, str):
                out.append(cid)
    for array_name in (
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


def _restore_delta_from_row_payload(delta_id: str, payload: dict) -> dict:
    """分支重放路径专用：从已解析 payload 还原 7 数组（最小元信息即可）。

    ``_branch_current_state`` 在重放时不需要全部 10 元信息字段，仅 7 数组；
    本函数提供最小可被 ``apply_delta`` 使用的 delta 形态（chapter_id 等设为
    占位符，不参与 apply）。
    """
    return {
        "delta_id": delta_id,
        "delta_version": 1,
        "schema_version": "state-delta-v0",
        "chapter_id": "",
        "workflow_run_id": "",
        "previous_state_version": 0,
        "created_by": "branch:replay",
        "created_at": "",
        "supersedes": None,
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
    def get_current_state(self, project_id: str, *, branch_id: str | None = None) -> dict:
        """返回当前 Canonical State。

        ``branch_id``（Sprint 7 新增）：
        - None（默认）：返回 main 当前 state；语义与 Sprint 2/4 逐字节一致——
          取 ``story_states`` 最新快照；无快照时 ``build_initial_state`` 并填
          ``state_version=0``。
        - 非 None：返回该分支当前 state——
          1) 取 ``branches.base_state_version`` 处的 main 快照作为 base；
          2) 按 ``commits.branch_id`` 顺序重放本分支 delta，复用 ``apply_delta``。
          分支不写 ``story_states`` 新行，state_version 为分支内独立编号。

        分支不存在 / 不属于该项目 → :class:`BranchNotFound`。
        """
        if branch_id is None:
            conn = get_connection(self.db_path)
            try:
                version, snap = _latest_snapshot_version(conn, project_id)
            finally:
                conn.close()
            if snap is None:
                conn = get_connection(self.db_path)
                try:
                    initial = build_initial_state(conn, project_id)
                finally:
                    conn.close()
                initial["state_version"] = 0
                return initial
            snap["state_version"] = version
            return snap

        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT project_id, status FROM branches WHERE branch_id = ?", (branch_id,)
            ).fetchone()
            if row is None:
                raise BranchNotFound(
                    f"branch {branch_id!r} not found",
                    resource="branch",
                    resource_id=branch_id,
                )
            if row["project_id"] != project_id:
                raise BranchNotFound(
                    f"branch {branch_id!r} does not belong to project {project_id!r}",
                    resource="branch",
                    resource_id=branch_id,
                )
            version, snap = _branch_current_state(conn, project_id, branch_id)
        finally:
            conn.close()
        if snap is None:
            conn = get_connection(self.db_path)
            try:
                initial = build_initial_state(conn, project_id)
            finally:
                conn.close()
            initial["state_version"] = version
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
    def submit_delta(self, delta: dict, *, branch_id: str | None = None) -> dict:
        """落 state_deltas 行（proposed → validated/rejected）。

        ``branch_id``（Sprint 7 新增）：可选，标识 Delta 归属分支。
        - None：现有行为不变（落 main），与 Sprint 2/4 字节级一致。
        - 非 None：先通过 ``_resolve_branch`` 校验归属 + ACTIVE 状态；校验失败抛
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
        # 缺失时 ``_project_id_for_chapter`` 返回 None 即跳过前置守卫——之后
        # ``validate_delta`` 会因 chapter_id 缺失返回 422 校验错误，与不带
        # branch_id 的行为一致，避免 KeyError 导致 500。
        if branch_id is not None:
            conn = get_connection(self.db_path)
            try:
                project_id = self._project_id_for_chapter(conn, delta.get("chapter_id"))
                if project_id is not None:
                    _resolve_branch(conn, project_id, branch_id)
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
        *,
        branch_id: str | None = None,
        _inverse_cleanup: dict | None = None,
        _rollback_of: str | None = None,
        _skip_approval: bool = False,
    ) -> dict:
        """执行 Commit。

        ``branch_id``（Sprint 7 新增）：可选，标识 commit 归属分支。
        - None（默认）：现有 main 行为，逐字节保持 Sprint 2/4 兼容。
          main branch 行由 ``_ensure_branch`` 兜底创建；乐观锁基于
          ``story_states`` 全局最新 version（与 Sprint 2/4 一致）。
        - 非 None：分支 commit **不写 ``story_states`` 新快照**（对齐任务书口径；
          分支当前状态由 ``get_current_state(branch_id=...)`` 在读路径推导：
          ``branches.base_state_version`` 处的快照 + 按 ``branch_id`` 过滤 commits
          顺序重放 delta）。``commits.resulting_state_version`` 取分支内独立递增
          序列（max(commits.resulting_state_version WHERE branch_id=X)+1）。
          领域表写透仍执行；rollback 触发时同样在 commit_delta 内完成逆路径清理。

        流程（同一事务）：
        1. 读 delta；必须 status='validated'，否则 ``StateConflictError``（409 语义）。
        2. 解析 branch（None → ensure main；否则 ``_resolve_branch`` 校验）。
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

            # 2) project / branch 解析 + 乐观锁
            project_id = self._project_id_for_chapter(conn, delta_row["chapter_id"])
            if project_id is None:
                conn.rollback()
                conn.close()
                raise StateNotFoundError(
                    f"chapter {delta_row['chapter_id']!r} not found",
                    resource="chapter",
                    resource_id=delta_row["chapter_id"],
                )
            if branch_id is None:
                # main 路径：保留 _ensure_branch 兜底创建 main 行（与 Sprint 2/4 字节级一致）
                branch_id = _ensure_branch(conn, project_id)
                current_version, current_state = _latest_snapshot_version(conn, project_id)
            else:
                # branch 路径：前置守卫（BranchNotFound / BranchClosed）
                branch_id = _resolve_branch(conn, project_id, branch_id)
                # 即使显式传 branch_id，若该 branch 是 main（名称 = 'main'），也走 main 路径
                # —— main 是项目根分支，必须保持与 Sprint 2/4 一致的快照行为；
                # 分支 commit 仅在非 main 的「feature branch」上走 _branch_current_state 推导路径。
                row = conn.execute(
                    "SELECT name FROM branches WHERE branch_id = ?", (branch_id,)
                ).fetchone()
                if row is not None and row["name"] == "main":
                    current_version, current_state = _latest_snapshot_version(conn, project_id)
                else:
                    current_version, current_state = _branch_current_state(conn, project_id, branch_id)
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
            self._write_through(
                conn, project_id, delta, new_version,
                skip_all=(not is_main_branch_for_write),
            )

            # 6) commits
            commit_id = new_id("cmt")
            now = now_iso()
            # branch_id 在步骤 2 已解析（main 路径 ensure main，分支路径 _resolve_branch）
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

            # 7) 逆路径清理（仅 rollback 触发）：与 _write_through 同一事务。
            if _inverse_cleanup:
                _apply_inverse_cleanup_to_state(new_state, _inverse_cleanup)
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

    # -------------------------------------------------------------- rollback_commit
    def rollback_commit(self, commit_id: str, author_approval: dict, *, branch_id: str | None = None) -> dict:
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
            original_branch_id = row["branch_id"]
            chapter_id = row["chapter_id"]
            workflow_run_id = row["workflow_run_id"]
            # 取原 commit 所在分支的当前 version 作为逆 delta 的 previous_state_version
            if original_branch_id is None:
                current_version, _snap = _latest_snapshot_version(conn, project_id)
            else:
                branch_row = conn.execute(
                    "SELECT name FROM branches WHERE branch_id = ?", (original_branch_id,)
                ).fetchone()
                if branch_row is not None and branch_row["name"] == "main":
                    current_version, _snap = _latest_snapshot_version(conn, project_id)
                else:
                    current_version, _snap = _branch_current_state(conn, project_id, original_branch_id)
            # 决定本次 rollback 落点：默认原分支（branch_id 仅作接口占位；当前实现维持原分支）
            _ = branch_id
        finally:
            conn.close()

        inverse = self._build_inverse_delta(
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

        submit_result = self.submit_delta(inverse["delta"], branch_id=original_branch_id)
        if submit_result["status"] != "validated":
            raise StateConflictError(
                f"inverse delta rejected by validator: {submit_result['errors']}",
                delta_id=submit_result["delta_id"],
            )
        # 用 commit_delta 完成；rollback_of 在 commit_delta 同一事务内落 commits 行；
        # inverse_cleanup 在同一事务内清理领域表 + mutate 快照。
        commit_result = self.commit_delta(
            submit_result["delta_id"],
            ap,
            f"system:rollback:{commit_id}",
            branch_id=original_branch_id,
            _inverse_cleanup=cleanup,
            _rollback_of=commit_id,
        )
        commit_result["rollback_of"] = commit_id
        return commit_result

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
        *,
        skip_new_events_hooks: bool = False,
        skip_all: bool = False,
    ) -> None:
        """把 delta 的 7 数组写透到对应领域表。

        ``skip_new_events_hooks``（Sprint 7 兼容）：仅跳过 ``new_events`` /
        ``new_hooks`` 写主表（plot_events / hooks）。

        ``skip_all``（Sprint 7 修订，Sprint 7 审查 P0 修复引入）：
        分支 commit 路径下为 True，**整体跳过**领域表写透（含 character /
        world / relationship / debt / new_events / new_hooks / resolved_hooks）。
        分支路径不应污染 main 领域表——分支的所有 7 数组副作用由 promote 时
        按序重放在 main commit 路径下统一写入。
        优先级：``skip_all=True`` 时跳过整个方法体；
        ``skip_all=False`` 时再按 ``skip_new_events_hooks`` 决定是否写
        ``new_events`` / ``new_hooks``。
        """
        if skip_all:
            # 分支 commit：领域表写透不在此路径执行；副作用由 promote 按序重放
            # 在 main commit 时统一落库（commit_delta 走 main 路径时不传
            # skip_all）。
            return
        chapter_id = delta["chapter_id"]

        # character_changes
        for ch in delta.get("character_changes") or []:
            cid = ch.get("character_id")
            op = ch.get("op")
            facet = ch.get("facet")
            field = ch.get("field") or ""
            after = ch.get("after")
            who_knows_enc = _encode_who_knows(_read_who_knows(ch))
            vis_value = _read_visibility(ch)
            if facet == "state":
                # P2-2: 若该角色无任何 state 行，先补 v1 行（空 state_json）再追加
                # 否则后续引用 max(state_version)+1 直接落到 (cid, 2) 跳过了 v1。
                seed_row = conn.execute(
                    "SELECT 1 FROM character_states WHERE character_id = ? LIMIT 1",
                    (cid,),
                ).fetchone()
                if seed_row is None:
                    conn.execute(
                        """
                        INSERT INTO character_states
                            (character_id, state_version, state_json, visibility, who_knows, created_at)
                        VALUES (?, 1, '{}', 'VISIBLE', NULL, ?)
                        """,
                        (cid, now_iso()),
                    )
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
                # visibility：None → 沿用 VISIBLE；显式值 → 使用
                vis_final = vis_value or "VISIBLE"
                conn.execute(
                    """
                    INSERT INTO character_states
                        (character_id, state_version, state_json, visibility, who_knows, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (cid, next_v, _dump(base), vis_final, who_knows_enc, now_iso()),
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
                # who_knows：缺失=沿用（不 UPDATE 该列）；非 None=显式覆盖
                if who_knows_enc is not None:
                    conn.execute(
                        """
                        UPDATE characters SET core_json = ?, who_knows = ?, updated_at = ?
                        WHERE character_id = ?
                        """,
                        (_dump(base), who_knows_enc, now_iso(), cid),
                    )
                else:
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
            who_knows_enc = _encode_who_knows(_read_who_knows(w))
            vis_value = _read_visibility(w)
            if kind == "location":
                cur = conn.execute(
                    "SELECT name, statement, data_json FROM locations WHERE location_id = ?",
                    (wid,),
                ).fetchone()
                if cur is None and op == "add":
                    base_name = ((after or {}).get("name") if isinstance(after, dict) else None) or wid
                    base_stmt = ((after or {}).get("statement") if isinstance(after, dict) else None) or ""
                    base_data = ((after or {}).get("data_json") if isinstance(after, dict) else None) or {}
                    vis_final = vis_value or "PUBLIC"
                    conn.execute(
                        """
                        INSERT INTO locations
                            (location_id, project_id, name, statement, data_json,
                             visibility, who_knows, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (wid, project_id, base_name, base_stmt, _dump(base_data),
                         vis_final, who_knows_enc, now_iso(), now_iso()),
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
                # who_knows 缺失=沿用（不更新该列）；非 None=显式覆盖
                if who_knows_enc is not None:
                    conn.execute(
                        "UPDATE locations SET data_json = ?, who_knows = ?, updated_at = ? WHERE location_id = ?",
                        (_dump(base_data), who_knows_enc, now_iso(), wid),
                    )
                else:
                    conn.execute(
                        "UPDATE locations SET data_json = ?, updated_at = ? WHERE location_id = ?",
                        (_dump(base_data), now_iso(), wid),
                    )
            elif kind == "faction":
                cur = conn.execute(
                    "SELECT data_json FROM factions WHERE faction_id = ?", (wid,)
                ).fetchone()
                if cur is None and op == "add":
                    base_data = ((after or {}).get("data_json") if isinstance(after, dict) else None) or {}
                    base_name = ((after or {}).get("name") if isinstance(after, dict) else None) or wid
                    base_stmt = ((after or {}).get("statement") if isinstance(after, dict) else None) or ""
                    vis_final = vis_value or "VISIBLE"
                    conn.execute(
                        """
                        INSERT INTO factions
                            (faction_id, project_id, name, statement, data_json,
                             visibility, who_knows, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (wid, project_id, base_name, base_stmt, _dump(base_data),
                         vis_final, who_knows_enc, now_iso(), now_iso()),
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
                if who_knows_enc is not None:
                    conn.execute(
                        "UPDATE factions SET data_json = ?, who_knows = ?, updated_at = ? WHERE faction_id = ?",
                        (_dump(base_data), who_knows_enc, now_iso(), wid),
                    )
                else:
                    conn.execute(
                        "UPDATE factions SET data_json = ?, updated_at = ? WHERE faction_id = ?",
                        (_dump(base_data), now_iso(), wid),
                    )
            elif kind == "rule":
                cur = conn.execute(
                    "SELECT data_json FROM world_rules WHERE world_rule_id = ?", (wid,)
                ).fetchone()
                if cur is None and op == "add":
                    base_data = ((after or {}).get("data_json") if isinstance(after, dict) else None) or {}
                    base_name = ((after or {}).get("name") if isinstance(after, dict) else None) or wid
                    base_stmt = ((after or {}).get("statement") if isinstance(after, dict) else None) or ""
                    vis_final = vis_value or "PUBLIC"
                    conn.execute(
                        """
                        INSERT INTO world_rules
                            (world_rule_id, project_id, name, statement, data_json,
                             visibility, who_knows, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (wid, project_id, base_name, base_stmt, _dump(base_data),
                         vis_final, who_knows_enc, now_iso(), now_iso()),
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
                if who_knows_enc is not None:
                    conn.execute(
                        "UPDATE world_rules SET data_json = ?, who_knows = ?, updated_at = ? WHERE world_rule_id = ?",
                        (_dump(base_data), who_knows_enc, now_iso(), wid),
                    )
                else:
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
        if not skip_new_events_hooks:
            for ev in delta.get("new_events") or []:
                ev_who = _encode_who_knows(_read_who_knows(ev))
                ev_vis = _read_visibility(ev) or "RESTRICTED"
                conn.execute(
                    """
                    INSERT INTO plot_events
                        (event_id, project_id, type, cause_json, effects_json, participants_json,
                         location_id, time_json, status, introduced_chapter_id, visibility, who_knows)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'recorded', ?, ?, ?)
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
                        ev_vis,
                        ev_who,
                    ),
                )

        # resolved_hooks
        # 哨兵：notes 含 ``__CLEAR_PAYOFF_CHAPTER__`` → 显式把 hooks.payoff_chapter_id 置 NULL
        # （用于逆 Delta；schema 不允许新字段，只能用 notes 字符串携带标记）。
        for rh in delta.get("resolved_hooks") or []:
            notes = rh.get("notes") or ""
            clear_payoff = "__CLEAR_PAYOFF_CHAPTER__" in notes
            if clear_payoff:
                conn.execute(
                    """
                    UPDATE hooks
                    SET status = ?, payoff_chapter_id = NULL, updated_at = ?
                    WHERE hook_id = ?
                    """,
                    (rh["to_status"], now_iso(), rh["hook_id"]),
                )
            else:
                conn.execute(
                    """
                    UPDATE hooks
                    SET status = ?, payoff_chapter_id = COALESCE(?, payoff_chapter_id), updated_at = ?
                    WHERE hook_id = ?
                    """,
                    (rh["to_status"], rh.get("payoff_chapter_id") or chapter_id, now_iso(), rh["hook_id"]),
                )

        # new_hooks
        if not skip_new_events_hooks:
            for nh in delta.get("new_hooks") or []:
                nh_who = _encode_who_knows(_read_who_knows(nh))
                nh_vis = _read_visibility(nh) or "RESTRICTED"
                conn.execute(
                    """
                    INSERT INTO hooks
                        (hook_id, project_id, name, introduced_chapter_id, status, importance,
                         expected_payoff_chapter_id, payoff_chapter_id, visibility, who_knows,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'OPEN', ?, ?, NULL, ?, ?, ?, ?)
                    """,
                    (
                        nh["hook_id"],
                        project_id,
                        nh["name"],
                        chapter_id,
                        float(nh.get("importance") or 0.5),
                        nh.get("expected_payoff_chapter_id"),
                        nh_vis,
                        nh_who,
                        now_iso(),
                        now_iso(),
                    ),
                )

        # debt_changes
        for db in delta.get("debt_changes") or []:
            op = db.get("op")
            did = db.get("debt_id")
            db_who = _encode_who_knows(_read_who_knows(db))
            db_vis = _read_visibility(db) or "RESTRICTED"
            existing = conn.execute("SELECT debt_id FROM narrative_debts WHERE debt_id = ?", (did,)).fetchone()
            if op == "add":
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO narrative_debts
                            (debt_id, project_id, description, created_chapter_id, severity,
                             deadline_chapter_id, status, visibility, who_knows,
                             created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            did,
                            project_id,
                            db.get("description") or "",
                            chapter_id,
                            float(db.get("severity_after") or 0.5),
                            db.get("deadline_chapter_id"),
                            db.get("status_after") or "open",
                            db_vis,
                            db_who,
                            now_iso(),
                            now_iso(),
                        ),
                    )
            elif op == "update":
                if existing is not None:
                    # 3-state who_knows：缺失=不更新该列（沿用），非 None=覆盖
                    if db_who is not None:
                        conn.execute(
                            """
                            UPDATE narrative_debts
                            SET severity = COALESCE(?, severity),
                                status = COALESCE(?, status),
                                deadline_chapter_id = COALESCE(?, deadline_chapter_id),
                                who_knows = ?,
                                updated_at = ?
                            WHERE debt_id = ?
                            """,
                            (
                                db.get("severity_after"),
                                db.get("status_after"),
                                db.get("deadline_chapter_id"),
                                db_who,
                                now_iso(),
                                did,
                            ),
                        )
                    else:
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
        """生成 schema 合法的逆 Delta；返回 ``{"delta": <dict>}``。

        逆 Delta 中 ``new_events`` 与 ``new_hooks`` 数组保持空——schema 禁止 remove op。
        原 delta 的 event_id / hook_id 由调用方（rollback_commit）从原 delta 收集并通过
        ``_inverse_cleanup`` 私有参数传给 commit_delta，commit_delta 在同事务内
        DELETE 领域表行 + mutate 快照 JSON。

        ``current_version`` 为当前最新快照 version（用于填 ``previous_state_version`` 满足
        schema `minimum=1` 约束）。
        """
        # 用于 audit 的占位：scheduler 当前不接受 timestamp；直接生成
        now_ts = datetime.now(timezone.utc).isoformat()
        delta_id = new_id("dlt")

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

        # resolved_hooks：update 反向 = update(to_status=from_status)；from_status 缺失则抛错。
        # 逆条目必须显式置 payoff_chapter_id=NULL —— 用 ``notes`` 携带哨兵
        # ``__CLEAR_PAYOFF_CHAPTER__``（schema 允许的字符串字段），由 _write_through 识别并清列。
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
                    # payoff_summary 必填（schema minLength=1）。填回滚说明。
                    "payoff_summary": f"reverted by rollback of {original.get('delta_id')}",
                    "confidence": h.get("confidence"),
                    "evidence": h.get("evidence"),
                    "risk_level": h.get("risk_level"),
                    # notes 携带哨兵：_write_through 检测到此标记即把 hooks.payoff_chapter_id 显式置 NULL。
                    "notes": f"inverse of {h.get('change_id')};__CLEAR_PAYOFF_CHAPTER__",
                }
            )

        # new_hooks 与 new_events 不在逆 Delta 中承载（schema 禁止 remove op）；
        # 调用方 rollback_commit 会从原 delta 收集 event_id / hook_id 并通过
        # _inverse_cleanup 私有参数传给 commit_delta，在同事务内清理领域表与 mutate 快照。

        # debt_changes
        # Schema-legal fields only: status_before/status_after/severity_before/severity_after/description/reason.
        # No `before`/`after` keys (debt_change schema doesn't define them).
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
            rollback_reason = f"rollback of {original.get('delta_id')}"
            if op == "add":
                # Inverse add → op=remove；remove 时 status_after 必填（schema）——
                # 用原 status_after 或兜底 "forgiven"（合法枚举）。
                inv_debt.append(
                    {
                        **base,
                        "op": "remove",
                        "status_after": d.get("status_after") or "forgiven",
                        "reason": rollback_reason,
                    }
                )
            elif op == "update":
                # Inverse update → status_before/status_after 互换；severity 同理。
                # status_after 必填；兜底 "open"。
                inv_debt.append(
                    {
                        **base,
                        "op": "update",
                        "status_before": d.get("status_after"),
                        "status_after": d.get("status_before") or "open",
                        "severity_before": d.get("severity_after"),
                        "severity_after": d.get("severity_before"),
                    }
                )
            elif op == "remove":
                # Inverse remove → op=add；用原值重建（description/severity_after/status_after）。
                # 若原 delta 缺这些字段，兜底为合法值。
                inv_debt.append(
                    {
                        **base,
                        "op": "add",
                        "description": d.get("description") or f"re-add of {d.get('change_id')}",
                        "severity_after": d.get("severity_after") if d.get("severity_after") is not None else 0.5,
                        "status_after": d.get("status_after") or "open",
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
        return {"delta": inverse_delta}

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

    # -------------------------------------------------------------- branches (Sprint 7)

    def create_branch(
        self,
        project_id: str,
        name: str,
        *,
        base_state_version: int | None = None,
    ) -> dict:
        """创建分支（state-delta-v0.md §6.3）。

        流程：
        1. 确保 main branch 行存在（``_ensure_branch``）。
        2. 同 project 下 name 唯一：重名 → :class:`StateConflictError`（409）。
        3. ``base_state_version`` 缺省 = main 当前最新 version（``story_states``
           全局最大 version；无 story_states 视为 0）。
        4. 插入 ``branches`` 行：``parent_branch_id`` = main.branch_id，``status='ACTIVE'``。

        返回 ``dict`` 包含 ``branch_id`` / ``name`` / ``parent_branch_id`` /
        ``base_state_version`` / ``status`` / ``created_at``。
        """
        if not isinstance(name, str) or not name or name == "main":
            raise StateConflictError(
                f"branch name {name!r} invalid: must be non-empty and != 'main'",
                delta_id=None,
            )
        conn = get_connection(self.db_path)
        try:
            main_branch_id = _ensure_branch(conn, project_id)
            dup = conn.execute(
                "SELECT branch_id FROM branches WHERE project_id = ? AND name = ?",
                (project_id, name),
            ).fetchone()
            if dup is not None:
                raise StateConflictError(
                    f"branch name {name!r} already exists for project {project_id!r}",
                    delta_id=None,
                )
            if base_state_version is None:
                v, _ = _latest_snapshot_version(conn, project_id)
                base = int(v)
            else:
                base = int(base_state_version)
            branch_id = new_id("br")
            now = now_iso()
            conn.execute(
                """
                INSERT INTO branches
                    (branch_id, project_id, name, parent_branch_id, base_state_version, status, created_at)
                VALUES
                    (?, ?, ?, ?, ?, 'ACTIVE', ?)
                """,
                (branch_id, project_id, name, main_branch_id, base, now),
            )
            conn.commit()
        finally:
            conn.close()
        return {
            "branch_id": branch_id,
            "project_id": project_id,
            "name": name,
            "parent_branch_id": main_branch_id,
            "base_state_version": base,
            "status": "ACTIVE",
            "created_at": now,
        }

    def list_branches(self, project_id: str) -> list[dict]:
        """列项目下所有分支（main 优先，其余按 created_at ASC）。"""
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT * FROM branches
                WHERE project_id = ?
                ORDER BY (name = 'main') DESC, created_at ASC
                """,
                (project_id,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def promote_branch(
        self,
        project_id: str,
        branch_id: str,
        *,
        chapter_id: str | None = None,
    ) -> dict:
        """把分支 commits 按序重放到 main（Sprint 7 审查 P0 修订）。

        旧实现（被否决）：把分支全部 commits 的 7 数组 concat 成单个 merged
        delta，在 main 上提交一次。该方案的**致命缺陷**在于：applier.py
        固定顺序 ``resolved_hooks → new_hooks``（与 character / world 等
        顺序无关），合并时若分支内某 commit 顺序为「先 new_hook 后
        resolved_hooks 同 hook_id」，concat 后新顺序会变成「resolved 先于
        add」，导致 resolved 静默丢失——hook 以 OPEN 落 main。

        新语义（Sprint 7 审查拍板）：
        1. 校验 branch 存在 + 属于同 project + ``status='ACTIVE'``。
        2. 取分支全部 commits 按 ``resulting_state_version ASC``。
        3. 对每个分支 commit：
           - 读原 delta 的 ``payload_json``（7 数组）；
           - 构造 replay delta：**新 delta_id**（避免撞 PK），``chapter_id``
             取该 commit 的原 chapter_id（参数 ``chapter_id`` 仅作「兜底
             补充」，不强制覆盖），``previous_state_version`` 重写为 main
             当前最新 version（每次重放递增 +1），``workflow_run_id`` /
             ``created_by`` / ``notes`` 标记 ``"replay-from-branch"``；
           - ``submit_delta`` 校验，通过后 ``commit_delta(branch_id=None)``
             走 main 路径；``author_approval['promoted_from']`` + ``source_commit_id``
             注入 ``validation_json`` 便于审计。
        4. 全部成功后 ``UPDATE branches SET status='MERGED'``；中途失败：
           已成功重放的 main commits 保留（与正常 commit 一致的事务语义），
           branch 保持 ``ACTIVE``，错误向上抛（router 按异常类型映射到
           422 / 409 / 500）。
        5. **新 delta 行不复用原 delta_id**：原 state_deltas 行（status=
           applied、归属分支）只读不动；重放生成**新 delta 行**（新 delta_id，
           同样 ``status='applied'``，归属 main commits）。

        偏离 ``state-delta-v0.md §6.3``「单一合并 commit」字面——详见
        ``packages/core/story_state/README.md §6.5`` deviation 段落（采用
        按序重放保留每条 delta 顺序语义与写透正确性；单合并 commit 的
        concat 方案被否决以避免 resolved_hooks/new_hooks 应用顺序破坏）。

        返回 dict：``commit_id`` / ``state_version`` / ``delta_id``（最后一个
        重放产生的 main delta_id）/ ``promoted_from`` / ``promoted_commits`` /
        ``branch_id``（main.branch_id）/ ``replayed_delta_ids``（本次重放
        在 main 上产生的新 delta_id 列表，便于测试断言）。
        """
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT branch_id, project_id, status, base_state_version FROM branches WHERE branch_id = ?",
                (branch_id,),
            ).fetchone()
            if row is None:
                raise BranchNotFound(
                    f"branch {branch_id!r} not found",
                    resource="branch",
                    resource_id=branch_id,
                )
            if row["project_id"] != project_id:
                raise BranchNotFound(
                    f"branch {branch_id!r} does not belong to project {project_id!r}",
                    resource="branch",
                    resource_id=branch_id,
                )
            if row["status"] != "ACTIVE":
                raise BranchClosed(
                    f"branch {branch_id!r} is {row['status']!r}; only ACTIVE branches can be promoted",
                    branch_id=branch_id,
                    status=row["status"],
                )

            commit_rows = conn.execute(
                """
                SELECT commit_id, chapter_id, resulting_state_version, delta_id
                FROM commits
                WHERE branch_id = ? AND project_id = ?
                ORDER BY resulting_state_version ASC
                """,
                (branch_id, project_id),
            ).fetchall()

            if not commit_rows:
                # 空分支：直接 MERGED，返回最小结果
                now = now_iso()
                conn.execute(
                    "UPDATE branches SET status = 'MERGED' WHERE branch_id = ?",
                    (branch_id,),
                )
                conn.commit()
                return {
                    "branch_id": branch_id,
                    "status": "MERGED",
                    "promoted_commits": 0,
                    "commit_id": None,
                    "state_version": int(row["base_state_version"] or 0),
                    "delta_id": None,
                    "replayed_delta_ids": [],
                }

            # 收集分支所有 (commit_id, chapter_id, delta_id) 用于按序重放
            ordered: list[tuple[str, str, str]] = [
                (cr["commit_id"], cr["chapter_id"] or chapter_id, cr["delta_id"])
                for cr in commit_rows
            ]
        finally:
            conn.close()

        # 按序重放：每次 main commit +1，不复用原 delta 行（避免 PK 冲突与
        # 跨分支溯源混乱）；中途失败保留已重放的 main commits，错误向上抛。
        replayed: list[dict] = []  # [{delta_id, commit_id, state_version, source_commit_id}, ...]
        try:
            for source_commit_id, source_chapter_id, source_delta_id in ordered:
                replay_payload = self._load_branch_commit_payload(source_delta_id)
                # 取 main 当前最新 version（每次重放后 +1）
                conn = get_connection(self.db_path)
                try:
                    current_version, _ = _latest_snapshot_version(conn, project_id)
                finally:
                    conn.close()

                replay_delta_id = new_id("dlt")
                now_ts = datetime.now(timezone.utc).isoformat()
                # chapter_id：优先使用参数 ``chapter_id``（任务书「可选章节归属」）；
                # 否则沿用该 commit 在分支上的原 chapter_id（保持重放与原 delta
                # 的章节归属语义一致，便于审计）。
                replay_chapter_id = chapter_id or source_chapter_id
                replay_delta = {
                    "delta_id": replay_delta_id,
                    "delta_version": 1,
                    "schema_version": "state-delta-v0",
                    "chapter_id": replay_chapter_id,
                    "workflow_run_id": f"system:promote:{branch_id}",
                    "previous_state_version": current_version,
                    "created_by": f"system:promote:{branch_id}",
                    "created_at": now_ts,
                    "supersedes": None,
                    "notes": f"replay-from-branch:{branch_id}:source_commit:{source_commit_id}",
                    **replay_payload,
                }

                submit_result = self.submit_delta(replay_delta)
                if submit_result["status"] != "validated":
                    raise StateConflictError(
                        f"promote replay delta rejected by validator: {submit_result['errors']}",
                        delta_id=submit_result["delta_id"],
                    )
                ap: dict = {
                    "approver": f"system:promote:{branch_id}",
                    "notes": f"replay from branch {branch_id} (source_commit={source_commit_id})",
                    "promoted_from": branch_id,
                    "source_commit_id": source_commit_id,
                }
                commit_result = self.commit_delta(
                    submit_result["delta_id"],
                    ap,
                    f"system:promote:{branch_id}",
                    branch_id=None,  # 走 main 路径
                )
                replayed.append({
                    "delta_id": replay_delta_id,
                    "commit_id": commit_result["commit_id"],
                    "state_version": commit_result["state_version"],
                    "source_commit_id": source_commit_id,
                })
        except Exception:
            # 中途失败：保留已成功重放的 main commits（与正常 commit 一致
            # 的事务语义：每次 commit_delta 独立事务、已 commit 不回滚），
            # branch 保持 ACTIVE，错误向上抛。
            raise

        # 全部成功 → UPDATE branches.status='MERGED'
        conn = get_connection(self.db_path)
        try:
            conn.execute(
                "UPDATE branches SET status = 'MERGED' WHERE branch_id = ?",
                (branch_id,),
            )
            conn.commit()
        finally:
            conn.close()

        last = replayed[-1]
        # 取 main.branch_id 便于 router / 测试断言
        _conn = get_connection(self.db_path)
        try:
            _row = _conn.execute(
                "SELECT branch_id FROM branches WHERE project_id = ? AND name = 'main'",
                (project_id,),
            ).fetchone()
            main_branch_id = _row["branch_id"] if _row is not None else None
        finally:
            _conn.close()

        return {
            "commit_id": last["commit_id"],
            "delta_id": last["delta_id"],
            "state_version": last["state_version"],
            "promoted_from": branch_id,
            "promoted_commits": len(replayed),
            "branch_id": main_branch_id,
            "replayed_delta_ids": [r["delta_id"] for r in replayed],
            "snapshot_ref": None,
        }

    def _load_branch_commit_payload(self, source_delta_id: str) -> dict:
        """从 state_deltas 行读 ``payload_json``，还原 7 数组用于重放。

        重放路径专用：仅读 payload_json 列（7 数组），不依赖元信息字段；
        元信息（chapter_id / previous_state_version / created_by 等）由
        promote_branch 按 main 当前上下文重写。
        """
        conn = get_connection(self.db_path)
        try:
            d_row = conn.execute(
                "SELECT payload_json FROM state_deltas WHERE delta_id = ?",
                (source_delta_id,),
            ).fetchone()
        finally:
            conn.close()
        if d_row is None:
            raise StateConflictError(
                f"promote replay source delta {source_delta_id!r} not found in state_deltas",
                delta_id=source_delta_id,
            )
        payload = _parse_required_json(d_row["payload_json"], {}) or {}
        keys = (
            "character_changes",
            "world_changes",
            "relationship_changes",
            "new_events",
            "resolved_hooks",
            "new_hooks",
            "debt_changes",
        )
        return {k: list(payload.get(k) or []) for k in keys}

    def diff_versions(
        self,
        project_id: str,
        version_a: int,
        version_b: int,
        *,
        branch_id: str | None = None,
    ) -> dict:
        """返回两个 state_version 之间结构化 diff。

        口径（任务书）：基于 ``story_states`` 表的快照对比；分支 commit 不写
        ``story_states``，因此 ``diff_versions`` 仅支持 main 路径。``branch_id``
        仅作为审计字段记入返回结构；不参与快照读取。

        若调用方需要分支视角的 diff，应在外部用 ``get_current_state`` 两次取
        分支快照 JSON 自行 diff；本接口不直接支撑。

        返回结构（递归 dict diff，list 按 id/name 键匹配）：
        .. code-block:: python

            {
              "characters": {"changed": [...], "added": [...], "removed": [...]},
              "world":     {"locations": {...}, "factions": {...}, ...},
              "hooks":     {"changed": [...], "added": [...], "removed": [...]},
              "debts":     {"changed": [...], "added": [...], "removed": [...]},
              "events_changed":  {"<event_id>": {...}},
              "recent_events":   {"added"/"removed"/"changed"},
              "version_a": int,
              "version_b": int,
              "branch_id": str | None,
            }
        """
        # 分支视角的 diff 不在本接口范围（MVP 限制）
        if branch_id is not None:
            raise StateConflictError(
                "diff_versions does not support branch_id; use get_current_state twice to diff branches externally",
                delta_id=None,
            )
        conn = get_connection(self.db_path)
        try:
            snap_a = self.get_snapshot(project_id, version_a)
            snap_b = self.get_snapshot(project_id, version_b)
        finally:
            conn.close()

        if snap_a is None or snap_b is None:
            raise StateNotFoundError(
                f"snapshot not found: a={version_a} exists={snap_a is not None}, "
                f"b={version_b} exists={snap_b is not None}",
                resource="snapshot",
            )
        a = strip_state_version(snap_a)
        b = strip_state_version(snap_b)
        return diff_snapshots(a, b, version_a=version_a, version_b=version_b, branch_id=None)


# ----------------------------------------------------------------------------- diff helpers (Sprint 7)


def strip_state_version(snap: dict) -> dict:
    """去掉 ``state_version`` 字段以便 diff。"""
    if isinstance(snap, dict) and "state_version" in snap:
        out = dict(snap)
        out.pop("state_version", None)
        return out
    return snap


def _list_key(item: dict) -> str | None:
    """提取列表元素的稳定 key（id/name/hook_id/debt_id/event_id 等）。"""
    if not isinstance(item, dict):
        return None
    for k in ("character_id", "location_id", "faction_id", "world_rule_id",
              "hook_id", "debt_id", "event_id", "relationship_id", "id", "name"):
        v = item.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _diff_lists(a: list, b: list) -> dict:
    """按稳定 key 配对 diff 两个列表。

    返回 ``{"added": [...], "removed": [...], "changed": [{...}, ...]}``；仅在确有差异时返回非空。
    """
    a_keys = {_list_key(x): x for x in a if isinstance(x, dict)}
    b_keys = {_list_key(x): x for x in b if isinstance(x, dict)}
    a_set = set(a_keys.keys())
    b_set = set(b_keys.keys())
    added_keys = b_set - a_set
    removed_keys = a_set - b_set
    common = a_set & b_set
    changed: list = []
    for k in sorted(common):
        if a_keys[k] != b_keys[k]:
            sub = _diff_dicts(a_keys[k], b_keys[k])
            changed.append({"id": k, **({"changes": sub} if sub else {})})
    out: dict = {}
    if added_keys:
        out["added"] = [b_keys[k] for k in sorted(added_keys)]
    if removed_keys:
        out["removed"] = [a_keys[k] for k in sorted(removed_keys)]
    if changed:
        out["changed"] = changed
    return out


def _diff_dicts(a: dict, b: dict) -> dict:
    """递归 diff 两个 dict：仅返回 b 中与 a 不同（含 key 存在/值不同）的字段。

    list 元素按稳定 key 配对（``_list_key``）；非 dict/list 直接 ``{before, after}``。
    """
    diff: dict = {}
    keys = set(a.keys()) | set(b.keys())
    for k in keys:
        av = a.get(k)
        bv = b.get(k)
        if isinstance(av, dict) and isinstance(bv, dict):
            sub = _diff_dicts(av, bv)
            if sub:
                diff[k] = sub
        elif isinstance(av, list) and isinstance(bv, list):
            ld = _diff_lists(av, bv)
            if ld:
                diff[k] = ld
        elif av != bv:
            diff[k] = {"before": av, "after": bv}
    return diff


def diff_snapshots(a: dict, b: dict, *, version_a: int, version_b: int, branch_id: str | None) -> dict:
    """按 snapshot 结构组装 diff。"""
    out: dict = {
        "version_a": version_a,
        "version_b": version_b,
        "branch_id": branch_id,
    }
    chars_diff = _diff_lists(a.get("characters") or [], b.get("characters") or [])
    if chars_diff:
        out["characters"] = chars_diff
    world_a = a.get("world") or {}
    world_b = b.get("world") or {}
    world_diff = _diff_dicts(world_a, world_b)
    if world_diff:
        out["world"] = world_diff
    hooks_diff = _diff_lists(a.get("hooks") or [], b.get("hooks") or [])
    if hooks_diff:
        out["hooks"] = hooks_diff
    debts_diff = _diff_lists(a.get("debts") or [], b.get("debts") or [])
    if debts_diff:
        out["debts"] = debts_diff
    events_a = a.get("events") or {}
    events_b = b.get("events") or {}
    events_diff = _diff_dicts(events_a, events_b)
    if events_diff:
        out["events_changed"] = events_diff
    recent_diff = _diff_lists(a.get("recent_events") or [], b.get("recent_events") or [])
    if recent_diff:
        out["recent_events"] = recent_diff
    return out


__all__ = ["StoryStateService"]
