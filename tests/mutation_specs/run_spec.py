"""逐组突变验证的执行器（2026-10-15 批次（六）新增）。

为什么要有它
------------
本仓的突变验证以前只有一份共享脚本 ``scripts/mutation_harness.py``。本轮要**并发**
补 67 个端点的 response_model 契约，每个开发成员都要自验自己的测试**是否真能判别**
——而自验必须能改动源码跑测试，也就是必须能往那张表里加条目。多人共写一个文件
必然撞车（铁律 4：并行开发按文件路径互斥）。

于是把**数据**与**执行**拆开：

* 数据 = ``tests/mutation_specs/g*.py``，每个成员独占一个文件（纯 ``MUTATIONS`` 列表）；
* 执行 = 本文件，**所有人共用、所有人都不许改**。

用法::

    # 单规格（开发期自验，最常用）
    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py tests/mutation_specs/g1_world.py

    # 只跑「本批改动命中的」突变（**每批收尾的默认口径**，见下）
    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py --changed

    # 只读预检（秒级，不做任何突变）
    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py --all --precheck

    # 全量（发版前 / 周期性；**串行**，≈ 53 分钟）
    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py --all

闸门的三个档位（2026-10-22 批次二十一修订，**这是纪律变更**）
------------------------------------------------------------------
原纪律是「每批收尾都跑一次全量」，370 条 × 每条新起一个 pytest 进程 ≈ **53 分钟**，
且随规格只增不减（2026-10-15 起 21 → 120 → 370，**线性膨胀**）⇒ 到 1000 条就会变成
两三个小时。**这不是「测试慢」，是「把 370 次进程启动重复付了一遍」**：每条突变
的 pytest 固定开销（解释器 + 应用 import + collect ≈ 4–6s）远大于被测断言本身。

| 档位 | 命令 | 目标耗时 | 频率 |
|---|---|---|---|
| **只读预检** | `--all --precheck` | **秒级** | 每次改动后（可进 CI） |
| **增量突变** | `--changed`（默认取 `git diff HEAD --name-only`） | **分钟级** | **每批收尾（新默认）** |
| **全量突变** | `--all`（**串行**） | ≈ 53 分钟 | 发版前 / 每周 |

⚠️ **`--jobs N`（并发档位）**已降级为「**降噪参考**」，**不得作为闸门结论**：
并发分片只保证「同一目标**源文件**」不并发，**不保证**「被测文件 `import` 的**其它**
源文件」不被另一个 worker 临时改坏 ⇒ 会产出**假 CAUGHT**、**判定不可复现**、
**归因错配**三类失真（2026-10-23 对抗审查 B 在隔离副本上实测）。⇒ 并发档位的
**退出码恒为 3**（除非显式 `--accept-concurrent` 承认「我只要看个大概」）⇒
CI / 闸门脚本不可能把它误当成绿的结论。

⚠️ **「退出码恒为 3」不许有旁路**（2026-10-23 对抗审查 B 第三轮 minor#4，Lead 复算）：
原先只有「跑完突变后的汇总」那条路守 exit 3 ⇒ **任何早退**都能绕开——实测
``--all --changed --jobs 2`` 且改动集为空 ⇒ 早退直接 ``return 0`` ⇒
**退出码 0、无「档位:」行、无并发警告** ⇒ 闸门脚本会把「并发档位跑出来的 0」
当成绿灯。⇒ 现由 :func:`_tier_exit` 统一收口：**一切非用法错误（≠2）的结束路径**
（改动集为空 / 只读预检结束 / 跑完全部突变）都经它；:func:`_tier_lines` 保证
**每条结束路径都打印「档位:」行** ⇒ 日志永远看得出这次是哪个档位。
（CI 侧 ``ci.yml`` 跑的是串行 ``--all --precheck`` ⇒ 不受影响。）

为什么可以这样拆：全量跑的**真正目的**有两个 —— ①「本批改动没打断既有锚点」②
「既有的 370 条判别力还在」。① 是**纯只读计算**（`count(anchor) == 1` + 测试文件存在 +
replacement 有语法），秒级可做完，而且**比跑全量更灵敏**（锚点一断立刻报，不必等到
某条突变 `[ABORT]`）。② 与「本批改了什么」无关，是**周期性健康检查**，不需要每批付
53 分钟。**只有 ① 与「本批新增/修改的规格」必须每批验**，而它们天然只涉及本批改过的
文件 ⇒ `--changed` 应当覆盖。

⚠️ **`--changed` 的选中集是并集（2026-10-23 批次二十九 task-24 修的真实门禁盲区）**：

1. **目标源文件** ∈ 改动集（原口径）；
2. **规格文件本身** ∈ 改动集（新增/修改的规格一律实跑）。

反例实证：``l16_structured_output.py`` 于批次二十五新建，但其目标
``structured_output.py`` 之后三批未改 ⇒ **旧口径下三批门禁都没跑过它**，直到批次
二十八由一条 `[SURVIVED]` 暴露。⇒ 只筛目标文件是**漏筛**，不是省时。

⚠️ 但**不许**退化成「规格文件一改就全跑 428 条」：并集只选**被改动规格文件自己的突变**
（外加目标文件命中的突变），其它规格一条都不牵连——否则这个省时开关就废了。判别：
`tests/unit/test_mutation_specs_runner.py::test_changed_spec_file_does_not_pull_in_unrelated_specs`。

覆盖清单必须可见：`--changed` 打印「目标文件命中 N 条 / 规格文件命中 M 条 ⇒ 合计 U 条」
（与 `--all` 必打印发现的规格文件数同一条纪律：看不见执行域的闸门等于没有闸门）。

⚠️ **不要**为了省时间把多条突变塞进同一个 pytest 进程（例如 `pytest.main()` 复跑）：
已 import 的模块会缓存旧字节 ⇒ **突变根本不生效** ⇒ 静默假绿。每条突变必须落在
**独立的新进程**里，这是本执行器不可让渡的语义。

四条硬断言（从 ``scripts/mutation_harness.py`` 原样继承，**不许删**）
----------------------------------------------------------------
坑区已登记同一族事故四次，每次都是「非零退出码同时承载『跑过了且红』与『压根没跑』
两种语义」。故：

1. **绝对路径**起子进程——cwd 与本文件所在目录不保证一致，相对路径会 ``FileNotFoundError``，
   而它与「用例红」在退出码上完全同形；
2. **启动失败与测试失败分桶**：``LaunchFailure`` 归 ``INVALID``，绝不落进
   survived/caught 任何一个桶；
3. **必须有「跑过了」的证据**：输出里找不到 runner 的执行痕迹（vitest 的 ``Test Files`` /
   pytest 的 `` passed``）时同样判 INVALID——被上游杀掉、收集阶段就崩了，与断言失败同形；
4. **锚点未命中即判脚本自身缺陷、强制中止**，不许记成「跳过」。若记成跳过，本轮会报成
   「N-1 条存活」而实际是 N-2 条跑了。

⚠️ **本执行器会临时改源码** ⇒ 禁与任何 pytest 并发跑（含 ``-n 4``）。
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.util
import itertools
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

# ⚠️ 本机 Windows stdout 默认 cp936（坑区「Windows 默认 cp936 吞中文」家族）。
# **两侧都要钉**：本进程 stdout 重配 + 子进程 env——只给一边照样乱码。
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"
#: 子进程用的 Python 解释器。
#:
#: **Windows 优先用项目 venv**（``.venv/Scripts/python.exe``）——本仓铁律：全量 pytest
#: 一律用 venv 解释器（系统 python 的 editable 元数据缺失，会把版本读成 ``dev``，
#: 误判成 schema 漂移）。**但 venv 路径是 Windows 形状** ⇒ 在 Linux CI 上不存在 ⇒
#: 子进程会 ``FileNotFoundError`` ⇒ 与「用例红」在退出码上完全同形（坑区已登记四次：
#: 非零退出码同时承载「跑过了且红」与「压根没跑」）。
#:
#: ⇒ **venv 存在就用它，不存在就退回当前解释器**（CI 里 ``sys.executable`` 就是
#: ``pip install -e ".[dev]"`` 装进的那个环境，依赖齐全）。
_VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
PY = str(_VENV_PY) if _VENV_PY.exists() else sys.executable
VITEST = WEB / "node_modules" / ".bin" / ("vitest.cmd" if sys.platform == "win32" else "vitest")
SPEC_DIR = ROOT / "tests" / "mutation_specs"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

#: 每条突变的 pytest ``--basetemp`` 根目录（每条突变再各带一个唯一子目录）。
#:
#: **为什么必须显式给**（2026-10-23 实测，本机专属环境问题）：
#: pytest 的默认 basetemp 是 ``%TEMP%/pytest-of-<user>``。本机该目录一旦被创建，
#: **连 owner 也拒绝访问**（``PermissionError: [WinError 5]``）⇒ 用到 ``tmp_path``
#: 的用例在**准备阶段**就 PermissionError ⇒ 全部 **ERROR**（不是 FAIL）⇒
#: 突变执行器读到的是「跑挂了」而不是「被抓住了」。
#:
#: 后果实测（本轮 40 条）：① 40 条突变被误判成 INVALID（**量具没测到被测物**）；
#: ② 更糟的是**假阳性**——目标用例 ERROR，但同文件里另一个用例 ``passed``，
#: 执行器看到 ``" passed"`` + 退出码 1 ⇒ 判成 ``[CAUGHT]`` ⇒ **把「没抓住」报成抓住了**。
#:
#: ⇒ 这不是被测代码的缺陷，是**量具的环境前提没满足**。修法是给每条突变一个
#: **独立且可写**的 basetemp（不能用同一个：pytest 启动时会清空 basetemp 目录，
#: 并发跑会互相踩）。
_BASETEMP_ROOT = pathlib.Path(tempfile.gettempdir()) / "novelos-mutbasetemp"
_basetemp_counter = itertools.count()


def _basetemp_for(mid: str) -> pathlib.Path:
    """给一条突变分配**唯一**的 basetemp 目录（并行跑也不撞）。

    ⚠️ 根目录必须**先建出来**：pytest 内部用的是 ``Path.mkdir(mode=0o700)``（**不带
    parents**）⇒ 父目录不存在时直接 ``FileNotFoundError``，表现为「10 errors」
    （又一层假象：看起来像用例挂了，实际是量具自己没准备好路径）。
    """
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", mid)[:60]
    _BASETEMP_ROOT.mkdir(parents=True, exist_ok=True)
    return _BASETEMP_ROOT / f"{safe}-{os.getpid()}-{next(_basetemp_counter)}"


#: 并发档位的警告文案（**闸门结论必须用串行**）。
#:
#: ⚠️ 为什么有它（2026-10-23 对抗审查 B 实证，Lead 复算）：
#: ``--jobs N`` 的分片只保证「**同一目标源文件**」不并发，**不保证**「被测文件
#: ``import`` 的**其它**源文件」不被另一个 worker 临时改坏 ⇒ 产出三类失真：
#:
#: 1. **假 CAUGHT**：一条「只改注释、单独跑必然 SURVIVED」的突变，在并发时被**另一条
#:    突变临时改坏的源文件**污染——该源文件被本条的目标测试文件 ``import`` 并执行
#:    ⇒ 被报成 ``[CAUGHT]``（实测 8/8 轮）。
#:    ⚠️ **与两条突变是否共用同一目标测试文件无关**：实测**测试文件不同**照样被污染
#:    ⇒ 「按测试文件分片」修不好这个洞。
#: 2. **判定不可复现**：同一输入、同一代码，一次报 ``SURVIVED``、一次报
#:    ``CAUGHT``（时序相关 ⇒ 结论不可重跑复现）；
#: 3. **归因错配**：证据行报告的失败用例**不是该突变能影响的那条**
#:    （⇒ 复核者「确认红的是预期那条」这一步拿到的是错的证据）。
#:
#: ⇒ **并发档位只能作「降噪参考」，其结果不得作为闸门结论。**
#: ⇒ 闸门一律**串行**：``jobs`` 默认就是 1（不传 ``--jobs`` 即串行）；
#:    增量档位（每批 20~40 条）串行只要 2~4 分钟 ⇒ 没有必要为省这点时间
#:    拿「结论不可信」去换。
#: ⇒ 待排期（未做）：「每 worker 一份独立树副本」的隔离方案 —— 已实测可行
#:    （本仓 editable 是老式 ``.pth`` ⇒ ``PYTHONPATH`` 优先级更高 ⇒ 副本能接管
#:    ``import packages.*``；拷 ``packages`` 仅 0.42s），可保留全量档位的速度，
#:    但需先解决副本内的 ``data/`` 与 ``conftest`` 路径依赖。
PARALLEL_WARNING_KEYWORDS = ("假 CAUGHT", "判定不可复现", "归因错配", "不得作为闸门结论")


def _parallel_warning(jobs: int) -> str:
    """并发档位的警告文案（抽成函数 ⇒ 仓内用例可直接断言其内容）。"""
    return (
        f"⚠️ [并发档位 · {jobs} 路 · **结果不得作为闸门结论**]\n"
        "   并发分片只保证「同一目标源文件」不并发，**不保证**「被测文件 import 的"
        "其它源文件」不被另一个 worker 临时改坏 ⇒ 已实测三类失真：\n"
        "     ① **假 CAUGHT**：只改注释、单独跑必然 SURVIVED 的突变，被另一条突变"
        "临时改坏的源文件污染 ⇒ 并发时被报成 [CAUGHT]（实测 8/8 轮；**测试文件不同"
        "照样中招** ⇒ 按测试文件分片修不好）；\n"
        "     ② **判定不可复现**：同一输入一次 SURVIVED、一次 CAUGHT（时序相关）；\n"
        "     ③ **归因错配**：证据行报告的失败用例不是该突变能影响的那条。\n"
        "   ⇒ 并发结果只能作「降噪参考」；**闸门一律串行**（不传 --jobs 即串行）。\n"
        "   ⇒ 本次退出码恒为 **3**（除非显式加 --accept-concurrent 承认「只看个大概」）"
        " ⇒ 闸门脚本不可能把它当成绿的结论。"
    )


def _tier_lines(jobs: int, accept_concurrent: bool) -> list[str]:
    """档位说明行（**每条非用法错误的结束路径都要打** ⇒ 日志永远看得出这次是哪个档位）。

    ⚠️ 为什么要抽成函数：原先这段只出现在「跑完突变后的汇总」里 ⇒ **任何早退**
    （改动集为空 / 只读预检结束）都打不出档位 ⇒ 只看日志的人无法判断这次跑的是
    哪个档位（2026-10-23 对抗审查 B 第三轮 minor#4）。
    """
    if jobs <= 1:
        lines = ["档位: 串行（结论可信 ⇒ 可作闸门结论）"]
        if accept_concurrent:
            lines.append("（--accept-concurrent 在串行档位无意义，已忽略）")
        return lines
    lines = [f"档位: 并发（{jobs} 路）"]
    if accept_concurrent:
        lines.append("  ⇒ 已用 --accept-concurrent 承认「本结果只作降噪参考」")
    else:
        lines.append(
            "  ⇒ **本结果不可作闸门结论**（并发会造假 CAUGHT / 判定不可复现 / "
            "归因错配）⇒ 退出码恒为 3"
        )
    return lines


def _tier_exit(base_code: int, jobs: int, accept_concurrent: bool) -> int:
    """并发档位的**统一退出码出口**：未经 ``--accept-concurrent`` 承认 ⇒ 恒为 **3**。

    ⚠️ 为什么必须「所有路径都过这里」（2026-10-23 对抗审查 B 第三轮 minor#4）：
    只守住「跑完突变」那一条路 ⇒ 任何**早退**都能绕开契约（实测：
    ``--all --changed --jobs 2`` 且改动集为空 ⇒ 早退直接 ``return 0`` ⇒
    **退出码 0、无档位行、无并发警告**）⇒ 闸门脚本 / CI 就会把「并发档位跑出来的 0」
    当成「闸门通过」的绿灯。
    ⇒ 用法错误（退出码 2）**不走这里**（它发生在任何执行之前，更精确）；
    其余**一切**结束路径都经由本函数。
    """
    if jobs > 1 and not accept_concurrent:
        return 3
    return base_code


class LaunchFailure(RuntimeError):
    """测试命令**没能启动**——与「用例红」是两回事，必须分开报。"""


def load_spec(path: pathlib.Path) -> list[tuple[str, str, str, str, str]]:
    """按路径加载规格文件（不要求 ``tests`` 是包——它没有 ``__init__.py``）。"""
    spec = importlib.util.spec_from_file_location(f"_mspec_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise LaunchFailure(f"规格文件加载失败: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.MUTATIONS)


def _run(cmd, cwd) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
    except FileNotFoundError as exc:  # 命令不存在 / 路径错
        raise LaunchFailure(f"{cmd[0]} 启动失败: {exc}") from exc
    global _last_exit_code
    _last_exit_code = r.returncode
    return r.returncode == 0, (r.stdout + r.stderr)


#: 最近一次 ``_run`` 拿到的退出码（供 ``_assert_tests_ran`` 判「真的跑过了」）。
#: 非零退出码在本仓已登记过五次「同时承载『跑过了且红』与『压根没跑』」的事故，
#: 故这里显式留存，而不是只看输出文本。
_last_exit_code: int | None = None


def _assert_tests_ran(out: str, runner: str) -> None:
    """必须有「跑过了」的证据，否则一律按启动/收集失败处理。

    ⚠️ 2026-10-16 补第五条硬断言：**退出码要与「有测试真的跑了」一致**。
    ------------------------------------------------------------------
    原实现只认输出里有没有 marker，而 marker 表里有一条 ``"no tests ran"``——
    那正是 pytest 在**测试文件不存在**时的输出，且此时退出码是 **4**（不是 0），
    于是一个**根本不存在的测试文件**会被放行、被读成「用例红」、
    记成一条**假的 ``[CAUGHT]``**。实测（本机 venv）：
        文件不存在   → ``no tests ran``          → exit 4
        ``-k`` 全不匹配 → ``N deselected``        → exit 5
        正常跑完     → ``N passed``              → exit 0
    exit 4/5 都**不是**「断言失败」，必须归 INVALID。

    顺带把过期的 ``"no tests ran"`` 从 marker 表里去掉——它不是「跑过了」的证据，
    恰恰相反。
    """
    # pytest：4=用法/收集错误、5=零测试被收集、2=中断、1=有失败；0 才是真跑完。
    # 1 是「跑过了且红」⇒ 允许通过检查；其余非零一律 INVALID。
    if runner == "pytest" and _last_exit_code not in (0, 1):
        raise LaunchFailure(
            f"pytest 退出码 {_last_exit_code}（4=收集错误/文件不存在，5=零测试被收集，"
            f"2=中断）——都不是「跑过了且红」，按无效处理。输出尾部：{out[-300:]}"
        )
    if runner == "vitest":
        markers = ("Test Files", "Tests  ")
    else:
        markers = (" passed", " failed")
    if not any(m in out for m in markers):
        raise LaunchFailure(f"输出里没有任何测试执行痕迹（可能被上游杀掉）：{out[-400:]}")


def summary_line(out: str) -> str:
    """第一条能说明「红了哪条」的**非空**行（去 ANSI）。

    ⚠️ 必须喂**完整**输出——本函数原先读到的是截断窗，而失败条数多时 vitest 会打整段
    DOM 调试输出，把顶部的摘要行顶出窗口 ⇒ 摘要退化成空串。**空摘要不是无害的**：
    复核者「确认红的是预期那条」这一步会无法执行——量具恰在最需要被复核的场合交白卷。
    """
    lines = [_ANSI.sub("", ln).strip() for ln in out.splitlines()]
    # pytest 的 short summary 行以 "FAILED <test_id>" 开头，是**唯一**带用例 id 的
    # 失败行——优先于 `===== FAILURES =====` 横幅（横幅只说「有失败」，不说哪条）。
    for ln in lines:
        if ln.startswith("FAILED "):
            return ln[:150]
    for ln in lines:
        if ln and ("FAIL" in ln or "failed" in ln):
            return ln[:150]
    for ln in reversed(lines):  # 兜底：取最后一条非空行
        if ln:
            return ln[:150]
    return "(输出为空，无法提取摘要——本条须手工复核)"


def _sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_one(
    mid: str,
    rel: str,
    anchor: str,
    replacement: str,
    test_file: str,
    *,
    fail_fast: bool = False,
) -> str:
    """跑一条突变，返回 CAUGHT / SURVIVED / ABORT / INVALID 之一。

    ``fail_fast``（``--fail-fast``）：给 pytest 加 ``-x`` —— 首个用例失败即停。
    本执行器只需要「退出码是否为 0」来判 CAUGHT，不关心红了几条 ⇒ 通常能省掉
    目标用例之后那些用例的执行时间。
    ⚠️ 边界：若突变导致的是 **error**（而非 failure），``-x`` 下输出可能既无
    ``" passed"`` 也无 ``" failed"`` ⇒ 会被 ``_assert_tests_ran`` 判成 ``INVALID``。
    那是**响亮失败**（执行器最后 exit 1），不会静默通过；但默认仍**关闭** ``-x``，
    由调用方显式开启。
    """
    path = ROOT / rel
    try:
        original = path.read_text(encoding="utf-8")
    except OSError as exc:
        # ⚠️ 目标文件读不出来 ⇒ **突变根本没被执行**（文件被删 / 改名 / 不是文件）
        # ⇒ 不是「没被抓住」，也不是「用例红了」⇒ 归 INVALID（响亮失败）。
        # 2026-10-23 对抗审查 B 的 F-8：原先这里直接抛 `FileNotFoundError`
        # ⇒ **整轮崩溃**（后面所有规格一条都没跑，退出码也不是可判读的语义）。
        return f"[INVALID]  {mid}: 目标文件读不出来：{rel}（{exc}）——突变未被执行"
    before = _sha(path)

    hits = original.count(anchor)
    if hits != 1:
        return f"[ABORT] {mid}: 锚点命中 {hits} 次（要求恰好 1），文件={rel}"

    bak = path.with_suffix(path.suffix + ".mutbak")
    shutil.copy2(path, bak)
    try:
        path.write_text(original.replace(anchor, replacement), encoding="utf-8")
        if _sha(path) == before:
            return f"[ABORT] {mid}: 写入后 SHA256 未变——**变异没生效**（等于没改）"

        is_py = test_file.startswith("tests/")
        basetemp: pathlib.Path | None = None
        try:
            if is_py:
                basetemp = _basetemp_for(mid)
                py_args = [
                    PY, "-m", "pytest", test_file, "-q", "-p", "no:cacheprovider",
                    f"--basetemp={basetemp}",
                ]
                if fail_fast:
                    py_args.append("-x")
                ok, out = _run(py_args, ROOT)
                runner = "pytest"
            else:
                ok, out = _run([str(VITEST), "run", test_file], WEB)
                runner = "vitest"
            _assert_tests_ran(out, runner)
            if runner == "pytest" and _last_exit_code == 1 and " failed" not in out:
                # **用例 ERROR ≠ 用例 FAILED**（2026-10-23 实测的假阳性来源）：
                # fixture / setup 阶段出错时 pytest 摘要行是「N errors」而非「N failed」，
                # 退出码同样是 1 ⇒ 旧逻辑只看到同文件里另一个用例的 `` passed``
                # 就会把它判成 ``[CAUGHT]``——**把「没抓住」报成抓住了**。
                # 这里单独拦：没有真正的 failed ⇒ 突变没被断言抓住 ⇒ 归 INVALID（响亮失败）。
                return (
                    f"[INVALID]  {mid}: 用例是 ERROR 而非 FAIL（摘要里没有 ' failed'）"
                    f"——突变把测试跑挂了，不是被断言抓住。输出尾部：{out[-300:]}"
                )
        except LaunchFailure as exc:
            return f"[INVALID]  {mid}: {exc}"
        finally:
            if basetemp is not None:
                shutil.rmtree(basetemp, ignore_errors=True)

        if ok:
            return f"[SURVIVED] {mid}: 突变后用例仍全绿 ⇒ 该测试测不到这个机制"
        return f"[CAUGHT]   {mid}: {summary_line(out)}"
    finally:
        shutil.copy2(bak, path)
        bak.unlink(missing_ok=True)
        status = "还原校验 OK" if _sha(path) == before else "!!! 还原校验失败 !!!"
        print(f"           还原 {rel}: {status} ({_sha(path)[:16]})", flush=True)


def discover_specs() -> list[pathlib.Path]:
    """``--all`` 的规格发现。

    ⚠️ **2026-10-15 订正**：原实现是 ``glob("g*.py")``——只匹配首批 9 个成员的
    ``g1``~``g9``。后来主会话自办的 ``l0_*`` 与第二轮 4 个成员的 ``l1``~``l4``
    不匹配该模式，于是 ``--all`` **静默地只跑了 120/175 条**，却照样打印
    「突变存活: 无」+ exit 0。

    这就是本仓坑区那条的老形状：**量具的覆盖面被静默缩小，而它报告成量过了**
    （同「看守自己被悄悄缩小了执行域」）。教训是通用的——
    **``--all`` 的语义是「全部」，就不能靠一条前缀规则去猜**。

    改为按「含 ``MUTATIONS`` 列表、且不是执行器自身」发现，并对
    「g/l 之外的命名」也一视同仁；同时**打印发现的文件数**，
    让覆盖面本身在输出里可见（不能再靠「数字看着对」来判断）。
    """
    out = []
    for p in sorted(SPEC_DIR.glob("*.py")):
        if p.name in {"run_spec.py", "validate_specs.py"} or p.name.startswith("_"):
            continue
        if "MUTATIONS" not in p.read_text(encoding="utf-8"):
            continue
        out.append(p)
    return out


def _resolve_spec(arg: str) -> pathlib.Path:
    """把命令行给的规格名解析成绝对路径——**裸文件名按 SPEC_DIR 解析**。

    ⚠️ 2026-10-16 修的自身缺陷：原先直接 ``Path(arg)``，于是 ``run_spec.py
    l0_pid_alive.py`` 会按 **CWD** 解析、找不到文件，且炸出一整段 traceback。
    两个害处：① ``validate_specs.py`` 接受裸名、``run_spec.py`` 不接受——**同一套
    规格名在两个工具里语义不同**，而错误消息里出现的恰恰是裸名；
    ② traceback 既没进 INVALID 桶也没告诉人正确写法，是坑区
    「量具自身缺陷伪装成执行结果」的同款形状（只不过这次伪装得比较明显）。
    """
    p = pathlib.Path(arg)
    if p.is_file():
        return p.resolve()
    cand = SPEC_DIR / p.name
    if cand.is_file():
        return cand
    raise LaunchFailure(
        f"规格文件不存在：{arg!r}（已试过 {p.resolve()} 与 {cand}）"
    )


def _git_changed_paths(rev: str) -> set[str]:
    """``--changed`` 的「改动文件」口径：已跟踪 diff + 未跟踪新文件。

    **口径写死在这里**（不做智能推断）：``git diff --name-only <rev>`` 的并集
    ``git ls-files --others --exclude-standard``。后者必须算进来——新增的规格文件
    与被新增的模块都在里面，漏了它们等于「新写的突变永远不进增量闸门」。
    """
    changed: set[str] = set()
    for cmd in (
        ["git", "diff", "--name-only", rev],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        r = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            raise LaunchFailure(f"{' '.join(cmd)} 失败：{r.stderr[-300:]}")
        changed |= {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    return changed


def _spec_rel(spec_name: str) -> str:
    """规格文件名 → 仓库相对路径（``--changed`` 的「规格文件命中」口径）。

    ``entries`` 里存的是 ``spec_file.name``（裸文件名，规格目录扁平且名字唯一）；
    git 的改动路径是**仓库相对路径** ⇒ 必须补 ``tests/mutation_specs/`` 前缀再比。
    """
    return f"tests/mutation_specs/{spec_name}"


def precheck(entries: list[tuple[str, str, str, str, str, str]]) -> tuple[list[str], int]:
    """**只读预检**：不改任何源码，秒级验完「突变是否还能被施加」。

    四类问题（与 ``run_one`` 的四个失败桶一一对应，只是提前到「不花钱」的阶段）：
    1. 目标文件不存在 ⇒ 将来 ``[INVALID]``/崩；
    2. 锚点命中 != 1 ⇒ 将来 ``[ABORT]``；
    3. 定向测试文件不存在 ⇒ 将来跑出 exit 4 被误读；
    4. ``replacement == anchor`` ⇒ 变异等于没改（``run_one`` 靠 SHA 变化兜底，但那时
       已经花掉了一次 pytest 启动）。

    ⇒ 本函数就是「每批收尾」要的那一档：**它比跑全量更灵敏**（锚点一断立刻报），
    而且成本是毫秒级。实测 370 条全量 **0.2 秒**（2026-10-22）。

    入参是**已组装的条目**（而不是规格文件）⇒ 与 ``--changed`` 可组合：
    ``--changed --precheck`` = 「只预检本批改动命中的那些突变」，即每批真正要的那一步。
    """
    problems: list[str] = []
    for _spec_name, mid, rel, anchor, replacement, test_file in entries:
        path = ROOT / rel
        if not path.is_file():
            problems.append(f"{mid}: 目标文件不存在 {rel}")
            continue
        hits = path.read_text(encoding="utf-8", errors="replace").count(anchor)
        if hits != 1:
            problems.append(f"{mid}: 锚点命中 {hits} 次（要求恰好 1）@ {rel}")
        if anchor == replacement:
            problems.append(f"{mid}: replacement 与 anchor 相同 ⇒ 变异不会生效")
        tf = (ROOT / test_file) if test_file.startswith("tests/") else (WEB / test_file)
        if not tf.is_file():
            problems.append(f"{mid}: 定向测试文件不存在 {test_file}")
    return problems, len(entries)


def _bin_by_target(entries: list[tuple], jobs: int) -> list[list[int]]:
    """按**目标文件**分组后做 LPT 装箱 —— 同一个 ``rel`` 绝不同时给两个 worker。

    为什么必须按 ``rel`` 而不按规格分片：不同规格会改**同一个文件**
    （`E1-1` 与 `SKILL-KEY-2` 都锚在 `writer_input.py`，各自还有独立备份路径
    ``<file>.mutbak``）⇒ 两个 worker 同时改同一文件会互相覆盖备份、并且把对方的
    改动当成自己的「原文」写回去 ⇒ **静默污染源码**。按 ``rel`` 分组是唯一安全的分片单位。
    """
    by_rel: dict[str, list[int]] = {}
    for idx, it in enumerate(entries):
        by_rel.setdefault(it[2], []).append(idx)
    bins: list[list[int]] = [[] for _ in range(max(1, jobs))]
    load = [0] * len(bins)
    for rel, idxs in sorted(by_rel.items(), key=lambda kv: -len(kv[1])):
        target = load.index(min(load))
        bins[target].extend(idxs)
        load[target] += len(idxs)
    return bins


def main(argv: list[str]) -> int:
    spec_args: list[str] = []
    use_all = False
    jobs = 1
    accept_concurrent = False
    use_changed = False
    changed_since = "HEAD"
    fail_fast = False
    precheck_only = False

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--all":
            use_all = True
        elif arg == "--jobs" and i + 1 < len(argv):
            jobs = int(argv[i + 1])
            i += 1
        elif arg.startswith("--jobs="):
            jobs = int(arg.split("=", 1)[1])
        elif arg == "--accept-concurrent":
            accept_concurrent = True
        elif arg == "--changed":
            use_changed = True
        elif arg == "--changed-since" and i + 1 < len(argv):
            changed_since = argv[i + 1]
            i += 1
        elif arg.startswith("--changed-since="):
            changed_since = arg.split("=", 1)[1]
        elif arg == "--fail-fast":
            fail_fast = True
        elif arg == "--precheck":
            precheck_only = True
        elif arg in ("-h", "--help"):
            print("用法: run_spec.py <规格文件...> | --all  [--jobs N] [--changed] [--precheck]")
            print("      --changed [--changed-since REV]  只跑「目标源文件或规格文件命中改动集」的突变")
            print("      --precheck                       只做只读预检（秒级，不改源码）")
            print("      --jobs N                         按目标文件分片并行（默认 1=串行）")
            print("                                      ⚠️ 并发结果**不得作为闸门结论**"
                  "（会造假 CAUGHT / 判定不可复现 / 归因错配，已实证）⇒ 闸门一律串行")
            print("      --accept-concurrent              承认「并发结果只作降噪参考」，"
                  "才允许退出码 0（默认并发档位恒为 3）")
            print("      --fail-fast                      pytest 加 -x（首红即停）")
            print("")
            print("退出码：0=全通过 / 1=有存活或无效 / 2=用法错误或不可判 /"
                  " 3=并发档位未经 --accept-concurrent 确认（结果不可作闸门结论；"
                  "存活清单仍见上方汇总）")
            print("      ⚠️ 退出码 3 适用于**所有非用法错误的结束路径**"
                  "（改动集为空 / 只读预检结束 / 跑完全部突变）"
                  "⇒ 并发档位没有旁路；每条结束路径都会打印「档位:」行")
            return 0
        elif arg.startswith("-"):
            print(f"[INVALID]  未知选项：{arg}")
            return 2
        else:
            spec_args.append(arg)
        i += 1

    if use_all:
        spec_files = discover_specs()
        print(f"[覆盖] 发现规格文件 {len(spec_files)} 个：" f"{[p.name for p in spec_files]}")
    else:
        try:
            spec_files = [_resolve_spec(a) for a in spec_args]
        except LaunchFailure as exc:
            print(f"[INVALID]  {exc}")
            return 2
    if not spec_files:
        print("用法: run_spec.py <规格文件...> | --all  [--jobs N] [--changed] [--precheck]")
        return 2

    # ---- 组装条目（并按 --changed 过滤）----
    entries: list[tuple[str, str, str, str, str, str]] = []
    for sf in spec_files:
        for mid, rel, anchor, replacement, test_file in load_spec(sf):
            entries.append((sf.name, mid, rel, anchor, replacement, test_file))

    if use_changed:
        try:
            changed = _git_changed_paths(changed_since)
        except LaunchFailure as exc:
            print(f"[INVALID]  {exc}")
            return 2
        # 并集口径（2026-10-23 批次二十九 task-24）：目标源文件命中 ∪ 规格文件命中。
        # 只筛目标文件是**漏筛**——新增规格若目标文件未改，旧口径永远不会选中它。
        target_hits = sum(1 for e in entries if e[2] in changed)
        spec_hits = sum(1 for e in entries if _spec_rel(e[0]) in changed)
        selected: list[tuple[str, str, str, str, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for e in entries:
            if e[2] in changed or _spec_rel(e[0]) in changed:
                key = (e[0], e[1])
                if key not in seen:
                    seen.add(key)
                    selected.append(e)
        overlap = target_hits + spec_hits - len(selected)
        entries = selected
        print(
            f"[增量] 目标文件命中 {target_hits} 条 / 规格文件命中 {spec_hits} 条"
            f" ⇒ 合计 {len(entries)} 条"
            + (f"（重叠 {overlap} 条，已去重）" if overlap else "")
            + f"；口径：目标源文件或规格文件 ∈ 改动集"
            f"（git diff --name-only {changed_since} ∪ 未跟踪文件，改动文件 {len(changed)} 个）"
        )
        if not entries:
            print(
                "✅ 本批没有任何突变的目标源文件或规格文件被改动 ⇒ 增量闸门无事可做"
            )
            # ⚠️ 早退**也要打档位**并走统一出口（2026-10-23 对抗审查 B 第三轮 minor#4）：
            # 原先这里直接 ``return 0`` ⇒ 并发档位下退出码 0、无档位行、无并发警告
            # ⇒ 「并发档位退出码恒为 3」的契约被绕过。
            for line in _tier_lines(jobs, accept_concurrent):
                print(line)
            if jobs > 1:
                print(_parallel_warning(jobs))
            return _tier_exit(0, jobs, accept_concurrent)

    # ---- 只读预检（在 --changed 过滤之后 ⇒ 可组合成「本批秒级闸门」）----
    if precheck_only:
        problems, total = precheck(entries)
        scope = "（已按 --changed 收窄）" if use_changed else ""
        print(f"[预检] 突变 {total} 条{scope} / 问题 {len(problems)} 处")
        for p in problems:
            print(f"  ✗ {p}")
        print("✅ 全部可安全交给 run_spec.py 执行" if not problems else "❌ 预检未通过")
        # ⚠️ 只读档位同样要打档位行 + 走统一出口（与上面的早退同一条纪律）。
        for line in _tier_lines(jobs, accept_concurrent):
            print(line)
        if jobs > 1:
            print(_parallel_warning(jobs))
        return _tier_exit(1 if problems else 0, jobs, accept_concurrent)

    survived: list[str] = []
    aborted: list[str] = []

    def _record(line: str) -> None:
        print(line, flush=True)
        if line.startswith("[SURVIVED]"):
            survived.append(line.split(":", 1)[0].replace("[SURVIVED]", "").strip())
        elif line.startswith("[ABORT]") or line.startswith("[INVALID]"):
            aborted.append(line.split(":", 1)[0].split("]")[-1].strip())

    if jobs <= 1:
        # 串行：保持历史输出形态（带「规格:」分段），便于与旧日志对照
        last_spec = None
        for spec_name, mid, rel, anchor, replacement, test_file in entries:
            if spec_name != last_spec:
                print("=" * 78)
                print(f"规格: {spec_name}")
                last_spec = spec_name
            _record(
                run_one(mid, rel, anchor, replacement, test_file, fail_fast=fail_fast)
            )
    else:
        bins = _bin_by_target(entries, jobs)

        def _work(idxs: list[int]) -> None:
            for idx in idxs:
                _, mid, rel, anchor, replacement, test_file = entries[idx]
                _record(
                    run_one(mid, rel, anchor, replacement, test_file, fail_fast=fail_fast)
                )

        print(f"[并行] {jobs} 路（按目标文件分片；同一文件绝不并发）")
        print(_parallel_warning(jobs))
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            list(pool.map(_work, [b for b in bins if b]))

    print("=" * 78)
    for line in _tier_lines(jobs, accept_concurrent):
        print(line)
    print(f"突变存活: {survived or '无'}")
    print(f"脚本中止/无效: {aborted or '无'}")
    if jobs > 1:
        # 末尾再打一次：只看结论尾部的人（CI 日志、grep「突变存活」）不得错过。
        print(_parallel_warning(jobs))
    # ⚠️ **并发档位的退出码恒为 3**（哪怕一条存活都没有）——
    # 2026-10-23 对抗审查 B 的 F-1：并发会产出**假 CAUGHT**（虚报绿）⇒
    # 若这里允许 exit 0，闸门/CI 就会把「不可信的并发结果」当成
    # 「全量突变通过」⇒ 那正是量具自己造假绿灯的形状。
    # 存活/无效清单仍在上面的汇总里（人看得到），但**退出码不冒充绿**。
    return _tier_exit(1 if (survived or aborted) else 0, jobs, accept_concurrent)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
