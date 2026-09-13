"""AI 味信号共享词表与密度工具（packages.core.quality.ai_flavor）。

「AI 味」在本仓有四条独立计算通道（V3.9 批次 3.4 审查发现口径漂移风险）：

1. :func:`packages.core.quality.ai_patterns.scan_ai_patterns` —— **权威信号源**：
   正则 / 句子级模式检测（禁用词、三连句式、他她排比、章尾升华、标点滥用、解释腔），
   由 chapter_review 的 basic_hints 消费；产物是「命中清单」，不进 quality 子分。
2. ``style`` 子分（:func:`packages.core.quality.scoring.score_style`）—— **密度阈值**：
   对 :data:`AI_FLAVOR_MARKERS` 计算每千字命中密度，≥ 5 / 千字 扣 15 分。
3. ``ai_trace`` 子分（:func:`packages.core.quality.ai_trace.cliche_density`）——
   **套话密度阶梯 + 跨章重复**：对 :data:`AI_CLICHES` 计算每千字命中密度并按阶梯扣分；
   跨章重复用 13 字 shingle（与 Q6 同窗口）。
4. critic LLM 的 ``ai_flavor`` 维度（chapter_review；本轮不改，R9 留档）—— LLM 主观评分。

本模块是 2 / 3 两条 rule-based 子分的**唯一词表来源**。此前 style 用
``guardrails.AI_MARKERS``、ai_trace 自带一份 ``AI_CLICHES``，转折 / 议论类词
（然而、但是、不仅、更重要的是……）两边各写一遍，改一边忘另一边即口径漂移。现在：

- :data:`AI_FLAVOR_MARKERS`：REQ-Q7 词表（spec §4.7，12 条），style 密度 + req_q7 共用；
- :data:`AI_CLICHE_CONNECTIVES`：:data:`AI_CLICHES` 里与上面重叠的转折 / 议论子集；
- :data:`AI_CLICHE_DESCRIPTORS`：ai_trace 独有的神情 / 情绪 / 氛围 / 套路化叙事套话；
- :data:`AI_CLICHES` = descriptors + connectives（内容与 V3.9.2 前的 ai_trace.AI_CLICHES
  完全一致，保证子分行为不变）。

分工（消除重复计算但保留差异化）：

- **style 管密度阈值**——单章内 marker 密度超阈值即扣分；
- **ai_trace 管跨章重复与套话阶梯**——与最近 N 章 shingle 重合率、长期稳定套话命中；
- **scan_ai_patterns 管模式级检测**——句式排比 / 章尾升华 / 标点滥用等，供 review 提示。

行为约束：本模块只统一「词表 + 密度工具」，不改任何既有阈值 / 权重 / 阶梯。
"""

from __future__ import annotations

from typing import Iterable

# 权威信号源：在本模块再导出，调用方可从单点引用 AI 味检测能力。
from .ai_patterns import scan_ai_patterns  # noqa: F401

AI_FLAVOR_MARKERS: tuple[str, ...] = (
    "首先",
    "其次",
    "再次",
    "最后",
    "不仅",
    "更重要的是",
    "然而",
    "但是",
    "总而言之",
    "综上所述",
    "值得注意的是",
    "由此可见",
)
"""REQ-Q7 AI 标记词（spec §4.7）：style 密度阈值与 req_q7 共用。"""

AI_CLICHE_CONNECTIVES: tuple[str, ...] = (
    "然而",
    "但是",
    "不仅",
    "更重要的是",
    "值得注意的是",
    "由此可见",
    "总而言之",
    "综上所述",
)
"""转折 / 议论类套话（AI_FLAVOR_MARKERS 的子集）；ai_trace 套话表引用本常量。"""

AI_CLICHE_DESCRIPTORS: tuple[str, ...] = (
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
)
"""ai_trace 独有的神情 / 情绪 / 氛围 / 套路化叙事套话（非转折类）。"""

AI_CLICHES: tuple[str, ...] = AI_CLICHE_DESCRIPTORS + AI_CLICHE_CONNECTIVES
"""中文 AI 高频套话全表（~47 条）；内容与共享化之前完全一致，仅来源改为集中维护。"""


def marker_hits_per_kchars(text: str, markers: Iterable[str]) -> float:
    """每千字命中次数（子串匹配；空文本 ⇒ 0.0）。

    style 密度与 ai_trace 套话密度共用本函数——此前两处各写一遍
    ``sum(count) / (len / 1000)``，本函数是「密度」的唯一定义。
    """
    if not text:
        return 0.0
    n = len(text)
    if n <= 0:
        return 0.0
    hits = sum(text.count(m) for m in markers)
    return hits / (n / 1000.0)


__all__ = [
    "scan_ai_patterns",
    "AI_FLAVOR_MARKERS",
    "AI_CLICHE_CONNECTIVES",
    "AI_CLICHE_DESCRIPTORS",
    "AI_CLICHES",
    "marker_hits_per_kchars",
]
