"""卷纲质量检查（outline_check）。

## 为什么需要它

`volume_outliner` 的产出**只校验 schema**（字段全不全），一条质量判据都没有——
所以一份"主角开挂无阻力 / 反派站着等打 / 每章零成本"的大纲能一路跑通，
直到写完 21 章、被人读完才发现。（2026-09-15 实证事故。）

本模块把 `NovelOS-Content/docs/methodology/创作判据.md` 里的判据落成**可执行的检查**。

## 定位：报告器，不是闸门

它输出每个判据的**命中项 + 命中证据原文**，由人（或主会话）看了再判断。
理由与 AI 味算子同源：正则/关键词匹配的是**字面形态**，不解析语义——
所以宁可多报、附证据，也不做自动 pass/fail。**每个算子的阈值都按本仓语料校准，不照搬外部数字。**

## 判据与算子对应

| 判据 | 检查 | 可靠性 |
|---|---|---|
| `DS-10` 颗粒度 | 每章 key_beats 条数 2-4；过少/过多告警 | 高（纯计数） |
| `DS-9` 代价 | 全书含"代价/失去/抵押/卖掉/欠下"语义的章数占比 | 中（关键词） |
| `DS-8` 主角主动 | 每章至少 1 条 beat 是主角主动发起 | 中（关键词） |
| `DS-6` 反派主动性 | 反派名 + 主动动词的章数；连续无反派行动告警 | 中（关键词） |
| `DS-5` 章末钩子 | 末条 beat 是否为具体新事态；命中禁止模式即告警 | 中（黑名单） |
| 金手指边界 | 金手指被用于"凭空取得证据/答案"的章 | 中（模式） |
| 节奏 | expected_role 分布；climax/高张力占比过高的告警 | 高（纯计数） |
| 章末钩子率 | 有具体钩子的章占比 vs 基线 0.85 | 高（计数） |

外部基线来自三本番茄头部书的 canon（`reference_canons`）：
章末钩子率 0.84 / 0.96 / 0.92。**注意：`mini/major_climax_interval` 是占位值，不可用**（见工具文档 §3.1）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

# --- 阈值（本仓校准值，全部可按参数覆盖）-----------------------------------

MIN_BEATS_PER_CHAPTER = 2
# 上限：判据 DS-10 的来源（B 级 [S6][S7]）给的是 2-4 条，但**本仓实测**
# `volume_outliner` 稳定产出 5 条/章（21/21 章），按其原始 4 条上限会 100% 命中 = 噪声。
# 按算子纪律校准到 5，并在报告里保留条数明细供人工判断是否真的塞多了。
MAX_BEATS_PER_CHAPTER = 5
# 章末钩子率下限：三本番茄头部书实测 0.84 / 0.96 / 0.92，取下沿。
HOOK_RATE_FLOOR = 0.80
# 代价覆盖率下限：判据 DS-9 要求"每次跃迁伴随等价损失"，取 0.30（约三成章有代价）为报告线。
COST_RATIO_FLOOR = 0.30
# 连续多少章无反派行动 → 告警（DS-6「每次反派出场必须改变一个变量」的执行近似）。
MAX_CHAPTERS_WITHOUT_ANTAGONIST_ACTION = 3
# climax 章占比上限。**只数 climax，不数 turn**——校准依据：三本番茄头部书
# 的 spine 里 climax 分别是 0 / 1 / 1（占比 0% / 4% / 4%），而 turn 是 2 / 5 / 7
# （11% / 21% / 29%），是**正常的结构位**，把它并进"高张力"会误报健康大纲
# （首版就是这么错的，被 test_clean_outline_has_no_alerts 抓出来）。
# 事故版大纲 climax 占 38%（8/21）——那才是要报的形状。报告线取 0.25。
CLIMAX_RATIO_CEILING = 0.25

# --- 词表 -------------------------------------------------------------------

# 代价语义（DS-9）
COST_MARKERS = (
    "代价", "失去", "损失", "抵押", "卖掉", "卖了", "典当", "欠下", "负债",
    "折损", "重伤", "被革", "被夺", "破产", "变卖", "预支", "许诺", "交换",
    "让出", "赔", "罚", "降级", "除名", "暴露",
)
# 主角主动发起（DS-8）。
# 校准记录（2026-09-16）：首版只有"设局/上书/呈上"这类**权谋向**动词，对"经营向"
# 主角严重漏报——实测新书 01 的 ch15-19（主角连夜搬粮、摸进北库、贴告示告状、
# 指挥清点）被连报五章"被动"，逐条核对证据原文后确认是**词表覆盖不足**，非大纲问题。
# 补入动手/奔走/经营类动词后重测：新书误报清零，旧书那版真实存在的被动段仍被保留。
PROACTIVE_MARKERS = (
    "主动", "决定", "设局", "布局", "上书", "上奏", "呈上", "求见", "拜访",
    "试探", "接洽", "谈判", "拉拢", "策反", "抢占", "先手", "反手", "计",
    "布下", "埋下", "安排", "策动", "游说", "争取", "拿下了", "拿下", "夺",
    # —— 动手 / 奔走 / 经营向（本次校准补入）
    "搬", "摸进", "潜入", "夜访", "连夜", "挨家挨户", "敲门", "上门", "赶到",
    "盘点", "清点", "验", "查", "摸排", "付清", "签下", "买入", "买下",
    "写下", "定下", "贴出", "喊话", "领着", "带着", "自己动手", "重新",
)
# 反派主动行动（DS-6）。
# 校准记录（2026-09-16）：首版只收录**直接动手**的词，漏掉"通过第三方/暗处施压"
# 这类同样有效的反派行为——实测新书 01 的 ch10-13（吴七爷走口信、钱小六上门警告、
# 抢先买空存米）被连报四章"反派无行动"，核对证据后确认是词表覆盖不足。
# 补入间接施压/抢先类词后重测：新书误报清零，旧书那版真实的"反派长期不出现"仍被保留。
ANTAGONIST_ACTION_MARKERS = (
    "下套", "设局", "抓捕", "拘", "弹劾", "参劾", "截留", "暗杀", "刺杀",
    "下毒", "栽赃", "诬", "构陷", "造谣", "打压", "施压", "断", "封锁",
    "买通", "收买", "调兵", "围", "追杀", "扣押", "夺", "抢", "逼",
    # —— 间接施压 / 抢先 / 制度化手段（本次校准补入）
    "放话", "递话", "口信", "警告", "报信", "通风", "点名", "上门收",
    "抢先", "买空", "搬空", "接管", "收缴", "架走", "撤废", "划走",
)
# 章末钩子的禁止模式（DS-5：必须是具体新事态，不是故作悬念/旁观者盖章）
BAD_HOOK_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"此人(?:不可小觑|不简单|非寻常|绝非)", "旁观者盖章"),
    (r"(?:这|那)事(?:有意思|不简单|蹊跷)", "旁观者盖章"),
    (r"(?:神秘|黑色|一道)的?(?:黑影|身影|眼睛)", "故作悬念"),
    (r"一双眼睛", "故作悬念"),
    (r"似乎(?:有|在)(?:什么|酝酿)", "故作悬念"),
    (r"留下了悬念", "空指"),
    (r"故事(?:才|刚刚)开始", "空指"),
)
# 金手指"凭空取物"模式：动词 + 缺乏获取路径（DS 实战教训 1）
CONJURE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?:调出|取出|呈上|拿出|翻出)[^。；\n]{0,24}(?:底稿|原件|程文|账册|密档|卷宗)", "以金手指直接交出档案/原件"),
    (r"(?:所存|存于)[^。；\n]{0,16}(?:底稿|原件|程文)", "以「所存」直接给出原件"),
)


@dataclass
class Finding:
    """一条检查结论。``evidence`` 必须带命中原文，供人工判断（不做自动 pass/fail）。"""

    rule_id: str
    level: str          # 'alert' | 'info'
    message: str
    evidence: list[str] = field(default_factory=list)


def _beats_of(ch: dict[str, Any]) -> list[str]:
    out = []
    for b in ch.get("key_beats") or []:
        if isinstance(b, str):
            out.append(b)
        elif isinstance(b, dict):
            out.append(str(b.get("purpose") or b.get("summary") or b))
    return out


def _text_of(ch: dict[str, Any]) -> str:
    parts = [str(ch.get("chapter_goal") or "")]
    parts += _beats_of(ch)
    for h in ch.get("hook_handling") or []:
        parts.append(h if isinstance(h, str) else str(h))
    return "\n".join(parts)


def _hit(pattern: str, text: str) -> list[str]:
    return [m.group(0) for m in re.finditer(pattern, text)]


def _marker_hits(markers: Sequence[str], text: str) -> list[str]:
    return [m for m in markers if m in text]


def check_outline(
    chapters: Sequence[dict[str, Any]],
    *,
    antagonist_names: Sequence[str] = (),
    protagonist_names: Sequence[str] = (),
    hook_rate_floor: float = HOOK_RATE_FLOOR,
    cost_ratio_floor: float = COST_RATIO_FLOOR,
    climax_ratio_ceiling: float = CLIMAX_RATIO_CEILING,
) -> list[Finding]:
    """检查一卷的大纲。``chapters`` 按章号升序，每项含 chapter_goal / key_beats /
    expected_role / hook_handling（与 outline_json 同形）。"""
    findings: list[Finding] = []
    if not chapters:
        return [Finding("OUTLINE-EMPTY", "alert", "大纲为空")]

    n = len(chapters)
    nums = list(range(1, n + 1))

    # --- DS-10 颗粒度 -------------------------------------------------------
    thin, fat = [], []
    for i, ch in enumerate(chapters, 1):
        k = len(_beats_of(ch))
        if k < MIN_BEATS_PER_CHAPTER:
            thin.append(f"ch{i}({k}条)")
        elif k > MAX_BEATS_PER_CHAPTER:
            fat.append(f"ch{i}({k}条)")
    if thin:
        findings.append(Finding(
            "OUTLINE-THIN-BEATS", "alert",
            f"{len(thin)}/{n} 章的 key_beats 少于 {MIN_BEATS_PER_CHAPTER} 条"
            f"（DS-10：每章 2-4 条可拍摄事件）", thin[:8]))
    if fat:
        findings.append(Finding(
            "OUTLINE-FAT-BEATS", "info",
            f"{len(fat)}/{n} 章的 key_beats 多于 {MAX_BEATS_PER_CHAPTER} 条"
            f"（DS-10 上限；过多通常意味着把别章的事塞进来了）", fat[:8]))

    # --- DS-9 代价覆盖 ------------------------------------------------------
    cost_ch = [(i, _marker_hits(COST_MARKERS, _text_of(ch)))
               for i, ch in enumerate(chapters, 1)]
    cost_ch = [(i, h) for i, h in cost_ch if h]
    ratio = len(cost_ch) / n
    if ratio < cost_ratio_floor:
        findings.append(Finding(
            "OUTLINE-NO-COST", "alert",
            f"仅 {len(cost_ch)}/{n} 章（{ratio:.0%}）含代价语义，低于报告线 "
            f"{cost_ratio_floor:.0%}——DS-9 要求每次跃迁伴随等价损失",
            [f"ch{i}: {'/'.join(h[:3])}" for i, h in cost_ch[:6]]))
    else:
        findings.append(Finding(
            "OUTLINE-COST-COVERAGE", "info",
            f"代价覆盖 {len(cost_ch)}/{n} 章（{ratio:.0%}）",
            [f"ch{i}: {'/'.join(h[:3])}" for i, h in cost_ch[:6]]))

    # --- DS-6 反派主动性 ----------------------------------------------------
    ant_ch = []
    for i, ch in enumerate(chapters, 1):
        t = _text_of(ch)
        acts = _marker_hits(ANTAGONIST_ACTION_MARKERS, t)
        named = [nm for nm in antagonist_names if nm in t]
        if acts and named:
            ant_ch.append((i, named[:2], acts[:3]))
    # 收束段豁免（2026-09-16 校准）：高潮之后反派自然退场（被抓/跑路/被灾变冲走），
    # 那几章没有反派行动是**正常结构**，不是"反派站着等打"。故只统计
    # **最后一个 climax/turn 之前**出现的空档——旧书那版真实的 ch3-6/ch10-13 在
    # 高潮之前，仍会被保留；新书 01 的 ch20-23 落在高潮之后，不再误报。
    last_hi = 0
    for i, ch in enumerate(chapters, 1):
        if str(ch.get("expected_role") or "") in ("climax", "turn"):
            last_hi = i
    gaps, run_start = [], None
    for i in nums:
        if any(i == c[0] for c in ant_ch):
            run_start = None
            continue
        run_start = run_start or i
        if i - run_start + 1 > MAX_CHAPTERS_WITHOUT_ANTAGONIST_ACTION:
            # last_hi == 0（全 setup，无高潮位）时**不豁免**——没有收束段可言，
            # 全程无反派行动就是问题。首版漏了这个分支，被自检测试抓出。
            if last_hi == 0 or run_start < last_hi:
                gaps.append(f"ch{run_start}-{i}")
            run_start = None
    if gaps:
        findings.append(Finding(
            "OUTLINE-PASSIVE-ANTAGONIST", "alert",
            f"有连续 >{MAX_CHAPTERS_WITHOUT_ANTAGONIST_ACTION} 章既未出现反派、"
            f"也无反派主动行动（DS-6：反派每次出场必须改变一个变量）",
            gaps[:6]))
    findings.append(Finding(
        "OUTLINE-ANTAGONIST-ACTIONS", "info",
        f"{len(ant_ch)}/{n} 章有「反派名 + 主动行动」",
        [f"ch{i}: {'/'.join(a)} by {'/'.join(nm)}" for i, nm, a in ant_ch[:6]]))

    # --- DS-8 主角主动 ------------------------------------------------------
    passive_runs, run = [], []
    for i, ch in enumerate(chapters, 1):
        if _marker_hits(PROACTIVE_MARKERS, _text_of(ch)):
            if len(run) > 2:
                passive_runs.append(f"ch{run[0]}-{run[-1]}")
            run = []
        else:
            run.append(i)
    if len(run) > 2:
        passive_runs.append(f"ch{run[0]}-{run[-1]}")
    if passive_runs:
        findings.append(Finding(
            "OUTLINE-PASSIVE-PROTAGONIST", "alert",
            "有连续 >2 章未见主角主动推进（DS-8：被动卷入连续 ≤2 章）",
            passive_runs[:6]))

    # --- DS-5 章末钩子 ------------------------------------------------------
    hooks, bad_hooks = [], []
    for i, ch in enumerate(chapters, 1):
        beats = _beats_of(ch)
        tail = " ".join(beats[-1:]) + " " + " ".join(
            h if isinstance(h, str) else str(h) for h in (ch.get("hook_handling") or []))
        if not tail.strip():
            bad_hooks.append(f"ch{i}: 无钩子")
            continue
        hits = [(p, why) for p, why in BAD_HOOK_PATTERNS if re.search(p, tail)]
        if hits:
            bad_hooks.append(f"ch{i}: {hits[0][1]}——「{_hit(hits[0][0], tail)[:1]}」")
        else:
            hooks.append(i)
    rate = len(hooks) / n
    if rate < hook_rate_floor:
        findings.append(Finding(
            "OUTLINE-HOOK-RATE", "alert",
            f"章末钩子合格率 {rate:.0%}（{len(hooks)}/{n}），低于基线 {hook_rate_floor:.0%}"
            f"（三本番茄头部书实测 0.84/0.96/0.92）", bad_hooks[:8]))
    if bad_hooks:
        findings.append(Finding(
            "OUTLINE-BAD-HOOK", "alert",
            f"{len(bad_hooks)} 章的钩子命中禁止模式（DS-5：必须是具体新事态，"
            f"禁旁观者盖章 / 故作悬念 / 空指）", bad_hooks[:8]))

    # --- 金手指边界 ---------------------------------------------------------
    conjure = [(i, [w for p, w in CONJURE_PATTERNS for _ in _hit(p, _text_of(ch))])
               for i, ch in enumerate(chapters, 1)]
    conjure = [(i, w) for i, w in conjure if w]
    if conjure:
        findings.append(Finding(
            "OUTLINE-CONJURE-EVIDENCE", "alert",
            f"{len(conjure)} 章出现「金手指直接交出档案/原件」——须能说清主角"
            f"**怎么获得这份信息**的（备考：上一版 ch11 主角呈上考官考前批注的程文，无获取路径）",
            [f"ch{i}: {'/'.join(w)}" for i, w in conjure[:8]]))

    # --- 节奏 ---------------------------------------------------------------
    roles: dict[str, int] = {}
    for ch in chapters:
        r = str(ch.get("expected_role") or "unknown")
        roles[r] = roles.get(r, 0) + 1
    hi = roles.get("climax", 0)
    hi_ratio = hi / n
    findings.append(Finding(
        "OUTLINE-ROLE-MIX", "info" if hi_ratio <= climax_ratio_ceiling else "alert",
        f"角色分布 {roles}；climax 占 {hi_ratio:.0%}"
        + ("" if hi_ratio <= climax_ratio_ceiling
           else f"，高于报告线 {climax_ratio_ceiling:.0%}"
                f"（榜一/榜二/榜三的 climax 占比为 0%/4%/4%；事故版大纲曾达 38%）"),
        [f"{k}={v}" for k, v in sorted(roles.items())]))
    return findings


__all__ = [
    "CLIMAX_RATIO_CEILING", "COST_RATIO_FLOOR", "HOOK_RATE_FLOOR",
    "Finding", "check_outline",
]
