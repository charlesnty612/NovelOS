"""章内时点矛盾（``CONT-CLOCK-DAYBREAK`` / ``CONT-TIME-BACKSTEP``）——纯字面算子。

缺口（2026-09-18 实测，项目 ``prj_2567bb8de642``）
--------------------------------------------------
``guardrails.timeline_consistency`` / ``knowledge_leakage`` 都只校验 **state delta**
（状态机），全仓**没有任何算子读正文里的时间标记**。人工通读真实成书时发现：
``ch_2d2b57090ecd``（该书第 2 章，草稿 v2）开场写「沈砚抬头看了一眼墙上的钟，
**凌晨两点十七分**」，同一场景不隔断地写到章末「窗外的**天际线已经有一层淡淡的亮了**」
「几个字在**晨光**里格外清晰」——从凌晨两点十七分到天亮约三小时，正文里没有任何
时间推进的交代（中间只有一通电话，文中还明写沉默「十秒，二十秒，三十秒」）。
这类缺陷读者一眼能看出，而既有规则一条都抓不到。

机制（一句话，两个算子各说各的机制）
------------------------------------
**``CONT-CLOCK-DAYBREAK``**：取「时段前缀明确为深夜」的钟点（``凌晨/半夜/深夜/夜半``
＋钟点，如「凌晨两点十七分」），与**同一章里位置在其后**的「天亮类」标记
（天亮/破晓/黎明/晨光/晨曦/天色泛白/太阳升起/清晨/早上/早晨/一早…）配对；
两者之间若**没有任何时间推进标记**（第二天/次日/一夜过去/熬到/一觉…），
且间距 ≤ ``DEFAULT_MAX_GAP_CHARS``，即报一次「时钟与天色矛盾」。

**``CONT-TIME-BACKSTEP``**：把一章内**相邻**的两个「段首时点戳」映到一天内的小时数
（钟点按前缀折算：下午三点 → 15:00；时辰按起始小时折算：午时 → 11:00、亥时 → 21:00），
若后一个比前一个**早 ≥ ``DEFAULT_MIN_BACKSTEP_HOURS`` 小时**（且不属跨午夜顺行：
23:47 → 次日 03:42 不算回退），两者之间又无时间推进标记，即报一次「时点回退」。
「段首时点戳」= 该行从时点标记本身开始的时点（「亥时，内室只剩一盏油灯。」）——
它是作者给**场景**盖的时间章，与行内的引用型时点（起诉书里的历史时刻、期限语、
顺口提及的钟点）在字面形态上可区分。

名字即机制：两个算子量的都是**字面标记之间的直接矛盾**，不是叙事时间。

这不覆盖什么（照实说，防「名实不符」）
--------------------------------------
- **时长合理性**：正文写「他等了三个小时」而实际只过了一刻钟——没有任何字面标记
  可比对，看不见；
- **倒叙 / 回忆 / 预叙**：没有回述词表，靠**段首戳**与「时间推进标记」两条**近似**
  排除；写在**段中**的回述时点（「他想起三年前那个雨夜…」）与写在**段中**的正常
  场景时点（「军户们围过来的时候是午时三刻」）**都看不见**——这是有意的偏保守
  （漏 > 误，本仓实测放宽到行内后候选从 1 条涨到 3 条、两条新增全是误报）；
- **并行视角**：同一章换到另一人物的更早时间线（「她站在大楼门口…距离晚上八点的
  家宴还有不到两个小时」→ 下一段切到另一人「下午五点半」）在字面上与真实回退同形，
  本算子只能靠「段首戳」把它们一并挡掉，代价是这类**真实回退**也漏；
- **无字面标记的场景**：整章只有「夜深了」「日头偏西」这类模糊天色词、没有任何钟点/
  时辰的，一条都不判（本仓语料里这种章占多数）；
- **裸钟点不判早晚**：无时段前缀的「三点二十六分」**不参与任何判定**。本仓实证它
  指下午 15:26——同章原文「三点二十六分。距离明天上午九点的庭，还有十七个小时
  三十四分钟」（3:26 + 17:34 = 21:00 ≠ 次晨 9:00；15:26 + 17:34 才对）。任何
  把它当「凌晨 3 点」的写法都会算错，故本算子放弃这一档召回；
- **时辰的回退只在本章内可辨**：跨章时间线另有 story state 侧看守，本算子只看单章；
- **非中文正文**：只认汉字数字与阿拉伯数字，英文正文零命中。

校准（2026-09-18 / 2026-09-19。复算入口：``python scripts/ai_tone_calibrate.py
--all-projects --samples 20``——``--all-projects`` 是本次为稀有事件算子新增的开关，
默认两本样书里一处都没有；人类基线书的逐章守卫另见
``tests/unit/quality/test_continuity_time.py::test_human_baseline_has_no_clock_opportunity_and_zero_hits``）
----------------------------------------------------------------------------------------
语料：生成侧 = 本库 8 项目 92 章最新草稿 / 263,347 可见字；人类侧 = 文风锚点书榜一
《快穿之人渣洗白手册》侯府弧 19 章 / 38,566 字符（内容仓内，只读打开）＋
``author_style_samples`` 4 篇 / 9,029 字（脚本默认人类侧）。

**关键前提（必须照实说）**：人类基线书里**根本没有钟点**（「点」出现 64 次全是
「一点/有点/点头」，跨 19 章零个钟点标记），时辰只有 2 处且都不是时点（讲高粱
泡水）；``author_style_samples`` 同样零钟点。也就是说，**本算子的人类侧机会数为 0**，
「人类侧 0 命中」这句话不含信息，不能当作「对人类正文零误报」的证据。R 值在本算子
上一律不可采信（脚本输出里它显示为 ``inf``，正确读法是「无机会」而不是「零误报」）。

生成侧（口径：逐章判定）：

============================  ==========  ==========  ==================================
算子                          命中数      命中章数    人工逐条判定
============================  ==========  ==========  ==================================
``CONT-CLOCK-DAYBREAK``       1           1           1 真（ch_2d2b57090ecd）
``CONT-TIME-BACKSTEP``        1           1           1 真（prj_5be9256febad ch6）
============================  ==========  ==========  ==================================

两个算子合计 2 处 / 2 章 / 263,347 可见字 = **0.0076 / 千字**。人类侧 0 处（无机会）。

**采样先于采信（AGENTS.md 硬纪律）**：本算子的候选空间本身极小（92 章里只有 100 个
时点标记），**无法「抽 20 条命中」**——故把**加守卫前的全部候选**逐条过了一遍，
共 27 条（含一条判定为整体废弃的候选检查），逐条判定表见
``tests/unit/quality/test_continuity_time.py`` 模块 docstring。四条结论：

1. **「白天标记 → 夜晚标记」这条候选检查整体废弃**（14 条候选，14 条误报）。一章从
   「早上」写到「夜里」是**正常的跨日叙事**，本身就不是矛盾——该检查没有区分力，
   未进代码（同 AGENTS.md 的 97 命中 / 95 误报先例：抽样不合格的算子不发货）；
2. **「时点回退」原始 8 条候选里只有 1 条真**（precision 12.5%）。**加守卫后**只剩
   那一条真命中。守卫各自对应的误报类（**本仓逐条实例**）：
   - **段首戳**（行内时点多为引用）——「于**三年前十一月十九日**凌晨三时四十二分」
     （起诉书里的历史时刻）、「**距离**晚上八点的家宴还有不到两个小时」（期限语）、
     「军户们围过来的时候是午时三刻」（顺口提及）等 5 条；
   - **右邻期限语**——「子时**之前**」「子时**将近**」；
   - **跨午夜顺行**——晚上十一点四十七分 → 次日凌晨三点四十二分（字面递减、实际顺行）；
   - **时间推进标记**——出现在两标记之间即不判。
   **必须照实说的局限**：这些守卫是**在同一批候选上逐层定出来的**（8 条候选 → 4 类
   守卫），**没有留出验证集**；其中「段首戳」一条即可排除本仓全部 7 条误报，另外三条
   是纵深（在放宽到行内时曾是唯一排除手段），无本仓实例证明它们各自必要。故
   ``CONT-TIME-BACKSTEP`` 的可信度**低于** ``CONT-CLOCK-DAYBREAK``（后者 1 候选 1 真、
   零拟合）。仍然发出它的理由：它是**提示**不是闸门，且**已知**的两类残余偏差写在上面
   「不覆盖」里（段中时点戳会漏）；
3. **时间推进标记**（「第二天/次日/一夜过去/熬到/一觉醒来」）在 A 规则上是唯一的
   精确率守卫：合法写法「他熬到天亮」全靠它挡；
4. **裸钟点与「白天→夜晚」两档召回主动放弃**——前者歧义不可解（D03 实证），
   后者无区分力（结论 1）。

阈值取 ``DEFAULT_MAX_GAP_CHARS = 3000``：唯一真命中的间距 2283 字符，本仓语料**不含**
更长的候选，故该值**无法由数据分辨**——它写为常量只是留一个可校准旋钮（一章量级），
真正的精确率守卫是上面那几条。

severity 恒 ``warning``：两个算子都只看字面标记位置，**不判断正文是否真的漏了时间推进**
（合法写法同样会命中，如「他熬到天亮」——那由时间推进标记挡掉，但写法无穷），
故只作提示、不作闸门，本模块**不含任何返回 error 的路径**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 阈值常量（校准依据见模块 docstring；判别测试 tests/unit/quality/test_continuity_time.py）
# ---------------------------------------------------------------------------

# 一对标记的最大间距（原文字符）。真命中 2283——本仓语料不含更长的候选，
# 故 3000（一章量级）是不可由数据分辨的「旋钮」值，不是量出来的分离点。
DEFAULT_MAX_GAP_CHARS = 3000

# 「时点回退」的最小回退幅度（小时）。**它不能分离真假**：本仓实测真命中 10h，
# 而误报跨 3h~6h（3h×1 / 4h×3 / 6h×1）与跨午夜 18h 一档——分离靠段首戳等守卫，
# 2.0 只是把 1 小时量级的复述/折算噪声挡在门外。
DEFAULT_MIN_BACKSTEP_HOURS = 2.0

# ---------------------------------------------------------------------------
# 时段前缀 → 一天内小时数的折算
# ---------------------------------------------------------------------------

# 深夜档（本算子唯一认作「凌晨」的前缀）。裸钟点不入此档（见 docstring「不覆盖」）。
_EARLY_PERIODS = ("凌晨", "半夜", "深夜", "夜半")

# 前缀 → 折算函数。夜里/夜间 既可指 20:00 也可指 02:00，按小时分界（<6 视作凌晨侧）。
_MORNING_PERIODS = ("清晨", "清早", "早上", "早晨", "一早", "上午")
_NOON_PERIODS = ("中午", "正午", "晌午")
_AFTERNOON_PERIODS = ("下午", "午后")
_EVENING_PERIODS = ("傍晚", "黄昏", "晚上", "入夜")
_NIGHTISH_PERIODS = ("夜里", "夜间")

_PERIOD_ALT = "|".join(
    _EARLY_PERIODS + _MORNING_PERIODS + _NOON_PERIODS + _AFTERNOON_PERIODS
    + _EVENING_PERIODS + _NIGHTISH_PERIODS
)

# 时辰 → 该时辰的起始小时（子时 23:00、丑时 01:00 …… 亥时 21:00）。
# 用**起始小时**而非序号，是为了让「亥时 → 子时」自动成为顺行（21h → 23h），
# 不必再单写一条跨午夜规则。
SHICHEN_START_HOURS: dict[str, float] = {
    "子": 23.0, "丑": 1.0, "寅": 3.0, "卯": 5.0, "辰": 7.0, "巳": 9.0,
    "午": 11.0, "未": 13.0, "申": 15.0, "酉": 17.0, "戌": 19.0, "亥": 21.0,
}

# ---------------------------------------------------------------------------
# 标记正则
# ---------------------------------------------------------------------------

_CN_NUM = r"[0-9０-９〇零一二三四五六七八九十两廿]"

# 钟点：「凌晨两点十七分」「下午三点」「3 点 42 分」。**分必须有「分」字**才认定为
# 分钟（「一点一点」曾把「一点」+「一点」误配成 1:01——本仓实测 102 处「一点」全是副词）；
# 时段前缀、分钟、后缀（整/钟/左右/多/半）**三者有其一**才认定为钟点
# （见 :func:`time_markers` 的守卫）。
_CLOCK_RE = re.compile(
    rf"(?P<period>{_PERIOD_ALT})?\s*"
    rf"(?P<hour>{_CN_NUM}{{1,3}})\s*(?:点|時|时)\s*"
    rf"(?:(?P<minute>{_CN_NUM}{{1,3}})\s*分|(?P<tail>整|钟|左右|多|半))?"
)

# 冒号形态：「09:15」「3：42」
_COLON_RE = re.compile(
    rf"(?P<period>{_PERIOD_ALT})?\s*(?P<hour>[0-9０-９]{{1,2}})\s*[:：]\s*(?P<minute>[0-9０-９]{{2}})"
)

# 时辰：「巳时」「午时三刻」「亥时初」
_SHICHEN_RE = re.compile(r"(?P<name>[子丑寅卯辰巳午未申酉戌亥])时(?:初|正|[一二三]刻)?")

# 天亮类标记（只有「天亮」这一侧需要识别位置，方向恒为「时钟在前、天亮在后」）。
_DAYBREAK_RE = re.compile(
    r"天(?:就|已|快|刚)?(?:蒙蒙)?亮|破晓|拂晓|黎明|晨光|晨曦|鱼肚白"
    r"|天色(?:发白|泛白|渐亮)|东方泛白|泛起了?白|天光大亮|大亮了|天已大亮|晨色|旭日|太阳升起|日出了"
    r"|清晨|清早|早晨|早上|一早"
)

# 时间推进标记：出现在两标记之间即认为「作者交代了时间过去了」，不成矛盾。
_ADVANCE_RE = re.compile(
    r"第二天|次日|翌日|隔天|转天|几天后|三天后|半个月后|一夜(?:过去|没睡|未眠|没合眼|之间)"
    r"|熬到|熬了|熬过|一觉(?:醒来|睡到)|醒来时|睡到|直到(?:天|次)"
    r"|过了(?:一|两|三|四|几|半)(?:个)?(?:小时|时辰|钟头)"
)

# 守卫 ①（贴标记左邻，≤ ``_LEFT_TOKEN_CHARS`` 字）：计划语——标记本身是「将来的时点」。
_LEFT_PLAN_RE = re.compile(r"(?:明天|明日|后天|次日|翌日|隔天|转天|来日)$")
_LEFT_TOKEN_CHARS = 6

# 守卫 ②（贴标记右邻，≤ ``_RIGHT_REF_CHARS`` 字）：期限语——标记本身是「期限/倒计时」。
_RIGHT_REF_RE = re.compile(
    r"^(?:之前|以前|之内|将近|快到|未到|还早|还有|为止|截止|期限|限期|有效期|倒计时)"
)
_RIGHT_REF_CHARS = 12

# 左邻词扫描的边界（标点 / 引号 / 空白）：只取紧贴标记的那一小段文本。
_LEFT_BOUNDARY_RE = re.compile(r"[，。！？；：、\n\r\t\u3000 \"'“”‘’「」『』《》()（）]")

# ---------------------------------------------------------------------------
# 标记结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimeMarker:
    """正文里的一个时间标记（供抽样核对与判定复用）。"""

    kind: str  # 'clock'（钟点） | 'shichen'（时辰）
    start: int  # 原文起始偏移
    end: int  # 原文结束偏移
    text: str  # 标记原文
    hour: float | None  # 一天内小时数；None = 不可排序（裸钟点）
    period: str | None  # 时段前缀（钟点才有）
    named: str | None  # 时辰名（时辰才有）


@dataclass(frozen=True)
class TimeConflict:
    """一次章内时点矛盾（两个标记 + 判定依据），供章级 message 与抽样打印。"""

    rule_id: str
    kind: str  # 'clock_daybreak' | 'time_backstep'
    first: TimeMarker
    second: TimeMarker
    gap: int  # 两标记之间的原文字符数
    detail: str  # 人类可读的判定依据（如「凌晨两点十七分 → 晨光（相距 2283 字）」）

    @property
    def sample(self) -> str:
        """``前标记 → 后标记`` 的展示形态（samples 只供人工过目）。"""
        return f"{self.first.text}→{self.second.text}"


# ---------------------------------------------------------------------------
# 数字 / 时段折算
# ---------------------------------------------------------------------------

_CN_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "廿": 20}
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def _cn_to_int(raw: str) -> int | None:
    """中文/阿拉伯数字（0-99 档）→ int；认不出返回 None。"""
    text = raw.translate(_FULLWIDTH_DIGITS).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return _CN_DIGITS.get(text)


def _clock_hour(period: str | None, hour: int) -> float | None:
    """钟点 → 一天内小时数；**无时段前缀返回 None**（歧义不可解，不参与判定）。

    折算规则（含两条显式启发式）：
    - 深夜档（凌晨/半夜/深夜/夜半）按字面值取（``12`` → ``0``）；
    - 夜里/夜间 + 小时 < 6 视作当日凌晨侧，否则 +12；
    - 下午/午后 +12；傍晚/黄昏/晚上/入夜 +12；中午/正午/晌午 → 12。
    """
    if period is None:
        return None
    if hour == 24:
        hour = 0
    if period in _NOON_PERIODS:
        return 12.0
    if period in _AFTERNOON_PERIODS or period in _EVENING_PERIODS:
        return float(hour + 12) if hour < 12 else float(hour)
    if period in _NIGHTISH_PERIODS:
        return float(hour) if hour < 6 else float(hour + 12)
    if period in _EARLY_PERIODS:
        return 0.0 if hour == 12 else float(hour)
    return float(hour)  # 清晨/早上/早晨/一早/上午


def is_early_clock(marker: TimeMarker) -> bool:
    """该标记是否为「深夜档时钟」（``CONT-CLOCK-DAYBREAK`` 的左侧条件）。

    只认**时段前缀明确**的深夜钟点（凌晨/半夜/深夜/夜半）且小时 ≤ 6。
    裸钟点一律 False——见模块 docstring「不覆盖」的实证（「三点二十六分」= 15:26）。
    """
    return (
        marker.kind == "clock"
        and marker.period in _EARLY_PERIODS
        and marker.hour is not None
        and marker.hour <= 6
    )


# ---------------------------------------------------------------------------
# 标记提取
# ---------------------------------------------------------------------------


def time_markers(prose: str) -> list[TimeMarker]:
    """抽出正文里的全部时间标记（钟点 + 时辰），按出现位置升序。

    只抽**形态可辨认**的标记：
    - 钟点必须有「分/整/钟/左右/多/半」或时段前缀之一（「一点」这种副词不算）；
    - 时辰即十二地支 + 「时」（可带 初/正/X刻）。
    天亮类标记不进本列表（它没有可比较的时刻），由 :func:`daybreak_markers` 单独取。
    """
    out: list[TimeMarker] = []
    for m in _CLOCK_RE.finditer(prose):
        # 钟点守卫：时段前缀 / 分钟 / 后缀（整·钟·左右·多·半）至少有一个。
        # 三者全无的是「一点（也不）」「三点（意见）」这类副词/量词，不是钟点。
        if not (m.group("period") or m.group("minute") or m.group("tail")):
            continue
        hour = _cn_to_int(m.group("hour"))
        if hour is None:
            continue
        out.append(
            TimeMarker(
                kind="clock",
                start=m.start(),
                end=m.end(),
                text=m.group(0),
                hour=_clock_hour(m.group("period"), hour),
                period=m.group("period"),
                named=None,
            )
        )
    for m in _COLON_RE.finditer(prose):
        hour = _cn_to_int(m.group("hour"))
        if hour is None:
            continue
        out.append(
            TimeMarker(
                kind="clock",
                start=m.start(),
                end=m.end(),
                text=m.group(0),
                hour=_clock_hour(m.group("period"), hour),
                period=m.group("period"),
                named=None,
            )
        )
    for m in _SHICHEN_RE.finditer(prose):
        out.append(
            TimeMarker(
                kind="shichen",
                start=m.start(),
                end=m.end(),
                text=m.group(0),
                hour=SHICHEN_START_HOURS[m.group("name")],
                period=None,
                named=m.group("name"),
            )
        )
    out.sort(key=lambda mk: (mk.start, mk.end))
    return out


def daybreak_markers(prose: str) -> list[TimeMarker]:
    """天亮类标记（无时刻，只有位置）。``hour`` 恒 None。"""
    return [
        TimeMarker(
            kind="daybreak", start=m.start(), end=m.end(),
            text=m.group(0), hour=None, period=None, named=None,
        )
        for m in _DAYBREAK_RE.finditer(prose)
    ]


# ---------------------------------------------------------------------------
# 守卫
# ---------------------------------------------------------------------------


def _left_token(prose: str, pos: int) -> str:
    """标记左邻的**紧贴词**（从上一个边界标点/空白到标记起点，最多 6 字）。"""
    window = prose[max(0, pos - 24) : pos]
    cut = 0
    for boundary in _LEFT_BOUNDARY_RE.finditer(window):
        cut = boundary.end()
    return window[cut:][-_LEFT_TOKEN_CHARS:]


def _is_planned(marker: TimeMarker, prose: str) -> bool:
    """标记是否被「计划语」贴住（如「明天辰时」「次日辰时」）——是则不算现场时点。"""
    return bool(_LEFT_PLAN_RE.search(_left_token(prose, marker.start)))


def _is_deadline(marker: TimeMarker, prose: str) -> bool:
    """标记是否被「期限语」贴住（如「子时之前」「子时将近」「有效期」）——同上。"""
    right = prose[marker.end : marker.end + _RIGHT_REF_CHARS]
    return bool(_RIGHT_REF_RE.search(right))


def is_paragraph_stamp(prose: str, marker: TimeMarker) -> bool:
    """标记是否**独占段首**（该行从标记本身开始）——「段首时点戳」。

    这是 ``CONT-TIME-BACKSTEP`` 的**字面代理**守卫，代理的对象是「现场时点戳」：
    段首的时点戳是作者给**场景**盖的时间章（「亥时，内室只剩一盏油灯。」），
    行内的时点则多是**引用**——公文/起诉书里的历史时刻（「于三年前十一月十九日
    凌晨三时四十二分」）、期限语（「距离晚上八点的家宴还有不到两个小时」）、
    随口提到的钟点。两者混淆会产出整片误报（本仓实测：放宽到行内后，回退候选
    从 1 条涨到 3 条，新增两条全是引用型误报）。

    它代理不完美：作者把时点戳写在段中（「军户们围过来的时候是午时三刻」）一样是
    场景时间，会被漏掉——这是**有意的偏保守**（漏 > 误）。
    """
    line_start = prose.rfind("\n", 0, marker.start) + 1
    return not prose[line_start : marker.start].strip()


def _has_advance(prose: str, begin: int, end: int) -> bool:
    """两标记之间是否出现时间推进标记（出现了就不算矛盾）。"""
    return bool(_ADVANCE_RE.search(prose[begin:end]))


def _usable(marker: TimeMarker, prose: str) -> bool:
    """规则 A 的现场时点守卫：被计划语或期限语贴住的标记不参与判定。

    规则 B 不用本函数——它已要求标记**独占段首**，段首标记左邻必为空，
    「计划语」分支在那里恒不成立（保留在规则 A 是因为天亮标记常是行内词，
    「明**天早上**」这类计划引用必须挡掉）。
    """
    return not _is_planned(marker, prose) and not _is_deadline(marker, prose)


# ---------------------------------------------------------------------------
# 两个算子
# ---------------------------------------------------------------------------


def _clock_daybreak_conflicts(
    prose: str, *, max_gap_chars: int
) -> list[TimeConflict]:
    """``CONT-CLOCK-DAYBREAK``：深夜档时钟 → 其后的天亮类标记（近距离、无推进交代）。"""
    out: list[TimeConflict] = []
    for marker in time_markers(prose):
        if not is_early_clock(marker) or not _usable(marker, prose):
            continue
        for dawn in daybreak_markers(prose):
            if dawn.start <= marker.end:
                continue
            gap = dawn.start - marker.end
            if gap > max_gap_chars:
                continue
            if not _usable(dawn, prose):
                continue
            if _has_advance(prose, marker.end, dawn.start):
                continue
            out.append(
                TimeConflict(
                    rule_id="CONT-CLOCK-DAYBREAK",
                    kind="clock_daybreak",
                    first=marker,
                    second=dawn,
                    gap=gap,
                    detail=f"{marker.text} → {dawn.text}（相距 {gap} 字，中间无时间推进标记）",
                )
            )
    return out


def _time_backstep_conflicts(
    prose: str, *, max_gap_chars: int, min_backstep_hours: float
) -> list[TimeConflict]:
    """``CONT-TIME-BACKSTEP``：同章**相邻**两个**段首时点戳**，后者早至少 N 小时。

    相邻 = 可排序标记序列里的前后两项（``hour is None`` 的裸钟点不进序列）。
    四道守卫：① 两个标记都必须**独占段首**（:func:`is_paragraph_stamp`，行内时点多为
    引用）；② 跨午夜顺行（后标记 00:00-06:00、前标记 18:00-24:00）不算回退；
    ③ 右邻期限语（「子时之前」「子时将近」）不算现场时点；④ 中间有时间推进标记不判。
    """
    ordered = [
        m
        for m in time_markers(prose)
        if m.hour is not None and is_paragraph_stamp(prose, m)
    ]
    out: list[TimeConflict] = []
    for first, second in zip(ordered, ordered[1:]):
        gap = second.start - first.end
        if gap > max_gap_chars:
            continue
        if not (second.hour < first.hour - min_backstep_hours):
            continue
        # 跨午夜顺行：23:47 → 次日 03:42 在字面上递减，实际是顺行。
        if second.hour <= 6.0 and first.hour >= 18.0:
            continue
        if _is_deadline(first, prose) or _is_deadline(second, prose):
            continue
        if _has_advance(prose, first.end, second.start):
            continue
        out.append(
            TimeConflict(
                rule_id="CONT-TIME-BACKSTEP",
                kind="time_backstep",
                first=first,
                second=second,
                gap=gap,
                detail=(
                    f"{first.text}（{first.hour:g} 时）→ {second.text}"
                    f"（{second.hour:g} 时），回退 {first.hour - second.hour:g} 小时"
                ),
            )
        )
    return out


def time_conflicts(
    prose: str,
    *,
    max_gap_chars: int = DEFAULT_MAX_GAP_CHARS,
    min_backstep_hours: float = DEFAULT_MIN_BACKSTEP_HOURS,
) -> list[TimeConflict]:
    """章内时点矛盾全集（未过章级门；供抽样与阈值校准）。

    ``CONT-CLOCK-DAYBREAK`` 在前、``CONT-TIME-BACKSTEP`` 在后（规则顺序，稳定）。
    """
    if not prose:
        return []
    return [
        *_clock_daybreak_conflicts(prose, max_gap_chars=max_gap_chars),
        *_time_backstep_conflicts(
            prose,
            max_gap_chars=max_gap_chars,
            min_backstep_hours=min_backstep_hours,
        ),
    ]


def count_time_conflicts(prose: str, **kwargs) -> list[str]:
    """命中样例原文列表（``count_*`` 系列同形，精确率抽样核查入口）。"""
    return [hit.sample for hit in time_conflicts(prose, **kwargs)]


def scan_time_conflicts(
    prose: str,
    *,
    max_gap_chars: int = DEFAULT_MAX_GAP_CHARS,
    min_backstep_hours: float = DEFAULT_MIN_BACKSTEP_HOURS,
) -> list[dict]:
    """章级规则判定：每个算子最多出一份报告（``count`` 为该算子命中数）。

    severity **恒为 warning**（提示而非闸门）——算子只看字面标记位置，不判断正文
    是否真的漏了时间推进；本函数不含任何返回 error 的路径。
    """
    hits = time_conflicts(
        prose, max_gap_chars=max_gap_chars, min_backstep_hours=min_backstep_hours
    )
    if not hits:
        return []
    reports: list[dict] = []
    for rule_id, label in (
        ("CONT-CLOCK-DAYBREAK", "深夜时钟与天亮标记矛盾（同章、中间无时间推进交代）"),
        ("CONT-TIME-BACKSTEP", "同章相邻时点回退"),
    ):
        group = [h for h in hits if h.rule_id == rule_id]
        if not group:
            continue
        reports.append(
            {
                "rule_id": rule_id,
                "severity": "warning",
                "message": f"{label}：{len(group)} 处（例：{group[0].detail}）",
                "count": len(group),
                "samples": [h.detail for h in group[:5]],
                "excerpt": group[0].sample[:120],
            }
        )
    return reports


__all__ = [
    "DEFAULT_MAX_GAP_CHARS",
    "DEFAULT_MIN_BACKSTEP_HOURS",
    "SHICHEN_START_HOURS",
    "TimeConflict",
    "TimeMarker",
    "count_time_conflicts",
    "daybreak_markers",
    "is_early_clock",
    "is_paragraph_stamp",
    "scan_time_conflicts",
    "time_conflicts",
    "time_markers",
]
