"""番茄签约体检——纯规则检查函数（无 IO）。

设计要点：
- 输入结构全部为 Python 内置类型（``list[dict]``、``list[str]``），不耦合 ORM/DTO；
- 模块级词典（``CONFLICT_WORDS`` 等）以 ``tuple[str, ...]`` 给出，便于测试断言 + 扩展；
- 检查函数 :func:`run_checks` 一次性产出 :class:`CheckItem` 列表，按平台规则顺序排列；
- 每条 :class:`CheckItem` 含 ``key / level / detail / advice`` 四元组；advice 提供
  「如何修复」的引导性建议（启发式，非保证）。
- 平台依据：番茄男频公开规则（黄金三章、单章 1500-2200 字、签约窗口 2/5/8 万共 3 次机会）。
  词典词条来源为番茄编辑指南与公开网文写作共识；后续可按平台口径微调。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Final

from packages.core.quality.wordcount import visible_chars

# ---------------------------------------------------------------------------
# 词典（模块级常量，tuple 不可变；各 25-40 词贴合网文语境）
# ---------------------------------------------------------------------------

# 冲突/打脸/打压类：黄金三章要求「第一章 300 字内出冲突」，用词必须包含冲突事件。
CONFLICT_WORDS: Final[tuple[str, ...]] = (
    "打", "打脸", "退婚", "背叛", "血", "砸", "骂", "跪", "滚",
    "逼债", "赶出", "开除", "羞辱", "陷害", "废掉", "逐出",
    "悔婚", "休妻", "挑衅", "怒斥", "威胁", "嘲讽", "踩", "碾压",
    "扇", "耳光", "掐死", "捅", "刀", "剑", "杀",
)

# 金手指/系统/重生/穿越：网文「亮金手指」核心词；前两章必须出现至少 1 次。
GOLDEN_FINGER_WORDS: Final[tuple[str, ...]] = (
    "系统", "面板", "绑定", "重生", "穿越", "空间", "签到", "异能",
    "觉醒", "任务", "升级", "属性", "传承", "金手指", "老爷爷",
    "戒指", "玉佩", "魂穿", "修真", "灵气", "丹田", "灵根",
    "血脉", "天赋", "抽奖", "兑换", "加点", "修炼", "突破", "功法",
)

# 打脸/小高潮用词：番茄要求「第三章小高潮/打脸」。
FACE_SLAP_WORDS: Final[tuple[str, ...]] = (
    "打脸", "冷笑", "震惊", "倒吸", "跪下", "后悔", "不敢相信",
    "全场寂静", "哑口无言", "脸色大变", "目瞪口呆", "吓傻了",
    "反手", "一巴掌", "跪求", "崩溃", "认怂", "当场石化",
    "瞠目结舌", "面如死灰", "落荒而逃", "狼狈", "吐血",
    "呆滞", "呆住", "脸色铁青",
)

# 章末钩子用词：黄金三章要求「每章末尾留钩子」。
HOOK_WORDS: Final[tuple[str, ...]] = (
    "突然", "就在这时", "下一秒", "谁料", "竟然", "居然",
    "紧接着", "话音刚落", "猛然", "骤然", "不料", "刹那间",
    "瞬间", "恍惚间", "猛然间", "就在此时",
)

# 高频副词/描述词（AI 低质文易堆叠）；用于 echo_words 频次检测。
ECHO_WORDS: Final[tuple[str, ...]] = (
    "坚定", "瞬间", "顿时", "缓缓", "微微", "突然", "竟然",
    "明显", "似乎", "淡淡", "轻轻", "慢慢", "彻底", "完全",
    "深深", "静静", "默默", "暗暗", "悄悄", "死死", "狠狠",
    "猛然", "骤然", "悄然", "悠然", "凛然", "怦然", "截然",
    "截然不同", "心中", "眼里", "目光", "嘴角",
)

# ---------------------------------------------------------------------------
# 阈值常量（任务书给死）
# ---------------------------------------------------------------------------

# 第一章冲突窗口（前 N 字符）
_CH1_CONFLICT_WINDOW: Final[int] = 300
# 主角名扫描窗口（前 N 字符）
_CH1_PROTAG_WINDOW: Final[int] = 500
# 章末钩子扫描窗口（末尾 N 字符）
_HOOK_TAIL_WINDOW: Final[int] = 120
# 单章字数软阈值（区间外为 warn）
_CH_LEN_MIN: Final[int] = 1200
_CH_LEN_MAX: Final[int] = 2600
_CH_LEN_BEST_MIN: Final[int] = 1500
_CH_LEN_BEST_MAX: Final[int] = 2200
# 签约窗口节点（字数）
_SIGN_2W: Final[int] = 20_000
_SIGN_5W: Final[int] = 50_000
_SIGN_8W: Final[int] = 80_000
# echo_words 频次阈值：每千字
_ECHO_PER_KCH_THRESHOLD: Final[float] = 5.0


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckItem:
    """单条体检结果。

    - ``key``：机器可读标识（如 ``"ch1_conflict_300"``）；
    - ``level``：四档 ``pass`` / ``warn`` / ``fail`` / ``info``；
    - ``detail``：人读结果简述（中文）；
    - ``advice``：修复建议（启发式，非保证）。
    """

    key: str
    level: str
    detail: str
    advice: str


# ---------------------------------------------------------------------------
# 内部辅助（纯函数）
# ---------------------------------------------------------------------------


def _hit(text: str, words: tuple[str, ...]) -> bool:
    """任一词命中即 True。"""

    return any(w in text for w in words)


def _hits(text: str, words: tuple[str, ...]) -> list[str]:
    """返回命中词列表（去重保持顺序）。"""

    seen: list[str] = []
    for w in words:
        if w in text and w not in seen:
            seen.append(w)
    return seen


def _count_chars(text: str) -> int:
    """统计正文字符数（剔除空白，贴近番茄字数口径）。

    V3.7 起委托给 :func:`packages.core.quality.wordcount.visible_chars`，
    与全仓正文字数口径保持统一。函数名保留以维持既有调用链稳定。
    """

    return visible_chars(text)


def _chapter_by_number(chapters: list[dict]) -> dict[int, str]:
    """按 number 索引化（重复 number 取首条，避免静默丢数据）。"""

    out: dict[int, str] = {}
    for ch in chapters:
        n = ch.get("number")
        if isinstance(n, int) and n not in out:
            out[n] = ch.get("text", "") or ""
    return out


def _has_dialogue(text: str) -> bool:
    """第一章对话句粗判：含 ``\"…？\"`` / ``\"…！\"`` / ``「…」`` 之一。"""

    markers = ("\"", "“", "”", "「", "」")
    if not any(m in text for m in markers):
        return False
    # 中文引号包裹 + ?/！ 视为对话
    for opener, closer in (("“", "”"), ("「", "」"), ("\"", "\"")):
        i = text.find(opener)
        if i == -1:
            continue
        j = text.find(closer, i + 1)
        if j == -1:
            continue
        snippet = text[i + 1 : j]
        if "？" in snippet or "！" in snippet:
            return True
    return False


# ---------------------------------------------------------------------------
# 各项检查
# ---------------------------------------------------------------------------


def _check_ch1_conflict_300(ch1_text: str) -> CheckItem:
    head = ch1_text[:_CH1_CONFLICT_WINDOW]
    if _hit(head, CONFLICT_WORDS) or _has_dialogue(head):
        return CheckItem(
            key="ch1_conflict_300",
            level="pass",
            detail=f"第一章前{_CH1_CONFLICT_WINDOW}字含冲突事件或对话",
            advice="保持：开篇即抛出冲突或对白钩住读者",
        )
    return CheckItem(
        key="ch1_conflict_300",
        level="fail",
        detail=f"第一章前{_CH1_CONFLICT_WINDOW}字未命中冲突词或对话句",
        advice="番茄硬规则：开篇 300 字内必须出冲突或悬念对话；建议前置冲突事件或主角对白",
    )


def _check_ch1_protagonist_500(ch1_text: str, protagonist_names: list[str]) -> CheckItem:
    head = ch1_text[:_CH1_PROTAG_WINDOW]
    hit_name = next((name for name in protagonist_names if name and name in head), None)
    if hit_name:
        return CheckItem(
            key="ch1_protagonist_500",
            level="pass",
            detail=f"主角「{hit_name}」在前{_CH1_PROTAG_WINDOW}字出场",
            advice="保持：主角尽早出场建立代入",
        )
    return CheckItem(
        key="ch1_protagonist_500",
        level="fail",
        detail=f"主角名在前{_CH1_PROTAG_WINDOW}字未出现",
        advice="番茄硬规则：主角务必在前 500 字内出场；前置主角动作或自报家门",
    )


def _check_ch2_golden_finger(chapters_by_n: dict[int, str]) -> CheckItem:
    head2 = (chapters_by_n.get(1, "") + "\n" + chapters_by_n.get(2, ""))[: 2 * _CH1_PROTAG_WINDOW]
    if _hit(head2, GOLDEN_FINGER_WORDS):
        return CheckItem(
            key="ch2_golden_finger",
            level="pass",
            detail="前两章的前 1000 字内命中金手指关键词",
            advice="保持：前两章亮金手指建立期待",
        )
    return CheckItem(
        key="ch2_golden_finger",
        level="fail",
        detail="前两章的前 1000 字未命中金手指关键词",
        advice="番茄硬规则：前两章必须亮金手指；建议补系统觉醒/传承/异能等关键词与互动",
    )


def _check_ch3_climax(chapters_by_n: dict[int, str]) -> CheckItem:
    if 3 not in chapters_by_n:
        return CheckItem(
            key="ch3_climax",
            level="info",
            detail="尚未写到第三章",
            advice="后续写到第 3 章时务必给出小高潮或打脸",
        )
    ch3 = chapters_by_n[3]
    if _hit(ch3, FACE_SLAP_WORDS) or _hit(ch3, CONFLICT_WORDS):
        return CheckItem(
            key="ch3_climax",
            level="pass",
            detail="第三章含打脸/冲突高潮",
            advice="保持：第三章小高潮建立留存",
        )
    return CheckItem(
        key="ch3_climax",
        level="warn",
        detail="第三章未命中打脸/冲突高潮词",
        advice="番茄硬规则：第三章需出小高潮/打脸；考虑强化冲突对手与反转",
    )


def _check_chapter_hooks(chapters_by_n: dict[int, str]) -> list[CheckItem]:
    """每章一条；hook 缺失则为 warn。"""

    items: list[CheckItem] = []
    # 按章号升序迭代
    for n in sorted(chapters_by_n.keys()):
        text = chapters_by_n[n]
        tail = text[-_HOOK_TAIL_WINDOW:] if text else ""
        has_hook = (
            _hit(tail, HOOK_WORDS)
            or "？" in tail
            or "……" in tail
            or "!" in tail
            or "！" in tail
        )
        if has_hook:
            items.append(
                CheckItem(
                    key=f"chapter_hooks_ch{n}",
                    level="pass",
                    detail=f"第{n}章末尾 120 字含钩子",
                    advice="保持：章末悬念拉读者追更",
                )
            )
        else:
            items.append(
                CheckItem(
                    key=f"chapter_hooks_ch{n}",
                    level="warn",
                    detail=f"第{n}章末尾 120 字未含钩子词/？/……/！",
                    advice="番茄建议：每章末留悬念（突然/谁料/？/……）；强化转折或下章预告",
                )
            )
    return items


def _check_chapter_lengths(chapters_by_n: dict[int, str]) -> list[CheckItem]:
    """每章一条；超区间则 warn。"""

    items: list[CheckItem] = []
    for n in sorted(chapters_by_n.keys()):
        text = chapters_by_n[n]
        chars = _count_chars(text)
        if _CH_LEN_MIN <= chars <= _CH_LEN_MAX:
            items.append(
                CheckItem(
                    key=f"chapter_length_ch{n}",
                    level="pass",
                    detail=f"第{n}章字数 {chars}（{_CH_LEN_BEST_MIN}-{_CH_LEN_BEST_MAX} 最佳）",
                    advice="保持：当前字数落点理想",
                )
            )
        else:
            direction = "过短" if chars < _CH_LEN_MIN else "过长"
            items.append(
                CheckItem(
                    key=f"chapter_length_ch{n}",
                    level="warn",
                    detail=f"第{n}章字数 {chars}（{direction}，最佳 {_CH_LEN_BEST_MIN}-{_CH_LEN_BEST_MAX}）",
                    advice=f"番茄建议：单章 {_CH_LEN_BEST_MIN}-{_CH_LEN_BEST_MAX} 字最佳；当前 {direction}请补足或精简",
                )
            )
    return items


def _check_echo_words(chapters_by_n: dict[int, str]) -> CheckItem:
    """全文 echo 词频次：top3 任一词 >5 次/千字 → warn。"""

    total_chars = sum(_count_chars(t) for t in chapters_by_n.values())
    if total_chars == 0:
        return CheckItem(
            key="echo_words",
            level="info",
            detail="正文过短，跳过 echo 词频检测",
            advice="补足正文后再次体检",
        )
    counter: Counter[str] = Counter()
    for t in chapters_by_n.values():
        for w in ECHO_WORDS:
            counter[w] += t.count(w)
    top = counter.most_common(3)
    overflow = [
        (w, c, c / (total_chars / 1000.0))
        for w, c in top
        if (c / (total_chars / 1000.0)) > _ECHO_PER_KCH_THRESHOLD
    ]
    if not overflow:
        return CheckItem(
            key="echo_words",
            level="pass",
            detail="高频副词/描述词未超阈值",
            advice="保持：词汇多样，避免 AI 低质文堆叠",
        )
    parts = [f"「{w}」{cnt}次 ({freq:.1f}/千字)" for w, cnt, freq in overflow]
    return CheckItem(
        key="echo_words",
        level="warn",
        detail="高频副词/描述词堆叠：" + "；".join(parts),
        advice="番茄 AI 低质文严打：减少「坚定/瞬间/顿时」等高频副词重复，替换具体动作或感官描写",
    )


def _check_signing_window(total_chars: int) -> CheckItem:
    if total_chars < _SIGN_2W:
        diff = _SIGN_2W - total_chars
        return CheckItem(
            key="signing_window",
            level="info",
            detail=f"距首次申签（2万字）还差 {diff} 字",
            advice="番茄签约窗口 2万/5万/8万共 3 次机会，被拒 3 次永久无法签约",
        )
    if total_chars < _SIGN_8W:
        if total_chars < _SIGN_5W:
            window = "2万-5万"
        else:
            window = "5万-8万"
        return CheckItem(
            key="signing_window",
            level="info",
            detail=f"当前节点 {window}（共 2/5/8 万 3 次机会）",
            advice="番茄签约窗口仅 3 次机会，被拒 3 次永久无法签约；申签前自查黄金三章与钩子密度",
        )
    return CheckItem(
        key="signing_window",
        level="info",
        detail="已过全部签约窗口（>8万字）",
        advice="若仍未签约，建议复盘开篇/金手指/钩子密度或考虑其他平台",
    )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def run_checks(
    chapters: list[dict],
    protagonist_names: list[str],
) -> list[CheckItem]:
    """对一组章节执行全部签约体检检查。

    参数：
        chapters: ``[{"number": int, "text": str}, ...]``（按章号升序，重复 number 取首条）。
        protagonist_names: 主角名列表（来自 ``characters.role='protagonist'``）。

    返回：``list[CheckItem]``，按平台规则顺序排列（开篇 → 黄金三章 → 节奏 → 签约窗口）。
    少于 1 章时返回单条 ``info``「暂无正文」。
    """

    if not chapters:
        return [
            CheckItem(
                key="empty",
                level="info",
                detail="暂无正文",
                advice="写作后再次体检",
            )
        ]

    chapters_by_n = _chapter_by_number(chapters)
    ch1_text = chapters_by_n.get(1, "")

    items: list[CheckItem] = []
    # 黄金三章（前 3 项仅在有第 1 章时执行；ch2 在前两章存在其一即执行）
    if 1 in chapters_by_n:
        items.append(_check_ch1_conflict_300(ch1_text))
        items.append(_check_ch1_protagonist_500(ch1_text, protagonist_names))
        items.append(_check_ch2_golden_finger(chapters_by_n))
        items.append(_check_ch3_climax(chapters_by_n))

    # 章末钩子 + 单章字数（每章一条）
    items.extend(_check_chapter_hooks(chapters_by_n))
    items.extend(_check_chapter_lengths(chapters_by_n))

    # 词汇密度 + 签约窗口（全文统计，单条）
    items.append(_check_echo_words(chapters_by_n))
    total_chars = sum(_count_chars(t) for t in chapters_by_n.values())
    items.append(_check_signing_window(total_chars))

    return items


__all__ = [
    "CheckItem",
    "run_checks",
    "CONFLICT_WORDS",
    "GOLDEN_FINGER_WORDS",
    "FACE_SLAP_WORDS",
    "HOOK_WORDS",
    "ECHO_WORDS",
]
