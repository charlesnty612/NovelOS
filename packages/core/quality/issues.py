"""Issue 构造器与 MVP severity 矩阵（packages.core.quality）。

本模块职责：

1. 定义 Issue 的 severity / category 枚举常量（与 ``docs/evaluation/quality-scoring-v0.md``
   §1.3 / §3.7 / §4.x 一一对应；下个 Sprint 不允许再加 category，除非同步更新规格）。
2. 提供轻量构造器 ``make_issue(...)`` 简化调用方代码（自动填充 location/evidence_refs 缺省）。
3. 维护 ``MVP_SEVERITY_MATRIX``：把每个 category 在 MVP 阶段允许的最高严重度固化为可读的常量
   表（与 §4 收窄决策一致；规约变更时只改这一处即可）。

设计要点：

- 本模块不引入 pydantic 依赖，Issue dataclass 仅含字段，避免循环 import。
  ``models.Issue``（pydantic BaseModel）与之字段兼容，可直接互相赋值。
- 不要把规则判定逻辑放进本文件。本文件只定义"形状"与"可选矩阵"。
"""

from __future__ import annotations

from typing import Any, Iterable, Literal, Optional

# ----------------------------------------------------------------------------- enum types


Severity = Literal["error", "warning", "info"]
"""Issue severity 三档，对齐 §1.3 与 PRD §89 风险等级。"""

Category = Literal[
    "schema_validity",
    "timeline_consistency",
    "character_contradiction",
    "world_rule_contradiction",
    "knowledge_leakage",
    "plot",
    "character",
    "continuity",
    "style",
    "pacing",
    "foreshadowing",
    "payoff",
    "compliance",
]
"""Issue category 枚举，固定 13 个；与 spec §1.3 / §3.7 / §4.x 对齐。"""

# ----------------------------------------------------------------------------- location helpers


def loc(chapter_id: Optional[str], scene_id: Optional[str] = None) -> str:
    """构造 Issue.location。

    规则（对齐 §1.3）：
    - 既有 chapter_id 又 scene_id → ``"<chapter_id>:<scene_id>"``
    - 仅 chapter_id → ``"<chapter_id>"``
    - 均缺 → ``"<unknown>"``
    """
    if chapter_id and scene_id:
        return f"{chapter_id}:{scene_id}"
    if chapter_id:
        return str(chapter_id)
    return "<unknown>"


# ----------------------------------------------------------------------------- issue class


# 唯一 Issue 类型从 models 导入（pydantic BaseModel）；这里再次导出 ``Issue`` 别名
# 是为了让 ``guardrails / scoring / payoff`` 内部 ``from .issues import Issue``
# 与 pydantic 模型互通，避免循环构造。
from .models import Issue as Issue  # noqa: E402,F401


# ----------------------------------------------------------------------------- factory


def make_issue(
    *,
    severity: Severity,
    category: Category,
    rule_id: str,
    message: str,
    chapter_id: Optional[str] = None,
    scene_id: Optional[str] = None,
    location: Optional[str] = None,
    suggestion: Optional[str] = None,
    evidence_refs: Optional[Iterable[str]] = None,
    judge_trace: Optional[dict[str, Any]] = None,
) -> Issue:
    """构造 Issue 对象的便捷助手。

    参数：
        severity / category / rule_id / message：必填。
        chapter_id / scene_id / location：三选一/组合；``location`` 显式给出时优先级最高。
        evidence_refs：允许直接传可迭代对象，内部统一转 list。
    """
    resolved_loc = location if location is not None else loc(chapter_id, scene_id)
    ev_list: Optional[list[str]] = None
    if evidence_refs is not None:
        ev_list = list(evidence_refs)
    return Issue(
        severity=severity,
        category=category,
        rule_id=rule_id,
        message=message,
        location=resolved_loc,
        suggestion=suggestion,
        evidence_refs=ev_list,
        judge_trace=judge_trace,
    )


# ----------------------------------------------------------------------------- severity matrix


# MVP severity 矩阵（docs/evaluation/quality-scoring-v0.md §4 收窄决策）：
# - schema_validity / character_contradiction / world_rule_contradiction / compliance → "error"
# - timeline_consistency / knowledge_leakage                              → "warning"
# - 其余子分 / payoff                                                     → "warning"
# - payoff 范围内仅 H-3 连续 3 章 / H-5 越级碾压 升 error（H-5 越级 MVP 不实现）。
MVP_SEVERITY_MATRIX: dict[str, dict[str, str]] = {
    "schema_validity": {"mvp_max": "error"},
    "character_contradiction": {"mvp_max": "error"},
    "world_rule_contradiction": {"mvp_max": "error"},
    "compliance": {"mvp_max": "error"},
    "timeline_consistency": {"mvp_max": "warning"},
    "knowledge_leakage": {"mvp_max": "warning"},
    "plot": {"mvp_max": "warning"},
    "character": {"mvp_max": "warning"},
    "continuity": {"mvp_max": "warning"},
    "style": {"mvp_max": "warning"},
    "pacing": {"mvp_max": "warning"},
    "foreshadowing": {"mvp_max": "warning"},
    "payoff": {"mvp_max": "warning"},
}


def mvp_max_severity(category: str) -> Severity:
    """读取某 category 在 MVP 阶段允许的最高 severity（不存在时退回 ``"warning"``）。"""
    row = MVP_SEVERITY_MATRIX.get(category)
    if not row:
        return "warning"
    val = row.get("mvp_max", "warning")
    if val in ("error", "warning", "info"):
        return val  # type: ignore[return-value]
    return "warning"


__all__ = [
    "Severity",
    "Category",
    "Issue",
    "loc",
    "make_issue",
    "MVP_SEVERITY_MATRIX",
    "mvp_max_severity",
]
