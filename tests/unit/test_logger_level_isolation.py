"""日志级别**污染**的顺序鲁棒性判别（2026-10-23 task-12 X-1）。

现象
----
仓内 ``packages.core.logging_config.configure_logging(level)`` 会
``root.setLevel(level)`` 且**从不复原**（生产代码，本任务只读）⇒ 任何一次
``create_app(Settings(log_level="ERROR"))`` 都把**根 logger 级别**永久压成 ERROR。

而**具名 logger**（``logging.getLogger("novelos.domain.volume.service")`` 之类）
若**没有自己的级别**（``level == NOTSET``）⇒ 其**有效级别继承根 logger** ⇒
WARNING 记录在**到达任何 handler 之前**就被 logger 级别滤掉 ⇒ 挂在它上面的
捕获器看到 **0 条** ⇒ 「应恰好留痕 1 条」类断言恒红。

⇒ 于是**测试的执行顺序**决定门禁红绿：``test_openapi_contract.py`` 先跑（它
``create_app(log_level="ERROR")``）⇒ 后面任何断言「恰好 N 条日志」的用例全红。

实测（修前）：``pytest tests/api/test_openapi_contract.py
tests/unit/test_quality_domain_swallow_triage.py -q -p no:randomly``
⇒ **5 failed / 8 passed**。

本文件钉的是**顺序鲁棒性**
------------------------
两条判别线：

1. **机制线（进程内，快）**：把根级别压成 ERROR 之后，**直接跑**分诊文件里那
   些「恰好 N 条日志」的断言 ⇒ 它们必须仍然绿。
   ⇒ 判别点：分诊文件的 :class:`_LogCapture` 若**不显式** ``setLevel(DEBUG)``
   ⇒ 本条红。
2. **端到端线（子进程，权威）**：以子进程跑上面那条**两文件命令** ⇒ 必须 ``rc == 0``。
   ⇒ 这条**直接**就是 Lead 复现出来的那条命令 ⇒ 它红了就等于门禁红了。

为什么治标在测试侧
------------------
根因在 ``configure_logging`` 不复原根级别（**生产代码**，本任务**只读**）⇒
治标办法是让**捕获器把级别钉在自己身上**（``setLevel(DEBUG)``），并在退出时复原。
生产侧的修法（``configure_logging`` 复原根级别 / ``create_app`` 用完还原）另立项。

⚠️ **纪律**：子进程必须 env（``PYTHONIOENCODING`` / ``PYTHONUTF8``）+ ``encoding=``
**两侧同时钉**（AGENTS 坑区「子进程输出编码：父进程 ``text=True`` 缺 ``encoding=``
⇒ 父子两侧静默错位」）——本机 Windows 默认 cp936。
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"


# ---------------------------------------------------------------------------
# 机制线（进程内）
# ---------------------------------------------------------------------------


def _pollute_root_logger_to_error() -> None:
    """把**根 logger** 级别压成 ERROR（复刻 ``create_app(log_level="ERROR")`` 的效果）。

    走的是生产入口 ``configure_logging``（**不复制**它的实现）⇒ 本测试与真实
    污染路径同源；若将来生产侧改成复原根级别，本测试应当**变红**（⇒ 提醒
    「测试侧的钉死已多余，可以撤」）。
    """
    from packages.core.logging_config import configure_logging

    configure_logging("ERROR")
    assert logging.getLogger().level == logging.ERROR, "污染前提不成立：根级别不是 ERROR"


@pytest.fixture
def polluted_root_logger():
    """污染根级别 ⇒ 用例结束后**复原**（不污染后续用例）。"""
    root = logging.getLogger()
    prev = root.level
    prev_handlers = list(root.handlers)
    _pollute_root_logger_to_error()
    try:
        yield root
    finally:
        root.setLevel(prev)
        for h in list(root.handlers):
            if h not in prev_handlers:
                root.removeHandler(h)


def test_triage_assertions_survive_a_polluted_root_logger(polluted_root_logger):
    """机制线：根级别被压成 ERROR 之后，分诊文件的断言**仍然绿**。

    ⇒ 判别点 = 分诊文件的 :class:`_LogCapture` 是否显式 ``setLevel(DEBUG)``。
    撤掉那个 ``setLevel`` ⇒ 本条红（分诊断言里的
    ``assert len(cap.records) == 1`` 会看到 0 条）。
    """
    # 直接跑**分诊文件自己的断言**（不是复制一份逻辑）⇒ 修的正是它们。
    from tests.unit import test_quality_domain_swallow_triage as triage

    triage.test_dirty_character_aliases_are_logged_and_still_degrade_to_empty()
    triage.test_dirty_world_aliases_are_logged_and_still_degrade_to_empty()


def test_named_logger_inherits_root_level_when_it_has_none(polluted_root_logger):
    """**机理钉**：具名 logger 没有自己的级别时**有效级别继承根 logger**。

    ⇒ 这条把「为什么必须显式 setLevel」写成**可复算的事实**，而不是只写在
    注释里 ⇒ 将来有人质疑「显式设级别是不是多余的」，本条会给出答案。

    ⚠️ 口径：``logger.level`` 是**它自己**设的级别（``NOTSET``），
    ``logger.getEffectiveLevel()`` 才是**生效**级别（继承来的）。
    """
    named = logging.getLogger("novelos.domain.volume.service")
    named.setLevel(logging.NOTSET)
    assert named.level == logging.NOTSET, "前提：它没有自己的级别"
    assert named.getEffectiveLevel() == logging.ERROR, (
        "有效级别应继承被污染的根级别（ERROR）"
        "⇒ 它的 WARNING 记录在到达 handler 之前就被滤掉"
    )


def test_log_capture_restores_the_logger_level():
    """**复原钉**：捕获器退出后必须把 logger 级别**还回去**（不留副作用）。"""
    from tests.unit.test_quality_domain_swallow_triage import _LogCapture

    named = logging.getLogger("novelos.domain.volume.service")
    original = named.level
    with _LogCapture(named) as cap:
        assert named.level == logging.DEBUG, "捕获期间必须把自己的级别钉成 DEBUG"
        named.warning("probe-message")
    assert named.level == original, "捕获结束后必须复原原级别"
    assert cap.messages() == ["probe-message"]


# ---------------------------------------------------------------------------
# 端到端线（子进程，权威判据 = Lead 复现的那条命令）
# ---------------------------------------------------------------------------

#: Lead 复现出来的那条命令（原样）：先建 app（压根级别）再跑分诊断言。
REPRO_COMMAND = [
    "tests/api/test_openapi_contract.py",
    "tests/unit/test_quality_domain_swallow_triage.py",
]


@pytest.mark.skipif(
    not VENV_PYTHON.exists(), reason="找不到 .venv 解释器（本仓闸门固定用 venv）"
)
def test_openapi_contract_then_triage_is_green_in_a_fresh_process(tmp_path: Path):
    """端到端线：**先**跑 ``test_openapi_contract.py``（它建 app ⇒ 污染根级别）
    **再**跑分诊断言 ⇒ 必须全绿。

    ⚠️ 这是**门禁本身的顺序**：两文件同进程、``test_openapi_contract`` 在前。
    修前实测 **5 failed / 8 passed**（见模块 docstring）。

    ⚠️ **为什么要子进程**：在**本进程**里跑它会污染当前进程的根级别 ⇒ 污染
    **后续所有用例** ⇒ 用子进程隔离（且子进程天然是「干净起点」，与门禁真实
    形态一致）。
    """
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    proc = subprocess.run(
        [
            str(VENV_PYTHON),
            "-m",
            "pytest",
            *REPRO_COMMAND,
            "-q",
            "-p",
            "no:randomly",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    assert proc.returncode == 0, (
        "先跑 test_openapi_contract.py（压根级别）再跑分诊断言 ⇒ 必须全绿，"
        f"实际 rc={proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout[-4000:]}\n"
        f"--- stderr ---\n{proc.stderr[-2000:]}"
    )
