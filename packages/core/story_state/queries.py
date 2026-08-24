"""Story State 查询函数（Sprint 2 + Sprint 7）。

职责（god-object 拆分后）：
- ``get_current_state``——返回当前 Canonical State（main 默认；非 None
  branch_id 走分支推导）。
- ``get_snapshot``——返回指定 version 的快照；不存在返回 None。
- ``list_commits``——列 commits（main 或按 branch_id 过滤）。
- ``list_deltas``——列 state_deltas（按 chapter_id 过滤）。
- ``diff_versions``——两 version 之间结构化 diff；仅支持 main 路径
  （分支视角 diff 不在本接口范围）。

设计要点：
- 拆分后与原 ``service.StoryStateService.{get_current_state,get_snapshot,
  list_commits,list_deltas,diff_versions}`` **逐字节相同**；仅文件位置
  变更，公开行为 0 变化。
- 每个公开方法接收 ``service_self``（StoryStateService 实例）以访问
  ``db_path``。
- ``diff_versions`` 仍走 ``service_self.get_snapshot``（façade 路径，不直连
  DB），与原 god-object 行为一致。
"""

from __future__ import annotations

from packages.core.db import get_connection

from .branches import branch_current_state
from .exceptions import BranchNotFound, StateConflictError, StateNotFoundError
from .snapshot import build_initial_state
from .snapshots import (
    _parse_json,
    _parse_required_json,
    diff_snapshots,
    latest_snapshot_version,
    strip_state_version,
)


def get_current_state(service_self, project_id: str, *, branch_id: str | None = None) -> dict:
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
        conn = get_connection(service_self.db_path)
        try:
            version, snap = latest_snapshot_version(conn, project_id)
        finally:
            conn.close()
        if snap is None:
            conn = get_connection(service_self.db_path)
            try:
                initial = build_initial_state(conn, project_id)
            finally:
                conn.close()
            initial["state_version"] = 0
            return initial
        snap["state_version"] = version
        return snap

    conn = get_connection(service_self.db_path)
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
        version, snap = branch_current_state(conn, project_id, branch_id)
    finally:
        conn.close()
    if snap is None:
        conn = get_connection(service_self.db_path)
        try:
            initial = build_initial_state(conn, project_id)
        finally:
            conn.close()
        initial["state_version"] = version
        return initial
    snap["state_version"] = version
    return snap


def get_snapshot(service_self, project_id: str, version: int) -> dict | None:
    """返回指定 version 的快照；不存在返回 None。"""
    conn = get_connection(service_self.db_path)
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


def list_commits(service_self, project_id: str, *, branch_id: str | None = None) -> list[dict]:
    conn = get_connection(service_self.db_path)
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


def list_deltas(service_self, chapter_id: str) -> list[dict]:
    conn = get_connection(service_self.db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM state_deltas WHERE chapter_id = ? ORDER BY created_at ASC",
            (chapter_id,),
        ).fetchall()
    finally:
        conn.close()
    from .deltas import restore_delta_from_row  # 避免模块顶层循环
    out: list[dict] = []
    for r in rows:
        d = restore_delta_from_row(r)
        d["status"] = r["status"]
        out.append(d)
    return out


def diff_versions(
    service_self,
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
    conn = get_connection(service_self.db_path)
    try:
        snap_a = get_snapshot(service_self, project_id, version_a)
        snap_b = get_snapshot(service_self, project_id, version_b)
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


__all__ = [
    "get_current_state",
    "get_snapshot",
    "list_commits",
    "list_deltas",
    "diff_versions",
]
