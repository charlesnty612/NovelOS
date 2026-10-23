"""``run_spec.py --changed`` 的并集口径判别测试（2026-10-23 批次二十九 task-24）。

背景（真实门禁盲区）
-------------------
``--changed`` 旧口径只筛「**突变目标源文件**是否在改动集里」⇒ **新增规格若其目标源文件
未改，永远不会被选中**。实证：``l16_structured_output.py`` 于批次二十五新建，其目标
``structured_output.py`` 随后三批未改 ⇒ 三批门禁都没跑过它，直到批次二十八由一条
``[SURVIVED]`` 暴露。

本文件把新口径变成可断言对象（四条）：
1. **新规格 + 目标未改** ⇒ 仍被选中（并集来源②）；
2. 目标文件改动的旧场景 ⇒ 仍被选中（并集来源①，防修反向）；
3. **规格文件一改只跑它自己的突变**——不得全量 428 条一起跑（省时开关不得退化）；
4. 不传 ``--changed`` 时集合不变（向后兼容）。

全部走 ``--precheck``（只读，不碰源码），因此可与其它测试并发。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUN_SPEC = ROOT / "tests" / "mutation_specs" / "run_spec.py"
L16 = "tests/mutation_specs/l16_structured_output.py"
L16_TARGET = "packages/core/agent_runtime/structured_output.py"
L18 = "tests/mutation_specs/l18_genre_modules.py"


@pytest.fixture(scope="module")
def runner():
    """把执行器当模块加载（它只在 ``__main__`` 下才执行 main）。"""
    spec = importlib.util.spec_from_file_location("mutation_run_spec_under_test", RUN_SPEC)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mutation_run_spec_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_changed_spec_file_is_selected_even_when_target_untouched(
    runner, monkeypatch, capsys
):
    """① 并集来源②：规格文件改了、目标源文件未改 ⇒ 该规格的突变必须被选中。"""
    n_l16 = len(runner.load_spec(ROOT / L16))
    monkeypatch.setattr(runner, "_git_changed_paths", lambda rev: {L16})
    rc = runner.main(["--all", "--changed", "--precheck"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert f"目标文件命中 0 条 / 规格文件命中 {n_l16} 条 ⇒ 合计 {n_l16} 条" in out, out
    assert f"[预检] 突变 {n_l16} 条" in out, out


def test_changed_target_file_is_still_selected(runner, monkeypatch, capsys):
    """② 并集来源①：目标源文件改动 ⇒ 旧场景仍被选中（防修成「只看规格文件」）。"""
    n_l16 = len(runner.load_spec(ROOT / L16))
    monkeypatch.setattr(runner, "_git_changed_paths", lambda rev: {L16_TARGET})
    rc = runner.main(["--all", "--changed", "--precheck"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert f"目标文件命中 {n_l16} 条 / 规格文件命中 0 条 ⇒ 合计 {n_l16} 条" in out, out
    assert f"[预检] 突变 {n_l16} 条" in out, out


def test_changed_spec_file_does_not_pull_in_unrelated_specs(
    runner, monkeypatch, capsys
):
    """③ 省时开关不得退化：只改 l18 规格 ⇒ 只跑 l18 的突变，不牵连同批其它规格。"""
    n_l18 = len(runner.load_spec(ROOT / L18))
    monkeypatch.setattr(runner, "_git_changed_paths", lambda rev: {L18})
    rc = runner.main(["--all", "--changed", "--precheck"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert f"目标文件命中 0 条 / 规格文件命中 {n_l18} 条 ⇒ 合计 {n_l18} 条" in out, out
    assert f"[预检] 突变 {n_l18} 条" in out, out
    # 若退化成「规格文件改了就全跑」，这里会是全部条目数（当前 428+）而不是 9。
    assert "合计 428" not in out


def test_without_changed_flag_selection_is_unchanged(runner, capsys):
    """④ 不传 ``--changed`` ⇒ 全量集合不变（向后兼容）。"""
    total = sum(len(runner.load_spec(p)) for p in runner.discover_specs())
    rc = runner.main(["--all", "--precheck"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert f"[预检] 突变 {total} 条 / 问题 0 处" in out, out


def test_clean_worktree_changed_reports_zero(runner, monkeypatch, capsys):
    """工作区干净 ⇒ 「目标文件命中 0 条 / 规格文件命中 0 条 ⇒ 合计 0 条」+ 无事可做。"""
    monkeypatch.setattr(runner, "_git_changed_paths", lambda rev: set())
    rc = runner.main(["--all", "--changed", "--precheck"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "目标文件命中 0 条 / 规格文件命中 0 条 ⇒ 合计 0 条" in out, out
    assert "无事可做" in out, out
