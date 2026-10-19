# -*- coding: utf-8 -*-
"""人类基线的**可分发替代物**：自造语料的算子校准器（2026-10-19 新增）。

为什么需要这个脚本
------------------
本仓的检测器阈值（破折号 1.0/千字、短段 1.0/千字、对话占比 12% 等）是**按人类
基线校准**的——人类基线语料是真实出版小说（榜一《快穿之人渣洗白手册》侯府弧
19 章 / 37806 可见字），**不可分发**（内容仓按 2026-09-14 裁决不推 GitHub）。
⇒ **第三方拿不到「阈值是校准过的」这个结论的任何证据**，CI 里那几个基线看守
恒 skip（见 ``tests/unit/quality/HUMAN_BASELINE.md``）。

本脚本给出一条**可复现的替代路径**：

1. **内置合成语料**：本文件里的两段中文**全部是本仓库原创**（不抄任何受版权保护
   的文本）——一段「像人写的」自然叙述，一段「明显模板化」的机器腔。用它们跑
   本仓**已有**的检测器（``packages/core/quality/ai_patterns.py`` 的公开入口），
   输出「人类侧 vs 模板侧」的每千字频率对照（口径照 ``scripts/ai_tone_calibrate.py``：
   **R = 模板侧 ÷ 人类侧**）。
2. **公版语料入口**：``--corpus <path>`` 让你把**公版**中文文本（如《西游记》
   《红楼梦》《三国演义》的公版原文）指进来当人类侧。本脚本**不联网抓取**——
   语料从哪来由你决定（版权与获取方式都是你的事，脚本只管量）。

⚠️ **合成语料不是「人类基线」**：它**不能**替代真实出版小说的分布，只能证明
① 检测器在这份语料上**能区分**自然句与模板句（算子活着），② 口径可复算。
**任何阈值都不得改用合成语料的数字**（合成语料的分布是「我写的」分布，不是
「人写的」分布）。详见 ``tests/unit/quality/HUMAN_BASELINE.md``。

只读：不连库、不写任何仓库文件。输出默认只到 stdout；``--out`` 的路径由调用方
指定（**默认不进仓库**）。

用法::

    python scripts/human_baseline_synthetic.py                       # 内置合成语料
    python scripts/human_baseline_synthetic.py --format json
    python scripts/human_baseline_synthetic.py --out D:/report.json --format json
    python scripts/human_baseline_synthetic.py --corpus D:/texts/xiyouji   # 公版语料
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.quality.ai_patterns import (  # noqa: E402
    AI_PATTERN_FORBIDDEN_WORDS,
    AI_PATTERN_HARD_CLICHES,
    scan_ai_patterns,
)
from packages.core.quality.wordcount import visible_chars  # noqa: E402

# ---------------------------------------------------------------------------
# 语料（**本仓库原创**：下面两段文字均为本文件新写，不抄任何受版权保护的文本）
# ---------------------------------------------------------------------------

#: 「像人写的」一侧：平实叙述，有具体动作与物件，长短句交替，段长不整齐。
#: 刻意避开：禁用词（硬套话与弱词全表）、破折号、「不是A而是B」、解释腔、
#: 三连同词起首、连续人称代词起首的句子。
#:
#: ⚠️ 它是**阳性对照语料**（证明算子在自然文本上是哑的、在模板文本上是响的），
#: **不是**任何分布的模型——不得用它改阈值。
NATURAL_CORPUS = """\
灶上的水开了半刻，锅盖顶着白气往上跳。老张伸手把火压小，顺手在围裙上擦了擦手。
巷口卖糖炒栗子的老头收了摊，铁锅还烫着，他蹲在墙根抽了半支烟。
雨下到傍晚才停，青石板上积着薄薄一层水，倒映着两边的灯笼，红得晃眼。
她把账本翻到最后一页，笔尖在纸上顿了一下，终究没有再添什么，只是把本子合上，压在算盘底下。
巷子深处传来一阵脚步声，伴着沙沙的响动，是有人在扫落叶，扫帚每一下都拖得很长。
门房老头正把一筐白菜往屋里搬，看见他，笑了笑，递过来一个还带着泥的萝卜，说是自家地里种的，不值钱。
天色一点点沉下来，屋檐下的灯笼亮起，风一过就晃，光影在墙面上来回地挪。
“你去年也是这么说的。”他没抬头，手里的活计没停，麻绳在指间绕了一圈又一圈。
炉火映在墙上，影子忽长忽短，屋里并不算太冷，反而有股说不出的暖意，烤久了鞋底都有点焦味。
后院的桃树今年结得多，地上落了一层，踩上去有点滑，孩子专门挑没被踩过的捡，说那样才甜。
镇上的裁缝说是三两天就能取，老张没接话，把布包又往怀里按了按，又量了一遍肩宽，怕是量错了。
“你这是要走了？”老板娘从灶间探出头，手上还沾着面，围裙上蹭了两道白印子也没顾得上擦。
老张点点头，把扁担竖到墙边，绳结解开，一圈一圈绕在胳膊上，绕好了又抽开重新绕。
河边的柳树刚抽出新芽，风一过就抖，抖得人眼睛发酸，河边钓鱼的老头抱怨了好几回，说再这样下去鱼都吓跑了。
村口那棵老槐树下新支了个摊，卖凉粉的吆喝声隔着两里地都能听见，一碗三块，加辣不要钱。
他把碗接过来，先吹了吹，喝了一大口，凉得牙根发酸，末了把碗底的辣子也捞干净了。
天亮得越来越早，鸡叫三遍他就得下地，可地里的活永远干不完，锄完一遍又得补上一遍。
“孙子的事，让孙子自己去办。”他把烟锅在鞋底磕了磕，磕掉里面的灰，又把烟丝拨了拨匀。
屋后的水缸裂了一道缝，水一路渗到墙根，冬天就结一层薄冰，得用旧棉絮堵上才管用。
说话间雨又大了些，檐水成串地往下落，砸在青石板上啪啪作响，积水顺着砖缝往低处淌。
那盏旧马灯提着走，灯芯拨亮些，光圈在地上晃；巷子深处黑得伸手不见五指，只能靠脚下。
路面被车轮压出两道深沟，沟里积着昨夜的水，水面上浮着几片菜叶，鞋踩下去就黏住鞋底。
铁匠铺的灯亮到很晚，风箱声一阵一阵地传过整条街，叮叮当当的锤声混在雨里，听不出个数。
到了桥头，他停住脚，看了看对岸那扇亮着的窗，又把手缩回袖子里，在桥栏上靠了靠，凉的。
夜里的河面很静，只听见水拍着桥墩子的声音，一下，又一下，像有人拿指节敲木头。
“娘，我明儿还去。”灶间有人应了一声，锅铲碰着锅沿，哐当响了两下。
“路上看着点。”他应着，把马灯挂到肩上，踩着水声往回走。
"""

#: 「明显模板化」一侧：机器腔的教科书形态——硬套话、弱词成堆、破折号、
#: 「不是A而是B」、解释腔、三连同词起首、连续人称起首、节拍器式短段、
#: 章尾升华体。这一侧是**阳性对照**：算子在这份语料上必须**响**。
TEMPLATE_CORPUS = """\
值得注意的是，这一天注定不平凡。与此同时，命运的齿轮开始缓缓转动。
仿佛有一只无形的手，轻轻拨动了命运的琴弦，空气中弥漫着紧张的气息。
不是热血，而是责任；不是冲动，而是使命。他站在那里，俯瞰着脚下的城市。
他的心中充满了豪情。他的眼中燃烧着不屈的火焰。他的双手紧握成拳。
因为只有这样才能活下去，所以他别无选择。换句话说，命运早已注定。
骤然，他猛地转身，眼角的余光扫过身后的一切。
仿佛时间静止了一般。空气仿佛凝固，命运仿佛开了个不大不小的玩笑。
总而言之，这不仅是一次胜利，更是一次升华。综上所述，值得注意的是……
风停了。雪停了。夜停了。
新的篇章就此拉开序幕。从此以后，一切都不一样了。
他站在那里。他的背影。他的决绝。
不是结束，而是开始；不是失败，而是新的战斗。
他清楚地知道，这一刻的抉择，将决定所有人的命运。
空气中弥漫着硝烟的味道，冥冥之中似乎有什么在注视着这一切。
不禁让人想问，这一切究竟是为了什么？
于是他抬起头，看向远方，眼中是前所未有的坚定。
总而言之，故事仍在继续，而新的旅程才刚刚开始。
"""


# ---------------------------------------------------------------------------
# 算子表（全部走本仓**已有**的公开入口；不复制任何算子正则）
# ---------------------------------------------------------------------------

#: ``scan_ai_patterns`` 的「阈值 0 探针」：密度类规则在阈值 0 时**凡有命中必
#: 触发**，返回的 ``count`` 就是算子自己算出的命中数。抄算子正则等于养两套口径
#: （本仓已吃过这个亏），故一律走公开入口。
_PROBE_KWARGS = {
    "dash_threshold_per_1k": 0.0,
    "short_para_threshold_per_1k": 0.0,
    "contrast_pair_threshold_per_1k": 0.0,
}

OperatorFn = Callable[[str, dict[str, int]], int]

#: 算子表：名字 → 取值函数（入参 = 文本 + 该文本的规则命中计数表）。
#: 名字**按算子实际覆盖范围**写（禁用词分「字面频次」与「规则命中」两档：
#: 弱词层要同章堆积才报，两者语义不同，混在一起就是「名实不符」）。
OPERATORS: dict[str, OperatorFn] = {
    "禁用词字面频次": lambda t, c: sum(t.count(w) for w in AI_PATTERN_FORBIDDEN_WORDS),
    "硬套话字面频次": lambda t, c: sum(t.count(w) for w in AI_PATTERN_HARD_CLICHES),
    "AI-FORBIDDEN-WORD": lambda t, c: c.get("AI-FORBIDDEN-WORD", 0),
    "AI-EXPLAIN-TONE": lambda t, c: c.get("AI-EXPLAIN-TONE", 0),
    "AI-TRIPLET-OPENING": lambda t, c: c.get("AI-TRIPLET-OPENING", 0),
    "AI-PRONOUN-PILE": lambda t, c: c.get("AI-PRONOUN-PILE", 0),
    "AI-ENDING-SUMMARY": lambda t, c: c.get("AI-ENDING-SUMMARY", 0),
    "AI-LONG-PARA": lambda t, c: c.get("AI-LONG-PARA", 0),
    "AI-SHORT-PARA": lambda t, c: c.get("AI-SHORT-PARA", 0),
    "AI-PUNCT-ABUSE": lambda t, c: c.get("AI-PUNCT-ABUSE", 0),
    "AI-CONTRAST-PAIR": lambda t, c: c.get("AI-CONTRAST-PAIR", 0),
    "AI-DIALOGUE-ECHO": lambda t, c: c.get("AI-DIALOGUE-ECHO", 0),
    "AI-ANTHRO-VEHICLE": lambda t, c: c.get("AI-ANTHRO-VEHICLE", 0),
}

#: 比率型指标（不是每千字频次，单列）。
RATIO_METRICS: dict[str, Callable[[str], float]] = {
    "对话占比": lambda t: _dialogue_ratio(t),
}


def _dialogue_ratio(text: str) -> float:
    """对话可见字占比（走公开入口 ``dialogue_ratio``）。"""
    from packages.core.quality.ai_patterns import dialogue_ratio

    return dialogue_ratio(text)


def _rule_counts(text: str) -> dict[str, int]:
    """跑一次公开扫描入口，取各规则的命中计数（阈值 0 探针）。

    阈值 0 只影响三条密度类规则（让它们「凡有命中必报」），其余规则用各自默认
    阈值——**不改变**它们的判定。
    """
    counts: dict[str, int] = {}
    for hit in scan_ai_patterns(text, **_PROBE_KWARGS):
        rid = str(hit.get("rule_id", ""))
        counts[rid] = counts.get(rid, 0) + int(hit.get("count", 1) or 0)
    return counts


def _visible_chars(text: str) -> int:
    return visible_chars(text)


def _paragraph_count(text: str) -> int:
    return len([p for p in re.split(r"\n+", text) if p.strip()])


# ---------------------------------------------------------------------------
# 语料装载
# ---------------------------------------------------------------------------


def load_corpus(path: str | Path) -> str:
    """读一份语料（文件或目录下的 ``*.txt``），返回拼好的文本。

    只读：脚本**不联网**、不写任何文件。目录语料按文件名排序拼接。
    """
    p = Path(path)
    if p.is_dir():
        parts = []
        for f in sorted(p.glob("*.txt")):
            parts.append(f.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(parts)
    if p.is_file():
        return p.read_text(encoding="utf-8", errors="replace")
    raise SystemExit(f"语料路径不存在：{p}")


# ---------------------------------------------------------------------------
# 测量
# ---------------------------------------------------------------------------


def measure(text: str) -> dict[str, Any]:
    """量一份语料：可见字 / 段数 / 各算子命中数 / 每千字频率。"""
    chars = _visible_chars(text)
    counts = _rule_counts(text)
    per_1k = (chars / 1000.0) if chars else 0.0
    metrics: dict[str, dict[str, float]] = {}
    for name, fn in OPERATORS.items():
        n = int(fn(text, counts))
        metrics[name] = {
            "count": n,
            "per_1k": (n / per_1k) if per_1k else 0.0,
        }
    ratios = {name: float(fn(text)) for name, fn in RATIO_METRICS.items()}
    return {
        "visible_chars": chars,
        "paragraphs": _paragraph_count(text),
        "metrics": metrics,
        "ratios": ratios,
    }


def build_report(human_text: str, template_text: str, *, human_source: str) -> dict[str, Any]:
    """做两侧对照，产出结构化报告。

    **R 的口径照** ``scripts/ai_tone_calibrate.py``：**R = 模板侧 ÷ 人类侧**
    （那里的「生成侧 ÷ 人类侧」）。R < 1 表示模板侧反而更低——那通常意味着算子
    在这份语料上**测不到**该形态（量具失真），不是「模板化文本更干净」。
    """
    human = measure(human_text)
    template = measure(template_text)
    rows: list[dict[str, Any]] = []
    for name in OPERATORS:
        h = human["metrics"][name]
        t = template["metrics"][name]
        ratio = (t["per_1k"] / h["per_1k"]) if h["per_1k"] else None
        if h["per_1k"] == 0 and t["per_1k"] == 0:
            verdict = "两侧皆零（该形态在此语料上不存在——不可作阈值依据）"
        elif h["per_1k"] == 0:
            verdict = "人类侧零命中（不可作阈值依据）"
        elif ratio is not None and ratio >= 2.0:
            verdict = "模板侧偏高"
        elif ratio is not None and ratio >= 1.25:
            verdict = "模板侧略偏高"
        elif ratio is not None and 0.8 <= ratio < 1.25:
            verdict = "无区分"
        else:
            verdict = "人类侧偏高（模板侧没这个形态——**不**说明算子测错了）"
        rows.append(
            {
                "operator": name,
                "human_count": h["count"],
                "human_per_1k": round(h["per_1k"], 3),
                "template_count": t["count"],
                "template_per_1k": round(t["per_1k"], 3),
                "ratio": round(ratio, 2) if ratio is not None else None,
                "verdict": verdict,
            }
        )
    ratio_rows = [
        {
            "metric": name,
            "human": round(human["ratios"][name], 4),
            "template": round(template["ratios"][name], 4),
        }
        for name in RATIO_METRICS
    ]
    return {
        "schema": "human-baseline-synthetic/v1",
        "human_side": {
            "source": human_source,
            "visible_chars": human["visible_chars"],
            "paragraphs": human["paragraphs"],
        },
        "template_side": {
            "source": "built-in synthetic (original text, written for this repo)",
            "visible_chars": template["visible_chars"],
            "paragraphs": template["paragraphs"],
        },
        "metrics": rows,
        "ratios": ratio_rows,
        "notes": [
            "合成语料**不是人类基线**：它只证明算子活着、口径可复算，"
            "**不得**用它改任何阈值。",
            "R = 模板侧 ÷ 人类侧（口径同 scripts/ai_tone_calibrate.py）。",
            "两侧皆零的算子在该语料上**不可测**——别把它读成「干净」。",
        ],
    }


def render_text(report: dict[str, Any]) -> str:
    """人读的报告（表格）。"""
    lines: list[str] = []
    human = report["human_side"]
    template = report["template_side"]
    lines.append("=" * 96)
    lines.append("人类基线的可分发替代物：合成语料校准（自造语料，非真实出版小说）")
    lines.append("=" * 96)
    lines.append(
        f"人类侧：{human['source']} —— {human['visible_chars']} 可见字 / {human['paragraphs']} 段"
    )
    lines.append(
        f"模板侧：{template['source']} —— {template['visible_chars']} 可见字 / {template['paragraphs']} 段"
    )
    lines.append("")
    lines.append(f"{'算子':<24}{'人类/千字':>12}{'模板/千字':>12}{'R':>8}  判定")
    for row in report["metrics"]:
        r = "inf" if row["ratio"] is None else f"{row['ratio']:.2f}"
        lines.append(
            f"{row['operator']:<24}{row['human_per_1k']:>12.3f}"
            f"{row['template_per_1k']:>12.3f}{r:>8}  {row['verdict']}"
        )
    lines.append("")
    for row in report["ratios"]:
        lines.append(f"比率指标 {row['metric']}：人类侧 {row['human']:.3f} / 模板侧 {row['template']:.3f}")
    lines.append("")
    for note in report["notes"]:
        lines.append(f"※ {note}")
    return "\n".join(lines)


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="自造语料的 AI 味算子校准（人类基线的可分发替代物）"
    )
    ap.add_argument(
        "--corpus",
        default=None,
        help="人类侧语料（文件或目录下的 *.txt），如公版《西游记》文本；缺省用内置合成语料",
    )
    ap.add_argument("--format", choices=["text", "json"], default="text", help="stdout 格式")
    ap.add_argument("--out", default=None, help="报告副本写到哪（默认只输出到 stdout）")
    args = ap.parse_args(argv)

    if args.corpus:
        human_text = load_corpus(args.corpus)
        human_source = f"user corpus: {args.corpus}"
    else:
        human_text = NATURAL_CORPUS
        human_source = "built-in synthetic (original text, written for this repo)"

    report = build_report(human_text, TEMPLATE_CORPUS, human_source=human_source)
    rendered = render_json(report) if args.format == "json" else render_text(report)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(rendered)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        print(f"\n（报告副本已写到 {out_path}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
