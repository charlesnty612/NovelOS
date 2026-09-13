"""Issue 构造器与 MVP severity 矩阵（packages.core.quality）。

本模块职责：

1. 定义 Issue 的 severity / category 枚举常量（与 ``docs/evaluation/quality-scoring-v0.md``
   §1.3 / §3.7 / §4.x 一一对应；下个 Sprint 不允许再加 category，除非同步更新规格）。
2. 提供轻量构造器 ``make_issue(...)`` 简化调用方代码（自动填充 location/evidence_refs 缺省）。
3. 维护 ``MVP_SEVERITY_MATRIX``：把每个 category 在 MVP 阶段允许的最高严重度固化为可读的常量
   表（与 §4 收窄决策一致；规约变更时只改这一处即可）。
4. V3.9 批次 3.1：维护 ``BLOCKING_RULES``（阻断白名单）与 ``is_blocking_issue``——
   error 级 issue 分 **blocking / informational** 两组，只有 blocking 组把 overall 归零
   （见 :mod:`.aggregate`）；矩阵与白名单内容一并参与 ``scoring_formula_hash``。

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
    "ai_trace",
    "payoff",
    "compliance",
]
"""Issue category 枚举，固定 14 个；与 spec §1.3 / §3.7 / §4.x / ai_trace 对齐。"""

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
# - payoff 范围内仅 H-3 连续 3 章 / H-5 越级碾压可升 error（H-5 越级 MVP 不实现）；
#   V3.9.1 后 payoff 矩阵封顶 warning，故 _payoff_severity() 实际恒为 warning。
# - compliance 内 Q8 默认封顶 warning（V3.9 批次 3.3 裁决，规则级覆盖见 MVP_RULE_OVERRIDES）。
#
# 变更流程（V3.9 批次 3.5）：矩阵 / 规则级覆盖 / 阻断白名单任一变更 = 评分口径变更，
# 必须同步 (1) 本文件，(2) packages/core/quality/README.md §4，(3)
# docs/evaluation/quality-scoring-v0.md §3.7/§4；``scoring_formula_hash`` 会随之变化
# （由 aggregate.formula_hash 自动覆盖），并需跑 tests/unit/quality 回归。
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
    "ai_trace": {"mvp_max": "warning"},
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


# 规则级默认 severity（category 粒度不足以表达「同一 category 内不同规则的默认封顶」）。
# 仅覆盖需要偏离 category 矩阵的规则；未列出的规则用 mvp_max_severity(category)。
MVP_RULE_OVERRIDES: dict[str, str] = {
    # V3.9 批次 3.3 裁决：Q8 默认降为 warning。
    # 本工具是 AI 写作工具，生产 drafts.created_by='writer:v1'（统计修正后）⇒ 纯 AI 章
    # human_ratio=0；若维持 error，enforce 默认会把所有纯 AI 章全拦。作者显式设置
    # NOVELOS_QUALITY_Q8_STRICT=1（见 guardrails.q8_error_severity）才升级回 error 阻断。
    "RULE_Q8_HUMAN_RATIO_LOW": "warning",
}


# 阻断白名单（V3.9 批次 3.1）：只有「severity == 'error' 且 rule_id 在表内」的 issue
# 才把 overall 归零并触发 quality_gate enforce 阻断；其余 error 只进 issues 列表
# （informational error，不压死 overall）。
#
# 入表标准：错误会破坏「提交物本身可用 / 合规」——结构性损坏（schema / 世界状态 /
# 角色状态写坏）、参照书抄袭红线、评分本身失败（子分缺失）。
BLOCKING_RULES: frozenset[str] = frozenset(
    {
        "SCHEMA_VALIDATION_FAILED",  # §4.1 schema_validity：delta 不符合 schema
        "RULE_CHAR_DEAD_ACTIVE",  # §4.3 已死亡角色被写活动字段（状态写坏）
        "RULE_WORLD_HARD_RULE_CHANGED",  # §4.4 hard 世界规则被改写（世界观写坏）
        "RULE_Q6_OVERLAP_RATE",  # §4.6 参照书重叠率超红线（抄袭风险）
        "RULE_Q8_HUMAN_RATIO_LOW",  # §4.8 人工占比红线；默认 warning，NOVELOS_QUALITY_Q8_STRICT=1 时才产 error
        "scoring_missing_subscore",  # §2.1 子分缺失（无法给出有效评分，系统级）
    }
)
"""error 级 issue 中真正阻断的 rule_id 白名单（V3.9 批次 3.1）。"""


def rule_default_severity(rule_id: str, category: str) -> Severity:
    """规则级默认 severity：先查 ``MVP_RULE_OVERRIDES``，未命中回退 category 矩阵。"""
    override = MVP_RULE_OVERRIDES.get(rule_id)
    if override in ("error", "warning", "info"):
        return override  # type: ignore[return-value]
    return mvp_max_severity(category)


def is_blocking_issue(issue: Any) -> bool:
    """判断一条 issue 是否为 blocking（V3.9 批次 3.1）。

    条件：``severity == "error"`` 且 ``rule_id`` 在 :data:`BLOCKING_RULES` 内。
    其余 error（informational）保留在 issues 列表中，但不把 overall 归零。
    """
    if issue is None:
        return False
    return (
        getattr(issue, "severity", None) == "error"
        and getattr(issue, "rule_id", None) in BLOCKING_RULES
    )


def severity_config_fingerprint() -> str:
    """severity 配置的规范化文本指纹（供 ``scoring_formula_hash`` 覆盖矩阵内容）。

    覆盖三块：逐 category 的 ``mvp_max``、规则级覆盖 ``MVP_RULE_OVERRIDES``、
    阻断白名单 ``BLOCKING_RULES``。三者任一变更 ⇒ 指纹变 ⇒ formula_hash 变，
    历史报告可按 hash 区分评分口径。
    """
    parts = [f"{cat}={row.get('mvp_max', '')}" for cat, row in sorted(MVP_SEVERITY_MATRIX.items())]
    parts.extend(f"rule:{rid}={val}" for rid, val in sorted(MVP_RULE_OVERRIDES.items()))
    parts.append("blocking:" + ",".join(sorted(BLOCKING_RULES)))
    return "|".join(parts)


__all__ = [
    "Severity",
    "Category",
    "Issue",
    "loc",
    "make_issue",
    "MVP_SEVERITY_MATRIX",
    "MVP_RULE_OVERRIDES",
    "BLOCKING_RULES",
    "mvp_max_severity",
    "rule_default_severity",
    "is_blocking_issue",
    "severity_config_fingerprint",
]
