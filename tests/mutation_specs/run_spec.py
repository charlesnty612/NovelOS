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

    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py tests/mutation_specs/g1_world.py

全组合并跑（收尾闸门用）::

    .venv/Scripts/python.exe tests/mutation_specs/run_spec.py --all

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

import hashlib
import importlib.util
import os
import pathlib
import re
import shutil
import subprocess
import sys

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


def run_one(mid: str, rel: str, anchor: str, replacement: str, test_file: str) -> str:
    """跑一条突变，返回 CAUGHT / SURVIVED / ABORT / INVALID 之一。"""
    path = ROOT / rel
    original = path.read_text(encoding="utf-8")
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
        try:
            if is_py:
                ok, out = _run([PY, "-m", "pytest", test_file, "-q"], ROOT)
                runner = "pytest"
            else:
                ok, out = _run([str(VITEST), "run", test_file], WEB)
                runner = "vitest"
            _assert_tests_ran(out, runner)
        except LaunchFailure as exc:
            return f"[INVALID]  {mid}: {exc}"

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


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--all":
        spec_files = discover_specs()
        print(f"[覆盖] 发现规格文件 {len(spec_files)} 个：" f"{[p.name for p in spec_files]}")
    else:
        try:
            spec_files = [_resolve_spec(a) for a in argv]
        except LaunchFailure as exc:
            print(f"[INVALID]  {exc}")
            return 2
    if not spec_files:
        print("用法: run_spec.py <规格文件...> | --all")
        return 2

    survived: list[str] = []
    aborted: list[str] = []
    for sf in spec_files:
        print("=" * 78)
        print(f"规格: {sf.name}")
        for mid, rel, anchor, replacement, test_file in load_spec(sf):
            line = run_one(mid, rel, anchor, replacement, test_file)
            print(line, flush=True)
            if line.startswith("[SURVIVED]"):
                survived.append(mid)
            elif line.startswith("[ABORT]") or line.startswith("[INVALID]"):
                aborted.append(mid)

    print("=" * 78)
    print(f"突变存活: {survived or '无'}")
    print(f"脚本中止/无效: {aborted or '无'}")
    return 1 if (survived or aborted) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
