"""Story State 引擎自定义异常（Sprint 2）。

定义四类业务异常，Service 层抛出，由 router 层映射为合适的 HTTP 状态码：

- :class:`StateConflictError` —— 当前 delta 不在可提交状态（如非 validated）。
  对应 HTTP 409。
- :class:`OptimisticLockError` —— Observer 读到的 ``previous_state_version`` 已过期。
  对应 HTTP 409；提示「State 被其他流程更新，请重新观察」。
- :class:`ApprovalRequiredError` —— delta 含 HIGH 风险 change 但未提供 author_approval.approved。
  对应 HTTP 409。
- :class:`StateNotFoundError` —— 目标不存在（delta / commit / snapshot）。
  对应 HTTP 404。

设计要点：
- 异常层级浅，统一继承 ``StoryStateError`` 基类，便于 router / 上层 ``except StoryStateError``
  统一捕获后再细分子类型。
- 异常对象携带结构化属性（delta_id / commit_id / project_id / 当前 / 期望 version），便于
  router 写 detail 字段，也便于单元测试断言。
"""

from __future__ import annotations


class StoryStateError(Exception):
    """Story State 引擎业务异常基类。"""


class StateConflictError(StoryStateError):
    """Delta 状态机非法（如未通过 validated 就尝试 commit）。"""

    def __init__(self, message: str, *, delta_id: str | None = None) -> None:
        super().__init__(message)
        self.delta_id = delta_id


class OptimisticLockError(StoryStateError):
    """乐观锁失败：delta.previous_state_version 与当前快照不一致。"""

    def __init__(
        self,
        message: str,
        *,
        expected_version: int,
        actual_version: int,
        delta_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.expected_version = expected_version
        self.actual_version = actual_version
        self.delta_id = delta_id


class ApprovalRequiredError(StoryStateError):
    """HIGH 风险 change 缺少 author_approval.approved=True。"""

    def __init__(
        self,
        message: str,
        *,
        high_risk_change_ids: list[str] | None = None,
        delta_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.high_risk_change_ids = list(high_risk_change_ids or [])
        self.delta_id = delta_id


class StateNotFoundError(StoryStateError):
    """目标不存在（delta / commit / snapshot / branch）。"""

    def __init__(self, message: str, *, resource: str | None = None, resource_id: str | None = None) -> None:
        super().__init__(message)
        self.resource = resource
        self.resource_id = resource_id


class BranchNotFound(StateNotFoundError):
    """指定 branch 不存在或不属于该项目（404 语义）。

    继承 :class:`StateNotFoundError`（resource 固定为 ``"branch"``），便于上层
    ``except StateNotFoundError`` 统一捕获后映射到 404。
    """


class BranchClosed(StoryStateError):
    """分支已 MERGED / DISCARDED，状态机拒绝继续写入（409 语义）。

    直接继承 :class:`StoryStateError`（不复用 StateNotFoundError，避免被 router
    误归为 404）。携带 ``status`` / ``branch_id`` 供 router / 测试断言。
    """

    def __init__(
        self,
        message: str,
        *,
        branch_id: str | None = None,
        status: str | None = None,
    ) -> None:
        super().__init__(message)
        self.branch_id = branch_id
        self.status = status


__all__ = [
    "StoryStateError",
    "StateConflictError",
    "OptimisticLockError",
    "ApprovalRequiredError",
    "StateNotFoundError",
    "BranchNotFound",
    "BranchClosed",
]
