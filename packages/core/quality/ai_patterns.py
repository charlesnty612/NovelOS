"""AI 味 / AI 腔确定性检测（去 AI 味）——**门面**（V4.0 模块化拆分，2026-09-30）。

纯函数模块，不调用 LLM，全部基于正则与统计。规则对标 oh-story 项目的
``check-ai-patterns.js`` 思路，针对中文长篇小说的章节审校场景做了本地化扩展。

主要 API：
- :func:`scan_ai_patterns`：扫描正文，返回命中规则列表。
- :data:`AI_PATTERN_RULES` / :data:`AI_PATTERN_FORBIDDEN_WORDS`：模块级规则数据。

本文件原为 1457 行单文件，现按算法族拆为 4 个子模块，本文件只留「规则表 +
唯一入口 + 再导出」：

- :mod:`ai_patterns_text`      —— 段切分口径（``\n+``）与最小判定长度（多族共用）
- :mod:`ai_patterns_sentence`  —— 禁用词 / 三连句式 / 代词堆砌 / 标点滥用 / 解释腔 / 对举
- :mod:`ai_patterns_paragraph` —— 短段 / 长段 / 拟人化喻体
- :mod:`ai_patterns_dialogue`  —— 对白可见字占比 / 对白回声对

**深路径 import 行为逐字不变**：公共项在 ``__all__``（27 项，与拆分前逐项相同）；
各族的**被测算子与阈值**（``count_*`` / ``_scan_*`` / ``DEFAULT_*`` / ``_MIN_*``
/ ``DialogueEchoPair`` 等）由本门面一并再导出，既有测试与打点无需改写。

⚠️ **再导出的边界（2026-10-13 收尾复核实测）**：句式族有 5 个**纯实现细节**
（``_WEAK_WORD_PER_WORD_LIMIT`` / ``_WEAK_WORD_TOTAL_LIMIT`` /
``_AI_EXPLAIN_PATTERNS`` / ``_PRONOUN_START_RE`` / ``_DASH_OR_ELLIPSIS_RE``）
**不在门面**——它们只被 :mod:`ai_patterns_sentence` 自身消费，全仓零外部引用
（74 处深路径 import 逐个 ``hasattr`` 实测 0 失败），故不进门面契约；
``re`` / ``Counter`` / ``visible_chars`` 三个 import 同理。需要它们请从
:mod:`ai_patterns_sentence` 取，不要指望门面再导出。

⚠️ ``_last_paragraph`` 与 ``_scan_ending_summary`` **留在门面**：后者是前者的唯一
调用方，一并留下以免反向 import 成环。

**2026-10-15 批次（六）G8：``_last_paragraph`` 的口径已收敛到 ``_split_paragraphs``。**
它此前内联第二份按空行切分的实现（2026-09-27 留档待排期的那个），现已复用模块内
唯一切段入口——单换行语料上不再把整章当「末段」扫。实测数据与「为何不设阈值」见
该函数 docstring。⚠️ **连带待办**（本轮铁律「文件互斥」内未做，须另行指派）：
``tests/unit/quality/test_readability_rules.py`` 的形状级断言原写「内联段落切分应
只剩 ``_last_paragraph`` 一处」（``len(inline_splits) == 1``），收敛后应为 **0**，
该断言需随之订正；同批 ``ai_patterns_text.py`` 与 ``ai_patterns_sentence.py`` 里
「仍内联第二份实现 / 已登记为待修」的说明文字也已失准。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from packages.core.quality.beat_repeat import (
    DEFAULT_BEAT_MIN_COUNT,
    DEFAULT_BEAT_RATE_PER_1K,
    scan_beat_repeats,
)
from packages.core.quality.continuity_time import (
    DEFAULT_MAX_GAP_CHARS,
    DEFAULT_MIN_BACKSTEP_HOURS,
    scan_time_conflicts,
)

# --- 子模块再导出（V4.0 拆分；门面契约：深路径 import 逐字不变）----------------
from .ai_patterns_dialogue import (  # noqa: F401
    _DIALOGUE_ECHO_ASYMMETRY_LIMIT,
    _DIALOGUE_PAIR_RE,
    _ECHO_SAMPLE_MAX_CHARS,
    _MIN_DIALOGUE_ECHO_CHARS,
    _MIN_DIALOGUE_ECHO_COMPARABLE_CHARS,
    _SUBSTANTIVE_CHAR_RE,
    DEFAULT_DIALOGUE_ECHO_MIN_OPENING,
    DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP,
    DEFAULT_DIALOGUE_ECHO_MIN_RATIO,
    DEFAULT_DIALOGUE_LOW_WARN_RATIO,
    DialogueEchoPair,
    _dialogue_lines,
    _echo_ratios,
    _opening_repeat_len,
    _scan_dialogue_echo,
    _scan_dialogue_low,
    _substantive_chars,
    count_dialogue_echoes,
    count_dialogue_visible_chars,
    dialogue_echo_pairs,
    dialogue_ratio,
)
from .ai_patterns_paragraph import (  # noqa: F401
    _ANAPHORA_RE,
    _ANTHRO_ROLE_WORDS,
    _ANTHRO_VEHICLE_RE,
    _DIALOGUE_START_RE,
    _PARA_OPENER_MAX_CHARS,
    _PERSONAL_START_RE,
    _SPEAKER_LINE_RE,
    DEFAULT_ANTHRO_VEHICLE_MIN_COUNT,
    DEFAULT_LONG_PARA_ERROR_CHARS,
    DEFAULT_LONG_PARA_MIN_COUNT,
    DEFAULT_LONG_PARA_WARN_CHARS,
    DEFAULT_SHORT_PARA_RATE_PER_1K,
    _scan_anthro_vehicle,
    _scan_long_para,
    _scan_short_para,
    count_anthro_vehicles,
    count_long_paragraphs,
    count_short_paras,
)
from .ai_patterns_sentence import (  # noqa: F401
    _CONTRAST_PAIR_RE,
    _SEVERITY_UPGRADE_LIMITS,
    AI_PATTERN_FORBIDDEN_WORDS,
    AI_PATTERN_HARD_CLICHES,
    AI_PATTERN_WEAK_WORDS,
    DEFAULT_CONTRAST_PAIR_RATE_PER_1K,
    DEFAULT_DASH_THRESHOLD_PER_1K,
    _leading_chinese_bigram,
    _maybe_upgrade_severity,
    _scan_contrast_pair,
    _scan_explain_tone,
    _scan_forbidden_words,
    _scan_pronoun_pile,
    _scan_punct_abuse,
    _scan_triplet_opening,
    _sentences,
    count_contrast_pairs,
)
from .ai_patterns_text import (  # noqa: F401
    _MIN_PROSE_CHARS_FOR_DENSITY,
    _MIN_PROSE_CHARS_FOR_READABILITY,
    _split_paragraphs,
)

# 章尾总结/升华体套话（只在最后一段检测）
_AI_ENDING_SUMMARY_PHRASES: list[str] = [
    "这一刻",
    "从此以后",
    "新的篇章",
    "命运的齿轮",
    "故事的结局",
    "画上了句号",
    "落下了帷幕",
    "一切都结束了",
    "新的旅程",
    "未来可期",
    "时光荏苒",
    "岁月如梭",
]


@dataclass(frozen=True)
class AiPatternRule:
    """单条规则元数据，用于文档化与可扩展配置。"""

    rule_id: str
    severity: str  # 'warning' | 'error'，默认等级
    message: str
    description: str = ""

AI_PATTERN_RULES: list[AiPatternRule] = [
    AiPatternRule(
        rule_id="AI-FORBIDDEN-WORD",
        severity="warning",
        message="AI 高频套话/禁用词命中：{words}",
        description="检测仿佛、宛如、不禁、骤然、与此同时、值得一提的是、总而言之、空气中弥漫着等高频 AI 腔词。",
    ),
    AiPatternRule(
        rule_id="AI-TRIPLET-OPENING",
        severity="warning",
        message="连续 {count} 句以相同两字词「{word}」开头，疑似句式套路",
        description="连续 3 句及以上以同一个两字词开头。",
    ),
    AiPatternRule(
        rule_id="AI-PRONOUN-PILE",
        severity="warning",
        message="「他/她」主语排比堆砌 {count} 句",
        description="连续 3 句及以上以「他」或「她」开头，提示主语单调。",
    ),
    AiPatternRule(
        rule_id="AI-ENDING-SUMMARY",
        severity="warning",
        message="章尾出现总结/升华体套话：{words}",
        description=(
            "结尾段含这一刻、从此以后、新的篇章、命运的齿轮等。段切分口径 2026-10-15 "
            "由「按空行」收敛到模块内唯一的 ``\\n+`` 入口（单换行语料上旧口径把整章"
            "当末段扫，人类锚点书 19 章实测「末段」均值 2030 字 ≈ 整章，收敛后 41 字）。"
            "**未校准**：实测两侧皆零命中——12 条短语在人类锚点 19 章**全章零出现**，"
            "生成侧全库 97 章合计仅 1 次（「这一刻」且不在末段）；阳性对照证明算子本身"
            "能匹配，故 0 是「真没有」而非「测不到」。**本规则当前在真实语料上从不触发"
            "（死规则），且无可测分布故不设阈值**（分位数为 0 ⇒ 阈值不变式无解，"
            "任何非零值都是凭空造的数）。阈值与数据来源见 ``_last_paragraph`` docstring。"
        ),
    ),
    AiPatternRule(
        rule_id="AI-PUNCT-ABUSE",
        severity="warning",
        message="破折号/省略号滥用：每千字 {rate:.1f} 处（阈值 {threshold}）",
        description=(
            "每千字「——」或「……」超过阈值（默认 1.0）。2026-09-16 按人类基线书"
            "（榜一侯府弧 0.19/千字，我们 1.73/千字）由 3.5 收紧——见常量注释。"
        ),
    ),
    AiPatternRule(
        rule_id="AI-EXPLAIN-TONE",
        severity="warning",
        message="解释腔高密度：{count} 处（{labels}）",
        description="「因为……所以……」「换句话说」「也就是说」等解释连接词高频出现。",
    ),
    AiPatternRule(
        rule_id="AI-CONTRAST-PAIR",
        severity="warning",
        message="对举结构「不是……而是……」密度偏高：每千字 {rate:.1f} 处（阈值 {threshold}）",
        description="外部对照研究 R=3.4（生成侧 0.73 / 千字，人类侧 0.22）。按密度报警。",
    ),
    AiPatternRule(
        rule_id="AI-SHORT-PARA",
        severity="warning",
        message="短句独立成段密度偏高：每千字 {rate:.1f} 处（阈值 {threshold}）",
        description=(
            "段落由一个 ≤12 字短句构成（无回指词、非对白含 `名字：` 说话人行、"
            "非人称起首），即「节拍器式」短段行文。人类基线书榜一侯府弧 "
            "**0.212/千字**（19 章 8 命中 / 6 章有命中 / 1 章超阈——该章 3 条命中"
            "全是非散文行，散文口径 0.132/千字 0 章超阈），我们新书 01 全弧"
            "**2.080/千字**（48 章 260 命中），分离度 9.8×，阈值 1.0；"
            "2026-09-27 修正段切分口径——此前登记的「人类 0.00」是 ``\\n\\n`` 切分"
            "造成的量具假象，见常量注释。取自来源研究「段首零回指评论」的可正则化"
            "近似，抽样核实后按实际覆盖范围改名。"
        ),
    ),
    AiPatternRule(
        rule_id="AI-ANTHRO-VEHICLE",
        severity="warning",
        message="拟人化喻体（职业/角色类）同章 {count} 处",
        description=(
            "休眠守卫，**未校准**：来源研究 R=7.3（生成侧偏好理想化职业人格作喻体），"
            "但本仓生成侧与人类侧均零命中，无数据可校。正则亦无法区分研究指出的"
            "「理想化职业人格」（生成侧）与「具体个人」（人类侧，如「像一个老师傅」），"
            "故仅按同章次数报警、不做单例判定；一旦命中即为新信号。"
        ),
    ),
    AiPatternRule(
        rule_id="AI-LONG-PARA",
        severity="warning",
        message="长段堆积：单章 {count} 段超过 {warn_chars} 可见字（最长 {max_chars} 字）",
        description=(
            "单段可见字超限（手机上一段 5~6 行实心字，可读性头号杀手）：>100 字计入长段，"
            "单章 ≥2 段报警（人类基线书每章至多 1 段）；任一段 >140 字直接 error"
            "（人类基线书 779 段中零出现）。阈值与依据见模块内常量注释。"
        ),
    ),
    AiPatternRule(
        rule_id="AI-DIALOGUE-LOW",
        severity="warning",
        message="对话占比过低：{dialogue_ratio:.1%}（{dialogue_chars}/{total_visible_chars} 可见字）",
        description=(
            "对话（成对“…”内的可见字）占全章可见字比例 <12% → **warning（唯一档位）**；"
            "人类基线书 16.5%、番茄男频主流 25~40%、本仓题材包区间 10~32%。"
            "可见字 <600 的片段不判（统计无意义）。**2026-09-17 撤回 <8% error 档**："
            "对话占比是可优化指标，逼模型凑数会产出灌水对白；低对话章合法（人类锚点"
            "首章 0.0% 整章无对话）。本规则只作提示、不作闸门，绝不进 errors 桶——"
            "症状探针见 AI-DIALOGUE-ECHO。阈值与依据见模块内常量注释。"
        ),
    ),
    AiPatternRule(
        rule_id="AI-DIALOGUE-ECHO",
        severity="warning",
        message="相邻对白接词回声 {count} 对（例：{sample}）",
        description=(
            "相邻两句对白之间「后句接前句的词」——复述型（后句实义字 ≥70% 出现在前句）"
            "或接词型（两侧实义字可比、开口连续重合 ≥2 字、重叠系数 ≥0.66）。"
            "实测：本仓 48 章 34 对 / 24 章命中（重叠系数可比性守卫生效后 23 对 / "
            "18 章），人类锚点书 19 章 1 对（信息递增但用词撞车，合法写法）。"
            "算子只看字面重合，**不判断对白是否有后果**，故 severity 恒 warning、附 "
            "samples 供人工过目。阈值与依据见模块内常量注释。"
        ),
    ),
    AiPatternRule(
        rule_id="AI-BEAT-REPEAT",
        severity="warning",
        message="同章远距小句复现 {count} 处（例：「{sample}」）",
        description=(
            "**字面代理**算子（名字里的「节拍重播」是它代理的对象，不是它测量的东西）："
            "句读片段之间**最长公共子串** ≥6 字、同时覆盖两段片段各自 ≥60% 字面、两处"
            "相距 ≥200 字——即同一段文字素材在章内被搬用了两次以上。补的缺口是"
            "**shingle / trigram 只抓复制粘贴、抓不到「同一节拍换一套词重播」**"
            "（ch2 章尾收据画面写了三遍，shingle 比 0.0030 判为干净）。"
            "实测：本库 92 章 307 处（1.17/千字）；人类锚点书 19 章 2 处（0.05/千字），"
            "**没有一章**达到堆积门 ≥2 处。系统抽样 52 条人工过目：43 真 / 9 误，"
            "误报为「专名/术语复指」与「共用骨架 + 不同宾语」两类。"
            "**不覆盖**：语义改写型重复（字面零重合）、数字/计量串复现（数字守卫显式"
            "排除）、跨章复现、宏观结构重复。severity 恒 warning（提示非闸门）。"
            "阈值与依据见 beat_repeat 模块 docstring 与常量注释。"
        ),
    ),
    # -----------------------------------------------------------------------
    # 2026-09-19：章内时点矛盾（continuity_time 模块；本组**不是 AI 味**，是连续性）
    # -----------------------------------------------------------------------
    # 由来：``guardrails.timeline_consistency`` 只校验 state delta（时间线状态机），
    # 全仓没有任何算子读**正文里的时间标记**——真实成书里「凌晨两点十七分」开场、
    # 同场景不隔断地写到「天际线已经有一层淡淡的亮了」，一条规则都没报。
    # 两条规则都走本表是为了复用**既有接线**（basic_checks → review_report.warnings
    # 与 deterministic_hints → critic payload），rule_id 用独立命名空间 ``CONT-``
    # 标明它不是 AI 味算子。severity 恒 warning、不含 error 路径。
    # 校准证据（92 章 / 人类基线 19 章逐条抽样表）见 continuity_time 模块 docstring。
    AiPatternRule(
        rule_id="CONT-CLOCK-DAYBREAK",
        severity="warning",
        message="深夜时钟与天亮标记矛盾：{count} 处（例：{sample}）",
        description=(
            "**字面**算子：深夜档钟点（前缀 凌晨/半夜/深夜/夜半 + 钟点，如「凌晨两点"
            "十七分」）与其后**同章**的天亮类标记（天亮/破晓/黎明/晨光/晨曦/天色泛白/"
            "清晨/早上…）配对，间距 ≤3000 字且中间**无时间推进标记**即报。"
            "它量的不是「叙事时间是否合理」，而是**两个字面标记之间的直接冲突**。"
            "**不覆盖**：时长合理性、倒叙/回忆（只靠黑名单近似排除，故回述型会漏）、"
            "无字面标记的场景、裸钟点（本仓实证「三点二十六分」= 下午 15:26，故不判早晚）。"
            "实测：生成侧 1 处 / 92 章（真命中 ch_2d2b57090ecd）；人类基线书 19 章"
            "**零个钟点标记**，机会数为 0，故人类侧数字不含信息、不可作零误报证据。"
        ),
    ),
    AiPatternRule(
        rule_id="CONT-TIME-BACKSTEP",
        severity="warning",
        message="同章相邻时点回退：{count} 处（例：{sample}）",
        description=(
            "**字面**算子：同章**相邻**两个可排序时点（钟点按前缀折算为一天内小时；"
            "时辰按起始小时折算：午时 11:00 / 亥时 21:00），后者比前者早 ≥2 小时即报。"
            "三条守卫：跨午夜顺行不算（23:47 → 次日 03:42）、左邻计划语不算"
            "（「明天辰时」）、右邻期限语不算（「子时之前」「子时将近」）。"
            "**不覆盖**：无字面标记的场景、裸钟点（歧义）、跨章时间线、时长合理性。"
            "实测：原始 8 条候选中 7 条是误报（上列三类守卫正是抽样抓出来的），"
            "加守卫后生成侧 1 处（真命中 prj_5be9256febad ch6：亥时 → 午时无交代回退）；"
            "人类侧 0 处且机会数为 0（基线书仅 2 处「时辰」，都不是时点）。"
        ),
    ),
]

def _last_paragraph(prose: str) -> str:
    """取正文最后一段——**走模块内唯一切段入口** ``_split_paragraphs``（``\\n+``）。

    2026-10-15 批次（六）G8 收敛。此前本函数**内联第二份**按空行切分的实现，
    与 :mod:`ai_patterns_text` 的 ``\\n+`` 口径分叉——正是 2026-09-27「人类基线
    0.00 是量具假象」事故的**同一条**，只是发生在第二个算子上（当时只修了短段族，
    本函数留档待排期）。

    **实测（2026-10-15，入口 :func:`scan_ai_patterns` 逐章复算）**
    --------------------------------------------------
    语料：人类侧 = NovelOS-Content 榜一侯府弧 19 章 / 37806 可见字；
    生成侧 = dev 库 ``data/novelos.db`` 全库 97 章 / 276500 可见字（各章最新版）。

    ======================= ============== ============== ==================
    侧   口径                命中章 / 章数   命中短语数     「末段」返回长度均值
    ======================= ============== ============== ==================
    人类 旧（按空行）          0 / 19       0            **2030 字**（= 整章）
    人类 收敛后（``\\n+``）      0 / 19       0            **41 字**（真末段）
    生成 旧（按空行）          0 / 97       0            24 字
    生成 收敛后（``\\n+``）      0 / 97       0            24 字（逐字相同）
    ======================= ============== ============== ==================

    两处要点：

    ① **人类语料上旧口径把整章当成「末段」在扫**——该语料一行一段，``\\n\\n``
       总数 = 0，故旧实现的「末段」均值 2030 字 ≈ 整章。生成侧 97/97 章都有空行，
       两口径**逐字相同**，收敛对生成侧零影响。修复面只在单换行语料（外部锚点书、
       CRLF 文本、部分导入稿）。
    ② **两侧命中都是 0，而 0 是「真没有」不是「测不到」**：12 条短语在人类 19 章
       **全章零出现**；生成侧 97 章合计只出现 **1 次**（「这一刻」，且不在末段），
       被收敛后的正确口径拒之。阳性对照：合成末段含「从此以后 / 一切都结束了」
       时算子照常命中——即量具是活的。

    **为什么不给本规则设阈值（登记，不是遗漏）**
    ----------------------------------------------
    实测两侧皆 0 ⇒ **分位数为 0，人类侧上沿也是 0** ⇒ 「人类侧上沿 < warn 阈值 <
    生成侧上沿」在 0.0 < x < 0.0 上**无解**。按坑区「阈值只取本仓实测分布」与
    「阈值落在实测分布之外 = 死规则」，此处**任何非零阈值都是凭空造的数**，故
    保持二元判定（末段含任一条短语即 warning）不动。
    **真实结论是本规则当前在真实语料上从不触发（死规则）**——若将来任一语料出现
    命中，须同时重测两条分布再谈定阈值，不得只改阈值。
    """
    paragraphs = _split_paragraphs(prose)
    return paragraphs[-1] if paragraphs else prose

def _scan_ending_summary(prose: str) -> list[dict[str, Any]]:
    """章尾总结/升华体套话检测。"""
    last_para = _last_paragraph(prose)
    if not last_para:
        return []
    words_found: list[str] = []
    for phrase in _AI_ENDING_SUMMARY_PHRASES:
        if phrase in last_para and phrase not in words_found:
            words_found.append(phrase)
    if not words_found:
        return []
    return [
        {
            "rule_id": "AI-ENDING-SUMMARY",
            "severity": "warning",
            "message": f"章尾出现总结/升华体套话：{','.join(words_found)}",
            "count": len(words_found),
            "words": words_found,
            "excerpt": last_para[-160:],
        }
    ]

def _scan_beat_repeat(
    prose: str,
    *,
    min_count: int = DEFAULT_BEAT_MIN_COUNT,
    rate_per_1k: float = DEFAULT_BEAT_RATE_PER_1K,
) -> list[dict[str, Any]]:
    """同章远距小句字面复现（``AI-BEAT-REPEAT``）。

    机制、校准证据、**不覆盖范围**全部在 :mod:`packages.core.quality.beat_repeat`
    模块 docstring 里（本函数只是把该模块的章级判定接进统一扫描入口）。
    severity 恒 warning——它是提示不是闸门，不含 error 路径。
    """
    return scan_beat_repeats(prose, min_count=min_count, rate_per_1k=rate_per_1k)

def _scan_time_conflicts(
    prose: str,
    *,
    max_gap_chars: int = DEFAULT_MAX_GAP_CHARS,
    min_backstep_hours: float = DEFAULT_MIN_BACKSTEP_HOURS,
) -> list[dict[str, Any]]:
    """章内时点矛盾（``CONT-CLOCK-DAYBREAK`` / ``CONT-TIME-BACKSTEP``）。

    机制、校准证据、**不覆盖范围**全部在
    :mod:`packages.core.quality.continuity_time` 模块 docstring 里（本函数只是把该
    模块的章级判定接进统一扫描入口——**不是 AI 味规则**，rule_id 用 ``CONT-`` 前缀
    与 AI 味算子区分）。severity 恒 warning，不含 error 路径。
    """
    return scan_time_conflicts(
        prose, max_gap_chars=max_gap_chars, min_backstep_hours=min_backstep_hours
    )


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------


def scan_ai_patterns(
    prose: str,
    *,
    dash_threshold_per_1k: float = DEFAULT_DASH_THRESHOLD_PER_1K,
    contrast_pair_threshold_per_1k: float = DEFAULT_CONTRAST_PAIR_RATE_PER_1K,
    short_para_threshold_per_1k: float = DEFAULT_SHORT_PARA_RATE_PER_1K,
    anthro_vehicle_min_count: int = DEFAULT_ANTHRO_VEHICLE_MIN_COUNT,
    long_para_warn_chars: int = DEFAULT_LONG_PARA_WARN_CHARS,
    long_para_error_chars: int = DEFAULT_LONG_PARA_ERROR_CHARS,
    long_para_min_count: int = DEFAULT_LONG_PARA_MIN_COUNT,
    dialogue_low_warn_ratio: float = DEFAULT_DIALOGUE_LOW_WARN_RATIO,
    dialogue_echo_min_ratio: float = DEFAULT_DIALOGUE_ECHO_MIN_RATIO,
    dialogue_echo_min_overlap: float = DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP,
    dialogue_echo_min_opening: int = DEFAULT_DIALOGUE_ECHO_MIN_OPENING,
    beat_min_count: int = DEFAULT_BEAT_MIN_COUNT,
    beat_rate_per_1k: float = DEFAULT_BEAT_RATE_PER_1K,
    max_gap_chars: int = DEFAULT_MAX_GAP_CHARS,
    min_backstep_hours: float = DEFAULT_MIN_BACKSTEP_HOURS,
) -> list[dict[str, Any]]:
    """扫描正文中的 AI 味/AI 腔模式。

    返回命中列表，每条包含：
      - rule_id: 规则标识
      - severity: 'warning' | 'error'
      - message: 人类可读摘要
      - count?: 命中次数
      - excerpt?: 正文片段（用于定位）
      - 以及规则相关额外字段（words / word / rate / labels / samples 等）

    全部为确定性正则/统计规则，不调用 LLM。

    阈值均为关键词参数，便于按语料/体裁重新校准（本地校准入口：
    ``scripts/ai_tone_calibrate.py``）；不传即用模块默认值，行为稳定。
    """
    if not prose:
        return []
    hits: list[dict[str, Any]] = []
    hits.extend(_scan_forbidden_words(prose))
    hits.extend(_scan_triplet_opening(prose))
    hits.extend(_scan_pronoun_pile(prose))
    hits.extend(_scan_ending_summary(prose))
    hits.extend(_scan_punct_abuse(prose, threshold_per_1k=dash_threshold_per_1k))
    hits.extend(_scan_explain_tone(prose))
    hits.extend(_scan_contrast_pair(prose, threshold_per_1k=contrast_pair_threshold_per_1k))
    hits.extend(_scan_short_para(prose, threshold_per_1k=short_para_threshold_per_1k))
    hits.extend(_scan_anthro_vehicle(prose, min_count=anthro_vehicle_min_count))
    hits.extend(
        _scan_long_para(
            prose,
            warn_chars=long_para_warn_chars,
            error_chars=long_para_error_chars,
            min_count=long_para_min_count,
        )
    )
    hits.extend(
        _scan_dialogue_low(prose, warn_ratio=dialogue_low_warn_ratio)
    )
    hits.extend(
        _scan_dialogue_echo(
            prose,
            min_ratio=dialogue_echo_min_ratio,
            min_overlap=dialogue_echo_min_overlap,
            min_opening=dialogue_echo_min_opening,
        )
    )
    hits.extend(_scan_beat_repeat(prose, min_count=beat_min_count, rate_per_1k=beat_rate_per_1k))
    hits.extend(
        _scan_time_conflicts(
            prose, max_gap_chars=max_gap_chars, min_backstep_hours=min_backstep_hours
        )
    )
    return hits

__all__ = [
    "AI_PATTERN_FORBIDDEN_WORDS",
    "AI_PATTERN_HARD_CLICHES",
    "AI_PATTERN_RULES",
    "AI_PATTERN_WEAK_WORDS",
    "DEFAULT_ANTHRO_VEHICLE_MIN_COUNT",
    "DEFAULT_CONTRAST_PAIR_RATE_PER_1K",
    "DEFAULT_DASH_THRESHOLD_PER_1K",
    "DEFAULT_DIALOGUE_ECHO_MIN_OPENING",
    "DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP",
    "DEFAULT_DIALOGUE_ECHO_MIN_RATIO",
    "DEFAULT_DIALOGUE_LOW_WARN_RATIO",
    "DEFAULT_LONG_PARA_ERROR_CHARS",
    "DEFAULT_LONG_PARA_MIN_COUNT",
    "DEFAULT_LONG_PARA_WARN_CHARS",
    "DEFAULT_MAX_GAP_CHARS",
    "DEFAULT_MIN_BACKSTEP_HOURS",
    "DEFAULT_SHORT_PARA_RATE_PER_1K",
    "DialogueEchoPair",
    "count_anthro_vehicles",
    "count_contrast_pairs",
    "count_dialogue_echoes",
    "count_dialogue_visible_chars",
    "count_long_paragraphs",
    "count_short_paras",
    "dialogue_echo_pairs",
    "dialogue_ratio",
    "scan_ai_patterns",
]
