"""Golden Regression 参数化测试（Sprint 4-B）。

发现 ``tests/evals/golden`` 下全部 case 子目录（每个含 ``input.json``），每 case
生成一个 :func:`pytest` 参数化测试函数；测试体直接调
:func:`tests.evals.runner.run_case`，断言 ``passed == True`` 与全部 ``CheckResult.passed``。

不依赖 CLI（``scripts/eval_regression.py``）—— CI 可单独跑本测试。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 让 ``tests.evals.runner`` 可被 import（pytest 已在 rootdir）
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.evals.runner import discover_cases, run_case  # noqa: E402

GOLDEN_DIR = _REPO_ROOT / "tests" / "evals" / "golden"


def _case_ids() -> list[str]:
    """参数化 ID 列表（按 case 子目录名）。"""
    return [d.name for d in discover_cases(GOLDEN_DIR)]


@pytest.mark.parametrize("case_name", _case_ids())
def test_golden_case(case_name: str) -> None:
    """单 case 跑回归；任一断言失败即 pytest fail。"""
    case_dir = GOLDEN_DIR / case_name
    result = run_case(case_dir)
    # runner 自身异常 → 报告 + traceback
    if result.error:
        pytest.fail(
            f"runner exception in case {case_name!r}: {result.error}\n"
            f"{result.traceback or ''}"
        )
    # 逐项断言
    failed_checks = [c for c in result.checks if not c.passed]
    if failed_checks:
        details = "\n".join(
            f"  - [{c.check}] {c.detail}" for c in failed_checks
        )
        pytest.fail(
            f"golden case {case_name!r} failed {len(failed_checks)} check(s):\n{details}"
        )
    assert result.passed, f"case {case_name!r} result.passed should be True"


def test_golden_dir_has_at_least_one_case() -> None:
    """哨兵：保证 ``tests/evals/golden`` 至少落地一个种子 case。"""
    cases = discover_cases(GOLDEN_DIR)
    assert cases, (
        f"no golden cases discovered under {GOLDEN_DIR}; "
        f"S4-B 至少需落地一个种子 case（如 ch001_basic）"
    )
