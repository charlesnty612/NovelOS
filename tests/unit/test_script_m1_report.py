"""``scripts/m1_report.py`` 的纯函数与容错契约（dev-b 批次二十）。

为什么补这个
------------
``m1_report.py``（796 行）把长跑结果渲染成 Markdown 报告，是**零测试**脚本之一
（2026-10-22 批次十九实测）。它有一条明确的容错契约（脚本自己的分节注释写着
「纯读取 + 容错」）：**数据文件坏了要能被跳过并报出来，而不是让整份报告崩掉**。

本文件钉的就是这条契约 + 几个格式化/统计纯函数：

- ``_safe_load_json``：缺文件 / 非法 JSON / 读失败 ⇒ ``None``（不抛）；
- ``_safe_get``：链式取值，任一段不是 dict 或缺键 ⇒ 返回 ``default``；
- ``load_progress``：缺 ``progress.json`` ⇒ 空 dict（脚本继续，只打印告警）；
- ``load_chapters``：**坏文件被跳过并列入 ``skipped``**（不是静默丢，也不是崩）；
- ``_mean``：空列表 ⇒ ``0.0``（不是 ZeroDivisionError）。

零真实 LLM、零内容仓接触、零 dev 库、**零不可逆操作**：全部在 ``tmp_path`` 里造
合成 JSON 文件。
"""

from __future__ import annotations

import json

from scripts.m1_report import (
    _fmt_float,
    _fmt_int,
    _mean,
    _safe_get,
    _safe_load_json,
    load_chapters,
    load_progress,
)


def test_safe_load_json_returns_none_for_missing_or_broken_files(tmp_path):
    """缺文件 / 非法 JSON ⇒ ``None``，**不抛**（报告生成器不能被一个坏文件带崩）。"""
    missing = tmp_path / "nope.json"
    assert _safe_load_json(missing) is None

    broken = tmp_path / "broken.json"
    broken.write_text("{ this is not json", encoding="utf-8")
    assert _safe_load_json(broken) is None

    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert _safe_load_json(ok) == {"a": 1}


def test_safe_get_walks_chained_keys_and_falls_back_to_default():
    """链式取值：任一段不是 dict 或缺键 ⇒ 返回 ``default``（不抛）。"""
    data = {"a": {"b": {"c": 7}}}
    assert _safe_get(data, "a", "b", "c") == 7
    assert _safe_get(data, "a", "b", "missing") is None
    assert _safe_get(data, "a", "b", "missing", default="-") == "-"
    assert _safe_get(None, "a", default="-") == "-"
    # 中间一段不是 dict ⇒ 同样回落 default（不得 AttributeError）
    assert _safe_get({"a": 5}, "a", "b", default="-") == "-"


def test_load_progress_returns_empty_dict_when_file_is_absent(tmp_path):
    """缺 ``progress.json`` ⇒ 空 dict（脚本继续跑，只打印告警）。"""
    assert load_progress(tmp_path) == {}
    assert load_progress(tmp_path / "no-such-dir") == {}


def test_load_chapters_skips_broken_files_and_reports_them(tmp_path):
    """坏章节文件被**跳过并列入 ``skipped``**——不崩、也不静默丢。

    ⚠️ 两条失败方向都钉住：① 崩（一个坏文件带倒整份报告）；② **静默丢**
    （跳过却不报 ⇒ 作者以为章节齐了，其实少了几章）。本用例要求**必须报出来**。
    """
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    (chapters_dir / "ch001.json").write_text(
        json.dumps({"chapter_no": 1, "title": "第一章"}), encoding="utf-8"
    )
    (chapters_dir / "ch002.json").write_text("{ broken", encoding="utf-8")
    (chapters_dir / "ch003.json").write_text(
        json.dumps({"title": "没有章号"}), encoding="utf-8"
    )
    (chapters_dir / "notachapter.json").write_text(
        json.dumps({"chapter_no": 99}), encoding="utf-8"
    )

    chapters, skipped = load_chapters(tmp_path)

    assert list(chapters) == [1], "只有格式与章号都合法的文件进结果"
    assert sorted(skipped) == ["ch002.json", "ch003.json"], (
        f"坏文件必须被**报出来**，实得 {skipped}"
    )
    assert "notachapter.json" not in skipped, "命名不合规的文件本就不在扫描范围内"


def test_load_chapters_returns_empty_when_directory_is_absent(tmp_path):
    """没有 ``chapters/`` 目录 ⇒ 空结果 + 把该目录列入 ``skipped``（不崩）。"""
    chapters, skipped = load_chapters(tmp_path)
    assert chapters == {}
    assert len(skipped) == 1


def test_mean_of_empty_list_is_zero_not_a_crash():
    """空列表 ⇒ ``0.0``（不是 ZeroDivisionError）——报告里的「平均」列不能炸。"""
    assert _mean([]) == 0.0
    assert _mean([1.0, 2.0, 6.0]) == 3.0


def test_number_formatters_use_thousand_separators():
    """数字格式化带千分位（报告里的大数要能一眼读出量级）。"""
    assert _fmt_int(1234567) == "1,234,567"
    assert _fmt_float(1234.567, 1) == "1,234.6"
    assert _fmt_float(0.04, 2) == "0.04"
