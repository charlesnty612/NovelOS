"""同章远距小句字面复现（``AI-BEAT-REPEAT``）——「同一个节拍重播」的**字面代理**算子。

机制（一句话，规则名即机制名）
------------------------------
把正文按**句读**（，。！？；：、换行空白）切成小句片段，取同一章里**相距
≥ ``DEFAULT_BEAT_MIN_GAP_CHARS`` 字**的两段片段，求其**最长公共子串**；该子串
① 长度 ≥ ``DEFAULT_BEAT_MIN_CHARS`` 字，② 同时覆盖两段片段各自
≥ ``DEFAULT_BEAT_MIN_COVER`` 的字面（双侧覆盖率门），即记一次「小句复现」。
章级判定：命中数 ≥ ``DEFAULT_BEAT_MIN_COUNT`` **且** 密度 > ``DEFAULT_BEAT_RATE_PER_1K`` / 千字。

这是**字面**算子：它量的不是「语义上是不是同一件事」，而是**同一段文字素材在章内
被搬用了两次以上**。之所以能代理「节拍重播」，是因为模型注水时的真实形状是
**换一部分词、保留骨架**——详见下。

为什么需要它（实测缺口，2026-09-18，项目 ``prj_2567bb8de642``）
----------------------------------------------------------------
本仓已有两条章内重复算子，都只抓**复制粘贴**：

- 13 字滑动 shingle 率（``ai_trace`` 的章内重复子信号，权重 0.40）；
- trigram 重复率（``scoring`` 的两档阈值 ``STYLE_TRIGRAM_WARN_THRESHOLD`` /
  ``STYLE_TRIGRAM_CONFIRM_THRESHOLD``，产出 ``RULE_STYLE_REPETITION_TRIGRAM``）。

两者都按**字面重合占全章的比例**计分，而「同一节拍换一套词再演一遍」在比例上
几乎不留痕。两个真实反例（本次实测）：

1. ``prj_2567bb8de642`` ch2，章内 shingle 比 0.0030（判为干净）。章尾把「收据复印件
   还摆在桌上」这个收束画面写了**三遍**——「低头看着桌上那张收据的复印件。付款方
   一栏的“江氏集团有限公司”几个字在台灯下显得格外清楚」→「桌上那份收据复印件还摆着」
   →「桌上那份收据的复印件还摆着，付款方一栏的“江氏集团有限公司”几个字在晨光里
   格外清晰」。本算子命中 5 处，章级报警（1.85/千字）。
2. 同项目 ch3（作者改稿 v1），章内 shingle 比 0.0006（判为干净）。句读片段
   「江曜坐在原告席上」在同章出现 **3 次**、「指节在发白」2 次——本算子命中 2 处，
   章级报警（1.12/千字）。

本算子**不覆盖**什么（照实说，防「名实不符」）
----------------------------------------------
- **语义改写型重复**：同一件事完全换词、换句式写两遍，字面零重合 ⇒ 本算子看不见
  （那需要 LLM / 语义模型；本模块是纯字面、确定性、可复算，不调用任何模型）；
- **数字/计量串复现**：账目数字、日期、金额（如「一百九十二两」「二月廿九的市价」）
  的复现由**数字守卫**显式排除（见 ``_is_numeral_heavy``）——抽样实测这类全是账本
  体裁的正常复述（见下），代价是真正「数字型注水」会漏；
- **跨章复现**：只看单章内（跨章另有 ai_trace 的跨章子信号）；
- **宏观结构重复**（整章骨架、场景功能重复）无覆盖；
- **非中文正文**：参与比较的只有汉字与数字，拉丁字母不参与 ⇒ 英文正文零命中。

口径与校准（2026-09-18，复算入口 ``scripts/ai_tone_calibrate.py``）
------------------------------------------------------------------
语料：本库全部项目的最新草稿（92 章 / 263,347 可见字）vs 人类基线（文风锚点书榜一
《快穿之人渣洗白手册》侯府弧 19 章 / 37,806 可见字，内容仓内，只读打开）：

========================  ==========  ============  ===========  ================
侧                        命中数      密度（/千字）  ≥2 处章数    判定（≥2 且 >1.0）
========================  ==========  ============  ===========  ================
生成侧                    307         1.17          64 / 92      46 / 92
人类侧                    2           0.05          0 / 19       0 / 19
========================  ==========  ============  ===========  ================

**抽样先于采信（AGENTS.md 硬纪律）**：系统抽样 52 条命中逐条人工过（每 12 条取一条，
两个起点各 26 条）——**43 真 / 9 误**（≈82.7% 精确率）。误报集中在两类：
① 专名/文件名/术语的复指（「族老会的决议」「永丰号南集分号」「违约指纹同源」），
② 共用骨架 + 不同宾语（「他看了一眼通话时长 / 看了一眼通话记录」）。
第一版算子（无数字守卫）在**开局章节的连续 24 条命中**上只有 13/24——账目数字串
复述撑起了大半噪声，故补数字守卫（正是「抽样先于采信」抓到的形状）。

人类侧的 2 处（榜一侯府弧第 6、11 章）**都是逐字级的小句复现**（「惊喜来的太快」
「还有蓝眼睛的蛮子时不时过来抢…」）——即人类作者也会这么写，只是极少（19 章 2 处、
分属两章，各 1 处）。这也正是把章级门放在「≥2 处」而不是「≥1 处」的原因。

阈值取 **≥2 处且 >1.0/千字**：人类基线 19 章里**没有一章**达到 2 处，故该门在人类侧
零误报；单章单处（可能只是正常复指）不报，照本仓「堆积门」先例（弱词层 / 长段 /
拟人喻体）。severity 恒 warning——它是提示不是闸门。

阈值是关键词参数，本地校准 / 抽样入口：``scripts/ai_tone_calibrate.py``。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from packages.core.quality.wordcount import visible_chars

# ---------------------------------------------------------------------------
# 阈值常量（校准依据见模块 docstring；判别测试 tests/unit/quality/test_beat_repeat.py）
# ---------------------------------------------------------------------------

# 共享串最小字数：5 字档实测引入大量「同前缀不同谓语」误报（「法律顾问站在侧边 /
# 法律顾问站起来」），6 字档在抽样中精确率更高；代价是丢掉「指节在发白」这类 5 字节拍
# （本算子**不覆盖**，不是缺陷）。
DEFAULT_BEAT_MIN_CHARS = 6

# 双侧覆盖率门：共享串必须同时占两段片段各自 ≥60% 的字面。
# 起因（首轮抽样）：只取「最长公共子串」会把长片段里共有的名词短语当成节拍
# （「收据的复印件」出现在两句不同的话里），覆盖率门把这类「片段里的局部重合」挡掉，
# 只留「两段话本身就是同一段素材」的情形。0.6 是实测分离点——ch2 的真节拍
# 「付款方一栏的“江氏集团有限公司”几个字在」覆盖率 18/27、18/25（0.67/0.72）保留；
# 而「十一月十九日」那类事实复指只有 6/13（0.46）被挡。
DEFAULT_BEAT_MIN_COVER = 0.6

# 最小间距（原文字符）：两处片段起点相距不足此数不算「远距复现」——近距重复是
# **复制粘贴**，已由 shingle / trigram / 接词回声三条既有算子覆盖，本算子补的是
# 「隔了一段又写一遍」。200 字≈3~5 段。
DEFAULT_BEAT_MIN_GAP_CHARS = 200

# 数字守卫：共享串中「数字/量词字」占比 ≥ 此值时不计。
# 依据：抽样第一版（无守卫）里账目数字复述（「入账一千二百斤」「每斤一钱六分」
# 「一百九十二两」「二月廿九的市价」）占全部误报的一半。
DEFAULT_BEAT_NUMERAL_MAX_RATIO = 0.6

# 章级判定：命中数下限（堆积门，照 AI-LONG-PARA「单章 ≥2 段」先例）与密度阈值。
# 人类基线 19 章零章达到 2 处（全弧 2 处、分属两章），故该门在人类侧零误报。
DEFAULT_BEAT_MIN_COUNT = 2
DEFAULT_BEAT_RATE_PER_1K = 1.0

# 最小判定长度（照 ``ai_patterns._MIN_PROSE_CHARS_FOR_DENSITY`` 先例）：
# 几十字的片段（单测样例、引文）算密度没有统计意义。
MIN_PROSE_CHARS_FOR_BEATS = 200

# ---------------------------------------------------------------------------
# 内部分词 / 数字集
# ---------------------------------------------------------------------------

# 句读切分：**只按句读切**。引号（“”「」）、括号、书名号**不切**——按引号切会把
# 「付款方一栏的“江氏集团有限公司”几个字在…」这类真正的长复现碎成小块，
# 从而产出「付款方一栏的」这种无意义的半截命中（首版实测）。
_CLAUSE_BREAK_RE = re.compile(r"[，。！？；：、,.;:!?\n\r\t\u3000 ]+")
# 参与比较的字符：汉字与数字。标点/空白/拉丁字母不参与（故英文正文不判）。
_KEEP_CHAR_RE = re.compile(r"[\u4e00-\u9fff0-9]")

# 数字与常用计量单位字（数字守卫用）。
_NUMERAL_CHARS = frozenset("0123456789０１２３４５６７８９一二三四五六七八九十百千万亿零两廿卅")
_MEASURE_CHARS = frozenset("年月日时点分秒号斤两钱亩石贯文斗升尺寸丈里倍成")

# 样例展示用的单侧截断长度（samples 只供人工过目，不参与判定）。
_SAMPLE_MAX_CHARS = 40


@dataclass(frozen=True)
class BeatRepeat:
    """一次「同章远距小句复现」及其两处上下文（供抽样核对与人工过目）。"""

    text: str  # 复现的字面小句（两处共享串）
    first: str  # 先出现的那处片段原文（已截断）
    second: str  # 后出现的那处片段原文（已截断）
    length: int  # 共享串字数
    gap: int  # 两处片段起点相距的**原文字符**数
    occurrences: int  # 该串在本章出现的片段数

    @property
    def sample(self) -> str:
        """``先出现｜后出现`` 形态（章级 message 与抽样打印用）。"""
        return f"{self.first}｜{self.second}"


def _clauses(prose: str) -> list[tuple[int, str, str]]:
    """句读片段列表 ``(原文字符起始偏移, 参与比较的字, 片段原文)``。"""
    out: list[tuple[int, str, str]] = []
    start = 0
    for match in _CLAUSE_BREAK_RE.finditer(prose):
        piece = prose[start : match.start()]
        offset = start
        start = match.end()
        if not piece:
            continue
        chars = "".join(_KEEP_CHAR_RE.findall(piece))
        if chars:
            out.append((offset, chars, piece))
    tail = prose[start:]
    if tail:
        chars = "".join(_KEEP_CHAR_RE.findall(tail))
        if chars:
            out.append((start, chars, tail))
    return out


def _longest_common_substring(a: str, b: str) -> tuple[int, int, int]:
    """最长公共子串，返回 ``(长度, a 中起点, b 中起点)``。

    确定性：等长并列时取（先按 a 起点、再按 b 起点）最靠前者。DP 实现，无随机、
    无哈希序依赖（片段都短，不需要 difflib 的启发式去杂兼松）。
    """
    best_len = best_a = best_b = 0
    prev = [0] * (len(b) + 1)
    for i, ca in enumerate(a, 1):
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                n = prev[j - 1] + 1
                cur[j] = n
                if n > best_len:
                    best_len, best_a, best_b = n, i - n, j - n
        prev = cur
    return best_len, best_a, best_b


def _is_numeral_heavy(text: str, max_ratio: float) -> bool:
    """共享串是否以数字/计量字为主（账目、日期、金额的复指，不作为节拍计）。"""
    hits = sum(1 for ch in text if ch in _NUMERAL_CHARS or ch in _MEASURE_CHARS)
    return hits / len(text) >= max_ratio


def _trigram_counts(chars: str) -> Counter[str]:
    """片段内的 3-gram **多重集**（DP 前的必要条件剪枝用）。

    用多重集而非集合：重复字构成的串（「哈哈哈哈」）只有一个**不同**的 3-gram，
    但一个有 6 字的公共子串必然贡献 4 个**带重数**的公共 3-gram——多重集哨兵
    才是严密的必要条件（用集合会漏判这类重复字串）。
    """
    return Counter(chars[i : i + 3] for i in range(len(chars) - 2))


def beat_repeats(
    prose: str,
    *,
    min_chars: int = DEFAULT_BEAT_MIN_CHARS,
    min_cover: float = DEFAULT_BEAT_MIN_COVER,
    min_gap_chars: int = DEFAULT_BEAT_MIN_GAP_CHARS,
    numeral_max_ratio: float = DEFAULT_BEAT_NUMERAL_MAX_RATIO,
) -> list[BeatRepeat]:
    """全部「同章远距小句复现」（未过章级堆积门；供抽样与阈值校准）。

    - 同一共享串只记一次（上下文取**最先**出现的那一对），``occurrences`` 记该串
      在本章出现的片段数；
    - 被更长命中串包含的短串不重复计（最大性去重）；
    - 返回顺序按**先出现位置**升序（阅读顺序，抽样打印更可读）。
    """
    clauses = _clauses(prose)
    # 3-gram 多重集：长度 ≥min_chars 的公共子串必然带来 ≥min_chars-2 个（带重数的）
    # 公共 3-gram，故「公共 3-gram 数 ≥ min_chars-2」是**必要条件**（不会漏判），
    # 却能把绝大多数无关片段对挡在 DP 之前（实测 716 片段的章里 DP 次数降到十分之一）。
    trigrams = [
        _trigram_counts(chars) if len(chars) >= min_chars else Counter()
        for _off, chars, _raw in clauses
    ]
    min_shared_trigrams = max(1, min_chars - 2)
    found: dict[str, BeatRepeat] = {}
    for i in range(len(clauses)):
        off_i, chars_i, raw_i = clauses[i]
        len_i = len(chars_i)
        if len_i < min_chars:
            continue
        tri_i = trigrams[i]
        cover_i = min_cover * len_i
        for j in range(i + 1, len(clauses)):
            off_j, chars_j, raw_j = clauses[j]
            gap = off_j - off_i
            if gap < min_gap_chars:
                # 片段按起始偏移升序 ⇒ gap 随 j 单调增，近处的跳过即可。
                continue
            len_j = len(chars_j)
            if len_j < min_chars:
                continue
            # 覆盖率门的必要条件（长度比），先做 O(1) 剪枝再跑 DP。
            if min(len_i, len_j) < min_cover * max(len_i, len_j):
                continue
            if sum((tri_i & trigrams[j]).values()) < min_shared_trigrams:
                continue
            if chars_i == chars_j:
                # 逐字相同的片段不必跑 DP（LCS 就是片段本身）。
                n, si = len_i, 0
            else:
                n, si, _sj = _longest_common_substring(chars_i, chars_j)
            if n < min_chars or n < cover_i or n < min_cover * len_j:
                continue
            shared = chars_i[si : si + n]
            if _is_numeral_heavy(shared, numeral_max_ratio):
                continue
            previous = found.get(shared)
            if previous is None:
                occurrences = sum(1 for _, chars, _ in clauses if shared in chars)
                found[shared] = BeatRepeat(
                    text=shared,
                    first=raw_i[:_SAMPLE_MAX_CHARS],
                    second=raw_j[:_SAMPLE_MAX_CHARS],
                    length=n,
                    gap=gap,
                    occurrences=occurrences,
                )
    kept = [
        hit
        for hit in sorted(found.values(), key=lambda h: (-h.length, h.gap))
        if not any(hit.text in other for other in found if other != hit.text)
    ]
    return sorted(kept, key=lambda h: h.gap)


def count_beat_repeats(prose: str, **kwargs) -> list[str]:
    """命中串原文列表（精确率抽样核查入口，与 ``count_*`` 系列同形）。"""
    return [hit.text for hit in beat_repeats(prose, **kwargs)]


def scan_beat_repeats(
    prose: str,
    *,
    min_count: int = DEFAULT_BEAT_MIN_COUNT,
    rate_per_1k: float = DEFAULT_BEAT_RATE_PER_1K,
    min_prose_chars: int = MIN_PROSE_CHARS_FOR_BEATS,
    **kwargs,
) -> list[dict]:
    """章级规则判定：命中数 ≥ ``min_count`` 且密度 > ``rate_per_1k``/千字。

    severity **恒为 warning**（提示而非闸门）——算子只看字面重合，不判断两处是否真是
    「同一节拍」；后果档由评审侧（``quality.issues`` 的确认白名单）决定，本模块
    **不含任何返回 error 的路径**。
    """
    if not prose:
        return []
    total = visible_chars(prose)
    if total < min_prose_chars:
        return []
    hits = beat_repeats(prose, **kwargs)
    if len(hits) < min_count:
        return []
    rate = len(hits) / (total / 1000.0)
    if rate <= rate_per_1k:
        return []
    return [
        {
            "rule_id": "AI-BEAT-REPEAT",
            "severity": "warning",
            "message": f"同章远距小句复现 {len(hits)} 处（例：「{hits[0].text}」）",
            "count": len(hits),
            "rate": round(rate, 2),
            "threshold": rate_per_1k,
            "samples": [hit.text for hit in hits[:5]],
            "excerpt": hits[0].sample[:120],
        }
    ]


__all__ = [
    "DEFAULT_BEAT_MIN_CHARS",
    "DEFAULT_BEAT_MIN_COUNT",
    "DEFAULT_BEAT_MIN_COVER",
    "DEFAULT_BEAT_MIN_GAP_CHARS",
    "DEFAULT_BEAT_NUMERAL_MAX_RATIO",
    "DEFAULT_BEAT_RATE_PER_1K",
    "MIN_PROSE_CHARS_FOR_BEATS",
    "BeatRepeat",
    "beat_repeats",
    "count_beat_repeats",
    "scan_beat_repeats",
]
