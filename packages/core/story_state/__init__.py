"""Story State 引擎包（Sprint 2）。

公共 API：
- :class:`StoryStateService` —— State Delta → Validate → Commit → Rollback 的统一入口。
- :func:`validate_delta` —— 纯 schema + 业务校验函数。
- :func:`apply_delta` —— 纯函数 apply（不读不写 DB）。
- :func:`build_initial_state`, :func:`materialize_snapshot` —— 快照构建与落库。
- 异常：:class:`StoryStateError` / :class:`StateConflictError` /
  :class:`OptimisticLockError` / :class:`ApprovalRequiredError` /
  :class:`StateNotFoundError`。

设计原则：
- 所有变更必须经 ``submit_delta → commit_delta`` 链路；不允许任何路径直接改 story_states。
- 状态机（state-delta-v0.md §4）：
  proposed → validated → applied（终态）；
  rejected / superseded 终态。
- Commit 链路所有 DB 写入（领域表 + story_states + commits）必须同一事务，任一失败整体回滚。
- Rollback 不修改原 commit，由「存在 rollback_of 指向它的 commit」表达（commits 不可变）。
"""

from __future__ import annotations

from .applier import apply_delta
from .exceptions import (
    ApprovalRequiredError,
    OptimisticLockError,
    StateConflictError,
    StateNotFoundError,
    StoryStateError,
)
from .service import StoryStateService
from .snapshot import build_initial_state, materialize_snapshot
from .validator import validate_delta

__all__ = [
    "StoryStateService",
    "apply_delta",
    "validate_delta",
    "build_initial_state",
    "materialize_snapshot",
    "StoryStateError",
    "StateConflictError",
    "OptimisticLockError",
    "ApprovalRequiredError",
    "StateNotFoundError",
]
