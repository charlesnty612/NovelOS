"""评审固定分类法（``rule_id`` 注册表）与连续性规则 id 的**稳定名册**。

为什么要有这个模块
------------------
1. **critic 的 ``category`` 枚举太粗**：现有 6 值（``pacing / character / logic /
   foreshadowing / ai_flavor / other``）里，「凌晨两点十七分 → 天亮」「引用了一件
   全书没发生过的事」「原告席出现在刑诉里」三类缺陷**都得报 logic 或 other**，
   作者与下游看不出差别。故在 category 之外增加一条**正交**的 ``rule_id`` 轴，
   取值取自本模块 :data:`CRITIC_TAXONOMY`（**闭集**）。
2. **确定性算子与 LLM 评审必须共用一套 id**：``CONT-CLOCK-DAYBREAK`` /
   ``CONT-TIME-BACKSTEP`` 由 :mod:`packages.core.quality.continuity_time` 产出，
   经 ``deterministic_hints`` 进 critic payload；critic 需要把它们与自己的判断
   对齐到同一个 ``CONT-TIMELINE``。两处各写一份 id 必然漂移，故集中在此。
3. **不新增 category 的原因（有意为之）**：category 的闭集由
   ``packages/core/agent_runtime/structured_output._validate_critic`` 校验，
   给它加值等于改契约校验器（非本模块职责）。故本表**只映射到现有 6 个 category**，
   新增的子类信息全部承载在 ``rule_id`` 上。

后果档（必须是 ``auto``）
------------------------
本表里所有 ``CONT-*`` id 都由 **LLM 产出**（``CONT-CLOCK-DAYBREAK`` /
``CONT-TIME-BACKSTEP`` 例外，它们是确定性算子）。LLM 结论**非确定性、不可复算**，
因此：

- 一律 ``gate == "auto"``（可见、不阻断）；
- **不得**进 ``packages.core.quality.issues.CONFIRM_RULES``（那是「确定性、可复算、
  作者无从辩驳」的白名单）或 ``BLOCKING_RULES``；
- **不得**用作自动改稿 / 自动驳回的触发器（改稿入口只认确定性的 ``W-LEN`` 类与
  人工 ``revise`` 决议）。

下游接线现状（照实说）
----------------------
``critic_report`` 整体经 ``chapter_review`` 的 pause payload 输出，故 ``rule_id`` /
``reason`` **已经**随报告上浮到评审 UI 的数据里（前端须加可选字段才渲染，属
``apps/web`` 的改动）。真正**未接线**的是「把连续性 findings 提成报告顶层的结构化
出口」——接线点在 :func:`continuity_findings` 的 docstring 里写明了确切位置与改动。
"""

from __future__ import annotations

from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 分类法（闭集）：rule_id → 契约校验器认可的 category
# ---------------------------------------------------------------------------

# 连续性五类（本批新增）+ 既有五类的显式 id（让每条 issue 都有 rule_id 可填）。
CRITIC_TAXONOMY: dict[str, str] = {
    # --- 连续性（CONT-*）：本批新增，覆盖「正文自身前后不一致」这一类 ---
    "CONT-TIMELINE": "logic",  # 时间/时点矛盾（凌晨两点十七分 → 天亮；时点回退）
    "CONT-UNANCHORED-REF": "logic",  # 指向本书不存在的事件/事实的引用
    "CONT-REGISTER": "other",  # 与设定时代/语域不符（称谓、名物、制度、器物）
    "CONT-PROCEDURE": "other",  # 现实流程错误（诉讼、医疗、警务等程序）
    "CONT-BEAT-DEVIATION": "other",  # 计划要求的关键节拍未兑现/偏离
    # --- 既有维度：保持原语义，只是补一个稳定 id ---
    "AI-FLAVOR": "ai_flavor",
    "PACE": "pacing",
    "CHARACTER": "character",
    "LOGIC": "logic",
    "FORESHADOW": "foreshadowing",
    "OTHER": "other",
}

CONTINUITY_RULE_IDS: frozenset[str] = frozenset(
    rid for rid in CRITIC_TAXONOMY if rid.startswith("CONT-")
)
"""critic 侧连续性分类法（LLM 产出）的 id 集合。"""

DETERMINISTIC_CONTINUITY_RULE_IDS: frozenset[str] = frozenset(
    {
        "CONT-CLOCK-DAYBREAK",  # continuity_time：深夜时钟 ↔ 天亮标记
        "CONT-TIME-BACKSTEP",  # continuity_time：同章段首时点回退
    }
)
"""确定性连续性算子（:mod:`packages.core.quality.continuity_time` 产出）的 id 集合。

它们**不在** :data:`CRITIC_TAXONOMY` 里——那份表是 critic 要填的闭集，
而这两个 id 由确定性算子产出、经 ``deterministic_hints`` 只读传入 critic。
critic 引用它们时应把问题归类为 ``CONT-TIMELINE``。
"""

ALL_CONTINUITY_RULE_IDS: frozenset[str] = (
    CONTINUITY_RULE_IDS | DETERMINISTIC_CONTINUITY_RULE_IDS
)
"""连续性命名空间（``CONT-``）全量 id：确定性 + LLM 两侧，供测试与下游共用。"""


def is_continuity_rule_id(rule_id: Any) -> bool:
    """``rule_id`` 是否属于连续性命名空间（``CONT-`` 前缀，两侧全量）。"""
    return isinstance(rule_id, str) and rule_id in ALL_CONTINUITY_RULE_IDS


def taxonomy_category(rule_id: Any) -> str | None:
    """取该 ``rule_id`` 必须配套的 category；不在闭集内返回 ``None``。"""
    return CRITIC_TAXONOMY.get(rule_id) if isinstance(rule_id, str) else None


def continuity_findings(issues: Iterable[Any] | None) -> list[dict[str, Any]]:
    """把 critic 报告里**连续性类**的 issue 提成结构化清单（供报告顶层输出）。

    **当前没有生产调用方**——接线点属 ``packages/workflows/chapter_review/pipeline.py``
    （非本批所有权范围），需要的改动是精确的一处：在 ``_critic_review_node`` 完成
    「quote 可溯源」与「已采纳建议去重」两道过滤**之后**、返回 ``critic_report`` 之前，
    加一行

    ``out["continuity_findings"] = continuity_findings(out.get("issues"))``

    收益：``pause_payload.critic_report.continuity_findings`` 成为稳定结构（前端与
    观测脚本不必从 issues 里筛 rule_id）；且**两道过滤之后**提取，天然只含可溯源条目。
    **不要**把 ``quote`` 不可溯源的条目提进来——那正是现有过滤器挡掉的东西。

    返回值只搬运四个字段（``rule_id`` / ``category`` / ``severity`` / ``quote`` /
    ``suggestion``），不复制 LLM 原话之外的任何推断。
    """
    out: list[dict[str, Any]] = []
    for issue in issues or ():
        if not isinstance(issue, dict):
            continue
        rule_id = issue.get("rule_id")
        if not is_continuity_rule_id(rule_id):
            continue
        out.append(
            {
                "rule_id": rule_id,
                "category": issue.get("category"),
                "severity": issue.get("severity"),
                "quote": issue.get("quote"),
                "suggestion": issue.get("suggestion"),
            }
        )
    return out


__all__ = [
    "ALL_CONTINUITY_RULE_IDS",
    "CONTINUITY_RULE_IDS",
    "CRITIC_TAXONOMY",
    "DETERMINISTIC_CONTINUITY_RULE_IDS",
    "continuity_findings",
    "is_continuity_rule_id",
    "taxonomy_category",
]
