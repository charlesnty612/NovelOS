"""通用突变探针：临时改一个源文件 ⇒ 跑指定测试 ⇒ 报告「红了哪几条用例」⇒ 逐字节还原。

为什么需要它
------------
本仓有三处**同款探针**的雏形（`probe_profile_mask_exit.py` / `probe_mask_inner_scope.py`
/ 2026-10-22 批次十七的一次性探针），做的事完全一样：
「备份 → 施加一处改动 → 跑定向测试 → 看红了没有 → finally 还原并校验 SHA」。
按 AGENTS 坑区「副本不是『重复的代码』，是『重复的义务』」，同一语义出现第 3 次就该
收敛成 1 处 ⇒ 本脚本是那个单点。

它比突变执行器（``tests/mutation_specs/run_spec.py``）多一件事：
**报告失败的是哪一条用例**。执行器的摘要只给「哪个文件、几条失败」，
而复核者需要确认「红的是**预期那条**、不是收集失败或另一条用例」——
本脚本把失败用例名逐条列出来，堵上「量具在最需要被复核的场合交白卷」那个坑。

与执行器的分工
--------------
* **执行器**（``run_spec.py``）：跑**已登记**的突变规格（325 条），判「存活 / 捕获」。
* **本探针**：跑**未登记**的临时改动（调查用），判「红了哪几条」，**不产生规格**。
  ⇒ 用完若结论要长期保留，应当把它**写进一条突变规格**，而不是留着探针。

用法
----
    # 前端（vitest）
    python scripts/mutation_probe.py \\
        --target apps/web/src/pages/SimulationPanel.tsx \\
        --anchor-file C:/tmp/anchor.txt --replacement-file C:/tmp/repl.txt \\
        --test src/pages/SimulationPanel.test.tsx --runner vitest

    # 后端（pytest）
    python scripts/mutation_probe.py \\
        --target packages/core/api/main.py \\
        --anchor '        @app.get("/", include_in_schema=False)' \\
        --replacement '        @app.get("/")' \\
        --test tests/api/test_spa_hosting.py --runner pytest

⚠️ **本脚本会临时改工作区** ⇒ 与突变执行器/pytest 并发是危险的
（AGENTS 坑区「突变执行器在飞时对工作区做任何写操作 ⇒ 突变态进 git 历史」）。
⇒ 本脚本**自带闸**：发现工作区存在 ``*.mutbak``（执行器的还原备份）即拒绝运行；
且每次运行结束都在 ``finally`` 里**逐字节还原**并打印 SHA 校验结果。
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"

ANSI = re.compile(r"\x1b\[[0-9;]*m")

#: 判定标签（三桶，与 ``scripts/mutation_harness.py`` **同名同义**）。
JUDGE_SURVIVED = "SURVIVED"
JUDGE_CAUGHT = "CAUGHT"
JUDGE_INVALID = "INVALID"


def judge_pytest_run(code: int, out: str) -> tuple[str, str]:
    """判定一次 pytest 子进程跑的结论 ⇒ ``(标签, 理由)``。**纯函数**。

    ⚠️ 口径与 ``scripts/mutation_harness.py`` 的同名函数**逐字同形**
    ⇒ 两套突变执行器对「环境炸了」与「用例真红」的判定**必须一致**
    （本仓坑区「两套实现口径分叉」形状的防线；仓内看守见
    ``tests/unit/test_mutation_harness_judgment.py``：同一张判定表、两个被测对象）。

    判定表::

        退出码 0                       ⇒ SURVIVED（真全过）
        退出码 1 且摘要含 " failed"     ⇒ CAUGHT（真红了）
        退出码 1 但摘要**无** " failed" ⇒ INVALID（用例是 ERROR 而非 FAIL ⇒ 环境/收集层炸了）
        退出码 2/3/4/5                 ⇒ INVALID（压根没跑过）
        输出含 "no tests ran"          ⇒ INVALID（零测试被收集）

    ⚠️ **本函数与 ``scripts/mutation_harness.py`` 的同名函数是「刻意复制」**：两套突变
    执行器各自独立运行（probe 可被复制到别处单跑）⇒ 抽共享模块会扩依赖面
    ⇒ **改一处必须同步改另一处**；同口径由
    ``tests/unit/test_mutation_harness_judgment.py`` 的「两者不许分叉」用例**机器保证**
    （AGENTS 坑区「两套实现口径分叉」的既有对策形状：用机器看守钉住，而不是靠结构）。
    """
    if code not in (0, 1):
        return JUDGE_INVALID, (
            f"pytest 退出码 {code}（2=中断 / 3=内部错误 / 4=用法或收集错误 / 5=零测试被收集）"
            f"——都不是「跑过了」"
        )
    if "no tests ran" in out:
        return JUDGE_INVALID, "pytest 报告「no tests ran」⇒ 目标文件没收集到任何用例"
    if code == 1:
        if " failed" in out:
            return JUDGE_CAUGHT, "pytest 退出码 1 且摘要含「 failed」⇒ 有用例断言失败（真被抓住）"
        return JUDGE_INVALID, (
            "pytest 退出码 1 但摘要里没有「 failed」⇒ 用例是 **ERROR** 而非 FAIL"
            "（环境 / 收集层炸了，一个断言都没跑到）⇒ 不算「被抓住」"
        )
    if " passed" not in out:
        return JUDGE_INVALID, "pytest 退出码 0 但输出里没有「 passed」⇒ 没有任何执行痕迹"
    return JUDGE_SURVIVED, "pytest 退出码 0 且有用例通过数 ⇒ 全过"


def judge_vitest_run(code: int, out: str) -> tuple[str, str]:
    """判定一次 vitest 子进程跑的结论 ⇒ ``(标签, 理由)``。**纯函数**。

    ⚠️ 口径**来自实测**（2026-10-23，vitest 2.1.9），**不是**照搬 pytest：

    ===========================  ====  ==========================================
    情形                          退出码  输出关键行
    ===========================  ====  ==========================================
    全过                          0     ``Test Files 1 passed`` / ``Tests 13 passed``
    断言失败                      1     ``Test Files 1 failed`` / ``Tests 1 failed``
    收集期错误（模块解析失败）      1     ``Test Files 1 failed`` / ``Tests  no tests``
    零测试收集                    1     ``No test files found``（**无** Test Files 行）
    环境炸了（config 加载失败）    1     错误栈（**无** Test Files 行）
    ===========================  ====  ==========================================

    ⇒ **判别点**有两个：①「``Test Files``」行（有没有真的跑起测试文件）；
    ②「``Tests  no tests``」（套件在收集期就炸了 ⇒ **一个用例都没跑到**）。

    ⚠️ **已知未覆盖面（2026-10-23 登记）**：vitest 的「Unhandled Error」形态**未**纳入
    实测（已实测 6 种：全绿 / 断言失败 / 收集期错误 / 零测试收集 / 环境炸了 /
    ``--passWithNoTests``）⇒ 若 vitest 改版输出形态变化，本判定表需**重新实测**。

    ⚠️ **本函数与 ``scripts/mutation_harness.py`` 的同名函数是「刻意复制」**
    ⇒ 改一处必须同步改另一处（机器保证见
    ``tests/unit/test_mutation_harness_judgment.py`` 的「两者不许分叉」用例）。
    """
    text = ANSI.sub("", out)
    if "Test Files" not in text:
        return JUDGE_INVALID, (
            "vitest 输出里没有「Test Files」计数行 ⇒ 环境炸了 / 零测试收集（一个用例都没跑到）"
        )
    if "no tests" in text:
        return JUDGE_INVALID, (
            "vitest 报「Tests  no tests」⇒ 套件在收集期就炸了（Failed Suites），"
            "一个用例都没跑到 ⇒ 不算「被抓住」"
        )
    if code == 0:
        return JUDGE_SURVIVED, "vitest 退出码 0 且有测试计数 ⇒ 全过"
    return JUDGE_CAUGHT, "vitest 退出码非 0 且有「failed」用例 ⇒ 真失败"

# vitest 失败行形态：`FAIL  src/pages/X.test.tsx > Suite > 用例名`
_VITEST_FAIL = re.compile(r"FAIL\s+(\S+)\s*>\s*(.+)")
# pytest 失败行形态：`FAILED tests/...::test_x - 断言...`
_PYTEST_FAIL = re.compile(r"^FAILED\s+(\S+)")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_no_mutation_runner(root: Path | None = None) -> None:
    """拒绝与突变执行器并发：执行器在飞时工作区会有 ``*.mutbak``。

    依据：AGENTS 坑区「突变执行器在飞时对工作区做任何写操作 ⇒ 突变态进 git 历史」
    ——那次事故的根因正是「在执行器改源码的窗口里执行了 ``git add -A``」。
    """
    base = root or ROOT
    backups = list(base.rglob("*.mutbak"))
    backups = [p for p in backups if ".venv" not in p.parts and "node_modules" not in p.parts]
    if backups:
        raise SystemExit(
            "[ABORT] 工作区存在 *.mutbak（突变执行器的还原备份）⇒ 执行器可能正在飞。\n"
            f"       命中：{[str(p) for p in backups[:5]]}\n"
            "       本脚本会改工作区，与执行器并发 = 突变态进 git 历史 ⇒ 拒绝运行。\n"
            "       等执行器跑完（或确认残留已清）再试。"
        )


def read_spec(text: str | None, file_path: str | None, what: str) -> str:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")
    if text is None:
        raise SystemExit(f"[ABORT] 缺 {what}：给 --{what} 或 --{what}-file")
    # 命令行传入时无法带真实换行 ⇒ 允许用 \n 转义
    return text.replace("\\n", "\n")


def parse_failures(output: str, runner: str) -> list[str]:
    """从 runner 输出里抽出「失败的用例名」列表。"""
    text = ANSI.sub("", output)
    out: list[str] = []
    if runner == "vitest":
        for path, name in _VITEST_FAIL.findall(text):
            out.append(f"{path} > {name.strip()}")
    else:
        for line in text.splitlines():
            m = _PYTEST_FAIL.match(line.strip())
            if m:
                out.append(m.group(1))
    # 去重保序
    seen: set[str] = set()
    uniq = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


#: pytest 子进程的 basetemp 根目录（**仓库外** ⇒ 不污染工作树）。
#: ⚠️ pytest 内部是 ``Path.mkdir(mode=0o700)``（**不带 parents**）⇒ 父目录必须先建好，
#: 否则表现为 ``FileNotFoundError``（看起来像用例挂了，其实是用例压根没跑起来）。
_BASETEMP_ROOT = Path(tempfile.gettempdir()) / "novelos-mutbasetemp"
_basetemp_counter = itertools.count()


def _basetemp_for(tag: str) -> Path:
    """给一次 pytest 子进程分配**唯一**的 basetemp（并发 / 连续跑都不撞）。"""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", tag)[:60]
    _BASETEMP_ROOT.mkdir(parents=True, exist_ok=True)
    return _BASETEMP_ROOT / f"probe-{safe}-{os.getpid()}-{next(_basetemp_counter)}"


def run_tests(test_file: str, runner: str, root: Path) -> tuple[int, str]:
    """在 ``root`` 下跑测试。⚠️ cwd 必须是 ``root``（不是仓库根）——否则临时目录里的
    测试文件在真仓库里找不到，pytest 报**收集错误（退出码 4）**，
    而退出码 4 与「用例红」在非零码上完全同形（AGENTS 坑区：量具失真）。

    ⚠️ **pytest 必须自带唯一 basetemp**（2026-10-23 实测修）：本机
    ``%LOCALAPPDATA%\\Temp\\pytest-of-<user>`` 一旦被创建就**连 owner 都拒绝访问**
    （``PermissionError: [WinError 5]``）⇒ 用到 ``tmp_path`` 的用例在**准备阶段**
    就 ERROR ⇒ 子进程退出码 1，而输出里**没有** ``FAILED`` 行 ⇒ 本探针只能报
    「未从输出解析出失败用例名——量具侧失真」⇒ **在本机根本测不到东西**。
    """
    basetemp: Path | None = None
    if runner == "vitest":
        vitest = WEB / "node_modules" / ".bin" / ("vitest.cmd" if sys.platform == "win32" else "vitest")
        cmd = [str(vitest), "run", test_file]
        cwd = str(WEB)
    else:
        py = str(VENV_PY) if VENV_PY.is_file() else sys.executable
        basetemp = _basetemp_for(test_file)
        cmd = [py, "-m", "pytest", test_file, "-q", f"--basetemp={basetemp}"]
        cwd = str(root)
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        if basetemp is not None:
            # 子进程已退出 ⇒ 句柄已释放；``ignore_errors=True`` 保证清理失败**不会**
            # 把结论搅浑（清理不是判别点）。
            shutil.rmtree(basetemp, ignore_errors=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    # ⚠️ 必须在函数里 + 判 hasattr（AGENTS 坑区：模块级 reconfigure 会让 import 就炸）
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="通用突变探针（改一处 → 跑测试 → 报红了哪几条 → 还原）")
    ap.add_argument("--target", required=True, help="要临时改的源文件（相对仓库根）")
    ap.add_argument("--anchor", default=None, help="待替换的原文（\\n 表示换行）")
    ap.add_argument("--anchor-file", default=None, help="待替换的原文（从文件读，可含真实换行）")
    ap.add_argument("--replacement", default=None, help="替换成的新文")
    ap.add_argument("--replacement-file", default=None, help="替换成的新文（从文件读）")
    ap.add_argument("--test", required=True, help="要跑的测试文件（pytest: tests/...；vitest: src/...）")
    ap.add_argument("--runner", choices=("pytest", "vitest"), default="pytest")
    ap.add_argument(
        "--root",
        default=None,
        help="基准目录（默认仓库根）——**可测性参数**：让测试能在临时目录里造一个微型仓库来验证本脚本",
    )
    ap.add_argument(
        "--allow-missing",
        action="store_true",
        help="允许锚点命中 0 次（只报告，不施加改动）",
    )
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else ROOT
    assert_no_mutation_runner(root)

    target = (root / args.target).resolve()
    if not target.is_file():
        raise SystemExit(f"[ABORT] 目标文件不存在：{target}")

    anchor = read_spec(args.anchor, args.anchor_file, "anchor")
    replacement = read_spec(args.replacement, args.replacement_file, "replacement")

    # ⚠️ **字节级**（2026-10-23 实测修）：旧实现走 ``read_text``/``write_text`` 文本通道
    # ⇒ Windows 上 ``write_text`` 把 LF 翻成 CRLF ⇒ **还原后 SHA 与前值不等**
    # ⇒ ①「[还原] 校验」形同虚设；② 被测文件的行尾被静默污染（git 归一后还看不见）。
    # ⇒ 现在全程走**字节通道**：除锚点那几字节外，其余字节逐位保留；还原 = 原字节回写。
    original_bytes = target.read_bytes()
    anchor_bytes = anchor.encode("utf-8")
    if anchor_bytes not in original_bytes:
        # CLI 传入的锚点行尾（多为 LF）与文件（可能 CRLF）不一致 ⇒ 按文件的主导行尾再试一次
        alt = (
            anchor_bytes.replace(b"\n", b"\r\n")
            if b"\r\n" in original_bytes
            else anchor_bytes.replace(b"\r\n", b"\n")
        )
        if alt in original_bytes:
            anchor_bytes = alt
    hits = original_bytes.count(anchor_bytes)
    print(f"[目标] {args.target}")
    print(f"[锚点] 命中 {hits} 次（要求恰好 1）")
    if hits != 1:
        if args.allow_missing:
            print("[SKIP] 锚点未唯一命中，未施加改动")
            return 0
        raise SystemExit("[ABORT] 锚点命中次数不是 1 ⇒ 未施加任何改动")

    before = sha256(target)
    try:
        # 替换文本的行尾随文件（否则 CRLF 文件会被塞进 LF 片段）
        replacement_bytes = replacement.encode("utf-8")
        replacement_bytes = (
            replacement_bytes.replace(b"\n", b"\r\n")
            if b"\r\n" in original_bytes
            else replacement_bytes.replace(b"\r\n", b"\n")
        )
        target.write_bytes(original_bytes.replace(anchor_bytes, replacement_bytes))
        after_write = sha256(target)
        if after_write == before:
            print("[ABORT] 写入后 SHA256 未变 ⇒ 变异没生效（等于没改）")
            return 2

        code, output = run_tests(args.test, args.runner, root)
        judge = judge_pytest_run if args.runner == "pytest" else judge_vitest_run
        label, reason = judge(code, output)
        failures = parse_failures(output, args.runner)
        print(f"[退出码] {code}")
        print(f"[判定] {label} —— {reason}")
        print(f"[失败用例] （共 {len(failures)} 条）")
        for f in failures:
            print(f"   - {f}")
        if label == JUDGE_INVALID:
            # ⚠️ **响亮**：环境炸了 / 一个用例都没跑到 ⇒ **不是**「突变存活」，
            # 也不是「突变被抓住」⇒ 用独立的退出码（2）告诉调用方「本次无效」。
            print("   ⚠️ 量具不可用（环境炸了 / 没跑到用例）——**这不是**「突变存活」，请人工核对")
            return 2
        if not failures:
            print("   ⚠️ 未从输出解析出失败用例名——量具侧失真，需人工看原始输出")
        return 0 if label == JUDGE_CAUGHT else 1
    finally:
        # ⚠️ **字节级还原**：原字节回写 ⇒ 还原后 SHA 与前值**逐位一致**
        # （含行尾；这是旧实现做不到的那一条）。
        target.write_bytes(original_bytes)
        restored = sha256(target)
        ok = restored == before
        print(f"[还原] SHA {restored[:16]} == 前值 {before[:16]} ? {ok}")
        if not ok:
            print("[ABORT] 还原校验失败 ⇒ 源文件可能处于改动态，请手工核对！")
            return 3


if __name__ == "__main__":
    raise SystemExit(main())
