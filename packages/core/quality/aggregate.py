"""Overall 聚合公式（§2.1）。

按权威文档 ``docs/evaluation/quality-scoring-v0.md`` §2.1：

```
overall = round(0.20*plot + 0.20*character + 0.20*continuity +
                0.15*style + 0.15*pacing + 0.10*foreshadowing)
```

规则：

1. 任一 error 级 issue（categories 限定：guardrail 五条 + compliance）⇒ overall = 0。
2. 任一子分缺失（None）⇒ push 一条 ``scoring_missing_subscore`` issue，并把 overall 设为 0。
3. warning 与 info 不阻断。

``compute_overall`` 与 :mod:`.engine` 衔接：它只修改 overall 数值与追加 issues，
不读 QualityReport 实例。
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional

from .issues import Issue, make_issue


# ============================================================================
# 权重（§2.1 固定；不得在调用方覆盖）
# ============================================================================

WEIGHTS: dict[str, float] = {
    "plot": 0.20,
    "character": 0.20,
    "continuity": 0.20,
    "style": 0.15,
    "pacing": 0.15,
    "foreshadowing": 0.10,
}
"""六子分聚合权重，与 spec §2.1 字面对齐。"""

SUBSCORE_NAMES: tuple[str, ...] = tuple(WEIGHTS.keys())
"""六子分固定顺序。"""

# 任一这些 category 的 error ⇒ overall = 0
_ERROR_BLOCKING_CATEGORIES: frozenset[str] = frozenset(
    {
        "schema_validity",
        "timeline_consistency",
        "character_contradiction",
        "world_rule_contradiction",
        "knowledge_leakage",
        "compliance",
    }
)


# ============================================================================
# 公式字符串 + 哈希（_meta.scoring_formula_hash 字段）
# ============================================================================


_FORMULA_TEXT: str = (
    "overall = round(0.20*plot + 0.20*character + 0.20*continuity + "
    "0.15*style + 0.15*pacing + 0.10*foreshadowing)"
)


def formula_hash() -> str:
    """返回 §2.1 公式字符串的 sha256 前 16 位 hex。"""
    return hashlib.sha256(_FORMULA_TEXT.encode("utf-8")).hexdigest()[:16]


def formula_text() -> str:
    """返回 §2.1 公式原文（仅供自检 / 文档化使用）。"""
    return _FORMULA_TEXT


# ============================================================================
# 公开 API
# ============================================================================


def compute_overall(
    subscores: Optional[dict[str, Optional[int]]],
    issues: Optional[list[Issue]] = None,
) -> tuple[int, list[Issue]]:
    """计算 overall 并按规则补写缺失子分 / 阻断 issue。

    参数：
        subscores: 六子分 dict；任意子分缺 / None ⇒ push scoring_missing_subscore。
        issues: 调用方维护的 issues 列表；本函数**就地追加** missing subscore issue。

    返回：
        (overall, updated_issues) — updated_issues 与传入为同一对象（便于流水线串联）。
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

    # 2) 任意 error 阻断（来自 guardrail / compliance / subscore-missing 等）
    blocked = any(
        isinstance(it, Issue) and it.severity == "error" for it in issues
    )

    if blocked or missing:
        return 0, issues

    # 3) 加权聚合
    total = 0.0
    for name, w in WEIGHTS.items():
        v = subscores.get(name)
        if not isinstance(v, (int, float)):
            # 防御性：这里理论上已被前面的 missing 处理过滤掉
            return 0, issues
        total += w * float(v)

    overall = int(round(total))
    return max(0, min(100, overall)), issues


__all__ = ["compute_overall", "WEIGHTS", "SUBSCORE_NAMES", "formula_hash", "formula_text"]
