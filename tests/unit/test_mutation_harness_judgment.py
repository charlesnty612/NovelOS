"""突变执行器的**判定逻辑**仓内看守（2026-10-23）。

为什么补这条
------------
本仓坑区已登记**两条**「量具失真」事故：**非零退出码同时承载「跑过了且红」与
「压根没跑」**。2026-10-23 实测查出**第三处同形状**：

- ``scripts/mutation_harness.py`` 的 pytest 子进程**不给** ``--basetemp``
  ⇒ 本机 ``%LOCALAPPDATA%\\Temp\\pytest-of-<user>`` 根目录属主拒绝访问
  ⇒ 用到 ``tmp_path`` 的用例在**准备阶段**就 ERROR ⇒ 子进程退出码 **1**；
- 而旧判定只看输出里有没有 ``" passed"`` 子串 ⇒ ``1 passed, 3 errors`` **也满足**
  ⇒ 而退出码非 0 又会被主循环落进 ``[CAUGHT]``
  ⇒ **把「环境炸了」报成「突变被抓住」**。

⇒ 本文件把「判定表」钉成**仓内可复跑**的断言：

    pytest tests/unit/test_mutation_harness_judgment.py -q

- **不依赖**仓外文件、**不依赖** ``git show HEAD:``（CI 上没有完整历史 / 浅克隆会失败）
  ⇒ 旧版行为一律用**内联的假输出**构造；
- 断言打在**判定标签**上（``SURVIVED`` / ``CAUGHT`` / ``INVALID``），不是输出子串；
- **同一张表、两个被测对象**（``mutation_harness`` 与 ``mutation_probe``）
  ⇒ 两套突变执行器的口径不许分叉（本仓坑区「两套实现口径分叉」形状的防线）。

判定表（pytest）
-----------------
======================  ==========================  ==========
退出码                  摘要                        判定
======================  ==========================  ==========
0                       全过                        SURVIVED
1                       含 ``" failed"``           CAUGHT
1                       **无** ``" failed"``        INVALID
1                       **既有 passed 又有 errors** INVALID（★防恒真）
2 / 3 / 4 / 5           任意                        INVALID
任意                    含 ``"no tests ran"``       INVALID
======================  ==========================  ==========

判定表（vitest）——口径**来自实测**（vitest 2.1.9），**不是**照搬 pytest
-------------------------------------------------------------------------
======================  ==========================================  ==========
退出码                  输出关键行                                  判定
======================  ==========================================  ==========
0                       ``Test Files 1 passed`` / ``Tests 13 passed``  SURVIVED
1                       ``Test Files 1 failed`` / ``Tests 1 failed``  CAUGHT
1                       ``Test Files 1 failed`` / ``Tests  no tests`` INVALID
1                       ``No test files found``（**无** Test Files 行）  INVALID
1                       错误栈（**无** Test Files 行）                 INVALID
0                       ``No test files found, exiting with code 0``  INVALID（★防恒真）
======================  ==========================================  ==========

⚠️ vitest 的两个判别点缺一不可：①「``Test Files``」行（有没有真的跑起测试文件）；
②「``Tests  no tests``」（套件在收集期就炸了 ⇒ **一个用例都没跑到**）——
只看 ① 的话，「收集期错误」与「断言失败」的摘要**逐字同形**。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts import mutation_harness, mutation_probe

REPO_ROOT = Path(__file__).resolve().parents[2]

SURVIVED = "SURVIVED"
CAUGHT = "CAUGHT"
INVALID = "INVALID"

#: 两个被测对象（同一张判定表 ⇒ 两个实现必须同口径）。
PYTEST_JUDGES = [
    pytest.param(mutation_harness.judge_pytest_run, mutation_probe.judge_pytest_run, id="pytest"),
]
VITEST_JUDGES = [
    pytest.param(mutation_harness.judge_vitest_run, mutation_probe.judge_vitest_run, id="vitest"),
]

#: (退出码, 输出, 期望判定, 为什么)
PYTEST_CASES = [
    (0, "1 passed in 0.10s", SURVIVED, "全过 ⇒ 突变存活"),
    (
        1,
        "1 failed, 3 passed in 0.12s\nFAILED tests/x.py::test_y - assert 1 == 2",
        CAUGHT,
        "真红 ⇒ 被抓住",
    ),
    (
        1,
        "3 passed, 16 errors in 3.87s\nERROR tests/x.py::test_y",
        INVALID,
        "ERROR 而非 FAIL ⇒ 环境炸了（一个断言都没跑到）",
    ),
    (
        1,
        "1 passed, 3 errors in 1.34s",
        INVALID,
        "★防恒真：既有 passed 又有 errors ⇒ 仍 INVALID（旧版正是误报成 CAUGHT 的那一格）",
    ),
    (2, "Interrupted: 1 error during collection", INVALID, "中断"),
    (3, "INTERNALERROR> Traceback (most recent call last)", INVALID, "内部错误"),
    (4, "ERROR: file or directory not found: tests/x.py", INVALID, "用法 / 收集错误"),
    (5, "no tests ran in 0.02s", INVALID, "零测试被收集"),
    (0, "no tests ran in 0.02s", INVALID, "「no tests ran」优先于退出码 0"),
]

#: (退出码, 输出, 期望判定, 为什么)——输出形态取自 vitest 2.1.9 的**实测**
VITEST_CASES = [
    (0, " Test Files  1 passed (1)\n      Tests  13 passed (13)", SURVIVED, "全过"),
    (
        1,
        " Test Files  1 failed (1)\n      Tests  1 failed (1)\n ❯ failing.test.ts:4:13",
        CAUGHT,
        "断言失败 ⇒ 被抓住",
    ),
    (
        1,
        " Failed Suites 1\n FAIL  broken.test.ts\n Test Files  1 failed (1)\n      Tests  no tests",
        INVALID,
        "收集期错误（Failed Suites）⇒ 一个用例都没跑到",
    ),
    (1, "No test files found, exiting with code 1", INVALID, "零测试收集（无 Test Files 行）"),
    (
        1,
        "Error: MODULE_NOT_FOUND\nRequire stack:\n- C:\\tmp\\vitest.config.ts",
        INVALID,
        "环境炸了（无 Test Files 行）",
    ),
    (
        0,
        "No test files found, exiting with code 0",
        INVALID,
        "★防恒真：--passWithNoTests ⇒ 退出码 0 但一个用例都没跑",
    ),
]


@pytest.mark.parametrize("harness_judge,probe_judge", PYTEST_JUDGES)
@pytest.mark.parametrize(
    "code,out,expected,why",
    PYTEST_CASES,
    ids=[c[3].split("：")[0][:12] + f"-{c[0]}" for c in PYTEST_CASES],
)
def test_pytest_judgment_table(harness_judge, probe_judge, code, out, expected, why):
    """pytest 判定表：两个执行器**逐格**同口径。"""
    for judge in (harness_judge, probe_judge):
        label, reason = judge(code, out)
        assert label == expected, (
            f"{why}：期望 {expected}，实得 {label}（理由：{reason}）"
        )
        assert reason, "判定必须给出理由（不许空口下结论）"


@pytest.mark.parametrize("harness_judge,probe_judge", VITEST_JUDGES)
@pytest.mark.parametrize(
    "code,out,expected,why",
    VITEST_CASES,
    ids=[c[3].split("：")[0][:12] + f"-{c[0]}" for c in VITEST_CASES],
)
def test_vitest_judgment_table(harness_judge, probe_judge, code, out, expected, why):
    """vitest 判定表（口径来自实测）：两个执行器**逐格**同口径。"""
    for judge in (harness_judge, probe_judge):
        label, reason = judge(code, out)
        assert label == expected, (
            f"{why}：期望 {expected}，实得 {label}（理由：{reason}）"
        )
        assert reason, "判定必须给出理由（不许空口下结论）"


@pytest.mark.parametrize(
    "code,out,expected,_why",
    PYTEST_CASES + VITEST_CASES,
    ids=[f"p{i}" for i in range(len(PYTEST_CASES))]
    + [f"v{i}" for i in range(len(VITEST_CASES))],
)
def test_harness_and_probe_never_disagree(code, out, expected, _why):
    """**口径不许分叉**：两套执行器对同一输入必须给同一标签。

    ⚠️ 本仓坑区「两套实现口径分叉」（同一个闸门对两份产物给出不同判定）。
    """
    is_pytest = "Test Files" not in out and "no tests ran" not in out
    h = mutation_harness.judge_pytest_run if is_pytest else mutation_harness.judge_vitest_run
    p = mutation_probe.judge_pytest_run if is_pytest else mutation_probe.judge_vitest_run
    assert h(code, out)[0] == p(code, out)[0], "两套执行器对同一输入给出了不同判定"


def test_invalid_is_never_reported_as_caught():
    """防恒真总闸：**任何** errors-only / 无执行痕迹的形态都**不许**判成 CAUGHT。"""
    error_shapes = [
        (1, "3 passed, 16 errors in 3.87s"),
        (1, "1 passed, 3 errors in 1.34s"),
        (1, "2 errors in 1.02s"),
        (1, "Interrupted: 1 error during collection"),
        (1, "ERROR: file or directory not found"),
        (1, "no tests ran in 0.02s"),
    ]
    for code, out in error_shapes:
        for judge in (mutation_harness.judge_pytest_run, mutation_probe.judge_pytest_run):
            label, reason = judge(code, out)
            assert label != CAUGHT, f"退出码 {code} + {out!r} 被误判成 CAUGHT（理由：{reason}）"


def test_no_module_level_stdout_reconfigure_in_either_script():
    """**模块级** ``sys.stdout.reconfigure`` 会让 pytest 里 import 即崩 ⇒ 脚本不可测。

    ⚠️ 「不可测的闸门最危险」——闸门正是最该有看守的那类东西。
    """
    for rel in ("scripts/mutation_harness.py", "scripts/mutation_probe.py"):
        path = REPO_ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = [
            node.lineno
            for node in tree.body
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "reconfigure"
        ]
        assert not offenders, (
            f"{rel} 在**模块级**调了 .reconfigure(...)（行 {offenders}）"
            "⇒ pytest 捕获期 import 即崩 ⇒ 请移进函数并判 hasattr"
        )


def test_both_scripts_expose_the_same_judgment_api():
    """两套执行器必须暴露**同名同义**的判定入口（否则调用方要分叉）。"""
    for name in ("judge_pytest_run", "judge_vitest_run"):
        h = getattr(mutation_harness, name)
        p = getattr(mutation_probe, name)
        assert callable(h) and callable(p), f"{name} 必须在两个脚本里都存在"
    for name, value in (
        ("JUDGE_SURVIVED", SURVIVED),
        ("JUDGE_CAUGHT", CAUGHT),
        ("JUDGE_INVALID", INVALID),
    ):
        assert getattr(mutation_harness, name) == value
        assert getattr(mutation_probe, name) == value


# ── A-1（Chief 2026-10-25 批文 · PM-196-A1-code）────────────────────────────────
# 缺陷：``--root`` 空串/纯空白**未被拒** ⇒ ``pathlib.Path("")`` ＝ ``Path(".")``
#       ⇒ ``resolve()`` ＝ **CWD** ⇒ 目标根＝CWD（未必是仓外副本）
#       ⇒ 会在**那棵树**上做突变改写 ⇒ 伤数据，必须由代码拒。
# 同源形态：``--root=.`` 经 ``Path(".").resolve()`` 同样落到 CWD。
#
# 判别设计（改前必红 / 改后必绿）：
# - 四形态（``--root=``／纯空白／``--root=.``／缺 ``--root``）必须**被代码拒**；
# - 拒绝路径必须**零子进程**（``_run`` spy 未被调用）＋**零文件改动**（tmp_path 内文件集不变）；
# - ⚠️ 必须把 CWD 切到**仓外**（``tmp_path``）⇒ 否则 ``--root=.`` 会被「活树内」判据挡住，
#   本组变成恒绿（判别力为零）。
@pytest.mark.parametrize(
    "argv_tail",
    [
        pytest.param(["--root=", "--confirm"], id="root-empty"),
        pytest.param(["--root=   ", "--confirm"], id="root-blank"),
        pytest.param(["--root=.", "--confirm"], id="root-dot"),
        pytest.param(["--confirm"], id="root-missing"),
    ],
)
def test_a1_root_forms_are_rejected_by_code(monkeypatch, tmp_path, argv_tail):
    """A-1 判别：四形态必须抛 ``SystemExit`` 且退出码非 0（改前**不抛** ⇒ 本用例必红）。"""
    launched = []
    monkeypatch.setattr(mutation_harness, "_run", lambda *a, **k: launched.append((a, k)))
    monkeypatch.chdir(tmp_path)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    monkeypatch.setattr("sys.argv", ["mutation_harness.py", *argv_tail])
    with pytest.raises(SystemExit) as exc:
        mutation_harness.main()
    code = exc.value.code
    assert code != 0, f"拒绝路径的退出码必须非 0，实得 {code!r}"
    assert not launched, f"拒绝路径**零子进程**，但起过 {launched!r}"
    after = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    assert after == before, "拒绝路径**零文件改动**"
