"""自造语料校准器（``scripts/human_baseline_synthetic.py``）的判别用例。

为什么要有这条测试
------------------
人类基线语料是**真实出版小说**，不可分发（内容仓不推 GitHub）⇒ 第三方拿不到
「检测器阈值是按人类基线校准过的」的任何证据（见 ``HUMAN_BASELINE.md``）。
本仓的替代路径是**自造语料**：一段本仓库原创的「像人写的」自然叙述 + 一段
「明显模板化」的机器腔，用本仓**已有**的检测器跑一遍，证明

① 算子在自然文本上是**哑的**、在模板文本上是**响的**（量具是活的）；
② 口径可复算（第三方拿同样的脚本能跑出同样的数）。

⚠️ **合成语料不是人类基线**：它只证明「算子活着」，**不能**用来改任何阈值。
本文件因此**不断言任何具体频率数值**（那会脆，且会诱使人把合成语料当基线），
只断言**结构自洽**与**区分度**。

本文件同时守住脚本的**边界**：只读、不联网、默认不往仓库里写东西。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import human_baseline_synthetic as hbs

_REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def report() -> dict:
    """内置合成语料的对照报告。"""
    return hbs.build_report(
        hbs.NATURAL_CORPUS,
        hbs.TEMPLATE_CORPUS,
        human_source="built-in synthetic (original text, written for this repo)",
    )


# ---------------------------------------------------------------------------
# 口径自洽
# ---------------------------------------------------------------------------


def test_report_structure_is_self_consistent(report: dict) -> None:
    """报告的每个数字都要能被**独立复算**出来（不许有算错的口径）。

    这条对应本仓的老教训：「登记的基线数字必须能被复算出来」——写死的数字会
    悄悄过期，而复算式会当场现形。
    """
    assert report["schema"] == "human-baseline-synthetic/v1"
    human = report["human_side"]
    template = report["template_side"]
    assert human["visible_chars"] > 0 and template["visible_chars"] > 0
    assert human["paragraphs"] > 0 and template["paragraphs"] > 0

    rows = {row["operator"]: row for row in report["metrics"]}
    assert set(rows) == set(hbs.OPERATORS), (
        "报告里的算子集合与算子表不一致——新加算子忘了进表，或表里有算子没被量"
    )
    for name, row in rows.items():
        for side, side_chars in (("human", human["visible_chars"]), ("template", template["visible_chars"])):
            count = row[f"{side}_count"]
            expected = count / (side_chars / 1000.0)
            assert row[f"{side}_per_1k"] == pytest.approx(expected, abs=0.001), (
                f"{name} 的 {side} 侧每千字频率与「命中数 ÷ (可见字/1000)」不符"
            )
        if row["human_per_1k"] > 0:
            assert row["ratio"] == pytest.approx(
                row["template_per_1k"] / row["human_per_1k"], abs=0.01
            ), f"{name} 的 R 与「模板侧 ÷ 人类侧」口径不符"
        else:
            assert row["ratio"] is None, f"{name} 人类侧为零命中时 R 应当为空（不是 0）"


def test_every_operator_is_measured_through_the_public_entry_point() -> None:
    """算子表里的每一项都必须**真的**产出数字（不许有登记了却没接线的算子）。

    教训来源：本仓有过「规则登记了却不生效（惰性声明）」——表里有它，消费路径上
    没有它。本条把「登记即被量」钉住。
    """
    measured = hbs.measure(hbs.TEMPLATE_CORPUS)
    assert set(measured["metrics"]) == set(hbs.OPERATORS)
    assert set(measured["ratios"]) == set(hbs.RATIO_METRICS)
    for name in hbs.OPERATORS:
        assert measured["metrics"][name]["count"] >= 0


# ---------------------------------------------------------------------------
# 区分度（**不**断言具体数值）
# ---------------------------------------------------------------------------


def test_templated_side_is_separated_from_the_natural_side(report: dict) -> None:
    """模板侧命中的每一个算子，自然侧都必须**更少**（严格小于）。

    判据是**派生**的：集合 = 「模板侧命中数 > 0 的算子」，不是手挑名单 ⇒ 新增
    算子自动进判别面（不会像另列一份手挑名单那样悄悄变小）。

    另附两条边界断言：模板侧至少要命中一条（否则语料本身死了，量具是哑的），
    自然侧命中总数必须严格少于模板侧。
    """
    rows = {row["operator"]: row for row in report["metrics"]}
    fired = {name: row for name, row in rows.items() if row["template_count"] > 0}
    assert fired, (
        "模板侧一个算子都没命中——语料本身不成立（量具是哑的，"
        "此时「自然侧零命中」什么也证明不了）"
    )
    for name, row in fired.items():
        assert row["human_count"] < row["template_count"], (
            f"{name}：自然侧命中 {row['human_count']}，模板侧 {row['template_count']}——"
            "模板语料没能被区分出来（算子没活，或自然语料不自然）"
        )
    total_human = sum(row["human_count"] for row in rows.values())
    total_template = sum(row["template_count"] for row in rows.values())
    assert total_human < total_template, (
        f"自然侧总命中 {total_human} 未严格少于模板侧 {total_template}"
    )


def test_natural_corpus_stays_free_of_the_ai_flavor_words() -> None:
    """自然侧语料必须**不含**任何禁用词与破折号（它是阳性对照的「阴性」半边）。

    这条是区分度的**前提**：自然侧若混进禁用词，「模板侧命中更多」就可能只是
    「自然侧写得稀烂」的假象。
    """
    from packages.core.quality.ai_patterns import AI_PATTERN_FORBIDDEN_WORDS

    hits = [w for w in AI_PATTERN_FORBIDDEN_WORDS if w in hbs.NATURAL_CORPUS]
    assert not hits, f"自然侧语料混进了禁用词：{hits}"
    assert "——" not in hbs.NATURAL_CORPUS, "自然侧语料不该有破折号"


def test_report_keeps_the_caveat_that_synthetic_is_not_a_baseline(report: dict) -> None:
    """报告必须始终带着「合成语料不是人类基线」的警告（防止它被悄悄删掉）。"""
    joined = "\n".join(report["notes"])
    assert "不是人类基线" in joined
    assert "不得" in joined and "阈值" in joined


# ---------------------------------------------------------------------------
# 语料入口（公版语料由用户指定；脚本不联网）
# ---------------------------------------------------------------------------


def test_user_supplied_corpus_becomes_the_human_side(tmp_path: Path, capsys) -> None:
    """``--corpus`` 指进来的语料成为人类侧（公版文本的接入口）。

    用法示例（公版《西游记》全文放好后）::

        python scripts/human_baseline_synthetic.py --corpus D:/texts/xiyouji
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.txt").write_text(hbs.NATURAL_CORPUS, encoding="utf-8")
    (corpus / "b.txt").write_text("另有一段。", encoding="utf-8")

    loaded = hbs.load_corpus(corpus)
    assert hbs.NATURAL_CORPUS in loaded and "另有一段。" in loaded
    assert loaded.index(hbs.NATURAL_CORPUS) < loaded.index("另有一段。"), "目录语料未按文件名排序拼接"

    rc = hbs.main(["--corpus", str(corpus), "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert str(corpus) in payload["human_side"]["source"]
    assert payload["human_side"]["visible_chars"] > 0


def test_missing_corpus_path_fails_loudly(tmp_path: Path) -> None:
    """语料路径不存在 ⇒ 明确报错退出（不是静默用空语料继续跑）。"""
    with pytest.raises(SystemExit):
        hbs.load_corpus(tmp_path / "no-such-dir")


def test_script_never_reaches_out_to_the_network() -> None:
    """脚本**不联网**（公版语料由用户自备；抓取涉及版权与外部依赖，不在授权内）。"""
    source = Path(hbs.__file__).read_text(encoding="utf-8")
    for forbidden in ("import requests", "import urllib", "urlopen", "import socket", "httpx"):
        assert forbidden not in source, f"脚本里出现了网络访问形态：{forbidden}"


# ---------------------------------------------------------------------------
# 只读边界
# ---------------------------------------------------------------------------


def test_default_run_writes_nothing_into_the_repository(tmp_path: Path) -> None:
    """默认输出只到 stdout ⇒ **仓库根目录不新增任何文件**。

    「--out 的路径由调用方指定，默认不进仓库」是本脚本的只读边界；这条把它
    钉住（用目录清单前后对比来判，不用 git——git 状态会被别的改动污染）。
    """
    before = set(os.listdir(_REPO_ROOT))
    assert hbs.main([]) == 0
    after = set(os.listdir(_REPO_ROOT))
    assert before == after, f"脚本往仓库根目录写了东西：{sorted(after - before)}"


def test_out_copy_is_written_where_the_caller_asks(tmp_path: Path, capsys) -> None:
    """``--out`` 把报告副本写到**调用方指定**的位置（测试里是临时目录）。"""
    target = tmp_path / "report.json"
    assert hbs.main(["--format", "json", "--out", str(target)]) == 0
    capsys.readouterr()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema"] == "human-baseline-synthetic/v1"
    assert payload["metrics"], "写出的报告没有指标行"
