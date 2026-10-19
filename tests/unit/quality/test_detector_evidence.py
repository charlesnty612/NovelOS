"""检测器效果证据表 + 漂移看守的判别测试（2026-10-19）。

本文件守三件事：

1. **证据表结构自洽**——每条规则必有 ``rule_id`` / ``evidence_status`` / ``source``；
   标 ``evidence_status="missing"`` 的规则两侧数字必须是 ``null``（不许编造）；
   15 条 ``AI_PATTERN_RULES`` 规则一条不漏。
2. **漂移看守是活的**——正反一对：
   - 证据表与代码一致 ⇒ 守卫视绿（退出码 0）；
   - 证据表与代码不一致 ⇒ 守卫视红并**点名**（哪条规则、表说 X、代码是 Y）。
   两个方向都测，防止「守卫视红」是因为它对一切都不绿、或「守卫视绿」是因为它
   根本不看代码。
3. **突变自检**——把看守里的「阈值比较」改成恒真 / 恒假，用例**必须红**。
   这是 AGENTS 方法论 §三.2「声称与实测分离」的直接落地：一个从不参与判断的
   比较函数，测出来的「一致」毫无意义。

⚠️ 本文件**不**测「检测器本身的行为」——那是 ``test_ai_patterns.py`` 等既有文件的
职责。本文件测的是**证据表与代码之间的一致性机制**。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "detector_evidence_check.py"
TABLE_PATH = ROOT / "packages" / "core" / "quality" / "detector_evidence.json"

VALID_STATUSES = {"measured", "partial", "missing", "deprecated"}


# ---------------------------------------------------------------------------
# 载入
# ---------------------------------------------------------------------------


def _load_guard():
    """把守看脚本当模块加载（scripts/ 不是包，用 spec_from_file_location）。"""
    spec = importlib.util.spec_from_file_location("detector_evidence_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["detector_evidence_check"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_table() -> dict:
    return json.loads(TABLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def guard():
    return _load_guard()


@pytest.fixture(scope="module")
def table() -> dict:
    return _load_table()


# ---------------------------------------------------------------------------
# 1. 证据表结构自洽
# ---------------------------------------------------------------------------


def test_table_has_version_and_provenance(table: dict):
    """表必须自带版本与「人工维护」的声明——否则读者会误以为它是自动生成的。"""
    assert table["schema_version"]
    assert "人工维护" in table["generated_for"]
    assert table["last_reviewed"]


def test_every_rule_has_required_fields(table: dict):
    """每条规则必有 rule_id / evidence_status / source（任务书硬要求）。"""
    entries = table["rules"] + table.get("deprecated_rules", [])
    assert entries
    for entry in entries:
        assert entry.get("rule_id"), f"有条规则缺 rule_id：{entry}"
        assert entry.get("evidence_status") in VALID_STATUSES, (
            f"{entry.get('rule_id')} 的 evidence_status 非法：{entry.get('evidence_status')}"
        )
        assert "source" in entry, f"{entry.get('rule_id')} 缺 source"


def test_rule_ids_are_unique_and_cover_the_live_rule_set(table: dict):
    """rule_id 不重复，且 15 条 AI_PATTERN_RULES 一条不漏、一条不多。"""
    from packages.core.quality.ai_patterns import AI_PATTERN_RULES

    ids = [entry["rule_id"] for entry in table["rules"] + table.get("deprecated_rules", [])]
    assert len(ids) == len(set(ids)), f"rule_id 重复：{ids}"

    live = {rule.rule_id for rule in AI_PATTERN_RULES}
    covered = {
        entry["rule_id"]
        for entry in table["rules"]
        if entry.get("family") == "ai_pattern" and not entry.get("deprecated")
    }
    assert covered == live, (
        f"证据表与 AI_PATTERN_RULES 不一致：缺 {sorted(live - covered)}，"
        f"多 {sorted(covered - live)}"
    )
    assert len(live) == 15, f"规则集应当是 15 条，实际 {len(live)}"


def test_missing_status_rules_have_null_numbers(table: dict):
    """标 missing 的规则两侧数字必须 null——**绝不许编造**。"""
    for entry in table["rules"]:
        if entry.get("evidence_status") != "missing":
            continue
        assert entry.get("human_side") is None, (
            f"{entry['rule_id']} 标 missing 却有人类侧数字"
        )
        assert entry.get("generated_side") is None, (
            f"{entry['rule_id']} 标 missing 却有生成侧数字"
        )


def test_measured_rules_cite_a_corpus_and_a_date(table: dict):
    """标 measured 的规则必须写清语料与采集日期（否则数字无从复核）。"""
    for entry in table["rules"]:
        if entry.get("evidence_status") != "measured":
            continue
        rid = entry["rule_id"]
        assert entry["source"].get("collected_on"), f"{rid} 缺采集日期"
        for side in ("human_side", "generated_side"):
            data = entry.get(side)
            if data is None:
                continue
            assert data.get("corpus") or data.get("note"), f"{rid}.{side} 既无语料也无说明"


def test_thresholds_are_declared_with_a_basis(table: dict):
    """每条 code_ref（阈值/守卫/升级门）必须带定阈依据指针。"""
    for entry in table["rules"]:
        for ref in entry.get("code_refs", []):
            assert ref.get("basis_ref"), f"{entry['rule_id']} 的 {ref.get('name')} 缺 basis_ref"
            assert ref.get("basis_summary"), (
                f"{entry['rule_id']} 的 {ref.get('name')} 缺 basis_summary"
            )


def test_severity_matches_the_live_rule_table(table: dict):
    """表里的 severity 必须与 AI_PATTERN_RULES 登记的逐条一致。"""
    from packages.core.quality.ai_patterns import AI_PATTERN_RULES

    live = {rule.rule_id: rule.severity for rule in AI_PATTERN_RULES}
    for entry in table["rules"]:
        if entry.get("family") != "ai_pattern":
            continue
        assert entry["severity"] == live[entry["rule_id"]]


# ---------------------------------------------------------------------------
# 2. 漂移看守：正反一对
# ---------------------------------------------------------------------------


def test_guard_is_green_on_the_shipped_table(guard, table: dict):
    """证据表与代码一致 ⇒ 无漂移。"""
    result = guard.check_table(table)
    assert not result.errors, f"证据表自身有错：{result.errors}"
    assert not result.drifts, f"证据表与代码不一致：{result.drifts}"
    assert result.checked_refs > 0
    assert result.checked_probes > 0


def test_guard_is_red_when_the_table_disagrees_with_the_code(guard, table: dict):
    """造一份「与代码不一致」的证据表副本 ⇒ 守卫视红并点名。"""
    broken = json.loads(json.dumps(table))
    target = None
    for entry in broken["rules"]:
        for ref in entry.get("code_refs", []):
            if ref.get("name") == "DEFAULT_SHORT_PARA_RATE_PER_1K":
                ref["value"] = 5.5
                target = entry["rule_id"]
    assert target, "证据表里应当有 DEFAULT_SHORT_PARA_RATE_PER_1K 这条阈值"

    result = guard.check_table(broken)
    assert result.drifts, "阈值与代码不一致时守看必须报漂移"
    named = [d for d in result.drifts if d.rule_id == target]
    assert named, f"漂移必须点名规则 {target}，实际：{result.drifts}"
    assert named[0].expected == "5.5"
    assert named[0].actual == "1.0"
    assert not result.consistent


def test_guard_is_red_when_the_code_constant_changes(guard, table: dict):
    """**改代码常量但证据表没改** ⇒ 守卫视红（任务书点名的核心方向）。"""
    real_resolver = guard.default_resolver

    def changed_resolver(module: str, attr: str):
        if attr == "DEFAULT_SHORT_PARA_RATE_PER_1K":
            return 5.5  # 模拟：代码里的阈值被改了，证据表还停在 1.0
        return real_resolver(module, attr)

    result = guard.check_table(table, resolver=changed_resolver)
    assert result.drifts, "代码常量变了而证据表没变时，守看必须报漂移"
    named = [d for d in result.drifts if d.name == "DEFAULT_SHORT_PARA_RATE_PER_1K"]
    assert named, f"漂移必须点名那一条阈值，实际：{result.drifts}"
    assert named[0].actual == "5.5"


def test_guard_catches_word_list_content_change_not_just_length(guard, table: dict):
    """词表**长度不变但内容变了**也必须被抓到（只查个数会漏）。"""
    real_resolver = guard.default_resolver

    def changed_resolver(module: str, attr: str):
        if attr == "AI_PATTERN_HARD_CLICHES":
            return ["改掉了一个词", *real_resolver(module, attr)[1:]]
        return real_resolver(module, attr)

    result = guard.check_table(table, resolver=changed_resolver)
    assert result.drifts, "词表内容变了（长度不变）也必须报漂移"
    kinds = {d.kind for d in result.drifts if d.name == "AI_PATTERN_HARD_CLICHES"}
    assert kinds, f"必须点名那个词表，实际：{result.drifts}"


def test_guard_catches_regex_change(guard, table: dict):
    """正则被改（算子过宽/过窄的常见成因）必须被抓到。"""
    real_resolver = guard.default_resolver

    def changed_resolver(module: str, attr: str):
        value = real_resolver(module, attr)
        if attr == "_ANAPHORA_RE":
            import re

            return re.compile(value.pattern + "|新增")
        return value

    result = guard.check_table(table, resolver=changed_resolver)
    assert result.drifts, "正则变了必须报漂移"
    assert any(d.kind == "regex" for d in result.drifts), (
        f"必须以 regex 类别点名，实际：{result.drifts}"
    )


def test_guard_catches_rule_set_and_severity_changes(guard, table: dict):
    """规则集变了（多一条/少一条）与 severity 变了都必须被抓到。"""
    real_rule_table = guard.default_rule_table

    def changed_rule_table():
        rows = real_rule_table()
        return [(rid, "error" if rid == "AI-SHORT-PARA" else sev) for rid, sev in rows]

    result = guard.check_table(table, rule_table=changed_rule_table)
    assert result.drifts, "severity 变了必须报漂移"
    assert any(d.kind == "severity" for d in result.drifts)


def test_guard_catches_a_rule_added_to_code_but_not_to_table(guard, table: dict):
    """代码里新增了一条规则而证据表没登记 ⇒ 报漂移（规则集方向）。"""
    real_rule_table = guard.default_rule_table

    def changed_rule_table():
        return [*real_rule_table(), ("AI-BRAND-NEW-RULE", "warning")]

    result = guard.check_table(table, rule_table=changed_rule_table)
    assert result.drifts, "新增规则未登记必须报漂移"
    assert any(d.kind == "rule_set" and d.rule_id == "AI-BRAND-NEW-RULE" for d in result.drifts)


# ---------------------------------------------------------------------------
# 3. 突变自检：阈值比较必须是活的
# ---------------------------------------------------------------------------


def test_mutation_proof_comparison_must_be_load_bearing(guard, table: dict, monkeypatch):
    """突变自检：把「阈值比较」改成恒真 ⇒ 漂移检测必须失效。

    若把 ``_values_equal`` 改成恒真之后，用例**仍然**报漂移，那说明这个比较
    根本没参与判断——此时「守卫视绿」这件事毫无意义（AGENTS §三.2：声称与实测分离）。
    本用例因此**要求**突变后检测失效：它绿，就证明比较确实在干活。
    """
    broken = json.loads(json.dumps(table))
    for entry in broken["rules"]:
        for ref in entry.get("code_refs", []):
            if ref.get("name") == "DEFAULT_SHORT_PARA_RATE_PER_1K":
                ref["value"] = 5.5

    # 未突变：必须抓到漂移
    assert guard.check_table(broken).drifts, "前提不成立：未突变时本就抓不到漂移"

    # 突变：比较改成恒真
    monkeypatch.setattr(guard, "_values_equal", lambda expected, actual: True)
    assert not guard.check_table(broken).drifts, (
        "比较被改成恒真后仍然报漂移 ⇒ 该比较根本没参与判断，是装饰"
    )


def test_mutation_proof_comparison_must_not_be_vacuous(guard, table: dict, monkeypatch):
    """突变自检（反向）：把「阈值比较」改成恒假 ⇒ 一致的表也必须报漂移。

    这一条防的是**恒真断言**的镜像：若比较恒假而用例仍说「一致」，那用例
    断言的是一件它测不到的事。
    """
    monkeypatch.setattr(guard, "_values_equal", lambda expected, actual: False)
    result = guard.check_table(table)
    assert result.drifts, "比较被改成恒假后仍然报无漂移 ⇒ 该比较根本没参与判断"


def test_mutation_proof_probes_are_not_vacuous(guard, table: dict, monkeypatch):
    """突变自检（探针方向）：把「探针期望」翻转 ⇒ 必须报漂移。

    探针是唯一能看守**内联字面量阈值**的手段（AI-TRIPLET-OPENING / AI-PRONOUN-PILE
    的「3」不是命名常量）。若探针比较不参与判断，它就是空装饰。
    """
    flipped = json.loads(json.dumps(table))
    for entry in flipped["rules"]:
        for probe in entry.get("probes", []):
            probe["expect_hit"] = not probe["expect_hit"]

    result = guard.check_table(flipped)
    assert result.drifts, "探针期望翻转后必须报漂移"
    assert any(d.kind == "probe" for d in result.drifts), (
        f"必须以 probe 类别点名，实际：{result.drifts}"
    )


# ---------------------------------------------------------------------------
# 4. CLI 契约：退出码 0 / 1 / 2
# ---------------------------------------------------------------------------


def test_cli_exit_codes(guard, table: dict, tmp_path: Path, capsys):
    """0=一致 · 1=有漂移 · 2=自身出错。"""
    consistent = tmp_path / "consistent.json"
    consistent.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
    assert guard.main(["--table", str(consistent), "--quiet"]) == 0

    broken_table = json.loads(json.dumps(table))
    for entry in broken_table["rules"]:
        for ref in entry.get("code_refs", []):
            if ref.get("name") == "DEFAULT_DASH_THRESHOLD_PER_1K":
                ref["value"] = 3.5
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(broken_table, ensure_ascii=False), encoding="utf-8")
    assert guard.main(["--table", str(broken), "--quiet"]) == 1

    missing = tmp_path / "missing.json"
    assert guard.main(["--table", str(missing), "--quiet"]) == 2

    capsys.readouterr()


def test_cli_json_output_is_machine_readable(guard, table: dict, tmp_path: Path, capsys):
    """--json 输出必须能被机器解析，且 consistent 字段与退出码自洽。"""
    path = tmp_path / "table.json"
    path.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
    assert guard.main(["--table", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["consistent"] is True
    assert payload["drifts"] == []
    assert payload["checked"]["code_refs"] > 0
    assert payload["checked"]["probes"] > 0
