"""AI 味/AI 腔确定性检测（去 AI 味）。

纯函数模块，不调用 LLM，全部基于正则与统计。规则对标 oh-story 项目的
``check-ai-patterns.js`` 思路，针对中文长篇小说的章节审校场景做了本地化扩展。

主要 API：
- :func:`scan_ai_patterns`：扫描正文，返回命中规则列表。
- :data:`AI_PATTERN_RULES` / :data:`AI_PATTERN_FORBIDDEN_WORDS`：模块级规则数据，便于扩展。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from packages.core.quality.wordcount import visible_chars

# ---------------------------------------------------------------------------
# 模块级规则数据（易扩展）
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 禁用词两层制（2026-09-15 分层重构）
# ---------------------------------------------------------------------------
# 起因：改造前是**一张「命中即报」的表**，结果 67% 的章节被报——实测是
# **忽然**（0.24 vs 人类 0.20 /千字，R=1.18 无区分力）与**突然**（0.19 vs 0）
# 两个中文小说正常用词在撑起全部噪声，而 19 个词里有 15 个两侧都近乎零命中。
# 同一形状（规则宣称的覆盖面 ≠ 实测覆盖面）的第三例，故按实测分层、分层给判定。

# 第一层：硬套话——公文/说明文腔与计划泄漏，本题材内没有正当用法，**命中即报**。
AI_PATTERN_HARD_CLICHES: list[str] = [
    "本章目标",          # plan_json 文本泄漏进正文（硬错误，不属风格问题）
    "与此同时",
    "值得一提的是",
    "总而言之",
    "总的来说",
    "综上所述",
    "空气中弥漫着",
    "冥冥之中",
]

# 第二层：常用叙述弱词——中文小说的正常用词，实测**无区分力或人类侧更高**
# （忽然 R=1.18 · 好像 人类 0.20 对生成 0.01）。不做「命中即报」，
# 只在**同章堆积**时报警：单词 ≥3 次，或本层合计 ≥6 次。
AI_PATTERN_WEAK_WORDS: list[str] = [
    "仿佛",
    "宛如",
    "如同",
    "似乎",
    "好像",
    "不禁",
    "骤然",
    "猛然",
    "忽然",
    "突然",
]

# 向后兼容：并集。既有调用方（chapter_review 的 forbidden_word_hits、题材包禁词
# 扫描、polisher 预检）按整表读，语义不变；变的是**触发条件**（weak 层需堆积）。
AI_PATTERN_FORBIDDEN_WORDS: list[str] = (
    AI_PATTERN_HARD_CLICHES + AI_PATTERN_WEAK_WORDS
)

# 弱词层堆积阈值（同章内）。
_WEAK_WORD_PER_WORD_LIMIT = 3
_WEAK_WORD_TOTAL_LIMIT = 6

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

# 解释腔连接词/句式
_AI_EXPLAIN_PATTERNS: list[tuple[str, str | None]] = [
    # (regex, 简化标签)
    (r"因为[^。]{0,30}所以", "因为……所以……"),
    (r"换句话说", None),
    (r"也就是说", None),
    (r"换言之", None),
    (r"究其原因", None),
    (r"这意味着", None),
    (r"不难发现", None),
    (r"众所周知", None),
]

# 破折号阈值。沿革（每次收紧都留痕）：6（初版）→ 3.5（2026-09-15 按本仓旧语料
# p85；原值 6 是死规则——本仓实测 max 4.73 都够不到）→ **1.0（2026-09-16 按人类
# 基线书收紧）**。
# 依据（2026-09-16 实测，复算入口 scripts/ai_tone_calibrate.py）：文风锚点书榜一
# 《快穿之人渣洗白手册》侯府弧 19 章 / 37806 可见字 **0.19/千字**，而我们新书 01
# 全弧 63625 字 **1.73/千字**（R=9.34）。3.5 落在两者**之外**——连我们自己的均值
# 都够不到，即 AGENTS.md 点名的「阈值落在实测分布之外 = 死规则」形状；而真正的
# 达标线接近零。取 1.0：对榜一基线留约 5 倍宽容（0.19×5≈0.95），对 1.73 立即报警。
# 判别：tests/unit/quality/test_human_baseline_ai_thresholds.py（改回 3.5 必红）。
# 语义为 float（本常量旧注解误写 int，2026-09-16 一并订正，与短段常量同款）。
DEFAULT_DASH_THRESHOLD_PER_1K = 1.0

# ---------------------------------------------------------------------------
# 2026-09-15：外部对照研究移植的四条算子
# ---------------------------------------------------------------------------
# 来源：lieflat-less-ai-tone（283 万字对照语料 / 629 篇 / 26 项候选特征）——
# 11 项通过检验（判定标准 R = 生成侧频率 ÷ 人类侧频率 ≥ 1.25），本组取其中
# 四项可正则化者。该研究同时证否了三项流行认知（比喻 / 正文设问 / 句长均匀度
# 均非生成侧偏高，其中设问方向相反：人类是生成侧的 17 倍），故本组不扩禁用词表。
#
# 本仓实测（33 章 / 87,599 字 生成侧 vs 4,843 字 人类样章；口径与来源同）：
#   破折号 R=2.83（研究 3.0）· 对举结构 0.76/千字（研究生成侧 0.73）·
#   段首短句 R=6.03（研究 4.4）——三项在本域（中文长篇网文）方向一致。
#
# 方法纪律（对齐来源研究的操作规程，亦与本仓「声称与实测分离」同源）：
# 算子在采信频率前**必须抽样检视命中实例**——正则匹配字面形态而不解析语义，
# 算子过宽会把正常写法计入。精确率核查入口：``scripts/ai_tone_calibrate.py``。
# 该研究公开的六次测量失误全部是「算子覆盖范围宽于规则名称」这一形状。

# 对举结构「不是 A 而是/是 B」——研究 R=3.4。人类侧亦有（0.22/千字），
# 故按**密度**报警而非单例判定。阈值取本仓实测值（0.76/千字）约两倍。
DEFAULT_CONTRAST_PAIR_RATE_PER_1K = 1.5

# 短句独立成段（原拟名「段首零回指评论」，抽样核实后改名——见下）。
# 来源研究区分力最强项 R=4.4 是「段首直接给评价/结论而不标示评价对象」；
# 本仓的可正则化近似（段首 ≤12 字、无回指词、非对话、非人称起首）**抽 12 条
# 命中逐条看**：命中是「林策开口了」「万卷阁开了」「三天」这类**叙述短句独立成段**，
# 不是评价性评论；人类样章同样有该写法。故按算子**实际覆盖范围**改名登记，
# 不冒用来源特征名（该研究公开的六次失误全是这个形状）。
# 本仓实测（**算子修正后重测**）：生成侧 2.63/千字 vs 人类样章 0.81/千字（R=3.25，
# 方向与来源研究一致）；逐章分布 均 2.65 / p75 3.26 / p90 5.47 / max 7.63。
# 阈值沿革：5.5（2026-09-15，取本仓**旧语料**逐章 p90）→ **1.0（2026-09-16 按人类
# 基线书收紧）**。依据（2026-09-16 实测）：文风锚点书榜一侯府弧 19 章 / 37806 可见字
# **0.00/千字**（全弧一次都没出现），我们新书 01 全弧 63625 字 **2.62/千字**——5.5
# 落在两者之外（生成侧 p90 5.47 都够不到），即「阈值落在实测分布之外 = 死规则」。
# 取 1.0：对「人类基线为 0」留有限宽容（约每千字 1 处），对生成侧 2.62 立即报警。
# 判别：tests/unit/quality/test_human_baseline_ai_thresholds.py（改回 5.5 必红）。
# （初版算子只看段首句，把「灯芯闪了一下。苏婉清没有出声…」也算成短段，
#   分布虚高到均 5.06；修正为「整段就是那一句短句」后按新分布重定阈。）
DEFAULT_SHORT_PARA_RATE_PER_1K = 1.0

# 拟人化喻体（以职业/角色名词作喻体）——研究 R=7.3。
# **精确率警告**：正则分不出研究指出的真正差异——生成侧偏好「理想化的职业人格」
# （像一位智慧的导师），人类侧偏好「具体个人」（像一个老师傅）；二者字面同形。
# 故只在**同章出现 ≥N 次**时报警，不做事例级判定。
DEFAULT_ANTHRO_VEHICLE_MIN_COUNT = 3

# 译文句式：**不迁移**（2026-09-15 抽样核查后的裁决）。
# 来源研究在说明文语料上得 R=2.6~5.3，但本仓实测：生成侧 0.59/千字 vs
# 人类 1.15/千字（R=0.49，**方向相反**）；且「过长前置定语」算子抽 97 条命中，
# 95 条是误报（「看着两人的背影消失在堂后的门槛里。」根本不是长定语）。
# 精确率不合格 + 无区分力 → 两条都不满足，按纪律废弃，不保留死代码。
# 这条同时是「来源研究的结论不能整体照搬」的实证：体裁差异真实存在。

# ---------------------------------------------------------------------------
# 2026-09-17：可读性算子（长段 / 对话占比过低）
# ---------------------------------------------------------------------------
# 来源：书 prj_bcb9d1930bd4 全弧 48 章 / 122350 可见字 的读者反馈「可读性差」，
# 与文风锚点书榜一《快穿之人渣洗白手册》侯府弧 19 章 / 37806 可见字 逐指标对照
# （复算入口 scripts/readability_audit.py，两处数字均由该脚本只读复现）：
#
#   指标                     榜一（人类基线）        我们（prj_bcb9d1930bd4）
#   单段 >100 可见字段落      7 段（779 段的 0.9%）   133 段（3061 段的 4.3%）
#   单段 >140 可见字段落      0 段                    16 段（最长 212 字）
#   每章 >100 字段落数        至多 1 段（19 章里 7 章） 最多 9 段（ch30）
#   对话占比（引号内可见字）   16.5%（6229/37806）     11.4%（13941/122350）
#
# 「单段 >120 字」是手机阅读的头号杀手（一段 5~6 行实心字），改造前无任何算子看守。
# 段切分口径见 :func:`_readability_paragraphs`（``\n+``——外部锚点书是一行一段）。

# 长段阈值：单段 >100 可见字 → warning 档；>140 → error 档。
# **堆积门**（照弱词层 / 拟人喻体「人类侧同样有的写法不做命中即报」纪律）：
# 人类基线每章至多 1 段超 100 字（7/19 章各 1 段，最长 135 字）——单段超长是正常
# 写作，故 warning 要求**单章 ≥2 段**；而 >140 字段落在榜一 779 段里零出现，
# 单段即判 error（我们 16 段、最长 212 字，是真正的排版病）。
DEFAULT_LONG_PARA_WARN_CHARS = 100
DEFAULT_LONG_PARA_ERROR_CHARS = 140
DEFAULT_LONG_PARA_MIN_COUNT = 2

# 对话占比阈值：<12% → warning 档；<8% → error 档。
# 依据：榜一 16.5%（人类基线）· 番茄男频主流 25~40% · 本仓题材包 style_constraints
# 对话区间 10~32%（我们 11.4%，贴着题材区间地板）。
# **口径**：对话＝成对 “…”（U+201C/U+201D）内的**可见字**；分母＝全章可见字
# （与 wordcount.visible_chars 同式）。落库前经 quality.normalize 统一引号形态，
# 故“” 是权威形态（「」/英式直引号在落库时已规整；归一化前调用会低估）。
# 注意与 guardrails._dialogue_chars 的差别：后者只认 ASCII `"` 且分母是 `len(draft)`
# ——两处口径不同源，本规则不做「对话占比过低」以外的判定，也不改那处。
# **章级提示而非违规判定**：单章方差极大（榜一 ch1 0.0% / ch19 40.6%），
# 弧级看守见 tests/unit/quality/test_readability_baseline.py。
DEFAULT_DIALOGUE_LOW_WARN_RATIO = 0.12
DEFAULT_DIALOGUE_LOW_ERROR_RATIO = 0.08

# 两条可读性规则的**最小判定长度**（照 _MIN_PROSE_CHARS_FOR_DENSITY 先例）：
# 短片段（单测样例、引文、题记）没有排版/配比的统计意义，一律不判。
# 生产章节目标 3000 字、带宽下限 1200，落在守卫之上不受影响。
_MIN_PROSE_CHARS_FOR_READABILITY = 600

# 命中数超阈值时 severity 由 warning 升级为 error
_SEVERITY_UPGRADE_LIMITS: dict[str, int | float] = {
    "AI-FORBIDDEN-WORD": 10,  # 总命中次数
    "AI-EXPLAIN-TONE": 5,
    "AI-PRONOUN-PILE": 5,
    "AI-TRIPLET-OPENING": 5,
}


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
        description="结尾段含这一刻、从此以后、新的篇章、命运的齿轮等。",
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
            "段落由一个 ≤12 字短句构成（无回指词、非对话、非人称起首），即「节拍器式」"
            "短段行文——人类基线书榜一侯府弧 0.00/千字（全弧零出现），我们新书 01 全弧"
            "2.62/千字；2026-09-16 阈值由 5.5 收紧至 1.0，见常量注释。"
            "取自来源研究「段首零回指评论」的可正则化近似，抽样核实后按实际覆盖范围改名。"
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
            "对话（成对“…”内的可见字）占全章可见字比例 <12% 报警、<8% error；"
            "人类基线书 16.5%、番茄男频主流 25~40%、本仓题材包区间 10~32%。"
            "可见字 <600 的片段不判（统计无意义）。阈值与依据见模块内常量注释。"
        ),
    ),
]


# ---------------------------------------------------------------------------
# 内部 helpers
# ---------------------------------------------------------------------------


def _leading_chinese_bigram(sentence: str) -> str | None:
    """取句子开头（去空白/前导标点后）的前两个连续中文字符。"""
    cleaned = sentence.lstrip()
    if not cleaned:
        return None
    m = re.match(r"[\u4e00-\u9fff]{2}", cleaned)
    return m.group(0) if m else None


def _sentences(prose: str) -> list[str]:
    """按中文句末标点切分句子，过滤空句。"""
    parts = re.split(r"[。！？；\n]+", prose)
    return [p.strip() for p in parts if p.strip()]


def _last_paragraph(prose: str) -> str:
    """取正文最后一段（按空行分隔），若无空行则取全文。"""
    paragraphs = [p.strip() for p in prose.split("\n\n") if p.strip()]
    return paragraphs[-1] if paragraphs else prose


def _maybe_upgrade_severity(rule_id: str, count: int | float, base_severity: str) -> str:
    """命中数超过规则阈值时，把 warning 升级为 error。"""
    if base_severity != "warning":
        return base_severity
    limit = _SEVERITY_UPGRADE_LIMITS.get(rule_id)
    if limit is not None and count >= limit:
        return "error"
    return "warning"


# ---------------------------------------------------------------------------
# 各规则扫描器
# ---------------------------------------------------------------------------


def _scan_forbidden_words(prose: str) -> list[dict[str, Any]]:
    """禁用词两层扫描（2026-09-15 分层）。

    - **硬套话层**：命中即报（`AI_PATTERN_HARD_CLICHES`）。
    - **常用弱词层**：仅同章**堆积**才报——单词 ≥ ``_WEAK_WORD_PER_WORD_LIMIT``
      次，或本层合计 ≥ ``_WEAK_WORD_TOTAL_LIMIT`` 次。理由见模块内分层注释：
      这些是中文小说正常用词，实测无区分力（忽然 R=1.18 / 好像人类更高），
      「命中即报」会把 67% 的章节判成异常，噪声淹没信号。
    """
    hard_found = [(w, prose.count(w)) for w in AI_PATTERN_HARD_CLICHES]
    hard_found = [(w, c) for w, c in hard_found if c]

    weak_counts = {w: prose.count(w) for w in AI_PATTERN_WEAK_WORDS}
    weak_counts = {w: c for w, c in weak_counts.items() if c}
    weak_total = sum(weak_counts.values())
    piled = sorted(w for w, c in weak_counts.items() if c >= _WEAK_WORD_PER_WORD_LIMIT)
    weak_pile_up = bool(piled) or weak_total >= _WEAK_WORD_TOTAL_LIMIT

    words_found = [w for w, _ in hard_found]
    if weak_pile_up:
        words_found += piled or sorted(weak_counts)
    if not words_found:
        return []
    total_count = sum(c for _, c in hard_found) + (weak_total if weak_pile_up else 0)
    severity = _maybe_upgrade_severity("AI-FORBIDDEN-WORD", total_count, "warning")
    suffix = "（弱词同章堆积）" if weak_pile_up else ""
    return [
        {
            "rule_id": "AI-FORBIDDEN-WORD",
            "severity": severity,
            "message": f"AI 高频套话/禁用词命中{suffix}：{','.join(words_found)}",
            "count": total_count,
            "words": words_found,
            "weak_pile_up": weak_pile_up,
            "excerpt": prose[:120],
        }
    ]


def _scan_triplet_opening(prose: str) -> list[dict[str, Any]]:
    """连续 3 句以上以相同两字词开头。"""
    sents = _sentences(prose)
    if len(sents) < 3:
        return []
    result: list[dict[str, Any]] = []
    i = 0
    while i < len(sents) - 2:
        word = _leading_chinese_bigram(sents[i])
        if word is None:
            i += 1
            continue
        j = i + 1
        while j < len(sents):
            nxt = _leading_chinese_bigram(sents[j])
            if nxt == word:
                j += 1
            else:
                break
        run_len = j - i
        if run_len >= 3:
            severity = _maybe_upgrade_severity("AI-TRIPLET-OPENING", run_len, "warning")
            result.append(
                {
                    "rule_id": "AI-TRIPLET-OPENING",
                    "severity": severity,
                    "message": f"连续 {run_len} 句以相同两字词「{word}」开头，疑似句式套路",
                    "count": run_len,
                    "word": word,
                    "excerpt": " | ".join(sents[i:j])[:160],
                }
            )
            i = j
        else:
            i += 1
    return result


_PRONOUN_START_RE = re.compile(r"^[他她][\u4e00-\u9fff]")


def _scan_pronoun_pile(prose: str) -> list[dict[str, Any]]:
    """「他/她」开头句子排比堆砌。"""
    sents = _sentences(prose)
    if len(sents) < 3:
        return []
    result: list[dict[str, Any]] = []
    i = 0
    while i < len(sents) - 2:
        if not _PRONOUN_START_RE.match(sents[i]):
            i += 1
            continue
        j = i + 1
        while j < len(sents) and _PRONOUN_START_RE.match(sents[j]):
            j += 1
        run_len = j - i
        if run_len >= 3:
            severity = _maybe_upgrade_severity("AI-PRONOUN-PILE", run_len, "warning")
            result.append(
                {
                    "rule_id": "AI-PRONOUN-PILE",
                    "severity": severity,
                    "message": f"「他/她」主语排比堆砌 {run_len} 句",
                    "count": run_len,
                    "excerpt": " | ".join(sents[i:j])[:160],
                }
            )
            i = j
        else:
            i += 1
    return result


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


_DASH_OR_ELLIPSIS_RE = re.compile(r"——|…{2,}")


def _scan_punct_abuse(
    prose: str, *, threshold_per_1k: float = DEFAULT_DASH_THRESHOLD_PER_1K
) -> list[dict[str, Any]]:
    """破折号/省略号滥用：按 visible_chars 计算每千字出现次数。"""
    matches = list(_DASH_OR_ELLIPSIS_RE.finditer(prose))
    if not matches:
        return []
    wc = max(1, visible_chars(prose))
    rate = len(matches) / (wc / 1000.0)
    if rate <= threshold_per_1k:
        return []
    base_severity = "error" if rate >= threshold_per_1k * 2 else "warning"
    return [
        {
            "rule_id": "AI-PUNCT-ABUSE",
            "severity": base_severity,
            "message": f"破折号/省略号滥用：每千字 {rate:.1f} 处（阈值 {threshold_per_1k}）",
            "count": len(matches),
            "rate": round(rate, 2),
            "threshold": threshold_per_1k,
            "excerpt": prose[:120],
        }
    ]


def _scan_explain_tone(prose: str) -> list[dict[str, Any]]:
    """解释腔连接词/句式高密度检测。"""
    labels: list[str] = []
    total = 0
    for pattern, label in _AI_EXPLAIN_PATTERNS:
        found = list(re.finditer(pattern, prose))
        if found:
            total += len(found)
            if label and label not in labels:
                labels.append(label)
    if total == 0:
        return []
    severity = _maybe_upgrade_severity("AI-EXPLAIN-TONE", total, "warning")
    label_text = ",".join(labels) if labels else "解释连接词"
    return [
        {
            "rule_id": "AI-EXPLAIN-TONE",
            "severity": severity,
            "message": f"解释腔高密度：{total} 处（{label_text}）",
            "count": total,
            "labels": labels,
            "excerpt": prose[:120],
        }
    ]


def _paragraphs(prose: str) -> list[str]:
    """按空行切分段落（与 ``_last_paragraph`` 同口径，返回全部段）。"""
    return [p.strip() for p in prose.split("\n\n") if p.strip()]


# --- 对举结构 ---------------------------------------------------------------

_CONTRAST_PAIR_RE = re.compile(r"不是[^，。！？；：\n]{1,20}[，,]\s*(?:而是|是)")


def count_contrast_pairs(prose: str) -> list[str]:
    """对举结构「不是 A 而是/是 B」的全部命中原文（供精确率抽样核查）。"""
    return [m.group(0) for m in _CONTRAST_PAIR_RE.finditer(prose)]


def _scan_contrast_pair(
    prose: str, *, threshold_per_1k: float = DEFAULT_CONTRAST_PAIR_RATE_PER_1K
) -> list[dict[str, Any]]:
    hits = count_contrast_pairs(prose)
    if not hits:
        return []
    wc = visible_chars(prose)
    if wc < _MIN_PROSE_CHARS_FOR_DENSITY:
        return []
    rate = len(hits) / (wc / 1000.0)
    if rate <= threshold_per_1k:
        return []
    return [
        {
            "rule_id": "AI-CONTRAST-PAIR",
            "severity": "warning",
            "message": (
                f"对举结构「不是……而是……」密度偏高：每千字 {rate:.1f} 处"
                f"（阈值 {threshold_per_1k}）"
            ),
            "count": len(hits),
            "rate": round(rate, 2),
            "threshold": threshold_per_1k,
            "samples": hits[:5],
            "excerpt": hits[0][:120],
        }
    ]


# --- 短句独立成段（原拟名「段首零回指评论」，抽样核实后改名）---------------------------------------------------------

# 回指/指示成分：出现即视为「已标示评价对象」，不算零回指。
_ANAPHORA_RE = re.compile(r"[这那此该其]|上述|前者|后者|以上|方才|前面|下面")
# 对话/引语起首（排除对话，对话短句属正常写法）。
_DIALOGUE_START_RE = re.compile(r"^[「『\"'“]")
# 人称代词起首的短句属叙述推进，另由 AI-PRONOUN-PILE 管，此处不重复计。
_PERSONAL_START_RE = re.compile(r"^[他她它]")
_PARA_OPENER_MAX_CHARS = 12
# 密度类规则的**最小判定长度**：几十字的字符串（单测样例、片段）算密度没有统计意义，
# 「1 处命中 / 17 字 = 58/千字」会稳定误报。低于此长度一律不判密度。
_MIN_PROSE_CHARS_FOR_DENSITY = 200


def count_short_paras(prose: str) -> list[str]:
    """短句独立成段的全部命中原文（供精确率抽样核查）。

    判定：段落首句（也是该段唯一可判内容）满足全部四条——
    ① 可见字 ≤ 12；② 非对话（不以引号起首）；③ 不含回指/指示成分；
    ④ 非人称代词起首（那属叙述推进，另有规则管）。

    抽样核实（2026-09-15，抽 12 条）：命中为「林策开口了」「万卷阁开了」这类
    **叙述短句独立成段**，非来源研究所述「零回指评价性评论」——故规则名按实际
    覆盖范围登记为 AI-SHORT-PARA，不冒用来源特征名。
    """
    out: list[str] = []
    for para in _paragraphs(prose):
        # 整段就是那一句短句才算「独立成段」——只看段首句会把
        # 「灯芯闪了一下。苏婉清没有出声，把玉佩收回袖中。」误判成短段。
        if len(re.sub(r"\s", "", para)) > _PARA_OPENER_MAX_CHARS:
            continue
        first = re.split(r"[。！？；\n]", para, maxsplit=1)[0].strip()
        if not first:
            continue
        if _DIALOGUE_START_RE.match(first) or _PERSONAL_START_RE.match(first):
            continue
        if _ANAPHORA_RE.search(first):
            continue
        out.append(first)
    return out


def _scan_short_para(
    prose: str, *, threshold_per_1k: float = DEFAULT_SHORT_PARA_RATE_PER_1K
) -> list[dict[str, Any]]:
    hits = count_short_paras(prose)
    if not hits:
        return []
    wc = visible_chars(prose)
    if wc < _MIN_PROSE_CHARS_FOR_DENSITY:
        return []
    rate = len(hits) / (wc / 1000.0)
    if rate <= threshold_per_1k:
        return []
    return [
        {
            "rule_id": "AI-SHORT-PARA",
            "severity": "warning",
            "message": (
                f"短句独立成段密度偏高：每千字 {rate:.1f} 处（阈值 {threshold_per_1k}）"
            ),
            "count": len(hits),
            "rate": round(rate, 2),
            "threshold": threshold_per_1k,
            "samples": hits[:5],
            "excerpt": hits[0][:120],
        }
    ]


# --- 拟人化喻体 -------------------------------------------------------------

_ANTHRO_ROLE_WORDS = (
    "导师|老师|教师|医师|医生|专家|学者|智者|哲人|匠人|工匠|艺术家|指挥家|"
    "建筑师|设计师|工程师|裁判|考官|审查员|监察员|守卫|哨兵|管家|仆人|猎人|"
    "农夫|旅人|向导|领航员|旁观者|见证者|记录者|观察者|讲述者|会计|"
    "技师|维修工|侦探|法官|医生|教练|舵手|领队|总管"
)
_ANTHRO_VEHICLE_RE = re.compile(
    r"像(?:一位|一名|一个|位|名)[^，。！？；：\n]{0,10}?(?:" + _ANTHRO_ROLE_WORDS + r")"
)


def count_anthro_vehicles(prose: str) -> list[str]:
    """拟人化喻体（以职业/角色名词作喻体）的全部命中原文。"""
    return [m.group(0) for m in _ANTHRO_VEHICLE_RE.finditer(prose)]


def _scan_anthro_vehicle(
    prose: str, *, min_count: int = DEFAULT_ANTHRO_VEHICLE_MIN_COUNT
) -> list[dict[str, Any]]:
    hits = count_anthro_vehicles(prose)
    if len(hits) < min_count:
        return []
    return [
        {
            "rule_id": "AI-ANTHRO-VEHICLE",
            "severity": "warning",
            "message": f"拟人化喻体（职业/角色类）同章 {len(hits)} 处",
            "count": len(hits),
            "samples": hits[:5],
            "excerpt": hits[0][:120],
        }
    ]


# --- 可读性：长段 -----------------------------------------------------------


def _readability_paragraphs(prose: str) -> list[str]:
    """可读性规则的段切分口径：按**任意换行序列**切段（``\\n+``）。

    与 :func:`_paragraphs`（``\\n\\n``）的差别只在「单换行是否算分段」：文风锚点书
    等外部语料是**一行一段**（用 ``\\n\\n`` 会把整章读成一段，指标就没法与人类
    语料同口径对照）；生产 draft 落库形态以 ``\\n\\n`` 为主，实测两口径在本仓
    语料上仅差 5 段（3056 vs 3061，0.16%，个别章里存在单换行）。
    """
    return [p.strip() for p in re.split(r"\n+", prose) if p.strip()]


def count_long_paragraphs(
    prose: str, *, warn_chars: int = DEFAULT_LONG_PARA_WARN_CHARS
) -> list[str]:
    """超过 ``warn_chars`` 可见字的段落原文（供精确率抽样核查）。"""
    return [p for p in _readability_paragraphs(prose) if visible_chars(p) > warn_chars]


def _scan_long_para(
    prose: str,
    *,
    warn_chars: int = DEFAULT_LONG_PARA_WARN_CHARS,
    error_chars: int = DEFAULT_LONG_PARA_ERROR_CHARS,
    min_count: int = DEFAULT_LONG_PARA_MIN_COUNT,
) -> list[dict[str, Any]]:
    """长段堆积：单段可见字超限即计入，按段数/最大段长定档。

    - 段落判定与分母口径：``visible_chars``（去空白）——与全仓字数口径同一函数；
    - ``count`` = 单章长段（>warn_chars）数；``samples`` = 前 5 段开头 30 字，
      供人工抽样核对（算子只看字面长度，不判断内容是否真的「实心字」）；
    - 定档：任一段 > ``error_chars`` ⇒ error；否则长段数 ≥ ``min_count`` ⇒ warning。
    """
    if visible_chars(prose) < _MIN_PROSE_CHARS_FOR_READABILITY:
        return []
    long_paras = count_long_paragraphs(prose, warn_chars=warn_chars)
    over_error = [p for p in long_paras if visible_chars(p) > error_chars]
    if not over_error and len(long_paras) < min_count:
        return []
    lengths = [visible_chars(p) for p in long_paras]
    severity = "error" if over_error else "warning"
    return [
        {
            "rule_id": "AI-LONG-PARA",
            "severity": severity,
            "message": (
                f"长段堆积：单章 {len(long_paras)} 段超过 {warn_chars} 可见字"
                f"（最长 {max(lengths)} 字）"
            ),
            "count": len(long_paras),
            "error_count": len(over_error),
            "max_chars": max(lengths),
            "warn_chars": warn_chars,
            "error_chars": error_chars,
            "samples": [p[:30] for p in long_paras[:5]],
            "excerpt": long_paras[0][:120],
        }
    ]


# --- 可读性：对话占比 -------------------------------------------------------

# 成对弯双引号内的内容（U+201C/U+201D）。落库前 quality.normalize 已把
# 「」/英式直引号规整为“”，故此处不再兼容其它引号形态。
_DIALOGUE_PAIR_RE = re.compile(r"\u201c([^\u201d]*)\u201d")


def count_dialogue_visible_chars(prose: str) -> int:
    """成对 “…” 内的可见字数（去空白口径，与 :func:`visible_chars` 同式）。"""
    return sum(visible_chars(m.group(1)) for m in _DIALOGUE_PAIR_RE.finditer(prose))


def dialogue_ratio(prose: str) -> float:
    """对话占比 = 对话可见字 ÷ 全章可见字；空正文返回 0.0。"""
    total = visible_chars(prose)
    if total == 0:
        return 0.0
    return count_dialogue_visible_chars(prose) / total


def _scan_dialogue_low(
    prose: str,
    *,
    warn_ratio: float = DEFAULT_DIALOGUE_LOW_WARN_RATIO,
    error_ratio: float = DEFAULT_DIALOGUE_LOW_ERROR_RATIO,
) -> list[dict[str, Any]]:
    """对话占比过低：<warn_ratio ⇒ warning；<error_ratio ⇒ error。"""
    total = visible_chars(prose)
    if total < _MIN_PROSE_CHARS_FOR_READABILITY:
        return []
    dialogue_chars = count_dialogue_visible_chars(prose)
    ratio = dialogue_chars / total
    if ratio >= warn_ratio:
        return []
    return [
        {
            "rule_id": "AI-DIALOGUE-LOW",
            "severity": "error" if ratio < error_ratio else "warning",
            "message": (
                f"对话占比过低：{ratio:.1%}"
                f"（{dialogue_chars}/{total} 可见字，阈值 {warn_ratio:.0%}）"
            ),
            "count": 1,
            "dialogue_ratio": round(ratio, 4),
            "dialogue_chars": dialogue_chars,
            "total_visible_chars": total,
            "warn_ratio": warn_ratio,
            "error_ratio": error_ratio,
            "excerpt": prose[:120],
        }
    ]


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
    dialogue_low_error_ratio: float = DEFAULT_DIALOGUE_LOW_ERROR_RATIO,
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
        _scan_dialogue_low(
            prose, warn_ratio=dialogue_low_warn_ratio, error_ratio=dialogue_low_error_ratio
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
    "DEFAULT_DIALOGUE_LOW_ERROR_RATIO",
    "DEFAULT_DIALOGUE_LOW_WARN_RATIO",
    "DEFAULT_LONG_PARA_ERROR_CHARS",
    "DEFAULT_LONG_PARA_MIN_COUNT",
    "DEFAULT_LONG_PARA_WARN_CHARS",
    "DEFAULT_SHORT_PARA_RATE_PER_1K",
    "count_anthro_vehicles",
    "count_contrast_pairs",
    "count_dialogue_visible_chars",
    "count_long_paragraphs",
    "count_short_paras",
    "dialogue_ratio",
    "scan_ai_patterns",
]
