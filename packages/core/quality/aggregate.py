"""Overall 聚合公式（§2.1 + ai_trace 增量）。

权威文档 ``docs/evaluation/quality-scoring-v0.md`` §2.1 原口径（六子分加权）：
```
overall = round(0.20*plot + 0.20*character + 0.20*continuity +
                0.15*style + 0.15*pacing + 0.10*foreshadowing)
```

任务书「AI 痕迹（ai_trace）维度」扩展为七子分后，公式改为**七维平均**（任务书拍板；
不保留旧版加权和，原因是 ai_trace 是新增维度，权重重新分配会引入主观偏好，
而平均权重对作者/平台解释成本最低、易对齐验收基线）：

```
overall = round((plot + character + continuity + style + pacing + foreshadowing + ai_trace) / 7)
```

规则：

1. blocking error（``issues.is_blocking_issue``）⇒ overall = 0；
   informational error（severity=="error" 但 rule_id 不在阻断白名单）只进 issues 列表，
   不压死 overall（V3.9 批次 3.1「悬崖聚合改造」）。
2. **confirm** issue（2026-09-18 P0-1：``rule_id ∈ CONFIRM_RULES``，或该条自带
   ``gate=="confirm"`` 的单条上修）⇒ overall **不变**；其后果由 quality_gate 承担
   （必须显式接受才放行），并汇总进 :class:`GateSummary`。
3. 任一子分缺失（None）⇒ push 一条 ``scoring_missing_subscore`` issue，并把 overall 设为 0。
4. warning 与 info 不阻断。

``scoring_formula_hash`` 的输入 = 公式文本 + :func:`issues.severity_config_fingerprint`
（severity 矩阵 / 规则级覆盖 / 阻断白名单 / 确认白名单），矩阵变更即公式变更，
历史报告可按 hash 区分口径。

``compute_overall`` 与 :mod:`.engine` 衔接：它只修改 overall 数值与追加 issues，
不读 QualityReport 实例；``compute_overall_with_gates`` 是同口径 + 后果档摘要的入口。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional

from .issues import (
    Issue,
    is_blocking_issue,
    issue_gate,
    make_issue,
    severity_config_fingerprint,
)

# ============================================================================
# 权重（七子分固定；不得在调用方覆盖）
# ============================================================================

WEIGHTS: dict[str, float] = {
    "plot": 1.0,
    "character": 1.0,
    "continuity": 1.0,
    "style": 1.0,
    "pacing": 1.0,
    "foreshadowing": 1.0,
    "ai_trace": 1.0,
}
"""七子分聚合权重（七维平均：每个 1.0，除以 7）。"""

SUBSCORE_NAMES: tuple[str, ...] = tuple(WEIGHTS.keys())
"""七子分固定顺序。"""


# ============================================================================
# 公式字符串 + 哈希（_meta.scoring_formula_hash 字段）
# ============================================================================


_FORMULA_TEXT: str = (
    "overall = round((plot + character + continuity + style + pacing + "
    "foreshadowing + ai_trace) / 7); "
    "blocking error (severity=='error' AND rule_id IN BLOCKING_RULES) => overall = 0; "
    "informational error => issues only; "
    "confirm gate (rule_id IN CONFIRM_RULES OR the issue itself carries gate='confirm') "
    "=> issues only, overall unchanged "
    "(consequence enforced by quality_gate, not by the score); "
    "missing subscore => overall = 0"
)


def formula_hash() -> str:
    """返回公式字符串 + severity 配置指纹的 sha256 前 16 位 hex。

    V3.9 批次 3.1：severity 矩阵 / 规则级覆盖 / 阻断白名单内容纳入 hash 输入，
    矩阵变更 = 评分口径变更，历史报告可按 hash 区分。
    """
    payload = f"{_FORMULA_TEXT}\n{severity_config_fingerprint()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def formula_text() -> str:
    """返回 overall 公式原文（仅供自检 / 文档化使用）。"""
    return _FORMULA_TEXT


def severity_config_text() -> str:
    """返回 severity 配置指纹原文（自检 / 报告展示用；与 formula_hash 输入一致）。"""
    return severity_config_fingerprint()


# ============================================================================
# 公开 API
# ============================================================================


@dataclass(frozen=True)
class GateSummary:
    """一次聚合里各后果档的 rule_id 汇总（2026-09-18 P0-1）。

    用于让「必须显式接受」与「硬停」两类 issue 在聚合结果里**可见**：
    ``compute_overall`` 只把它们当 issues 列出，不给 overall 加杠杆，因此调用方
    （quality_gate / 前端 / API / 报告 ``_meta``）需要用本摘要判断后果。

    - ``blocking_rule_ids``：``gate == "block"``（overall = 0，硬停）。
    - ``confirm_rule_ids``：``gate == "confirm"``（不归零，但必须显式接受才放行）；
      去重后按字典序排序，便于逐字比较（override 覆盖判定用它）。
    """

    blocking_rule_ids: tuple[str, ...] = ()
    confirm_rule_ids: tuple[str, ...] = ()
    # 原始 issue 对象（按 gate 分组的完整列表，含证据，供门禁消息展示）。
    blocking_issues: tuple[Issue, ...] = field(default=())
    confirm_issues: tuple[Issue, ...] = field(default=())

    @property
    def confirm_count(self) -> int:
        return len(self.confirm_rule_ids)

    @property
    def blocking_count(self) -> int:
        return len(self.blocking_rule_ids)

    def as_meta(self) -> dict[str, Any]:
        """落 ``_meta`` / checkpoint 的 JSON 友好形态（不含 issue 对象本体）。"""
        return {
            "blocking_rule_ids": list(self.blocking_rule_ids),
            "confirm_rule_ids": list(self.confirm_rule_ids),
            "blocking_count": self.blocking_count,
            "confirm_count": self.confirm_count,
        }


def summarize_gates(issues: Optional[list[Issue]]) -> GateSummary:
    """按 :func:`issues.issue_gate` 把 issue 列表分组为 block / confirm 摘要。"""
    blocking: list[Issue] = []
    confirm: list[Issue] = []
    for it in issues or []:
        gate = issue_gate(it)
        if gate == "block":
            blocking.append(it)
        elif gate == "confirm":
            confirm.append(it)
    return GateSummary(
        blocking_rule_ids=tuple(sorted({str(i.rule_id) for i in blocking})),
        confirm_rule_ids=tuple(sorted({str(i.rule_id) for i in confirm})),
        blocking_issues=tuple(blocking),
        confirm_issues=tuple(confirm),
    )


def compute_overall_with_gates(
    subscores: Optional[dict[str, Optional[int]]],
    issues: Optional[list[Issue]] = None,
) -> tuple[int, list[Issue], GateSummary]:
    """:func:`compute_overall` + 后果档摘要（2026-09-18 P0-1）。

    口径与 :func:`compute_overall` 完全一致（confirm **不归零**、block 归零），
    额外返回 :class:`GateSummary` 让「必须显式接受」的那组可见。
    引擎用本函数；只看 overall 的既有调用方继续用 :func:`compute_overall`。
    """
    overall, out = compute_overall(subscores, issues)
    return overall, out, summarize_gates(out)


def compute_overall(
    subscores: Optional[dict[str, Optional[int]]],
    issues: Optional[list[Issue]] = None,
) -> tuple[int, list[Issue]]:
    """计算 overall 并按规则补写缺失子分 / 阻断 issue。

    参数：
        subscores: 七子分 dict；任意子分缺 / None ⇒ push scoring_missing_subscore。
        issues: 调用方维护的 issues 列表；本函数**就地追加** missing subscore issue。

    返回：
        (overall, updated_issues) — updated_issues 与传入为同一对象（便于流水线串联）。

    阻断口径（V3.9 批次 3.1 + 2026-09-18 P0-1）：
        - blocking error（severity=="error" 且 rule_id ∈ ``BLOCKING_RULES``）⇒ overall = 0；
        - **confirm** issue（``issues.issue_gate`` 判为 ``"confirm"``：``rule_id ∈ CONFIRM_RULES``
          或被单条上修）⇒
          overall **不变**（重复类问题不是结构性损坏，不该靠分数表达），但会出现在
          :class:`GateSummary.confirm_issues` 中，由 quality_gate 要求显式接受；
        - informational error（severity=="error" 但 rule_id 不在白名单）⇒ 保留在 issues，
          overall 仍按七维平均给出部分分；
        - missing subscore ⇒ 追加 error issue，overall = 0（系统级阻断）。
    """
    if issues is None:
        issues = []

    # 1) 缺失子分检查（在阻断前做，确保所有 missing subscore 也被记录）
    if not isinstance(subscores, dict):
        subscores = {}
    missing: list[str] = []
    for name in SUBSCORE_NAMES:
        v = subscores.get(name)
        if v is None:
            missing.append(name)

    for name in missing:
        issues.append(
            make_issue(
                severity="error",
                category=name,  # type: ignore[arg-type]
                rule_id="scoring_missing_subscore",
                message=f"{name} 子分缺失",
            )
        )

    # 2) blocking error 阻断（V3.9 批次 3.1：error 分 blocking / informational 两组）
    # blocking 组 = severity=="error" 且 rule_id ∈ BLOCKING_RULES（结构性损坏 / 合规红线 /
    # 评分缺失，见 issues.BLOCKING_RULES 注释）；informational error 仅进 issues 列表，
    # 不把 overall 归零。
    blocked = any(is_blocking_issue(it) for it in issues)

    if blocked or missing:
        return 0, issues

    # 3) 七维平均（任务书拍板；详见 _FORMULA_TEXT 注释）
    total = 0.0
    count = 0
    for name in SUBSCORE_NAMES:
        v = subscores.get(name)
        if not isinstance(v, (int, float)):
            # 防御性：这里理论上已被前面的 missing 处理过滤掉
            return 0, issues
        total += float(v)
        count += 1

    overall = int(round(total / count))
    return max(0, min(100, overall)), issues


__all__ = [
    "compute_overall",
    "compute_overall_with_gates",
    "summarize_gates",
    "GateSummary",
    "WEIGHTS",
    "SUBSCORE_NAMES",
    "formula_hash",
    "formula_text",
    "severity_config_text",
]
