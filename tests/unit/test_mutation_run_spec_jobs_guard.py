"""``run_spec.py --jobs N`` 并发档位的**硬约束看守**（2026-10-23 对抗审查 B 实证）。

背景（真实闸门缺陷，已实证并被 Lead 复算确认）
------------------------------------------------
``--jobs N`` 的分片只保证「**同一目标源文件**」不并发，**不保证**「被测文件
``import`` 的**其它**源文件」不被另一个 worker 临时改坏。对抗审查员 B 在隔离副本上
实测出三类失真：

1. **假 CAUGHT**：一条「只改注释、单独跑必然 SURVIVED」的突变，被**另一条突变临时
   改坏的源文件**污染（该源文件被本条的目标测试文件 ``import`` 并执行）⇒ 并发时
   被报成 ``[CAUGHT]``（实测 8/8 轮）。
   ⚠️ **与两条突变是否共用同一目标测试文件无关** —— 实测**测试文件不同**照样中招
   ⇒ 「按测试文件分片」修不好这个洞。
2. **判定不可复现**：同一输入、同一代码，一次 ``SURVIVED``、一次 ``CAUGHT``
   （时序相关 ⇒ 结论不可重跑复现）；
3. **归因错配**：证据行报告的失败用例**不是该突变能影响的那条**
   ⇒ 复核者「确认红的是预期那条」这一步拿到的是错的证据。

⇒ 并发结果**不得作为闸门结论**。本文件把这条纪律钉成**可执行约束**（不只是文案）：

A. **退出码硬约束**：并发档位（未经 ``--accept-concurrent``）的退出码**恒为 3**，
   **哪怕一条存活都没有** ⇒ 闸门 / CI 不可能把「不可信的并发结果」当成绿的结论。
B. **默认串行**：不传 ``--jobs`` 就是串行 ⇒ 输出里不得出现并发警告。
C. **汇总写明档位**：结论尾部必须写清「串行」还是「并发 N 路」。
D. **警告必须自己喊出来**：四个关键短语齐（假 CAUGHT / 判定不可复现 / 归因错配 /
   不得作为闸门结论），且在**结果汇总之后**再出现一次。
E. **目标文件缺失 ⇒ 记 INVALID 而不是整轮崩**（F-8）。

⚠️ **为什么本文件不真跑突变**：真跑会临时改源码 ⇒ 与 ``pytest -n 20`` 的其它 worker
互相踩（AGENTS 纪律：突变执行器禁与任何读/写源码的操作并发）。⇒ 这里把
``discover_specs`` / ``load_spec`` / ``run_one`` 全部替换成替身 ⇒ **零源码改动、
零 pytest 子进程** ⇒ 可与其它测试并发。**代价**：本组用例不覆盖「并发真的会不会造假
CAUGHT」——那件事由对抗审查的隔离副本实验负责；本组只保证**约束不会被悄悄删掉**。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUN_SPEC = ROOT / "tests" / "mutation_specs" / "run_spec.py"

FAKE_SPEC = "fake_spec_for_jobs_guard.py"
FAKE_ENTRY = (
    "FAKE-1",
    "packages/core/example_target.py",
    "anchor text\n",
    "replacement text\n",
    "tests/unit/test_example_target.py",
)


@pytest.fixture(scope="module")
def runner():
    """把执行器当模块加载（它只在 ``__main__`` 下才执行 main）。"""
    spec = importlib.util.spec_from_file_location(
        "mutation_run_spec_jobs_guard_under_test", RUN_SPEC
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mutation_run_spec_jobs_guard_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_main(runner, monkeypatch, argv: list[str]) -> tuple[int, str]:
    """用替身跑一次 ``main``，返回 ``(退出码, stdout)``。

    替身三件套：``discover_specs`` 不读真实规格、``load_spec`` 给一条假条目、
    ``run_one`` 直接返回固定结果 ⇒ **不碰任何源码、不起任何 pytest 子进程**。

    ⚠️ ``discover_specs`` 的替身必须返回**带 ``.name`` 的对象**（执行器会打印
    ``[p.name for p in spec_files]``）⇒ 这里用 ``pathlib.Path``。
    """
    monkeypatch.setattr(runner, "discover_specs", lambda: [Path(FAKE_SPEC)])
    monkeypatch.setattr(runner, "load_spec", lambda _sf: [FAKE_ENTRY])
    monkeypatch.setattr(
        runner, "run_one", lambda mid, rel, anchor, replacement, test_file, **kw: (
            f"[CAUGHT]   {mid}: FAILED tests/unit/test_example_target.py::test_fake"
        )
    )
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = runner.main(argv)
    return rc, buf.getvalue()


# ---------------------------------------------------------------------------
# A. 退出码硬约束（并发档位不得冒充绿灯）—— 本组最关键的一条
# ---------------------------------------------------------------------------


def test_parallel_run_exit_code_is_3_even_when_nothing_survived(runner, monkeypatch):
    """并发档位**一条存活都没有**时，退出码也**不得为 0**（必须是 3）。

    这是 F-1 的核心：并发会产出**假 CAUGHT**（虚报绿）⇒ 若这里允许 exit 0，
    闸门 / CI 就会把「不可信的并发结果」当成「全量突变通过」。
    """
    rc, out = _run_main(runner, monkeypatch, ["--all", "--jobs", "2"])
    assert "突变存活: 无" in out, "替身应当全部 CAUGHT（前提不成立则本用例无意义）"
    assert rc == 3, f"并发档位在无存活时退出码竟为 {rc} ⇒ 不可信的并发结果被当成绿灯"


def test_accept_concurrent_is_required_for_zero_exit(runner, monkeypatch):
    """显式 ``--accept-concurrent``（承认「只作降噪参考」）才允许退出码 0。"""
    rc, out = _run_main(
        runner, monkeypatch, ["--all", "--jobs", "2", "--accept-concurrent"]
    )
    assert rc == 0, f"已承认并发只作参考时退出码应为 0，实得 {rc}"
    assert "不得作为闸门结论" in out, "即使承认并发，警告也必须照打"


def test_serial_run_exit_code_is_still_zero(runner, monkeypatch):
    """**串行档位**的退出码语义不受影响（0 = 通过）—— 防我把闸门自己堵死。"""
    rc, out = _run_main(runner, monkeypatch, ["--all"])
    assert rc == 0, f"串行档位退出码应仍为 0（本次替身无存活/无效），实得 {rc}"
    assert "档位: 串行" in out


# ---------------------------------------------------------------------------
# B/C. 档位可见性
# ---------------------------------------------------------------------------


def test_default_is_serial_and_prints_no_warning(runner, monkeypatch):
    """**默认不传 ``--jobs`` 就是串行** ⇒ 输出里不得出现并发警告。

    这条钉住「闸门默认安全」：只有显式 ``--jobs N`` 才会进入并发档。
    """
    rc, out = _run_main(runner, monkeypatch, ["--all"])
    assert rc == 0
    assert "不得作为闸门结论" not in out, "串行档位不该打并发警告（默认必须安静）"
    assert "[并行]" not in out, "不传 --jobs 却走了并行分支 ⇒ 默认值被改了"


def test_summary_states_the_tier(runner, monkeypatch):
    """**汇总行必须写明档位** —— 只看结论尾部的人要能一眼看出这是串行还是并发。"""
    _rc, out = _run_main(runner, monkeypatch, ["--all"])
    assert "档位: 串行" in out, "串行汇总行没有写明档位"
    _rc2, out2 = _run_main(runner, monkeypatch, ["--all", "--jobs", "2"])
    assert "档位: 并发" in out2, "并发汇总行没有写明档位"
    assert "2 路" in out2, "并发汇总行没写明路数"


def test_warning_text_contains_all_four_key_phrases(runner):
    """并发警告必须同时说清三类失真 + 「不得作为闸门结论」。"""
    text = runner._parallel_warning(16)
    for kw in ("假 CAUGHT", "判定不可复现", "归因错配", "不得作为闸门结论"):
        assert kw in text, f"并发警告漏掉关键短语：{kw}"


def test_warning_does_not_blame_shared_test_file(runner):
    """警告文案**不得**把成因说成「共用同一目标测试文件」。

    ⚠️ 实测**测试文件不同**照样被污染（污染源是「被 import 的其它源文件」）⇒
    写成「共用同一测试文件」是**错误归因**，会误导下一个人去按测试文件分片
    （那条路修不好这个洞）。
    """
    text = runner._parallel_warning(16)
    assert "共用同一目标测试文件" not in text, (
        "并发警告把成因误写成「共用同一目标测试文件」⇒ 错误归因"
    )
    assert "import" in text, "警告必须点明真正的污染通道（被 import 的其它源文件）"


def test_keywords_constant_does_not_diverge_from_text(runner):
    """``PARALLEL_WARNING_KEYWORDS`` 与文案不得分叉（改文案忘改常量 ⇒ 红）。"""
    text = runner._parallel_warning(16)
    for kw in runner.PARALLEL_WARNING_KEYWORDS:
        assert kw in text, f"常量列了 {kw!r}，但文案里没有 ⇒ 两处已分叉"


def test_parallel_run_prints_warning(runner, monkeypatch):
    """并发跑时输出里必须有那句警告（撤掉 ⇒ 红）。"""
    rc, out = _run_main(runner, monkeypatch, ["--all", "--jobs", "2"])
    assert rc == 3, f"并发档位退出码应为 3，实得 {rc}"
    assert "不得作为闸门结论" in out, "并发输出里没有闸门警告"
    assert "假 CAUGHT" in out and "归因错配" in out


def test_warning_also_printed_at_summary_tail(runner, monkeypatch):
    """警告在**结果汇总之后**也要出现一次（只看 CI 日志尾部的人不得错过）。"""
    _rc, out = _run_main(runner, monkeypatch, ["--all", "--jobs", "2"])
    assert out.count("不得作为闸门结论") >= 2, (
        "并发警告只在开头出现了一次 ⇒ 只看结论尾部（CI 日志 / grep「突变存活」）的人会错过"
    )
    tail = out[out.rfind("突变存活:"):]
    assert "不得作为闸门结论" in tail, "汇总末尾没有警告"


def test_help_text_mentions_parallel_is_not_a_gate_conclusion(runner):
    """``--help`` 里必须写明并发不得作为闸门结论 + 退出码语义（否则没人知道）。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        runner.main(["--help"])
    out = buf.getvalue()
    assert "不得作为闸门结论" in out
    assert "默认 1=串行" in out
    assert "--accept-concurrent" in out, "--help 没提 --accept-concurrent"
    assert "3=" in out, "--help 没写退出码 3 的含义"


# ---------------------------------------------------------------------------
# E. 目标文件缺失 ⇒ 记 INVALID 而不是整轮崩（F-8）
# ---------------------------------------------------------------------------


def test_missing_target_file_is_invalid_not_crash(runner):
    """目标源文件**不存在**时 ⇒ 记 ``[INVALID]``，**不得**抛异常、**不得**整轮崩。

    原实现直接 ``path.read_text()`` ⇒ ``FileNotFoundError`` ⇒ 整轮崩溃
    （后面所有规格一条都没跑，退出码也不是可判读的语义）。
    """
    rc = runner.run_one(
        "FAKE-MISSING",
        "packages/core/__definitely_not_a_real_file__.py",
        "anchor text\n",
        "replacement text\n",
        "tests/unit/test_example_target.py",
    )
    assert rc.startswith("[INVALID]"), f"目标文件缺失却没记 INVALID，返回：{rc!r}"
    assert "读不出来" in rc, f"INVALID 没说明原因：{rc!r}"
