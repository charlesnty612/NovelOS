"""AI 痕迹检测（ai_trace）子分（packages.core.quality.ai_trace）。

为对抗番茄等平台的低质 AI 文检测（章节连续性重复率、文风机械性），
新增 ai_trace 维度。三个子信号合成 0-100 分（分数越高越"像人写"，扣分制）：

- a. 章内重复率（in_chapter）：对正文做 shingles（13 字窗口，参照
      :func:`packages.core.quality.guardrails.compute_shingles` 与 Q6 / §3.4 一致），
      计算重复 shingle 占比。占比越高扣分越多。
- b. 跨章重复率（cross_chapter）：与同项目最近 N 章已提交正文比对 shingles 重合率。
      拿不到历史章节时降级满分并记 note（不阻断）。
- c. AI 套话命中（cliche_hit）：内置一张默认中文 AI 高频套话表（约 30 条），按每千字
      命中次数阶梯扣分。套话表定义为模块级常量，方便后续扩展。

合成公式（write 模块顶部 ``_FORMULA_TEXT``）：

    ai_trace = 100
              - w_in * in_chapter_deduction
              - w_cross * cross_chapter_deduction
              - w_cliche * cliche_deduction

默认权重（见 :data:`WEIGHTS`）：

- w_in     = 0.40（章内重复权重最高——章内大段复制是最强的"机械感"信号）
- w_cross  = 0.35（跨章重复权重次之）
- w_cliche = 0.25（套话命中作为辅助信号）

权重和阈值建议值（待校准）：

- 套话密度 < 0.5 / 千字   ⇒  0 分
- 套话密度 0.5-1.5 / 千字 ⇒  8 分
- 套话密度 1.5-3.0 / 千字 ⇒ 18 分
- 套话密度 ≥ 3.0 / 千字   ⇒ 30 分

校准基线（用于单测固化）：
- 普通人工章节 ≥ 80 分；明显灌水重复章节 ≤ 60 分。

设计要点：

- 纯函数：除 :func:`compute_shingles` 外不依赖 DB / 网络 / LLM；
- 章内/跨章重复判定与 Q6 / §3.4 trigram 重复共用 :func:`compute_shingles` 入口，避免
  在多个模块各自实现 shingle 计算；
- 套话表为模块级 tuple 常量，方便后续扩展并保证可序列化；
- 不产出 error 级 issue——ai_trace 是 score 子分，不是 guardrail；其结果以子分形式
  表达，不阻断提交。
"""

from __future__ import annotations

from typing import Iterable

from .guardrails import _norm, compute_shingles


# ============================================================================
# AI 高频套话表（默认；模块级常量，便于扩展）
# ============================================================================
#
# 收录标准：网文社区吐槽的中文 AI 高频套话；多为短句或短语，作为子串匹配。
# 新增套话直接改本表即可（无需改 scoring 逻辑）。

AI_CLICHES: tuple[str, ...] = (
    # 神情 / 微反应
    "不禁",
    "仿佛",
    "嘴角勾起",
    "嘴角微微上扬",
    "眼中闪过一丝",
    "眼中闪过一抹",
    "眼底闪过",
    "眼底深处",
    "眸子微微一缩",
    "眉头微皱",
    "眉头紧锁",
    "神色微变",
    # 情绪
    "深吸一口气",
    "深吸了一口",
    "倒吸一口凉气",
    "倒吸了一口凉气",
    "心情复杂",
    "心下一凛",
    "心中一震",
    "心底涌起",
    # 氛围 / 描写
    "空气仿佛凝固",
    "空气骤然凝固",
    "空气瞬间凝固",
    "凝固了一般",
    "时间仿佛停止",
    "仿佛凝固",
    "落针可闻",
    "一片死寂",
    "整个空间",
    # 转折 / 议论
    "然而",
    "但是",
    "不仅",
    "更重要的是",
    "值得注意的是",
    "由此可见",
    "总而言之",
    "综上所述",
    # 套路化叙事
    "不置可否",
    "嗤笑一声",
    "冷冷一笑",
    "淡淡开口",
    "淡淡说道",
    "淡淡地开口",
    "沉声开口",
    "沉声说道",
    "声音低沉",
    "一字一句",
    # 数量 ~30
)
"""中文 AI 高频套话表（默认 ~30 条）。模块级 tuple 常量；扩展时直接追加。"""


# ============================================================================
# 阈值与权重（建议值待校准；普通人工 ≥ 80，明显灌水 ≤ 60）
# ============================================================================

# 章内重复率阶梯（重复 shingle 占比 → 扣分）
_INTRA_BUCKETS: tuple[tuple[float, int], ...] = (
    (0.05, 0),   # < 5%：不扣
    (0.15, 20),  # 5-15%：扣 20
    (0.50, 50),  # 15-50%：扣 50
    (1.0001, 70),  # ≥ 50%：扣 70
)
"""章内重复率阶梯扣分（建议值待校准）。"""

# 跨章重复率阶梯（与最近 N 章的 shingle 重合率 → 扣分）
_CROSS_BUCKETS: tuple[tuple[float, int], ...] = (
    (0.10, 0),   # < 10%：不扣
    (0.25, 20),  # 10-25%：扣 20
    (0.45, 40),  # 25-45%：扣 40
    (1.0001, 60),  # ≥ 45%：扣 60
)
"""跨章重复率阶梯扣分（建议值待校准）。"""

# 套话命中阶梯（每千字命中数 → 扣分）
_CLICHE_BUCKETS: tuple[tuple[float, int], ...] = (
    (0.5, 0),   # < 0.5 / 千字：不扣
    (1.5, 12),  # 0.5-1.5 / 千字：扣 12
    (3.0, 28),  # 1.5-3.0 / 千字：扣 28
    (1000.0001, 45),  # ≥ 3.0 / 千字：扣 45
)
"""套话命中阶梯扣分（建议值待校准）。"""

WEIGHTS: dict[str, float] = {
    "in_chapter": 0.40,
    "cross_chapter": 0.35,
    "cliche": 0.25,
}
"""ai_trace 三子信号权重。"""

WINDOW: int = 13
"""ai_trace 使用的 shingle 窗口长度（与 Q6 一致：13 字）。"""

PREVIOUS_CHAPTERS_LIMIT: int = 3
"""跨章重复比对取最近 N 章正文（按章节 number 降序）。"""


# ============================================================================
# 子信号计算（纯函数）
# ============================================================================


def _shingle_repetition_ratio(s: str, n: int) -> float:
    """返回 s 中「出现次数 ≥2 的 shingle 总重复次数 / shingle 总数」。

    取值 [0, 1]：0 表示无重复，1 表示所有 shingle 都有重复。
    """
    total = 0
    extras = 0
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for i in range(len(s) - n + 1):
        sh = s[i : i + n]
        counts[sh] = counts.get(sh, 0) + 1
        total += 1
    if total == 0:
        return 0.0
    for c in counts.values():
        if c > 1:
            extras += c - 1
    return extras / total


def _bucket_deduct(value: float, buckets: Iterable[tuple[float, int]]) -> int:
    """按阶梯表查扣分（value 越大扣分越多；上限 = 最大桶扣分）。

    阶梯语义：``(threshold, deduct)`` 表示 ``value >= threshold`` 时扣分升级到
    ``deduct``；遍历时按升序处理，返回最后匹配的扣分（即最大 deduct）。
    """
    deduct = 0
    for threshold, score in buckets:
        if value >= threshold:
            deduct = score
        else:
            break
    return deduct


def intra_chapter_repetition(draft: str, *, window: int = WINDOW) -> tuple[float, int]:
    """章内重复率子信号。

    返回 ``(ratio, deduction)``：ratio 是重复 shingle 占比；deduction 是按
    :data:`_INTRA_BUCKETS` 阶梯扣分。空文本 / 短文本 ⇒ ``(0.0, 0)``。
    """
    t = _norm(draft or "")
    if len(t) < window:
        return 0.0, 0
    ratio = _shingle_repetition_ratio(t, window)
    return ratio, _bucket_deduct(ratio, _INTRA_BUCKETS)


def cross_chapter_repetition(
    draft: str,
    previous_drafts: Iterable[str],
    *,
    window: int = WINDOW,
) -> tuple[float, int, bool]:
    """跨章重复率子信号。

    参数：
        draft: 当前章节正文。
        previous_drafts: 同项目最近 N 章正文（来自 QualityContext.previous_drafts）。

    返回 ``(ratio, deduction, available)``：

    - ``ratio``：与最近 N 章 shingle 的重合率 = ``len(common) / len(current)``；
    - ``deduction``：按 :data:`_CROSS_BUCKETS` 阶梯扣分；
    - ``available``：False 表示无历史章节可比对 ⇒ 跨章子信号降级（ratio=0, deduction=0）。

    说明：
    - 历史章节按顺序拼接进 shingles 池（不去重，章节间重复也算）；
    - 取不到任何历史章节（previous_drafts 为空 / None / 全空串）⇒ 降级满分。
    """
    t = _norm(draft or "")
    if len(t) < window:
        return 0.0, 0, False
    cur = compute_shingles(t, window)
    if not cur:
        return 0.0, 0, False

    pool: set[str] = set()
    avail = False
    for pd in previous_drafts or []:
        if not pd:
            continue
        avail = True
        pool.update(compute_shingles(pd, window))
    if not avail or not pool:
        return 0.0, 0, False

    cur_set = set(cur)
    common = cur_set & pool
    ratio = len(common) / len(cur_set) if cur_set else 0.0
    return ratio, _bucket_deduct(ratio, _CROSS_BUCKETS), True


def cliche_density(
    draft: str,
    *,
    cliches: tuple[str, ...] = AI_CLICHES,
) -> tuple[float, int]:
    """AI 套话命中子信号。

    返回 ``(per_kchars, deduction)``：per_kchars 是每千字命中次数；deduction 是
    按 :data:`_CLICHE_BUCKETS` 阶梯扣分。空文本 ⇒ ``(0.0, 0)``。
    """
    if not draft or not cliches:
        return 0.0, 0
    n_chars = len(draft)
    if n_chars <= 0:
        return 0.0, 0
    hits = sum(draft.count(c) for c in cliches)
    per_k = hits / (n_chars / 1000.0)
    return per_k, _bucket_deduct(per_k, _CLICHE_BUCKETS)


# ============================================================================
# 合成（ai_trace 子分）
# ============================================================================


_FORMULA_TEXT: str = (
    "ai_trace = round(100 - 0.40*in_deduct - 0.35*cross_deduct - 0.25*cliche_deduct)"
)
"""ai_trace 合成公式（与 WEIGHTS 对齐；hash 重算用）。"""


def compute_ai_trace(
    draft: str,
    previous_drafts: Iterable[str] | None = None,
) -> tuple[int, dict[str, object]]:
    """ai_trace 子分主入口。

    参数：
        draft: 当前章节正文。
        previous_drafts: 同项目最近 N 章正文（可选）；为空时跨章子信号降级。

    返回 ``(score, detail)``：

    - score: ``int`` ∈ [0, 100]（合成扣分后截断）；
    - detail: dict 含三个子信号原始值与扣分 + formula 公式文本。
    """
    in_ratio, in_deduct = intra_chapter_repetition(draft or "")
    cr_ratio, cr_deduct, cross_avail = cross_chapter_repetition(
        draft or "", previous_drafts or []
    )
    cl_per_k, cl_deduct = cliche_density(draft or "")

    raw = (
        100.0
        - WEIGHTS["in_chapter"] * in_deduct
        - WEIGHTS["cross_chapter"] * cr_deduct
        - WEIGHTS["cliche"] * cl_deduct
    )
    score = int(round(raw))
    score = max(0, min(100, score))

    detail: dict[str, object] = {
        "in_chapter_ratio": round(in_ratio, 4),
        "in_chapter_deduct": int(in_deduct),
        "cross_chapter_ratio": round(cr_ratio, 4),
        "cross_chapter_deduct": int(cr_deduct),
        "cross_chapter_available": bool(cross_avail),
        "cliche_per_kchars": round(cl_per_k, 4),
        "cliche_deduct": int(cl_deduct),
        "formula": _FORMULA_TEXT,
    }
    return score, detail


def formula_text() -> str:
    """返回 ai_trace 合成公式原文（仅供自检 / 文档化使用）。"""
    return _FORMULA_TEXT


__all__ = [
    "AI_CLICHES",
    "WEIGHTS",
    "WINDOW",
    "PREVIOUS_CHAPTERS_LIMIT",
    "intra_chapter_repetition",
    "cross_chapter_repetition",
    "cliche_density",
    "compute_ai_trace",
    "formula_text",
]