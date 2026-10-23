"""脚本落盘守卫 + 痕迹仓落点边界的回归测试（2026-10-23 批次三十 / 对抗审查 B1·B2 + 台账 D-19）。

缺陷形状（两条同族）：

1. **B1**：``scripts/eval_regression.py`` / ``scripts/m1_report.py`` 的痕迹仓根解析
   **最后一档回退就是软件仓自身** ⇒ 在**新克隆 / CI / 换机**（拿不到痕迹仓）上跑一次
   ``--write-docs``，就把含真实 ``prj_``/``ch_`` id 与正文片段的报告写回软件仓
   （``.gitignore`` 挡得住 ``git add``，**挡不住落盘**）。
2. **B2**：``eval_regression.py`` 的默认 ``--baseline`` 仍指向软件仓，而那个路径下只有
   ``.gitkeep``（基线文件从未被跟踪）⇒ **每次运行都走「无基线 → 建基线 → PASS」**
   ⇒ 结构漂移闸门退化为「与上一次自产基线比对」，**首次出现的漂移被静默吸收**；
   同时把新基线写回软件仓里**无 gitignore 规则**的路径。
3. **D-19**：台账登记项「脚本『拒写仓库根下非 data/tmp 路径』」的另一半**未实现**
   ⇒ 本文件同时钉住可复用守卫 ``scripts/guard_repo_write_path.py``。

本文件钉住：

- 守卫的**真实判定**：仓根下的 ``foo.md`` 被拒且**不产生文件**；``data/``、``tmp/``
  与仓外路径放行；
- 痕迹仓根解析**绝不回落到软件仓**（找不到 ⇒ ``root is None``）；
- 隔离目录（同级无 ``NovelOS-Artifacts``）里跑两个脚本 ⇒ **拒绝写** + stderr 说明
  原因 + 退出码非 0 + **隔离目录内零新增文件**；
- 基线**落在痕迹仓根下**（不是软件仓），且软件仓内零新增文件；
- 显式逃生口 ``--allow-software-repo-out`` 仍然可用（默认关）。

⚠️ 隔离测试用**真实 subprocess**跑脚本（不是 import 后调函数）：拒绝/退出码/stderr
必须来自**真实的 CLI 行为**。子进程只读本仓的 ``tests/evals/golden``，写落点全在
``tmp_path`` 内；**零真实 LLM 调用、零内容仓接触、零 git 写操作**。
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
GOLDEN_DIR = REPO_ROOT / "tests" / "evals" / "golden"
EVAL_SCRIPT = SCRIPTS_DIR / "eval_regression.py"
M1_SCRIPT = SCRIPTS_DIR / "m1_report.py"
M2_JUDGE_SCRIPT = SCRIPTS_DIR / "m2_judge.py"
REAL_LLM_SCRIPT = SCRIPTS_DIR / "real_llm_e2e.py"
GUARD_SCRIPT = SCRIPTS_DIR / "guard_repo_write_path.py"

EXIT_WRITE_PATH_REJECTED = 2
EXIT_ARTIFACTS_UNAVAILABLE = 3


@pytest.fixture()
def guard():
    """导入被测守卫模块（``scripts`` 是命名空间包，仓库根在 ``pythonpath`` 上）。"""
    return importlib.import_module("scripts.guard_repo_write_path")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _snapshot(root: Path) -> set[str]:
    """目录内**全部文件**的相对路径集合（用于「零新增文件」断言）。

    排除 ``__pycache__``/``*.pyc``：那是解释器导入字节码的产物，**不是**脚本产物
    （要判的是「脚本有没有把东西写进仓里」）。
    """
    if not root.exists():
        return set()
    out: set[str] = set()
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        out.add(str(p.relative_to(root)))
    return out


def _run_script(
    script: Path,
    args: list[str],
    *,
    cwd: Path | None = None,
    unset_env: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """跑脚本 CLI（真实 subprocess）⇒ 返回 ``CompletedProcess``。

    环境：``PYTHONPATH`` 指向本仓（子进程要 import ``tests.evals.*``），
    且**清掉** ``NOVELOS_ARTIFACTS_DIR``（否则本机的痕迹仓会掩盖「找不到痕迹仓」这条路径）。
    ``unset_env`` 额外清掉的变量（如 ``MINIMAX_API_KEY``，保证子进程不可能发起真实调用）。
    """
    env = dict(os.environ)
    env.pop("NOVELOS_ARTIFACTS_DIR", None)
    for name in unset_env or []:
        env.pop(name, None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd or REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )


def _isolated_copy(tmp_path: Path, names: list[str]) -> Path:
    """把脚本复制到隔离目录 ``<iso>/scripts/``（模拟「新克隆、无痕迹仓」的机器）。

    隔离目录的**同级**故意不放 ``NovelOS-Artifacts`` ⇒ 痕迹仓探测必然失败。
    """
    iso = tmp_path / "isolated" / "NovelOS"
    (iso / "scripts").mkdir(parents=True)
    for name in names:
        shutil.copy2(SCRIPTS_DIR / name, iso / "scripts" / name)
    assert not (iso.parent / "NovelOS-Artifacts").exists(), "隔离前提：同级不得有痕迹仓"
    assert not (iso / "NovelOS-Artifacts").exists()
    return iso


# ---------------------------------------------------------------------------
# D-19：写路径守卫（真实判定）
# ---------------------------------------------------------------------------


def test_guard_rejects_file_at_repo_root_and_writes_nothing(guard) -> None:
    """仓库根下的 ``foo.md`` 必须被拒，且**不得产生文件**。"""
    target = REPO_ROOT / "foo.md"
    assert not target.exists(), "前提：目标不得已存在"

    with pytest.raises(guard.RepoWritePathError) as excinfo:
        guard.check_write_path(target)

    msg = str(excinfo.value)
    assert "foo.md" in msg, msg
    assert "data/" in msg and "tmp/" in msg, msg
    assert not target.exists(), "守卫必须拒绝落盘，不得留下文件"


def test_guard_rejects_repo_docs_subtree(guard) -> None:
    """仓库内的 ``docs/`` 子树同样被拒（D-19：脚本不得把产物写回软件仓）。"""
    with pytest.raises(guard.RepoWritePathError) as excinfo:
        guard.check_write_path(REPO_ROOT / "docs" / "evaluation" / "runs" / "x.json")
    assert "docs" in str(excinfo.value)


@pytest.mark.parametrize("rel", ["data/report.json", "tmp/probe.md"])
def test_guard_allows_data_and_tmp_subtrees(guard, rel: str) -> None:
    """``data/``、``tmp/`` 是软件仓内**允许**的两个子树。"""
    resolved = guard.check_write_path(REPO_ROOT / rel)
    assert resolved == (REPO_ROOT / rel).resolve()


def test_guard_allows_path_outside_repo(guard, tmp_path: Path) -> None:
    """仓外路径（系统临时目录、痕迹仓）一律放行。"""
    target = tmp_path / "out.md"
    resolved = guard.check_write_path(target)
    assert resolved == target.resolve()


def test_guard_allow_repo_root_is_explicit_opt_in(guard) -> None:
    """逃生口 ``allow_repo_root=True`` 才放行仓内非允许子树（默认关）。"""
    target = REPO_ROOT / "docs" / "escape.md"
    with pytest.raises(guard.RepoWritePathError):
        guard.check_write_path(target)
    resolved = guard.check_write_path(target, allow_repo_root=True)
    assert resolved == target.resolve()


def test_guard_is_inside_repo_helper(guard, tmp_path: Path) -> None:
    """``is_inside_repo`` 的真实判定（仓内 True / 仓外 False）。"""
    assert guard.is_inside_repo(REPO_ROOT / "docs" / "x.md") is True
    assert guard.is_inside_repo(tmp_path / "x.md") is False


# ---------------------------------------------------------------------------
# B1：痕迹仓根解析**绝不回落到软件仓**
# ---------------------------------------------------------------------------


def test_resolve_artifacts_root_never_falls_back_to_software_repo(guard, tmp_path: Path) -> None:
    """找不到痕迹仓 ⇒ ``root is None``（**不是**软件仓）。"""
    fake_repo = tmp_path / "NovelOS"
    fake_repo.mkdir()
    assert not (fake_repo.parent / "NovelOS-Artifacts").exists()

    res = guard.resolve_artifacts_root(repo_root=fake_repo, env={})

    assert res.root is None, "找不到痕迹仓时不得回落到软件仓"
    assert res.source == "none"
    assert res.root != fake_repo
    assert guard.ARTIFACTS_ENV_VAR in res.detail, "必须说明找过哪些地方 / 怎么修"


def test_resolve_artifacts_root_precedence(guard, tmp_path: Path) -> None:
    """解析顺序：``--artifacts-dir`` > 环境变量 > 同级目录。"""
    fake_repo = tmp_path / "NovelOS"
    fake_repo.mkdir()
    sibling = fake_repo.parent / "NovelOS-Artifacts"
    sibling.mkdir()

    cli = guard.resolve_artifacts_root(tmp_path / "cli", repo_root=fake_repo, env={})
    assert cli.source == "cli" and cli.root == (tmp_path / "cli").resolve()

    env_res = guard.resolve_artifacts_root(
        None, repo_root=fake_repo, env={guard.ARTIFACTS_ENV_VAR: str(tmp_path / "envdir")}
    )
    assert env_res.source == "env" and env_res.root == (tmp_path / "envdir").resolve()

    sib = guard.resolve_artifacts_root(None, repo_root=fake_repo, env={})
    assert sib.source == "sibling" and sib.root == sibling.resolve()


# ---------------------------------------------------------------------------
# B1：隔离目录（无痕迹仓）⇒ 两个脚本都**拒绝写**
# ---------------------------------------------------------------------------


def test_eval_regression_write_docs_refused_without_artifacts(tmp_path: Path) -> None:
    """隔离目录里 ``--write-docs`` ⇒ 拒绝写 + stderr 说明 + 退出码非 0 + 零新增文件。"""
    iso = _isolated_copy(tmp_path, ["eval_regression.py", "guard_repo_write_path.py"])
    before = _snapshot(iso)

    proc = _run_script(iso / "scripts" / "eval_regression.py", ["--write-docs", "--run-id", "run_iso"])

    assert proc.returncode == EXIT_ARTIFACTS_UNAVAILABLE, (
        f"痕迹仓缺失时必须明确失败，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "痕迹仓" in proc.stderr, proc.stderr
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert "NovelOS-Artifacts" in proc.stderr or "NOVELOS_ARTIFACTS_DIR" in proc.stderr, proc.stderr
    assert _snapshot(iso) == before, "隔离目录（模拟软件仓）内不得产生任何文件"


def test_m1_report_write_docs_refused_without_artifacts(tmp_path: Path) -> None:
    """``m1_report --write-docs`` 同样拒绝写（两个脚本行为一致）。"""
    iso = _isolated_copy(tmp_path, ["m1_report.py", "guard_repo_write_path.py"])
    before = _snapshot(iso)

    proc = _run_script(
        iso / "scripts" / "m1_report.py",
        ["--write-docs", "--data-dir", str(tmp_path / "data")],
    )

    assert proc.returncode == EXIT_ARTIFACTS_UNAVAILABLE, (
        f"痕迹仓缺失时必须明确失败，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "痕迹仓" in proc.stderr, proc.stderr
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert _snapshot(iso) == before, "隔离目录（模拟软件仓）内不得产生任何文件"


def test_eval_regression_refuses_to_build_baseline_without_artifacts(tmp_path: Path) -> None:
    """痕迹仓缺失 ⇒ **拒绝建基线**（而不是建到软件仓）+ 明确报错 + 非 0 退出。"""
    iso = _isolated_copy(tmp_path, ["eval_regression.py", "guard_repo_write_path.py"])
    before = _snapshot(iso)

    proc = _run_script(iso / "scripts" / "eval_regression.py", ["--run-id", "run_iso_nobase"])

    assert proc.returncode == EXIT_ARTIFACTS_UNAVAILABLE, proc.stderr
    assert "拒绝建基线" in proc.stderr, proc.stderr
    assert not (iso / "docs" / "evaluation" / "baseline" / "last_passing_run.json").exists()
    assert _snapshot(iso) == before, "拒绝建基线 ⇒ 隔离目录内不得产生任何文件"


# ---------------------------------------------------------------------------
# B2：基线落点 = 痕迹仓根下（**不是**软件仓）
# ---------------------------------------------------------------------------


def test_eval_regression_baseline_lands_in_artifacts_root(tmp_path: Path) -> None:
    """``--artifacts-dir`` ⇒ 报告与基线都落在该根下；软件仓内零新增文件。"""
    artifacts = tmp_path / "artifacts"
    eval_base = REPO_ROOT / "docs" / "evaluation"
    before_repo = _snapshot(eval_base)

    proc = _run_script(
        EVAL_SCRIPT,
        [
            "--artifacts-dir",
            str(artifacts),
            "--write-docs",
            "--run-id",
            "run_artifacts_landing",
        ],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "decision: PASS" in proc.stdout, proc.stdout

    baseline = artifacts / "docs" / "evaluation" / "baseline" / "last_passing_run.json"
    assert baseline.is_file(), f"基线应落在痕迹仓根下：{baseline}"
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run_artifacts_landing"
    assert payload["aggregate"]["cases_total"] >= 1
    assert payload["aggregate"]["cases_passed"] == payload["aggregate"]["cases_total"]
    assert payload["cases"], "基线必须含逐 case 签名"

    report = artifacts / "docs" / "evaluation" / "runs" / "run_artifacts_landing.json"
    assert report.is_file(), f"报告应落在痕迹仓根下：{report}"

    assert _snapshot(eval_base) == before_repo, "软件仓 docs/evaluation 内不得新增文件"
    assert not (eval_base / "baseline" / "last_passing_run.json").exists()


def test_eval_regression_refuses_baseline_inside_software_repo(tmp_path: Path) -> None:
    """显式把基线指向软件仓内的**不存在**路径 ⇒ 拒绝建（守卫）+ 不产生文件。"""
    target = REPO_ROOT / "docs" / "evaluation" / "baseline" / "last_passing_run.json"
    assert not target.exists(), "前提：软件仓内不得已有基线文件"

    proc = _run_script(
        EVAL_SCRIPT,
        ["--baseline", str(target), "--run-id", "run_repo_baseline"],
    )

    assert proc.returncode == EXIT_WRITE_PATH_REJECTED, (
        f"软件仓内建基线必须被拒，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert not target.exists(), "拒绝 ⇒ 不得在软件仓内产生基线文件"


def test_m1_report_write_docs_lands_in_artifacts_root(tmp_path: Path) -> None:
    """``m1_report --write-docs --artifacts-dir`` ⇒ 报告落在痕迹仓根下。"""
    artifacts = tmp_path / "artifacts"
    eval_base = REPO_ROOT / "docs" / "evaluation"
    before_repo = _snapshot(eval_base)

    proc = _run_script(
        M1_SCRIPT,
        [
            "--artifacts-dir",
            str(artifacts),
            "--write-docs",
            "--data-dir",
            str(tmp_path / "data"),
        ],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    out = artifacts / "docs" / "evaluation" / "m1-data.md"
    assert out.is_file(), f"报告应落在痕迹仓根下：{out}"
    assert "M1" in out.read_text(encoding="utf-8")
    assert _snapshot(eval_base) == before_repo, "软件仓 docs/evaluation 内不得新增文件"


def test_m1_report_out_at_repo_root_is_rejected(tmp_path: Path) -> None:
    """``--out`` 指向仓库根下的 ``foo.md`` ⇒ 守卫拒绝 + **不产生文件**。"""
    target = REPO_ROOT / "m1-report-guard-probe.md"
    assert not target.exists(), "前提：目标不得已存在"

    proc = _run_script(
        M1_SCRIPT,
        ["--out", str(target), "--data-dir", str(tmp_path / "data")],
    )

    assert proc.returncode == EXIT_WRITE_PATH_REJECTED, (
        f"仓库根下的输出路径必须被拒，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert not target.exists(), "拒绝 ⇒ 不得在软件仓内产生文件"


# ---------------------------------------------------------------------------
# 逃生口：默认关，显式开才生效
# ---------------------------------------------------------------------------


def test_escape_hatch_allows_writing_into_software_repo(tmp_path: Path) -> None:
    """``--allow-software-repo-out`` 显式开启后，痕迹才允许落回软件仓（并给出告警）。"""
    iso = _isolated_copy(tmp_path, ["eval_regression.py", "guard_repo_write_path.py"])

    proc = _run_script(
        iso / "scripts" / "eval_regression.py",
        [
            "--write-docs",
            "--allow-software-repo-out",
            "--run-id",
            "run_escape_hatch",
            "--golden-dir",
            str(GOLDEN_DIR),
        ],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "--allow-software-repo-out" in proc.stderr, proc.stderr
    written = iso / "docs" / "evaluation" / "runs" / "run_escape_hatch.json"
    assert written.is_file(), f"逃生口开启后报告应落在软件仓：{written}"
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run_escape_hatch"


def test_default_run_does_not_write_into_software_repo(tmp_path: Path) -> None:
    """默认（无 ``--write-docs``）跑一次 ⇒ 软件仓内零新增文件（报告落系统临时目录）。"""
    eval_base = REPO_ROOT / "docs" / "evaluation"
    before_repo = _snapshot(eval_base)

    proc = _run_script(
        EVAL_SCRIPT,
        ["--run-id", "run_default_landing", "--golden-dir", str(tmp_path / "empty_golden")],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert _snapshot(eval_base) == before_repo, "默认模式不得在软件仓内新增文件"
    assert "novelos_eval_runs" in proc.stdout, proc.stdout


# ---------------------------------------------------------------------------
# 第二批（对抗审查 B1 同族）：m2_judge / real_llm_e2e 的落点也必须落痕迹仓
# ---------------------------------------------------------------------------


def _seed_judge_db(db_path: Path) -> None:
    """造一个最小可评审库：1 章 COMMITTED + 1 条 draft（**合成夹具**，不碰真库）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE chapters ("
            "chapter_id TEXT PRIMARY KEY, number INTEGER, title TEXT, status TEXT)"
        )
        conn.execute(
            "CREATE TABLE drafts ("
            "id INTEGER PRIMARY KEY, chapter_id TEXT, version INTEGER, content TEXT)"
        )
        conn.execute(
            "INSERT INTO chapters VALUES ('ch_synthetic01', 1, '合成章', 'COMMITTED')"
        )
        conn.execute(
            "INSERT INTO drafts VALUES (1, 'ch_synthetic01', 1, '合成正文片段，用于 dry-run 评审。')"
        )
        conn.commit()
    finally:
        conn.close()


def test_m2_judge_refuses_to_write_without_artifacts(tmp_path: Path) -> None:
    """隔离目录（无痕迹仓）跑 ``m2_judge`` ⇒ 拒绝写 + 零新增文件 + 退出码非 0。"""
    iso = _isolated_copy(tmp_path, ["m2_judge.py", "guard_repo_write_path.py"])
    db = tmp_path / "synthetic.db"
    _seed_judge_db(db)
    before = _snapshot(iso)
    repo_eval = _snapshot(REPO_ROOT / "docs" / "evaluation")

    proc = _run_script(
        iso / "scripts" / "m2_judge.py",
        ["--dry-run", "--db", str(db)],
    )

    assert proc.returncode == EXIT_ARTIFACTS_UNAVAILABLE, (
        f"痕迹仓缺失时必须明确失败，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "痕迹仓" in proc.stderr, proc.stderr
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert "chapter_id" in proc.stderr, proc.stderr
    assert _snapshot(iso) == before, "隔离目录（模拟软件仓）内不得产生任何文件"
    assert _snapshot(REPO_ROOT / "docs" / "evaluation") == repo_eval, "软件仓内不得新增文件"


def test_m2_judge_lands_in_artifacts_root(tmp_path: Path) -> None:
    """``--artifacts-dir`` ⇒ 评审产物真的落在该根下（内容非空），软件仓零新增。"""
    artifacts = tmp_path / "artifacts"
    db = tmp_path / "synthetic.db"
    _seed_judge_db(db)
    repo_eval = _snapshot(REPO_ROOT / "docs" / "evaluation")

    proc = _run_script(
        M2_JUDGE_SCRIPT,
        ["--dry-run", "--db", str(db), "--artifacts-dir", str(artifacts)],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    out_dir = artifacts / "docs" / "evaluation" / "m2-judge"
    results = out_dir / "judge_results.json"
    summary = out_dir / "judge_summary.md"
    assert results.is_file(), f"评审明细应落在痕迹仓根下：{results}"
    assert summary.is_file(), f"汇总应落在痕迹仓根下：{summary}"
    payload = json.loads(results.read_text(encoding="utf-8"))
    assert payload["results"], "judge_results.json 必须含逐章结果"
    assert payload["results"][0]["number"] == 1
    assert payload["results"][0]["dry_run"] is True
    assert summary.read_text(encoding="utf-8").strip(), "汇总报告不得为空"
    assert _snapshot(REPO_ROOT / "docs" / "evaluation") == repo_eval, "软件仓内不得新增文件"


def test_m2_judge_out_dir_inside_software_repo_is_rejected(tmp_path: Path) -> None:
    """``--out-dir`` 指向软件仓内 ⇒ 守卫拒绝 + 不产生文件。"""
    target_dir = REPO_ROOT / "docs" / "evaluation" / "m2-judge"
    db = tmp_path / "synthetic.db"
    _seed_judge_db(db)
    before = _snapshot(target_dir)

    proc = _run_script(
        M2_JUDGE_SCRIPT,
        ["--dry-run", "--db", str(db), "--out-dir", str(target_dir)],
    )

    assert proc.returncode == EXIT_WRITE_PATH_REJECTED, (
        f"软件仓内的产物目录必须被拒，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert _snapshot(target_dir) == before, "拒绝 ⇒ 软件仓内不得产生文件"


def test_m2_judge_escape_hatch_allows_writing_into_software_repo(tmp_path: Path) -> None:
    """``--allow-software-repo-out`` 显式开启后，产物才允许落回软件仓（并给出告警）。"""
    iso = _isolated_copy(tmp_path, ["m2_judge.py", "guard_repo_write_path.py"])
    db = tmp_path / "synthetic.db"
    _seed_judge_db(db)

    proc = _run_script(
        iso / "scripts" / "m2_judge.py",
        ["--dry-run", "--db", str(db), "--allow-software-repo-out"],
    )

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "--allow-software-repo-out" in proc.stderr, proc.stderr
    written = iso / "docs" / "evaluation" / "m2-judge" / "judge_results.json"
    assert written.is_file(), f"逃生口开启后产物应落在软件仓：{written}"
    assert json.loads(written.read_text(encoding="utf-8"))["results"], "产物不得为空"


def test_real_llm_e2e_refuses_without_artifacts_before_any_llm_call(tmp_path: Path) -> None:
    """隔离目录跑 ``real_llm_e2e`` ⇒ **在任何 LLM 调用之前**就拒绝写。

    ⚠️ 子进程环境**清掉** ``MINIMAX_API_KEY`` ⇒ 无论如何都不可能发起真实模型调用
    （本批硬约束：零真实 LLM）。退出码 3（而不是「未设置 key」的 2）⇒ 证明落点判定
    排在 API key 检查与真实调用**之前**。
    """
    iso = _isolated_copy(tmp_path, ["real_llm_e2e.py", "guard_repo_write_path.py"])
    before = _snapshot(iso)

    proc = _run_script(
        iso / "scripts" / "real_llm_e2e.py",
        [],
        unset_env=["MINIMAX_API_KEY", "NOVELOS_API_KEY_OPENAI_COMPATIBLE"],
    )

    assert proc.returncode == EXIT_ARTIFACTS_UNAVAILABLE, (
        f"痕迹仓缺失时必须明确失败，实际退出码 {proc.returncode}\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "痕迹仓" in proc.stderr, proc.stderr
    assert "拒绝写入" in proc.stderr, proc.stderr
    assert "MINIMAX_API_KEY" not in proc.stdout, "落点判定必须早于 API key 检查"
    assert _snapshot(iso) == before, "隔离目录（模拟软件仓）内不得产生任何文件"


def test_real_llm_e2e_report_path_lands_in_artifacts_root(tmp_path: Path) -> None:
    """落点判定函数：``--artifacts-dir`` ⇒ 报告路径落在该根下（纯函数，不跑真调用）。"""
    mod = importlib.import_module("scripts.real_llm_e2e")
    artifacts = tmp_path / "artifacts"

    out = mod.resolve_report_path(artifacts_dir=str(artifacts))

    assert out == artifacts / "docs" / "evaluation" / "runs" / mod.REPORT_FILENAME


def test_real_llm_e2e_report_path_raises_when_artifacts_missing(tmp_path: Path, monkeypatch) -> None:
    """落点判定函数：痕迹仓不可用 ⇒ 抛异常（**不回落到软件仓**）。"""
    mod = importlib.import_module("scripts.real_llm_e2e")
    fake_repo = tmp_path / "NovelOS"
    fake_repo.mkdir()
    monkeypatch.setattr(mod, "ROOT", fake_repo)
    monkeypatch.delenv("NOVELOS_ARTIFACTS_DIR", raising=False)

    with pytest.raises(mod.ArtifactsUnavailableError) as excinfo:
        mod.resolve_report_path()

    msg = str(excinfo.value)
    assert "痕迹仓不可用" in msg, msg
    assert "NovelOS-Artifacts" in msg, msg
    assert "--allow-software-repo-out" in msg, msg
    assert "docs/evaluation/runs" not in msg, "不得把软件仓路径当成可用落点"


def test_real_llm_e2e_escape_hatch_falls_back_to_software_repo(tmp_path: Path, monkeypatch) -> None:
    """逃生口开启 ⇒ 明确回落到软件仓（并打告警），而不是静默。"""
    mod = importlib.import_module("scripts.real_llm_e2e")
    fake_repo = tmp_path / "NovelOS"
    fake_repo.mkdir()
    monkeypatch.setattr(mod, "ROOT", fake_repo)
    monkeypatch.delenv("NOVELOS_ARTIFACTS_DIR", raising=False)

    out = mod.resolve_report_path(allow_software_repo_out=True)

    assert out == fake_repo / "docs" / "evaluation" / "runs" / mod.REPORT_FILENAME
