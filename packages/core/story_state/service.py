"""StoryStateService Façade（Sprint 2 + Sprint 7 + Sprint 10）。

本模块是 god-object 拆分后的**重导出层（façade）**：仅暴露 ``StoryStateService``
类，所有方法体委派到 ``.snapshots`` / ``.deltas`` / ``.write_through`` /
``.commits`` / ``.branches`` / ``.queries`` 子模块。

公开行为 0 变化（仅文件位置与代码组织变更）；Sprint 2/4/7/10 的 SQL 语义、
返回结构、异常类型逐字节保持。调用方（simulation / API router / chapter
commit pipeline / context_engine / backup.ids 等）零改动。
"""

from __future__ import annotations

from pathlib import Path

from . import branches, commits, queries
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
from .snapshots import diff_snapshots, strip_state_version
from .validator import validate_delta

# ---------------------------------------------------------------------------
# 私有符号兼容重导出（拆分后保持外部深路径 import 0 改动）
# 历史 / 测试 / 调用方可能直接 ``from packages.core.story_state.service
# import <私有名>`` ——本 façade 保留 alias 以维持零行为变化。
# **新代码请直接 import 委派模块的公开函数**。
# ---------------------------------------------------------------------------
from .write_through import (  # noqa: F401
    encode_who_knows as _encode_who_knows,
    read_who_knows as _read_who_knows,
    read_visibility as _read_visibility,
    write_through as _write_through,
    apply_inverse_cleanup_to_state as _apply_inverse_cleanup_to_state,
)
from .commits import project_id_for_chapter as _project_id_for_chapter  # noqa: F401
from .branches import (  # noqa: F401
    ensure_branch as _ensure_branch,
    resolve_branch as _resolve_branch,
    branch_current_state as _branch_current_state,
    load_branch_commit_payload as _load_branch_commit_payload,
)
from .deltas import (  # noqa: F401
    payload_json_from_delta as _payload_json_from_delta,
    restore_delta_from_row as _restore_delta_from_row,
    restore_delta_from_row_payload as _restore_delta_from_row_payload,
    high_risk_change_ids as _high_risk_change_ids,
    build_inverse_delta as _build_inverse_delta,
)


class StoryStateService:
    """State Delta → Validate → Commit → Rollback 引擎（façade）。"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)

    # -------------------------------------------------------------- 私有方法委派
    # 兼容历史代码 ``svc._<method>(...)`` 实例方法形式调用。
    def _project_id_for_chapter(self, conn, chapter_id: str):
        return commits.project_id_for_chapter(self, conn, chapter_id)

    def _write_through(self, conn, project_id, delta, new_version, *, skip_new_events_hooks=False, skip_all=False):
        from .write_through import write_through
        return write_through(conn, project_id, delta, new_version,
                             skip_new_events_hooks=skip_new_events_hooks, skip_all=skip_all)

    def _apply_inverse_cleanup_to_state(self, state, cleanup):
        from .write_through import apply_inverse_cleanup_to_state
        return apply_inverse_cleanup_to_state(state, cleanup)

    def _build_inverse_delta(self, original, *, chapter_id, workflow_run_id, current_version):
        from .deltas import build_inverse_delta
        return build_inverse_delta(original, chapter_id=chapter_id,
                                   workflow_run_id=workflow_run_id, current_version=current_version)

    # -------------------------------------------------------------- queries
    def get_current_state(self, project_id: str, *, branch_id: str | None = None) -> dict:
        return queries.get_current_state(self, project_id, branch_id=branch_id)

    def get_snapshot(self, project_id: str, version: int) -> dict | None:
        return queries.get_snapshot(self, project_id, version)

    def list_commits(self, project_id: str, *, branch_id: str | None = None) -> list[dict]:
        return queries.list_commits(self, project_id, branch_id=branch_id)

    def list_deltas(self, chapter_id: str) -> list[dict]:
        return queries.list_deltas(self, chapter_id)

    def diff_versions(self, project_id, version_a, version_b, *, branch_id=None) -> dict:
        return queries.diff_versions(self, project_id, version_a, version_b, branch_id=branch_id)

    # -------------------------------------------------------------- commits
    def init_genesis(self, project_id: str, chapter_id: str) -> dict:
        return commits.init_genesis(self, project_id, chapter_id)

    def submit_delta(self, delta: dict, *, branch_id: str | None = None) -> dict:
        return commits.submit_delta(self, delta, branch_id=branch_id)

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
        return commits.commit_delta(
            self, delta_id, author_approval, workflow_run_id,
            branch_id=branch_id, _inverse_cleanup=_inverse_cleanup,
            _rollback_of=_rollback_of, _skip_approval=_skip_approval,
        )

    def rollback_commit(self, commit_id: str, author_approval: dict, *, branch_id: str | None = None) -> dict:
        return commits.rollback_commit(self, commit_id, author_approval, branch_id=branch_id)

    # -------------------------------------------------------------- branches
    def create_branch(self, project_id, name, *, base_state_version: int | None = None) -> dict:
        return branches.create_branch(self, project_id, name, base_state_version=base_state_version)

    def list_branches(self, project_id: str) -> list[dict]:
        return branches.list_branches(self, project_id)

    def promote_branch(self, project_id: str, branch_id: str, *, chapter_id: str | None = None) -> dict:
        return branches.promote_branch(self, project_id, branch_id, chapter_id=chapter_id)


__all__ = ["StoryStateService"]
