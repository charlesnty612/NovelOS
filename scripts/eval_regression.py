"""Regression Eval CLI 入口（Sprint 4-B + §6 基线判定）。

调用 :func:`tests.evals.runner.run_all` 跑 ``tests/evals/golden`` 下全部 case，
逐项打印断言明细；再按 ``tests/evals/regression_baseline.run_regression`` 做
§6 基线判定（结构签名比对），并落盘 ``docs/evaluation/runs/<run_id>.json``。

判定与基线更新语义（详见 ``tests/evals/README.md`` §5）：

- 首跑无基线 → 建基线并 PASS（输出注明 baseline-created）；
- 有基线 → 默认 check 模式：不一致只报告不更新（exit 1）；全绿且一致也不更新，
  仅显式 ``--update-baseline`` 才更新基线；
- 任一 case 失败或存在结构漂移 → BLOCK，基线永不更新。

用法：
    python scripts/eval_regression.py
    python scripts/eval_regression.py --golden-dir tests/evals/golden
    python scripts/eval_regression.py --update-baseline
    python scripts/eval_regression.py --check  # 显式 check（默认即 check）
    python scripts/eval_regression.py --run-id run_manual_20260823
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _print_results(results) -> None:
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
        # 基线签名（§6 判定数据面）
        print(
            f"  signature: state_version={r.state_version} "
            f"delta_arrays={sorted(r.delta_arrays)} hooks={sorted(r.hooks)} "
            f"guardrails_pass={r.guardrails_pass}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NovelOS Regression Eval —— golden 数据集回归 + §6 基线判定"
    )
    parser.add_argument(
        "--golden-dir",
        default="tests/evals/golden",
        help="golden case 根目录（默认 tests/evals/golden）",
    )
    parser.add_argument(
        "--baseline",
        default="docs/evaluation/baseline/last_passing_run.json",
        help="基线文件路径（默认 docs/evaluation/baseline/last_passing_run.json）",
    )
    parser.add_argument(
        "--runs-dir",
        default="docs/evaluation/runs",
        help="报告落盘目录（默认 docs/evaluation/runs）",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="全绿且与基线一致时，把基线更新为本次 run（默认不更新）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="显式 check 模式（默认即 check：只报告不更新基线）",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="本次 run 的 run_id（默认自动生成 run_<ts>）",
    )
    args = parser.parse_args()

    # 让 runner 可被 import（scripts/ 在 repo 根，与 tests 同级）
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from tests.evals.regression_baseline import load_baseline, run_regression
    from tests.evals.runner import run_all

    golden_dir = Path(args.golden_dir)
    passed, results = run_all(golden_dir)

    print("=" * 72)
    print(f"Regression Eval —— cases={len(results)} passed={passed}")
    print("=" * 72)

    _print_results(results)

    # §6 基线判定 + 报告落盘
    baseline_path = Path(args.baseline)
    baseline_existed = load_baseline(baseline_path) is not None
    report = run_regression(
        results,
        baseline_path,
        Path(args.runs_dir),
        update_baseline=args.update_baseline,
        run_id=args.run_id,
        trigger="cli",
    )

    print()
    print("-" * 72)
    print(f"summary: {passed}/{len(results)} cases passed")
    print(
        f"baseline: {'EXISTS (run_id=' + str(report['baseline']['run_id']) + ')' if baseline_existed else 'NONE'}"
    )
    if report["baseline"]["created"]:
        print("baseline-created: 无既有基线，本次全绿结果已建为基线（PASS）")
    print(f"decision: {report['decision']}")
    for reason in report["decision_reasons"]:
        print(f"  - {reason}")
    for d in report["drift"]:
        print(f"  drift: case={d['case']} field={d['field']} "
              f"baseline={d['baseline']} actual={d['actual']} — {d['reason']}")
    print(f"report: {Path(args.runs_dir) / (report['run_id'] + '.json')}")
    print("-" * 72)

    return 0 if report["decision"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
