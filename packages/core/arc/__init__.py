"""叙事弧光聚合视图（V3.2 P2-2）。

对外接口：

- :func:`build_arc_view` —— 纯函数，``(db_path, project_id) -> dict``；
  串起 chapters/quality_reports/drafts/hooks/narrative_debts 五类数据，
  产出一屏汇总「弧线是否健康」的 JSON 视图（含 alerts 告警）。

设计要点：

- 纯启发式规则、无 LLM、无新依赖；
- DB 取数集中在 :mod:`.service`，业务判定（payoff/charge 解析、streak 计数、告警）
  抽成纯函数，便于单元测试覆盖；
- 模块常量集中放顶部（``_CHARGE_STREAK_FAIL``、``_PAYOFF_DENSITY_*`` 等），便于后续微调。
"""

from __future__ import annotations

from .service import build_arc_view

__all__ = ["build_arc_view"]
