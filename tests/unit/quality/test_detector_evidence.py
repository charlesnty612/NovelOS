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


# ---------------------------------------------------------------------------
# 5. 语义字段看守（2026-10-23 task-16 Q-1/Q-2/Q-4）
# ---------------------------------------------------------------------------
#
# ⚠️ **这一节守的是什么**（审计 B 实测）：此前看守**只比代码派生值**
# ⇒ 语义面（corpora / status_vocabulary / separation / evidence_status /
# 两侧全部数字 / threshold_summary / basis_ref / conflicts）**改错了也不红**
# （实测 28 项扰动里 **26 项**漏过）。
# ⇒ 本节每条扰动都必须让守卫视红，且**点名**。


def _entry(table: dict, rule_id: str) -> dict:
    for entry in table["rules"]:
        if entry.get("rule_id") == rule_id:
            return entry
    raise AssertionError(f"证据表里没有 {rule_id}")


#: (用例名, 扰动函数, 必须被点名的关键词)
_SEMANTIC_PERTURBATIONS = [
    (
        "corpus_id_typo",
        lambda t: _entry(t, "AI-PUNCT-ABUSE")["human_side"].__setitem__(
            "corpus", "houfu_human_19c"
        ),
        "corpus",
    ),
    (
        "evidence_status_out_of_vocabulary",
        lambda t: _entry(t, "AI-BEAT-REPEAT").__setitem__("evidence_status", "bogus"),
        "evidence_status",
    ),
    (
        "measured_but_corpus_is_null",
        lambda t: _entry(t, "AI-BEAT-REPEAT")["human_side"].__setitem__("corpus", None),
        "corpus",
    ),
    (
        "measured_but_chapters_total_missing",
        lambda t: _entry(t, "AI-BEAT-REPEAT")["generated_side"].pop("chapters_total"),
        "chapters_total",
    ),
    (
        "chapters_total_contradicts_corpora_registry",
        lambda t: _entry(t, "AI-BEAT-REPEAT")["generated_side"].__setitem__(
            "chapters_total", 999
        ),
        "chapters_total",
    ),
    (
        "separation_outside_the_rounding_envelope",
        lambda t: _entry(t, "AI-CONTRAST-PAIR").__setitem__("separation", 6.63),
        "separation",
    ),
    (
        "basis_ref_points_at_a_missing_file",
        lambda t: _entry(t, "AI-BEAT-REPEAT")["source"].__setitem__(
            "basis_ref", "packages/core/quality/no_such_file.py:1-2"
        ),
        "basis_ref",
    ),
    (
        "severity_escalation_registration_removed",
        lambda t: _entry(t, "AI-PUNCT-ABUSE").pop("severity_escalation"),
        "severity_escalation",
    ),
    (
        "severity_escalation_threshold_wrong",
        lambda t: _entry(t, "AI-PUNCT-ABUSE")["severity_escalation"].__setitem__(
            "value", 3
        ),
        "severity_escalation",
    ),
    (
        "probes_of_one_rule_emptied",
        lambda t: _entry(t, "AI-TRIPLET-OPENING").__setitem__("probes", []),
        "未成对",
    ),
    (
        "probes_of_one_rule_left_one_sided",
        lambda t: _entry(t, "AI-PRONOUN-PILE").__setitem__(
            "probes", _entry(t, "AI-PRONOUN-PILE")["probes"][:1]
        ),
        "未成对",
    ),
    (
        "description_number_registration_removed",
        lambda t: _entry(t, "AI-SHORT-PARA")["description_numbers"].pop(),
        "description",
    ),
    (
        "description_number_registration_points_at_wrong_constant",
        lambda t: _entry(t, "AI-TRIPLET-OPENING")["description_numbers"][0].__setitem__(
            "value", 4
        ),
        "description",
    ),
]


@pytest.mark.parametrize(
    ("perturb", "keyword"),
    [(p[1], p[2]) for p in _SEMANTIC_PERTURBATIONS],
    ids=[p[0] for p in _SEMANTIC_PERTURBATIONS],
)
def test_guard_is_red_when_a_semantic_field_is_perturbed(
    guard, table: dict, perturb, keyword
):
    """语义字段扰动 ⇒ 守卫视红**并点名**。

    ⚠️ **这是审计 B 的 critical 缺口**的判别点：这些字段此前**完全没被看守**
    ⇒ 扰动它们守卫视仍然绿 ⇒ 证据表的语义面可以随便改。
    """
    broken = json.loads(json.dumps(table))
    perturb(broken)

    result = guard.check_table(broken)
    assert not result.consistent, (
        f"扰动语义字段后守卫视仍然一致 ⇒ 该字段**没有看守**（缺 keyword={keyword!r}）"
    )
    messages = result.errors + [f"{d.kind} {d.rule_id} {d.expected} {d.actual} {d.detail}" for d in result.drifts]
    assert any(keyword in m for m in messages), (
        f"守卫视红了但没点名 {keyword!r}（诊断不可定位）⇒ 实际消息：{messages}"
    )


def test_guard_is_green_on_the_shipped_table_with_the_new_checks(guard, table: dict):
    """正向：加了语义看守后，**仓内这份表**必须仍然一致。

    ⚠️ 这条是「守卫不是空壳」的反面证据：若新增的 13 类语义判据在仓内表上就红，
    说明判据写错了（而不是表错了）。
    """
    result = guard.check_table(table)
    assert not result.errors, f"语义看守在仓内表上报错：{result.errors}"
    assert not result.drifts, f"语义看守在仓内表上报漂移：{result.drifts}"


def test_every_ai_pattern_rule_has_paired_probes(guard, table: dict):
    """**名单从活代码派生**（``AI_PATTERN_RULES``）⇒ 每条规则探针成对。

    ⚠️ **为什么不能从表里派生**：从表里派生 = 删空某条规则的探针后就没人要求它了
    ⇒ 空壳（审计 B 的 major-2 实测：删空任一条规则的探针，守卫视仍一致）。
    """
    from packages.core.quality.ai_patterns import AI_PATTERN_RULES

    live = {rule.rule_id for rule in AI_PATTERN_RULES}
    for rule_id in sorted(live):
        entry = _entry(table, rule_id)
        probes = entry.get("probes", [])
        trues = sum(1 for p in probes if p.get("expect_hit") is True)
        falses = sum(1 for p in probes if p.get("expect_hit") is False)
        assert trues >= 1 and falses >= 1, (
            f"{rule_id} 的探针未成对（true={trues} / false={falses}）"
            "⇒ 阈值或正则被改时无守卫"
        )


def test_severity_escalation_registrations_match_the_code(guard, table: dict):
    """6 条升级路径必须**逐条**登记，且与代码派生的判据一致。

    升级路径的**权威在代码**：``_SEVERITY_UPGRADE_LIMITS``（4 条）+
    破折号乘数（1 条）+ 长段 error 字符阈值（1 条）。
    """
    from packages.core.quality.ai_patterns_paragraph import DEFAULT_LONG_PARA_ERROR_CHARS
    from packages.core.quality.ai_patterns_sentence import (
        _SEVERITY_UPGRADE_LIMITS,
        DEFAULT_DASH_ERROR_MULTIPLIER,
    )

    expected = {
        rule_id: {"to": "error", "kind": "count", "value": value}
        for rule_id, value in _SEVERITY_UPGRADE_LIMITS.items()
    }
    expected["AI-PUNCT-ABUSE"] = {
        "to": "error",
        "kind": "rate_multiplier",
        "value": DEFAULT_DASH_ERROR_MULTIPLIER,
    }
    expected["AI-LONG-PARA"] = {
        "to": "error",
        "kind": "chars",
        "value": DEFAULT_LONG_PARA_ERROR_CHARS,
    }
    assert len(expected) == 6, f"升级路径数变了（{len(expected)}），登记与判据都要更新"

    for rule_id, want in expected.items():
        got = _entry(table, rule_id).get("severity_escalation")
        assert got is not None, f"{rule_id} 的 severity_escalation 没登记"
        assert got["to"] == want["to"], f"{rule_id} 的升级目标档不符：{got}"
        assert got["kind"] == want["kind"], f"{rule_id} 的升级判据类型不符：{got}"
        assert float(got["value"]) == float(want["value"]), (
            f"{rule_id} 的升级阈值与代码不符：表 {got['value']} vs 代码 {want['value']}"
        )


def test_corpora_reachability_is_registered(guard, table: dict):
    """Q-5：9 份语料每条都要登记可达性，且**都**不在版本控制内。

    ⚠️ 这条守的是**事实登记**（不是「语料应该可达」）：本仓实测 9/9 份语料都在
    版本控制之外 ⇒ 表里必须这么写 ⇒ 将来哪天有一份进了版本控制，本用例会红
    ⇒ 提醒同步改可达性登记（而不是让「不可复现」的说法悄悄过期）。
    """
    corpora = table["corpora"]
    assert len(corpora) == 9, f"语料份数变了（{len(corpora)}），可达性登记要同步"
    for corpus_id, corpus in corpora.items():
        assert "in_version_control" in corpus, f"{corpus_id} 没登记 in_version_control"
        assert "reproducible_by" in corpus, f"{corpus_id} 没登记 reproducible_by"
        assert corpus["in_version_control"] is False, (
            f"{corpus_id} 的 in_version_control 变了 ⇒ 可达性登记要同步"
        )
        assert corpus["reproducible_by"] == "本机", (
            f"{corpus_id} 的 reproducible_by 变了 ⇒ 可达性登记要同步"
        )


def test_description_numbers_are_all_registered(guard, table: dict):
    """Q-4：description 里的每个数字都必须**具名**登记来源。

    ⚠️ **为什么不能按值全域匹配**（审计 B 实测）：那会把 ``AI-TRIPLET-OPENING``
    的「3」误配到 ``_WEAK_WORD_PER_WORD_LIMIT``（同值不同常量）⇒ 阈值真改成 4 时
    守卫仍绿 ⇒ 空壳。
    ⇒ 本用例钉的是**登记的具名来源**：``code_ref`` 指向的常量现值必须等于登记值。
    """
    result = guard.check_table(table)
    assert not result.errors, f"description 数字登记有缺口：{result.errors}"

    total = 0
    for entry in table["rules"]:
        rows = entry.get("description_numbers")
        if not rows:
            continue
        for row in rows:
            total += 1
            assert row.get("kind"), f"{entry['rule_id']} 的登记缺 kind"
            if row["kind"] == "code_ref":
                assert row.get("module") and row.get("attr"), (
                    f"{entry['rule_id']} 的 code_ref 登记缺 module/attr"
                )
    assert total >= 80, f"登记的数字数异常偏少（{total}）⇒ 登记被清掉了？"


# ---------------------------------------------------------------------------
# 5. 2026-10-23 批次二十七 task-22（审查 minor M-1/M-2/M-3）
# ---------------------------------------------------------------------------

HUMAN_BASELINE = ROOT / "tests" / "unit" / "quality" / "HUMAN_BASELINE.md"


def test_guard_is_red_when_descriptions_injection_is_empty(guard, table: dict):
    """M-1：descriptions 注入为空 ⇒ 登记了 ``description_numbers`` 的条目必须报 **error**。

    修前行为（2026-10-23 实测留证）：``check_table(table, descriptions=lambda: {})``
    返回 **errors=0 / drifts=0 / consistent=True** —— Q-4 的「登记 ↔ 活 description」
    半边**整体静默失效**，而 resolver / rule_table / scan 失效时都会响亮报错。
    """
    result = guard.check_table(table, descriptions=lambda: {})
    assert result.errors, (
        "descriptions 整体为空时 Q-4 半边静默失效（修前 errors=0）——"
        "必须对「登记了 description_numbers 却取不到 live description」显式报错"
    )
    text = " | ".join(result.errors)
    assert "description_numbers" in text and "取不到 live description" in text, text
    registered_ids = {
        entry["rule_id"] for entry in table["rules"] if entry.get("description_numbers")
    }
    assert any(rid in text for rid in registered_ids), (
        f"error 必须点名具体 rule_id（带 rule_id 的可定位信息），实际：{result.errors}"
    )
    assert not result.consistent


def test_regex_kind_with_non_pattern_reports_drift_not_crash(guard, table: dict):
    """M-2：``kind="regex"`` 但 resolver 返回非 Pattern ⇒ 报 drift，**不得抛异常**。

    修前行为（2026-10-23 实测留证）：直接抛 ``AttributeError: 'int' object has no
    attribute 'pattern'`` —— CLI 被 ``main()`` 收口成 exit 2（响亮），但库调用方会崩。
    """
    real_resolver = guard.default_resolver
    target = next(
        ref
        for entry in table["rules"]
        for ref in entry.get("regexes", [])
        if ref.get("kind") == "regex" and ref.get("attr")
    )

    def wrong_type_resolver(module: str, attr: str):
        if module == target["module"] and attr == target["attr"]:
            return 123  # 声明是 regex，实际给 int
        return real_resolver(module, attr)

    result = guard.check_table(table, resolver=wrong_type_resolver)  # 不得抛
    named = [d for d in result.drifts if d.name == target["attr"]]
    assert named, f"非 Pattern 值必须报 drift 并点名 {target['attr']}，实际：{result.drifts}"
    drift = named[0]
    assert drift.kind == "regex"
    assert drift.expected == "re.Pattern"
    assert drift.actual == "int"


def _human_baseline_section8_rows() -> dict[str, dict[str, str]]:
    """解析 ``HUMAN_BASELINE.md`` §8 的九行可达性表格（``corpus_id`` → 各列）。"""
    text = HUMAN_BASELINE.read_text(encoding="utf-8")
    section = text.split("## 8. 九份语料的**可达性登记**", 1)[1]
    table_part = section.split("**为什么 9/9 都是", 1)[0]
    rows: dict[str, dict[str, str]] = {}
    for line in table_part.splitlines():
        stripped = line.strip()
        if not stripped.startswith("| `"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != 6:  # corpus_id / 种类 / 章数 / 位置 / in_version_control / reproducible_by
            continue
        rows[cells[0].strip("`")] = {
            "kind": cells[1],
            "chapters": cells[2],
            "in_version_control": cells[4].strip("`"),
            "reproducible_by": cells[5].strip("`"),
        }
    return rows


def test_human_baseline_section8_matches_evidence_corpora(table: dict):
    """M-3：``HUMAN_BASELINE.md`` §8 九行表格 ↔ JSON ``corpora`` **逐行双向比对**。

    修前：§8 自称「副本、无机器看守」（诚实但仍是假事实温床——改 JSON 或改文档都无人红）。
    现在：本节多一行 / 少一行 / 任一格的值（种类 / 章数 / ``in_version_control`` /
    ``reproducible_by``）与 JSON 不同 ⇒ 本用例必红。
    """
    rows = _human_baseline_section8_rows()
    corpora = table["corpora"]
    assert set(rows) == set(corpora), (
        "HUMAN_BASELINE §8 表格与 JSON corpora 的 corpus_id 集合不一致："
        f"文档多出 {sorted(set(rows) - set(corpora))} / "
        f"文档缺少 {sorted(set(corpora) - set(rows))}"
    )
    for corpus_id, row in rows.items():
        corpus = corpora[corpus_id]
        expected_vc = str(bool(corpus["in_version_control"])).lower()
        assert row["in_version_control"] == expected_vc, (
            f"{corpus_id} 的 in_version_control：文档 {row['in_version_control']!r} "
            f"vs JSON {expected_vc!r}"
        )
        assert row["reproducible_by"] == corpus["reproducible_by"], (
            f"{corpus_id} 的 reproducible_by：文档 {row['reproducible_by']!r} "
            f"vs JSON {corpus['reproducible_by']!r}"
        )
        assert row["kind"] == corpus["kind"], f"{corpus_id} 的 kind 不一致"
        assert row["chapters"] == str(corpus["chapters"]), f"{corpus_id} 的章数不一致"
