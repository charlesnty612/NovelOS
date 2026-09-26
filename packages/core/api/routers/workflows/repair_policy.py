"""改稿修复策略：失败形状 → 单一修复动作（2026-09-18，替代「一律 revise」）。

## 为什么需要这张表

2026-09-18 线上事故（``prj_2567bb8de642``）：auto_revise 回路对**每一种**失败施加同一个
动作——resume 原 review run 带 ``revise: true``。而 revise 在 writer 侧是**契约受限**的
（``docs/agents/prompts/writer-v3.md`` 规则 20「净增 ≤ +5%」、规则 1 / §6.1「未提及的部分
逐字保留、不整章重写」），于是：

- 首稿远低于目标字数时，能到字数带的唯一路径就是改稿回路，而回路按 revise 驱动 ⇒
  实测 917→1044→1242→1300 字（单轮 +5% 量级），追 -65% 的缺口数学上追不回，「轮次耗尽」；
- 逃逸到 ``fresh_write`` 后字数虽然补上，但一次改稿产出 **9 段逐字重复**（章内重复率
  20.3% / trigram 30.37%，writer 自述「draft_text 为上游既成稿…不做破坏性扩写」）——
  **revise 正是制造重复的那把工具**，拿它修长度是选错了工具。

对策：把「失败形状 → 修复动作」写成**纯函数 + 数据表**，动作是闭集三选一：

============================  ======================================================
``regenerate``                丢掉旧稿全新重写（write 子 run 带 ``fresh_write``，
                              writer 回到 ``mode='write'``）：长度带下限大缺口、
                              章内重复。
``revise``                    定向局部改稿（保留骨架与已认可的正文）：局部形态类
                              规则、压缩、小缺口长度、作者主观驳回。
``stop``                      停手交给作者：**不认识的 rule_id**、缺 rule_id、
                              连续性 / 逻辑 / 设定类规则、读不到评审报告。
============================  ======================================================

## 不变量（每条都有测试看守，见 ``tests/unit/test_repair_policy.py``）

1. **纯函数**：输入只有评审报告 dict + 本回路剩余轮次；不读库、不看时间、不调 LLM。
   决策依据全部写进 :class:`RepairDecision`（``reason`` / ``detail`` / ``rule_ids`` /
   ``shortfall``），供 run 日志与 API payload 审计。
2. **未知规则一律硬停**：``errors[]`` 里出现表外 rule_id、或缺 rule_id → ``stop``；
   绝不「不认识就按 revise 处理」。
3. **F-19**：任何「占比 / 频率」类**风格偏好**指标（对白占比、参照书重叠率、人工占比 …）
   不得映射到任何自动修复动作——它们可被优化，模型凑数最省力的手段就是灌水。
   表外比值规则自动落 ``stop``（不变量 2），另有专门用例钉住。
4. **轮次有界**：本模块只回答「这一轮怎么修」，轮次上限在调用方（``_auto_revise_loop``）；
   耗尽时由 :func:`describe_exhaustion` 点名未能修复的形状。

## 与 ``scripts/produce_chapters.py`` 的关系

驱动侧 ``REVISABLE_RULE_IDS``（"哪些 error 可以交给服务端自动修"）与本表
:data:`REVISE_RULE_IDS` 是**同一组规则**；差别只有两处，且都在保守侧：
重复类规则（:data:`REGENERATE_RULE_IDS`）驱动不做定向改稿笔记 —— 它另有更强的
「草稿自身重复率超阈值就拒绝批准」闸门（见该脚本 ``decide_review`` 第 1 步）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Action = Literal["regenerate", "revise", "stop"]

# revise 模式单轮净增上限。与 ``docs/agents/prompts/writer-v3.md`` 规则 20
# 「revise 修订模式下净增字数不得超过修订前的 +5%」同源，改 prompt 必须同步此处。
REVISE_MAX_NET_GROWTH_RATIO = 0.05


# ---------------------------------------------------------------------------
# 规则表
# ---------------------------------------------------------------------------

# 长度类：review 侧 ``W-LEN-DEVIATION``（basic_checks 出带恒发 warning、越带超带边距
# 升 error）与题材核销 ``GENRE-WORD-BAND-DEVIATION``（恒 warning，见 AGENTS.md 硬规则 4：
# GENRE-* 不进阻断白名单）。这两个不走 rule_id 表分类，走下面的「字数带」分支。
LENGTH_RULE_IDS: frozenset[str] = frozenset({"W-LEN-DEVIATION", "GENRE-WORD-BAND-DEVIATION"})

# 章内重复类 → 重产。``RULE_STYLE_REPETITION_TRIGRAM`` 是 quality 评分侧确定性算子
# （``quality/scoring.py``；超确认阈值 0.25 的那一档为 confirm：不许静默通过）；
# ``AI-BEAT-REPEAT`` 是 character 章内远距小句字面复现（``quality/beat_repeat.py``，
# severity 恒 warning ⇒ 只在 ``ai_pattern_hits`` / ``warnings`` 出现，不进 errors）。
#
# 为什么这两条**永不 revise**：重复本身就是 revise 的产物（同一段素材被搬两次），
# 让它「局部改」只会在原地再搬一次；重产是从头写一遍，不复用旧稿素材。
REGENERATE_RULE_IDS: frozenset[str] = frozenset(
    {"RULE_STYLE_REPETITION_TRIGRAM", "AI-BEAT-REPEAT"}
)

# 局部形态类 → 定向改稿（revise 真正擅长的事：句式 / 标点 / 段落 / 套话的局部替换）。
# 与 ``scripts/produce_chapters.py:REVISABLE_RULE_IDS`` 同组，外加 ``AI-TRIPLET-OPENING``：
# 它是「连续 3 句以同一个两字词开头」，与 AI-PRONOUN-PILE 同族、同样可 error
# （``_SEVERITY_UPGRADE_LIMITS``：≥5 次升 error），不在表内就等于把一个可局部修的问题
# 一律硬停。
#
# 未列入的两类（刻意）：
# - ``AI-ENDING-SUMMARY`` / ``AI-ANTHRO-VEHICLE`` / ``AI-DIALOGUE-ECHO``：severity 恒
#   warning，永远不会进 errors，本表无从分类（进了也只当没有）；
# - 占比 / 频率类风格偏好指标（``AI-DIALOGUE-LOW`` 等，见 :data:`RATIO_METRIC_RULE_IDS`）。
REVISE_RULE_IDS: frozenset[str] = frozenset(
    {
        "AI-PUNCT-ABUSE",
        "AI-LONG-PARA",
        "AI-SHORT-PARA",
        "AI-PRONOUN-PILE",
        "AI-TRIPLET-OPENING",
        "AI-CONTRAST-PAIR",
        "AI-EXPLAIN-TONE",
        "AI-FORBIDDEN-WORD",
    }
)

# F-19 明文禁入项（占比 / 频率类**风格偏好**指标）：既不在 :data:`REVISE_RULE_IDS`，
# 也不在 :data:`REGENERATE_RULE_IDS` ⇒ 命中即按「未知规则」硬停。本集合的两用：
# 测试的显式禁入断言 + 文档化的边界。注意边界按本仓既有口径划（AGENTS.md F-19 的
# 实证对象是「对话占比」：模型为凑占比灌对白），破折号 / 短段这类**形态计数**规则
# 是另一个批次校准过的（阈值取自本仓 p85/p90 实测分布），不在此列。
RATIO_METRIC_RULE_IDS: frozenset[str] = frozenset(
    {
        "AI-DIALOGUE-LOW",  # 对白占比（可优化指标，2026-09-17 已从驱动 REVISABLE 撤回）
        "AI-DIALOGUE-ECHO",  # 对白回声对数的比例化口径
        "RULE_Q6_OVERLAP_RATE",  # 参照书重叠率红线（抄袭风险，阻断白名单，须人工裁定）
        "RULE_Q8_HUMAN_RATIO_LOW",  # 人工占比红线（同上）
    }
)

KNOWN_RULE_IDS: frozenset[str] = (
    LENGTH_RULE_IDS | REGENERATE_RULE_IDS | REVISE_RULE_IDS
)

# 形状 → 人类可读标签（轮次耗尽 / 硬停时点名用）。
SHAPE_LABELS: dict[str, str] = {
    "no_review_report": "读不到本轮评审报告（无法判定失败形状）",
    "error_without_rule_id": "评审 error 缺 rule_id（无法定向）",
    "unknown_rule_id": "出现策略表未覆盖的 rule_id",
    "in_chapter_repetition": "章内重复",
    "length_shortfall_beyond_revise_cap": "字数带下限缺口超出 capped revise 可达幅度",
    "length_shortfall_within_revise_cap": "字数带下限缺口（capped revise 可达）",
    "length_over_band": "字数超带（压缩不受 +5% 上限约束）",
    "surgical_style_rules": "局部形态类规则",
    "author_requested_revision": "作者主观驳回（报告无 error）",
}


@dataclass(frozen=True)
class RepairDecision:
    """一次修复决策：动作 + 原因码 + 可审计依据。"""

    action: Action
    # 机器可读原因码（= SHAPE_LABELS 的键），轮次耗尽时按它点名形状。
    reason: str
    # 人类可读依据：把决策用到的数字与 rule_id 全写进来，日志/响应可直接读。
    detail: str
    rule_ids: tuple[str, ...] = ()
    # 长度分支的 ``(achieved, band_low)``；非长度分支为 None。
    shortfall: tuple[int, int] | None = None

    @property
    def regenerate(self) -> bool:
        """本轮 write 是否走 ``fresh_write``（全新重写）。"""
        return self.action == "regenerate"

    @property
    def label(self) -> str:
        return SHAPE_LABELS.get(self.reason, self.reason)

    def as_dict(self) -> dict[str, Any]:
        """落 API payload / 日志的结构化形态（字段少而稳定，便于驱动读）。"""
        return {
            "action": self.action,
            "reason": self.reason,
            "shape": self.label,
            "rule_ids": list(self.rule_ids),
            "detail": self.detail,
            "shortfall": list(self.shortfall) if self.shortfall else None,
        }


def describe_exhaustion(reasons: list[str], max_iter: int) -> str:
    """轮次耗尽时的失败信息：**点名**它修不动的形状（而不是「轮次耗尽」四个字）。"""
    shapes: list[str] = []
    for reason in reasons:
        label = f"{reason}（{SHAPE_LABELS.get(reason, reason)}）"
        if label not in shapes:
            shapes.append(label)
    joined = "、".join(shapes) if shapes else "（无决策记录）"
    return (
        f"auto_revise 轮次耗尽（{max_iter} 轮）：未能修复的形状 = {joined}；"
        f"停在人工处置，不再自动重试"
    )


# ---------------------------------------------------------------------------
# 报告取数（纯函数；报告形态见 chapter_review.pipeline._basic_checks_node）
# ---------------------------------------------------------------------------


def length_band_shortfall(report: dict[str, Any]) -> tuple[int, int] | None:
    """报告报出的「字数带**下限**缺口」；返回 ``(achieved, band_low)``。

    数据源两处（同一份 draft 的同一口径，取缺口最大者）：
    - 报告顶层 ``within_range=False`` + ``word_count`` + ``word_band``：review 只要出带
      就发 ``[W-LEN-DEVIATION]`` warning（error 档仅在越带超带边距时出现），而 warning
      级缺口同样可能大到 capped revise 追不回；
    - ``errors[]`` 中 rule_id ∈ :data:`LENGTH_RULE_IDS` 的结构化条目
      （带 ``visible_chars`` + ``word_band``）。

    上限侧（超带）**不**在此产出：writer 规则 20 只对增侧设 +5% cap（压缩按明确指令幅度
    净减，不受 cap 限制）⇒「capped revise 追不回」在压缩方向不成立。
    无带数据 / 缺口 ≤ 0 → None。

    两源口径不一致时取**缺口最大**的一对（``max by band_low - achieved``）：决定
    「capped revise 够不够」的是最紧的那一侧，取小缺口会让回路误判为「改稿就能追上」。
    """
    pairs: list[tuple[int, int]] = []

    def _accept(achieved: Any, band: Any) -> None:
        if isinstance(achieved, bool) or not isinstance(achieved, int) or achieved <= 0:
            return
        if not isinstance(band, dict):
            return
        low = band.get("low")
        if isinstance(low, bool) or not isinstance(low, int) or low <= achieved:
            return
        pairs.append((achieved, low))

    if report.get("within_range") is False:
        _accept(report.get("word_count"), report.get("word_band"))
    for entry in report.get("errors") or []:
        if not isinstance(entry, dict) or entry.get("rule_id") not in LENGTH_RULE_IDS:
            continue
        _accept(entry.get("visible_chars"), entry.get("word_band"))
    if not pairs:
        return None
    return max(pairs, key=lambda item: item[1] - item[0])


def is_over_band(report: dict[str, Any]) -> bool:
    """报告是否报「字数超带」（超上限）——压缩方向，revise 不受 +5% cap 约束。"""
    band = report.get("word_band")
    count = report.get("word_count")
    if isinstance(band, dict) and isinstance(count, int) and not isinstance(count, bool):
        high = band.get("high")
        if isinstance(high, int) and not isinstance(high, bool) and count > high:
            return True
    for entry in report.get("errors") or []:
        if not isinstance(entry, dict) or entry.get("rule_id") not in LENGTH_RULE_IDS:
            continue
        band = entry.get("word_band")
        count = entry.get("visible_chars")
        if not isinstance(band, dict) or not isinstance(count, int) or isinstance(count, bool):
            continue
        high = band.get("high")
        if isinstance(high, int) and not isinstance(high, bool) and count > high:
            return True
    return False


def _error_entries(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in (report.get("errors") or []) if isinstance(e, dict)]


def _repetition_hits(report: dict[str, Any]) -> tuple[str, ...]:
    """章内重复类规则 id 的命中通道（三处取并集，去重保序）。

    - ``errors[]``：error 档的重复条目（``RULE_STYLE_REPETITION_TRIGRAM`` 走 quality
      评分侧，可能经质量门禁回灌进报告）；
    - ``ai_pattern_hits[]``：确定性扫描的结构化命中（``AI-BEAT-REPEAT`` 恒 warning，
      只可能出现在这里或 ``warnings`` 文本行里）；
    - ``warnings[]``：``[RULE-ID] …`` 文本行（``_basic_checks_node`` 把 warning 级命中
      以该形态追加）。

    重复是**任何档位都要拦**的形状（confirm 档成员本就恒 warning，靠 severity 过滤会
    整条漏掉），因此这里刻意不看 severity。
    """
    hits: list[str] = []

    def _add(rule_id: Any) -> None:
        rid = str(rule_id or "").strip()
        if rid in REGENERATE_RULE_IDS and rid not in hits:
            hits.append(rid)

    for entry in _error_entries(report):
        _add(entry.get("rule_id"))
    for entry in report.get("ai_pattern_hits") or []:
        if isinstance(entry, dict):
            _add(entry.get("rule_id"))
    for line in report.get("warnings") or []:
        if not isinstance(line, str):
            continue
        for rid in REGENERATE_RULE_IDS:
            if f"[{rid}]" in line:
                _add(rid)
    return tuple(hits)


# ---------------------------------------------------------------------------
# 决策
# ---------------------------------------------------------------------------


def decide_repair(
    report: dict[str, Any] | None,
    *,
    remaining_rounds: int,
    revise_max_net_growth_ratio: float = REVISE_MAX_NET_GROWTH_RATIO,
) -> RepairDecision:
    """本轮修复动作（**唯一决策入口**，纯函数）。

    判定顺序（先到先得，每步都能被单测钉住）：

    1. 报告缺失 / 非 dict ⇒ ``stop``（读不到形状就不动手，绝不默认 revise）；
    2. ``errors[]`` 有条目缺 rule_id ⇒ ``stop``（无法定向）；
    3. ``errors[]`` 出现表外 rule_id ⇒ ``stop``（含连续性 / 逻辑 / 设定类与一切
       占比指标——不认识就不猜）；
    4. 任一通道命中章内重复（:data:`REGENERATE_RULE_IDS`）⇒ ``regenerate``；
    5. 字数带**下限**缺口 > 本回路剩余轮次在 +5%/轮下可达幅度
       （``(1+r)**n - 1``）⇒ ``regenerate``；缺口在可达幅度内 ⇒ ``revise``；
    6. 字数**超带**（超上限）⇒ ``revise``（压缩方向不受 cap 约束）；
    7. ``errors`` 为空（作者主观驳回，机器没报问题）⇒ ``revise``（改稿意见由
       ``plan_json.revision_note`` 承载）；
    8. 其余（errors 全在 :data:`REVISE_RULE_IDS` 内）⇒ ``revise``。
    """
    if not isinstance(report, dict) or not report:
        return RepairDecision(
            action="stop",
            reason="no_review_report",
            detail=(
                "读不到本轮的 review_report（chapter-review 的 author_review "
                "pause_payload 落点为空）——无法判定失败形状，停在人工处置，不猜"
            ),
        )

    errors = _error_entries(report)
    raw_errors = list(report.get("errors") or [])
    missing = [e for e in raw_errors if not isinstance(e, dict) or not str(e.get("rule_id") or "").strip()]
    if missing:
        return RepairDecision(
            action="stop",
            reason="error_without_rule_id",
            detail=f"{len(missing)} 条评审 error 没有 rule_id，无法定向修复——人工处置",
        )

    error_ids = [str(e.get("rule_id") or "").strip() for e in errors]
    unknown = sorted({rid for rid in error_ids if rid not in KNOWN_RULE_IDS})
    if unknown:
        return RepairDecision(
            action="stop",
            reason="unknown_rule_id",
            rule_ids=tuple(error_ids),
            detail=(
                f"评审 error 含策略表未覆盖的规则 {unknown}（连续性 / 逻辑 / 设定类与"
                f"占比类指标都在此列）——人工处置，不自动改稿、不猜"
            ),
        )

    repetition = _repetition_hits(report)
    if repetition:
        return RepairDecision(
            action="regenerate",
            reason="in_chapter_repetition",
            rule_ids=repetition,
            detail=(
                f"章内重复命中 {list(repetition)}：revise 会复用旧稿素材（重复正是它造出来的），"
                f"改为全新重写（write 子 run 带 fresh_write，writer 回 mode='write'）"
            ),
        )

    shortfall = length_band_shortfall(report)
    if shortfall is not None:
        achieved, band_low = shortfall
        required_growth = (band_low - achieved) / achieved
        capped_reach = (1.0 + revise_max_net_growth_ratio) ** max(remaining_rounds, 0) - 1.0
        if required_growth > capped_reach:
            return RepairDecision(
                action="regenerate",
                reason="length_shortfall_beyond_revise_cap",
                rule_ids=tuple(sorted(set(error_ids) & LENGTH_RULE_IDS)),
                shortfall=shortfall,
                detail=(
                    f"字数带下限缺口 {achieved}→{band_low}（需 +{required_growth:.1%}）> 剩余 "
                    f"{remaining_rounds} 轮在 revise 净增 ≤{revise_max_net_growth_ratio:.0%}/轮下"
                    f"可达的 +{capped_reach:.1%} ⇒ 本轮 write 走全新重写（fresh_write）"
                ),
            )
        return RepairDecision(
            action="revise",
            reason="length_shortfall_within_revise_cap",
            rule_ids=tuple(sorted(set(error_ids) & LENGTH_RULE_IDS)),
            shortfall=shortfall,
            detail=(
                f"字数带下限缺口 {achieved}→{band_low}（需 +{required_growth:.1%}）在剩余 "
                f"{remaining_rounds} 轮可达的 +{capped_reach:.1%} 之内 ⇒ 仍走 capped revise"
            ),
        )

    if is_over_band(report):
        return RepairDecision(
            action="revise",
            reason="length_over_band",
            rule_ids=tuple(sorted(set(error_ids) & LENGTH_RULE_IDS)),
            detail="字数超带：压缩按明确指令幅度净减，不受 revise 的 +5% 净增上限约束 ⇒ 定向改稿",
        )

    if not error_ids:
        return RepairDecision(
            action="revise",
            reason="author_requested_revision",
            detail=(
                "报告无 error（机器没报可修问题），改稿意图来自作者驳回 ⇒ 按 "
                "plan_json.revision_note 定向改稿，保留已认可的正文"
            ),
        )

    return RepairDecision(
        action="revise",
        reason="surgical_style_rules",
        rule_ids=tuple(error_ids),
        detail=(
            f"评审 error 全部是局部形态类规则 {sorted(set(error_ids))} ⇒ 定向改稿"
            f"（保留情节骨架，只改句式 / 标点 / 段落 / 套话）"
        ),
    )


__all__ = [
    "Action",
    "KNOWN_RULE_IDS",
    "LENGTH_RULE_IDS",
    "RATIO_METRIC_RULE_IDS",
    "REGENERATE_RULE_IDS",
    "REVISE_MAX_NET_GROWTH_RATIO",
    "REVISE_RULE_IDS",
    "SHAPE_LABELS",
    "RepairDecision",
    "decide_repair",
    "describe_exhaustion",
    "is_over_band",
    "length_band_shortfall",
]
