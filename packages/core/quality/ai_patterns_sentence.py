"""句式 / 词面族算子（V4.0 模块化拆分自 ai_patterns.py，2026-09-30）。

禁用词（两层制）/ 三连句式 / 代词堆砌 / 标点滥用 / 解释腔 / 对举结构。

最小判定长度与段切分口径在 :mod:`ai_patterns_text`。
``AI-ENDING-SUMMARY``（章尾升华）**未随本族迁移**——它依赖 ``_last_paragraph``，
而后者仍随门面留在 ai_patterns.py；**2026-10-15 批次（六）G8 起它也已改走
``_split_paragraphs``**（此前是模块内第二份独立的段落切分实现，见 text 处的说明）。
"""
from __future__ import annotations

import re
from typing import Any

from packages.core.quality.wordcount import visible_chars

from .ai_patterns_text import _MIN_PROSE_CHARS_FOR_DENSITY

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
#
# ⚠️ **依据强度：经验值（未经语料校准）**（2026-10-22 批次二十一补记）。
#   上面那段注释给出的是**分层设计**的实测依据（弱词在人类侧命中更高 ⇒ 不做
#   「命中即报」，只做「同章堆积」）——**那部分有依据**；但**两个阈值数字本身
#   （3 / 6）查不到出处**：既无实测分位，也无 spec 条文或长跑报告数值。
#   ⇒ 按 AGENTS「声称与实测分离」，此处**如实标注为经验值**，不得当实测分位引用。
#   将来若要给它定锚：跑 ``scripts/ai_tone_calibrate.py`` 取「同章堆积」分布，
#   并按 AGENTS 硬规则 9 抽 20 条命中人工过一遍。
_WEAK_WORD_PER_WORD_LIMIT = 3
_WEAK_WORD_TOTAL_LIMIT = 6

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

# 破折号/省略号命中率的**升级乘数**：rate ≥ 阈值 × 本乘数 ⇒ severity 由 warning
# 升级为 error。
# ⚠️ **2026-10-23 task-16 Q-3**：此前这个「2」是 `_scan_punct_abuse` 里的**内联
# 字面量** ⇒ 证据表 `detector_evidence.json` 的 code_refs 钉不到它 ⇒ 把 2 改成 3
# 时**证据表仍显示一致**（实测：守卫视 consistent）⇒ 升级档静默漂移。
# ⇒ 提为命名常量（可被证据表 `code_refs` 钉住、可被突变规格覆盖）。
DEFAULT_DASH_ERROR_MULTIPLIER = 2
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

# 命中数超阈值时 severity 由 warning 升级为 error
_SEVERITY_UPGRADE_LIMITS: dict[str, int | float] = {
    "AI-FORBIDDEN-WORD": 10,  # 总命中次数
    "AI-EXPLAIN-TONE": 5,
    "AI-PRONOUN-PILE": 5,
    "AI-TRIPLET-OPENING": 5,
}


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
    base_severity = (
        "error" if rate >= threshold_per_1k * DEFAULT_DASH_ERROR_MULTIPLIER else "warning"
    )
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
