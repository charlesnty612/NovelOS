"""Quality Engine 纯核心包（packages.core.quality）。

**职责**：

Sprint 6 的 Quality 子模块，按 ``docs/evaluation/quality-scoring-v0.md`` 落地纯函数式评估能力。
本包**不接 DB、不接 pipeline**：integration 由 Sprint 6 下一任务在本包之上接入。

**公开 API**：

- :class:`QualityEngine` — ``evaluate(ctx) -> QualityReport`` 编排入口。
- :class:`QualityContext` — 评估输入（含 draft / plan / snapshot_pre / delta 等）。
- :class:`QualityReport` — 评估输出（八字段 + ``_meta`` + 回显字段）。
- :class:`Issue` — 单条问题（与 spec §1.3 一一对应）。
- :func:`compute_overall` — §2.1 加权聚合 + 缺失子分 / error 阻断。
- 常量 :data:`MVP_SEVERITY_MATRIX` — MVP 阶段允许的最高 severity 表。

子模块：

- :mod:`.guardrails` — 8 条 Guardrail（schema / 5 业务连续性 / REQ-Q6/Q7/Q8）。
- :mod:`.scoring` — 六子分 rule-based 部分。
- :mod:`.aggregate` — 聚合公式与 hash。
- :mod:`.payoff` — 爽感维度 H-1~H-5。
- :mod:`.issues` — Issue 构造器与 severity 矩阵常量。
- :mod:`.models` — pydantic 数据模型。

**MVP 收窄与 defer 清单**（README 同步说明）：

1. LLM judge 全部 deferred；六子分仅实现 rule-based 并归一到 [0, 100]。
2. REQ-Q6 仅实现 13 字滑动 shingle 双轨，embedding 双轨 defer 到 V1。
3. §3.5 pacing 使用 MVP 代理实现（5 段对话密度极差 + 末段钩子），无 Scene 张力曲线标注。
4. §4.5 knowledge_leakage 仅对显式声明 who_knows 的 event/hook 做严格校验；
   未声明一律 pass。
5. H-5 越级碾压检测不实现；仅境界名词一致性 warning。
6. §2.1 权重与所有阈值/常数均为"建议值待校准"。

**维护注意点**：

- LLM judge 接入时必须保持 rule-based + LLM 双轨；并把 judge model_versions 写入
  ``QualityReport.meta``。
- 修改 §2.1 公式必须同步更新 ``aggregate.formula_text``（hash 自动重算）。
- 修改 Guardrail severity 时同步更新 :data:`.issues.MVP_SEVERITY_MATRIX`。
- 与 Story State service 集成时通过复用 ``packages.core.story_state.validator.validate_delta``
  （已在 :mod:`.guardrails` 内调用）；不要在 engine 内直接读 DB。
"""

from __future__ import annotations

# 暴露常用 guardrails / scorings 便于测试 / 集成
from . import guardrails, scoring
from .aggregate import (
    SUBSCORE_NAMES,
    WEIGHTS,
    compute_overall,
    formula_hash,
    formula_text,
)
from .engine import QualityEngine
from .issues import MVP_SEVERITY_MATRIX, Category, Issue, Severity, loc, make_issue, mvp_max_severity
from .models import Issue as IssueModel
from .models import QualityContext, QualityReport
from .payoff import PayoffContext
from .payoff import evaluate as payoff_evaluate

__all__ = [
    # 类与工厂
    "QualityEngine",
    "QualityContext",
    "QualityReport",
    "Issue",
    "IssueModel",
    "PayoffContext",
    "make_issue",
    "loc",
    "mvp_max_severity",
    # 聚合
    "compute_overall",
    "WEIGHTS",
    "SUBSCORE_NAMES",
    "formula_hash",
    "formula_text",
    # payoff
    "payoff_evaluate",
    # 模块
    "guardrails",
    "scoring",
    # 常量
    "MVP_SEVERITY_MATRIX",
    "Severity",
    "Category",
]
