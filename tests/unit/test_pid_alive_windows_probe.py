"""`_pid_alive` 的 Windows 存活探测（2026-10-16）。

背景：``tests/unit/test_m1_lock.py`` 长期被 ``--ignore``，理由登记为「单跑会硬杀
整个进程树、真因未定位」。**该登记经实测不成立**（同文件在隔离进程组下
13 passed / 1 skipped / 1.28s，不杀任何进程）。但调查过程中挖到一个**真缺陷**：

    ``os.kill(pid, 0)`` 在 Windows 上**不是稳定的存活探测**。实测同一台机器、
    同一个解释器，对**自己**的探测结果随进程环境而变：

    | 进程环境                     | ``os.kill(self, 0)``       |
    |-----------------------------|----------------------------|
    | 普通                         | 无异常（正确）              |
    | ``CREATE_NEW_PROCESS_GROUP`` | 无异常（正确）              |
    | ``DETACHED_PROCESS``        | ``OSError: [WinError 87]`` |

    旧实现 ``except OSError: return False`` 把它读成「进程已死」。

**危害不是崩溃，是锁静默失效**：``m1_long_run`` 的单实例锁防的是两个 ``run``
实例并发写同一个 SQLite（M1 事故根因）。进程若以 detached 方式启动（Windows
计划任务、部分 CI runner、守护场景），**活着的**实例被判死 ⇒ 第二实例抢锁覆写。

本组用例的形状（**这才是关键**）：必须在**真实 DETACHED_PROCESS 子进程里**断言，
否则在普通环境下断言 `_pid_alive(self) is True` 是恒真的——旧实现在普通环境下
本来就对。

⚠️ 附带守一条反向不变量：探测**不能**退化成「一律返回 True」。
否则「不存在的 PID」也判活 ⇒ 陈旧锁永远解不开 ⇒ 与原缺陷等价，只是反了个方向。

⚠️⚠️ **2026-10-21 事故补充（本模块曾经自伤，务必别退回）**：本文件此前会让
**运行它的整个控制台进程树**静默消失（pytest 全量在 67% 处猝死、无 traceback）。
机制是 ``_CHILD_PROBE`` 里的 ``os.kill(os.getpid(), 0)``：Windows 上
``signal.CTRL_C_EVENT == 0``，CPython 把它翻成
``GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)``—— **广播 Ctrl+C，不是探测存活**。
子进程与父进程共享控制台时，Ctrl+C 同时落到 pytest 与调用它的 shell 上。
修法见 :func:`_child_creationflags`（非 detached 档一律 ``CREATE_NEW_CONSOLE``）。
**新增任何「在子进程里调 ``os.kill(pid, 0)``」的用例，必须先确认该子进程没有
共享控制台**——这条不变量没有别的看守，只能靠这段文字与那个 helper。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import m1_long_run as m1  # noqa: E402

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NEW_CONSOLE = 0x00000010
WIN = sys.platform == "win32"

# 子进程里跑的探针：把探测结果打印成机器可读的一行。
# ⚠️ 刻意**不用** str.format 注入路径——子脚本里含 `{exc.errno}` 这类花括号，
# 会被 .format 当成格式字段（实测 KeyError: 'exc'）。用显式占位符替换。
_CHILD_PROBE = r'''
import json, os, sys
sys.path.insert(0, "__SCRIPTS__")
import m1_long_run as m1

result = {
    "self_alive": m1._pid_alive(os.getpid()),
    "dead_alive": m1._pid_alive(2_000_000_000),
    "zero": m1._pid_alive(0),
    "negative": m1._pid_alive(-1),
    # os.kill 在 DETACHED 下可能抛错——把事实带出来，证明环境确实变了
    "os_kill": "ok",
}
try:
    os.kill(os.getpid(), 0)
except OSError as exc:
    result["os_kill"] = "OSError:" + str(getattr(exc, "errno", None))

print("PROBE_JSON=" + json.dumps(result), flush=True)
'''


def _child_creationflags(flags: int) -> int:
    """探针子进程的创建标志——**非 detached 档必须再给一个独立控制台**。

    ⚠️ 2026-10-21（本仓实测，事故级）：Windows 上 ``signal.CTRL_C_EVENT == 0``，
    而 CPython 的 ``os.kill(pid, 0)`` 对 ``CTRL_C_EVENT`` 走的是
    ``GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)``——**它不是存活探测，而是给
    ``pid`` 所在控制台广播 Ctrl+C**。``_CHILD_PROBE`` 里那句
    ``os.kill(os.getpid(), 0)`` 因此在**与父进程共享控制台**时会连带杀掉
    **运行测试的整棵控制台进程树**：

    - 实测 1：`pytest -n 4 -q` 全量在 67% 处静默猝死（exit 1、无 traceback、
      日志截断）——因为 ``flags=0`` 那一档就是共享控制台；
    - 实测 2：单跑本文件没有任何输出、连调用它的 shell 一起死；
    - 实测 3：``-k "not non_positive_pids"``（只剩 detached 档）→ 8 passed / 2.56s。

    ``DETACHED_PROCESS`` 档本身没有控制台 ⇒ ``GenerateConsoleCtrlEvent`` 失败
    （``WinError 87``）⇒ 安全（这也正是 AGENTS 坑区记的「87」的来源，但它只记了
    症状，没记「非 detached 档会广播 Ctrl+C」这一半）。故只给**非 detached** 档
    补 ``CREATE_NEW_CONSOLE``，让探针的爆炸半径被关在子进程自己的控制台里。
    """
    if flags & DETACHED_PROCESS:
        return flags
    return flags | CREATE_NEW_CONSOLE


def _run_child(flags: int) -> dict:
    script = _CHILD_PROBE.replace("__SCRIPTS__", str(ROOT / "scripts").replace("\\", "/"))
    out = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=_child_creationflags(flags),
    )
    assert out.returncode == 0, f"探针子进程挂了 rc={out.returncode}: {out.stderr[-800:]}"
    line = next(ln for ln in out.stdout.splitlines() if ln.startswith("PROBE_JSON="))
    import json

    return json.loads(line[len("PROBE_JSON=") :])


@pytest.mark.skipif(not WIN, reason="Windows 专用探测；POSIX 走 os.kill 分支")
def test_pid_alive_is_correct_under_detached_process():
    """核心：在 ``DETACHED_PROCESS`` 下对**活着的自己**必须判活。

    这条在旧实现下会红（``except OSError → False``）。
    ⚠️ 顺带确认环境**确实**变了：``os_kill`` 字段应为 ``OSError:*``——
    若它是 ``ok``，说明本机 os.kill 在 DETACHED 下不报错，那这条用例就测不到
    那个分支（此时测试仍会绿，但覆盖的是另一条路径），必须如实区分。
    """
    res = _run_child(DETACHED_PROCESS)
    assert res["self_alive"] is True, (
        f"DETACHED_PROCESS 下活着的进程被判死（self_alive={res['self_alive']}）"
        f"⇒ 单实例锁会静默失效；同环境 os.kill 结果={res['os_kill']}"
    )


@pytest.mark.skipif(not WIN, reason="Windows 专用探测")
def test_pid_alive_is_correct_under_detached_plus_new_group():
    """组合标志档也要对（两种限制叠加时行为不该不同）。"""
    res = _run_child(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
    assert res["self_alive"] is True, f"DETACHED+GROUP 下误判死：{res}"


@pytest.mark.skipif(not WIN, reason="Windows 专用探测")
def test_pid_alive_still_reports_dead_process_as_dead():
    """反向不变量：探测**不能**退化成「一律返回 True」。

    否则「不存在的 PID」也判活 ⇒ 陈旧锁永远解不开（要 --force-lock）⇒
    与原缺陷等价，只是把方向反过来 —— 那不是修复，是把 bug 挪了位置。
    """
    res = _run_child(DETACHED_PROCESS)
    assert res["dead_alive"] is False, (
        f"不存在的 PID 被判活（dead_alive={res['dead_alive']}）"
        "⇒ 探测退化成恒 True；陈旧锁将永远拿不到"
    )


@pytest.mark.skipif(not WIN, reason="Windows 专用探测")
def test_pid_alive_rejects_non_positive_pids_in_every_environment():
    """PID<=0 一律判死，且**不受进程环境影响**（这条在任何环境下都该成立）。"""
    for flags in (0, DETACHED_PROCESS):
        res = _run_child(flags)
        assert res["zero"] is False, f"pid=0 在 flags={flags} 下判活：{res}"
        assert res["negative"] is False, f"pid=-1 在 flags={flags} 下判活：{res}"


@pytest.mark.skipif(not WIN, reason="Windows 专用探测")
def test_os_kill_is_not_a_reliable_probe_on_this_platform():
    """**把调查结论钉成测试**：本机上 ``os.kill(pid,0)`` 在 DETACHED 下不可靠。

    这条的判别方向是反的——它断言的是「旧实现依赖的那个东西**不成立**」。
    将来若 CPython 修好了 ``os.kill``（或换到别的平台），它会变红，
    那时本模块可以（且应该）简化回 ``os.kill`` 单路实现。
    """
    res = _run_child(DETACHED_PROCESS)
    assert res["os_kill"] != "ok", (
        "本机上 os.kill(pid,0) 在 DETACHED_PROCESS 下已经不报错了"
        "⇒ 那个 Windows 坑不再存在，_pid_alive_win 与本模块的 Windows 分支"
        "可以评估退化为单路 os.kill（详见 AGENTS 坑区该条）"
    )


@pytest.mark.skipif(not WIN, reason="Windows 侧兜底契约")
def test_win32_fallback_is_conservative_when_probe_is_inconclusive(
    monkeypatch: pytest.MonkeyPatch,
):
    """``_pid_alive_win`` 返回 None（探测判不出来）时 → **判活**。

    ⚠️ 这条是被突变验证逼出来的：``L0-ALIVE-2``（把 None 兜底改回 ``return False``）
    最初**存活**——因为真实子进程里 ``_pid_alive_win`` 从不返回 None，
    该分支在测试环境下**不可达**。不可达不等于不需要守：它是「探测失败时保守」
    这条契约的唯一执行点，撤掉它等于把误判死放回来。

    手段：把 ``_pid_alive_win`` 换成一个恒返回 None 的桩。
    """
    monkeypatch.setattr(m1, "_pid_alive_win", lambda _pid: None)
    assert m1._pid_alive(12345) is True, (
        "探测判不出来时必须保守判活；判死会让两个实例并发写同一个 SQLite"
    )


@pytest.mark.skipif(not WIN, reason="Windows 侧兜底契约")
def test_win32_probe_result_wins_over_fallback(monkeypatch: pytest.MonkeyPatch):
    """反向：探测**有结论**时必须用探测结果，不得被兜底覆盖。

    没有这条，上一条会被「恒返回 None」的实现骗过（那等于把探测整个废了）。
    """
    monkeypatch.setattr(m1, "_pid_alive_win", lambda _pid: False)
    assert m1._pid_alive(12345) is False, "探测判死 ⇒ 必须判死，兜底不得覆盖它"


def test_posix_fallback_treats_unknown_oserror_as_alive(monkeypatch: pytest.MonkeyPatch):
    """POSIX 分支的保守兜底：``OSError``（非 ESRCH/EPERM）⇒ **判活**。

    为什么要单独测：win32 分支把它短路了，**上面 5 条都覆盖不到它**——
    撤掉这行，DETACHED 那两条照样红，所以「绿色」不代表它被守住。

    手段：把 ``sys.platform`` 临时改成非 win32（让函数走 POSIX 分支），
    再把 ``os.kill`` 换成抛 ``OSError(errno=EINVAL)``。

    ⚠️ 判别方向：旧实现这里是 ``return False``。它错了——理由见
    ``_pid_alive`` 的 docstring：误判活只是要 ``--force-lock``（有人能解开），
    误判死会让两个实例并发写同一个 DB（M1 事故本身）。
    """
    monkeypatch.setattr(m1.sys, "platform", "linux")

    def boom(_pid: int, _sig: int) -> None:
        raise OSError(22, "EINVAL")

    monkeypatch.setattr(m1.os, "kill", boom)
    assert m1._pid_alive(12345) is True, (
        "POSIX 分支遇到解释不了的 OSError 应当保守判活；"
        "判死会让并发写同一个 SQLite（M1 事故根因）"
    )


def test_posix_fallback_still_reports_esrch_as_dead(monkeypatch: pytest.MonkeyPatch):
    """反向：``ProcessLookupError``（ESRCH）必须仍判死。

    没有这条，上一条会被「一切异常都判活」的实现骗过。
    """
    monkeypatch.setattr(m1.sys, "platform", "linux")

    def boom(_pid: int, _sig: int) -> None:
        raise ProcessLookupError("no such process")

    monkeypatch.setattr(m1.os, "kill", boom)
    assert m1._pid_alive(12345) is False, "进程确实不存在 ⇒ 必须判死"
