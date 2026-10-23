"""Regression Eval CLI 入口（Sprint 4-B + §6 基线判定）。

调用 :func:`tests.evals.runner.run_all` 跑 ``tests/evals/golden`` 下全部 case，
逐项打印断言明细；再按 ``tests.evals.regression_baseline.run_regression`` 做
§6 基线判定（结构签名比对），并落盘 ``<runs_dir>/<run_id>.json``。

⚠️ **默认不写仓库**（2026-10-23 批次二十九 task-26 / 台账 C-3）：``--runs-dir`` 缺省时
报告写**系统临时目录**（``<tempdir>/novelos_eval_runs``），只有显式
``--write-docs`` 才写**痕迹仓** ``docs/evaluation/runs``（不是软件仓！见下）。旧默认直接写仓库 ``docs/``，
只读环境跑一次就脏工作树。

⚠️ **痕迹仓缺失 ⇒ 拒绝写**（2026-10-23 批次三十 / 对抗审查 B1·B2 + 台账 D-19）：

- **痕迹仓根解析的最后一档不再是软件仓自身**：``--artifacts-dir`` >
  ``NOVELOS_ARTIFACTS_DIR`` > 同级目录 ``<repo父目录>/NovelOS-Artifacts``；
  **都找不到就明确失败**（退出码 3）并说明原因——旧行为（回落到软件仓）正是要防的落点
  （新克隆 / CI / 换机拿不到痕迹仓 ⇒ 报告含真实 ``prj_``/``ch_`` id 与正文片段
  写回软件仓 ⇒ 进 GitHub 历史，不可撤回）。
- **基线默认落点随痕迹仓走**：``<痕迹仓>/docs/evaluation/baseline/last_passing_run.json``
  （**不再**是软件仓 ``docs/evaluation/baseline/…``——那个路径下只有 ``.gitkeep``，
  基线文件从未被跟踪 ⇒ 旧默认每次都走「无基线 → 建基线 → PASS」，把**首次出现的
  结构漂移静默吸收**，还会把新基线写回软件仓里**无 gitignore 规则**的路径）。
- **守卫（D-19）**：待写路径落在软件仓根下的非 ``data/``、非 ``tmp/`` 位置 ⇒ 拒绝
  （退出码 2）；逃生口 ``--allow-software-repo-out``（默认关）须由用户显式承担。

判定与基线更新语义（详见 ``tests/evals/README.md`` §5）：

- 首跑无基线 → 建基线并 PASS（输出注明 baseline-created）；**基线只建在痕迹仓**，
  痕迹仓不可用时**拒绝建基线**（退出码 3）而不是建到软件仓；
- 有基线 → 默认 check 模式：不一致只报告不更新（exit 1）；全绿且一致也不更新，
  仅显式 ``--update-baseline`` 才更新基线；
- 任一 case 失败或存在结构漂移 → BLOCK，基线永不更新。

用法：
    python scripts/eval_regression.py
    python scripts/eval_regression.py --golden-dir tests/evals/golden
    python scripts/eval_regression.py --update-baseline
    python scripts/eval_regression.py --check  # 显式 check（默认即 check）
    python scripts/eval_regression.py --run-id run_manual_20260823
    python scripts/eval_regression.py --write-docs  # 显式写痕迹仓 docs/evaluation/runs
    python scripts/eval_regression.py --artifacts-dir D:/zcodeproject/NovelOS-Artifacts
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_DIR = Path(tempfile.gettempdir()) / "novelos_eval_runs"

try:  # 正常路径：仓库根在 sys.path（包导入 / pytest pythonpath=["."]）
    from scripts.guard_repo_write_path import (
        ARTIFACTS_BASELINE_RELPATH,
        ARTIFACTS_RUNS_RELPATH,
        EXIT_ARTIFACTS_UNAVAILABLE,
        EXIT_WRITE_PATH_REJECTED,
        ArtifactsResolution,
        RepoWritePathError,
        check_write_path,
        format_artifacts_unavailable,
        resolve_artifacts_root,
    )
except ImportError:  # pragma: no cover —— 直接 `python scripts/eval_regression.py` 时
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from guard_repo_write_path import (  # type: ignore[no-redef]
        ARTIFACTS_BASELINE_RELPATH,
        ARTIFACTS_RUNS_RELPATH,
        EXIT_ARTIFACTS_UNAVAILABLE,
        EXIT_WRITE_PATH_REJECTED,
        ArtifactsResolution,
        RepoWritePathError,
        check_write_path,
        format_artifacts_unavailable,
        resolve_artifacts_root,
    )


def _root_from_runs_dir(runs_dir: Path | None) -> Path | None:
    """由 ``<root>/docs/evaluation/runs`` 上溯出痕迹仓根；形态不符 ⇒ ``None``。

    存在的理由：``DOCS_RUNS_DIR`` 是**可被 monkeypatch 的常量**（既有测试用它替身
    痕迹仓），而痕迹仓根必须与 ``--write-docs`` 的落点**同源** ⇒ 由它反推根，
    保证「报告落点」与「基线落点」永远同根。
    """
    if runs_dir is None:
        return None
    parts = Path(runs_dir).parts
    if len(parts) < 5:
        return None
    return Path(*parts[:-3])


def _artifacts_root(explicit: str | None = None) -> ArtifactsResolution:
    """痕迹仓根目录（AGENTS.md 硬规则 5：操作痕迹在 NovelOS-Artifacts）。

    解析顺序：``--artifacts-dir`` → 环境变量 ``NOVELOS_ARTIFACTS_DIR`` → 软件仓的
    **同级目录** ``<repo父目录>/NovelOS-Artifacts`` → 由 ``DOCS_RUNS_DIR`` 反推的根
    （导入期已解析；测试可 monkeypatch 该常量）。

    ⚠️ **找不到就是找不到**（``root=None``）——**绝不**回落到软件仓自身。
    """
    res = resolve_artifacts_root(explicit, repo_root=REPO_ROOT)
    if res.ok:
        return res
    const_root = _root_from_runs_dir(DOCS_RUNS_DIR)
    if const_root is not None:
        return ArtifactsResolution(const_root, "constant", "由 DOCS_RUNS_DIR 上溯得出")
    return res


#: 痕迹仓根（导入期解析；``None`` = 找不到痕迹仓 ⇒ **拒绝写**，不回落到软件仓）。
ARTIFACTS_ROOT: Path | None = resolve_artifacts_root(repo_root=REPO_ROOT).root

#: ``--write-docs`` 的落点：**痕迹仓**，不是软件仓（``None`` = 痕迹仓不可用）。
#: ⚠️ 2026-10-23 全面审查：运行报告含真实 prj_/ch_ id 与书籍正文片段，
#: 写进软件仓就会进 GitHub 历史（**不可撤回**）⇒ 必须落痕迹仓。
DOCS_RUNS_DIR: Path | None = (
    ARTIFACTS_ROOT / ARTIFACTS_RUNS_RELPATH if ARTIFACTS_ROOT is not None else None
)

#: 基线默认落点：**痕迹仓** ``docs/evaluation/baseline/last_passing_run.json``。
#: ⚠️ 旧默认（软件仓同相对路径）下该文件从未被跟踪 ⇒ 每次运行都退化成
#: 「无基线 → 建基线 → PASS」，首次出现的结构漂移会被静默吸收。
DEFAULT_BASELINE_PATH: Path | None = (
    ARTIFACTS_ROOT / ARTIFACTS_BASELINE_RELPATH if ARTIFACTS_ROOT is not None else None
)


def _err(message: str) -> None:
    """错误/告警一律走 stderr（stdout 只留机器可读的结果行）。"""
    print(message, file=sys.stderr)


def _artifacts_unavailable_message(res: ArtifactsResolution) -> str:
    """痕迹仓不可用时的**明确**说明（含逃生口；四个落盘脚本共用同一措辞）。"""
    return format_artifacts_unavailable(
        res,
        subject=(
            "运行报告含真实 prj_/ch_ id 与书籍正文片段 ⇒ 必须落痕迹仓，"
            "不能落软件仓（落进软件仓 = 进 GitHub 历史，不可撤回）。"
        ),
    )


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
        default=None,
        help="基线文件路径（默认 <痕迹仓>/docs/evaluation/baseline/last_passing_run.json；"
        "显式给出时按该路径读取/更新，写入仍受仓库守卫约束）",
    )
    parser.add_argument(
        "--runs-dir",
        default=None,
        help="报告落盘目录（默认系统临时目录 <tempdir>/novelos_eval_runs；"
        "显式 --write-docs 时写痕迹仓 docs/evaluation/runs）",
    )
    parser.add_argument(
        "--write-docs",
        action="store_true",
        help="把报告写**痕迹仓** docs/evaluation/runs（默认写系统临时目录，避免脏工作树）",
    )
    parser.add_argument(
        "--artifacts-dir",
        default=None,
        help="显式指定痕迹仓根目录（覆盖 NOVELOS_ARTIFACTS_DIR 与同级目录探测）",
    )
    parser.add_argument(
        "--allow-software-repo-out",
        action="store_true",
        help="逃生口：痕迹仓不可用时接受把痕迹写回软件仓（默认关；风险自负）",
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

    # ------------------------------------------------------------------
    # 落点解析（台账 C-3 + D-19 + 对抗审查 B1/B2）：先定落点，再干活
    # ------------------------------------------------------------------
    artifacts = _artifacts_root(args.artifacts_dir)
    allow_repo_out = bool(args.allow_software_repo_out)

    runs_dir: Path | None
    if args.runs_dir:
        runs_dir = Path(args.runs_dir)
    elif args.write_docs:
        runs_dir = DOCS_RUNS_DIR
    else:
        runs_dir = DEFAULT_RUNS_DIR

    # 显式痕迹仓（--artifacts-dir / 环境变量 / 运行时才存在的同级目录）覆盖导入期常量
    if args.write_docs and artifacts.source in ("cli", "env", "sibling"):
        runs_dir = artifacts.root / ARTIFACTS_RUNS_RELPATH

    if args.write_docs and runs_dir is None:
        if not allow_repo_out:
            _err(_artifacts_unavailable_message(artifacts))
            return EXIT_ARTIFACTS_UNAVAILABLE
        runs_dir = REPO_ROOT / ARTIFACTS_RUNS_RELPATH
        _err(
            f"[警告] --allow-software-repo-out 已开启：本次运行痕迹将落软件仓 {runs_dir}"
            "（可能被误提交 ⇒ 进 GitHub 历史不可撤回）。"
        )

    # 基线落点：显式 --baseline > 痕迹仓默认（**不再**是软件仓路径）
    baseline_path: Path | None
    if args.baseline:
        baseline_path = Path(args.baseline)
    elif artifacts.ok:
        baseline_path = artifacts.root / ARTIFACTS_BASELINE_RELPATH
    elif allow_repo_out:
        baseline_path = REPO_ROOT / ARTIFACTS_BASELINE_RELPATH
        _err(
            "[警告] --allow-software-repo-out 已开启：基线将落软件仓 "
            f"{baseline_path}（缺基线时结构漂移闸门会退化为『与自产基线比对』）。"
        )
    else:
        _err(
            "[拒绝建基线] 痕迹仓不可用 ⇒ 无法定位基线路径；"
            "**拒绝把基线建到软件仓**。\n"
            f"             {artifacts.detail}。\n"
            "             理由：没有钉住的基线时，结构漂移闸门会退化为"
            "『与上一次自产基线比对』⇒ 首次出现的漂移会被静默吸收。\n"
            "             逃生口：--artifacts-dir <path> | 设置 NOVELOS_ARTIFACTS_DIR | "
            "--baseline <已有基线路径>（只读比对，不写）"
        )
        return EXIT_ARTIFACTS_UNAVAILABLE

    # 守卫（D-19）：待写路径不得落在软件仓根下非 data/tmp 处
    write_targets: list[Path] = [runs_dir] if runs_dir is not None else []
    if args.update_baseline or not baseline_path.exists():
        write_targets.append(baseline_path)
    for target in write_targets:
        try:
            check_write_path(target, allow_repo_root=allow_repo_out)
        except RepoWritePathError as exc:
            _err(f"[拒绝写入] {exc}")
            return EXIT_WRITE_PATH_REJECTED

    # 让 runner 可被 import（scripts/ 在 repo 根，与 tests 同级）
    repo_root = REPO_ROOT
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
    baseline_existed = load_baseline(baseline_path) is not None
    report = run_regression(
        results,
        baseline_path,
        runs_dir,
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
    print(f"report: {runs_dir / (report['run_id'] + '.json')}")
    print("-" * 72)

    return 0 if report["decision"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
