"""Regression 基线判定单测（quality-scoring-v0 §6 MVP 子集）。

用「临时 baseline + 构造的 RunResult 观察字段」验证判定语义，不跑完整 workflow：

1. 首跑无基线 → baseline-created（建基线并 PASS）；
2. 结构漂移（state_version / delta_arrays / hooks 任一不一致）→ BLOCK 且基线不更新；
3. Guardrail pass→fail → BLOCK 且基线不更新；
4. 一致 + 默认 check 模式 → PASS 但基线不更新；
5. 一致 + --update-baseline → PASS 且基线更新；
6. case 失败 → BLOCK（既有 runner 判定透传）且基线不更新。

报告落盘（runs/<run_id>.json）由 :func:`run_regression` 每次调用写入临时 runs_dir，
测试同时断言报告内容。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.evals.regression_baseline import (
    build_baseline,
    case_signature,
    compare_signatures,
    load_baseline,
    run_regression,
)
from tests.evals.runner import RunResult

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_result(
    case_name: str = "ch001_basic",
    *,
    passed: bool = True,
    state_version: int = 3,
    delta_arrays: list[str] | None = None,
    hooks: list[str] | None = None,
    guardrails_pass: bool = True,
) -> RunResult:
    return RunResult(
        case_name=case_name,
        passed=passed,
        state_version=state_version,
        delta_arrays=list(delta_arrays or ["new_events", "new_hooks"]),
        hooks=list(hooks or ["识海古镜传承", "周元的窥伺", "禁地异象惊宗门"]),
        guardrails_pass=guardrails_pass,
    )


@pytest.fixture()
def workdir(tmp_path: Path) -> tuple[Path, Path]:
    """返回 (baseline_path, runs_dir) 两个临时路径。"""
    return tmp_path / "baseline" / "last_passing_run.json", tmp_path / "runs"


# ---------------------------------------------------------------------------
# 首跑无基线
# ---------------------------------------------------------------------------


def test_first_run_creates_baseline_and_passes(workdir: tuple[Path, Path]) -> None:
    baseline_path, runs_dir = workdir
    assert not baseline_path.exists()

    report = run_regression(
        [_make_result()],
        baseline_path,
        runs_dir,
        update_baseline=False,  # 无基线时与 --check 无关，bootstrap 建基线
    )

    assert report["decision"] == "PASS"
    assert report["baseline"]["created"] is True
    assert report["baseline"]["updated"] is False
    assert report["baseline"]["exists"] is False  # 判定时基线尚不存在
    assert baseline_path.exists(), "首次运行必须落盘基线"
    assert any("baseline-created" in r for r in report["decision_reasons"])

    saved = load_baseline(baseline_path)
    assert saved is not None
    assert saved["run_id"] == report["run_id"]
    assert saved["cases"]["ch001_basic"] == case_signature(_make_result())
    assert saved["aggregate"] == {"cases_passed": 1, "cases_total": 1}

    # 报告落盘（§6.4）
    report_path = runs_dir / f"{report['run_id']}.json"
    assert report_path.exists()
    assert report["decision"] == "PASS"


# ---------------------------------------------------------------------------
# 结构漂移 → BLOCK 且基线不更新
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate, expected_field",
    [
        (lambda r: setattr(r, "state_version", 4), "state_version"),
        (lambda r: setattr(r, "delta_arrays", ["new_events"]), "delta_arrays"),
        (
            lambda r: setattr(
                r, "hooks", ["识海古镜传承", "周元的窥伺", "禁地异象惊宗门", "新钩子"]
            ),
            "hooks",
        ),
    ],
    ids=["state_version", "delta_arrays", "hooks"],
)
def test_structural_drift_blocks_and_keeps_baseline(
    workdir: tuple[Path, Path], mutate, expected_field: str
) -> None:
    baseline_path, runs_dir = workdir
    # 先建基线（模拟上次全绿）
    first = run_regression(
        [_make_result()], baseline_path, runs_dir, update_baseline=True
    )
    assert first["decision"] == "PASS"
    assert baseline_path.exists()
    baseline_before = load_baseline(baseline_path)

    drifted = _make_result()
    mutate(drifted)
    report = run_regression(
        [drifted], baseline_path, runs_dir, update_baseline=True
    )

    assert report["decision"] == "BLOCK"
    assert any("结构回归" in r for r in report["decision_reasons"])
    assert report["drift"], "BLOCK 报告必须携带 drift 明细"
    assert report["drift"][0]["field"] == expected_field
    # 基线不得被更新（漂移时无论 --update-baseline 与否都不写）
    assert load_baseline(baseline_path) == baseline_before
    assert report["baseline"]["updated"] is False


def test_guardrail_pass_to_fail_blocks_and_keeps_baseline(
    workdir: tuple[Path, Path]
) -> None:
    baseline_path, runs_dir = workdir
    first = run_regression(
        [_make_result()], baseline_path, runs_dir, update_baseline=True
    )
    assert first["decision"] == "PASS"
    baseline_before = load_baseline(baseline_path)

    report = run_regression(
        [_make_result(guardrails_pass=False)],
        baseline_path,
        runs_dir,
        update_baseline=True,
    )

    assert report["decision"] == "BLOCK"
    assert any("结构回归" in r for r in report["decision_reasons"])
    assert report["drift"][0]["field"] == "guardrails_pass"
    assert "Guardrail 由 pass 变 fail" in report["drift"][0]["reason"]
    assert load_baseline(baseline_path) == baseline_before


def test_failed_case_blocks_and_keeps_baseline(workdir: tuple[Path, Path]) -> None:
    baseline_path, runs_dir = workdir
    first = run_regression(
        [_make_result()], baseline_path, runs_dir, update_baseline=True
    )
    assert first["decision"] == "PASS"
    baseline_before = load_baseline(baseline_path)

    report = run_regression(
        [_make_result(passed=False, state_version=2)],
        baseline_path,
        runs_dir,
        update_baseline=True,
    )

    assert report["decision"] == "BLOCK"
    assert any("case 失败" in r for r in report["decision_reasons"])
    assert load_baseline(baseline_path) == baseline_before


# ---------------------------------------------------------------------------
# 一致 → PASS；基线更新语义
# ---------------------------------------------------------------------------


def test_consistent_check_mode_passes_without_updating_baseline(
    workdir: tuple[Path, Path]
) -> None:
    baseline_path, runs_dir = workdir
    first = run_regression(
        [_make_result()], baseline_path, runs_dir, update_baseline=True
    )
    assert first["decision"] == "PASS"
    baseline_before = load_baseline(baseline_path)

    report = run_regression(
        [_make_result()], baseline_path, runs_dir, update_baseline=False
    )

    assert report["decision"] == "PASS"
    assert report["baseline"]["updated"] is False
    assert any("check 模式" in r for r in report["decision_reasons"])
    # 语义：check 模式不更新基线（即使全绿一致）
    assert load_baseline(baseline_path) == baseline_before


def test_consistent_update_mode_updates_baseline(workdir: tuple[Path, Path]) -> None:
    baseline_path, runs_dir = workdir
    first = run_regression(
        [_make_result(state_version=3)],
        baseline_path,
        runs_dir,
        update_baseline=True,
    )
    assert first["decision"] == "PASS"
    old_run_id = load_baseline(baseline_path)["run_id"]

    second = run_regression(
        [_make_result(state_version=3)],  # 签名与基线一致 → 无漂移
        baseline_path,
        runs_dir,
        update_baseline=True,
    )

    assert second["decision"] == "PASS"
    assert second["baseline"]["updated"] is True
    saved = load_baseline(baseline_path)
    assert saved["run_id"] == second["run_id"]
    assert saved["run_id"] != old_run_id
    assert saved["cases"]["ch001_basic"]["state_version"] == 3


# ---------------------------------------------------------------------------
# compare_signatures 纯函数
# ---------------------------------------------------------------------------


def test_compare_signatures_no_drift() -> None:
    r = _make_result()
    baseline_cases = {"ch001_basic": case_signature(r)}
    assert compare_signatures(baseline_cases, [r]) == []


def test_compare_signatures_guardrail_improvement_is_not_drift() -> None:
    """fail→pass 是改善，不阻断（§6.3 条件 3 只阻断 pass→fail）。"""
    baseline_cases = {"ch001_basic": case_signature(_make_result(guardrails_pass=False))}
    improved = _make_result(guardrails_pass=True)
    assert compare_signatures(baseline_cases, [improved]) == []


def test_compare_signatures_missing_case_is_drift() -> None:
    r = _make_result()
    assert compare_signatures({}, [r]) != []


# ---------------------------------------------------------------------------
# 建基线内容
# ---------------------------------------------------------------------------


def test_build_baseline_shape() -> None:
    baseline = build_baseline("run_x", [_make_result(), _make_result("ch002_alt")])
    assert baseline["run_id"] == "run_x"
    assert baseline["scoring_version"] == "quality-scoring-v0"
    assert set(baseline["cases"].keys()) == {"ch001_basic", "ch002_alt"}
    assert baseline["aggregate"] == {"cases_passed": 2, "cases_total": 2}
