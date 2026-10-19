"""``AI-ENDING-SUMMARY`` 的段切分口径已收敛 + 「零命中是实测不是量具假象」的看守。

背景（2026-09-27 → 2026-10-15，同一条事故的第二次）
--------------------------------------------------
``ai_patterns_text.py`` 收敛段切分口径时（本仓第一次），只修了短段 / 长段 / 对白三条
算子；``AI-ENDING-SUMMARY`` 的 ``_last_paragraph`` 仍内联**第二份**按空行切分的实现，
登记「留档待排期」——理由是「该规则判末段是不是总结句，全章扫描的误报面与短段族
不同，改动需独立实测」。

2026-10-15 批次（六）G8 做那次独立实测。复算入口是真实语料（只读）：

======================== ========================== ======================
侧                        语料                       章数 / 可见字
======================== ========================== ======================
人类侧（榜一侯府弧）      NovelOS-Content 参考书     19 章 / 37806 字
生成侧                    dev 库 ``data/novelos.db``  97 章 / 276500 字
======================== ========================== ======================

实测「末段」返回长度（人类侧 19 章）::

    口径        sum     mean    min    max    len/可见字
    旧（空行）  38566   2029.8  1319   2940   min 1.0163   ← 比可见字还长 ⇒ 它是整章
    收敛后      787     41.4    9      104    max 0.0533   ← 真末段

人类语料一行一段、``\\n\\n`` 总数 = 0 ⇒ 旧实现返回的「末段」**就是整章**。
生成侧 97/97 章都有空行，两口径**逐字相同**——收敛对生成侧零影响，修复面只在单换行
语料（外部锚点书 / CRLF 文本 / 导入稿）。这一条与 2026-09-27「人类基线 0.00 是量具
假象」是**同一形状的第二次发生**，故本文件把两条纪律都钉成用例。

本文件守三条
------------
1. **口径**：单换行正文下 ``_last_paragraph`` 返回**真末段**，不是全章（判别 M1）。
2. **取末端**：返回的是最后一段而不是第一段（判别 M2）。
3. **量具动得了 + 零命中是实测**：人类侧 19 章上算子**能看见 27~68 段**，而两侧
   命中均为 0 ⇒ 0 是「真没有」，不是「测不到」。阳性对照（合成末段含套话）必须命中。

⚠️ **为什么本文件没有「阈值不变式」用例**：实测两侧皆 0 ⇒ 分位数为 0、人类侧上沿
也是 0 ⇒ 「人类侧上沿 < warn 阈值 < 生成侧上沿」在 0.0 < x < 0.0 上**无解**。按
坑区「阈值只取本仓实测分布」与「阈值落在实测分布之外 = 死规则」，此处任何非零阈值
都是凭空造的数，故**不引入阈值**。真实结论是本规则当前在真实语料上**从不触发**
（死规则），已登记在 ``_last_paragraph`` docstring 与规则 description 里。
语料不在本机时按既有惯例 skip（环境前提缺失，非断言软化）。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from packages.core.quality.ai_patterns import (
    _AI_ENDING_SUMMARY_PHRASES,
    _last_paragraph,
    _split_paragraphs,
    scan_ai_patterns,
)
from packages.core.quality.wordcount import visible_chars

from . import _human_baseline_gate as gate

#: 标记「本文件是人类基线看守」（故障注入插件据此定位目标用例）。
HUMAN_BASELINE_GUARD = True

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEV_DB = _REPO_ROOT / "data" / "novelos.db"

# 实测登记值（2026-10-15；见模块 docstring 的复算口径）。改这些数之前先重跑逐章复算。
HUMAN_CHAPTERS = 19
HUMAN_VISIBLE_CHARS = 37806
#: 收敛后「末段长度 / 可见字」的全弧上沿（实测 0.0533）。断言上限取 0.10 —— 比实测上沿
#: 宽约 2×、比旧口径的 min 1.0163 窄约 10×，两侧都留足余量，不在分布上「贴边」。
_LAST_PARA_MAX_RATIO = 0.10
#: 旧口径下该比值的最小值（实测 1.0163 > 1）——「末段」比整章的可见字还长，因为它连
#: 标点带空白一起数了。这就是「把整章当末段扫」的可证伪指纹。
_LAST_PARA_OLD_MIN_RATIO = 1.0163


def _human_chapters() -> list[tuple[str, str]]:
    """榜一侯府弧全部 19 章（与 ``test_human_baseline_ai_thresholds`` 同一取数口径）。

    语料定位与「不可得怎么办」由 :mod:`unit.quality._human_baseline_gate` 统一处理
    （本文件**不得**再自带裸 ``pytest.skip``——那会绕开状态记录与警告）。
    """
    ref_dir = gate.require_reference_corpus(__name__)
    numbered = [
        (int(m.group(1)), p)
        for p in ref_dir.glob("*.txt")
        if (m := re.search(r"第(\d+)章", p.name))
    ]
    numbered.sort(key=lambda item: item[0])
    assert len(numbered) >= HUMAN_CHAPTERS, f"榜一侯府弧应有 ≥{HUMAN_CHAPTERS} 章"
    return [
        (f"第{no}章", path.read_text(encoding="utf-8", errors="replace"))
        for no, path in numbered
    ]


def _generated_chapters() -> list[tuple[str, str]]:
    """生成侧：dev 库全库 × 每章最新草稿（取数口径照 ``scripts/ai_tone_calibrate.py``）。"""
    if not _DEV_DB.exists():  # pragma: no cover - 环境前提缺失
        pytest.skip(f"dev 库不存在：{_DEV_DB}")
    conn = sqlite3.connect(str(_DEV_DB))
    try:
        rows = conn.execute(
            """
            SELECT ch.project_id, ch.number, d.content
            FROM chapters ch JOIN drafts d ON d.chapter_id = ch.chapter_id
            WHERE d.version = (
                SELECT MAX(version) FROM drafts WHERE drafts.chapter_id = ch.chapter_id
            )
            ORDER BY ch.project_id, ch.number
            """
        ).fetchall()
    finally:
        conn.close()
    return [(f"{pid[-4:]}#ch{num}", content) for pid, num, content in rows if content]


def _ending_hits(prose: str) -> list[str]:
    """``AI-ENDING-SUMMARY`` 命中的短语（走公开入口，不复制短语表）。"""
    hits = scan_ai_patterns(prose)
    for h in hits:
        if h["rule_id"] == "AI-ENDING-SUMMARY":
            return list(h["words"])
    return []


# ---------------------------------------------------------------------------
# 1. 口径已收敛：单换行正文取到真末段，而不是全章（判别 M1）
# ---------------------------------------------------------------------------


def test_last_paragraph_under_single_newline_prose_returns_the_real_last_segment():
    """单换行（无空行）正文下取到的是**真末段**。

    ⚠️ 判别性说明：必须用**单换行**语料。按空行切分的旧实现在这种正文里没有分隔符可
    切，返回的是**整篇**——断言 ``== "丙段。"`` 时旧实现给的是三段拼接串，必红。
    若把样例换成空行分隔，旧新两口径结果相同，本用例就退化成恒真断言。
    """
    prose = "甲段。\n乙段。\n丙段。"
    assert "\n\n" not in prose, "本用例的判别性依赖「无空行」这一前提"

    last = _last_paragraph(prose)
    assert last == "丙段。", (
        f"单换行正文下取到的不是真末段（得到 {last!r}）。"
        "旧口径在没有空行时返回整篇——这正是本条要修的 bug"
    )
    # 旧实现的指纹：返回值里装着前面的段
    assert "甲段" not in last and "乙段" not in last


def test_last_paragraph_takes_the_last_segment_not_the_first():
    """取的是**最后**一段（判别 M2）——与上条互不替代：上条只钉「不是全章」。

    M2 把 ``paragraphs[-1]`` 改成 ``paragraphs[0]`` 时，用例必红；而「末段含套话时规则
    仍应触发」的正例对它无感（首段无套话时两者都不触发），故这条是唯一的把关点。
    """
    prose = "首段的套话：这一切都要结束了。\n中段。\n尾段。"

    assert _split_paragraphs(prose) == [
        "首段的套话：这一切都要结束了。",
        "中段。",
        "尾段。",
    ], "先确认切段入口本身给出的段序正确，否则本用例测的不是取末段"
    assert _last_paragraph(prose) == "尾段。", (
        "取到的不是最后一段——段序反了或取了首段"
    )
    assert _last_paragraph(prose) != "首段的套话：这一切都要结束了。"


def test_ending_summary_rule_ignores_phrases_in_middle_segments():
    """规则只扫**末段**：套话落在中间段不得报（判别 M1 与 M3）。

    这是本条 bug 的**规则级**后果：旧口径把整章当末段，于是章中间任何一处套话都被
    报成「章尾出现总结/升华体套话」，且 ``excerpt`` 给出的是整章尾部 160 字——报错
    指向的位置与实际问题位置无关。

    M3（把 ``last_para = _last_paragraph(prose)`` 换成 ``last_para = prose``）会让
    本用例转红。
    """
    prose = "这一刻，故事落下了帷幕。\n无关的字。\n无关的字。\n收束。"

    assert "\n\n" not in prose, "本用例的判别性依赖「无空行」这一前提"
    assert not _ending_hits(prose), (
        "章中间的套话被报成章尾套话——规则扫的不是末段而是整章"
    )
    # 阳性对照：同一篇正文，套话挪到末段就必须报——否则上面那条可能只是算子坏了
    moved = "无关的字。\n无关的字。\n这一刻，故事落下了帷幕。"
    assert _ending_hits(moved) == ["这一刻", "落下了帷幕"], (
        "套话在末段却没报——算子本身失能，上一条的『不报』就没有意义了"
    )


# ---------------------------------------------------------------------------
# 2. 量具动得了 + 零命中是实测（人类侧 19 章真实语料）
# ---------------------------------------------------------------------------


def test_human_corpus_last_paragraph_is_a_segment_not_the_whole_chapter():
    """人类侧 19 章上「末段 / 可见字」比值上沿 0.0533；旧口径下沿是 1.0163（>1）。

    这一条是 2026-09-27「人类基线 0.00 是量具假象」的**同形状第二次**的回归红线：
    人类锚点书一行一段，``\\n\\n`` 总数 = 0 ⇒ 旧实现返回的「末段」是整章
    （mean 2029.8 字 > 整章可见字均值 1990）。

    判别性：把 ``_last_paragraph`` 改回按空行切分，本用例**逐章**转红（比值全 >1），
    不是靠运气命中一条。
    """
    chapters = _human_chapters()
    visible = sum(visible_chars(prose) for _, prose in chapters)
    assert visible == HUMAN_VISIBLE_CHARS, (
        f"语料已换代：可见字 {visible} 与登记值 {HUMAN_VISIBLE_CHARS} 不符，基线需重算"
    )

    worst = 0.0
    for label, prose in chapters:
        wc = visible_chars(prose)
        ratio = len(_last_paragraph(prose)) / wc
        worst = max(worst, ratio)
        assert ratio <= _LAST_PARA_MAX_RATIO, (
            f"{label} 的「末段」占了本章可见字的 {ratio:.1%}（上限 {_LAST_PARA_MAX_RATIO:.0%}）。"
            f"旧口径在该语料上实测下沿为 {_LAST_PARA_OLD_MIN_RATIO}（>1，即整章）——"
            "说明口径被改回了按空行切分"
        )
    assert worst <= _LAST_PARA_MAX_RATIO


def test_human_corpus_instrument_can_actually_move():
    """零命中必须是「真没有」而不是「测不到」——先证明量具在人类语料上动得了。

    2026-09-27 那次的教训是：0.00 那个数**永远成立**（旧口径下人类语料构造上不可能
    命中），于是任何断言都抓不住口径被改回去。本条先钉住「段切分入口在人类语料上切出
    27~68 段、末段远短于整章」，再由
    :func:`test_ending_summary_zero_hits_are_measured_not_unmeasurable` 钉住零命中。
    两条合起来才构成「0 是实测」；单看零命中恒真。
    """
    chapters = _human_chapters()
    segment_counts = [len(_split_paragraphs(prose)) for _, prose in chapters]
    assert min(segment_counts) > 1, (
        f"人类语料上段切分入口只切出 {min(segment_counts)} 段——量具动不了，"
        "此时任何零命中都不能作数"
    )
    # 12 条套话在人类 19 章**全章零出现**（不只是不在末段）：这才是「真没有」
    whole_chapter_occurrences = sum(
        prose.count(phrase) for _, prose in chapters for phrase in _AI_ENDING_SUMMARY_PHRASES
    )
    assert whole_chapter_occurrences == 0, (
        f"套话在人类语料全章出现 {whole_chapter_occurrences} 次，"
        "「零命中」的归因需重查（可能不再是「真没有」）"
    )


def test_ending_summary_zero_hits_are_measured_not_unmeasurable():
    """登记：人类侧与生成侧实测命中均为 0 ⇒ 本规则当前是**死规则**，不设阈值。

    ⚠️ **这条断言的意图是「登记」，不是「守住一个安全基线」。** 它与
    ``test_human_baseline_ai_thresholds`` 里被订正过的那条形似而实不同：后者守的是
    「人类正文不该被判违规」，守的是**非零**的安全一侧；本条守的恰恰是**零**，因为
    两边都零 ⇒ 没有任何可测分布 ⇒ **不得凭空定阈值**。

    有人往任一语料塞一段末段套话时本条转红——那正是「需要重测两条分布再谈定阈值」
    的信号，而不是「阈值该调高了」。

    生成侧章数随 dev 库增长，故只断言语料非空、不钉死章数（实测快照 2026-10-15：
    97 章 / 276500 可见字；12 条套话合计仅出现 1 次，「这一刻」，且不在末段）。
    """
    human = _human_chapters()
    generated = _generated_chapters()

    human_hits = [(label, w) for label, prose in human if (w := _ending_hits(prose))]
    assert not human_hits, (
        f"人类侧出现末段套话命中 {human_hits}——两侧不再同为 0，"
        "「无可测分布故不设阈值」的登记失效，须重测两条分布"
    )

    assert len(generated) >= 19, (
        f"生成侧语料仅 {len(generated)} 章，不足以作对照分布"
    )
    gen_hits = [(label, w) for label, prose in generated if (w := _ending_hits(prose))]
    assert not gen_hits, (
        f"生成侧出现末段套话命中 {gen_hits}——同上，须重测两条分布"
    )

    # 阳性对照：两侧都零，必须排除「算子压根不匹配」。合成末段含套话 ⇒ 必须命中。
    control = "风停了。\n从今往后，一切都将不同。".replace("从今往后", "从此以后")
    assert _ending_hits(control) == ["从此以后"], (
        f"阳性对照失能：合成末段含「从此以后」却未命中（{control!r}）——"
        "上面的零命中是算子坏了，不是语料干净"
    )


# ---------------------------------------------------------------------------
# 3. 口径收敛不得回退：模块内不再有第二份段落切分实现
# ---------------------------------------------------------------------------


def test_no_second_paragraph_split_implementation_in_ai_patterns_package():
    """``ai_patterns*.py`` 全集里按空行切段的调用点必须为 **0**。

    ⚠️ 本条是 :mod:`ai_patterns_text` 的守卫**在收敛后的延续**，不是重复它。
    既有 ``tests/unit/quality/test_readability_rules.py`` 的形状级断言原写
    ``len(inline_splits) == 1``（「内联切分应只剩 ``_last_paragraph`` 一处」，并注明
    「已登记为待修」）——G8 把那个待修项做掉之后，正确值是 **0**。

    词法边界照既有断言如实登记：谓词绑定「变量名 ``prose`` + ``.split(`` + 字面量
    空行」三者，写成别的形态（如换变量名 / 换 ``re`` API / 动态构造）本条抓不到。
    它守的是**别把空行切段再写回来**，不是「任何第三份实现都必被抓到」。
    """
    pkg_dir = Path(_last_paragraph.__globals__["__file__"]).parent
    hits: list[tuple[str, int, str]] = []
    for path in sorted(pkg_dir.glob("ai_patterns*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if 'prose.split("\\n\\n")' in line:
                hits.append((path.name, i, line.strip()))
    assert not hits, (
        "ai_patterns 包内又出现了按空行切段的实现（段切分口径分叉过一次就出过"
        f"「人类基线是量具假象」的事故）：{hits}"
    )
