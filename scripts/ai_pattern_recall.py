# -*- coding: utf-8 -*-
"""检测召回率的**测量脚手架**（只读）——逐规则算「命中数 / 每千字命中率 / 该规则的阈值」。

⚠️ **本脚本测的不是真实召回率**
===============================
真实 AI 文本只能来自**真实模型调用**（AGENTS §一 硬约束 2 禁止用 NovelOS 自身发起
真实调用来验证软件行为），而 dev 库的既有书稿**不得**作为验证语料（AGENTS §一
硬约束 1）。因此：

    **这是合成代理下的数字，不是真实 AI 文本的召回率；真实召回率需要标注过的
    AI 侧语料，本批因硬约束 2/1 无法取得。**

脚手架本身是**语料无关**的：只要调用方给它一个目录，它就按同一口径重算——将来
拿到标注过的 AI 侧语料，把目录换掉即可，数字口径不变。

语料来源完全由调用者提供
------------------------
* 本脚本**不下载**任何东西、**不联网**、**不内置任何受版权语料**；
* 输入是「一个目录」，目录下的 ``*.txt`` 逐个当作一篇文本；
* 语料的代表性由调用者负责——本脚本只负责**同口径地重算**。

只读
----
不写文件、不碰数据库、不改任何检测器。算子实现**全部从 ``packages.core.quality``
导入**（不复制正则、不复制阈值）——复制即分叉，分叉即「量具失真」那一族事故。

算子定义（文件头即口径表）
--------------------------
每条规则的「命中数」用的是**算子级原始命中**（不是章级判定结果），与
``scripts/ai_tone_calibrate.py`` 的 ``OPERATORS`` 表同款口径；**阈值**一律从
**活的代码常量**里取，因此本脚本的阈值列**不可能与代码漂移**。

============================  ==========================  ==================================
rule_id                       命中数的口径                阈值（从代码常量实时取）
============================  ==========================  ==================================
AI-FORBIDDEN-WORD             硬套话 + 弱词的**词频**      弱词同章堆积门（章节级）
AI-TRIPLET-OPENING            同两字词开头的**段数**      连续 3 句（内联字面量）
AI-PRONOUN-PILE               「他/她」起首的**段数**      连续 3 句（内联字面量）
AI-ENDING-SUMMARY             **末段**里的套话短语数       无阈值（二元判定）
AI-PUNCT-ABUSE                破折号 / 省略号**出现次数**  每千字 1.0 处
AI-EXPLAIN-TONE               解释连接词**出现次数**       无阈值（二元判定）
AI-CONTRAST-PAIR              「不是…而是/是…」**命中数** 每千字 1.5 处
AI-SHORT-PARA                 短句独立成段的**段数**       每千字 1.0 处
AI-ANTHRO-VEHICLE             职业类喻体**命中数**         同章 ≥3 处
AI-LONG-PARA                  >100 可见字的**段数**        >100 字（>140 ⇒ error）
AI-DIALOGUE-LOW               **低于阈值的文件数**         对话占比 <12%
AI-DIALOGUE-ECHO              相邻对白回声**对数**         复述 0.70 / 接词 0.66 / 开口 2
AI-BEAT-REPEAT                远距小句复现**处数**         共享串 ≥6 字 / 覆盖 ≥0.6
CONT-CLOCK-DAYBREAK           时钟↔天亮矛盾**处数**        两标记间距 ≤3000 字
CONT-TIME-BACKSTEP            时点回退**处数**             回退 ≥2 小时
============================  ==========================  ==================================

⚠️ **口径提醒**：密度类算子在**规则层**有 200 / 600 可见字的最小判定长度
（``_MIN_PROSE_CHARS_FOR_DENSITY`` / ``_MIN_PROSE_CHARS_FOR_READABILITY``）。本脚本
报的是**算子级原始命中**，那两道长度门**没有施加**——所以短文本的每千字率会虚高。
跑真语料时，语料是整章（远长于 600 字）时这个差别不存在。

用法
----
::

    python scripts/ai_pattern_recall.py --corpus DIR [--corpus DIR2 ...]
    python scripts/ai_pattern_recall.py --corpus DIR --label "合成正例"
    python scripts/ai_pattern_recall.py --corpus DIR --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.core.quality.ai_patterns import (  # noqa: E402
    _AI_ENDING_SUMMARY_PHRASES,
    AI_PATTERN_HARD_CLICHES,
    AI_PATTERN_WEAK_WORDS,
    DEFAULT_ANTHRO_VEHICLE_MIN_COUNT,
    DEFAULT_CONTRAST_PAIR_RATE_PER_1K,
    DEFAULT_DASH_THRESHOLD_PER_1K,
    DEFAULT_DIALOGUE_ECHO_MIN_OPENING,
    DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP,
    DEFAULT_DIALOGUE_ECHO_MIN_RATIO,
    DEFAULT_DIALOGUE_LOW_WARN_RATIO,
    DEFAULT_LONG_PARA_ERROR_CHARS,
    DEFAULT_LONG_PARA_MIN_COUNT,
    DEFAULT_LONG_PARA_WARN_CHARS,
    DEFAULT_SHORT_PARA_RATE_PER_1K,
    _last_paragraph,
    count_anthro_vehicles,
    count_contrast_pairs,
    count_long_paragraphs,
    count_short_paras,
    dialogue_ratio,
)
from packages.core.quality.ai_patterns_dialogue import (  # noqa: E402
    _DIALOGUE_ECHO_ASYMMETRY_LIMIT,
    _MIN_DIALOGUE_ECHO_COMPARABLE_CHARS,
    count_dialogue_echoes,
)
from packages.core.quality.ai_patterns_sentence import (  # noqa: E402
    _DASH_OR_ELLIPSIS_RE,
    _scan_explain_tone,
    _scan_pronoun_pile,
    _scan_triplet_opening,
)
from packages.core.quality.beat_repeat import (  # noqa: E402
    DEFAULT_BEAT_MIN_CHARS,
    DEFAULT_BEAT_MIN_COUNT,
    DEFAULT_BEAT_MIN_COVER,
    DEFAULT_BEAT_MIN_GAP_CHARS,
    DEFAULT_BEAT_RATE_PER_1K,
    count_beat_repeats,
)
from packages.core.quality.continuity_time import (  # noqa: E402
    DEFAULT_MAX_GAP_CHARS,
    DEFAULT_MIN_BACKSTEP_HOURS,
    time_conflicts,
)
from packages.core.quality.wordcount import visible_chars  # noqa: E402

PROXY_DISCLAIMER = (
    "这是**合成代理**下的数字，**不是**真实 AI 文本的召回率；"
    "真实召回率需要标注过的 AI 侧语料，**本批因硬约束 2/1 无法取得**。"
)

USAGE = """\
用法: python scripts/ai_pattern_recall.py --corpus DIR [--corpus DIR2 ...] [选项]

  --corpus DIR    待测语料目录（可多次传入；目录下的 *.txt 逐个当作一篇文本）
  --label NAME    语料标签（可多次传入，与 --corpus 按位置对应；缺省用目录名）
  --json          输出 JSON（默认输出人读文本）
  -h/--help       显示本帮助

只读：不写文件、不联网、不碰数据库、不内置任何语料。
"""


# ---------------------------------------------------------------------------
# 算子表（口径定义在文件头，见模块 docstring）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Operator:
    """一条规则的测量口径。"""

    rule_id: str
    label: str
    unit: str  # 「次」/「段」/「对」/「处」/「文件」
    count: Callable[[str], float]
    threshold: Callable[[], str]
    note: str = ""


def _count_forbidden_words(prose: str) -> float:
    """硬套话 + 弱词的**词频**（章节级堆积门未施加——见模块 docstring 口径提醒）。"""
    total = 0
    for word in (*AI_PATTERN_HARD_CLICHES, *AI_PATTERN_WEAK_WORDS):
        total += prose.count(word)
    return float(total)


def _count_ending_summary(prose: str) -> float:
    """**末段**里的套话短语数（与规则同口径：只看末段）。"""
    last = _last_paragraph(prose)
    if not last:
        return 0.0
    return float(sum(1 for phrase in _AI_ENDING_SUMMARY_PHRASES if phrase in last))


def _count_explain_tone(prose: str) -> float:
    return float(sum(hit["count"] for hit in _scan_explain_tone(prose)))


def _count_dialogue_low(prose: str) -> float:
    """低于阈值的**文件数**口径在语料层算——这里返回的是「本篇是否低于阈值」（0/1）。"""
    return 1.0 if dialogue_ratio(prose) < DEFAULT_DIALOGUE_LOW_WARN_RATIO else 0.0


OPERATORS: tuple[Operator, ...] = (
    Operator(
        "AI-FORBIDDEN-WORD",
        "AI 腔套话/禁用词",
        "次",
        _count_forbidden_words,
        lambda: (
            "弱词同章堆积：单词 ≥3 次 或 本层合计 ≥6 次（章节级门；此处报词频）"
        ),
        "硬套话层命中即报；弱词层需堆积。词频口径未施加章节门。",
    ),
    Operator(
        "AI-TRIPLET-OPENING",
        "连续同两字词开头",
        "段",
        lambda p: float(len(_scan_triplet_opening(p))),
        lambda: "连续 3 句以相同两字词开头（内联字面量，非命名常量）",
    ),
    Operator(
        "AI-PRONOUN-PILE",
        "「他/她」起首排比",
        "段",
        lambda p: float(len(_scan_pronoun_pile(p))),
        lambda: "连续 3 句以「他/她」开头（内联字面量，非命名常量）",
    ),
    Operator(
        "AI-ENDING-SUMMARY",
        "章尾总结/升华体",
        "次",
        _count_ending_summary,
        lambda: "无阈值（二元判定）：末段含 12 条短语任一条即报（真实语料上两侧皆 0）",
    ),
    Operator(
        "AI-PUNCT-ABUSE",
        "破折号/省略号",
        "次",
        lambda p: float(len(_DASH_OR_ELLIPSIS_RE.findall(p))),
        lambda: f"每千字 > {DEFAULT_DASH_THRESHOLD_PER_1K} 处（≥2× ⇒ error）",
    ),
    Operator(
        "AI-EXPLAIN-TONE",
        "解释腔连接词",
        "次",
        _count_explain_tone,
        lambda: "无阈值（二元判定）；命中总数 ≥5 ⇒ severity 升 error",
    ),
    Operator(
        "AI-CONTRAST-PAIR",
        "对举结构「不是…而是…」",
        "次",
        lambda p: float(len(count_contrast_pairs(p))),
        lambda: f"每千字 > {DEFAULT_CONTRAST_PAIR_RATE_PER_1K} 处",
    ),
    Operator(
        "AI-SHORT-PARA",
        "短句独立成段",
        "段",
        lambda p: float(len(count_short_paras(p))),
        lambda: f"每千字 > {DEFAULT_SHORT_PARA_RATE_PER_1K} 处",
    ),
    Operator(
        "AI-ANTHRO-VEHICLE",
        "拟人化喻体（职业类）",
        "次",
        lambda p: float(len(count_anthro_vehicles(p))),
        lambda: f"同章 ≥ {DEFAULT_ANTHRO_VEHICLE_MIN_COUNT} 处",
    ),
    Operator(
        "AI-LONG-PARA",
        "长段（可读性）",
        "段",
        lambda p: float(len(count_long_paragraphs(p))),
        lambda: (
            f">{DEFAULT_LONG_PARA_WARN_CHARS} 可见字（>{DEFAULT_LONG_PARA_ERROR_CHARS} ⇒ error）"
            f" · 单章 ≥{DEFAULT_LONG_PARA_MIN_COUNT} 段"
        ),
    ),
    Operator(
        "AI-DIALOGUE-LOW",
        "对话占比过低",
        "文件",
        _count_dialogue_low,
        lambda: f"对话占比 <{DEFAULT_DIALOGUE_LOW_WARN_RATIO:.0%}（唯一档位，绝不进 errors 桶）",
        "命中数口径 = 低于阈值的文件数（每篇记 1）。",
    ),
    Operator(
        "AI-DIALOGUE-ECHO",
        "相邻对白接词回声",
        "对",
        lambda p: float(len(count_dialogue_echoes(p))),
        lambda: (
            f"复述型 ≥{DEFAULT_DIALOGUE_ECHO_MIN_RATIO} 或 接词型 ≥{DEFAULT_DIALOGUE_ECHO_MIN_OVERLAP}"
            f" 且开口 ≥{DEFAULT_DIALOGUE_ECHO_MIN_OPENING} 字"
            f"（可比性：任一侧 ≥{_MIN_DIALOGUE_ECHO_COMPARABLE_CHARS} 字 且 后句 <"
            f"{_DIALOGUE_ECHO_ASYMMETRY_LIMIT}× 前句）"
        ),
    ),
    Operator(
        "AI-BEAT-REPEAT",
        "同章远距小句复现",
        "处",
        lambda p: float(len(count_beat_repeats(p))),
        lambda: (
            f"共享串 ≥{DEFAULT_BEAT_MIN_CHARS} 字 · 覆盖 ≥{DEFAULT_BEAT_MIN_COVER}"
            f" · 间距 ≥{DEFAULT_BEAT_MIN_GAP_CHARS} 字；章级门 ≥{DEFAULT_BEAT_MIN_COUNT} 处"
            f" 且 >{DEFAULT_BEAT_RATE_PER_1K}/千字"
        ),
    ),
    Operator(
        "CONT-CLOCK-DAYBREAK",
        "深夜时钟与天亮矛盾",
        "处",
        lambda p: float(sum(1 for hit in time_conflicts(p) if hit.rule_id == "CONT-CLOCK-DAYBREAK")),
        lambda: f"两标记间距 ≤{DEFAULT_MAX_GAP_CHARS} 字且中间无时间推进标记",
    ),
    Operator(
        "CONT-TIME-BACKSTEP",
        "同章相邻时点回退",
        "处",
        lambda p: float(sum(1 for hit in time_conflicts(p) if hit.rule_id == "CONT-TIME-BACKSTEP")),
        lambda: f"回退 ≥{DEFAULT_MIN_BACKSTEP_HOURS} 小时（跨午夜顺行等三守卫除外）",
    ),
)


# ---------------------------------------------------------------------------
# 语料读取（只读）
# ---------------------------------------------------------------------------


@dataclass
class Corpus:
    label: str
    path: Path
    files: list[tuple[str, str]] = field(default_factory=list)

    @property
    def visible_chars(self) -> int:
        return sum(visible_chars(text) for _, text in self.files)


def read_corpus(path: Path, label: str | None = None) -> Corpus:
    """读一个目录下的 ``*.txt``（**只读**）。"""
    files: list[tuple[str, str]] = []
    for item in sorted(path.glob("*.txt")):
        files.append((item.name, item.read_text(encoding="utf-8", errors="replace")))
    return Corpus(label=label or path.name, path=path, files=files)


def measure(corpus: Corpus) -> dict:
    """逐规则算命中数 / 每千字 / 阈值。"""
    total = corpus.visible_chars
    per_k = (total / 1000.0) if total else 0.0
    rows: list[dict] = []
    for op in OPERATORS:
        hits = sum(op.count(text) for _, text in corpus.files)
        rows.append(
            {
                "rule_id": op.rule_id,
                "label": op.label,
                "unit": op.unit,
                "hits": hits,
                "per_1k": (hits / per_k) if per_k else 0.0,
                "threshold": op.threshold(),
                "note": op.note,
            }
        )
    return {
        "label": corpus.label,
        "path": str(corpus.path),
        "files": len(corpus.files),
        "visible_chars": total,
        "rules": rows,
        "total_hits": sum(row["hits"] for row in rows),
    }


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def render_text(reports: list[dict]) -> str:
    lines: list[str] = []
    lines.append("=" * 100)
    lines.append("检测召回率测量脚手架（合成代理口径）")
    lines.append("=" * 100)
    lines.append(f"⚠️ {PROXY_DISCLAIMER}")
    lines.append("")
    for report in reports:
        lines.append(
            f"--- 语料「{report['label']}」：{report['files']} 篇 / "
            f"{report['visible_chars']} 可见字  [{report['path']}]"
        )
        lines.append(
            f"{'rule_id':<24}{'命中数':>8}{'每千字':>9}  {'单位':<4}阈值"
        )
        for row in report["rules"]:
            lines.append(
                f"{row['rule_id']:<24}{row['hits']:>8.0f}{row['per_1k']:>9.2f}  "
                f"{row['unit']:<4}{row['threshold']}"
            )
        lines.append(f"{'合计':<24}{report['total_hits']:>8.0f}")
        lines.append("")
    if len(reports) >= 2:
        lines.append("区分度（两两合计命中数）：")
        for i, a in enumerate(reports):
            for b in reports[i + 1 :]:
                lines.append(
                    f"  「{a['label']}」{a['total_hits']:.0f}  vs  "
                    f"「{b['label']}」{b['total_hits']:.0f}"
                )
    lines.append("")
    lines.append(f"⚠️ {PROXY_DISCLAIMER}")
    return "\n".join(lines)


def render_json(reports: list[dict]) -> str:
    return json.dumps(
        {"disclaimer": PROXY_DISCLAIMER, "corpora": reports},
        ensure_ascii=False,
        indent=2,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--corpus", action="append", default=[])
    parser.add_argument("--label", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    args = parser.parse_args(argv)

    if args.help:
        print(USAGE)
        return 0
    if not args.corpus:
        print(USAGE)
        return 2

    corpora: list[Corpus] = []
    for idx, raw in enumerate(args.corpus):
        path = Path(raw)
        if not path.is_dir():
            print(f"语料目录不存在：{path}", file=sys.stderr)
            return 2
        label = args.label[idx] if idx < len(args.label) else path.name
        corpus = read_corpus(path, label)
        if not corpus.files:
            print(
                f"语料目录里没有 *.txt：{path}"
                "（本脚本只读目录**顶层**的 .txt；子目录里的文件不算——"
                "请把待测语料目录本身指过来）",
                file=sys.stderr,
            )
            return 2
        corpora.append(corpus)

    reports = [measure(corpus) for corpus in corpora]
    if args.json:
        print(render_json(reports))
    else:
        print(render_text(reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
