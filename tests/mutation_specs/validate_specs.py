"""突变规格的**只读**预检（2026-10-15 批次（六））。

为什么需要它
------------
`run_spec.py` 会**临时改源码** ⇒ 禁与任何 pytest 并发。本轮 9 个成员并发开发时，
成员要自验「我的规格条目能不能跑」，唯一的办法就是先做**只读**检查。

本脚本只读文件、不写任何字节，逐条核对四件事：

1. **锚点命中恰好 1 次**——0 次或 >1 次都是规格自身写错（脚本运行时会判
   ``[ABORT]``，但那时已经改过一次文件了）；
2. **变异确实改变内容**——replacement 是 anchor 的超集就等于**什么都没改**
   （批次五 M15 实测存活：插了行注释，逻辑未变）；
3. **变异后仍可编译**——排除「红的是收集失败而不是断言失败」那一类量具缺陷；
4. **目标文件存在**。

⚠️ 本脚本**只保证「这条突变可以正确地跑起来」，不保证「它会被捕获」**——
后者只能由 ``run_spec.py`` 实跑得出。两者不可互相替代（同坑区：
「突变被 SKIP ≠ 突变存活」）。

用法::

    .venv/Scripts/python.exe tests/mutation_specs/validate_specs.py
    .venv/Scripts/python.exe tests/mutation_specs/validate_specs.py g1_world.py g2_ledger_plot.py
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC_DIR = ROOT / "tests" / "mutation_specs"


def load(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(f"_vs_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.MUTATIONS)


def check_one(mid: str, rel: str, anchor: str, replacement: str, test_file: str) -> list[str]:
    problems: list[str] = []
    path = ROOT / rel
    if not path.exists():
        return [f"{mid}: 目标文件不存在 {rel}"]
    src = path.read_text(encoding="utf-8")

    hits = src.count(anchor)
    if hits != 1:
        problems.append(f"{mid}: 锚点命中 {hits} 次（要求恰好 1）")

    mutated = src.replace(anchor, replacement, 1)
    if mutated == src:
        problems.append(f"{mid}: 变异未改变内容（replacement 是 anchor 的超集＝等于没改）")

    if rel.endswith(".py"):
        try:
            # ⚠️ 去文件头 BOM 再解析（2026-10-16 批次九）：Python 的 import 走
            # utf-8-sig、**容忍** BOM，而 ``ast.parse(str)`` 不容忍——不镜像加载器就会
            # 把**合法**的带 BOM 源文件误判成「变异后语法错误」，于是任何指向它的突变
            # 都拿不到结论（同坑区「量具缺陷伪装成被测物有病」）。本仓实测 3 个 .py
            # 带 BOM，全在 chapter_write/ 下：polisher / save_draft / scene_planner。
            ast.parse(mutated.removeprefix(chr(0xFEFF)))
        except SyntaxError as exc:
            problems.append(f"{mid}: 变异后语法错误（{exc}）→ 跑起来会「收集失败」而非断言红")

    if not test_file.startswith(("tests/", "src/")):
        problems.append(f"{mid}: 定向用例路径形态可疑 {test_file}")
    return problems


def main(argv: list[str]) -> int:
    files = (
        [SPEC_DIR / a for a in argv]
        if argv
        else sorted(p for p in SPEC_DIR.glob("*.py") if not p.name.startswith("_") and p.name != "run_spec.py" and p.name != "validate_specs.py")
    )
    if not files:
        print("没有找到规格文件")
        return 2

    total = bad = 0
    for f in files:
        if not f.exists():
            print(f"❌ 规格文件不存在: {f}")
            bad += 1
            continue
        try:
            muts = load(f)
        except Exception as exc:  # noqa: BLE001 —— 预检脚本本身要抗住坏规格
            print(f"❌ {f.name}: 规格文件加载失败 {type(exc).__name__}: {exc}")
            bad += 1
            continue
        problems: list[str] = []
        for m in muts:
            total += 1
            problems += check_one(*m)
        mark = "✅" if not problems else "❌"
        print(f"{mark} {f.name}: {len(muts)} 条")
        for p in problems:
            print(f"     {p}")
            bad += 1

    print("=" * 70)
    print(f"规格文件 {len(files)} 个 / 条目 {total} 条 / 问题 {bad} 处")
    if bad == 0:
        print("✅ 全部可安全交给 run_spec.py 执行（**不代表会被捕获**——那要实跑）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
