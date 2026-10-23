"""两个评估脚本的**默认落盘位置**（2026-10-23 批次二十九 task-26 / 台账 C-3）。

缺陷形状：``eval_regression.py`` 的 ``--runs-dir`` 与 ``m1_report.py`` 的 ``--out``
默认都指向仓库内 ``docs/`` ⇒ 在只读环境（审查 / CI / 别人 clone 后跑一次）就会
**脏工作树**，且没有任何提示。

本文件钉三件事：
1. 默认模式写**系统临时目录**，仓库 ``docs/`` 无新增/改动；
2. 显式 ``--write-docs`` 才写回仓库（用 monkeypatch 假 repo root 验，不碰真 docs/）；
3. 判据落点 = 实际写出的文件路径（不是 help 文案）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import scripts.eval_regression as er
import scripts.m1_report as mr

ROOT = Path(__file__).resolve().parents[2]
REAL_DOCS_RUNS = ROOT / "docs" / "evaluation" / "runs"
REAL_DOCS_M1 = ROOT / "docs" / "evaluation" / "m1-data.md"


def _runs_snapshot() -> dict[str, int]:
    return {p.name: p.stat().st_size for p in sorted(REAL_DOCS_RUNS.glob("*"))}


def test_eval_regression_default_writes_temp_not_docs(tmp_path: Path, monkeypatch, capsys):
    before = _runs_snapshot()
    temp_runs = tmp_path / "temp_runs"
    monkeypatch.setattr(er, "DEFAULT_RUNS_DIR", temp_runs)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "eval_regression.py",
            "--golden-dir",
            str(tmp_path / "golden"),
            "--baseline",
            str(tmp_path / "baseline.json"),
            "--run-id",
            "run_task26_default",
        ],
    )
    rc = er.main()
    out = capsys.readouterr().out
    assert rc == 0, out
    written = temp_runs / "run_task26_default.json"
    assert written.is_file(), f"默认应写临时目录，实际未写：{written}"
    assert str(written) in out, out
    assert _runs_snapshot() == before, "默认模式不得在仓库 docs/evaluation/runs 新增文件"


def test_eval_regression_write_docs_flag_writes_repo_docs(tmp_path: Path, monkeypatch, capsys):
    fake_repo = tmp_path / "repo"
    (fake_repo / "docs" / "evaluation").mkdir(parents=True)
    monkeypatch.setattr(er, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(er, "DOCS_RUNS_DIR", fake_repo / "docs" / "evaluation" / "runs")
    before = _runs_snapshot()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "eval_regression.py",
            "--golden-dir",
            str(tmp_path / "golden"),
            "--baseline",
            str(tmp_path / "baseline.json"),
            "--run-id",
            "run_task26_docs",
            "--write-docs",
        ],
    )
    rc = er.main()
    out = capsys.readouterr().out
    assert rc == 0, out
    assert (fake_repo / "docs" / "evaluation" / "runs" / "run_task26_docs.json").is_file()
    assert _runs_snapshot() == before, "--write-docs 在假 repo 上验证，真 docs 不得被动到"


def test_m1_report_default_writes_temp_not_docs(tmp_path: Path, monkeypatch, capsys):
    before = REAL_DOCS_M1.stat().st_mtime_ns if REAL_DOCS_M1.exists() else None
    temp_out = tmp_path / "temp_m1" / "m1-data.md"
    monkeypatch.setattr(mr, "DEFAULT_OUT_PATH", temp_out)
    monkeypatch.setattr(
        sys, "argv", ["m1_report.py", "--data-dir", str(tmp_path / "data")]
    )
    rc = mr.main()
    out = capsys.readouterr().out
    assert rc == 0, out
    assert temp_out.is_file(), f"默认应写临时目录，实际未写：{temp_out}"
    assert f"written: {temp_out.resolve()}" in out, out
    now = REAL_DOCS_M1.stat().st_mtime_ns if REAL_DOCS_M1.exists() else None
    assert now == before, "默认模式不得改写仓库 docs/evaluation/m1-data.md"


def test_m1_report_write_docs_flag_writes_repo_docs(tmp_path: Path, monkeypatch, capsys):
    fake_repo = tmp_path / "repo"
    (fake_repo / "docs" / "evaluation").mkdir(parents=True)
    fake_out = fake_repo / "docs" / "evaluation" / "m1-data.md"
    before = REAL_DOCS_M1.stat().st_mtime_ns if REAL_DOCS_M1.exists() else None
    monkeypatch.setattr(mr, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(mr, "DOCS_OUT_PATH", fake_out)
    monkeypatch.setattr(
        sys, "argv", ["m1_report.py", "--data-dir", str(tmp_path / "data"), "--write-docs"]
    )
    rc = mr.main()
    out = capsys.readouterr().out
    assert rc == 0, out
    assert fake_out.is_file()
    now = REAL_DOCS_M1.stat().st_mtime_ns if REAL_DOCS_M1.exists() else None
    assert now == before, "--write-docs 在假 repo 上验证，真 docs 不得被动到"
