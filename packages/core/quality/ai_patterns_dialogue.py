"""对白族算子（V4.0 模块化拆分自 ai_patterns.py，2026-09-30）。

对白可见字与占比（AI-DIALOGUE-LOW），以及相邻对白回声（AI-DIALOGUE-ECHO，
含 ``overlap_comparable`` 可比性闸门——对称指标要求两侧质量可比才可解释）。

段切分口径与最小判定长度取自 :mod:`ai_patterns_text`，与段落族同一入口。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from packages.core.quality.wordcount import visible_chars

from .ai_patterns_text import (
    _MIN_PROSE_CHARS_FOR_DENSITY,
    _MIN_PROSE_CHARS_FOR_READABILITY,
    _split_paragraphs,
)

# 对话占比阈值：**只有 <12% → warning 这一档**（<8% → error 档已于 2026-09-17 撤回，
# 见下）。依据：榜一 16.5%（人类基线）· 番茄男频主流 25~40% · 本仓题材包
# style_constraints 对话区间 10~32%（我们 11.4%，贴着题材区间地板）。
# **口径**：对话＝成对 “…”（U+201C/U+201D）内的**可见字**；分母＝全章可见字
# （与 wordcount.visible_chars 同式）。落库前经 quality.normalize 统一引号形态，
# 故“” 是权威形态（「」/英式直引号在落库时已规整；归一化前调用会低估）。
# 注意与 guardrails._dialogue_chars 的差别：后者只认 ASCII `"` 且分母是 `len(draft)`
# ——两处口径不同源，本规则不做「对话占比过低」以外的判定，也不改那处。
# **章级提示而非违规判定**：单章方差极大（榜一 ch1 0.0% / ch19 40.6%），
# 弧级看守见 tests/unit/quality/test_readability_baseline.py。
#
# **撤回 error 档（2026-09-17，用户实测反馈）**：对话占比是**可优化指标**，不是
# 质量判据——逼模型凑指标必然产出灌水。原 <8% → error 档连同题材包/生成驱动里的
# 「对话占比 ≥20%」硬指标一起施行后，用户实测反馈「你现在的改法书一点就不好读了，
# 全是重复性的对话灌水，上一句说了啥，下一句接着重复一遍」，实证：
#   - ch3 验仓戏**为凑对话编造对白并破人设**：让主角主动交代「七成发霉率」、主动
#     透露「三天后什么价」（价签是他唯一底牌）；
#   - ch33 六句对白全是**接词回声**（「三百人，够不够守城？」→「三百。」→「三百人，
#     吃什么？」→「够七天。」→「七天之后呢？」→「七天之后，看你守不守得住。」），
#     每句复述上一句的词，六句下来局面毫无变化。
# 章级口径本身也会误报：人类锚点书 19 章里 8 章低于 12%、**首章 0.0%**——人类作者
# 写得出整章无对话的动作戏。故本规则**只作提示不作闸门**：任何情况只返回 warning，
# 绝不进 errors 桶（判别：tests/unit/quality/test_dialogue_low_warning_only.py）。
# 由此暴露的真实缺陷形状（指标化写作目标的副作用）见 F-18 与下面的 AI-DIALOGUE-ECHO。
DEFAULT_DIALOGUE_LOW_WARN_RATIO = 0.12

# ---------------------------------------------------------------------------
# 2026-09-17：接词回声（AI-DIALOGUE-ECHO）——把上面那个副作用的**症状**本身
# 做成算子：对话不该是「接龙复述」，而该是**信息递增**（每句带来新东西）。
# ---------------------------------------------------------------------------
# 为什么是「相邻对白」而不是「对话占比」：占比逼出来的病有具体形状可正则化，
# 而占比本身没有（人类 0.0% 的章合法）。两条一正一反：撤掉错误的闸门，留下症状的探针。
#
# 实测（复算入口 scripts/readability_audit.py，两侧语料同口径；摘要在该脚本汇总行）。
# **2026-09-27 复算订正**（此前登记的「26 对 / 20 章」已过期，且早于本次定义修正）：
#   修正前口径：书 prj_bcb9d1930bd4 48 章 → **34 对 / 24 章命中**（相邻对白对 674）；
#              榜一侯府弧 19 章 → 1 对 / 1 章（相邻对白对 61）。
#   修正后口径（重叠系数可比性守卫生效，见下）：**23 对 / 18 章**；人侧仍 1 对 / 1 章。
#   被守卫剔掉的 11 对（后句 ≥ 2 × 前句）**不是「全部合法写法、0 条真信号」**——那是
#   2026-09-27 首轮交付里的过强断言，对抗审查已打回。按逐条判档：
#     - 合法写法 6：ch13 引语式回应 / ch17 称呼+展开 / ch31 复述关键词后逐条驳斥 /
#       ch44 `即日。`+展开 / ch36 引词后驳斥 / ch9 议价引数；
#     - **存疑 3**：ch14 / ch32 让步式回应 / ch35 发令者复述命令以解释；
#     - **真信号 1**：ch44 `他们敢。`→`他们敢。他们正愁没理由。`（下方详述）。
#   **已知代价（登记，不改守卫）**：守卫在「复述关键词后补理由」家族内**与回声度反
#   相关**——它按长度比剔除，恰好剔掉这一族里字面重复较高的那些。ch44 该对
#   `他们敢。`(3 字) → `他们敢。他们正愁没理由。`(10 字) 是**逐字复述对方整句
#   （3/3）再追加理由**，字面重复比例 100%，比被判为真信号且**仍在守卫内**的 ch45
#   `他们退了。`→`他们退了。`（recall 1.00）只低一个量级。同一把尺子得出相反判档，
#   说明边界确实切错了家族。审计自己标的 3 条「存疑」（recall 0.83 / 1.00 / 0.75）
#   全部由 recall 分支保住、**一条都没被守卫碰**——即守卫的伤害面就是上面这 1~2 条。
#   替代判据实测过（`opening_repeat >= 2 且 共同字/后句 >= 0.25`）并**被否决**：人类侧
#   从 1 对涨到 4 对 / 3 章，更差。守卫本身站得住，**问题在判档措辞，不在实现**。
#
# **重叠系数的分母缺陷（2026-09-27 定性）**：`overlap = 共同实义字 ÷ min(前,后)`
# 是**对称**度量（问「两句用词是不是同一批」），而 `recall = 共同实义字 ÷ 后句`
# 是**非对称**度量（问「新句里有多少比例是复述」）。两种问法各自成立，但共用
# `min()` 分母会在一种形态下**测不到它声称的东西**：后句是前句的超集时
# （短称呼 + 长展开），`min()` 恒等于前句的字数，只要长句包含短句的字就得到
# ``overlap = 1.00``——那衡量的是「长句含短句的字」，不是「用词是同一批」。
# 实测 ch17 `“林昭！”` → `“林昭，你跟我走，茶馆比县仓安全。”`：前句实义 2 字、
# 后句 13 字、recall 2/13 = 0.15、overlap 1.00——**recall 说「后句 85% 是新东西」，
# overlap 却在说「完全重合」**，后者被前者证伪。该形态不携带回声信息。
#
# 守卫（:data:`_DIALOGUE_ECHO_ASYMMETRY_LIMIT` + :data:`_MIN_DIALOGUE_ECHO_COMPARABLE_CHARS`，
# 合起来即 :attr:`DialogueEchoPair.overlap_comparable`）：**不对称是原则性的，
# 不是随手选的 2 倍**——recall 的分母取后句是设计使然，故「后句更短」那一侧由
# recall 正确兜住（`见周账房了？`→`见了。` recall 1.00 是「直接应答」，是登记在册的
# 必命中形态）；只有「后句是超集」这一侧 recall 必然走低（实测 11 对的 recall
# 落在 0.07~0.33）而 overlap 被机械拉到 1.00，才需要守卫。实测「两侧都只有 2 字」
# 时 overlap 同样恒为 1.00（3 对 2 字齐诵 `不退。`→`不退！` 即如此），故另设最小
# 可比字数 3。
#
# ⚠️ **11 对是薄样本**：这是**实测锚定**（本仓生成侧 34 对全量 + 人侧 1 对），
# **不是分布结论**，不构成「任何后句 ≥2 倍前句的对白都合法」的一般命题。
#
# **2.0 这个值是定义性选择，不是标定所得**（2026-09-27 对抗审查订正——首轮交付写成
# 「2.0 取自 11 对的下界」是**循环论证**：被剔集合的实测比值正是
# 2.00 / 2.50 / 2.83 / 3.00 / 3.33 / 4.07 / 5.18 / 6.00 / 6.50 / 6.50 / 11.43，
# 2.0 恰是该组最小值，把阈值放在待判集合的最小值上必然全剔，那是恒真式不是证据）。
# 参数扫描实测（本仓语料，min<3 守卫固定为 3）：
#   limit 1.5 / 1.88 / 1.95 / **2.0** → 生成侧**恒 23 对 / 18 章**（区间内无影响）
#   limit 2.05 / 2.2 / 2.5            → 24 对 / 19 章（回来的是 g32，比值**恰为 2.00**：
#                                        `back < 2.0*front` 即 `12 < 12` 为假被剔，
#                                        `2.05*6 = 12.3 > 12` 即真放回）
#   limit 2.83                        → 25 对 / 20 章（+g35 `你守后队`，比值 2.50）
#   limit 3.5 / 5.0                   → 28 / 29 对
#   人类侧在 [1.5, 5.0] 全程**恒 1 对 / 1 章**，无区分力（61 对候选的比值
#   p50 1.27 / p75 1.88 / p90 2.88 / p95 4.67 / max 24.50，`back>=2×front` 占 21.3%）。
# 结论：该值的正当性来自**度量的可解释性**（后句是超集 ⇒ `min()` 分母被前句钉死 ⇒
# overlap 读数机械失效），**不来自任何精度/召回标定**；精度代价见上面「已知代价」段。
#
# 两种形态分开定阈（实测两种都真实存在；只留其一都会漏掉用户实证里的对白）：
#   ① **复述型** ``recall >= 0.70``：后句实义字中 ≥70% 出现在前句（把对方的话基本
#      复述一遍）。例（2026-09-27 逐条复算的实测值）：`见周账房了？`→`见了`
#      （recall 1.00、开口 1 字）、`账上的数我知道。上月你在这儿当值？`→`我当值`
#      （recall 1.00）、`先把库房那几袋茶处理了。`→`那十几袋茶？`（recall 0.80）。
#      - 2026-09-27 订正：旧注释把 `你守后队。`→`后队有人殿后，你在那儿。`（实测
#        recall **0.30**）列为本形态样例，**举错例了**——它是下面的接词型命中
#        （重叠 0.75、开口 2），不是复述型。
#   ② **接词型** ``overlap >= 0.66`` 且**开口连续重合 ≥2 字**且**两侧可比**
#      （:attr:`DialogueEchoPair.overlap_comparable`）：后句开口就接对方的词。
#      例（均为实测命中且两侧可比）：`够七天。`→`七天之后呢？`（0.67、开口 2）、
#      `他临走前那晚…借据造假。`→`借据呢？`（0.67、开口 2）。
#      - 0.66 而非 0.70：上述必命中样例实测 2/3 = 0.67，0.70 会漏掉它（阈值落在
#        样例分布之外）；人侧 19 章在该形态上 0.66 与 0.70 **同为 0 对**——即 0.70
#        只减召回、不加精确率。
#      - 开口 ≥2 字是**精确率守卫**（实测逼出来的）：放开到 0，人侧仍 1 对不变，而
#        生成侧多出 22 对，**实测形态是「前句的词出现在后句中段/末尾」**——追问的
#        对象（`北山驿路。`→`你怎么知道北山驿路的事？`）、长回答里夹带
#        （`林出纳。`→`永丰号那边传话…说林出纳的名字已经上了册子。`）、术语复指
#        （`你这价——`→`三天后还你等量的粮。…`）。这类**开口不接词**，与接词无关。
#        - 2026-09-27 订正：旧注释称该守卫挡住「短促称呼/术语 + 长回答」型误报，
#          **实测没挡住**：`林昭！`→`林昭，你跟我走，茶馆比县仓安全。` 的开口连续
#          重合是 2，照样命中——因为后句恰好**以该称呼起首**。它挡的是「称呼出现在
#          后句中段」那一种（见上），不是全部。该形态改由重叠系数的可比性守卫拦。
# 精度口径：算子只看字面重合，**不判断对白是否有后果**——信息递增但用词撞车的正常
# 对白会被计入（人侧那 1 对即此类：「…我们明天一早就在城门口等吧」→「好啊，明儿个
# 我们就在城门口等他们。」），故 severity 恒 warning、附 samples 供人工过目。
DEFAULT_DIALOGUE_ECHO_MIN_RATIO = 0.70
DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP = 0.66
DEFAULT_DIALOGUE_ECHO_MIN_OPENING = 2
# 单侧实义字下限：单字应答（「嗯。」「好。」）不含信息、判它没有意义，不参与成对比较。
# ——这是**成对资格**的下限；重叠系数另有更高的**可比性**下限（下面两个常量）。
_MIN_DIALOGUE_ECHO_CHARS = 2
# 重叠系数的**可比性**下限（两项缺一即视为 overlap 不可解释，见上面「分母缺陷」段）：
#   ① 任一侧实义字 < 3 时 overlap 必为 1.00（分母落在 2 字那一侧），该读数不携带
#      信息；
#   ② 后句 ≥ 2 × 前句时后句是超集，min() 分母被前句钉死，overlap 同样必为 1.00。
#
# **两条守卫各自的实测作用**（2026-09-27 二次订正；首轮交付写的「① 覆盖 8 对」与
# 「① 是 0 增量 no-op」不能同时为真，现按触发 / 净抑制分列）：
#   prj_bcb9d1930bd4 48 章 / 34 对无守卫命中：
#     ① 触发 **11 对**、② 触发 **11 对**、①∩② = 3 对、① 独有 = 8 对；
#     ① 独有的 8 对**全部 recall ≥ 0.70**（`卷走？` / `退了！`×2 / `来了。` /
#     `三年。` / `走了。` / `即日。` / `见了。`）⇒ **① 独有抑制 0 对**，净抑制 11 对
#     全部来自 ②。① 是**定义性**守卫：撤掉它，`退了。`→`退了呀。` 这类 2/3 字对
#     会凭恒等于 1.00 的读数命中（合成用例判别）。
#   全库 196 份 draft / 无守卫 71 对：① 触发 21 对、① 独有 12 对、①∩② = 5 对；
#     ① 独有的 12 对**同样全部 recall ≥ 0.70** ⇒ **独有抑制 0 对**（跨项目一致）。
# 真信号 3 对（`不退。`→`不退！`×2 的 2→2、`他们退了。`→`他们退了。` 的 4→4）由
# recall 命中，不因两条守卫而丢失。
# ⚠️ 薄样本，实测锚定非分布结论。
_MIN_DIALOGUE_ECHO_COMPARABLE_CHARS = 3
_DIALOGUE_ECHO_ASYMMETRY_LIMIT = 2.0


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
) -> list[dict[str, Any]]:
    """对话占比过低：<warn_ratio ⇒ **warning（唯一档位）**。

    error 档（<8%）已于 2026-09-17 撤回——理由与实证见模块内常量注释（逼模型凑
    对话指标会产出灌水对白，且人类锚点书写得出整章无对话）。本函数**不含任何
    返回 error 的路径**，severity 恒为 ``"warning"``。
    """
    total = visible_chars(prose)
    if total < _MIN_PROSE_CHARS_FOR_READABILITY:
        return []
    dialogue_chars = count_dialogue_visible_chars(prose)
    ratio = dialogue_chars / total
    if ratio >= warn_ratio:
        return []
    summary = (
        f"对话占比 {ratio:.1%}"
        f"（{dialogue_chars}/{total} 可见字，阈值 {warn_ratio:.0%}）"
    )
    return [
        {
            "rule_id": "AI-DIALOGUE-LOW",
            "severity": "warning",
            "message": f"对话占比过低：{summary}",
            "count": 1,
            "dialogue_ratio": round(ratio, 4),
            "dialogue_chars": dialogue_chars,
            "total_visible_chars": total,
            "warn_ratio": warn_ratio,
            # 2026-09-21 检修 m3：本规则的「证据」就是占比数字本身。此前没有任何
            # samples / words 键，``_ai_pattern_evidence`` 只能退回 ``excerpt``
            # （正文前 120 字）——读者看到一段与占比无关的原文，看不出在接受什么。
            "samples": [summary],
            "excerpt": prose[:120],
        }
    ]


# --- 可读性：相邻对白接词回声 -------------------------------------------------

# 实义字：汉字与数字（数字是接词回声的关键信号——「七天」「三百」「十四五两」这类
# 数字被后句复述，是本规则最能分辨「复述」与「真回答」的特征；标点/空白/字母不计）。
_SUBSTANTIVE_CHAR_RE = re.compile(r"[\u4e00-\u9fff0-9]")
# 抽样展示时的单侧截断长度（samples 只供人工过目，不参与判定）。
# ⚠️ **依据强度：经验值（未经语料校准）**（2026-10-22 批次二十一补记）。
#   它是**展示截断**参数（不进任何判定），本身不需要实测依据；但「40」这个
#   数字是经验值，**不得当阈值引用**。
_ECHO_SAMPLE_MAX_CHARS = 40


@dataclass(frozen=True)
class DialogueEchoPair:
    """一对相邻对白及其回声指标（供抽样核对：算子只看字面，判定权在人和 LLM）。"""

    front: str  # 前句对白原文（引号内，已截断）
    back: str  # 后句对白原文（引号内，已截断）
    front_chars: str  # 前句实义字
    back_chars: str  # 后句实义字
    recall_ratio: float  # 复述型指标：后句实义字中出现在前句的比例
    overlap_ratio: float  # 接词型指标：共同实义字 ÷ 较短一侧实义字（**须配合 overlap_comparable 读**）
    opening_repeat: int  # 后句开口起、连续出现在前句中的实义字数

    @property
    def overlap_comparable(self) -> bool:
        """``overlap_ratio`` 这个读数在本对上是否**可解释**（2026-09-27）。

        ``overlap`` 的分母取 ``min(前,后)``，对称地问「两句用词是不是同一批」。
        分母落在薄的一侧时该问法失效——读数被机械地拉到 1.00，量的其实是
        「长句含短句的字」。两种失效形态：

        1. 任一侧实义字 < ``_MIN_DIALOGUE_ECHO_COMPARABLE_CHARS``（3）——``min()``
           等于那一侧的 2 字，只要对方包含它就得 1.00；
        2. 后句 ≥ ``_DIALOGUE_ECHO_ASYMMETRY_LIMIT``（2.0）× 前句——后句是超集，
           分母恒等于前句；而此时 recall 必然走低（后句大半是新内容），两个指标
           互相证伪。

        只有 recall 是不对称的（分母恒为后句，问的是「新句里有多少是复述」），
        「后句更短」那一侧由它正确兜住，故本守卫**只挡超集方向**。

        不可比的对不因本属性而**不报**——复述型仍照常判定（2 字齐诵 `不退。`→
        `不退！` 就是靠 recall 命中的真信号）。本属性只决定 ``overlap_ratio``
        能不能用来触发接词型。
        """
        front_len, back_len = len(self.front_chars), len(self.back_chars)
        if min(front_len, back_len) < _MIN_DIALOGUE_ECHO_COMPARABLE_CHARS:
            return False
        return back_len < _DIALOGUE_ECHO_ASYMMETRY_LIMIT * front_len

    @property
    def sample(self) -> str:
        """``前句→后句`` 形式（命中样例的展示形态）。"""
        return f"{self.front}→{self.back}"

def _substantive_chars(text: str) -> str:
    """实义字序列：去标点/空白，保留汉字与数字。"""
    return "".join(_SUBSTANTIVE_CHAR_RE.findall(text))

def _dialogue_lines(prose: str) -> list[str]:
    """全章的**对白句**序列：以 “ 起首的段，取其引号内的对白文本。

    - 段切分与短段 / 长段两条规则同口径（:func:`_split_paragraphs`，``\\n+``）——
      外部锚点书一行一段、生产 draft 空行分段；
    - 段内夹带的叙述（``“三百。”曹淳说`` 的 ``曹淳说``）不计入比较：本规则量的是
      **对白之间**的接词，叙述是独立信息；
    - 只认“”（U+201C/U+201D）：落库前经 ``quality.normalize`` 统一引号形态。
    """
    lines: list[str] = []
    for para in _split_paragraphs(prose):
        if not para.startswith("\u201c"):
            continue
        quoted = "".join(m.group(1) for m in _DIALOGUE_PAIR_RE.finditer(para))
        if quoted:
            lines.append(quoted)
    return lines

def _opening_repeat_len(back: str, front: str) -> int:
    """后句开口起、在前句中**连续出现**的最长实义字序列长度。

    前缀是嵌套的（后句前 i 字出现 ⇒ 前 i-1 字也出现），故一旦失配即可停。
    「开口接词」是本规则的第二形态：例「够七天」→「**七天**之后呢」（2）。
    """
    length = 0
    while length < len(back) and back[: length + 1] in front:
        length += 1
    return length

def _echo_ratios(front: str, back: str) -> tuple[float, float]:
    """返回 ``(复述型比例, 重叠系数)``（均按实义字**多重集**取小计共同字）。

    - 复述型 = 共同字 ÷ **后句**字数——问「新句里有多少比例是复述对方的」，
      **非对称**，分母恒为后句，因此在两侧长度悬殊时仍然可解释（短后句被完整
      包含 ⇒ 1.00，那是「直接应答」的定义，如 `见周账房了？`→`见了。`）。
    - 重叠系数 = 共同字 ÷ **较短一侧**字数——问「两句的用词是不是同一批」，
      **对称**。对称问法要求两侧**质量可比**：分母落在薄的一侧时该读数不携带
      信息（被机械拉到 1.00）。故**本函数不声明它是否可解释**，可解释性由
      :attr:`DialogueEchoPair.overlap_comparable` 单独判定，调用方必须先看它。

    两者不可互相替代：短称呼 + 长展开时 recall 低（后句大半是新内容）而 overlap
    恒 1.00，两者互相证伪——此时以 recall 为准（实测 ch17 `林昭！`→`林昭，你跟我
    走，茶馆比县仓安全。`：recall 0.15 / overlap 1.00）。
    """
    front_count, back_count = Counter(front), Counter(back)
    front_total, back_total = sum(front_count.values()), sum(back_count.values())
    shared = sum(min(count, front_count[ch]) for ch, count in back_count.items())
    recall = shared / back_total if back_total else 0.0
    overlap = shared / min(front_total, back_total) if front_total and back_total else 0.0
    return recall, overlap

def dialogue_echo_pairs(prose: str) -> list[DialogueEchoPair]:
    """全部**相邻对白对**及其指标（未过滤；供抽样与阈值校准）。

    相邻 = 对白句序列里前后两项（中间夹叙述段不影响相邻性——夹在两句对白之间的一段
    动作描写不改变「后句接了前句的词」这一事实）。单侧实义字 <
    ``_MIN_DIALOGUE_ECHO_CHARS``（单字应答）不参与成对比较。
    """
    lines = _dialogue_lines(prose)
    pairs: list[DialogueEchoPair] = []
    for front_raw, back_raw in zip(lines, lines[1:]):
        front, back = _substantive_chars(front_raw), _substantive_chars(back_raw)
        if len(front) < _MIN_DIALOGUE_ECHO_CHARS or len(back) < _MIN_DIALOGUE_ECHO_CHARS:
            continue
        recall, overlap = _echo_ratios(front, back)
        pairs.append(
            DialogueEchoPair(
                front=front_raw[:_ECHO_SAMPLE_MAX_CHARS],
                back=back_raw[:_ECHO_SAMPLE_MAX_CHARS],
                front_chars=front,
                back_chars=back,
                recall_ratio=recall,
                overlap_ratio=overlap,
                opening_repeat=_opening_repeat_len(back, front),
            )
        )
    return pairs

def count_dialogue_echoes(
    prose: str,
    *,
    min_ratio: float = DEFAULT_DIALOGUE_ECHO_MIN_RATIO,
    min_overlap: float = DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP,
    min_opening: int = DEFAULT_DIALOGUE_ECHO_MIN_OPENING,
) -> list[DialogueEchoPair]:
    """命中的接词回声对（同一对相邻对白最多计一次，两种形态是「或」关系）。

    - **复述型**：``recall_ratio >= min_ratio``——后句基本是前句的复述；
    - **接词型**：``overlap_comparable``（两侧实义字可比，见该属性）且
      ``overlap_ratio >= min_overlap`` 且 ``opening_repeat >= min_opening``
      ——后句开口就接对方的词。

    两者都命中时仍只记一对。``overlap_comparable`` 是**度量可解释性**的守卫，
    不是对本对白的豁免：不可比的对仍可凭 recall 命中（真信号形态即如此）。
    **不判对白是否有后果**（那既无法正则化，也不该由字面算子决定）——本规则是
    提示，不是闸门。
    """
    hits: list[DialogueEchoPair] = []
    for pair in dialogue_echo_pairs(prose):
        repeated = pair.recall_ratio >= min_ratio
        picking_up = (
            pair.overlap_comparable
            and pair.overlap_ratio >= min_overlap
            and pair.opening_repeat >= min_opening
        )
        if repeated or picking_up:
            hits.append(pair)
    return hits

def _scan_dialogue_echo(
    prose: str,
    *,
    min_ratio: float = DEFAULT_DIALOGUE_ECHO_MIN_RATIO,
    min_overlap: float = DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP,
    min_opening: int = DEFAULT_DIALOGUE_ECHO_MIN_OPENING,
) -> list[dict[str, Any]]:
    """接词回声章级命中：``count`` = 对数、``samples`` = 前 5 对（``前句→后句``）。

    - 最小判定长度照 ``_MIN_PROSE_CHARS_FOR_DENSITY`` 先例：短片段（单测样例、引文）
      的配比没有统计意义，不判；
    - severity 恒 ``warning``（提示非闸门）——误报的判别权在人工/LLM 评审。
    """
    if visible_chars(prose) < _MIN_PROSE_CHARS_FOR_DENSITY:
        return []
    hits = count_dialogue_echoes(
        prose, min_ratio=min_ratio, min_overlap=min_overlap, min_opening=min_opening
    )
    if not hits:
        return []
    samples = [p.sample for p in hits[:5]]
    return [
        {
            "rule_id": "AI-DIALOGUE-ECHO",
            "severity": "warning",
            "message": f"相邻对白接词回声 {len(hits)} 对（例：{samples[0]}）",
            "count": len(hits),
            "samples": samples,
            "excerpt": samples[0][:120],
        }
    ]
