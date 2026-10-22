# -*- coding: utf-8 -*-
"""检测器效果证据表 ↔ 代码实际值的**漂移看守**（只读）。

问题背景
--------
本仓 15 条 AI 味 / 连续性检测器的「效果证据」（人类侧 vs 生成侧的命中数、每千字
密度、精确率抽样、定阈依据）长期**只以源码注释的形式存在**。注释没有机器校验，
于是「改了阈值/词表但注释没改」就会静默发生——声称还挂在，但已经不真了。

本脚本是那张证据表的**看守**，不是它的生成器：
证据表 ``packages/core/quality/detector_evidence.json`` 是**人工维护**的
（``generated_for`` 字段里写明了这一点），本脚本只做一件事——把表里**断言的值**
与**代码里当前的实际值**逐条比对，不一致就点名报错。

它比对的**全部是代码里的真实值**（import 后的常量 / 词表 / 正则 / 规则表），
**不是注释文本**——所以「改代码不改表」必然被抓到，而「改注释」与本脚本无关
（注释不是被测物）。

判据（四类 + 两项全局）
----------------------
1. **常量**（``kind=value`` / ``kind=dict_item``）：阈值、守卫下限、severity 升级门。
2. **词表**（``kind=list`` / ``list_first`` / ``sorted_items`` / ``str``）：
   元素个数 **与** 内容 sha256 双查——只查个数会漏掉「换了一个词但个数不变」。
3. **正则**（``kind=regex``）：``re.Pattern.pattern`` 的 sha256。算子过宽/过窄
   最常见的成因就是正则被动了而阈值没动。
4. **行为探针**（``probes``）：阈值**没有命名常量**的规则（``AI-TRIPLET-OPENING``
   与 ``AI-PRONOUN-PILE`` 的「3」是内联字面量）靠探针看守。探针成对：
   ``expect_hit=true`` 与 ``expect_hit=false`` 各一条，**双向**都成立才算探针活着
   （只验正例 ⇒ 「探针恒不触发」会被误当成通过）。
5. **规则集**：表里 ``family=ai_pattern`` 的 rule_id 集合必须与
   ``AI_PATTERN_RULES`` **逐条相等**（多一条少一条都报）。
6. **severity**：每条规则的 ``severity`` 必须与 ``AI_PATTERN_RULES`` 里登记的一致。

只读
----
不写任何文件、不碰数据库、不联网。``--json`` 只是把同一份结论打到 stdout。

退出码
------
* ``0`` —— 一致（无漂移）
* ``1`` —— 有漂移（输出点名：哪条规则、证据表说 X、代码实际是 Y）
* ``2`` —— 自身出错（表文件不存在 / JSON 非法 / 依赖 import 失败 / 表格结构非法）

用法
----
::

    python scripts/detector_evidence_check.py                # 默认检查 + 打印用法
    python scripts/detector_evidence_check.py --json         # 机器可读输出
    python scripts/detector_evidence_check.py --table PATH   # 指定另一份表（复算用）
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_TABLE = REPO_ROOT / "packages" / "core" / "quality" / "detector_evidence.json"

USAGE = """\
用法: python scripts/detector_evidence_check.py [选项]

  --table PATH   证据表路径（默认 packages/core/quality/detector_evidence.json）
  --json         输出 JSON（默认输出人读文本）
  --quiet        只在有漂移时输出
  -h/--help      显示本帮助

退出码: 0=一致  1=有漂移  2=自身出错
"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Drift:
    """一条漂移：证据表的断言 vs 代码的实际值。"""

    kind: str  # value | dict_item | list | list_first | sorted_items | str | regex
    rule_id: str
    name: str
    expected: str
    actual: str
    detail: str = ""


@dataclass
class CheckResult:
    drifts: list[Drift] = field(default_factory=list)
    checked_refs: int = 0
    checked_probes: int = 0
    checked_rules: int = 0
    table_last_reviewed: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        return not self.drifts and not self.errors


# ---------------------------------------------------------------------------
# 比较与指纹（**唯一的比较单点**——测试要证明它是被真正用上的）
# ---------------------------------------------------------------------------


def _values_equal(expected: Any, actual: Any) -> bool:
    """标量比较。

    * 布尔只与布尔比（``True`` 不等于 ``1``——否则 ``expect_hit`` 探针会失真）；
    * 数值按**值**比（int/float 互通：JSON 里的 ``100`` 与 ``100.0`` 是同一个阈值）；
    * 其余按类型严格相等（字符串逐字比）。
    """
    if isinstance(expected, bool) or isinstance(actual, bool):
        return (
            isinstance(expected, bool)
            and isinstance(actual, bool)
            and expected == actual
        )
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return float(expected) == float(actual)
    return type(expected) is type(actual) and expected == actual


def _fingerprint_list(items: Iterable[Any]) -> str:
    return hashlib.sha256(
        "\n".join(str(x) for x in items).encode("utf-8")
    ).hexdigest()


def _fingerprint_list_first(items: Iterable[Any]) -> str:
    return hashlib.sha256(
        "\n".join(str(x[0]) for x in items).encode("utf-8")
    ).hexdigest()


def _fingerprint_sorted_items(mapping: Any) -> str:
    return hashlib.sha256(
        "\n".join(str(kv) for kv in sorted(mapping.items())).encode("utf-8")
    ).hexdigest()


def _fingerprint_str(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _fingerprint_regex(pattern: Any) -> str:
    return hashlib.sha256(str(pattern.pattern).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 取值：默认从**活的代码**里 import 取
# ---------------------------------------------------------------------------


def default_resolver(module: str, attr: str) -> Any:
    """import 模块并取属性——**代码里的真实值**，不是注释文本。"""
    mod = importlib.import_module(module)
    return getattr(mod, attr)


def default_rule_table() -> list[tuple[str, str]]:
    """``AI_PATTERN_RULES`` 的 ``(rule_id, severity)`` 列表（活代码）。"""
    from packages.core.quality.ai_patterns import AI_PATTERN_RULES

    return [(rule.rule_id, rule.severity) for rule in AI_PATTERN_RULES]


def default_scan(prose: str) -> set[str]:
    """跑一次统一扫描入口，返回命中的 rule_id 集合。"""
    from packages.core.quality.ai_patterns import scan_ai_patterns

    return {hit["rule_id"] for hit in scan_ai_patterns(prose)}


def default_descriptions() -> dict[str, str]:
    """``AI_PATTERN_RULES`` 的 ``{rule_id: description}``（活代码里的说明文字）。"""
    from packages.core.quality.ai_patterns import AI_PATTERN_RULES

    return {rule.rule_id: (rule.description or "") for rule in AI_PATTERN_RULES}


# --- description 里的数字（剥日期 / 时刻 / 章 id 噪声）----------------------
_DESC_DATE_RE = re.compile(r"20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}")
_DESC_TIME_RE = re.compile(r"\d{1,2}:\d{2}")
_DESC_CHAPNO_RE = re.compile(r"ch\d+|\d+ch\b|第\s*\d+\s*章")
_DESC_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def description_numbers(text: str) -> list[str]:
    """description 里出现的**数字**（去掉日期 / 时刻 / 章 id 这类噪声）。"""
    out: list[str] = []
    for m in _DESC_NUMBER_RE.finditer(text):
        span = text[max(0, m.start() - 8) : m.end() + 8]
        if (
            _DESC_DATE_RE.search(span)
            or _DESC_TIME_RE.search(span)
            or _DESC_CHAPNO_RE.search(span)
        ):
            continue
        out.append(m.group(0))
    return out


# ---------------------------------------------------------------------------
# 主检查
# ---------------------------------------------------------------------------


def check_table(
    table: dict,
    *,
    resolver: Callable[[str, str], Any] = default_resolver,
    rule_table: Callable[[], list[tuple[str, str]]] = default_rule_table,
    scan: Callable[[str], set[str]] = default_scan,
    descriptions: Callable[[], dict[str, str]] = default_descriptions,
) -> CheckResult:
    """把证据表与代码实际值逐条比对，返回全部漂移。

    参数都是可注入的——测试用它们证明「比较逻辑真的被用上了」
    （把 ``resolver`` 换成返回别的值 ⇒ 必须报漂移）。
    """
    result = CheckResult()
    result.table_last_reviewed = table.get("last_reviewed")

    rules = table.get("rules")
    if not isinstance(rules, list):
        result.errors.append("证据表缺 'rules' 数组")
        return result
    deprecated = table.get("deprecated_rules", [])
    if not isinstance(deprecated, list):
        result.errors.append("证据表的 'deprecated_rules' 不是数组")
        return result

    # --- 表结构自洽（每条必有 rule_id / evidence_status / source）----------
    seen_ids: set[str] = set()
    for entry in rules + deprecated:
        rid = entry.get("rule_id")
        if not isinstance(rid, str) or not rid:
            result.errors.append(f"有条规则缺 rule_id：{entry!r:.120}")
            continue
        if rid in seen_ids:
            result.errors.append(f"rule_id 重复：{rid}")
        seen_ids.add(rid)
        if "evidence_status" not in entry:
            result.errors.append(f"{rid} 缺 evidence_status")
        if "source" not in entry:
            result.errors.append(f"{rid} 缺 source")
        if entry.get("evidence_status") == "missing":
            for side in ("human_side", "generated_side"):
                if entry.get(side) not in (None, {}):
                    result.errors.append(
                        f"{rid} 标 evidence_status=missing 但 {side} 非 null——"
                        "「没有实测数字」必须写 null"
                    )

    # --- 逐条规则：常量 / 词表 / 正则 --------------------------------------
    for entry in rules:
        rid = entry.get("rule_id", "<unknown>")
        result.checked_rules += 1
        for ref in entry.get("code_refs", []):
            _check_ref(result, rid, ref, resolver)
        for wl in entry.get("word_lists", []):
            _check_ref(result, rid, wl, resolver)
        for rx in entry.get("regexes", []):
            _check_ref(result, rid, rx, resolver)

    # --- 全局：规则集 + severity -------------------------------------------
    live_rules = rule_table()
    live_ids = {rid for rid, _ in live_rules}
    live_sev = dict(live_rules)
    table_ai_ids = {
        entry["rule_id"]
        for entry in rules
        if entry.get("family") == "ai_pattern" and not entry.get("deprecated")
    }
    for missing in sorted(live_ids - table_ai_ids):
        result.drifts.append(
            Drift(
                kind="rule_set",
                rule_id=missing,
                name=missing,
                expected="证据表里应有这条规则",
                actual="AI_PATTERN_RULES 有、证据表没有",
                detail="新加规则必须同时登记进证据表",
            )
        )
    for extra in sorted(table_ai_ids - live_ids):
        result.drifts.append(
            Drift(
                kind="rule_set",
                rule_id=extra,
                name=extra,
                expected="AI_PATTERN_RULES 里应有这条规则",
                actual="证据表有、代码的 AI_PATTERN_RULES 没有",
                detail="规则被删/改名了，证据表没跟上",
            )
        )
    for entry in rules:
        rid = entry.get("rule_id", "<unknown>")
        if rid not in live_sev:
            continue
        if entry.get("family") != "ai_pattern":
            continue
        if not _values_equal(entry.get("severity"), live_sev[rid]):
            result.drifts.append(
                Drift(
                    kind="severity",
                    rule_id=rid,
                    name=rid,
                    expected=str(entry.get("severity")),
                    actual=str(live_sev[rid]),
                    detail="severity 与 AI_PATTERN_RULES 登记的不一致",
                )
            )
    for entry in deprecated:
        rid = entry.get("rule_id", "<unknown>")
        if rid in live_ids:
            result.drifts.append(
                Drift(
                    kind="rule_set",
                    rule_id=rid,
                    name=rid,
                    expected="已废弃，不应在 AI_PATTERN_RULES 里",
                    actual="代码的 AI_PATTERN_RULES 里有它",
                    detail="废弃的算子被重新加回来了",
                )
            )

    # --- 表内语义字段自洽（2026-10-23 task-16 Q-1）--------------------------
    _check_semantics(result, table, rules, deprecated)

    # --- description 数字的来源登记（2026-10-23 task-16 Q-4）----------------
    try:
        live_descriptions = descriptions()
    except Exception as exc:  # pragma: no cover - 依赖 import 失败
        result.errors.append(f"取 AI_PATTERN_RULES 的 description 失败：{exc}")
        live_descriptions = {}
    _check_descriptions(result, table, rules, live_descriptions, resolver)

    # --- 逐条规则：行为探针 ------------------------------------------------
    for entry in rules:
        rid = entry.get("rule_id", "<unknown>")
        for probe in entry.get("probes", []):
            result.checked_probes += 1
            try:
                hit_ids = scan(probe.get("text", ""))
            except Exception as exc:  # pragma: no cover - 探针文本异常
                result.errors.append(f"{rid} 探针执行失败：{exc}")
                continue
            actual_hit = rid in hit_ids
            expected_hit = bool(probe.get("expect_hit"))
            if not _values_equal(expected_hit, actual_hit):
                result.drifts.append(
                    Drift(
                        kind="probe",
                        rule_id=rid,
                        name=probe.get("why", "")[:60],
                        expected=f"命中={expected_hit}",
                        actual=f"命中={actual_hit}",
                        detail="行为探针与预期不符（阈值/字面量可能被改了）",
                    )
                )

    # --- 探针成对（2026-10-23 task-16 Q-2）---------------------------------
    # ⚠️ **名单从活代码派生**（``AI_PATTERN_RULES`` 的 rule_id 集合）——**不手挑**、
    # 也**不从表里派生**（从表里派生 = 删空某条规则的探针后就没人要求它了 ⇒ 空壳）。
    for rid in sorted(live_ids):
        entry = next((e for e in rules if e.get("rule_id") == rid), None)
        if entry is None:
            continue  # 缺整条规则由 rule_set 漂移点名，不在这里重复报
        probes = entry.get("probes", [])
        trues = sum(1 for p in probes if p.get("expect_hit") is True)
        falses = sum(1 for p in probes if p.get("expect_hit") is False)
        if trues < 1 or falses < 1:
            result.errors.append(
                f"{rid} 的行为探针**未成对**：expect_hit=true {trues} 条 / "
                f"expect_hit=false {falses} 条（各须 ≥1）"
                "⇒ 该规则的探针被删空或只剩单边 ⇒ 阈值/正则被改时无守卫"
                "（名单由 AI_PATTERN_RULES 派生，不手挑）"
            )

    return result


# ---------------------------------------------------------------------------
# 表内**语义**字段自洽（2026-10-23 task-16 Q-1）
# ---------------------------------------------------------------------------

#: ``separation`` 的判据是**舍入包络**而不是相等：表里的 rate 存到**两位小数**，
#: 而分离度是拿**未舍入**的 rate 算的 ⇒ 直接比必然对不上。
#: ⇒ 用「rate 的舍入半宽」构造比值的取值区间，separation 落在区间内即自洽。
#: 实测（2026-10-23 task-16 Q-1④）：AI-PUNCT-ABUSE 的 9.34 落在
#: [1.725/0.195, 1.735/0.185] = [8.846, 9.378] 内 ⇒ **自洽**（未舍入的 0.1852
#: 算出来正是 9.34）；而 AI-CONTRAST-PAIR 的 6.63 落在
#: [0.755/0.225, 0.765/0.215] = [3.356, 3.558] **之外** ⇒ 真正的口径分叉。


def _rate_half_width(value: float) -> float:
    """一个**已存进表**的 rate 的舍入半宽（按它实际存了几位小数算）。"""
    if isinstance(value, int) or float(value).is_integer():
        return 0.5
    text = repr(float(value))
    if "e" in text or "E" in text:  # pragma: no cover - 表内无科学计数法
        return 0.005
    decimals = len(text.split(".", 1)[1].rstrip("0"))
    return 0.5 * (10 ** -decimals)


def _separation_envelope(h_rate: float, g_rate: float) -> tuple[float, float] | None:
    """给定两侧**已存**的 rate，比值的取值区间（含各自的舍入半宽）。"""
    h_delta = _rate_half_width(h_rate)
    g_delta = _rate_half_width(g_rate)
    h_lo, h_hi = h_rate - h_delta, h_rate + h_delta
    g_lo, g_hi = g_rate - g_delta, g_rate + g_delta
    if h_lo <= 0:
        return None
    return (g_lo / h_hi, g_hi / h_lo)


def _side_numbers(side: Any) -> list[tuple[str, float]]:
    """一侧里的**实测数字**（键 + 值）；``note`` 这类说明字段不算。"""
    if not isinstance(side, dict):
        return []
    out: list[tuple[str, float]] = []
    for key, value in side.items():
        if key == "note":
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out.append((key, float(value)))
    return out


def _basis_path(basis_ref: Any) -> str | None:
    """从 ``"path/to/file.py:12-34"`` 里取**文件路径**部分（行号会漂，文件不会）。"""
    if not isinstance(basis_ref, str) or not basis_ref.strip():
        return None
    head = basis_ref.split(":", 1)[0].strip()
    return head or None


def _check_semantics(
    result: CheckResult,
    table: dict,
    rules: list[dict],
    deprecated: list[dict],
) -> None:
    """**语义字段**看守：表内「说了什么」自不自洽。

    ⚠️ **为什么必须单列一批**（2026-10-23 task-16 Q-1，critical）：本脚本此前
    **只比代码派生值**（常量 / 词表 / 正则 / 探针）⇒ 证据表的**语义面**
    ——``corpora`` / ``status_vocabulary`` / ``separation`` / ``evidence_status``
    / 两侧**全部数字** / ``threshold_summary`` / ``basis_ref`` / ``conflicts``
    ——**改错了也不红**（实测 28 项扰动中 **26 项**漏过）。⇒ 语义面必须自己看守。

    判据（逐条可机判）：
    ① 两侧 ``corpus`` ∈ ``corpora`` 键集（写错语料 id ⇒ 数字指不到任何语料）；
    ② ``evidence_status`` ∈ ``status_vocabulary`` 键集；
    ③ ``measured`` **由定义派生**：两侧各有实测数字、``corpus`` 非 null、
       ``chapters_total`` 非 null（且与 ``corpora[corpus].chapters`` 相等）；
    ④ ``separation`` **由同条目两侧 rate 派生**（不许裸数字 ⇒ 口径分叉无处藏）；
    ⑤ ``basis_ref`` 所指**文件必须存在**（行号会漂，文件不会）；
    ⑥ ``severity_escalation`` 与**代码派生**的升级路径逐条相符。
    """
    corpora = table.get("corpora")
    if not isinstance(corpora, dict):
        result.errors.append("证据表缺 'corpora' 段（语义字段无法核对）")
        corpora = {}
    status_vocab = table.get("status_vocabulary")
    if not isinstance(status_vocab, dict):
        result.errors.append("证据表缺 'status_vocabulary' 段")
        status_vocab = {}

    for entry in rules + deprecated:
        rid = entry.get("rule_id", "<unknown>")

        # ① corpus ∈ corpora 键集 -----------------------------------------
        for side_name in ("human_side", "generated_side"):
            side = entry.get(side_name)
            if not isinstance(side, dict) or not side:
                continue
            corpus_id = side.get("corpus")
            if corpus_id is None:
                continue
            if not isinstance(corpus_id, str) or corpus_id not in corpora:
                result.errors.append(
                    f"{rid} 的 {side_name}.corpus={corpus_id!r} 不在证据表 corpora 键集里"
                    "⇒ 这两侧的数字指不到任何已登记语料"
                )

        # ② evidence_status ∈ status_vocabulary ----------------------------
        status = entry.get("evidence_status")
        if status is not None and status not in status_vocab:
            result.errors.append(
                f"{rid} 的 evidence_status={status!r} 不在 status_vocabulary 键集里"
                f"（合法值：{sorted(status_vocab)}）"
            )

        # ③ measured 由定义派生 --------------------------------------------
        if status == "measured":
            for side_name in ("human_side", "generated_side"):
                side = entry.get(side_name)
                if not isinstance(side, dict) or not side:
                    result.errors.append(
                        f"{rid} 标 evidence_status=measured 但 {side_name} 无实测数字"
                    )
                    continue
                if not _side_numbers(side):
                    result.errors.append(
                        f"{rid} 标 evidence_status=measured 但 {side_name} 里没有任何实测数字"
                    )
                if side.get("corpus") is None:
                    result.errors.append(
                        f"{rid} 标 evidence_status=measured 但 {side_name}.corpus 为 null"
                        "⇒ 语料标识不全（按 status_vocabulary 只能算 partial）"
                    )
                total = side.get("chapters_total")
                if total is None:
                    result.errors.append(
                        f"{rid} 标 evidence_status=measured 但 {side_name}.chapters_total 为 null"
                        "⇒ 口径不全（按 status_vocabulary 只能算 partial）"
                    )
                else:
                    corpus_id = side.get("corpus")
                    known = corpora.get(corpus_id, {}).get("chapters") if corpus_id else None
                    if known is not None and float(known) != float(total):
                        result.errors.append(
                            f"{rid} 的 {side_name}.chapters_total={total} 与 corpora"
                            f"[{corpus_id!r}].chapters={known} 不一致"
                        )

        # ④ separation 由同条目两侧 rate 派生 -------------------------------
        separation = entry.get("separation")
        if separation is not None:
            human = entry.get("human_side") or {}
            generated = entry.get("generated_side") or {}
            h_rate = human.get("rate_per_1k")
            g_rate = generated.get("rate_per_1k")
            if not isinstance(h_rate, (int, float)) or not isinstance(g_rate, (int, float)):
                result.errors.append(
                    f"{rid} 有 separation={separation} 但两侧缺 rate_per_1k"
                    "⇒ 分离度无法由本条目派生（口径分叉无处藏）"
                )
            elif not h_rate:
                result.errors.append(
                    f"{rid} 有 separation={separation} 但人侧 rate_per_1k={h_rate}"
                    "⇒ 比值为无穷，无法派生"
                )
            else:
                derived = float(g_rate) / float(h_rate)
                envelope = _separation_envelope(float(h_rate), float(g_rate))
                if envelope is None:
                    result.errors.append(
                        f"{rid} 有 separation={separation} 但人侧 rate={h_rate} 的舍入半宽"
                        "已覆盖 0 ⇒ 比值区间无界，无法判定自洽"
                    )
                elif not (envelope[0] - 1e-9 <= float(separation) <= envelope[1] + 1e-9):
                    result.errors.append(
                        f"{rid} 的 separation={separation} 落在两侧 rate"
                        f"（{g_rate}/{h_rate}，未舍入比值 {derived:.4f}）的舍入包络"
                        f" [{envelope[0]:.4f}, {envelope[1]:.4f}] 之外"
                        "⇒ 分离度必须由本条目两侧 rate 派生"
                        "（或把另一对数字登记进 conflicts 并改写本字段）"
                    )

        # ⑤ basis_ref 所指文件必须存在 -------------------------------------
        for label, basis_ref in _iter_basis_refs(entry):
            rel = _basis_path(basis_ref)
            if rel is None:
                result.errors.append(f"{rid} 的 {label} 为空")
                continue
            target = REPO_ROOT / rel
            if not target.exists():
                result.errors.append(
                    f"{rid} 的 {label}={basis_ref!r} 指向的文件不存在：{rel}"
                    "（行号会漂，文件不会 ⇒ 指向的文件必须存在）"
                )

    # ⑥ severity_escalation 与代码派生的升级路径逐条相符 -------------------
    for entry in rules:
        rid = entry.get("rule_id", "<unknown>")
        expected = _expected_escalation(rid)
        actual = entry.get("severity_escalation")
        if expected is None:
            if actual is not None:
                result.drifts.append(
                    Drift(
                        kind="severity_escalation",
                        rule_id=rid,
                        name=rid,
                        expected="无升级路径",
                        actual=str(actual),
                        detail="表里登记了升级路径，但代码里没有这条升级路径",
                    )
                )
            continue
        if actual is None:
            result.drifts.append(
                Drift(
                    kind="severity_escalation",
                    rule_id=rid,
                    name=rid,
                    expected=f"{expected['to']}（{expected['kind']}={expected['value']}）",
                    actual="表里没登记升级路径",
                    detail="代码里这条规则有 severity 升级路径，证据表没登记",
                )
            )
            continue
        if (
            actual.get("to") != expected["to"]
            or actual.get("kind") != expected["kind"]
            or not _values_equal(actual.get("value"), expected["value"])
        ):
            result.drifts.append(
                Drift(
                    kind="severity_escalation",
                    rule_id=rid,
                    name=rid,
                    expected=f"{expected['to']}（{expected['kind']}={expected['value']}）",
                    actual=f"{actual.get('to')}（{actual.get('kind')}={actual.get('value')}）",
                    detail="severity 升级路径与代码派生值不一致",
                )
            )


def _iter_basis_refs(entry: dict) -> list[tuple[str, Any]]:
    """列出这条规则里所有 ``basis_ref``（来源指针）及其位置标签。"""
    out: list[tuple[str, Any]] = []
    source = entry.get("source")
    if isinstance(source, dict) and "basis_ref" in source:
        out.append(("source.basis_ref", source["basis_ref"]))
    for ref in entry.get("code_refs", []):
        if isinstance(ref, dict) and "basis_ref" in ref:
            out.append((f"code_refs[{ref.get('name', '?')}].basis_ref", ref["basis_ref"]))
    return out


def _expected_escalation(rule_id: str) -> dict | None:
    """从**活代码**派生这条规则的 severity 升级路径。

    ⚠️ **升级路径的权威在代码**，不在表里——表里的 ``severity_escalation`` 只是
    登记，登记与代码不符才算漂移。
    """
    from packages.core.quality.ai_patterns_paragraph import DEFAULT_LONG_PARA_ERROR_CHARS
    from packages.core.quality.ai_patterns_sentence import (
        _SEVERITY_UPGRADE_LIMITS,
        DEFAULT_DASH_ERROR_MULTIPLIER,
    )

    if rule_id in _SEVERITY_UPGRADE_LIMITS:
        return {"to": "error", "kind": "count", "value": _SEVERITY_UPGRADE_LIMITS[rule_id]}
    if rule_id == "AI-PUNCT-ABUSE":
        return {"to": "error", "kind": "rate_multiplier", "value": DEFAULT_DASH_ERROR_MULTIPLIER}
    if rule_id == "AI-LONG-PARA":
        return {"to": "error", "kind": "chars", "value": DEFAULT_LONG_PARA_ERROR_CHARS}
    return None


def _check_descriptions(
    result: CheckResult,
    table: dict,
    rules: list[dict],
    descriptions: dict[str, str],
    resolver: Callable[[str, str], Any],
) -> None:
    """**description 数字的派生式看守**（2026-10-23 task-16 Q-4）。

    ⚠️ **口径纪律**：**同值 ≠ 同一常量**——按值全域匹配会把 ``AI-TRIPLET-OPENING``
    的「3」误配到 ``_WEAK_WORD_PER_WORD_LIMIT``（同在 ``ai_patterns_sentence.py``）。
    ⇒ 本表对每个 description 数字**具名**登记来源（常量名 / 内联字面量所在函数 /
    表内 JSON 路径 / 表内文本 / 外部说明），看守按**名字**复核 ⇒ 值变了就红。

    判据：
    ① description 里每个数字都**必须**有登记（漏登 ⇒ 红，点名那个数字）；
    ② 每条登记都**必须**复核通过（常量现值 / 字面量仍在 / 表内路径值 / 文本在 /
       外部说明非空）；
    ③ 登记的 ``in_description`` 必须仍出现在**活代码**的 description 里
       （⇒ 改了 description 忘了同步登记 ⇒ 红）。
    """
    for entry in rules:
        rid = entry.get("rule_id", "<unknown>")
        live_desc = descriptions.get(rid)
        if live_desc is None:
            continue  # 不在 AI_PATTERN_RULES 里（style_subscore / 废弃条目）⇒ 无活 description
        registrations = entry.get("description_numbers")
        if not isinstance(registrations, list):
            result.errors.append(
                f"{rid} 的 description 里有数字，但表里没有 description_numbers 登记"
                "⇒ 每个数字都必须登记来源（常量 / 内联字面量 / 表内路径 / 外部值）"
            )
            continue

        registered: dict[str, dict] = {}
        for reg in registrations:
            if not isinstance(reg, dict):
                result.errors.append(f"{rid} 的 description_numbers 里有一条不是对象")
                continue
            raw = reg.get("in_description")
            if not isinstance(raw, str):
                result.errors.append(f"{rid} 的 description_numbers 缺 in_description")
                continue
            key = f"{float(raw):g}"
            if key in registered:
                result.errors.append(
                    f"{rid} 的数字 {raw} 登记了多次（每个数字只登记一次）"
                )
                continue
            registered[key] = reg
            _verify_description_registration(result, rid, reg, entry, resolver)

        for number in description_numbers(live_desc):
            key = f"{float(number):g}"
            if key not in registered:
                result.errors.append(
                    f"{rid} 的 description 里的数字 {number} **没有登记来源**"
                    "⇒ 每个数字要么指向一个具名常量 / 内联字面量 / 表内路径，"
                    "要么显式登记为外部值或历史值"
                )


def _verify_description_registration(
    result: CheckResult,
    rid: str,
    reg: dict,
    entry: dict,
    resolver: Callable[[str, str], Any],
) -> None:
    """复核一条 description 数字登记（按**名字**，不是按值）。"""
    kind = reg.get("kind")
    raw = reg.get("in_description")
    label = f"{rid} 的 description 数字 {raw}"

    if kind == "code_ref":
        module, attr = reg.get("module"), reg.get("attr")
        if not module or not attr:
            result.errors.append(f"{label} 的 code_ref 缺 module/attr")
            return
        try:
            actual = resolver(module, attr)
        except Exception as exc:  # pragma: no cover - 常量缺失
            result.errors.append(f"{label} 的 code_ref 解析失败：{module}.{attr}（{exc}）")
            return
        if not isinstance(actual, (int, float)) or isinstance(actual, bool):
            result.errors.append(f"{label} 的 code_ref 指向的不是数值：{actual!r}")
            return
        value = float(raw)
        expected = float(actual) * 100 if reg.get("unit") == "percent" else float(actual)
        if abs(expected - value) > 1e-9:
            result.errors.append(
                f"{label} 的 code_ref {module}.{attr} 现值 {actual}"
                f"（换算后 {expected}）≠ 登记值 {value}"
                "⇒ 常量改了但 description 没改"
            )
    elif kind == "inline_literal":
        rel, func, value = reg.get("file"), reg.get("function"), reg.get("value")
        if not rel or not func:
            result.errors.append(f"{label} 的 inline_literal 缺 file/function")
            return
        path = REPO_ROOT / rel
        if not path.exists():
            result.errors.append(f"{label} 的 inline_literal 指向的文件不存在：{rel}")
            return
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError as exc:  # pragma: no cover - 源文件坏了
            result.errors.append(f"{label} 的 inline_literal 文件解析失败：{rel}（{exc}）")
            return
        target = next(
            (
                n
                for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func
            ),
            None,
        )
        if target is None:
            result.errors.append(f"{label} 的 inline_literal 找不到函数 {func}（{rel}）")
            return
        literals = {
            float(node.value)
            for node in ast.walk(target)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
        }
        if float(value) not in literals:
            result.errors.append(
                f"{label} 的内联字面量 {value} 已不在 {func} 里"
                "⇒ 阈值改了但 description 没改"
            )
    elif kind == "table_field":
        path = reg.get("path")
        if not isinstance(path, str):
            result.errors.append(f"{label} 的 table_field 缺 path")
            return
        if "description_numbers" in path:
            # ⚠️ **不许自指**：路径指回本登记自己 ⇒ 自指断言恒真（AGENTS 坑区同族）
            result.errors.append(
                f"{label} 的 table_field 路径 {path} 指回 description_numbers 自身"
                "⇒ 自指断言恒真，不算证据"
            )
            return
        node: Any = entry
        try:
            for part in path.split("."):
                if part.endswith("]") and "[" in part:
                    key, idx = part[:-1].split("[", 1)
                    node = node[key][int(idx)]
                else:
                    node = node[part]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            # ⚠️ **不许崩**：路径解析失败**正是**「表里那个字段被改了/删了」的信号
            # ⇒ 报成一条可定位的错误，而不是抛异常让整个看守挂掉。
            result.errors.append(
                f"{label} 的 table_field 路径 {path} 解析失败：{exc}"
                "⇒ 指向的字段已被改动或删除（登记与表分叉了）"
            )
            return
        if not isinstance(node, (int, float)) or isinstance(node, bool):
            result.errors.append(f"{label} 的 table_field {path} 不是数值：{node!r}")
            return
        if abs(float(node) - float(raw)) > 1e-9:
            result.errors.append(
                f"{label} 的 table_field {path} 现值 {node} ≠ 登记值 {raw}"
            )
    elif kind == "table_text":
        blob = json.dumps(entry, ensure_ascii=False)
        if str(raw) not in blob:
            result.errors.append(
                f"{label} 的 table_text：数字 {raw} 已不出现在本规则条目里"
            )
    elif kind == "external":
        note = reg.get("note")
        if not isinstance(note, str) or not note.strip():
            result.errors.append(
                f"{label} 登记为 external 但 note 为空"
                "⇒ 外部值 / 历史值必须写清来源或沿革"
            )
    else:
        result.errors.append(f"{label} 的 kind 未知：{kind!r}")


def _check_ref(
    result: CheckResult,
    rule_id: str,
    ref: dict,
    resolver: Callable[[str, str], Any],
) -> None:
    """比对一条 code_ref / word_list / regex。"""
    name = ref.get("name", "<unnamed>")
    module = ref.get("module")
    attr = ref.get("attr")
    kind = ref.get("kind")
    if not module or not attr or not kind:
        result.errors.append(f"{rule_id} 的 {name} 缺 module/attr/kind")
        return
    result.checked_refs += 1
    try:
        actual = resolver(module, attr)
    except Exception as exc:
        result.drifts.append(
            Drift(
                kind="unresolved",
                rule_id=rule_id,
                name=name,
                expected=f"{module}.{attr}",
                actual=f"取不到：{exc}",
                detail="代码里这个符号没了（改名/删除）",
            )
        )
        return

    if kind == "value":
        if not _values_equal(ref.get("value"), actual):
            result.drifts.append(
                Drift(
                    kind=kind,
                    rule_id=rule_id,
                    name=name,
                    expected=str(ref.get("value")),
                    actual=str(actual),
                    detail=f"{module}.{attr} 的值与证据表不一致",
                )
            )
    elif kind == "dict_item":
        key = ref.get("key")
        if not isinstance(actual, dict) or key not in actual:
            result.drifts.append(
                Drift(
                    kind=kind,
                    rule_id=rule_id,
                    name=name,
                    expected=f"{module}.{attr}[{key!r}]",
                    actual=f"键不存在（现有键：{sorted(actual) if isinstance(actual, dict) else actual}）",
                    detail="字典项被改名或删除",
                )
            )
            return
        if not _values_equal(ref.get("value"), actual[key]):
            result.drifts.append(
                Drift(
                    kind=kind,
                    rule_id=rule_id,
                    name=name,
                    expected=str(ref.get("value")),
                    actual=str(actual[key]),
                    detail=f"{module}.{attr}[{key!r}] 与证据表不一致",
                )
            )
    elif kind in ("list", "list_first", "sorted_items", "str"):
        if kind == "str":
            actual_count = len(str(actual))
            actual_sha = _fingerprint_str(actual)
        elif kind == "sorted_items":
            if not isinstance(actual, dict):
                result.drifts.append(
                    Drift(
                        kind=kind,
                        rule_id=rule_id,
                        name=name,
                        expected="映射",
                        actual=type(actual).__name__,
                        detail=f"{module}.{attr} 不是映射",
                    )
                )
                return
            actual_count = len(actual)
            actual_sha = _fingerprint_sorted_items(actual)
        else:
            if not isinstance(actual, (list, tuple)):
                result.drifts.append(
                    Drift(
                        kind=kind,
                        rule_id=rule_id,
                        name=name,
                        expected="序列",
                        actual=type(actual).__name__,
                        detail=f"{module}.{attr} 不是序列",
                    )
                )
                return
            actual_count = len(actual)
            actual_sha = (
                _fingerprint_list_first(actual)
                if kind == "list_first"
                else _fingerprint_list(actual)
            )
        if not _values_equal(ref.get("count"), actual_count):
            result.drifts.append(
                Drift(
                    kind=kind,
                    rule_id=rule_id,
                    name=name,
                    expected=f"count={ref.get('count')}",
                    actual=f"count={actual_count}",
                    detail=f"{module}.{attr} 的长度与证据表不一致",
                )
            )
        if not _values_equal(ref.get("sha256"), actual_sha):
            result.drifts.append(
                Drift(
                    kind=kind,
                    rule_id=rule_id,
                    name=name,
                    expected=f"sha256={ref.get('sha256')}",
                    actual=f"sha256={actual_sha}",
                    detail=f"{module}.{attr} 的内容与证据表不一致（个数相同也要报）",
                )
            )
    elif kind == "regex":
        actual_sha = _fingerprint_regex(actual)
        if not _values_equal(ref.get("sha256"), actual_sha):
            result.drifts.append(
                Drift(
                    kind=kind,
                    rule_id=rule_id,
                    name=name,
                    expected=f"sha256={ref.get('sha256')}",
                    actual=f"sha256={actual_sha}",
                    detail=f"{module}.{attr} 的正则与证据表不一致",
                )
            )
    else:
        result.errors.append(f"{rule_id} 的 {name} 用了未知的 kind={kind!r}")


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def render_text(result: CheckResult, table_path: Path) -> str:
    lines: list[str] = []
    lines.append("用法: python scripts/detector_evidence_check.py [--table PATH] [--json] [--quiet]")
    lines.append("      退出码 0=一致 1=有漂移 2=自身出错")
    lines.append("")
    lines.append(f"证据表: {table_path}")
    lines.append(
        f"检查范围: {result.checked_rules} 条规则 · {result.checked_refs} 条代码引用 · "
        f"{result.checked_probes} 条行为探针"
    )
    if result.table_last_reviewed:
        lines.append(f"证据表 last_reviewed: {result.table_last_reviewed}")
    lines.append("")
    if result.errors:
        lines.append("自身错误：")
        for err in result.errors:
            lines.append(f"  ✗ {err}")
        lines.append("")
    if result.drifts:
        lines.append(f"漂移（{len(result.drifts)} 处）：")
        for d in result.drifts:
            lines.append(f"  ✗ [{d.kind}] {d.rule_id} · {d.name}")
            lines.append(f"      证据表说: {d.expected}")
            lines.append(f"      代码实际: {d.actual}")
            if d.detail:
                lines.append(f"      说明: {d.detail}")
        lines.append("")
        lines.append("结论: 有漂移 —— 改了检测器就同步改证据表（packages/core/quality/detector_evidence.json）")
    else:
        lines.append("结论: 一致 —— 证据表与代码实际值逐条相符")
    return "\n".join(lines)


def render_json(result: CheckResult, table_path: Path) -> str:
    payload = {
        "table": str(table_path),
        "table_last_reviewed": result.table_last_reviewed,
        "consistent": result.consistent,
        "checked": {
            "rules": result.checked_rules,
            "code_refs": result.checked_refs,
            "probes": result.checked_probes,
        },
        "errors": result.errors,
        "drifts": [
            {
                "kind": d.kind,
                "rule_id": d.rule_id,
                "name": d.name,
                "expected": d.expected,
                "actual": d.actual,
                "detail": d.detail,
            }
            for d in result.drifts
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 cp936 ⇒ 中文输出会变乱码（本仓 scripts/readability_audit.py 同款处理）
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--table", default=str(DEFAULT_TABLE))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    args = parser.parse_args(argv)

    if args.help:
        print(USAGE)
        return 0

    table_path = Path(args.table)
    try:
        table = json.loads(table_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"自身错误：证据表不存在：{table_path}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"自身错误：证据表不是合法 JSON：{exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"自身错误：读不了证据表：{exc}", file=sys.stderr)
        return 2

    try:
        result = check_table(table)
    except Exception as exc:  # 依赖 import 失败等
        print(f"自身错误：检查过程抛异常：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(render_json(result, table_path))
    elif not args.quiet or not result.consistent:
        print(render_text(result, table_path))

    if result.errors:
        return 2
    return 0 if result.consistent else 1


if __name__ == "__main__":
    raise SystemExit(main())
