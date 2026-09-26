"""测试用中性正文填充：可见字数可控，且不触发任何确定性检测算子（2026-09-17 抽取）。

起因：多处 fixtures 用**单段** ``"中" * N``（或 ``"正文" * N``）充字数。那一整段
就是「手机上一段 N 行实心字」的病理样本——可读性算子会**正确**命中它
（``AI-LONG-PARA`` 单段 >140 可见字 → error、``AI-DIALOGUE-LOW`` 通篇零对话）。
但那些用例测的是字数带 / 题材核销 / 预检分腿，不是排版，被长段规则判红属于
「fixture 是伪样本」而不是「规则误报」——故抽取本模块统一替换。

**2026-09-18 二次重写（本版）**：上一版用 6 句**循环**填充，句子池被反复搬用——
实测 13 字 shingle 重复率 **0.71（300 字）/ 0.94（1300 字）/ 0.97（3000 字）**，
trigram 重复率同为 0.72~0.97。也就是说「中性」填充本身是 71~97% 重复的文本：
它不但让 ``AI-BEAT-REPEAT`` 这类新算子**正确地**报警，还把 ``score_style`` 的
trigram 子分（⇒ ``RULE_STYLE_REPETITION_TRIGRAM``，P0-1 起是 confirm 档）拖下水——
任何拿它当正文的用例都在测一个病理样本。故本版改为**组合式生成**，保证：

- **13 字 shingle 重复率 0.0000（300 / 1300 / 3000 字实测全为 0.0000）**；
  重写前 0.71 / 0.94 / 0.97。本仓 92 章真实草稿的中位是 0.000、max 0.026；
- ``scan_ai_patterns`` 在 300~3000 全部实测长度上返回 ``[]``（零命中）；
- ``req_q7``（AI 痕迹自检三项）零命中；``score_style`` ≥900 字时满分、
  ≥1200 字时只因 trigram 子分扣 15（见下「诚实交代」）。

``neutral_prose(chars)`` 的保证：

- **恰好** ``chars`` 个 ``visible_chars``（用例断言的是 ±15% / ±30% 这类精确百分比）；
- 多段（每段 2 句）：所有段 ≤ 100 可见字 ⇒ 不触发 ``AI-LONG-PARA`` /
  ``AI-SHORT-PARA``（尾部截断产生的短段会并回上一段）；
- 成对 “…” 对话占比 ≈ 15~16% ⇒ 不触发 ``AI-DIALOGUE-LOW``（阈值 12%）；
- 不触发 ``AI-DIALOGUE-ECHO``：相邻对白由不同槽位组合成句，用词不撞车；
- 无破折号 / 省略号 / 解释腔 / 禁用词 / 三连同一开头 / 他她排比 / 章尾套话；
- 不触发 ``AI-BEAT-REPEAT``：句读片段由（人物 × 地点 × 物件 × 动作 × 环境）
  组合生成，槽位池大小**两两互质**且步长互质 ⇒ 任意两个槽位的组合周期 =
  两者之积（820~2491 句）远超单章句数，同一小句不会在本章内复现；
- **不含任何时点标记**（时辰 / 钟点 / 天亮类词一个都不用）：2026-09-18 实测，
  原稿拿「清晨/晌午/三更/辰时/未时…」当槽位，词序由槽位步长决定，于是同章出现
  「黄昏 → 破晓」这类物理上不可能的时点回退，被并行交付的 ``CONT-TIME-BACKSTEP``
  **正确地**命中——这是 filler 的构造造成的伪样本，故整条时间轴从槽位里移除。

**trigram 重复率的诚实交代**：本 filler 随长度从 0.003（300 字）升到 0.200（3000 字），
落在生成侧实测带内（92 章 p50 0.1333 / p90 0.1749 / max 0.2097）——这不是「重复的正文」：
trigram 抓的是「的了」「了一声」这类**功能词搭配的天然冗余**，任何中文长文都会有
（人类锚点书 19 章 mean 0.0754 / max 0.0949，9/19 章也超旧阈值 0.08）。**2026-09-18
阈值重定后**实测：≤2300 字无 issue（2300 字 = 0.1584）、约 2350 字起为 warn 档
（``gate=auto``，无任何后果；3000 字 = 0.2001）、约 4000 字才够 confirm 阈值 0.25
（3600 字 = 0.2346、4000 字 = 0.2516）。用例最长只取到 3000 字 ⇒ 当前 filler 在任何
用例里都不会要求签字；若将来要用 ≥4000 字的 filler，**先改 filler、别改阈值**。
阈值该不该再调是**规则校准**问题（判别入口 ``scripts/ai_tone_calibrate.py`` /
``tests/unit/quality/test_trigram_tiers.py``），不是 filler 该绕开的问题——不得为了让
本文件「零命中」去调检测算子（AGENTS.md 硬纪律：不许为坏 fixture 削弱检测）。

需要「句长完全均一」之类的特殊分布时不要用本 helper（那类用例自带 fixture）。
"""

from __future__ import annotations

from functools import lru_cache

from packages.core.quality.wordcount import visible_chars

# ---------------------------------------------------------------------------
# 槽位池（全部 2 字：任何槽位内部都构不成 trigram，重复成本只落在跨槽窗口上）
# ---------------------------------------------------------------------------
# 池大小 41 / 43 / 47 / 53 两两互质；配合互质步长，任意**两个**槽位的组合在
# lcm(p_i, p_j) ≥ 41×43 = 1763 句内不重复（单章最多约 150 句）。
PEOPLE: tuple[str, ...] = (
    "老周", "阿禾", "陈五", "小满", "三婶", "铁牛", "秋娘", "二柱", "老陶", "素云",
    "栓子", "桂姨", "石生", "冬梅", "水生", "满仓", "春桃", "阿桂", "老鲁", "阿贵",
    "阿福", "阿顺", "阿元", "阿平", "老康", "老程", "老卞", "老季", "阿顾", "阿乔",
    "阿黎", "阿盛", "阿余", "阿封", "阿姚", "阿贺", "阿嵇", "阿屈", "阿晏", "阿路",
    "阿童",
)
PLACES: tuple[str, ...] = (
    "渡口", "石桥", "后巷", "祠堂", "廊檐", "铺面", "仓房", "井台", "城门", "河滩",
    "田埂", "茶棚", "库房", "灶间", "天井", "马厩", "晒场", "磨坊", "油坊", "染坊",
    "学塾", "药铺", "米行", "布庄", "当铺", "酒肆", "客栈", "驿馆", "船坞", "货栈",
    "柴房", "水榭", "棋亭", "报房", "砧板", "灶台", "磨盘", "帐房", "铁铺", "糖坊",
    "石阶", "竹棚", "盐仓",
)
OBJECTS: tuple[str, ...] = (
    "麻袋", "竹筐", "账册", "铜锁", "木牌", "草绳", "秤砣", "油纸", "陶罐", "铁钉",
    "布匹", "盐包", "灯盏", "铜壶", "竹篓", "瓦罐", "木匣", "布囊", "鱼篓", "绳索",
    "蒲扇", "铁锹", "筛子", "簸箕", "铜钱", "木桶", "竹篮", "纸包", "锡壶", "石臼",
    "铁锅", "陶碗", "瓷瓶", "木杵", "铜铃", "灯笼", "火折", "算盘", "笔墨", "印章",
    "线团", "木尺", "铜镜", "陶瓮", "木盆", "竹席", "草鞋",
)
ACTIONS: tuple[str, ...] = (
    "清点", "捆扎", "擦拭", "翻检", "归拢", "码放", "晾晒", "挑选", "称量", "修补",
    "封存", "搬运", "叠放", "收拾", "整理", "收拢", "归置", "掸扫", "检查", "缝补",
    "涮洗", "点数", "查验", "安置", "冲洗", "拆解", "封缄", "装订", "挑拣", "浸洗",
    "切分", "分类", "归总", "编号", "记账", "修整", "加固", "晾干", "烘干", "存放",
    "起运", "标记", "分拣", "清运", "归堆", "校对", "誊写", "翻晒", "圈点", "抹拭",
    "冲刷", "晒干", "扎紧",
)
# 「环境」槽位（替代原先的时辰槽位）：**刻意不含任何时点标记**。
# 2026-09-18 实测教训：原稿用「清晨/晌午/三更/辰时/未时…」当槽位，词序由槽位步长决定，
# 于是同一章里出现「黄昏 → 破晓 → 午后」这类**物理上不可能的时点回退**——并行交付的
# ``continuity_time.scan_time_conflicts``（``CONT-TIME-BACKSTEP``）**正确地**命中了它。
# 这是 filler 的构造造成的伪样本（不是规则误报），故整条时间轴从槽位里移除：
# 检测器看不见时点标记 ⇒ 这一类未来算子对本文件零影响。
SETTINGS: tuple[str, ...] = (
    "檐下", "雨里", "风里", "灯前", "雾中", "水边", "坡上", "树下", "桥边", "巷尾",
    "屋里", "院里", "门外", "窗前", "案前", "阶前", "篱边", "灶前", "廊前", "墙根",
)

# 句型模板：**固定串一律 ≤2 字**（含标点）。任何 ≥3 字的重复固定串都会同时抬高
# trigram 重复率与被 AI-BEAT-REPEAT 判为「同一小句搬用两次」（首版实测：
# 「退后两步看了看」「把数目记在掌心」这类 6~7 字固定串被正确命中）。
# 句长刻意参差（9~19 字），避免 `req_q7` 的句长标准差过平判定。
_NARRATION: tuple[str, ...] = (
    "{p}在{l}{a}着{o}。",
    "{s}，{p}把{o}{a}好。",
    "{p}又{a}过{o}。",
    "{l}的{o}，{p}{a}过。",
    "{s}的{l}，{p}{a}着{o}。",
    "{l}那边，{p}{a}完{o}。",
    "{p}把{o}{a}好，{s}再{a}。",
    "{s}{p}在{l}，{a}着{o}。",
    "{p}{a}着{o}，{s}没歇。",
    "{p}拿{o}{a}，{s}的{l}亮着。",
    "{l}的{p}，{a}过{o}。",
    "{p}{a}完{o}，往{l}去。",
    "{s}，{l}的{o}，{p}{a}。",
    "{p}在{l}{a}，{a}得慢。",
    "{s}{p}在{l}{a}着{o}，{p}没停手。",
    "{l}的{o}{a}完，{s}的{l}还{a}着。",
    "{p}把{l}的{o}{a}好，{s}又{a}过。",
    "{s}里{p}在{l}{a}着{o}，{p}没{a}。",
)
_DIALOGUE: tuple[str, ...] = (
    "“{o}我{p}收着。”",
    "“{s}{a}{o}。”",
    "“{p}，{o}{a}完。”",
    "“{o}归你。”",
    "“{l}的{o}我取。”",
    "“{a}{o}，{s}再{a}。”",
)

_STRIDES: dict[str, int] = {"p": 1, "l": 7, "o": 13, "a": 11, "s": 3}
_SENTENCES_PER_PARAGRAPH = 2
# 短段阈值（与 ai_patterns.count_short_paras 同口径：整段可见字 ≤12 即为「短段」）。
_SHORT_PARA_CHARS = 12
# 长段阈值上限（与 ai_patterns.DEFAULT_LONG_PARA_WARN_CHARS 同口径）：合并尾段时必须守住。
_PARA_MAX_CHARS = 100


def _fill(template: str, i: int) -> str:
    """按第 ``i`` 句的槽位下标填充模板（纯函数；同 ``i`` 恒定同结果）。"""
    return template.format(
        p=PEOPLE[(i * _STRIDES["p"]) % len(PEOPLE)],
        l=PLACES[(i * _STRIDES["l"]) % len(PLACES)],
        o=OBJECTS[(i * _STRIDES["o"]) % len(OBJECTS)],
        a=ACTIONS[(i * _STRIDES["a"]) % len(ACTIONS)],
        s=SETTINGS[(i * _STRIDES["s"]) % len(SETTINGS)],
    )


def _truncate_to(text: str, chars: int) -> str:
    """从尾部去掉可见字符直到恰好剩 ``chars`` 个（空白不计入）。"""
    excess = visible_chars(text) - chars
    if excess <= 0:
        return text.rstrip()
    removed = 0
    idx = len(text)
    while removed < excess:
        idx -= 1
        if not text[idx].isspace():
            removed += 1
    return text[:idx].rstrip()


def _merge_short_tail(text: str) -> str:
    """截断把尾段削成短段时，并回上一段（``AI-SHORT-PARA`` 只看「整段就是短句」）。

    合并**不改变**可见字数（分隔符是空白）；上一段并后若会超长段阈值则放弃合并
    （宁可留一个短段，也不造一个长段——单段短段在 ≥1000 字正文里够不到
    ``AI-SHORT-PARA`` 的堆积门 1.0/千字）。
    """
    parts = text.split("\n\n")
    if len(parts) < 2 or visible_chars(parts[-1]) > _SHORT_PARA_CHARS:
        return text
    if visible_chars(parts[-2] + parts[-1]) > _PARA_MAX_CHARS:
        return text
    return "\n\n".join(parts[:-2] + [parts[-2] + parts[-1]])


@lru_cache(maxsize=64)
def neutral_prose(chars: int) -> str:
    """恰好 ``chars`` 可见字的中性正文（多段 + 含约 1/6 对话；确定性、可缓存）。

    生成方式见模块 docstring：槽位池两两互质 + 互质步长 ⇒ 单章内不出现重复小句。
    """
    if chars <= 0:
        return ""
    paragraphs: list[str] = []
    total = 0
    sentence_index = 0
    dialogue_index = 0
    while total < chars:
        chunk: list[str] = []
        for slot in range(_SENTENCES_PER_PARAGRAPH):
            # 对话句固定落在**段首**（``ai_patterns._dialogue_lines`` 只认以 “ 起首的段），
            # 隔段出现 ⇒ 对话占比约 15~16%（阈值 12%）；对话模板用独立计数器轮转
            # （用句序 % 6 会与「隔段」的步长 4 共振，只用到 3 个模板——首版实测）。
            if slot == 0 and len(paragraphs) % 2 == 0:
                template = _DIALOGUE[dialogue_index % len(_DIALOGUE)]
                dialogue_index += 1
            else:
                template = _NARRATION[sentence_index % len(_NARRATION)]
            sentence = _fill(template, sentence_index)
            sentence_index += 1
            chunk.append(sentence)
            total += visible_chars(sentence)
        paragraphs.append("".join(chunk))
    return _merge_short_tail(_truncate_to("\n\n".join(paragraphs), chars))
