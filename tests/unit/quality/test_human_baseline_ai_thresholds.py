"""阈值不得再回到「死规则」，也不得靠**坏量具**得出结论：以文风锚点书（榜一）
的人类基线为看守。

背景（2026-09-16 实测，复算入口 ``scripts/ai_tone_calibrate.py``）：

==================== ===================== ==================== ==========
算子                 榜一侯府弧（人类基线） 新书 01 全弧（生成侧） 旧阈值
==================== ===================== ==================== ==========
``AI-PUNCT-ABUSE``   0.19 / 千字            1.73 / 千字           3.5
``AI-SHORT-PARA``    ~~0.00~~ → **0.212**  2.064 / 千字          5.5
==================== ===================== ==================== ==========

**2026-09-27 订正：``AI-SHORT-PARA`` 的「人类基线 0.00」是量具假象，不是实测。**
``count_short_paras`` 当时独自用 ``\\n\\n`` 切段，而榜一侯府弧 19 章的 ``\\n\\n``
总数 = **0**（外部语料一行一段）⇒ 19 章被读成 19 个整章大段，每段都远超 12 字
上限，该规则在人类语料上**构造上不可能命中**。同模块的长段 / 相邻对白两条算子早已
用 ``\\n+``——口径分叉过一次。口径统一后重测（详见
``packages/core/quality/ai_patterns.py::DEFAULT_SHORT_PARA_RATE_PER_1K``）：
人类侧 8 命中 / 0.212 每千字（改口径、尚未叠加说话人行守卫时是 10 / 0.265），
生成侧 258 命中 / 2.064 每千字，**分离度 9.7×**。

两条纪律写进本文件防回退：

1. **人类基线不得被判违规**（逐章，榜一侯府弧前 3 章零命中）；
2. **阈值必须落在实测分布之内**：高于「人类基线 × 约 5 倍」即等于放弃判定，
   故给两个常量钉死上限——改回 3.5 / 5.5 本文件必红。

外加 2026-09-27 新增的第三条纪律：

3. **登记的基线数字必须能被复算出来**——``AI-SHORT-PARA`` 的人类基线原来登记
   0.00 且**永远成立**（口径一改就变），这类「恒真的断言」不看守任何东西。
   本文件按全弧 19 章逐章复算命中数 / 每千字 / 超阈值章数，把数字钉在实测值上：
   **把段切分改回 ``\\n\\n`` 必然转红**（人类侧命中数掉到 0）。

正文数据在内容仓（三仓分离：正文不进软件仓），按
``NOVELOS_CONTENT_DIR`` → ``DEFAULT_CONTENT_DIR`` → 软件仓同级目录 的顺序定位；
内容仓不在本机时跳过（环境前提缺失，非断言软化）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from packages.core.content_sync.paths import DEFAULT_CONTENT_DIR
from packages.core.quality.ai_patterns import (
    DEFAULT_DASH_THRESHOLD_PER_1K,
    DEFAULT_SHORT_PARA_RATE_PER_1K,
    count_short_paras,
    scan_ai_patterns,
)
from packages.core.quality.wordcount import visible_chars

# --- 实测基线（2026-09-27 重算；改阈值前先重算这几个数） ---------------------

HUMAN_DASH_PER_1K = 0.19  # 榜一侯府弧 19 章 / 37806 可见字
HUMAN_SHORT_PARA_PER_1K = 0.212  # 榜一侯府弧全弧 8 命中 / 37806 可见字
GENERATED_DASH_PER_1K = 1.73  # 新书 01 全弧 63625 字
GENERATED_SHORT_PARA_PER_1K = 2.064  # prj_bcb9d1930bd4 48 章 / 125024 可见字 258 命中

# 人类短段基线的**全弧分布**（2026-09-27 复算，19 章）：
#   8 命中 / 0.212 每千字 / 6 章有命中 / 1 章超阈值 1.0。
# **那唯一 1 章超阈（ch19，rate 2.29）完全由该章 3 条非散文行造成**——2 条分隔线
# （`——————` / `—————————`）+ 1 条平台标记（`【继续】`），该章散文只有 1 条
# （`冬月二十八日，万事皆宜`）= 0.76/千字。8 条命中逐条分类：**散文 5 / 分隔线 2 /
# 平台标记 1**。故**散文口径下人类零容忍成立**：5 命中 / 0.132 每千字 / **0 章超阈**。
# ⚠️ 纯排版件/平台标记的排除规则**尚未实现**（那是一条新的形态豁免，需要自己的 20 条
# 抽样论证，超出本轮范围）——**登记为待办**。在此之前全口径的 0.212 / 1 章就是实测值。
HUMAN_SHORT_PARA_HITS = 8
HUMAN_SHORT_PARA_CHAPTERS_WITH_HITS = 6
HUMAN_SHORT_PARA_CHAPTERS_OVER_THRESHOLD = 1
# 散文口径（人工剔除非散文行后的实测登记值；判别见下方 test 的注释）
HUMAN_SHORT_PARA_PROSE_HITS = 5
HUMAN_SHORT_PARA_PROSE_PER_1K = 0.132
HUMAN_SHORT_PARA_PROSE_CHAPTERS_OVER_THRESHOLD = 0

# 破折号阈值的宽容上限：0.19 × 5 ≈ 0.95 向上取整到 1.0。
# 高于它 = 阈值脱离人类基线量级；同时必须低于生成侧实测均值，否则永不触发。
_MAX_TOLERANCE_PER_1K = 1.0

_REPO_ROOT = Path(__file__).resolve().parents[3]
_REFERENCE_SUBPATH = Path("reference-books/榜一-快穿之人渣洗白手册/侯府凤凰男")


def _reference_dir() -> Path | None:
    """定位榜一侯府弧正文目录；内容仓不在本机时返回 None。"""
    candidates: list[Path] = []
    env_dir = os.environ.get("NOVELOS_CONTENT_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(DEFAULT_CONTENT_DIR)
    candidates.append(_REPO_ROOT.parent / "NovelOS-Content")
    for root in candidates:
        target = root / _REFERENCE_SUBPATH
        if target.is_dir():
            return target
    return None


def _first_chapters(n: int = 3) -> list[tuple[str, str]]:
    """按章号取前 n 章 ``(章号标签, 正文)``。"""
    numbered = _numbered_reference_files()
    assert len(numbered) >= n, f"榜一侯府弧应有 ≥{n} 章正文，实际 {len(numbered)}"
    return [
        (f"第{no}章", path.read_text(encoding="utf-8")) for no, path in numbered[:n]
    ]


def _all_chapters() -> list[tuple[str, str]]:
    """榜一侯府弧全部章 ``(章号标签, 正文)``。"""
    numbered = _numbered_reference_files()
    assert len(numbered) >= 19, f"榜一侯府弧应有 19 章正文，实际 {len(numbered)}"
    return [
        (f"第{no}章", path.read_text(encoding="utf-8", errors="replace"))
        for no, path in numbered
    ]


def _numbered_reference_files() -> list[tuple[int, Path]]:
    ref_dir = _reference_dir()
    if ref_dir is None:  # pragma: no cover - 环境前提缺失
        pytest.skip(f"内容仓参考书目录不存在：{_REFERENCE_SUBPATH}")
    numbered: list[tuple[int, Path]] = []
    for path in ref_dir.glob("*.txt"):
        m = re.search(r"第(\d+)章", path.name)
        if m:
            numbered.append((int(m.group(1)), path))
    numbered.sort(key=lambda item: item[0])
    return numbered


def _measured_rates(prose: str) -> tuple[float, float]:
    """取两条密度规则的实测值（每千字）。

    走公开入口的「阈值 0 探针」，不复制算子正则（复制即两套口径）：
    阈值 0 时凡有命中必触发，返回的 ``rate`` 就是算子自己算出的密度。
    """
    probes = scan_ai_patterns(
        prose, dash_threshold_per_1k=0.0, short_para_threshold_per_1k=0.0
    )
    dash = next((h["rate"] for h in probes if h["rule_id"] == "AI-PUNCT-ABUSE"), 0.0)
    short_para = next((h["rate"] for h in probes if h["rule_id"] == "AI-SHORT-PARA"), 0.0)
    return dash, short_para


# ---------------------------------------------------------------------------
# 纪律 1：人类基线不得被判违规
# ---------------------------------------------------------------------------


def test_human_baseline_chapters_do_not_trigger_density_rules():
    """榜一侯府弧前 3 章跑默认阈值：破折号 / 短句独立成段两条都必须零命中。

    这是「人类写的正文不该被判 AI 腔」的最小看守——阈值若把人类基线也判违规，
    它衡量的就不是 AI 味（判别：定阈值前先看人侧是否干净）。逐章判定，
    与生产口径一致（review 按章跑算子）。
    """
    chapters = _first_chapters(3)
    worst: dict[str, float] = {"dash": 0.0, "short_para": 0.0}

    for label, prose in chapters:
        wc = visible_chars(prose)
        assert wc >= 200, "密度类规则有 200 字最小判定长度，样本必须够长"
        dash_rate, short_para_rate = _measured_rates(prose)
        worst["dash"] = max(worst["dash"], dash_rate)
        worst["short_para"] = max(worst["short_para"], short_para_rate)

        rule_ids = {h["rule_id"] for h in scan_ai_patterns(prose)}
        assert "AI-PUNCT-ABUSE" not in rule_ids, (
            f"{label} 人类正文被破折号规则误判：{dash_rate:.2f}/千字"
            f"（阈值 {DEFAULT_DASH_THRESHOLD_PER_1K}），可见字 {wc}"
        )
        assert "AI-SHORT-PARA" not in rule_ids, (
            f"{label} 人类正文被短段规则误判：{short_para_rate:.2f}/千字"
            f"（阈值 {DEFAULT_SHORT_PARA_RATE_PER_1K}），可见字 {wc}"
        )
        assert dash_rate <= DEFAULT_DASH_THRESHOLD_PER_1K, (
            f"{label} 破折号 {dash_rate:.2f}/千字 已越过阈值"
            f" {DEFAULT_DASH_THRESHOLD_PER_1K}——阈值已脱离人类基线"
        )
        assert short_para_rate <= DEFAULT_SHORT_PARA_RATE_PER_1K, (
            f"{label} 短段 {short_para_rate:.2f}/千字 已越过阈值"
            f" {DEFAULT_SHORT_PARA_RATE_PER_1K}——阈值已脱离人类基线"
        )

    assert worst["dash"] <= HUMAN_DASH_PER_1K * 5, (
        f"榜一破折号实测 {worst['dash']:.2f}/千字 与登记基线 {HUMAN_DASH_PER_1K} 量级不符——"
        "基线数字需重算（改阈值前先重算上表三个数）"
    )
    assert worst["short_para"] <= HUMAN_SHORT_PARA_PER_1K, (
        f"榜一前 3 章短段实测 {worst['short_para']:.2f}/千字 高于登记基线"
        f" {HUMAN_SHORT_PARA_PER_1K}——基线数字需重算"
    )


# ---------------------------------------------------------------------------
# 纪律 3：登记的基线数字必须能被复算出来（防「恒真的断言」）
# ---------------------------------------------------------------------------


def test_human_short_para_baseline_is_reproducible_not_a_measuring_artifact():
    """全弧 19 章逐章复算：命中 8 / 0.212 每千字 / 6 章有命中 / 1 章超阈值。

    这条是 2026-09-27 那次「人类基线 0.00 是 ``\\n\\n`` 切分造成的量具假象」的
    **回归红线**：旧登记值 0.00 在旧口径下**永远成立**（任何断言都抓不到口径被
    改回去），本条把它换成可复算的实测分布——**把段切分改回 ``\\n\\n`` 必红**
    （人类侧命中数会掉到 0）。

    另附**散文口径**对照：8 条命中逐条分类为散文 5 / 分隔线 2 / 平台标记 1，
    剔除非散文行后是 5 命中 / 0.132 每千字 / **0 章超阈**。即登记的 1/19 章超阈
    全部由非散文行造成，**人类零容忍在散文口径上成立**。
    ⚠️ 非散文行的剔除**在本测试里是按字面人工分类**的（分隔线 `—…`、平台标记
    `【…】`），因为算子侧**尚未实现**对应的排除规则（那需要自己的 20 条抽样论证，
    已登记为待办）。本断言的作用是把「散文口径 0 章超阈」这个结论钉住，不是假装
    算子已经会剔除它们。
    """
    chapters = _all_chapters()
    hits = 0
    chapters_with_hits = 0
    over_threshold = 0
    prose_hits = 0
    prose_over = 0
    per_chapter: list[str] = []
    for label, prose in chapters:
        wc = visible_chars(prose)
        assert wc >= 200, "密度类规则有 200 字最小判定长度，样本必须够长"
        chapter_hits = count_short_paras(prose)
        hits += len(chapter_hits)
        chapters_with_hits += bool(chapter_hits)
        if len(chapter_hits) / (wc / 1000.0) > DEFAULT_SHORT_PARA_RATE_PER_1K:
            over_threshold += 1
        # 非散文行 = 纯排版件（分隔线）或平台章节标记；人工分类，字面可判
        chapter_prose = [h for h in chapter_hits if not _is_non_prose_layout(h)]
        prose_hits += len(chapter_prose)
        if len(chapter_prose) / (wc / 1000.0) > DEFAULT_SHORT_PARA_RATE_PER_1K:
            prose_over += 1
        if chapter_hits:
            per_chapter.append(f"{label}:{len(chapter_hits)}")

    visible = sum(visible_chars(prose) for _, prose in chapters)
    rate = hits / (visible / 1000.0)
    assert visible == 37806, "语料已换代：可见字数与登记基线不符，基线数字需重算"
    assert hits == HUMAN_SHORT_PARA_HITS, (
        f"榜一全弧短段命中实测 {hits}（{per_chapter}），登记 {HUMAN_SHORT_PARA_HITS}。"
        "若为 0 请检查段切分口径——人类语料是一行一段，用 \\n\\n 会构造上不可能命中"
    )
    assert chapters_with_hits == HUMAN_SHORT_PARA_CHAPTERS_WITH_HITS
    assert over_threshold == HUMAN_SHORT_PARA_CHAPTERS_OVER_THRESHOLD, (
        f"超阈值章数实测 {over_threshold}，登记 {HUMAN_SHORT_PARA_CHAPTERS_OVER_THRESHOLD}。"
        "该章（ch19）的 3 条命中全是非散文行，散文口径下应为 0 章超阈"
    )
    assert rate == pytest.approx(HUMAN_SHORT_PARA_PER_1K, abs=0.001)
    assert hits > 0, "人类侧必须真有命中——全 0 意味着量具坏了，不是语料干净"
    assert prose_hits == HUMAN_SHORT_PARA_PROSE_HITS
    assert prose_hits / (visible / 1000.0) == pytest.approx(HUMAN_SHORT_PARA_PROSE_PER_1K, abs=0.001)
    assert prose_over == HUMAN_SHORT_PARA_PROSE_CHAPTERS_OVER_THRESHOLD, (
        "散文口径下人类零容忍：不许有任何一章越过阈值（若此处转红，说明某个"
        "「真信号」形态的散文被算进来了）"
    )


def _is_non_prose_layout(hit: str) -> bool:
    """短段命中是否属于**非散文排版件**（纯分隔线 / 平台章节标记）。

    两类（2026-09-27 对抗审查给的逐条分类，本函数把它字面化）：
      ① 纯排版件：去掉破折号/括号类排版字符后**不再有汉字或数字**——
         `——————` / `—————————`；
      ② 平台章节标记：整体被 ``【…】`` 包裹且内容极短——``【继续】``。

    这是**测试侧的人工分类口径**，算子侧尚无对应排除规则（那需要自己的 20 条抽样
    论证，已登记为待办），故只用于本文件的口径对照——**不假装算子已经会剔除它们**。
    """
    text = hit.strip()
    if re.fullmatch(r"【[^】]{1,4}】", text):
        return True
    body = re.sub(r"[—–\-·【】\[\]（）()\s]", "", text)
    return not re.search(r"[\u4e00-\u9fff0-9A-Za-z0-9]", body)


def test_short_para_threshold_separates_human_from_generated():
    """阈值 1.0 仍落在人机两条分布**之间**（9.7× 分离度），本次未动阈值。

    2026-09-27 修的是**论证链**（人类基线由假象变回实测），不是阈值本身：
    0.212 vs 2.064 的分离度与修正前的 0.00 vs 2.62 同量级，故 1.0 维持。
    """
    separation = GENERATED_SHORT_PARA_PER_1K / HUMAN_SHORT_PARA_PER_1K
    assert separation > 5.0, (
        f"人机分离度实测 {separation:.1f}×（0.212 vs 2.064）已不足 5×——"
        "阈值 1.0 的「留宽容」前提不再成立，须重新校准"
    )
    assert HUMAN_SHORT_PARA_PER_1K < DEFAULT_SHORT_PARA_RATE_PER_1K
    assert DEFAULT_SHORT_PARA_RATE_PER_1K < GENERATED_SHORT_PARA_PER_1K


# ---------------------------------------------------------------------------
# 纪律 2：阈值必须落在实测分布之内（防「改回旧值」）
# ---------------------------------------------------------------------------


def test_dash_threshold_within_measured_distribution():
    """破折号阈值必须贴人类基线量级，且低于生成侧实测均值。

    旧值 3.5 两条都不满足（0.19 的 18 倍；> 生成侧 1.73）——即死规则。
    """
    assert DEFAULT_DASH_THRESHOLD_PER_1K <= _MAX_TOLERANCE_PER_1K, (
        f"破折号阈值 {DEFAULT_DASH_THRESHOLD_PER_1K} 超出人类基线"
        f" {HUMAN_DASH_PER_1K}/千字 的宽容上限 {_MAX_TOLERANCE_PER_1K}"
    )
    assert DEFAULT_DASH_THRESHOLD_PER_1K < GENERATED_DASH_PER_1K, (
        f"破折号阈值 {DEFAULT_DASH_THRESHOLD_PER_1K} 高于生成侧实测均值"
        f" {GENERATED_DASH_PER_1K}/千字——等于永不触发（死规则）"
    )


def test_short_para_threshold_within_measured_distribution():
    """短段阈值同理：榜一 0.212、生成侧 2.064——阈值必须落在两者之间。"""
    assert DEFAULT_SHORT_PARA_RATE_PER_1K <= _MAX_TOLERANCE_PER_1K, (
        f"短段阈值 {DEFAULT_SHORT_PARA_RATE_PER_1K} 超出人类基线"
        f" {HUMAN_SHORT_PARA_PER_1K}/千字 的宽容上限 {_MAX_TOLERANCE_PER_1K}"
    )
    assert DEFAULT_SHORT_PARA_RATE_PER_1K < GENERATED_SHORT_PARA_PER_1K, (
        f"短段阈值 {DEFAULT_SHORT_PARA_RATE_PER_1K} 高于生成侧实测均值"
        f" {GENERATED_SHORT_PARA_PER_1K}/千字——等于永不触发（死规则）"
    )
