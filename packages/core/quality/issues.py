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
5. 2026-09-18（P0-1）：在 severity 之外引入**正交的后果轴** :data:`Gate`，配 :data:`CONFIRM_RULES`
   与 :func:`issue_gate`，表达「多严重」之外的「必须怎么处理」（见下方注释块）。

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

Gate = Literal["auto", "confirm", "block"]
"""Issue **后果**三档（2026-09-18 P0-1；与 severity 正交，不互相推导）。

- ``auto``：不产生后果，静默通过（warning / info / informational error 的既有行为）。
- ``confirm``：**不允许静默通过**——调用方必须给出显式、留痕的接受声明
  （``quality_gate`` 的 ``ctx["gate_override"]``，须覆盖本次全部 confirm rule_id 且附非空
  ``reason``）才放行；否则 ``enforce`` 模式阻断。
- ``block``：硬停，不可覆盖（既有 ``BLOCKING_RULES`` 行为）。

为什么与 severity 正交：severity 回答「哪里出问题、多严重」，gate 回答「谁能说通过」。
P0-1 事故（2026-09-18）——某章 9 段逐字重复仍提交成功、``overall: 90``——根因不是
重复算不上严重，而是**没有任何一处有权限说「不许静默通过」**：style/pacing/payoff 等
category 被矩阵封顶 ``warning``，于是重复类问题在构造上永远进不了阻断白名单。
"""

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
    gate: Optional[Gate] = None,
) -> Issue:
    """构造 Issue 对象的便捷助手。

    参数：
        severity / category / rule_id / message：必填。
        chapter_id / scene_id / location：三选一/组合；``location`` 显式给出时优先级最高。
        evidence_refs：允许直接传可迭代对象，内部统一转 list。
        gate：显式后果档；None ⇒ 按 :data:`CONFIRM_RULES` / 阻断白名单材料化。
            只允许**上修**（把规则表未覆盖的单条 issue 显式升为 ``confirm``）——
            构造末尾统一过 :func:`issue_gate`，因此显式 ``gate="auto"`` 无法把表内规则降级。
    """
    resolved_loc = location if location is not None else loc(chapter_id, scene_id)
    ev_list: Optional[list[str]] = None
    if evidence_refs is not None:
        ev_list = list(evidence_refs)
    obj = Issue(
        severity=severity,
        category=category,
        rule_id=rule_id,
        message=message,
        location=resolved_loc,
        suggestion=suggestion,
        evidence_refs=ev_list,
        judge_trace=judge_trace,
        gate=gate or "auto",
    )
    # 材料化：让落库 / 序列化出来的 ``gate`` 字段与解析器同口径（见 :func:`issue_gate`）。
    obj.gate = issue_gate(obj)
    return obj


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
    # 本工具是 AI 写作工具，生产 drafts.created_by 形如 ``writer:<prompt 版本>``
    # （2026-09-16 起取真值，见 quality/service.py 顶部口径注释；统计修正后）⇒ 纯 AI 章
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


# 确认白名单（2026-09-18 P0-1）：命中即 ``gate == "confirm"``——不允许静默通过，
# 必须由调用方给出显式、留痕的接受声明（见 :func:`issue_gate` 与
# ``packages/workflows/chapter_commit/gate.py`` 的 ``gate_override``）。
#
# 入表标准（严格）：**确定性、可复算、作者无从辩驳**的重复类算子。
#   - 命中可由原文重新算一遍复现（不需要 LLM judge、不依赖主观判断）；
#   - 「我认了这个结果」是唯一合理回应，因此正确的处理是要求显式接受，而不是调参。
#   - **表内条目必须要么「命中即错」，要么自带分布无关的强分离**；量级依赖分布的
#     算子不得靠 rule_id 入表——静态 rule_id 只能表达「这条规则命中就要签字」，
#     表达不了「同一条规则、两种量级、两种后果」（见下条留痕）。
#
# 明确**不收**的：任何比值 / 占比类算子（对白占比、段落长度、可读性频率 …）。
# AGENTS.md 记录的 F-19 实证：「占比/频率」一旦进驱动或硬指标，模型为凑指标会灌对白 /
# 拆短句，越改越坏；可测算子只作体检、不作处方。同理**不把任何既有 category 升 error**——
# gate 与 severity 正交，正是为了不靠改 severity 来造后果（见 :data:`Gate`）。
#
# ---------------------------------------------------------------------------
# 2026-09-18 撤销记录（先验判断被推翻，留痕）：
# ``RULE_STYLE_REPETITION_TRIGRAM`` 曾按「一次观测到的 30.37% 事故章」入表。次日实测
# 推翻该判断——章内 trigram 重复率 0.08 落在**正常分布内部**：人类锚点书（榜一侯府弧
# 19 章）mean 0.0754 / max 0.0949（9/19 章超 0.08），生成侧 92 章 p50 0.1333 / p90 0.1749
# （仅 2/92 章低于 0.08）。按 0.08 做 confirm ⇒ 人写的稿子也要签字，门禁退化为橡皮图章。
# 现改为：该规则两档（warn 0.16 / confirm 0.25，均为实测分位，见
# ``scoring.STYLE_TRIGRAM_*``），**只有超过 confirm 阈值的那一条**在
# ``scoring.score_style`` 里显式上修为 ``gate="confirm"``（:func:`issue_gate` 第 3 条
# 路径），不再依赖本表。规律：**量级依赖的后果不要写进 rule_id 静态表**。
# ---------------------------------------------------------------------------
CONFIRM_RULES: frozenset[str] = frozenset(
    {
        # 节拍/段落级逐字复现（``packages/core/quality/beat_repeat.py`` 产出）。
        # 入表依据（2026-09-18 实测，非单章先验）：生成侧 307 处 / 1.17 处每千字、
        # 64/92 章达标；人类侧 2 处 / 0.05 处每千字、**0/19 章**达标——章级门
        # 「≥2 处且 >1.0/千字」在人类语料上零误报，分离度与分布无关。
        "AI-BEAT-REPEAT",
    }
)
"""必须显式接受才放行的 rule_id 白名单（``gate == "confirm"``，2026-09-18 P0-1）。

共 1 条（2026-09-18：``RULE_STYLE_REPETITION_TRIGRAM`` 因阈值未被分布支撑而撤出，
改走 ``score_style`` 的单条上修路径——见上方撤销记录）。"""


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


def issue_gate(issue: Any) -> Gate:
    """解析一条 issue 的**后果档**（2026-09-18 P0-1）——本函数是唯一权威。

    判定顺序（先到先得；不可由 issue 自身字段降级）：

    1. :func:`is_blocking_issue` ⇒ ``"block"``（硬停，不可覆盖）；
    2. ``rule_id ∈ CONFIRM_RULES`` ⇒ ``"confirm"``（与 severity 无关——warning 级同样
       可以是 "不许静默通过"；表内规则见 :data:`CONFIRM_RULES` 入表标准）；
    3. issue 自带 ``gate == "confirm"`` ⇒ ``"confirm"``（单条显式上修）——**量级依赖的
       规则走这条**：同一 rule_id 在正常量级只出 warning、在异常量级才要求签字，
       静态表表达不了这种两档（先例：``scoring.score_style`` 的 trigram 两档）；
    4. 其余 ⇒ ``"auto"``。

    与 ``Issue.gate`` 字段的关系：字段是**材料化回显**（``make_issue`` / ``QualityEngine``
    写入，落 ``quality_reports`` 供前端与 API 直接读），本函数是**权威**。字段只能上修
    （单条升 ``confirm``），不能把表内规则降回 ``auto``；``"block"`` 一律实时由
    ``severity + BLOCKING_RULES`` 推出，不接受字段指定。
    """
    if issue is None:
        return "auto"
    if is_blocking_issue(issue):
        return "block"
    if getattr(issue, "rule_id", None) in CONFIRM_RULES:
        return "confirm"
    if getattr(issue, "gate", None) == "confirm":
        return "confirm"
    return "auto"


def severity_config_fingerprint() -> str:
    """severity 配置的规范化文本指纹（供 ``scoring_formula_hash`` 覆盖矩阵内容）。

    覆盖四块：逐 category 的 ``mvp_max``、规则级覆盖 ``MVP_RULE_OVERRIDES``、
    阻断白名单 ``BLOCKING_RULES``、确认白名单 ``CONFIRM_RULES``。任一项变更 ⇒ 指纹变
    ⇒ formula_hash 变，历史报告可按 hash 区分评分口径。
    """
    parts = [f"{cat}={row.get('mvp_max', '')}" for cat, row in sorted(MVP_SEVERITY_MATRIX.items())]
    parts.extend(f"rule:{rid}={val}" for rid, val in sorted(MVP_RULE_OVERRIDES.items()))
    parts.append("blocking:" + ",".join(sorted(BLOCKING_RULES)))
    parts.append("confirm:" + ",".join(sorted(CONFIRM_RULES)))
    return "|".join(parts)


__all__ = [
    "Severity",
    "Category",
    "Gate",
    "Issue",
    "loc",
    "make_issue",
    "MVP_SEVERITY_MATRIX",
    "MVP_RULE_OVERRIDES",
    "BLOCKING_RULES",
    "CONFIRM_RULES",
    "mvp_max_severity",
    "rule_default_severity",
    "is_blocking_issue",
    "issue_gate",
    "severity_config_fingerprint",
]
