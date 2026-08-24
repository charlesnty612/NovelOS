"""What-if Simulation Service（Sprint 10）。

职责：
- 在临时分支上顺序应用一组假设 delta，返回与 main 当前状态的**结构化 diff**，
  随后把分支标记为 ``ARCHIVED``（不写 main 快照、不写领域表、状态可追溯）。

公共方法：
- :meth:`SimulationService.simulate` —— 主入口；返回 :class:`SimulationResult`。
- :meth:`SimulationService.list_simulations` —— 列历史推演（``branches.name LIKE 'sim-%'``）。
- :meth:`SimulationService.get_simulation` —— 重放某次推演的 diff（从分支 commits 推导）。

设计要点：
- 临时分支走 :func:`StoryStateService.create_branch` 创建；``name`` 默认 ``sim-<ts>``，
  ``base_state_version`` 取 main 当前最新 version。
- 每个 delta 走 :func:`StoryStateService.submit_delta` 校验；校验失败 → 整批拒绝（422 语义）、
  分支归档，抛 :class:`SimulationError`。
- 每个 delta 走 :func:`StoryStateService.commit_delta(..., branch_id=temp_branch_id,
  _skip_approval=True)` 提交。``branch_id`` 非 main → S7 ``skip_all=True`` 整体跳过领域表写透；
  ``_skip_approval=True`` 绕过 HIGH 风险审批门（理由见 ``README.md §3``）。
- diff 计算走 :func:`StoryStateService.get_current_state` 两次：base（main）vs branch。
  当前实现复用 ``diff_snapshots``（story_state 公共 helper，对外接口是 ``diff_versions``）；simulation
  直接 import 该 helper 避免给 :class:`StoryStateService` 加公开分支 diff 接口（MVP 简化）。
- 失败语义：
  - 入参 ``deltas`` 为空 → :class:`SimulationError`（reason='empty_deltas'）。
  - 任意 delta 校验失败 → :class:`SimulationError`（reason='validation_failed'，
    ``errors`` 含逐条错误；``applied`` = 0；分支仍归档）。
  - project 不存在 → :class:`SimulationError`（reason='project_not_found'）。
  - 任意 commit 失败（含乐观锁 / APPROVAL 之外的运行时错误）→ :class:`SimulationError`
    （reason='commit_failed'，含已应用步数）。
- 清理：无论如何退出（成功或异常），临时分支 status 都会被设为 ``'ARCHIVED'``。
  不删行，保留 commits 供 ``get_simulation`` 重放。
- Simulation 不做 LLM 推演、不调用 Observer、不生成 narrative；只做状态推演
  （v0 与 docs/PRD设计评审-2026-08-23.md §47/§90 一致）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from packages.core.db import get_connection
from packages.core.ids import now_iso
from packages.core.logging_config import get_logger
from packages.core.story_state.exceptions import (
    ApprovalRequiredError,
    OptimisticLockError,
    StateConflictError,
    StateNotFoundError,
)
from packages.core.story_state.service import (
    StoryStateService,
    diff_snapshots,
    strip_state_version,
)

log = get_logger("novelos.simulation")


# ---------------------------------------------------------------------------
# 异常与结果
# ---------------------------------------------------------------------------


class SimulationError(Exception):
    """What-if Simulation 失败。

    字段：
    - ``reason``: 失败分类（'project_not_found' / 'empty_deltas' / 'validation_failed'
      / 'commit_failed'）。
    - ``applied``: 已成功提交到临时分支的 delta 数（用于失败上下文）。
    - ``issues``: 校验错误列表（reason='validation_failed' 时填充，逐 delta）。
    - ``branch_id``: 临时分支 ID（即便归档，保留引用便于审计）。
    - ``simulation_id``: 推演标识（与 SimulationResult.simulation_id 相同）。
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        applied: int = 0,
        issues: list[dict] | None = None,
        branch_id: str | None = None,
        simulation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.applied = applied
        self.issues = list(issues or [])
        self.branch_id = branch_id
        self.simulation_id = simulation_id


@dataclass
class SimulationResult:
    """What-if Simulation 结果。

    字段：
    - ``simulation_id``: 推演标识（== 临时分支的 branch_id；语义对齐 SPRINT 10 任务书 §SimulationResult）。
    - ``branch_id``: 临时分支 ID（== simulation_id）。
    - ``name``: 分支名（``sim-<ts>`` 或参数指定）。
    - ``base_version``: 推演基线 version（main 当前最新 version；推演结束时不变）。
    - ``base_state``: 推演基线 state JSON（diff 的 a 侧）。
    - ``final_state``: 推演后 state JSON（diff 的 b 侧）。
    - ``applied``: 成功提交的 delta 数。
    - ``diff``: 结构化 diff dict（对齐 ``diff_versions`` 返回形态，去掉 ``version_a/b``）。
    - ``issues``: 校验错误列表（每项 ``{"index": int, "errors": list[str]}``）。
    - ``branch_status``: 推演后分支状态（固定 'ARCHIVED'）。
    - ``created_at``: 推演结束时间（ISO-8601）。
    """

    simulation_id: str
    branch_id: str
    name: str
    base_version: int
    base_state: dict
    final_state: dict
    applied: int
    diff: dict
    issues: list[dict] = field(default_factory=list)
    branch_status: str = "ARCHIVED"
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "simulation_id": self.simulation_id,
            "branch_id": self.branch_id,
            "name": self.name,
            "base_version": self.base_version,
            "applied": self.applied,
            "diff": self.diff,
            "issues": self.issues,
            "branch_status": self.branch_status,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _now_compact_ts() -> str:
    """生成紧凑时间戳用于默认分支名（``sim-YYYYMMDDTHHMMSSZ``）。

    比 ISO-8601 短；按字典序排序即可近似按时间排序。
    """
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ensure_project_exists(conn: sqlite3.Connection, project_id: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
    ).fetchone()
    if row is None:
        raise StateNotFoundError(
            f"project {project_id!r} not found",
            resource="project",
            resource_id=project_id,
        )


def _archive_branch(conn: sqlite3.Connection, branch_id: str) -> None:
    """把临时分支 status 设为 ARCHIVED；不存在则跳过（防御性）。"""
    conn.execute(
        "UPDATE branches SET status = 'ARCHIVED' "
        "WHERE branch_id = ? AND status = 'ACTIVE'",
        (branch_id,),
    )
    conn.commit()


class SimulationService:
    """What-if Simulation 主服务。

    依赖注入 ``db_path`` 与可选 ``StoryStateService``（测试可注入 mock）。
    """

    def __init__(self, db_path, *, state_service: StoryStateService | None = None) -> None:
        self.db_path = str(db_path)
        self._state_service = state_service or StoryStateService(self.db_path)

    # -------------------------------------------------------------- main entry

    def simulate(
        self,
        project_id: str,
        deltas: list[dict],
        *,
        name: str | None = None,
    ) -> SimulationResult:
        """在临时分支上应用 ``deltas``，返回与 main 的结构化 diff，归档分支。

        流程：
        1. 校验 project 存在（404）。
        2. 取 main 当前 state 作为 base。
        3. 创建临时分支（``name`` 缺省 ``sim-<ts>``，base_state_version = main 当前 version）。
        4. 逐个 delta：
           a. ``submit_delta(branch_id=temp_branch_id)``：校验失败 → 整批拒绝，归档，抛 SimulationError。
           b. ``commit_delta(branch_id=temp_branch_id, _skip_approval=True)``：
              分支路径自动 ``skip_all=True``（S7 P0 修复），领域表写透整体跳过。
              HIGH 风险被绕过；审计字段保留。
           c. 失败同样归档 + 抛 SimulationError（含 applied 步数）。
        5. 取分支终态 state（走 :func:`get_current_state`）；与 base 计算结构化 diff。
        6. 归档分支（``status='ARCHIVED'``），返回 SimulationResult。

        入参要求：
        - ``deltas`` 是完整 delta dict 列表（与 ``submit_delta`` 入参一致）；
          每项必含 ``chapter_id``（schema 必填；commit_delta 通过 chapter 反查 project_id）。
          任务书允许 ``chapter_id`` 缺省的口径在 simulation 路径下不可行——
          ``commit_delta`` 通过 ``_project_id_for_chapter(conn, chapter_id)`` 取 project_id；
          缺 ``chapter_id`` → StateNotFoundError。我们保持与 production 一致的行为，
          把 chapter_id 必填的责任交给调用方（任务书 README 口径）。
        """
        if not isinstance(deltas, list) or not deltas:
            raise SimulationError(
                "deltas must be a non-empty list",
                reason="empty_deltas",
            )

        # 1) project 存在性
        conn = get_connection(self.db_path)
        try:
            _ensure_project_exists(conn, project_id)
        finally:
            conn.close()

        # 2) base state（main 当前）
        base_state = self._state_service.get_current_state(project_id)
        base_version = int(base_state.get("state_version") or 0)

        # 3) 创建临时分支
        sim_name = name or f"sim-{_now_compact_ts()}"
        branch: dict
        try:
            branch = self._state_service.create_branch(
                project_id=project_id, name=sim_name, base_state_version=base_version
            )
        except StateConflictError as exc:
            # 同名冲突（极端：用户传入与历史 sim-* 同名）→ 整批拒绝
            raise SimulationError(
                f"failed to create simulation branch {sim_name!r}: {exc}",
                reason="branch_create_failed",
            ) from exc
        branch_id: str = branch["branch_id"]
        simulation_id = branch_id  # 任务书 SimulationResult.simulation_id == branch_id
        created_at = now_iso()
        applied = 0
        issues: list[dict] = []

        # 4) 逐个 delta：submit + commit
        try:
            for idx, raw_delta in enumerate(deltas):
                if not isinstance(raw_delta, dict):
                    issues.append({"index": idx, "errors": ["delta must be a JSON object"]})
                    raise SimulationError(
                        f"delta[{idx}] is not a JSON object",
                        reason="validation_failed",
                        applied=applied,
                        issues=issues,
                        branch_id=branch_id,
                        simulation_id=simulation_id,
                    )
                # 不直接 mutating caller 入参
                delta = dict(raw_delta)
                submit = self._state_service.submit_delta(delta, branch_id=branch_id)
                if submit["status"] != "validated":
                    issues.append({"index": idx, "errors": submit.get("errors") or []})
                    raise SimulationError(
                        f"delta[{idx}] rejected by validator",
                        reason="validation_failed",
                        applied=applied,
                        issues=issues,
                        branch_id=branch_id,
                        simulation_id=simulation_id,
                    )
                # commit：branch 路径自动 skip_all；_skip_approval=True 绕过 HIGH 风险门
                try:
                    self._state_service.commit_delta(
                        submit["delta_id"],
                        # author_approval 不传 approved=True：审计字段如实记录 simulation_bypassed
                        author_approval={
                            "approver": "system:simulation",
                            "notes": f"what-if simulation {simulation_id} step {idx}",
                        },
                        workflow_run_id=f"system:simulation:{simulation_id}",
                        branch_id=branch_id,
                        _skip_approval=True,
                    )
                except (OptimisticLockError, StateConflictError, StateNotFoundError) as exc:
                    raise SimulationError(
                        f"delta[{idx}] commit failed: {exc}",
                        reason="commit_failed",
                        applied=applied,
                        issues=issues,
                        branch_id=branch_id,
                        simulation_id=simulation_id,
                    ) from exc
                except ApprovalRequiredError as exc:
                    # 防御性：_skip_approval=True 不应到这里；如果走到说明 commit_delta 改了逻辑。
                    raise SimulationError(
                        f"delta[{idx}] unexpectedly required approval: {exc}",
                        reason="approval_leak",
                        applied=applied,
                        issues=issues,
                        branch_id=branch_id,
                        simulation_id=simulation_id,
                    ) from exc
                applied += 1
        except Exception:
            # 任何失败：归档分支（防御性，DB 异常时尝试归档可能也失败 → 记录但不阻塞原异常）
            self._safe_archive(branch_id)
            raise

        # 5) 分支终态 + diff
        try:
            final_state = self._state_service.get_current_state(project_id, branch_id=branch_id)
            final_version = int(final_state.get("state_version") or base_version)
            diff = diff_snapshots(
                strip_state_version(base_state),
                strip_state_version(final_state),
                version_a=base_version,
                version_b=final_version,
                branch_id=branch_id,
            )
        except Exception:
            self._safe_archive(branch_id)
            raise

        # 6) 归档分支
        self._safe_archive(branch_id)

        return SimulationResult(
            simulation_id=simulation_id,
            branch_id=branch_id,
            name=sim_name,
            base_version=base_version,
            base_state=base_state,
            final_state=final_state,
            applied=applied,
            diff=diff,
            issues=issues,
            branch_status="ARCHIVED",
            created_at=created_at,
        )

    # -------------------------------------------------------------- list / get

    def list_simulations(self, project_id: str) -> list[dict]:
        """列历史推演（按 created_at DESC）。"""
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT branch_id, name, base_state_version, status, created_at
                FROM branches
                WHERE project_id = ? AND name LIKE 'sim-%'
                ORDER BY created_at DESC
                """,
                (project_id,),
            ).fetchall()
        finally:
            conn.close()
        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "simulation_id": r["branch_id"],
                    "branch_id": r["branch_id"],
                    "name": r["name"],
                    "base_version": int(r["base_state_version"] or 0),
                    "status": r["status"],
                    "created_at": r["created_at"],
                }
            )
        return out

    def get_simulation(self, project_id: str, simulation_id: str) -> SimulationResult | None:
        """重放某次推演：从分支 commits 推导 final_state，返回完整 SimulationResult。

        Sprint 11 审查修：base_state 走 ``branches.base_state_version`` 处的快照（与
        ``simulate`` 当时取的 base 严格一致），而非 main 当前最新——避免 main 在推演后
        又有新 commit 造成 replay diff 漂移；缺失该版本快照时退化为 ``get_current_state``
        与 ``simulate`` 当初行为对齐（main 当前）。找不到对应 branch（不属于该项目或
        非 sim-* 命名）→ 返回 None。
        """
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT branch_id, name, base_state_version, status, created_at
                FROM branches
                WHERE project_id = ? AND branch_id = ? AND name LIKE 'sim-%'
                """,
                (project_id, simulation_id),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None

        base_version = int(row["base_state_version"] or 0)
        # Sprint 11 fix：用 base_state_version 处的快照作为 base（避免 main drift）
        snap_at_base = self._state_service.get_snapshot(project_id, base_version)
        if isinstance(snap_at_base, dict):
            base_state = snap_at_base
        else:
            base_state = self._state_service.get_current_state(project_id)
        final_state = self._state_service.get_current_state(project_id, branch_id=simulation_id)
        final_version = int(final_state.get("state_version") or base_version)
        diff = diff_snapshots(
            strip_state_version(base_state),
            strip_state_version(final_state),
            version_a=base_version,
            version_b=final_version,
            branch_id=simulation_id,
        )
        # applied：从 commits 数推导（archived 分支的 commits 仍在）
        count_conn = get_connection(self.db_path)
        try:
            applied = int(
                count_conn.execute(
                    "SELECT COUNT(*) AS n FROM commits WHERE branch_id = ?",
                    (simulation_id,),
                ).fetchone()["n"]
            )
        finally:
            count_conn.close()

        return SimulationResult(
            simulation_id=simulation_id,
            branch_id=simulation_id,
            name=row["name"],
            base_version=base_version,
            base_state=base_state,
            final_state=final_state,
            applied=applied,
            diff=diff,
            issues=[],
            branch_status=row["status"],
            created_at=row["created_at"],
        )

    # -------------------------------------------------------------- helpers

    def _safe_archive(self, branch_id: str) -> None:
        """归档分支；DB 异常被吞并记录（不影响主流程）。"""
        try:
            conn = get_connection(self.db_path)
            try:
                _archive_branch(conn, branch_id)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("simulation cleanup failed for branch %s: %s", branch_id, exc)


__all__ = ["SimulationError", "SimulationResult", "SimulationService"]
