"""observer delta 源锚定检测（2026-09-26 批次，事故：observer 转写失真）。

背景：observer 把「第一镖头」写成「第一代老镖头」（凭空捏造代际）混进 knowledge
delta，现有校验全是结构 / 引用 / op 语义（validator.validate_delta /
structured_output._array_shape_errors），**无任何「delta 文本 vs 源文本」内容比对**，
只能靠人工风控门肉眼拦。

本模块提供纯函数 :func:`check_delta_source_anchoring`，对 delta 的 7 数组条目做两条
内容级检查：

    1. **excerpt 归一化核验**（OBS-EXCERPT-NOT-VERBATIM）：``evidence.excerpt`` 声明为
   原文摘录，非空时经引号/标点/空白归一化后必须是任一源文本（同样归一化）的
   子串——归一化消除 LLM 机械转写（引号 ``“”``→``''`` 等）的误报；实测背景
   违规率仍 ~60%（observer 复述型 excerpt），本信号定位为人工抽检入口，见模块
   docstring。
2. **after 文本 bigram 锚定**（OBS-UNSOURCED-PHRASE）：op ∈ {add, update} 且 after
   为 str 的条目，抽 CJK bigram（连续汉字二元组）集合，覆盖率 =
   |after_bigrams ∩ corpus_bigrams| / |after_bigrams|；覆盖率低于
   :data:`DELTA_BIGRAM_COVERAGE_MIN` 报违规。after 的 CJK bigram 数 <
   ``_MIN_BIGRAMS``（短状态词）或 corpus 无 CJK bigram → 跳过。

定位：**恒 warning 的体检信号，绝不阻断**——findings 只进节点 output /
pause payload 摘要，不进 BLOCKING_RULES、不碰 severity 矩阵、不改 gate 判定。
理由：observer 的 after 是**概括性转述**而非逐字摘录，覆盖率天然不会到 1.0；
阈值以下只说明「该条目与源文本词汇脱节、值得人工看一眼」，不构成硬伤。

阈值依据（DELTA_BIGRAM_COVERAGE_MIN = 0.08，2026-09-26 dev 库实测）：
- 校准脚本 ``scripts/delta_anchoring_calibrate.py`` 对 dev 库 ``data/novelos.db``
  的实测分布（每条 after vs 其所属章最新草稿，与生产接线同构；bigram 数已过
  ``_MIN_BIGRAMS=6`` 门槛）：n=398，p10=0.0986 / p25=0.1786 / p50=0.2971 /
  p85=0.500 / max=1.000（observer:v1 子集 n=334：p10=0.108）。
- 0.08 取正常质量带下沿（p10）之下：触发率 8.0%（32/398），受影响 delta 27 个
  ——作为恒 warning 体检信号不刷屏。
- **已知的判别力上限（如实记录）**：observer 的 after 是高度概括的状态转写
  （「冷静、试探」「警觉、受伤但仍在行动」），低覆盖率是常态；「第一代老镖头」
  式捏造的构造用例覆盖率 ≈0.06-0.23（取决于重叠词量），与正常概括带**部分重叠**
  ——bigram 覆盖率只能抓「几乎完全脱节」的条目，弱信号，主要价值在 excerpt
  核验的配合下提供抽检入口。低覆盖样本 20 条人工检视结论：全部为正常状态概括，
  无一条实捏造（2026-09-26，见校准脚本输出）。

excerpt 核验的实测背景（如实记录）：observer 的 excerpt 在 dev 库上**并非逐字
摘录**——逐字口径违规率 84.5%（1792/2121），引号/标点归一化后仍 59.8%
（1269/2121，全量）；按长度分桶 32%~78%，主因是模型的**复述型 excerpt**
（凭印象转写正文、引号 ``"``→``'``、压缩删节）而非捏造。因此本模块做引号/
标点/空白**归一化后**比对（消除机械转写误报），且该信号定位为「人工抽检入口」
——背景违规率高，但「第一代老镖头」事故条目的 excerpt 恰属任意版本都不命中的
捏造形态（dlt_2cefe69cd672 实证），抽检时优先看它。

边界：
- 源文本（corpus）strip 后全空 → 返回 []（无源可校，不可证伪即不报）。
- 非 dict 数组条目 / 缺 evidence / evidence 非 dict → 跳过（结构校验另有守卫）。
- bigram corpus 为空 → 跳过 bigram 检查（同上）。
- after 的 CJK bigram 数 < _MIN_BIGRAMS → 跳过（短状态词无锚定意义：实测
  bigram<6 组 p50=0.000，半数完全脱节且全是正常状态快照写法，纳入只产噪声）。
"""

from __future__ import annotations

from typing import Any, Sequence

__all__ = [
    "DELTA_BIGRAM_COVERAGE_MIN",
    "check_delta_source_anchoring",
]

# 与 state-delta-v0 的 7 数组一致（chapter_commit.pipeline_common._OBSERVER_ALL_ARRAYS
# 同名清单——core 不得 import workflows，此处按项目架构独立声明；一致性由接线测试看守）。
_DELTA_ARRAYS: tuple[str, ...] = (
    "character_changes",
    "world_changes",
    "relationship_changes",
    "new_events",
    "resolved_hooks",
    "new_hooks",
    "debt_changes",
)

# after 锚定检查覆盖的 op（delete 无新文本可锚定）。
_ANCHORED_OPS = frozenset({"add", "update"})

# bigram 覆盖率下限（阈值依据见模块 docstring；勿在无实测的情况下调整）。
DELTA_BIGRAM_COVERAGE_MIN: float = 0.08

# after 的 CJK bigram 数下限：低于此视为短状态词（「死亡」「冷静、试探」），
# 无锚定意义，跳过（实测依据见模块 docstring）。
_MIN_BIGRAMS = 6

# excerpt 归一化时剔除的字符：LLM 高频机械转写的引号系 / 破折号 / 省略号 /
# 间隔号 / 空白（dev 库实证：observer 会把正文 ``“”`` 转写成 ``''``、删节号改写）。
# 中文正文标点（，。！？等）保留——它们是逐字性的一部分。
_PUNCT_DROP = str.maketrans(
    "",
    "",
    "\u201c\u201d\u2018\u2019'\"\u300c\u300d\u300e\u300f\u3010\u3011"
    "\u2026\u2014\u2015\u00b7\u3000 \t\n\r",
)


def _normalize_verbatim(text: str) -> str:
    """excerpt 逐字核验的归一化形态：剔除引号系/破折号/省略号/空白。"""
    return text.translate(_PUNCT_DROP)


def _is_cjk(ch: str) -> bool:
    """CJK 统一表意文字基本区（U+4E00..U+9FFF）；扩展区不在 delta 文本实测分布内，不展开。"""
    return "\u4e00" <= ch <= "\u9fff"


def _cjk_bigrams(text: str) -> set[str]:
    """抽连续汉字二元组集合；CJK 字符不足 2 个 → 空集。"""
    chars = [ch for ch in text if _is_cjk(ch)]
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


def _sample(text: str, limit: int = 50) -> str:
    return text[:limit]


def check_delta_source_anchoring(
    delta: dict[str, Any],
    source_texts: Sequence[str],
) -> list[dict[str, Any]]:
    """对 delta 的 7 数组做源锚定检查，返回 findings 列表（恒 warning，不抛错）。

    参数：
        delta: 完整 delta dict（至少含 7 数组键；缺失键按空数组处理）。
        source_texts: 源文本序列（生产接线 = 本章最新草稿 draft_text）。

    返回：findings，每条形如：
        - {"rule_id": "OBS-EXCERPT-NOT-VERBATIM", "path": "<数组名>[i]",
           "sample": <excerpt 前 50 字>}
        - {"rule_id": "OBS-UNSOURCED-PHRASE", "path": "<数组名>[i]",
           "coverage": <float>, "sample": <after 前 50 字>}

    边界：``source_texts`` strip 后全空 → 无源可校，返回 []。
    """
    corpus_text = "\n".join(t for t in source_texts if isinstance(t, str))
    if not corpus_text.strip():
        return []
    corpus_bigrams = _cjk_bigrams(corpus_text)
    normalized_sources = [_normalize_verbatim(t) for t in source_texts
                          if isinstance(t, str)]

    findings: list[dict[str, Any]] = []
    for arr_name in _DELTA_ARRAYS:
        arr = delta.get(arr_name)
        if not isinstance(arr, list):
            continue
        for i, item in enumerate(arr):
            if not isinstance(item, dict):
                continue
            path = f"{arr_name}[{i}]"

            # a) excerpt 归一化核验（引号/标点转写不误报；背景违规率见 docstring）
            evidence = item.get("evidence")
            excerpt = (
                evidence.get("excerpt")
                if isinstance(evidence, dict) else None
            )
            if isinstance(excerpt, str) and excerpt.strip():
                normalized_excerpt = _normalize_verbatim(excerpt)
                if normalized_excerpt and not any(
                    normalized_excerpt in t for t in normalized_sources
                ):
                    findings.append({
                        "rule_id": "OBS-EXCERPT-NOT-VERBATIM",
                        "path": path,
                        "sample": _sample(excerpt),
                    })

            # b) after 文本 bigram 锚定
            op = item.get("op")
            after = item.get("after")
            if op in _ANCHORED_OPS and isinstance(after, str) and after.strip():
                after_bigrams = _cjk_bigrams(after)
                if (
                    after_bigrams
                    and len(after_bigrams) >= _MIN_BIGRAMS
                    and corpus_bigrams
                ):
                    covered = len(after_bigrams & corpus_bigrams)
                    coverage = covered / len(after_bigrams)
                    if coverage < DELTA_BIGRAM_COVERAGE_MIN:
                        findings.append({
                            "rule_id": "OBS-UNSOURCED-PHRASE",
                            "path": path,
                            "coverage": round(coverage, 4),
                            "sample": _sample(after),
                        })
    return findings
