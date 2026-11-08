"""用例清单守恒闸门（2026-11-08 批次三十八 · 重构基础设施 INV）。

## 为什么需要它

重构批次的第一验收口径是「**用例数零增减**」。此前这件事全靠**手写记账**
（例：``tests/workflow/test_chapter_pipelines.py`` 顶部那句「拆分前 17 ⇒ 拆分后 10 + 7 = 17」
是人工数出来写下的），而人工记账有三个已知失效方向：

- 拆文件时**复制**了若干用例 ⇒ 数量涨了却看不出（重复不是守恒）；
- 重写夹具时**顺手删**了两条，另一处又加了三条 ⇒ 总数对得上、**覆盖面已经变了**；
- 姑且核对了总数，但**逐文件**的增减被抵消。

⇒ 本脚本把口径固化为**两层**（2026-11-08 方案订正：不做逐 node-id 全量快照，
那样每次合法新增用例都要改快照，摩擦过大）：

1. **总数**（pytest 测试函数数 + vitest it/test 数）；
2. **逐文件计数**（哪一个文件多了/少了几条）。

任何变更都必须走 ``--update --reason <理由>``，理由进 ``change_log`` ⇒ 变更**可见且留痕**。

## 口径（机器可判，不依赖 pytest 收集）

- Python：``tests/**/test_*.py`` 里 AST 的 ``def test_*`` 数量（含嵌套定义）。
  不用 ``pytest --collect-only``：那要 import 全部测试模块（分钟级），而本闸门要能在
  每次提交前跑。参数化产生的多 node 不计——参数化变更属于**显式改动**，走 ``--update``。
- 前端：``apps/web/src/**/*.test.ts(x)`` 里 ``it(`` / ``test(``（含 ``.each`` / ``.skip`` 等）
  的行首调用数。

## 用法

    python scripts/check_test_inventory.py            # 校验（不一致 ⇒ exit 1）
    python scripts/check_test_inventory.py --update --reason "批次三十八：新增 N 例"

⚠️ 本脚本只读源码、只写 ``tests/test_inventory_snapshot.json``；不跑测试。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SNAPSHOT_PATH = _REPO_ROOT / "tests" / "test_inventory_snapshot.json"

#: 前端测试文件里的用例声明（行首，允许缩进）。
_VITEST_CASE_RE = re.compile(r"^\s*(?:it|test)(?:\.\w+)*\s*\(", re.MULTILINE)


def _iter_python_test_files() -> list[Path]:
    tests_dir = _REPO_ROOT / "tests"
    return sorted(p for p in tests_dir.rglob("test_*.py") if p.is_file())


def _iter_frontend_test_files() -> list[Path]:
    src_dir = _REPO_ROOT / "apps" / "web" / "src"
    if not src_dir.is_dir():
        return []
    return sorted(
        p
        for p in src_dir.rglob("*.test.ts*")
        if p.is_file() and p.suffix in (".ts", ".tsx")
    )


def _count_python_tests(path: Path) -> int:
    """AST 统计 ``def test_*`` 数量（含类内/嵌套定义）。

    解析失败时返回 ``-1``（调用方按「不可判」处理，绝不静默当 0）。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except SyntaxError:
        return -1
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            count += 1
    return count


def _count_frontend_tests(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    return len(_VITEST_CASE_RE.findall(text))


def _rel(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def collect_inventory() -> dict:
    """返回 ``{"python": {...}, "frontend": {...}}``；``-1`` 表示解析失败。"""
    py_files: dict[str, int] = {}
    for p in _iter_python_test_files():
        py_files[_rel(p)] = _count_python_tests(p)
    fe_files: dict[str, int] = {}
    for p in _iter_frontend_test_files():
        fe_files[_rel(p)] = _count_frontend_tests(p)
    return {
        "python": {
            "total": sum(v for v in py_files.values() if v >= 0),
            "files": py_files,
        },
        "frontend": {
            "total": sum(fe_files.values()),
            "files": fe_files,
        },
    }


def load_snapshot() -> dict:
    if not _SNAPSHOT_PATH.is_file():
        raise FileNotFoundError(
            f"快照不存在：{_SNAPSHOT_PATH}；首次使用请跑 --update --reason <理由>"
        )
    return json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))


def diff_inventory(current: dict, snapshot: dict) -> list[str]:
    """返回人类可读的差异行；空列表 = 一致。"""
    problems: list[str] = []
    for side, label in (("python", "pytest"), ("frontend", "vitest")):
        cur = current[side]
        old = snapshot.get(side) or {}
        cur_total = cur["total"]
        old_total = old.get("total", 0)
        if cur_total != old_total:
            problems.append(
                f"[{label}] 总数 {old_total} → {cur_total}（{cur_total - old_total:+d}）"
            )
        cur_files = cur["files"]
        old_files = old.get("files") or {}
        for path in sorted(set(old_files) | set(cur_files)):
            a = old_files.get(path)
            b = cur_files.get(path)
            if a == b:
                continue
            if a is None:
                problems.append(f"[{label}] 新增文件 {path}（{b} 条）")
            elif b is None:
                problems.append(f"[{label}] 文件消失 {path}（原 {a} 条）")
            else:
                problems.append(f"[{label}] {path}: {a} → {b}（{b - a:+d}）")
        # 解析失败不得静默当 0
        for path, n in cur_files.items():
            if n < 0:
                problems.append(f"[{label}] {path} 解析失败（AST SyntaxError），不可判")
    return problems


def write_snapshot(current: dict, reason: str) -> None:
    old = {}
    if _SNAPSHOT_PATH.is_file():
        try:
            old = load_snapshot()
        except json.JSONDecodeError:
            old = {}
    log = list(old.get("change_log") or [])
    log.append(
        {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "reason": reason,
            "python_total": current["python"]["total"],
            "frontend_total": current["frontend"]["total"],
        }
    )
    payload = {
        "_comment": "用例清单守恒快照（scripts/check_test_inventory.py 生成；改动必须走 --update --reason）",
        "python": current["python"],
        "frontend": current["frontend"],
        "change_log": log,
    }
    _SNAPSHOT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _ensure_utf8_stdout() -> None:
    """把标准输出切到 UTF-8（仅当流支持 reconfigure 时）。

    ⚠️ 必须在 **函数内** 调用而不是模块级：pytest 捕获期间 ``sys.stdout`` 是
    ``_pytest.capture`` 的对象，没有 ``reconfigure`` 方法（AGENTS 坑区
    「模块级 sys.stdout.reconfigure ⇒ pytest 里 import 就炸」）。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="用例清单守恒闸门")
    parser.add_argument("--update", action="store_true", help="用当前清单重写快照（必须配 --reason）")
    parser.add_argument("--reason", default="", help="变更理由（写入 change_log）")
    args = parser.parse_args(argv)

    current = collect_inventory()
    if args.update:
        if not args.reason.strip():
            print("[失败] --update 必须配 --reason（变更必须留痕）", file=sys.stderr)
            return 2
        write_snapshot(current, args.reason.strip())
        print(
            f"[已更新] pytest {current['python']['total']} 条 / "
            f"vitest {current['frontend']['total']} 条 ⇒ {_rel(_SNAPSHOT_PATH)}"
        )
        return 0

    try:
        snapshot = load_snapshot()
    except FileNotFoundError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 2

    problems = diff_inventory(current, snapshot)
    if problems:
        print("[不一致] 用例清单与快照不符：", file=sys.stderr)
        for line in problems:
            print("  - " + line, file=sys.stderr)
        print(
            "\n若这是**有意的**变更，跑：\n"
            "  python scripts/check_test_inventory.py --update --reason <一句话说明>",
            file=sys.stderr,
        )
        return 1
    print(
        f"[一致] pytest {current['python']['total']} 条 / "
        f"vitest {current['frontend']['total']} 条"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
