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
import hashlib
import importlib
import json
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


# ---------------------------------------------------------------------------
# 主检查
# ---------------------------------------------------------------------------


def check_table(
    table: dict,
    *,
    resolver: Callable[[str, str], Any] = default_resolver,
    rule_table: Callable[[], list[tuple[str, str]]] = default_rule_table,
    scan: Callable[[str], set[str]] = default_scan,
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

    return result


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
