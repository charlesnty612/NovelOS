"""Regression 基线判定（quality-scoring-v0 §6 的 MVP 可执行子集）。

对齐 ``docs/evaluation/quality-scoring-v0.md`` §6（PRD §85「不能直接上线」），
但按 MVP 收窄口径把「分数与 Guardrail 通过率对基线」映射到现有 golden runner
（流程+结构断言）可观测的数据面上，即 **case 基线签名**：

- ``state_version``：最新 ``story_states.state_version``（结构演化程度）；
- ``delta_arrays``：observer 业务载荷中非空数组名集合（Delta 产出结构）；
- ``hooks``：最新快照 hooks 名集合（快照物化结构）；
- ``guardrails_pass``：MVP 阻断级 Guardrail（schema_validity + observer 7 数组
  结构）是否通过。

判定规则（§6.3 精神的 MVP 子集，语义详见 ``tests/evals/README.md`` §5）：

1. 任一 case 失败 → BLOCK（现状已有）；
2. 任一 case 的 ``state_version`` / ``delta_arrays`` / ``hooks`` 与基线不一致
   → BLOCK（结构回归，§6.3 条件 4 在 MVP 数据面上的映射）；
3. ``guardrails_pass`` 由基线 true 变 false → BLOCK（§6.3 条件 3）；
4. 全部一致 → PASS。

基线更新语义（取更安全的一档）：

- 首次运行（无基线文件）→ 全绿即**建基线**并 PASS（输出注明 baseline-created）；
  这一动作与 ``--check`` 无关——没有基线就无从比对，建基线是 bootstrap 而非更新；
- 基线已存在 → 默认 ``check`` 模式：不一致只报告不更新；全绿且一致也**不更新**，
  只有显式 ``--update-baseline`` 才把基线更新为本次 run；
- 任一 case 失败或存在漂移 → 无论如何**不更新**基线。

落盘（§6.4）：每次运行写 ``docs/evaluation/runs/<run_id>.json``，含 baseline 引用、
各 case 结果、drift、decision；仅 PASS 且（bootstrap 或显式更新）时写
``docs/evaluation/baseline/last_passing_run.json``。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests.evals.runner import RunResult

SCORING_VERSION = "quality-scoring-v0"
BASELINE_SEMANTICS = "structural-signature-mvp"
BASELINE_REL_PATH = "docs/evaluation/baseline/last_passing_run.json"
RUNS_DIR_REL = "docs/evaluation/runs"

# 基线签名中的关键字段（§6 判定数据面）
SIGNATURE_FIELDS = ("state_version", "delta_arrays", "hooks", "guardrails_pass")


# ---------------------------------------------------------------------------
# 基线签名
# ---------------------------------------------------------------------------


def case_signature(result: RunResult) -> dict[str, Any]:
    """从单 case 运行结果提取基线签名（供基线比对）。"""
    return {
        "state_version": int(result.state_version or 0),
        "delta_arrays": sorted(result.delta_arrays or []),
        "hooks": sorted(result.hooks or []),
        "guardrails_pass": bool(result.guardrails_pass),
    }


# ---------------------------------------------------------------------------
# 基线读写
# ---------------------------------------------------------------------------


def load_baseline(path: Path | str) -> dict[str, Any] | None:
    """读取基线文件；不存在返回 None；JSON 损坏视为无基线（调用方如实报告）。"""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def save_baseline(path: Path | str, baseline: dict[str, Any]) -> None:
    """原子写基线文件（临时文件 + os.replace，避免半截 JSON）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(baseline, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp_name, p)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def build_baseline(
    run_id: str,
    results: list[RunResult],
    *,
    date: str | None = None,
) -> dict[str, Any]:
    """由全绿结果构造基线内容（口径：{run_id, date, cases, aggregate}）。"""
    cases: dict[str, dict[str, Any]] = {}
    for r in results:
        cases[r.case_name] = case_signature(r)
    return {
        "run_id": run_id,
        "date": date or _now_iso(),
        "cases": cases,
        "aggregate": {
            "cases_passed": sum(1 for r in results if r.passed),
            "cases_total": len(results),
        },
        "scoring_version": SCORING_VERSION,
        "baseline_semantics": BASELINE_SEMANTICS,
    }


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------


def compare_signatures(
    baseline_cases: dict[str, dict[str, Any]],
    results: list[RunResult],
) -> list[dict[str, Any]]:
    """逐 case 比对基线签名；返回漂移明细（无漂移 → 空列表）。

    规则：
    - ``state_version`` / ``delta_arrays`` / ``hooks``：不一致即漂移（对称）；
    - ``guardrails_pass``：仅「基线 true → 实际 false」为漂移（§6.3 条件 3，
      pass→fail 才阻断；fail→pass 是改善，不阻断）。
    """
    drift: list[dict[str, Any]] = []
    for r in results:
        baseline_sig = baseline_cases.get(r.case_name)
        if baseline_sig is None:
            drift.append(
                {
                    "case": r.case_name,
                    "field": "<case>",
                    "baseline": "<missing>",
                    "actual": "<present>",
                    "reason": "case 不在基线中（新增 case 需重建基线）",
                }
            )
            continue
        actual = case_signature(r)
        for f in ("state_version", "delta_arrays", "hooks"):
            if baseline_sig.get(f) != actual[f]:
                drift.append(
                    {
                        "case": r.case_name,
                        "field": f,
                        "baseline": baseline_sig.get(f),
                        "actual": actual[f],
                        "reason": "结构签名与基线不一致（§6.3 条件 4 数据面映射）",
                    }
                )
        if baseline_sig.get("guardrails_pass") is True and actual["guardrails_pass"] is False:
            drift.append(
                {
                    "case": r.case_name,
                    "field": "guardrails_pass",
                    "baseline": True,
                    "actual": False,
                    "reason": "Guardrail 由 pass 变 fail（§6.3 条件 3）",
                }
            )
    return drift


def run_regression(
    results: list[RunResult],
    baseline_path: Path | str,
    runs_dir: Path | str,
    *,
    update_baseline: bool,
    run_id: str | None = None,
    trigger: str = "manual",
) -> dict[str, Any]:
    """完整回归判定流程：比对 → decision → 落盘报告 →（必要时）更新基线。

    返回报告 dict（同时已落盘 ``runs_dir/<run_id>.json``）。
    decision ∈ {"PASS", "BLOCK"}；首次建基线时 ``baseline_created=True``。
    """
    baseline_path = Path(baseline_path)
    runs_dir = Path(runs_dir)
    run_id = run_id or new_run_id()
    date = _now_iso()

    cases_passed = sum(1 for r in results if r.passed)
    cases_total = len(results)
    baseline = load_baseline(baseline_path)

    decision: str
    reasons: list[str] = []
    baseline_created = False
    baseline_updated = False
    baseline_ref: str | None = None

    if cases_passed != cases_total:
        decision = "BLOCK"
        reasons.append(f"case 失败：{cases_passed}/{cases_total} 通过（现有 runner 判定）")
    elif baseline is None:
        # 首跑无基线 → 建基线并 PASS（bootstrap，与 --check 无关）
        decision = "PASS"
        baseline_created = True
        reasons.append(
            "baseline-created：无既有基线，本次全绿结果已建为基线"
        )
    else:
        drift = compare_signatures(baseline.get("cases") or {}, results)
        if drift:
            decision = "BLOCK"
            reasons.append(f"结构回归：{len(drift)} 处基线签名漂移（详见 drift）")
        else:
            decision = "PASS"
            reasons.append(f"全部 case 与基线一致（baseline run_id={baseline.get('run_id')}）")
            if update_baseline:
                baseline_updated = True
                reasons.append("--update-baseline：基线已更新为本次 run")
            else:
                reasons.append("check 模式：基线未更新（--update-baseline 才更新）")

    if baseline is not None:
        baseline_ref = baseline.get("run_id")

    # 写基线（仅 PASS 路径；bootstrap 或显式更新）
    if decision == "PASS" and (baseline_created or baseline_updated):
        new_baseline = build_baseline(run_id, results, date=date)
        save_baseline(baseline_path, new_baseline)

    # 落盘报告（§6.4：每次运行都写）
    report: dict[str, Any] = {
        "run_id": run_id,
        "date": date,
        "trigger": trigger,
        "scoring_version": SCORING_VERSION,
        "baseline_semantics": BASELINE_SEMANTICS,
        "cases_total": cases_total,
        "cases_passed": cases_passed,
        "cases": {
            r.case_name: {
                "passed": r.passed,
                "state_version": int(r.state_version or 0),
                "delta_arrays": sorted(r.delta_arrays or []),
                "hooks": sorted(r.hooks or []),
                "guardrails_pass": bool(r.guardrails_pass),
            }
            for r in results
        },
        "baseline": {
            "path": str(baseline_path),
            "run_id": baseline_ref,
            "exists": baseline is not None,
            "created": baseline_created,
            "updated": baseline_updated,
        },
        "decision": decision,
        "decision_reasons": reasons,
        "drift": (
            compare_signatures(baseline.get("cases") or {}, results)
            if decision == "BLOCK" and baseline is not None and cases_passed == cases_total
            else []
        ),
    }
    write_report(runs_dir, report)
    return report


def new_run_id() -> str:
    """生成 run_id：``run_<UTC 时间戳(微秒)>``。"""
    return f"run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_report(runs_dir: Path | str, report: dict[str, Any]) -> Path:
    """报告落盘 ``runs_dir/<run_id>.json``；返回报告路径。"""
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    report_path = runs_dir / f"{report['run_id']}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report_path


__all__ = [
    "SIGNATURE_FIELDS",
    "case_signature",
    "load_baseline",
    "save_baseline",
    "build_baseline",
    "compare_signatures",
    "run_regression",
    "new_run_id",
    "write_report",
]
