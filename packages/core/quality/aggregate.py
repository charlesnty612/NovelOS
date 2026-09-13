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
2. 任一子分缺失（None）⇒ push 一条 ``scoring_missing_subscore`` issue，并把 overall 设为 0。
3. warning 与 info 不阻断。

``scoring_formula_hash`` 的输入 = 公式文本 + :func:`issues.severity_config_fingerprint`
（severity 矩阵 / 规则级覆盖 / 阻断白名单），矩阵变更即公式变更，历史报告可按 hash 区分口径。

``compute_overall`` 与 :mod:`.engine` 衔接：它只修改 overall 数值与追加 issues，
不读 QualityReport 实例。
"""

from __future__ import annotations

import hashlib
from typing import Optional

from .issues import Issue, is_blocking_issue, make_issue, severity_config_fingerprint

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
    "informational error => issues only; missing subscore => overall = 0"
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

    阻断口径（V3.9 批次 3.1）：
        - blocking error（severity=="error" 且 rule_id ∈ ``BLOCKING_RULES``）⇒ overall = 0；
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
    "WEIGHTS",
    "SUBSCORE_NAMES",
    "formula_hash",
    "formula_text",
    "severity_config_text",
]
