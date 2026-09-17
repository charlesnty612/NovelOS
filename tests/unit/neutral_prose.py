"""测试用中性正文填充：可见字数可控，且不触碰任何模式级规则（2026-09-17 抽取）。

起因：多处 fixtures 用**单段** ``"中" * N``（或 ``"正文" * N``）充字数。那一整段
就是「手机上一段 N 行实心字」的病理样本——可读性算子（2026-09-17 新增）会
**正确**命中它：``AI-LONG-PARA``（单段 >140 可见字 → error）与 ``AI-DIALOGUE-LOW``
（通篇零对话 → error）。但那些用例测的是字数带 / 题材核销 / 预检分腿，不是排版，
被长段规则判红属于「fixture 是伪样本」而不是「规则误报」——故抽取本模块统一替换。

``neutral_prose(chars)`` 的保证：

- **恰好** ``chars`` 个 ``visible_chars``（用例断言的是 ±15% / ±30% 这类精确百分比）；
- 多段（每段 2 句、约 26~36 字）：不触发 ``AI-LONG-PARA`` / ``AI-SHORT-PARA``；
- 含约 20% 成对 “…” 对话：不触发 ``AI-DIALOGUE-LOW``；
- 无破折号 / 省略号 / 解释腔 / 禁用词 / 三连同一开头 / 他她排比 / 章尾套话。

需要「句长完全均一」之类的特殊分布时不要用本 helper（那类用例自带 fixture）。
"""

from __future__ import annotations

from packages.core.quality.wordcount import visible_chars

# 句子池：句首两字互不相同（避免三连同一开头 / 他她排比），无禁用词与套话。
SENTENCES: tuple[str, ...] = (
    "他站在柜台后头，把价签翻了个面。",
    "雨点打在油纸伞上，声音又密又匀。",
    "“这袋盐先记在账上。”",
    "巷口的灯笼晃了两下，火光歪向一边。",
    "她把铜钱一枚一枚码进抽屉。",
    "“账本我拿走一册。”",
)
SENTENCES_PER_PARAGRAPH = 2


def neutral_prose(chars: int) -> str:
    """恰好 ``chars`` 可见字的中性正文（多段 + 含对话）。"""
    if chars <= 0:
        return ""
    sentences: list[str] = []
    total = 0
    while total < chars:
        sentence = SENTENCES[len(sentences) % len(SENTENCES)]
        sentences.append(sentence)
        total += visible_chars(sentence)
    paragraphs = [
        "".join(sentences[i:i + SENTENCES_PER_PARAGRAPH])
        for i in range(0, len(sentences), SENTENCES_PER_PARAGRAPH)
    ]
    text = "\n\n".join(paragraphs)
    while visible_chars(text) > chars:
        text = text[:-1]
    out = text.rstrip()
    assert visible_chars(out) == chars, f"{visible_chars(out)} != {chars}"
    return out
