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
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"

ANSI = re.compile(r"\x1b\[[0-9;]*m")

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


def run_tests(test_file: str, runner: str, root: Path) -> tuple[int, str]:
    """在 ``root`` 下跑测试。⚠️ cwd 必须是 ``root``（不是仓库根）——否则临时目录里的
    测试文件在真仓库里找不到，pytest 报**收集错误（退出码 4）**，
    而退出码 4 与「用例红」在非零码上完全同形（AGENTS 坑区：量具失真）。"""
    if runner == "vitest":
        vitest = WEB / "node_modules" / ".bin" / ("vitest.cmd" if sys.platform == "win32" else "vitest")
        cmd = [str(vitest), "run", test_file]
        cwd = str(WEB)
    else:
        py = str(VENV_PY) if VENV_PY.is_file() else sys.executable
        cmd = [py, "-m", "pytest", test_file, "-q"]
        cwd = str(root)
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
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

    original = target.read_text(encoding="utf-8")
    hits = original.count(anchor)
    print(f"[目标] {args.target}")
    print(f"[锚点] 命中 {hits} 次（要求恰好 1）")
    if hits != 1:
        if args.allow_missing:
            print("[SKIP] 锚点未唯一命中，未施加改动")
            return 0
        raise SystemExit("[ABORT] 锚点命中次数不是 1 ⇒ 未施加任何改动")

    before = sha256(target)
    try:
        target.write_text(original.replace(anchor, replacement), encoding="utf-8")
        after_write = sha256(target)
        if after_write == before:
            print("[ABORT] 写入后 SHA256 未变 ⇒ 变异没生效（等于没改）")
            return 2

        code, output = run_tests(args.test, args.runner, root)
        failures = parse_failures(output, args.runner)
        print(f"[退出码] {code}")
        print(f"[失败用例] （共 {len(failures)} 条）")
        for f in failures:
            print(f"   - {f}")
        if not failures:
            print("   ⚠️ 未从输出解析出失败用例名——量具侧失真，需人工看原始输出")
        return 0 if failures else 1
    finally:
        target.write_text(original, encoding="utf-8")
        restored = sha256(target)
        ok = restored == before
        print(f"[还原] SHA {restored[:16]} == 前值 {before[:16]} ? {ok}")
        if not ok:
            print("[ABORT] 还原校验失败 ⇒ 源文件可能处于改动态，请手工核对！")
            return 3


if __name__ == "__main__":
    raise SystemExit(main())
