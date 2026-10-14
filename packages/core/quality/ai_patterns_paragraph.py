"""段落族算子（V4.0 模块化拆分自 ai_patterns.py，2026-09-30）。

短段 / 长段 / 拟人化喻体。

段切分口径（``\n+``）与最小判定长度取自 :mod:`ai_patterns_text`——**本族与对白族
共用同一入口**，口径分叉过一次就出过「人类基线是量具假象」的事故。
"""
from __future__ import annotations

import re
from typing import Any

from packages.core.quality.wordcount import visible_chars

from .ai_patterns_text import (
    _MIN_PROSE_CHARS_FOR_DENSITY,
    _MIN_PROSE_CHARS_FOR_READABILITY,
    _split_paragraphs,
)

# 短句独立成段（原拟名「段首零回指评论」，抽样核实后改名——见下）。
# 来源研究区分力最强项 R=4.4 是「段首直接给评价/结论而不标示评价对象」；
# 本仓的可正则化近似（段首 ≤12 字、无回指词、非对白、非人称起首）**抽 12 条
# 命中逐条看**：命中是「林策开口了」「万卷阁开了」「三天」这类**叙述短句独立成段**，
# 不是评价性评论；人类样章同样有该写法。故按算子**实际覆盖范围**改名登记，
# 不冒用来源特征名（该研究公开的六次失误全是这个形状）。
# 阈值沿革：5.5（2026-09-15，取本仓**旧语料**逐章 p90）→ 1.0（2026-09-16）→
# **1.0 不动（2026-09-27 复核）**。两次「1.0」的论证链都出过错，第二次是量具：
# 2026-09-16 写的依据是「榜一人类基线 0.00/千字，对生成侧 2.62 立即报警」，而那个
# 0.00 **不是测出来的**——`count_short_paras` 当时独自用 ``\n\n`` 切段，而榜一
# 侯府弧 19 章的 ``\n\n`` 总数 = 0，19 章被读成 19 个整章大段，规则在该语料上
# **构造上不可能命中**（详见 :func:`_split_paragraphs`）。口径统一为 ``\n+`` 后重测
# （2026-09-27，两侧语料：`\n\n`` 总数 = 人类 0 / 生成侧 3445）：
#   榜一侯府弧 19 章 / 37806 可见字：改口径 10 命中 / 0.265 每千字 / 6 章有命中 /
#   2 章超阈值；再叠加 `名字：` 说话人行守卫——该守卫**只替掉 2 处**：1 处人名说话人行
#   （`陆明河：“……”`）+ 1 处字段行（`宋沛年：`，人类书里也是排版件形态）→ **8 命中 /
#   0.212 每千字 / 6 章有命中 / 1 章超阈值**。
#   prj_bcb9d1930bd4 48 章 / 125024 可见字：257 → 260 命中（2.056 → 2.080，仅 ch33
#   一章计数 2→5）；叠加说话人行守卫后 → **260 命中 / 2.080 每千字 / 34 章超阈值**
#   （守卫在生成侧不改变命中数，见 :data:`_SPEAKER_LINE_RE` 的宽度校准）。
# 分离度 2.080 / 0.212 ≈ **9.8×**，与 2026-09-16 的 2.62/0.00 在同一量级，故
# **阈值 1.0 维持不变，本次修的是论证链不是阈值**。
# 「人类零容忍」的准确说法（2026-09-27 二次订正）：人类侧 8 命中逐条分类为
# **散文 5 / 分隔线 2 / 平台标记 1**，唯一超阈的 ch19（rate 2.29）靠的是 3 条
# **非散文**行（2 条分隔线 + 1 条 `【继续】`），该章散文只有 1 条 = 0.76/千字。
# 故**散文口径下人类零容忍成立**：5 命中 / **0.132 每千字 / 0 章超阈值**；登记的
# 1/19 章超阈完全由非散文行构成。（纯排版件/平台标记的排除规则**尚未实现**——那需要
# 自己的 20 条抽样论证，登记为待办；在此之前全口径的 0.212 就是实测值，不加修饰。）
# 判别：tests/unit/quality/test_human_baseline_ai_thresholds.py（改回 5.5 必红；
# 把段切分改回 ``\n\n`` 同样必红——人类侧命中数会掉到 0）。
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
# 2026-09-17：可读性算子（长段 / 对话占比过低）+ 撤回其指标化产物（接词回声）
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
# 段切分口径见 :func:`_split_paragraphs`（``\n+``——外部锚点书是一行一段）。

# 长段阈值：单段 >100 可见字 → warning 档；>140 → error 档。
# **堆积门**（照弱词层 / 拟人喻体「人类侧同样有的写法不做命中即报」纪律）：
# 人类基线每章至多 1 段超 100 字（7/19 章各 1 段，最长 135 字）——单段超长是正常
# 写作，故 warning 要求**单章 ≥2 段**；而 >140 字段落在榜一 779 段里零出现，
# 单段即判 error（我们 16 段、最长 212 字，是真正的排版病）。
DEFAULT_LONG_PARA_WARN_CHARS = 100
DEFAULT_LONG_PARA_ERROR_CHARS = 140
DEFAULT_LONG_PARA_MIN_COUNT = 2


# --- 短句独立成段（原拟名「段首零回指评论」，抽样核实后改名）---------------------------------------------------------

# 回指/指示成分：出现即视为「已标示评价对象」，不算零回指。
_ANAPHORA_RE = re.compile(r"[这那此该其]|上述|前者|后者|以上|方才|前面|下面")
# 对话/引语起首（排除对话，对话短句属正常写法）。
_DIALOGUE_START_RE = re.compile(r"^[「『\"'“]")
# **说话人前缀行**（2026-09-27 补）：`名字：` 形态。此前 ``_DIALOGUE_START_RE``
# 只认「以引号起首」，于是人类锚点书的说话人行（`陆明河：“……”`、`宋沛年：？`）
# 被当成非对白短段计入本规则——即人类语料的排版件替生成侧的 tic 交了税。
#
# **不能写成宽规则**（本项目的反复踩坑处）：`^[^：:]{1,6}[：:]` 会把正文里的普通
# 冒号一并吞掉——`源头：省府常平仓调拨`（字段行）、`现在他只记得：有人笑过`（正常
# 句子）、`跟着浮出第二行：十倍` 全部字面同构。两个分支的判据与**宽度都按实测校准**：
#
#   ① 冒号后紧跟引号，宽度 **{1,3}**——宽度证据只有 3 字：人类锚点 19 章 779 段里
#      走完前置过滤后带冒号的 ≤12 字段落**只有 2 条**，其中走①的仅 `陆明河：“……”`
#      （冒号前 3 字）。收窄到 {1,3} 的实测效果：人类侧**逐字不变**（8 / 0.212 /
#      6 章 / 1 章超阈），生成侧 258 → **260**。原因见下。
#   ② 冒号即段末，宽度 **{1,6}**——覆盖 `宋沛年：`（人类书）与 `价签：` 这类标签行。
#
# **为什么①必须是 {1,3}**（对抗审查打回项，2026-09-27）：宽度放到 {1,6} 时，生成侧
# 有 2 段 ≥4 字前缀的该形态被误豁免，而它们恰恰是本规则声称要抓的**叙述节拍行**——
# `林昭点点头：“知道了。”` / `霍铁没懂：“给谁？”`（「主语+动作+冒号+引语」）。收窄后
# 这 2 段回到命中，生成侧命中数 258 → 260（超阈章仍 34），人类侧不变。
#
# **已知能力边界**：② 分不清**人名行**与**常见名词标签行**——`宋沛年：` 与 `价签：`
# 字面同构，两侧语料实测生成侧 0 例。如实登记在 tests/unit/quality/
# test_readability_rules.py::test_short_para_speaker_line_detection_is_tight。
_SPEAKER_LINE_RE = re.compile(
    r"^(?:[^：:”“]{1,3}[：:]\s*[“「『\"]"  # 分支①：≤3 字 + 冒号 + 引语
    r"|[^：:”“]{1,6}[：:]\s*[。！？…”」]?\s*$)"  # 分支②：≤6 字 + 冒号即段末
)
# 人称代词起首的短句属叙述推进，另由 AI-PRONOUN-PILE 管，此处不重复计。
_PERSONAL_START_RE = re.compile(r"^[他她它]")
_PARA_OPENER_MAX_CHARS = 12

def count_short_paras(prose: str) -> list[str]:
    """短句独立成段的全部命中原文（供精确率抽样核查）。

    判定：段落首句（也是该段唯一可判内容）满足全部四条——
    ① 可见字 ≤ 12；② 非对白（不以引号起首、也不是 `名字：` 说话人行）；
    ③ 不含回指/指示成分；④ 非人称代词起首（那属叙述推进，另有规则管）。

    段切分走 :func:`_split_paragraphs`（``\\n+``），与同模块长段 / 相邻对白两条
    算子同口径——此前本函数独自用 ``\\n\\n``，使人类语料（一行一段）被读成整章
    单段、构造上不可能命中（见该函数注释）。

    抽样核实（2026-09-15，抽 12 条）：命中为「林策开口了」「万卷阁开了」这类
    **叙述短句独立成段**，非来源研究所述「零回指评价性评论」——故规则名按实际
    覆盖范围登记为 AI-SHORT-PARA，不冒用来源特征名。

    **本规则除「说话人行」外不做任何合法写法豁免**（2026-09-27 复核后的定论）：
    真信号（`曹淳。` 人名单独成段）与合法写法（`屯田册。` 文书抬头独立成段）在 ≤12 字
    窗口内**字面同构**，实测任何形态判据（无谓词 / 计量短语 / 字段行 / 排版件）的
    并集都要漏放约一半真信号，故那一族一律不加豁免。
    **唯一例外是 :data:`_SPEAKER_LINE_RE`（说话人行 = 排版件）**，它不适用上述否决
    理由，理由有三：① 它只命中**排版件**这一类，判据是「冒号位置」而非「段内有无
    谓语」——而「有无谓语」才是那个在 ≤12 字窗口里不可判的轴；② 两侧语料里它只对应
    **1 个人类校准实例**（`陆明河：“……”`）+ 1 处人类字段行 + 1 处生成侧标签行，
    漏放面被语料本身限死；③ 分支①的宽度按实测收窄到 {1,3} 后**不再触及生成侧的
    叙述节拍行**（`林昭点点头：“知道了。”` / `霍铁没懂：“给谁？”` 仍判命中），
    即收窄把「豁免真信号」的风险实测归零。
    """
    out: list[str] = []
    for para in _split_paragraphs(prose):
        # 整段就是那一句短句才算「独立成段」——只看段首句会把
        # 「灯芯闪了一下。苏婉清没有出声，把玉佩收回袖中。」误判成短段。
        if len(re.sub(r"\s", "", para)) > _PARA_OPENER_MAX_CHARS:
            continue
        first = re.split(r"[。！？；\n]", para, maxsplit=1)[0].strip()
        if not first:
            continue
        if _DIALOGUE_START_RE.match(first) or _PERSONAL_START_RE.match(first):
            continue
        if _SPEAKER_LINE_RE.match(first):
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

def count_long_paragraphs(
    prose: str, *, warn_chars: int = DEFAULT_LONG_PARA_WARN_CHARS
) -> list[str]:
    """超过 ``warn_chars`` 可见字的段落原文（供精确率抽样核查）。"""
    return [p for p in _split_paragraphs(prose) if visible_chars(p) > warn_chars]

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
