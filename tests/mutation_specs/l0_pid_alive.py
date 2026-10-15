"""`_pid_alive` Windows 存活探测修复的突变规格（2026-10-16 · 主会话自办）。

缺陷：``os.kill(pid, 0)`` 在 Windows 上不是稳定探测——``DETACHED_PROCESS`` 环境下
对**自己**抛 ``OSError``（本机 3.12.13 实测 WinError 87），而旧实现
``except OSError: return False`` 把它读成「进程已死」⇒ M1 单实例锁静默失效
（两个 ``run`` 实例可并发写同一 SQLite）。

四条突变覆盖两个独立失效方向：

* **Windows 侧**（M1/M2）：撤掉 ``OpenProcess`` 探测 / 探测失败时不兜底；
* **POSIX 侧**（M3/M4）：把保守判活改回判死 / 把 ``ProcessLookupError``
  也误判成活（防「一切异常都判活」这种把 bug 挪位置的修法）。

⚠️ 定向用例是 ``tests/unit/test_pid_alive_windows_probe.py``，它会在**真实
DETACHED 子进程**里断言——在普通环境下断言 ``_pid_alive(self) is True`` 是恒真的，
旧实现在普通环境下本来就对。
"""

MUTATIONS = [
    (
        "L0-ALIVE-1",
        "scripts/m1_long_run.py",
        """    if sys.platform == "win32":
        probed = _pid_alive_win(pid)
        if probed is not None:
            return probed
        return True  # 探测判不出来 → 保守判活""",
        """    if False:
        probed = _pid_alive_win(pid)
        if probed is not None:
            return probed
        return True""",
        "tests/unit/test_pid_alive_windows_probe.py",
    ),
    (
        # 探测「判不出来」时改回判死：Windows 上 OpenProcess 返回未知错误码的路径
        "L0-ALIVE-2",
        "scripts/m1_long_run.py",
        """        probed = _pid_alive_win(pid)
        if probed is not None:
            return probed
        return True  # 探测判不出来 → 保守判活""",
        """        probed = _pid_alive_win(pid)
        if probed is not None:
            return probed
        return False  # MUT: 探测不出来就判死""",
        "tests/unit/test_pid_alive_windows_probe.py",
    ),
    (
        "L0-ALIVE-3",
        "scripts/m1_long_run.py",
        """    except OSError:
        return True  # 未知错误 → 同上，保守判活""",
        """    except OSError:
        return False  # MUT: 退回原来的判死""",
        "tests/unit/test_pid_alive_windows_probe.py",
    ),
    (
        # 「一切异常都判活」——把 bug 从误判死挪到误判活，不是修复
        "L0-ALIVE-4",
        "scripts/m1_long_run.py",
        """    except ProcessLookupError:
        return False
    except PermissionError:
        return True""",
        """    except ProcessLookupError:
        return True  # MUT: 连不存在的进程也判活
    except PermissionError:
        return True""",
        "tests/unit/test_pid_alive_windows_probe.py",
    ),
    # ------------------------------------------------------------------
    # 以下四条守 cmd_run 子进程端到端那三条用例（2026-10-16 补）。
    #
    # ⚠️ 精确记录**真实缺口**（2026-10-16 复核订正，原注释曾写成「force 零覆盖」
    # 是**夸大**）：`_acquire_lock` 的 force 分支一直有单元级覆盖
    # （`test_force_takes_over_alive_pid` 直接调它）。真正没有覆盖的是
    # **`--force-lock` 这条 CLI 管线的传递**——argparse 收下 → `args.force_lock`
    # → `cmd_run` → `_acquire_lock(force=…)`。原先两条 cmd_run 用例一条 skip、
    # 一条压根没传 flag ⇒ **这条管线一次都没被跑过**（把 `args.force_lock` 改成
    # 常量 False，历史测试全绿）。PLUMB-1 就是钉这一点的。
    #
    #   FORCE-1/2 → 接管判定本身（FORCE-1 另由既有单元测试捕获，此处求覆盖齐全）
    #   PLUMB-1  → flag 传递（**只有新加的 e2e 能抓到**）
    #   EXIT-1   → 互斥退出码确实是 5、且来自锁冲突那条分支
    # ------------------------------------------------------------------
    (
        "L0-PLUMB-1",
        "scripts/m1_long_run.py",
        "    force_lock: bool = args.force_lock",
        "    force_lock: bool = False  # MUT: flag 不往下传",
        "tests/unit/test_m1_lock.py",
    ),
    (
        "L0-FORCE-1",
        "scripts/m1_long_run.py",
        """        if alive and not force:
            return False, existing""",
        """        if alive:  # MUT: 忽略 force，--force-lock 彻底失效
            return False, existing""",
        "tests/unit/test_m1_lock.py",
    ),
    (
        # 方向相反的挪位：改成「一律拒」。同样要让 force 用例红，
        # 否则 FORCE-1 就可能被「恒假 force」这类实现蒙过去。
        "L0-FORCE-2",
        "scripts/m1_long_run.py",
        """        if alive and not force:
            return False, existing""",
        """        if alive or not force:  # MUT: 无条件拒，force 永远不生效
            return False, existing""",
        "tests/unit/test_m1_lock.py",
    ),
    (
        "L0-EXIT-1",
        "scripts/m1_long_run.py",
        """        print(f"[m1] 锁文件：{_lock_path(data_dir)}", flush=True)
        return RUN_LOCK_HELD_EXIT""",
        """        print(f"[m1] 锁文件：{_lock_path(data_dir)}", flush=True)
        return 0  # MUT: 互斥退出码写错""",
        "tests/unit/test_m1_lock.py",
    ),
]
