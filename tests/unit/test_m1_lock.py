"""scripts/m1_long_run.py 单实例锁单元测试。

覆盖（任务 DoD §1 / §5）：
1. ``_pid_alive``：当前 PID 活 / 不存在 PID 死 / 非法 PID 死。
2. ``_acquire_lock`` / ``_release_lock`` 基本流程（拿锁 / 释放 / 自动建目录）。
3. ``_acquire_lock`` 双拿：返回 ``(False, conflict)``，conflict 含 PID。
4. PID 已死 → 自动接管（覆盖写入 + previous_pid 记录）。
5. ``_force=True``：活 PID 也强制接管。
6. cmd_run 子进程端到端：活锁无 force ⇒ 5 / 活锁 + force ⇒ 接管并继续 / 无锁 ⇒ 正常拿锁。

所有 IO 落在 pytest tmp_path，不污染 data/m1_run。

⚠️ 2026-10-16 两处订正（此前两条 cmd_run 用例都是空壳，且一条被 skip）：

* ``test_cmd_run_exit_code_5_when_lock_held`` 原先 ``pytest.skip``，而 skip 理由
  指名 ``test_force_lock_cmd_run_progresses`` 为替代证据；
* 而后者注释里明写「**不带** --force-lock」且不写锁文件——**force 路径一次都没跑过**。

  即"替代证据"自己也是空的，等于这两条合起来什么都没验。现改为真跑子进程、
  真写活 PID 锁，并以退出码 5 ↔ 2 构成正反一对。
  （不搞"真双进程并发"是有意的：要证的分支是**判定**，不是并发正确性。）
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

# 把 scripts/ 加进 sys.path，便于直接 import m1_long_run
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import m1_long_run as m1  # noqa: E402

# ----------------------------------------------------------------------------
# _pid_alive
# ----------------------------------------------------------------------------


def test_pid_alive_current_process() -> None:
    assert m1._pid_alive(os.getpid()) is True


def test_pid_alive_nonexistent_returns_false() -> None:
    # 找一个极不可能被占用的 PID（接近 2^30）
    assert m1._pid_alive(2_000_000_000) is False


@pytest.mark.parametrize("bad", [0, -1, -100])
def test_pid_alive_invalid_returns_false(bad: int) -> None:
    assert m1._pid_alive(bad) is False


# ----------------------------------------------------------------------------
# _acquire_lock / _release_lock 基本行为
# ----------------------------------------------------------------------------


def test_acquire_and_release(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    acquired, conflict = m1._acquire_lock(data_dir, force=False)
    assert acquired is True
    assert conflict is None
    lock = m1._lock_path(data_dir)
    assert lock.exists()

    meta = json.loads(lock.read_text(encoding="utf-8"))
    assert meta["pid"] == os.getpid()
    assert "started_at" in meta
    assert "host" in meta

    m1._release_lock(data_dir)
    assert not lock.exists()


def test_acquire_creates_data_dir(tmp_path: Path) -> None:
    """未存在的 data_dir 应自动创建。"""
    data_dir = tmp_path / "fresh_dir"
    assert not data_dir.exists()
    acquired, _ = m1._acquire_lock(data_dir, force=False)
    assert acquired is True
    assert data_dir.is_dir()
    m1._release_lock(data_dir)


def test_release_missing_lock_is_idempotent(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    m1._release_lock(data_dir)  # 不抛
    m1._release_lock(data_dir)  # 再调也不抛
    assert not m1._lock_path(data_dir).exists()


# ----------------------------------------------------------------------------
# 冲突与接管
# ----------------------------------------------------------------------------


def test_double_acquire_returns_conflict(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    a1, c1 = m1._acquire_lock(data_dir, force=False)
    assert a1 is True and c1 is None
    try:
        a2, c2 = m1._acquire_lock(data_dir, force=False)
        assert a2 is False
        assert isinstance(c2, dict)
        assert c2.get("pid") == os.getpid()
        # 锁文件 PID 仍是当前进程（未接管）
        meta = json.loads(m1._lock_path(data_dir).read_text(encoding="utf-8"))
        assert meta["pid"] == os.getpid()
    finally:
        m1._release_lock(data_dir)


def test_take_over_dead_pid(tmp_path: Path) -> None:
    """锁文件中 PID 已死 → 自动接管，不需 force。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    ghost = 2_000_000_000
    m1._lock_path(data_dir).write_text(
        json.dumps(
            {"pid": ghost, "started_at": "1970-01-01T00:00:00Z", "host": "ghost"},
        ),
        encoding="utf-8",
    )
    acquired, conflict = m1._acquire_lock(data_dir, force=False)
    assert acquired is True
    assert conflict is None  # 已死分支直接接管
    meta = json.loads(m1._lock_path(data_dir).read_text(encoding="utf-8"))
    assert meta["pid"] == os.getpid()
    assert meta.get("force_took_over") is True
    assert meta.get("previous_pid") == ghost
    m1._release_lock(data_dir)


def test_force_takes_over_alive_pid(tmp_path: Path) -> None:
    """force=True：活 PID 也强制接管（构造活 PID = 当前进程 pid）。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    m1._lock_path(data_dir).write_text(
        json.dumps(
            {"pid": os.getpid(), "started_at": "now", "host": "self"},
        ),
        encoding="utf-8",
    )
    # 不带 force → 拒
    a0, c0 = m1._acquire_lock(data_dir, force=False)
    assert a0 is False
    assert isinstance(c0, dict)
    assert c0["pid"] == os.getpid()
    # 带 force → 接管
    a1, c1 = m1._acquire_lock(data_dir, force=True)
    assert a1 is True
    assert c1 is None
    meta = json.loads(m1._lock_path(data_dir).read_text(encoding="utf-8"))
    assert meta["pid"] == os.getpid()
    assert meta.get("force_took_over") is True
    assert meta.get("previous_pid") == os.getpid()
    m1._release_lock(data_dir)


# ----------------------------------------------------------------------------
# README 锁机制说明覆盖（确认常量与函数被暴露）
# ----------------------------------------------------------------------------


def test_public_constants_exposed() -> None:
    """确保 ``LOCK_FILE_NAME`` / ``RUN_LOCK_HELD_EXIT`` 已导出。"""
    assert m1.LOCK_FILE_NAME == ".run.lock"
    assert m1.RUN_LOCK_HELD_EXIT == 5


# ----------------------------------------------------------------------------
# 端到端：通过子进程跑 ``cmd_run``，预先写好"残留锁"模拟活实例
# ----------------------------------------------------------------------------
#
# 我们不去 spawn 两个真长跑进程（依赖服务端、依赖大量 mock），而是：
# 1) 在 tmp 里写一个"看起来活着的锁"（PID = 当前测试进程）；
# 2) 调子进程跑 ``cmd_run``，断言其直接退出码 5。
# 3) 加 ``--force-lock`` 再跑 → 应当越过锁；后续会因为缺 progress.json 退出码 2，
#    但只要越过锁这一步就是我们要证的。
#
# 这种用"假锁"代替"真子进程持锁"的版本更稳，覆盖语义也更聚焦：是否在拿锁
# 失败时返回 5、是否在 --force-lock 时跳过存活探测。

_RUN_HELPER = r'''
import sys
sys.path.insert(0, r"SCRIPTS_DIR_PH")
from pathlib import Path
import argparse
import m1_long_run as m1

# 复用真实 cmd_run 入口路径：让 argparse 处理参数，再走 cmd_run。
parser = m1._build_parser()
# argparse 顶层参数必须放在 subcommand 之前；--force-lock / --from / --to
# 属于 run 子命令。
args = parser.parse_args(
    ["--data-dir", "DATA_DIR_PH", "--port", "18100",
     "run", "--from", "1", "--to", "1"]
    + (["--force-lock"] if "--force-lock" in sys.argv else [])
)
try:
    rc = args.func(args)
except SystemExit as exc:
    rc = exc.code
print("CMD_RUN_RC=", rc, flush=True)
'''


def _spawn_cmd_run(data_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    helper = data_dir.parent / "_cmd_run_helper.py"
    script = (
        _RUN_HELPER
        .replace("SCRIPTS_DIR_PH", str(SCRIPTS_DIR).replace(chr(92), "/"))
        .replace("DATA_DIR_PH", str(data_dir).replace(chr(92), "/"))
    )
    helper.write_text(script, encoding="utf-8")
    # ⚠️ env + encoding **两侧都要钉**（坑区「子进程输出编码」条，本仓已中三次）：
    # 子进程 stdout 的**编码**跟随继承的环境变量，而 text=True 的**解码器**跟随
    # locale（本机 = cp936）。两者不一致时错位是**静默**的。只给一边照样中招。
    return subprocess.run(
        [sys.executable, str(helper), *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )


def _write_live_lock(data_dir: Path) -> Path:
    """写一份"看起来有活实例在跑"的锁。

    PID 取 **pytest 自己的 PID**：helper 是 pytest 的子进程，它探测这个 PID 时
    pytest 确实活着 ⇒ ``_pid_alive`` 必为 True ⇒ 走 ``alive and not force`` 分支。

    ⚠️ 这里不需要"真双进程并发"（那要真起两个 run 实例、依赖服务端与大量 mock）。
    要证的是**判定分支**：活锁 + 无 force ⇒ 拒（5）；活锁 + force ⇒ 接管（越过后退 2）。
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = m1._lock_path(data_dir)
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "started_at": "2026-01-01T00:00:00+00:00",
                "host": socket.gethostname(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return lock_path


def _rc_of(result: subprocess.CompletedProcess) -> int:
    assert "CMD_RUN_RC=" in result.stdout, (
        f"未拿到结果行：stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    line = next(ln for ln in result.stdout.splitlines() if ln.startswith("CMD_RUN_RC="))
    return int(line.split("=", 1)[1].strip() or "0")


def test_cmd_run_exit_code_5_when_lock_held(tmp_path: Path) -> None:
    """活 PID 锁 + **不带** --force-lock → 必须退出码 5，且不碰服务端。"""
    data_dir = tmp_path / "data"
    _write_live_lock(data_dir)

    result = _spawn_cmd_run(data_dir)
    rc = _rc_of(result)

    assert rc == m1.RUN_LOCK_HELD_EXIT, (
        f"活实例占锁时应当互斥退出（{m1.RUN_LOCK_HELD_EXIT}）；got {rc}"
    )
    # 反向判别：5 必须来自锁冲突那一条分支，而不是别处顺带返回了 5
    assert "检测到另一实例仍在运行" in result.stdout, (
        f"退出码 5 未伴随锁冲突提示 ⇒ 可能来自别的分支；stdout={result.stdout!r}"
    )


def test_force_lock_cmd_run_progresses(tmp_path: Path) -> None:
    """活 PID 锁 + **带** --force-lock → 应当**接管**锁越过互斥，再因缺 progress.json 退 2。

    ⚠️ 2026-10-16 修正：原实现注释里写着「不带 --force-lock」——**这条用例名为测
    force-lock，实际从未把该 flag 传给子进程**，且还不写锁文件，于是 force 路径
    一次都没被执行过。它与上面那条 skip 掉的用例是同一个空壳。
    """
    data_dir = tmp_path / "data"
    _write_live_lock(data_dir)

    result = _spawn_cmd_run(data_dir, "--force-lock")
    rc = _rc_of(result)

    # 关键：不能是 5。5 ⇒ force 没生效（退化成与上一条同一条路径，断言恒真）
    assert rc != m1.RUN_LOCK_HELD_EXIT, (
        f"--force-lock 未越过活锁（仍返回 {rc}）⇒ 强制接管失效；stdout={result.stdout!r}"
    )
    # 越过锁后落到 progress.json 缺失 ⇒ 2
    assert rc == 2, f"应有 progress.json 错误退出码 2；got {rc}; stderr={result.stderr!r}"
    # 接管动作本身要留痕（否则「force 参数传到了但没生效」也能过上面两条）
    assert "（force 接管）" in result.stdout, (
        f"未见 force 接管标记，接管可能未真正发生；stdout={result.stdout!r}"
    )


def test_cmd_run_without_any_lock_takes_the_lock_normally(tmp_path: Path) -> None:
    """对照组：**没有**任何锁时，走的是"正常拿锁"路径（不是 force 接管）。

    与上一条成对：两条若都返回 2，单看任一条都判别不出 force 是否生效。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    result = _spawn_cmd_run(data_dir)
    rc = _rc_of(result)

    assert rc == 2, f"无锁时应正常拿锁后因缺 progress.json 退 2；got {rc}"
    assert "（force 接管）" not in result.stdout, (
        f"无锁时不应出现 force 接管标记；stdout={result.stdout!r}"
    )
