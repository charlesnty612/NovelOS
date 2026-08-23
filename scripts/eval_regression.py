"""Regression Eval CLI 入口（Sprint 4-B）。

调用 :func:`tests.evals.runner.run_all` 跑 ``tests/evals/golden`` 下全部 case，
逐项打印断言明细；任一 case 失败 → exit 1。

用法：
    python scripts/eval_regression.py
    python scripts/eval_regression.py --golden-dir tests/evals/golden
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NovelOS Regression Eval —— 跑 golden 数据集全部 case"
    )
    parser.add_argument(
        "--golden-dir",
        default="tests/evals/golden",
        help="golden case 根目录（默认 tests/evals/golden）",
    )
    args = parser.parse_args()

    # 让 runner 可被 import（scripts/ 在 repo 根，与 tests 同级）
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from tests.evals.runner import run_all

    golden_dir = Path(args.golden_dir)
    passed, results = run_all(golden_dir)

    print("=" * 72)
    print(f"Regression Eval —— cases={len(results)} passed={passed}")
    print("=" * 72)

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"\n[{status}] case: {r.case_name}")
        if r.error:
            print(f"  error: {r.error}")
            if r.traceback:
                print(f"  traceback:\n{r.traceback}")
                continue
        for c in r.checks:
            mark = "PASS" if c.passed else "FAIL"
            print(f"    [{mark}] {c.check}")
            if not c.passed or c.detail and c.detail not in ("all COMPLETED", "all non-empty", "schema_validity=pass"):
                print(f"           detail: {c.detail}")

    print()
    print("-" * 72)
    print(f"summary: {passed}/{len(results)} cases passed")
    print("-" * 72)

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
