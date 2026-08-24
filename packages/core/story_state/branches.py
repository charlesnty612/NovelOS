"""Story State 分支能力（Sprint 7 + V2.0 Wave B 物化）。

职责（god-object 拆分后）：
- ``ensure_branch``——确保 (project_id, name='main') 行存在；返回 branch_id。
- ``resolve_branch``——解析并校验 branch_id；None → 返回 main branch_id（不创建）；
  非 None → 必须存在 / 属于该项目 / status='ACTIVE'。
- ``branch_current_state``——推导分支当前 state（**V2.0 Wave B 物化**）：
  最近一次物化快照 + 物化点之后的增量重放（找不到物化回退全量重放，保持兼容）。
- ``materialize_branch_snapshot`` / ``_read_branch_snapshot`` / ``_list_branch_snapshots``
  —— 物化读写辅助（create_branch / promote_branch 落盘专用）。
- ``create_branch`` / ``list_branches`` / ``promote_branch`` / ``load_branch_commit_payload``
  —— 分支 CRUD + 按序重放 promote（**create_branch 与 promote_branch 各自落盘物化快照**）。

设计要点：
- 分支 commit **不写 story_states 行**（按任务书口径）；分支当前状态由
  ``queries.get_current_state(branch_id=...)`` 读路径推导。
- V2.0 Wave B 物化（database/migrations/0009_branch_snapshots.sql）：
  * 新建 ``branch_snapshots(branch_id, state_version, snapshot_json, created_at)``，
    PK = (branch_id, state_version)；branch_snapshots 行属「推导缓存」——
    推导缓存与 commits 表同语义纯函数性，可丢可重建，无审计必要。
  * 物化时机：
    - ``create_branch`` 创建成功后 → 物化 base_state_version 处的 main 快照
      （state_version=base_state_version）作为「初始空基线」。
    - ``promote_branch`` 全部重放成功后 → main 分支物化新基线
      （state_version=新 main latest version）。
  * 读路径（``branch_current_state``）：先查 ``branch_snapshots`` 最近物化（按
    state_version DESC LIMIT 1）；若存在 → 从该物化点开始增量重放本分支
    「state_version > 物化点」的 commits；找不到物化 → 兜底全量重放
    （与旧行为一致；保留旧分支无感升级）。
  * 主线（branch_id=None / main）走 ``story_states`` 最新版本快照，行为零变化。
- promote 按序重放（每条 commit 走 ``submit_delta + commit_delta(branch_id=None)``），
  不复用原 delta_id，``validation_json.promoted_from`` + ``source_commit_id`` 标记审计。
- 与原 ``service._ensure_branch`` / ``_resolve_branch`` / ``_branch_current_state`` /
  ``create_branch`` / ``list_branches`` / ``promote_branch`` / ``_load_branch_commit_payload``
  **公开行为逐字节相同**（main 路径零变化；分支读路径仅在「有物化」时走增量，行为等价）
  仅文件位置变更。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from packages.core.ids import new_id, now_iso

from .applier import apply_delta
from .exceptions import BranchClosed, BranchNotFound, StateConflictError, StateNotFoundError
from .snapshot import build_initial_state
from .snapshots import _parse_required_json, latest_snapshot_version


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def ensure_branch(conn: sqlite3.Connection, project_id: str) -> str:
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


def resolve_branch(
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


def _read_branch_snapshot(
    conn: sqlite3.Connection,
    branch_id: str,
    *,
    after_version: int | None = None,
) -> tuple[int, dict] | tuple[int, None]:
    """取 ``branch_snapshots`` 最近一次物化快照。

    参数：
    - ``branch_id``：分支 ID（main 也可走本函数——但主线读路径不调用本函数）。
    - ``after_version``（V2.0 Wave B 预留）：若指定，仅返回 ``state_version > after_version``
      的物化行；当前实现仍走「最近一次」（MVP 仅在 create/promote 物化一行，无中间态）。

    返回：``(state_version, snapshot_dict_or_None)``；无物化行时 version=0, snapshot=None。
    """
    row = conn.execute(
        """
        SELECT state_version, snapshot_json
        FROM branch_snapshots
        WHERE branch_id = ?
        ORDER BY state_version DESC
        LIMIT 1
        """,
        (branch_id,),
    ).fetchone()
    if row is None:
        return 0, None
    return int(row["state_version"]), _parse_required_json(row["snapshot_json"], {})


def materialize_branch_snapshot(
    conn: sqlite3.Connection,
    *,
    branch_id: str,
    state_version: int,
    snapshot_json: dict,
    created_at: str,
) -> None:
    """把分支物化快照落库到 ``branch_snapshots``。

    V2.0 Wave B 物化时机：
    - ``create_branch`` 创建成功后：``state_version=base_state_version``，以 base 处
      的 main 快照内容（已含 base_state_version 字段）作为初始空基线。
    - ``promote_branch`` 全部重放成功后（main 分支）：``state_version=新 main latest
      version``，snapshot 为 promote 后的 main state。

    本函数不 commit / rollback——由调用方在同一事务内完成；写透 ``branch_snapshots``
    表的 INSERT / UPDATE 不抛异常时调用方负责 commit。

    兼容旧分支（无物化）→ 读路径自动回退全量重放；不需要回填旧分支物化。
    """
    serialized = _dump(snapshot_json)
    # 若同 (branch_id, state_version) 已有物化行 → REPLACE（幂等），
    # 避免 create_branch 重跑或 promote 重跑触发 UNIQUE 冲突。
    conn.execute(
        """
        INSERT OR REPLACE INTO branch_snapshots
            (branch_id, state_version, snapshot_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (branch_id, state_version, serialized, created_at),
    )


def branch_current_state(
    conn: sqlite3.Connection, project_id: str, branch_id: str
) -> tuple[int, dict | None]:
    """推导分支当前 state（V2.0 Wave B 物化读路径）。

    读路径策略：
    1) 取 ``branches.base_state_version`` 处的 main 快照作为「fallback base」
       （无该快照 → ``build_initial_state`` 兜底）。
    2) 查 ``branch_snapshots`` 最近一次物化（按 state_version DESC LIMIT 1）。
       - 若物化点的 ``state_version >= base_state_version`` 且物化点之后有分支 commits
         → 从物化点开始增量重放本分支「state_version > 物化点」的 commits，
         复用 ``apply_delta``。
       - 若物化点的 ``state_version < base_state_version``（理论上不应发生：
         base_state_version 只会增大，create_branch 物化时永远从 base_state_version
         开始；安全兜底走全量重放）→ 走全量重放。
       - 无物化行（旧分支 / 升级前的分支）→ 走全量重放（保持兼容）。
    3) version 序列：base_state_version 起递增，分支内独立编号（与 main 主键空间
       不冲突，因为分支不写 story_states 行）。

    行为兼容：与 Sprint 7 原「base + 全部 commits 重放」语义等价——
    全量重放是物化未命中时的兜底路径，逐字节一致。
    """
    from .deltas import restore_delta_from_row_payload  # 避免循环依赖

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

    # --- fallback base：取 base_state_version 处的 main 快照 ---
    if base_version <= 0:
        fallback_snap = build_initial_state(conn, project_id)
        fallback_snap["state_version"] = base_version
    else:
        base_row = conn.execute(
            "SELECT state_version, snapshot_json FROM story_states "
            "WHERE project_id = ? AND state_version = ?",
            (project_id, base_version),
        ).fetchone()
        if base_row is None:
            fallback_snap = build_initial_state(conn, project_id)
            fallback_snap["state_version"] = base_version
        else:
            fallback_snap = _parse_required_json(base_row["snapshot_json"], {})
            fallback_snap["state_version"] = base_row["state_version"]

    # --- V2.0 Wave B 物化读路径：取最近物化 + 增量重放 ---
    materialized_version, materialized_snap = _read_branch_snapshot(conn, branch_id)

    # 取分支全部 commits（按 resulting_state_version ASC）
    all_rows = conn.execute(
        """
        SELECT c.commit_id, c.resulting_state_version, c.delta_id, d.payload_json
        FROM commits c
        JOIN state_deltas d ON c.delta_id = d.delta_id
        WHERE c.branch_id = ? AND c.project_id = ?
        ORDER BY c.resulting_state_version ASC
        """,
        (branch_id, project_id),
    ).fetchall()

    # 决定起点：物化有效 & 物化点 >= base_version → 从物化点开始增量重放
    if materialized_snap is not None and materialized_version >= base_version:
        # 物化点之后的 commits（resulting_state_version > materialized_version）
        rows = [r for r in all_rows if int(r["resulting_state_version"]) > materialized_version]
        current_state = materialized_snap
        current_state["state_version"] = materialized_version
        current_version = materialized_version
    else:
        # 兜底：全量重放（与 Sprint 7 原行为一致）
        rows = all_rows
        current_state = fallback_snap
        current_version = base_version

    for r in rows:
        payload = _parse_required_json(r["payload_json"], {}) or {}
        delta = restore_delta_from_row_payload(r["delta_id"], payload)
        current_state = apply_delta(current_state, delta)
        current_version = int(r["resulting_state_version"])

    return current_version, current_state


def create_branch(
    service_self,
    project_id: str,
    name: str,
    *,
    base_state_version: int | None = None,
) -> dict:
    """创建分支（state-delta-v0.md §6.3 + V2.0 Wave B 物化）。

    注意：本函数接收 ``service_self``（StoryStateService 实例）以访问
    ``service_self.db_path`` 与 ``service_self.get_snapshot``，与原 god-object
    中方法语义保持一致——get_snapshot 走 façade。

    流程：
    1. 确保 main branch 行存在（``ensure_branch``）。
    2. 同 project 下 name 唯一：重名 → :class:`StateConflictError`（409）。
    3. ``base_state_version`` 缺省 = main 当前最新 version（``story_states``
       全局最大 version；无 story_states 视为 0）。
    4. 插入 ``branches`` 行：``parent_branch_id`` = main.branch_id，``status='ACTIVE'``。
    5. **V2.0 Wave B 物化**：在 base_state_version 处的 main 快照（如存在）落盘到
       ``branch_snapshots``（state_version=base）；base_state_version=0 时落
       ``build_initial_state`` 内容（``materialize_snapshot`` 不写
       story_states——无 main 快照时由 ``build_initial_state`` 兜底）。
       同事务，INSERT OR REPLACE 幂等（重跑 create_branch 不冲突）。

    返回 ``dict`` 包含 ``branch_id`` / ``name`` / ``parent_branch_id`` /
    ``base_state_version`` / ``status`` / ``created_at``。
    """
    if not isinstance(name, str) or not name or name == "main":
        raise StateConflictError(
            f"branch name {name!r} invalid: must be non-empty and != 'main'",
            delta_id=None,
        )
    from packages.core.db import get_connection
    conn = get_connection(service_self.db_path)
    try:
        main_branch_id = ensure_branch(conn, project_id)
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
            v, _ = latest_snapshot_version(conn, project_id)
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
        # V2.0 Wave B 物化：把 base 处的快照内容落到新分支（作为「初始空基线」）。
        # - base > 0 且 main 有该版本快照 → 用 main 快照；
        # - 否则用 build_initial_state 兜底（与 branch_current_state fallback 对齐）。
        if base > 0:
            base_row = conn.execute(
                "SELECT state_version, snapshot_json FROM story_states "
                "WHERE project_id = ? AND state_version = ?",
                (project_id, base),
            ).fetchone()
            if base_row is not None:
                initial_snap = _parse_required_json(base_row["snapshot_json"], {})
                initial_snap["state_version"] = int(base_row["state_version"])
            else:
                initial_snap = build_initial_state(conn, project_id)
                initial_snap["state_version"] = base
        else:
            initial_snap = build_initial_state(conn, project_id)
            initial_snap["state_version"] = base
        materialize_branch_snapshot(
            conn,
            branch_id=branch_id,
            state_version=base,
            snapshot_json=initial_snap,
            created_at=now,
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


def list_branches(service_self, project_id: str) -> list[dict]:
    """列项目下所有分支（main 优先，其余按 created_at ASC）。"""
    from packages.core.db import get_connection
    conn = get_connection(service_self.db_path)
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
    service_self,
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

    from packages.core.db import get_connection
    from packages.core.ids import new_id, now_iso

    conn = get_connection(service_self.db_path)
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
            replay_payload = load_branch_commit_payload(service_self, source_delta_id)
            # 取 main 当前最新 version（每次重放后 +1）
            conn = get_connection(service_self.db_path)
            try:
                current_version, _ = latest_snapshot_version(conn, project_id)
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

            submit_result = service_self.submit_delta(replay_delta)
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
            commit_result = service_self.commit_delta(
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

    # 全部成功 → UPDATE branches.status='MERGED' + V2.0 Wave B 物化 main 新基线
    conn = get_connection(service_self.db_path)
    try:
        conn.execute(
            "UPDATE branches SET status = 'MERGED' WHERE branch_id = ?",
            (branch_id,),
        )
        # V2.0 Wave B 物化：promote 完成后，main 分支持有新 latest version 的快照；
        # 把 main 分行的 (branch_id, state_version=latest, snapshot) 写入 branch_snapshots。
        # 这样 promote 之后从 main 视角读取分支当前状态可走「最新物化 + 0 增量」快路径
        # （但 get_current_state(branch_id=None) 仍走 story_states——主线行为零变化）。
        latest_row = conn.execute(
            "SELECT state_version, snapshot_json FROM story_states "
            "WHERE project_id = ? ORDER BY state_version DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        # 取 main branch_id（promote 完成后 status 已 MERGED，但 main 不受影响）
        main_row = conn.execute(
            "SELECT branch_id FROM branches WHERE project_id = ? AND name = 'main'",
            (project_id,),
        ).fetchone()
        if latest_row is not None and main_row is not None:
            main_snap = _parse_required_json(latest_row["snapshot_json"], {})
            main_snap["state_version"] = int(latest_row["state_version"])
            materialize_branch_snapshot(
                conn,
                branch_id=main_row["branch_id"],
                state_version=int(latest_row["state_version"]),
                snapshot_json=main_snap,
                created_at=now_iso(),
            )
        conn.commit()
    finally:
        conn.close()

    last = replayed[-1]
    # 取 main.branch_id 便于 router / 测试断言
    _conn = get_connection(service_self.db_path)
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


def load_branch_commit_payload(service_self, source_delta_id: str) -> dict:
    """从 state_deltas 行读 ``payload_json``，还原 7 数组用于重放。

    重放路径专用：仅读 payload_json 列（7 数组），不依赖元信息字段；
    元信息（chapter_id / previous_state_version / created_by 等）由
    promote_branch 按 main 当前上下文重写。
    """
    from packages.core.db import get_connection
    conn = get_connection(service_self.db_path)
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


__all__ = [
    "ensure_branch",
    "resolve_branch",
    "branch_current_state",
    "create_branch",
    "list_branches",
    "promote_branch",
    "load_branch_commit_payload",
    "materialize_branch_snapshot",
    "_read_branch_snapshot",
]
