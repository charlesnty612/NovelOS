"""What-if Simulation 包（Sprint 10）。

公共 API：
- :class:`SimulationService` —— 在临时分支上提交假设 delta、返回与 main 的 diff、归档分支。
- :class:`SimulationError` —— 推演失败领域异常（语义对齐
  :class:`packages.core.story_state.exceptions.StoryStateError`）。
- :class:`SimulationResult` —— 推演结果 dataclass
  （simulation_id / branch_id / base_version / applied / diff / issues）。

API 路由：``packages/core/api/routers/simulation.py``（自动发现挂载到 ``/api``）。

设计要点：
- Simulation 不污染 main 快照与领域表（S7 ``skip_all=True`` 保证）；全程在临时分支上跑。
- HIGH 风险 change 在 Simulation 路径下走 ``_skip_approval=True`` 绕过审批门（见 README §3）。
- 分支最终标记 ``ARCHIVED``，便于审计 / 历史重放，不影响主线。
- 本模块是纯执行者；架构决策（与分支关系 / 审批绕过理由 / 清理策略）见 ``README.md``。
"""

from __future__ import annotations

from .service import SimulationError, SimulationResult, SimulationService

__all__ = ["SimulationError", "SimulationResult", "SimulationService"]
