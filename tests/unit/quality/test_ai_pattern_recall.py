"""检测召回率测量脚手架的判别测试（2026-10-19）。

本文件用**合成语料**（``packages/core/quality/detector_evidence_corpus/``，本批
原创、不含任何受版权文本）验证三件事：

1. 脚手架**能跑通**、输出**结构自洽**（每条规则都有命中数 / 每千字 / 阈值；
   阈值列取自**活的代码常量**⇒ 不可能与代码漂移）；
2. 正例（合成 AI 腔）与负例（合成自然句）之间**存在区分度**；
3. 脚手架**只读**（跑完语料目录一个字节都不许变）。

⚠️ **断言纪律**：本文件**不断言具体数值**（命中数、每千字率都不写死）——那些值
随语料微调就会变，写死即脆。区分度一律用**相对关系**表达（正例 > 负例、
命中规则集合是严格超集）。唯一与「活代码常量」比较的断言是阈值列，那本来就是
「不许漂移」的保证。

⚠️ **这测的不是真实召回率**：真实 AI 文本只能来自真实模型调用（AGENTS §一 硬约束 2），
dev 库既有书稿不得作为验证语料（硬约束 1）⇒ 本批拿不到真实 AI 侧语料。
脚手架的定位是「口径固定、换语料即重算」，真实召回率留待语料到位后测。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ai_pattern_recall.py"
CORPUS_ROOT = ROOT / "packages" / "core" / "quality" / "detector_evidence_corpus"
POSITIVE = CORPUS_ROOT / "positive"
NEGATIVE = CORPUS_ROOT / "negative"
TIME_MARKERS = CORPUS_ROOT / "time_markers"


def _load_script():
    """把脚手架当模块加载（scripts/ 不是包）。"""
    spec = importlib.util.spec_from_file_location("ai_pattern_recall", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ai_pattern_recall"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def script():
    return _load_script()


def _run(script, argv: list[str]) -> dict:
    """跑一次脚手架并解析 JSON 输出。"""
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = script.main(argv)
    assert code == 0, f"脚手架退出码 {code}"
    return json.loads(buffer.getvalue())


# ---------------------------------------------------------------------------
# 1. 语料本身
# ---------------------------------------------------------------------------


def test_synthetic_corpus_exists_and_is_nontrivial():
    """三份合成语料都在，且有实际长度（正例须 >600 可见字，可读性规则才有判定资格）。"""
    from packages.core.quality.wordcount import visible_chars

    for directory in (POSITIVE, NEGATIVE, TIME_MARKERS):
        assert directory.is_dir(), f"合成语料目录缺失：{directory}"
        texts = list(directory.glob("*.txt"))
        assert texts, f"{directory} 里没有 .txt"
    positive = "".join(p.read_text(encoding="utf-8") for p in sorted(POSITIVE.glob("*.txt")))
    assert visible_chars(positive) > 600, "正例太短 ⇒ 可读性类规则连长度门都过不了"


def test_negative_corpus_is_clean_of_hard_cliches():
    """负例是**刻意避开 AI 腔特征**的构造样本——它自己得先干净。

    这条查的是**语料文本**（不是检测器输出），所以不违反「不断言具体数值」。
    """
    from packages.core.quality.ai_patterns import AI_PATTERN_HARD_CLICHES

    negative = "".join(p.read_text(encoding="utf-8") for p in sorted(NEGATIVE.glob("*.txt")))
    for word in AI_PATTERN_HARD_CLICHES:
        assert word not in negative, f"负例里混进了硬套话「{word}」——负例必须干净"


# ---------------------------------------------------------------------------
# 2. 脚手架能跑通 + 输出结构自洽
# ---------------------------------------------------------------------------


def test_help_exits_zero_and_prints_usage(script, capsys):
    assert script.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "--corpus" in out


def test_missing_corpus_argument_exits_two(script, capsys):
    assert script.main([]) == 2
    capsys.readouterr()


def test_output_structure_is_self_consistent(script):
    """每条规则都有 rule_id / 命中数 / 每千字 / 阈值；每千字与命中数自洽。"""
    payload = _run(
        script,
        [
            "--corpus", str(POSITIVE),
            "--label", "正例",
            "--corpus", str(NEGATIVE),
            "--label", "负例",
            "--json",
        ],
    )
    assert set(payload) == {"disclaimer", "corpora"}
    corpora = payload["corpora"]
    assert len(corpora) == 2

    from packages.core.quality.ai_patterns import AI_PATTERN_RULES

    live_ids = {rule.rule_id for rule in AI_PATTERN_RULES}
    for corpus in corpora:
        assert corpus["files"] >= 1
        assert corpus["visible_chars"] > 0
        assert corpus["rules"], "语料为空"
        rule_ids = {row["rule_id"] for row in corpus["rules"]}
        assert rule_ids == live_ids, (
            f"脚手架输出的规则集与 AI_PATTERN_RULES 不一致："
            f"缺 {sorted(live_ids - rule_ids)}，多 {sorted(rule_ids - live_ids)}"
        )
        per_k = corpus["visible_chars"] / 1000.0
        for row in corpus["rules"]:
            assert isinstance(row["hits"], (int, float))
            assert row["hits"] >= 0
            assert isinstance(row["per_1k"], (int, float))
            assert row["threshold"], f"{row['rule_id']} 的阈值列为空"
            assert row["per_1k"] == pytest.approx(row["hits"] / per_k, rel=1e-6)
        assert corpus["total_hits"] == pytest.approx(
            sum(row["hits"] for row in corpus["rules"])
        )


def test_thresholds_come_from_live_code(script):
    """阈值列必须取自**活的代码常量**⇒ 脚手架的阈值不可能与代码漂移。"""
    from packages.core.quality.ai_patterns import (
        DEFAULT_DASH_THRESHOLD_PER_1K,
        DEFAULT_SHORT_PARA_RATE_PER_1K,
    )

    payload = _run(script, ["--corpus", str(POSITIVE), "--json"])
    rows = {row["rule_id"]: row["threshold"] for row in payload["corpora"][0]["rules"]}
    assert str(DEFAULT_SHORT_PARA_RATE_PER_1K) in rows["AI-SHORT-PARA"]
    assert str(DEFAULT_DASH_THRESHOLD_PER_1K) in rows["AI-PUNCT-ABUSE"]


def test_disclaimer_is_present_in_both_output_modes(script, capsys):
    """「不是真实 AI 文本的召回率」这句标注必须出现在**两种**输出里。"""
    from packages.core.quality.ai_patterns import DEFAULT_SHORT_PARA_RATE_PER_1K  # noqa: F401

    payload = _run(script, ["--corpus", str(POSITIVE), "--json"])
    assert "不是" in payload["disclaimer"] and "召回率" in payload["disclaimer"]
    assert "硬约束" in payload["disclaimer"]

    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        script.main(["--corpus", str(POSITIVE)])
    text = buffer.getvalue()
    assert "合成代理" in text
    assert "不是**真实 AI 文本的召回率" in text


# ---------------------------------------------------------------------------
# 3. 区分度（只用相对关系，不断言具体数值）
# ---------------------------------------------------------------------------


def test_positive_and_negative_corpora_are_discriminated(script):
    """正例（合成 AI 腔）命中数必须**多于**负例（合成自然句）。

    只用相对关系表达，不写死任何具体数字。
    """
    payload = _run(
        script,
        [
            "--corpus", str(POSITIVE),
            "--label", "正例",
            "--corpus", str(NEGATIVE),
            "--label", "负例",
            "--json",
        ],
    )
    positive, negative = payload["corpora"]
    assert positive["total_hits"] > negative["total_hits"], (
        f"正例命中 {positive['total_hits']} 未多于负例 {negative['total_hits']} ⇒ 无区分度"
    )

    pos_ids = {row["rule_id"] for row in positive["rules"] if row["hits"] > 0}
    neg_ids = {row["rule_id"] for row in negative["rules"] if row["hits"] > 0}
    assert neg_ids < pos_ids, (
        f"正例命中的规则集合 {sorted(pos_ids)} 必须严格包含负例的 {sorted(neg_ids)}"
    )


def test_time_marker_corpus_exercises_the_continuity_operators(script):
    """时间标记语料必须让两条 CONT-* 算子都**有命中**（脚手架覆盖得到它们）。"""
    payload = _run(script, ["--corpus", str(TIME_MARKERS), "--json"])
    rows = {row["rule_id"]: row["hits"] for row in payload["corpora"][0]["rules"]}
    assert rows["CONT-CLOCK-DAYBREAK"] > 0
    assert rows["CONT-TIME-BACKSTEP"] > 0


# ---------------------------------------------------------------------------
# 4. 只读
# ---------------------------------------------------------------------------


def test_script_does_not_modify_the_corpus(script):
    """跑完语料目录必须一个字节都不变（只读纪律）。"""
    def _digest() -> dict[str, str]:
        out = {}
        for path in sorted(CORPUS_ROOT.rglob("*")):
            if path.is_file():
                out[str(path.relative_to(CORPUS_ROOT))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        return out

    before = _digest()
    assert before, "语料目录是空的"
    script.main(["--corpus", str(POSITIVE), "--json"])
    script.main(["--corpus", str(NEGATIVE), "--corpus", str(TIME_MARKERS), "--json"])
    assert _digest() == before, "脚手架动了语料目录里的文件——它必须是只读的"
